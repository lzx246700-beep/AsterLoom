"""Source-bounded native ATP table extraction from PMC's rendered article HTML.

This adapter does not infer missing conditions and does not digitize images.
It emits the same Review-B relation packet used by the JATS path, with DOM
anchors that are replayed against the exact HTML bytes.
"""
from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
import re
from pathlib import Path

from .atp_content_relations import _field

NUMERIC = re.compile(r"^\s*([+-]?[\d,]+(?:\.\d+)?)\s*±\s*([\d,]+(?:\.\d+)?)(?:\s*([a-z*#∗]+))?\s*$", re.I)
METRIC = re.compile(r"^(?:initial\s+|cellular\s+|intracellular\s+)?ATP(?:\s+(?:content|level|levels|concentration))?\s*\(([^)]*)\)\s*$", re.I)
CONTROL = re.compile(r"^(?:control|vehicle(?:\s+control)?|untreated(?:\s+control)?)\b", re.I)
MODEL = re.compile(r"\(([^()]+)\)\s*$")
CN_ATP_METRIC = re.compile(r"ATP\s*(?:含量|水平|浓度)", re.I)
CN_MODEL_CONTROL = re.compile(r"模型\s*对照(?:组)?")


class _Article(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section_stack = []
        self.region = "body"
        self.table = None
        self.row = None
        self.cell = None
        self.paragraph = None
        self.footnote = None
        self.title = None
        self.tables = []
        self.paragraphs = []
        self.footnotes = []
        self.titles = []
        self.meta = {}
        self.current_figure = None
        self.in_figcaption = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and a.get("id"):
            self.section_stack.append(a["id"])
        if tag == "figure":
            self.current_figure = a.get("id")
        if tag == "figcaption":
            self.in_figcaption = True
        if tag == "meta" and a.get("name") in ("citation_title", "citation_doi", "citation_pmcid", "citation_pmid"):
            self.meta[a["name"]] = a.get("content")
        if tag == "link" and a.get("rel") == "canonical":
            self.meta["canonical_url"] = a.get("href")
        if tag in ("thead", "tbody", "tfoot"):
            self.region = tag
        if tag == "table":
            self.table = {"section_id": self.section_stack[-1] if self.section_stack else None, "rows": []}
            self.tables.append(self.table)
        elif tag == "tr" and self.table is not None:
            self.row = {"region": self.region, "cells": []}
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"tag": tag, "text": "", "rowspan": a.get("rowspan", "1"),
                         "colspan": a.get("colspan", "1"), "sup_stack": [], "superscript_values": []}
        elif tag == "sup" and self.cell is not None:
            self.cell["sup_stack"].append("")
        elif tag == "p" and self.paragraph is None:
            self.paragraph = {"section_id": self.section_stack[-1] if self.section_stack else None,
                              "figure_id": self.current_figure, "is_figcaption": self.in_figcaption,
                              "links": [], "text": ""}
        elif tag == "a" and self.paragraph is not None:
            href = a.get("href")
            if href:
                self.paragraph["links"].append(href)
        elif tag == "div" and a.get("id", "").startswith("tfn"):
            self.footnote = {"id": a["id"], "text": ""}
        elif tag in ("h1", "title") and self.title is None:
            self.title = {"lang": a.get("lang", "unknown"), "text": ""}

    def handle_endtag(self, tag):
        if tag == "sup" and self.cell is not None and self.cell["sup_stack"]:
            self.cell["superscript_values"].append(self.cell["sup_stack"].pop())
        elif tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = " ".join(self.cell["text"].split())
            # Preserve exponent semantics only when the source explicitly
            # marks the digit as superscript; ordinary inline digits stay as
            # printed. This keeps units such as mmol/10^4 cells interpretable.
            for value in self.cell["superscript_values"]:
                if value.strip().isdigit():
                    digit = re.escape(value.strip())
                    self.cell["text"], n = re.subn(
                        rf"(?<=\d)\s+({digit})(?=\s*(?:个细胞|cells?\b|/|\)))",
                        rf"^\1", self.cell["text"], count=1, flags=re.I)
                    if n:
                        break
            self.row["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.table = None
        elif tag in ("thead", "tbody", "tfoot"):
            self.region = "body"
        elif tag == "p" and self.paragraph is not None:
            self.paragraph["text"] = " ".join(self.paragraph["text"].split())
            self.paragraphs.append(self.paragraph)
            self.paragraph = None
        elif tag == "figcaption":
            self.in_figcaption = False
        elif tag == "figure":
            self.current_figure = None
        elif tag == "div" and self.footnote is not None:
            self.footnote["text"] = " ".join(self.footnote["text"].split())
            self.footnotes.append(self.footnote)
            self.footnote = None
        elif tag in ("h1", "title") and self.title is not None:
            self.title["text"] = " ".join(self.title["text"].split())
            self.titles.append(self.title)
            self.title = None
        elif tag == "section" and self.section_stack:
            self.section_stack.pop()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data
            if self.cell["sup_stack"]:
                self.cell["sup_stack"][-1] += data
        if self.paragraph is not None:
            self.paragraph["text"] += data
        if self.footnote is not None:
            self.footnote["text"] += data
        if self.title is not None:
            self.title["text"] += data


def _citation(source_sha, anchor, text):
    return {"source_sha256": source_sha, "source_anchor": anchor, "quote": text,
            "start": 0, "end": len(text), "offset_basis": "normalized PMC HTML DOM text"}


def _number(text):
    match = NUMERIC.fullmatch(text)
    if not match:
        return None
    return {"mean": float(match[1].replace(",", "")),
            "sd": float(match[2].replace(",", "")), "marker": match[3]}


def _canonical_url(meta, source_path):
    url = meta.get("canonical_url") or ""
    match = re.search(r"/(PMC\d+)/?$", url)
    if match:
        return match[1]
    match = re.search(r"(PMC\d+)", Path(source_path).name, re.I)
    return match[1].upper() if match else None


def _author_cn_context(parser, text_index, table_id, alias, digest):
    """Bind only unique Methods/Results paragraphs that point at this ATP table."""
    paragraphs = parser.paragraphs
    caption = text_index.get(f"#{table_id}/p[1]", "")
    ref = re.search(r"表\s*(\d+)", caption)
    result = [p for p in paragraphs if p["section_id"] != table_id and alias in p["text"] and "ATP" in p["text"]
              and ref and re.search(rf"表\s*{ref[1]}(?=$|[^\d])", p["text"])]
    experiment = [p for p in paragraphs if alias in p["text"] and "ATP含量" in p["text"]
                  and "组别设置" in p["text"]]
    assay = [p for p in paragraphs if "ATP含量" in p["text"] and "比色法" in p["text"]]
    model = [p for p in paragraphs if "ATP" in p["text"] and re.search(
        r"primary cultured .*? cortical neuron cells", p["text"], re.I)]
    selected = {"result": result[0] if len(result) == 1 else None,
                "experiment": experiment[0] if len(experiment) == 1 else None,
                "assay": assay[0] if len(assay) == 1 else None,
                "model": model[0] if len(model) == 1 else None}
    citations = {name: (_citation(digest, f"#{p['section_id'] or 'article'}/p[{p['section_paragraph_index']}]", p["text"])
                        if p else None)
                 for name, p in selected.items()}
    dose = time_h = None
    dose_ev = time_ev = None
    if selected["experiment"]:
        p = selected["experiment"]
        dose_matches = list(re.finditer(r"([\d.]+)\s*(μmol/L|µmol/L|umol/L|μM|µM|uM)", p["text"], re.I))
        time_matches = list(re.finditer(r"处理\s*([\d.]+)\s*h", p["text"], re.I))
        if len(dose_matches) == 1:
            dose = dose_matches[0].group(0)
            dose_ev = dict(citations["experiment"], quote=dose,
                           start=dose_matches[0].start(), end=dose_matches[0].end())
        if len(time_matches) == 1:
            time_h = float(time_matches[0][1])
            time_ev = dict(citations["experiment"], quote=time_matches[0].group(0),
                           start=time_matches[0].start(), end=time_matches[0].end())
    # Only cite the exact assay literal if it explicitly gives ATP method and readout.
    assay_value = None
    if selected["assay"] and re.search(r"340\s*nm", selected["assay"]["text"], re.I):
        assay_value = "AUTHOR_COLORIMETRIC_ATP_ASSAY_340_NM"
    cell_value = None
    if selected["model"]:
        model_match = re.search(r"primary cultured .*? cortical neuron cells", selected["model"]["text"], re.I)
        if model_match:
            cell_value = model_match.group(0)
            citations["model"] = dict(citations["model"], quote=cell_value,
                start=model_match.start(), end=model_match.end())
    return {"result": citations["result"], "experiment": citations["experiment"],
            "assay": citations["assay"] if assay_value else None, "assay_value": assay_value,
            "model": citations["model"], "model_value": cell_value,
            "dose": dose, "dose_evidence": dose_ev, "time_h": time_h, "time_evidence": time_ev}


def _explicit_bilingual_name(parser, requested, alias, digest):
    """Find an author-defined bilingual name and a reagent-use statement.

    Parallel titles and retrieval queries alone are insufficient. This links
    source name strings only; canonical structure and tested form remain
    independent review questions.
    """
    requested_rx = re.escape(requested.strip())
    alias_rx = re.escape(alias.strip())
    patterns = (
        re.compile(rf"(?P<alias>{alias_rx})\s*[（(]\s*(?P<english>{requested_rx})\s*[)）]", re.I),
        re.compile(rf"(?P<english>{requested_rx})\s*[（(]\s*(?P<alias>{alias_rx})\s*[)）]", re.I),
    )
    pair_patterns = (
        re.compile(rf"(?P<alias>{alias_rx})\s*[（(]\s*(?P<english>[A-Za-z][A-Za-z0-9 -]{{2,60}}?)\s*[)）]"),
        re.compile(rf"(?P<english>[A-Za-z][A-Za-z0-9 -]{{2,60}}?)\s*[（(]\s*(?P<alias>{alias_rx})\s*[)）]"),
    )
    definitions = []
    name_pairs = set()
    reagent_mentions = []
    procurement = re.compile(r"纯度|批号|供应商|购自|产品|试剂|provided by|purchased from|supplier|catalog", re.I)
    for paragraph in parser.paragraphs:
        section = (paragraph.get("section_id") or "").casefold()
        if any(token in section for token in ("ref-list", "references", "back", "ack")):
            continue
        text = paragraph["text"]
        anchor = f"#{paragraph.get('section_id') or 'article'}/p[{paragraph.get('section_paragraph_index')}]"
        for pattern in patterns:
            for match in pattern.finditer(text):
                definitions.append({**_citation(digest, anchor, match.group()),
                                    "start": match.start(), "end": match.end()})
        for pattern in pair_patterns:
            for match in pattern.finditer(text):
                name_pairs.add((match['alias'].casefold(), match['english'].strip().casefold()))
        if re.search(alias_rx, text, re.I) and procurement.search(text):
            reagent_mentions.append(_citation(digest, anchor, text))
    if (not definitions or not reagent_mentions or
            name_pairs != {(alias.casefold(), requested.casefold())}):
        return None
    return {"state": "AUTHOR_EXPLICIT_BILINGUAL_NAME_CROSSWALK_PENDING",
            "requested_name": requested, "author_name": alias,
            "author_defined_name_pair": True,
            "canonical_structure_verified": False,
            "tested_chemical_form_verified": False,
            "source_sha256": digest,
            "definition_evidence": definitions,
            "reagent_evidence": reagent_mentions}


def _sentiment_conflict(model, paragraphs, known_models=()):
    relevant = []
    for paragraph in paragraphs:
        text = paragraph["text"]
        # Keep model/endpoint polarity inside one sentence and coordinated
        # clause; a single sentence may compare several models.
        boundaries = [m.end() for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", text)]
        starts = [0, *boundaries]
        ends = [*boundaries, len(text)]
        for start, end in zip(starts, ends):
            sentence = text[start:end]
            clause_boundaries = list(re.finditer(r";\s*|,\s*(?=(?:while|whereas|but)\b)|\s+(?:while|whereas|but)\s+", sentence, re.I))
            clause_starts = [0, *[m.end() for m in clause_boundaries]]
            clause_ends = [*[m.start() for m in clause_boundaries], len(sentence)]
            active_model = None
            active_model_start = None
            for clause_start, clause_end in zip(clause_starts, clause_ends):
                left = start + clause_start
                right = start + clause_end
                while left < right and text[left].isspace():
                    left += 1
                clause = text[left:right].strip()
                explicit_models = [name for name in known_models
                                   if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", clause, re.I)]
                if explicit_models:
                    active_model = explicit_models[0] if len({x.casefold() for x in explicit_models}) == 1 else None
                    active_model_start = left if active_model else None
                if active_model and active_model.casefold() == model.casefold() and re.search(r"\bATP\b", clause, re.I):
                    quote_start = active_model_start if active_model_start is not None else left
                    relevant.append({**paragraph, "sentence": text[quote_start:right].strip(),
                                     "sentence_start": quote_start})
    negative = [p for p in relevant if re.search(r"(?:no|not|without)\s+(?:a\s+)?significant|not significantly", p["sentence"], re.I)]
    positive = [p for p in relevant if re.search(r"(?:ATP\s+(?:content|level|generation).{0,100}(?:decreased|increased)|(?:decreased|increased).{0,100}ATP).{0,80}P\s*[<≤]", p["sentence"], re.I)]
    if not negative or not positive:
        return []
    neg, pos = negative[0], positive[0]
    return [{"kind": "AUTHOR_RESULT_CONFLICT", "summary": "Abstract and Results differ on ATP significance/direction for this model.",
             "abstract_evidence": _citation_placeholder(neg), "results_evidence": _citation_placeholder(pos)}]


def _author_percent_result_candidates(parser, digest, paper_id, compounds):
    """Retain author-reported ATP percentages linked to a figure in Results.

    Values remain on the author's relative scale. In particular, a statistical
    comparison against the model group is kept distinct from the scale
    denominator (for example, control group = 100%). No raw means are derived.
    """
    from .atp_content_relations import _field

    methods = [p for p in parser.paragraphs if re.search(r"\bATP\b", p["text"], re.I)
               and re.search(r"firefly\s+luciferase", p["text"], re.I)
               and re.search(r"ATP.{0,100}standard\s+curve|standard\s+curve.{0,100}ATP", p["text"], re.I)]
    if len(methods) != 1:
        return []
    method = methods[0]
    figures = {p["figure_id"]: p for p in parser.paragraphs
               if p.get("figure_id") and p.get("is_figcaption")}
    results = []
    for p in parser.paragraphs:
        text = p["text"]
        if not re.search(r"\bATP\s+(?:levels?|content|concentration)\b", text, re.I):
            continue
        figure_refs = [link[1:] for link in p.get("links", [])
                       if link.startswith("#") and link[1:] in figures]
        # Require an explicit in-paragraph link to an existing figure caption.
        if len(set(figure_refs)) != 1:
            continue
        figid = figure_refs[0]
        caption = figures[figid]
        fig_match = re.search(r"\bf(\d+)(?:[-_]|$)", figid, re.I)
        if not fig_match:
            continue
        fig_num = fig_match.group(1)

        for requested in compounds:
            name = re.escape(requested)
            if not re.search(rf"(?<![A-Za-z0-9]){name}(?![A-Za-z0-9])", text, re.I):
                continue
            # A single author-declared ordered dose list and ordered relative
            # values are required. This rejects ambiguous arm/value counts.
            dose_pattern = re.compile(
                rf"\b{ name }\s*\(\s*(?P<doses>[^()]{{1,100}}(?:μM|µM|uM|mM))\s*\)", re.I)
            dose_matches = list(dose_pattern.finditer(text))
            if len(dose_matches) != 1:
                continue
            dm = dose_matches[0]
            shared_unit = re.fullmatch(
                r"\s*(?P<values>[\d.,\s]+?(?:\s*(?:and|&)\s*[\d.]+)?)\s*(?P<unit>μM|µM|uM|mM)\s*",
                dm["doses"], re.I)
            number_tokens = list(re.finditer(r"(?<![\w.])([\d]+(?:\.\d+)?)", shared_unit["values"])) if shared_unit else []
            if not number_tokens:
                continue
            dose_unit = shared_unit["unit"]
            doses = [float(m[1]) * (1000 if dose_unit.casefold() == "mm" else 1) for m in number_tokens]
            if len(doses) != len(set(doses)):
                continue
            # Require this same Results paragraph to state percent-of-control
            # scale and the separate model-group comparison.
            control_scale = re.search(
                r"\b(?:compared\s+with|versus|vs\.?|relative\s+to)\s+the\s+CG\s+group\s*\(\s*100\s*±\s*[\d.]+\s*%\s*\)", text, re.I)
            model_compare = re.search(r"\b(?:compared\s+with|compared\s+to)\s+the\s+MG\s+group\b", text, re.I)
            if not control_scale or not model_compare:
                continue
            # Relative ATP values are recovered only from the ATP-specific
            # clause after the explicitly named compound dose list.
            tail = text[dm.end():]
            atp_clause = re.search(
                r"\bATP\s+(?:levels?|content)\s+were\s+(?P<direction>increased|decreased)\s+to\s+(?P<values>.{1,600}?\brespectively)\b",
                tail, re.I)
            if not atp_clause:
                continue
            value_matches = list(re.finditer(r"([\d]+(?:\.\d+)?)\s*±\s*([\d]+(?:\.\d+)?)\s*%", atp_clause["values"]))
            if len(value_matches) != len(doses):
                continue
            if not re.search(r"respectively", atp_clause["values"], re.I):
                continue
            # Verify the paragraph's cell model and the Methods identity of
            # that model. If absent or competing, retain no candidate.
            models = set(re.findall(r"\b([A-Z][A-Za-z0-9-]+)\s+cardiomyocytes\b", text))
            method_models = set(re.findall(r"\b([A-Z][A-Za-z0-9-]+)\s+cardiomyocytes\b", method["text"]))
            if len(models) != 1 or models != method_models:
                continue
            model = next(iter(models))
            # Match a unique author-described pretreatment schedule, but do
            # not treat it as total compound exposure.
            pretreat = [m for m in parser.paragraphs if
                        re.search(rf"\b{re.escape(model)}\b", m["text"])
                        and re.search(rf"\b{ name }\b", m["text"], re.I)
                        and re.search(r"pretreated\s+with", m["text"], re.I)
                        and re.search(r"divided\s+into", m["text"], re.I)
                        and re.search(r"control\s+group\s*\(CG\).*model\s+group\s*\(MG\)", m["text"], re.I)]
            if len(pretreat) != 1:
                continue
            schedule = pretreat[0]
            schedule_match = re.search(
                rf"\bpretreated\s+with\s+[^.]*?\b{ name }\b[^.]*?\bfor\s+([\d.]+)\s*h", schedule["text"], re.I)
            if not schedule_match:
                continue
            result_anchor = f"#{p['section_id'] or 'article'}/p[{p['section_paragraph_index']}]"
            caption_anchor = f"#{caption['section_id'] or 'article'}/p[{caption['section_paragraph_index']}]"
            method_anchor = f"#{method['section_id'] or 'article'}/p[{method['section_paragraph_index']}]"
            schedule_anchor = f"#{schedule['section_id'] or 'article'}/p[{schedule['section_paragraph_index']}]"
            citation = lambda anchor, quote, start=0: {"source_sha256": digest,
                "source_anchor": anchor, "quote": quote, "start": start,
                "end": start + len(quote), "offset_basis": "normalized PMC HTML DOM text"}
            result_ev = citation(result_anchor, text)
            caption_ev = citation(caption_anchor, caption["text"])
            method_ev = citation(method_anchor, method["text"])
            schedule_ev = citation(schedule_anchor, schedule["text"])
            ordered_dose_literal = text[dm.start():dm.end()]
            denominator_text = control_scale.group()
            comparator_text = model_compare.group()
            unit_factor = 1000 if dose_unit.casefold() == "mm" else 1
            for index, (mean, sd) in enumerate(
                    (float(v[1]), float(v[2])) for v in value_matches):
                value_match = value_matches[index]
                value_quote = value_match.group()
                dose_num = number_tokens[index][1]
                dose_quote = dose_num
                # Dose tokens are cited within the author-declared list so
                # repeated mentions elsewhere in the paragraph cannot shift
                # the arm binding.
                value_offsets = [m.start() for m in re.finditer(re.escape(value_quote), text)]
                dose_offsets = [dm.start("doses") + m.start(1) for m in number_tokens]
                if len(value_offsets) != 1 or index >= len(dose_offsets):
                    continue
                value_ev = citation(result_anchor, value_quote, value_offsets[0])
                dose_ev = citation(result_anchor, ordered_dose_literal, dm.start())
                segment_end = (value_matches[index + 1].start() if index + 1 < len(value_matches)
                               else len(atp_clause["values"]))
                significance_match = re.search(r"\bP\s*([<>=≤≥])\s*([\d.]+)",
                    atp_clause["values"][value_match.end():segment_end], re.I)
                significance = None
                significance_ev = None
                if significance_match:
                    significance = "P" + significance_match[1] + significance_match[2]
                    significance_start = (dm.end() + atp_clause.start("values") + value_match.end()
                                          + significance_match.start())
                    significance_ev = citation(result_anchor, significance_match.group(), significance_start)
                group_literal = f"{requested} {dose_quote} pretreatment"
                fields = {
                    "compound": _field(requested, [dose_ev], "AUTHOR_NAME_ONLY_STRUCTURE_UNVERIFIED"),
                    "cell_model": _field(model, [result_ev, method_ev], "AUTHOR_RESULTS_AND_METHODS_MODEL_MATCH"),
                    "dose_original": _field(dose_quote + " " + dose_unit + " (unit shared across author-ordered dose list)", [dose_ev], "AUTHOR_RESULTS_ORDERED_DOSE_LIST_SHARED_UNIT"),
                    "dose_uM": _field(float(dose_num) * unit_factor, [dose_ev], "AUTHOR_EXPLICIT_DOSE_UNIT"),
                    "total_compound_exposure_h": _field(),
                    "treated_arm": _field(group_literal, [dose_ev, schedule_ev], "AUTHOR_PRETREATMENT_ARM_REQUIRES_REVIEW"),
                    "comparator_arm": _field("model group (MG); statistical comparison stated by author", [result_ev], "AUTHOR_COMPARISON_GROUP_NOT_SCALE_DENOMINATOR"),
                    "assay_method": _field("firefly luciferase; intracellular ATP derived from standard curve", [method_ev], "AUTHOR_DIRECT_ATP_ASSAY_WITH_STANDARD_CURVE"),
                    "endpoint": _field("intracellular ATP level", [result_ev, caption_ev], "AUTHOR_DIRECT_ATP_ENDPOINT"),
                    "normalization": _field("percent of control group (CG=100%)", [result_ev], "AUTHOR_REPORTED_RELATIVE_SCALE"),
                    "measurement_unit": _field("% of control group (author reported)", [result_ev], "AUTHOR_REPORTED_PERCENT"),
                    "effect_direction": _field("INCREASE" if atp_clause["direction"].casefold() == "increased" else "DECREASE",
                        [result_ev], "AUTHOR_REPORTED_DIRECTION_VS_MODEL_GROUP")}
                cid = "ATP-REL-HTML-" + hashlib.sha256(
                    f"{digest}:{result_anchor}:{value_offsets[0]}:{dose_offsets[index]}".encode()).hexdigest()[:22]
                results.append({"candidate_id": cid, "compound_name": requested,
                    "compound_author_names": [requested], "chemical_identity_resolution": {
                        "requested_name": requested, "author_name": requested,
                        "state": "AUTHOR_NAME_ONLY_HOLD", "canonical_structure_verified": False,
                        "source_sha256": digest, "evidence": [dose_ev]},
                    "evidence_kind": "PMC_HTML_AUTHOR_RESULTS_RELATIVE_VALUE",
                    "figure_id": figid, "figure_label": f"Figure {fig_num}", "panel": None,
                    "table_id": None, "scope_evidence": result_ev, "fields": fields,
                    "treatment_stages": [{"phase": "PRETREATMENT", "duration_h": float(schedule_match[1]),
                        "evidence": [citation(schedule_anchor, schedule_match.group(), schedule_match.start())],
                        "total_exposure": False}],
                    "endpoint_kind": "ATP_CONTENT_AUTHOR_REPORTED_RELATIVE_CANDIDATE",
                    "treated_value": None, "control_value": None,
                    "measurement_unit": "% of control group (author reported)",
                    "numeric_origin": "AUTHOR_PROSE_PERCENT_OF_CONTROL",
                    "author_reported_percent_of_control": mean,
                    "author_reported_sd_percent": sd,
                    "percentage_semantics": "AUTHOR_REPORTED_PERCENT_OF_SCALE_CONTROL_WITH_DISTINCT_STATISTICAL_COMPARATOR",
                    "scale_control_arm": "control group (CG=100%)",
                    "statistical_comparator_arm": "model group (MG)",
                    "comparison_evidence": {"scale": citation(result_anchor, denominator_text, text.index(denominator_text)),
                        "comparator": citation(result_anchor, comparator_text, text.index(comparator_text))},
                    "relative_value_evidence": value_ev,
                    "relative_value_literal": value_quote,
                    "relative_uncertainty_kind": "AUTHOR_REPORTED_SD",
                    "reported_sample_size": 6 if re.search(r"\bn\s*=\s*6\b", caption["text"], re.I) else None,
                    "figure_caption_evidence": caption_ev, "assay_method_evidence": method_ev,
                    "numeric_pair_evidence": [result_ev, caption_ev, method_ev],
                    "reported_significance": significance,
                    "reported_significance_evidence": significance_ev,
                    "source_conflicts": [], "review_tier": "Review-B", "train_eligible": False,
                    "hold_reasons": ["INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED",
                        "CHEMICAL_STRUCTURE_IDENTITY_UNVERIFIED", "LICENSE_HOLD",
                        "AUTHOR_REPORTED_RELATIVE_VALUE_NOT_RAW_MEANS",
                        "SCALE_DENOMINATOR_DIFFERS_FROM_STATISTICAL_COMPARATOR",
                        "TOTAL_COMPOUND_EXPOSURE_UNRESOLVED"],
                    "independent_gold": 0, "training_approved": 0,
                    "author_reported_percent": mean})
    return results


def _citation_placeholder(paragraph):
    section = paragraph["section_id"] or "article"
    start = paragraph["sentence_start"]
    quote = paragraph["sentence"]
    return {"source_anchor": f"#{section}/p[{paragraph['section_paragraph_index']}]", "quote": quote,
            "start": start, "end": start + len(quote)}


def source_text_index(raw):
    parser = _Article()
    parser.feed(raw.decode("utf-8", errors="replace"))
    index = {}
    for table_index, table in enumerate(parser.tables, 1):
        table_id = table["section_id"] or f"article-table-{table_index}"
        for row_index, row in enumerate(table["rows"], 1):
            region = row["region"] if row["region"] in ("thead", "tbody", "tfoot") else "tr"
            for col_index, cell in enumerate(row["cells"], 1):
                anchor = f"#{table_id}/table[{table_index}]/{region}/tr[{row_index}]/{cell['tag']}[{col_index}]"
                index[anchor] = cell["text"]
    counts = {}
    for paragraph in parser.paragraphs:
        section = paragraph["section_id"] or "article"
        counts[section] = counts.get(section, 0) + 1
        paragraph["section_paragraph_index"] = counts[section]
        index[f"#{section}/p[{counts[section]}]"] = paragraph["text"]
    for note in parser.footnotes:
        index[f"#{note['id']}"] = note["text"]
    title_counts = {}
    for title in parser.titles:
        lang = title["lang"] or "unknown"
        title_counts[lang] = title_counts.get(lang, 0) + 1
        index[f"#title/lang[{lang}]/p[{title_counts[lang]}]"] = title["text"]
    return parser, index


def _row_oriented_candidates(parser, text_index, table, table_index, digest, compounds):
    """Extract numeric pairs from an explicitly labeled Chinese ATP table.

    A bilingual title supplies only a retrieval alias. It never approves a
    chemical structure, tested form, or missing biological condition.
    """
    rows = table["rows"]
    headers = next((r for r in rows if r["region"] == "thead"), None)
    if headers is None or len(headers["cells"]) < 3:
        return []
    header_cells = headers["cells"]
    names = [c["text"] for c in header_cells]
    metric_columns = [i for i, label in enumerate(names) if CN_ATP_METRIC.search(label)]
    if len(metric_columns) != 1:
        return []
    metric_col = metric_columns[0]
    english = [t for t in parser.titles if t["lang"].casefold().startswith("en")]
    chinese = [t for t in parser.titles if t["lang"].casefold().startswith(("zh", "cn"))]
    if not english or not chinese:
        return []
    model_controls = [r for r in rows if r["region"] == "tbody" and r["cells"]
                      and CN_MODEL_CONTROL.search(r["cells"][0]["text"])]
    if len(model_controls) != 1:
        return []
    control = model_controls[0]
    if len(control["cells"]) != len(names):
        return []
    control_value = _number(control["cells"][metric_col]["text"])
    if control_value is None:
        return []

    en_idx = {t["text"]: i for i, t in enumerate(english, 1)}
    zh_idx = {t["text"]: i for i, t in enumerate(chinese, 1)}
    table_id = table["section_id"] or f"article-table-{table_index}"
    control_row_i = rows.index(control) + 1
    control_value_anchor = f"#{table_id}/table[{table_index}]/tbody/tr[{control_row_i}]/td[{metric_col+1}]"
    control_group_anchor = f"#{table_id}/table[{table_index}]/tbody/tr[{control_row_i}]/td[1]"
    metric_cell = header_cells[metric_col]
    metric_anchor = f"#{table_id}/table[{table_index}]/thead/tr[1]/{metric_cell['tag']}[{metric_col+1}]"
    metric_ev = _citation(digest, metric_anchor, names[metric_col])
    control_value_ev = _citation(digest, control_value_anchor, control["cells"][metric_col]["text"])
    control_group_ev = _citation(digest, control_group_anchor, control["cells"][0]["text"])
    results = []
    for requested in compounds:
        title_en = next((t for t in english if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(requested)}(?![A-Za-z0-9])", t["text"], re.I)), None)
        if title_en is None:
            continue
        title_zh = next((t for t in chinese if any(
            re.sub(r"(?:小|中|大)剂量|剂量|组$", "", r["cells"][0]["text"]).strip()
            and re.sub(r"(?:小|中|大)剂量|剂量|组$", "", r["cells"][0]["text"]).strip() in t["text"]
            for r in rows if r["region"] == "tbody" and r["cells"])), None)
        if title_zh is None:
            continue
        alias_rows = []
        for row in rows:
            if row["region"] != "tbody" or len(row["cells"]) != len(names):
                continue
            group = row["cells"][0]["text"]
            alias = re.sub(r"(?:小|中|大)剂量|剂量|组$", "", group).strip()
            if alias and alias in title_zh["text"]:
                alias_rows.append((row, group, alias))
        for row, group, alias in alias_rows:
            value = _number(row["cells"][metric_col]["text"])
            if value is None:
                continue
            row_i = rows.index(row) + 1
            value_anchor = f"#{table_id}/table[{table_index}]/tbody/tr[{row_i}]/td[{metric_col+1}]"
            group_anchor = f"#{table_id}/table[{table_index}]/tbody/tr[{row_i}]/td[1]"
            value_ev = _citation(digest, value_anchor, row["cells"][metric_col]["text"])
            group_ev = _citation(digest, group_anchor, group)
            english_anchor = f"#title/lang[{title_en['lang']}]/p[{en_idx[title_en['text']]}]"
            chinese_anchor = f"#title/lang[{title_zh['lang']}]/p[{zh_idx[title_zh['text']]}]"
            identity_evidence = [_citation(digest, english_anchor, title_en["text"]),
                                 _citation(digest, chinese_anchor, title_zh["text"])]
            explicit_identity = _explicit_bilingual_name(parser, requested, alias, digest)
            if explicit_identity:
                identity = {**explicit_identity, "evidence": identity_evidence + [group_ev] +
                            explicit_identity["definition_evidence"] + explicit_identity["reagent_evidence"]}
                canonical_compound_name = requested
                canonical_author_names = [alias, requested]
                compound_field_value = requested
                compound_field_state = "AUTHOR_EXPLICIT_BILINGUAL_NAME_REGISTRY_CROSSWALK_PENDING"
                compound_evidence = [group_ev] + explicit_identity["definition_evidence"] + explicit_identity["reagent_evidence"]
            else:
                identity = {"requested_name": requested, "author_name": alias,
                            "state": "AUTHOR_PARALLEL_TITLE_ALIAS_HOLD", "canonical_structure_verified": False,
                            "source_sha256": digest, "evidence": identity_evidence + [group_ev]}
                canonical_compound_name = alias
                canonical_author_names = [alias]
                compound_field_value = alias
                compound_field_state = "AUTHOR_PARALLEL_TITLE_ALIAS_HOLD"
                compound_evidence = identity_evidence + [group_ev]
            context = _author_cn_context(parser, text_index, table_id, alias, digest)
            candidate_id = "ATP-HTML-" + hashlib.sha256(
                f"{digest}:{table_id}:{row_i}:{requested}".encode()).hexdigest()[:22]
            dose_match = re.fullmatch(r"\s*([\d.]+)\s*(μmol/L|µmol/L|umol/L|μM|µM|uM)\s*", context["dose"] or "", re.I)
            dose_uM = float(dose_match[1]) if dose_match else None
            fields = {
                "compound": _field(compound_field_value, compound_evidence, compound_field_state),
                "cell_model": _field(context["model_value"], [context["model"]] if context["model"] else [], "UNIQUE_AUTHOR_ABSTRACT_MODEL_SCOPE"),
                "dose_original": _field(context["dose"], [context["dose_evidence"]] if context["dose_evidence"] else [], "UNIQUE_AUTHOR_EXPERIMENT_DOSE_SCOPE"),
                "dose_uM": _field(dose_uM, [context["dose_evidence"]] if dose_uM is not None else [], "AUTHOR_UNIT_UMOL_PER_L" if dose_uM is not None else None),
                "total_compound_exposure_h": _field(context["time_h"], [context["time_evidence"]] if context["time_evidence"] else [], "UNIQUE_AUTHOR_EXPERIMENT_EXPOSURE"), "treated_arm": _field(group, [group_ev]),
                "comparator_arm": _field(control["cells"][0]["text"], [control_group_ev] + ([context["result"]] if context["result"] else [])),
                "assay_method": _field(context["assay_value"], [context["assay"]] if context["assay"] else [], "UNIQUE_DIRECT_ATP_METHOD" if context["assay_value"] else None), "endpoint": _field(names[metric_col], [metric_ev], "AUTHOR_TABLE_ATP_CONTENT_LABEL"),
                "normalization": _field("CELL_COUNT", [metric_ev], "EXPLICIT_AUTHOR_CELL_COUNT_UNIT")
                    if re.search(r"/\s*10\s*(?:\^\s*)?4\s*个细胞", names[metric_col]) else _field(),
                "measurement_unit": _field(names[metric_col][names[metric_col].find("(")+1:names[metric_col].rfind(")")]
                    if "(" in names[metric_col] and ")" in names[metric_col] else None, [metric_ev]),
                "effect_direction": _field()}
            treated_point_id = "ATP-NUM-" + hashlib.sha256(f"{digest}:{table_id}:{row_i}:{metric_col}".encode()).hexdigest()[:22]
            control_point_id = "ATP-NUM-" + hashlib.sha256(f"{digest}:{table_id}:{control_row_i}:{metric_col}".encode()).hexdigest()[:22]
            treated_point = {"point_id": treated_point_id, "numeric_value": value["mean"], "uncertainty": value["sd"],
                "source_literal": row["cells"][metric_col]["text"], "source_sha256": digest, "table_id": table_id,
                "value_evidence": [value_ev], "metric_author_literal": names[metric_col], "metric_evidence": [metric_ev],
                "author_group_literal": group, "group_evidence": [group_ev]}
            control_point = {"point_id": control_point_id, "numeric_value": control_value["mean"], "uncertainty": control_value["sd"],
                "source_literal": control["cells"][metric_col]["text"], "source_sha256": digest, "table_id": table_id,
                "value_evidence": [control_value_ev], "metric_author_literal": names[metric_col], "metric_evidence": [metric_ev],
                "author_group_literal": control["cells"][0]["text"], "group_evidence": [control_group_ev]}
            holds = ["INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED", "LICENSE_HOLD",
                     "CHEMICAL_PARENT_IDENTITY_UNRESOLVED", "TESTED_CHEMICAL_FORM_UNVERIFIED"]
            if not all((context["model_value"], dose_uM is not None, context["time_h"] is not None, context["assay_value"])):
                holds.append("CELL_MODEL_DOSE_TIME_AND_ASSAY_NOT_BOUND")
            if not context["result"]:
                holds.append("AUTHOR_TABLE_COMPARATOR_RELATION_NOT_CROSS_REFERENCED")
            results.append({"candidate_id": candidate_id, "compound_name": canonical_compound_name,
                "compound_author_names": canonical_author_names,
                "chemical_identity_resolution": identity, "evidence_kind": "PMC_HTML_ROW_ORIENTED_TABLE",
                "figure_id": None, "figure_label": None, "panel": None, "table_id": table_id,
                "scope_evidence": metric_ev, "fields": fields, "treatment_stages": [],
                "endpoint_kind": "ATP_CONTENT_CANDIDATE", "treated_value": value["mean"],
                "control_value": control_value["mean"], "measurement_unit": fields["measurement_unit"]["value"],
                "numeric_origin": "AUTHOR_TABLE_REPORTED", "native_points": {"treated": treated_point, "control": control_point},
                "numeric_pair_evidence": [value_ev, control_value_ev, metric_ev] + ([context["result"]] if context["result"] else []),
                "reported_significance": None, "reported_significance_evidence": None, "source_conflicts": [],
                "review_tier": "Review-B", "train_eligible": False,
                "hold_reasons": holds,
                "independent_gold": 0, "training_approved": 0, "author_reported_percent": None})
    return results


def extract_pmc_html_relations(source_path, expected_sha256, compounds, protocol):
    path = Path(source_path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH")
    parser, text_index = source_text_index(raw)
    paragraphs_by_section = {}
    for p in parser.paragraphs:
        paragraphs_by_section.setdefault(p["section_id"], []).append(p)
    compounds = sorted(set(compounds))
    paper_id = _canonical_url(parser.meta, path)
    doi = parser.meta.get("citation_doi")
    title = parser.meta.get("citation_title")
    candidates, numeric_points = [], []
    for table_index, table in enumerate(parser.tables, 1):
        rows = table["rows"]
        header_rows = [r for r in rows if r["region"] == "thead"]
        if len(header_rows) != 1 or any(int(c["rowspan"]) != 1 or int(c["colspan"]) != 1 for c in header_rows[0]["cells"]):
            continue
        headers = header_rows[0]["cells"]
        names = [c["text"] for c in headers]
        if any(CN_ATP_METRIC.search(name) for name in names):
            row_candidates = _row_oriented_candidates(parser, text_index, table, table_index, digest, compounds)
            candidates.extend(row_candidates)
            for candidate in row_candidates:
                numeric_points.extend(candidate["native_points"].values())
            continue
        if len(names) < 3 or len(set(names)) < 3:
            continue
        for row_index, row in enumerate(rows, 1):
            if row["region"] != "tbody" or len(row["cells"]) != len(names):
                continue
            cells = row["cells"]
            metric = cells[0]["text"]
            metric_match = METRIC.fullmatch(metric)
            if not metric_match:
                continue
            unit = metric_match[1]
            table_id = table["section_id"] or f"article-table-{table_index}"
            row_anchor = f"#{table_id}/table[{table_index}]/tbody/tr[{row_index}]"
            metric_ev = _citation(digest, f"{row_anchor}/td[1]", metric)
            cell_values = [_number(c["text"]) for c in cells]
            for treated_col, treatment_header in enumerate(names[1:], 1):
                if CONTROL.match(treatment_header):
                    continue
                model_match = MODEL.search(treatment_header)
                if not model_match:
                    continue
                model = model_match[1].strip()
                compound = treatment_header[:model_match.start()].strip()
                if compound.casefold() not in {x.casefold() for x in compounds}:
                    continue
                control_cols = [i for i, h in enumerate(names[1:], 1)
                                if CONTROL.match(h) and MODEL.search(h)
                                and MODEL.search(h)[1].strip().casefold() == model.casefold()]
                if len(control_cols) != 1:
                    continue
                control_col = control_cols[0]
                if cell_values[treated_col] is None or cell_values[control_col] is None:
                    continue
                treated_header_anchor = f"#{table_id}/table[{table_index}]/thead/tr[1]/th[{treated_col+1}]"
                control_header_anchor = f"#{table_id}/table[{table_index}]/thead/tr[1]/th[{control_col+1}]"
                treated_anchor = f"{row_anchor}/td[{treated_col+1}]"
                control_anchor = f"{row_anchor}/td[{control_col+1}]"
                treatment_ev = _citation(digest, treated_header_anchor, treatment_header)
                control_header_ev = _citation(digest, control_header_anchor, names[control_col])
                treated_ev = _citation(digest, treated_anchor, cells[treated_col]["text"])
                control_value_ev = _citation(digest, control_anchor, cells[control_col]["text"])
                point_id = "ATP-NUM-" + hashlib.sha256(f"{digest}:{row_anchor}:{treated_col}".encode()).hexdigest()[:22]
                treated_point = {"point_id": point_id, "numeric_value": cell_values[treated_col]["mean"],
                    "uncertainty": cell_values[treated_col]["sd"], "source_literal": cells[treated_col]["text"],
                    "source_sha256": digest, "table_id": table_id, "value_evidence": [treated_ev],
                    "metric_author_literal": metric, "metric_evidence": [metric_ev],
                    "author_group_literal": treatment_header, "group_evidence": [treatment_ev]}
                control_point_id = "ATP-NUM-" + hashlib.sha256(f"{digest}:{row_anchor}:{control_col}".encode()).hexdigest()[:22]
                control_point = {"point_id": control_point_id, "numeric_value": cell_values[control_col]["mean"],
                    "uncertainty": cell_values[control_col]["sd"], "source_literal": cells[control_col]["text"],
                    "source_sha256": digest, "table_id": table_id, "value_evidence": [control_value_ev],
                    "metric_author_literal": metric, "metric_evidence": [metric_ev],
                    "author_group_literal": names[control_col], "group_evidence": [control_header_ev]}
                # Only use explicit in-table concentration; never infer it from another assay's methods.
                dose_match = re.search(r"\b([\d.]+)\s*(μM|µM|uM|mM|μg/mL|µg/mL|μg/ml|µg/ml|mg/mL|mg/ml)\b", treatment_header, re.I)
                dose_original = dose_match.group(0) if dose_match else None
                dose_value = float(dose_match[1]) if dose_match and dose_match[2].casefold() in ("μm", "µm", "um") else None
                identity = {"requested_name": compound, "author_name": compound,
                            "state": "AUTHOR_NAME_ONLY_HOLD", "canonical_structure_verified": False,
                            "source_sha256": digest, "evidence": [treatment_ev]}
                fields = {
                    "compound": _field(compound, [treatment_ev], "SOURCE_BOUND_CANDIDATE"),
                    "cell_model": _field(model, [treatment_ev], "EXPLICIT_SAME_TABLE_MODEL_HEADER"),
                    "dose_original": _field(dose_original, [treatment_ev] if dose_original else []),
                    "dose_uM": _field(dose_value, [treatment_ev] if dose_value is not None else []),
                    "total_compound_exposure_h": _field(),
                    "treated_arm": _field(treatment_header, [treatment_ev]),
                    "comparator_arm": _field(names[control_col], [control_header_ev]),
                    "assay_method": _field(),
                    "endpoint": _field(metric, [metric_ev], "AUTHOR_TABLE_ATP_CONTENT_LABEL"),
                    "normalization": _field("PROTEIN", [metric_ev]) if re.search(r"/\s*(?:g|mg)\s*prot", unit, re.I) else _field(),
                    "measurement_unit": _field(unit, [metric_ev]),
                    "effect_direction": _field(),
                }
                known_models = [m.group(1).strip() for h in names if (m := MODEL.search(h))]
                conflicts = _sentiment_conflict(model, paragraphs_by_section.get("abstract1", []) + paragraphs_by_section.get("sec15", []), known_models)
                for conflict in conflicts:
                    conflict["abstract_evidence"] = {**conflict["abstract_evidence"], "source_sha256": digest,
                        "offset_basis": "normalized PMC HTML DOM text"}
                    conflict["results_evidence"] = {**conflict["results_evidence"], "source_sha256": digest,
                        "offset_basis": "normalized PMC HTML DOM text"}
                holds = ["INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED", "TESTED_CHEMICAL_FORM_UNVERIFIED", "LICENSE_HOLD",
                         "ATP_SPECIFIC_METHOD_UNRESOLVED", "DOSE_AND_EXPOSURE_NOT_BOUND_TO_ATP_TABLE"]
                if conflicts:
                    holds.append("AUTHOR_RESULT_CONFLICT_HOLD")
                cid = "ATP-HTML-" + hashlib.sha256(f"{digest}:{row_anchor}:{treated_col}:{compound}".encode()).hexdigest()[:22]
                candidate = {"candidate_id": cid, "compound_name": compound,
                    "compound_author_names": [compound], "chemical_identity_resolution": identity,
                    "evidence_kind": "PMC_HTML_NATIVE_TABLE", "figure_id": None, "figure_label": None,
                    "panel": None, "table_id": table_id, "scope_evidence": metric_ev,
                    "fields": fields, "treatment_stages": [], "endpoint_kind": "ATP_CONTENT_CANDIDATE",
                    "treated_value": treated_point["numeric_value"], "control_value": control_point["numeric_value"],
                    "measurement_unit": unit, "numeric_origin": "AUTHOR_TABLE_REPORTED",
                    "native_points": {"treated": treated_point, "control": control_point},
                    "numeric_pair_evidence": [treated_ev, control_value_ev, metric_ev],
                    "reported_significance": None, "reported_significance_evidence": None,
                    "source_conflicts": conflicts, "review_tier": "Review-B", "train_eligible": False,
                    "hold_reasons": sorted(set(holds)), "independent_gold": 0, "training_approved": 0,
                    "author_reported_percent": None}
                candidates.append(candidate)
                numeric_points.extend([treated_point, control_point])
    candidates.extend(_author_percent_result_candidates(parser, digest, paper_id, compounds))
    if not paper_id:
        raise ValueError("PMC_HTML_PMCID_UNRESOLVED")
    return {"schema": protocol, "source_format": "PMC_HTML", "paper": {"pmcid": paper_id,
        "doi": doi, "title": title}, "source_path": str(path.resolve()), "source_sha256": digest,
        "requested_compounds": compounds, "license_author_literal": [], "license_training_approval": False,
        "author_entity_resolution": [c["chemical_identity_resolution"] for c in candidates],
        "candidates": candidates, "native_numeric_points": numeric_points,
        "figure_inventory": [], "original_evidence_stats": {"atp_table_count": len(parser.tables),
            "numeric_points": len(numeric_points)},
        "scientific_metrics": {"precision": None, "recall": None, "f1": None, "human_gold": 0},
        "independent_scientific_validation": False, "training_approved": False}


def verify_pmc_html_relations(data, source_path, extract_fn):
    raw = Path(source_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != data.get("source_sha256"):
        raise ValueError("ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH")
    rerun = extract_fn(source_path, data["source_sha256"], data["requested_compounds"], data["schema"])
    if json.loads(json.dumps(data, sort_keys=True)) != json.loads(json.dumps(rerun, sort_keys=True)):
        raise ValueError("ATP_DETERMINISTIC_REPLAY_MISMATCH")
    parser, index = source_text_index(raw)
    checked = set()
    def walk(value):
        if isinstance(value, dict):
            if {"source_anchor", "quote", "source_sha256"} <= set(value):
                anchor = value["source_anchor"]
                text = index.get(anchor)
                if text is None or text[value["start"]:value["end"]] != value["quote"] or value["source_sha256"] != data["source_sha256"]:
                    raise ValueError("ATP_HTML_SOURCE_ANCHOR_REPLAY_MISMATCH")
                checked.add((anchor, value["start"], value["end"]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return {"source_replay_pass": True, "field_citation_checks": len(checked),
            "independent_scientific_validation": False, "training_approved": False}

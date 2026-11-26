"""Deterministic Ch2-S1B offline process-condition XML backfill & UNVERIFIED_HOLD audit.

Strict $0 offline, zero-LLM, zero-network guarantees:
1. Indexes local JATS/NLM XML files under AsterLoom-Agents/production_ch2*.
2. Parses section-aware text blocks and tables, matching event monomers/catalysts/
   targets before running context-guarded deterministic regex extractors for:
   - feed_ratio_m_to_i
   - initiator_loading_mol_pct (plus separate inferred_m_to_i_from_mol_pct)
   - monomer_feed_mol_pct
   - temp_c (strictly excluding GPC column / oven drying / Tg / Tm / cell assay temps)
   - time_h (strictly excluding drying / dialysis / washing / storage / release times)
3. Audits 390 UNVERIFIED_HOLD rows offline against frozen NSML v0.2 reference tables
   (three-tier bucket classification, never modifying outcome labels without approval)
   and scans local XMLs for missing GPC/NMR/FTIR characterization proofs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

try:
    from .ch2_condition_features import parse_text_list
except ImportError:
    from r031_evidence.ch2_condition_features import parse_text_list


@dataclass(frozen=True)
class XmlTextBlock:
    paper_id: str
    block_kind: str  # "paragraph" | "table_row"
    section_title: str
    is_methods_section: bool
    is_characterization_only: bool
    text: str


@dataclass(frozen=True)
class BackfillMatch:
    field: str
    value: float
    match_rule: str
    verbatim_quote: str
    context_score: int


_METHODS_SEC_RE = re.compile(
    r"(?i)\b(?:method|experimental|synthesis|polymeriz|copolymeriz|prepar|material|procedure|general|fabrication)\b"
)
_CHAR_ONLY_SEC_RE = re.compile(
    r"(?i)\b(?:characteriz|instrument|measurement|gpc|sec\b|nmr|ftir|tga|dsc|thermal\s+propert|biological|"
    r"cell\s+culture|cytotox|antimicrobial|in\s+vitro|in\s+vivo|biocompatib|drug\s+release|statistics)\b"
)
_POLYMER_ACTION_RE = re.compile(
    r"(?i)\b(?:polymeriz|copolymeriz|synthes|ring[- ]opening|rop\b|raft\b|atrp\b|polycondens|"
    r"esterif|graft(?:ed|ing)?|react(?:ed|ion)|stir(?:red|ring)|heat(?:ed|ing)|reflux|cur(?:ed|ing)|"
    r"irradiat|monomer|initiator|catalyst|molar\s+ratio|\[m\]|feed\s+ratio|carried\s+out|maintained\s+at|"
    r"kept\s+at|heated\s+at|heated\s+to)\b"
)

# Split complex sentences into clauses at clear transition markers so "heated at 80 °C for 24 h and then dried at 60 °C" isolates safely
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[;]|(?:\.\s+)|,\s*(?=(?:and\s+)?(?:then|followed\s+by|subsequently|afterward|after|dried|drying|"
    r"dialyz|precipitat|wash|purif|analyz|characteriz|measured|determined|characterised)))"
)

_TEMP_NEG_RE = re.compile(
    r"(?i)(?:\bgpc\b|\bsec\b|\bhplc\b|column\s+temp|eluent|mobile\s+phase|\boven\b|\bdried\b|\bdrying\b|"
    r"\bvacuo\b|\bvacuum\b|lyophiliz|freeze[- ]?dr(?:y|ied)|\btga\b|\bdsc\b|\btg\b|\btm\b|"
    r"glass\s+transition|melting\s+(?:point|temp)|decompos|anneal|calcin|storage|stored\s+at|"
    r"refrigerat|freezer|centrifug|incubator|drug\s+release|release\s+kinetics|release\s+profil|"
    r"depolymeriz|degradat|hydrolys|petase|cutinase|ph\s*7\.4|phosphate\s+buffer|cytotox|cell\s+viabil|"
    r"incuba(?:te|ted|tion)\b.*(?:cell|medium|pbs|buffer|culture|bacteria|release|enzyme|petase|cutinase)|"
    r"thermal\s+stability|\btd\d*\b)"
)

_TIME_NEG_RE = re.compile(
    r"(?i)(?:\bdried\b|\bdrying\b|dialyz|dialys|precipitat|wash(?:ed|ing)?|centrifug|\bvacuo\b|\bvacuum\b|"
    r"lyophiliz|freeze[- ]?dr(?:y|ied)|purg(?:e|ed|ing)|degas|sparg|stored|storage|release|degrad|"
    r"depolymeriz|petase|cutinase|incubator|cytotox|cell\s+viabil|\bgpc\b|\bsec\b|"
    r"dissolv|dissolution|solubiliz|swelling|sonicat|soaked|immersed|aged|aging|"
    r"incuba(?:te|ted|tion)\b.*(?:cell|medium|pbs|buffer|culture|bacteria|release|enzyme|petase|cutinase))"
)

_POSITIVE_TEMP_VERB_RE = re.compile(
    r"(?i)(?:polymeriz|copolymeriz|react(?:ed|ion)|stir(?:red|ring)|heat(?:ed|ing)|reflux|cur(?:ed|ing)|"
    r"maintained|kept|carried\s+out|held|rop\b|raft\b|atrp\b|polycondens|synthes|graft)"
)

_POSITIVE_TIME_VERB_RE = re.compile(
    r"(?i)(?:polymeriz|copolymeriz|react(?:ed|ion)|stir(?:red|ring)|heat(?:ed|ing)|reflux|cur(?:ed|ing)|"
    r"irradiat|maintained|kept|carried\s+out|allowed\s+to\s+(?:proceed|react)|rop\b|raft\b|atrp\b|polycondens|"
    r"synthes|graft)"
)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_quote(text: str, max_len: int = 200) -> str:
    clean = _normalize_ws(text)
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def _split_sentences(text: str) -> List[str]:
    clean = _normalize_ws(text)
    if not clean:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])", clean)
    return [p.strip() for p in parts if p.strip()]


def _extract_clauses(sentence: str) -> List[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(sentence) if p.strip()]
    return parts if parts else ([sentence] if sentence.strip() else [])


# ---------------------------------------------------------------------------
# XML Indexing & Structured Context Extraction
# ---------------------------------------------------------------------------


def index_local_ch2_xmls(agents_root: Path) -> Dict[str, Path]:
    """Recursively index author.xml and PMC*.xml under production_ch2* directories."""
    root = Path(agents_root)
    xml_map: Dict[str, Path] = {}
    for batch_dir in sorted(root.glob("production_ch2*")):
        if not batch_dir.is_dir():
            continue
        for xml_path in sorted(batch_dir.rglob("*.xml")):
            if xml_path.name == "author.xml":
                paper_id = xml_path.parent.name
                xml_map.setdefault(paper_id, xml_path)
            elif xml_path.name.startswith("PMC"):
                paper_id = xml_path.stem
                xml_map.setdefault(paper_id, xml_path)
    return xml_map


def parse_xml_contexts(xml_path: Path, paper_id: str = "") -> List[XmlTextBlock]:
    """Extract section-annotated sentences and table rows from a JATS/NLM XML file."""
    pid = paper_id or (xml_path.parent.name if xml_path.name == "author.xml" else xml_path.stem)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    blocks: List[XmlTextBlock] = []

    def _local_tag(el: ET.Element) -> str:
        return el.tag.rsplit("}", 1)[-1]

    def _walk_sec(el: ET.Element, sec_stack: Tuple[str, ...]) -> None:
        tag = _local_tag(el)
        if tag == "sec":
            title_el = next((c for c in el if _local_tag(c) == "title"), None)
            t_txt = _normalize_ws("".join(title_el.itertext())) if title_el is not None else ""
            new_stack = sec_stack + ((t_txt,) if t_txt else ())
            for child in el:
                _walk_sec(child, new_stack)
            return

        if tag == "table-wrap":
            cap_el = next((c for c in el if _local_tag(c) == "caption"), None)
            caption = _normalize_ws("".join(cap_el.itertext())) if cap_el is not None else ""
            sec_title = " > ".join(sec_stack) if sec_stack else caption
            full_hdr = f"{sec_title} {caption}"
            is_char = bool(_CHAR_ONLY_SEC_RE.search(full_hdr))
            is_meth = bool(_METHODS_SEC_RE.search(full_hdr))

            headers: List[str] = []
            thead = el.find(".//thead")
            if thead is not None:
                for tr in thead.findall(".//tr"):
                    cells = [_normalize_ws("".join(td.itertext())) for td in tr]
                    if any(cells):
                        headers = cells
                        break
            for tr in el.findall(".//tbody//tr") or el.findall(".//tr"):
                cells = [_normalize_ws("".join(td.itertext())) for td in tr]
                if not any(cells) or cells == headers:
                    continue
                if headers and len(headers) == len(cells):
                    row_str = "; ".join(f"{h}: {v}" if h else v for h, v in zip(headers, cells) if v)
                else:
                    row_str = " | ".join(c for c in cells if c)
                if caption:
                    row_str = f"[{caption[:80]}] {row_str}"
                blocks.append(
                    XmlTextBlock(
                        paper_id=pid,
                        block_kind="table_row",
                        section_title=sec_title,
                        is_methods_section=is_meth,
                        is_characterization_only=is_char,
                        text=row_str,
                    )
                )
            return

        if tag == "p":
            sec_title = " > ".join(sec_stack)
            is_char = bool(_CHAR_ONLY_SEC_RE.search(sec_title))
            is_meth = bool(_METHODS_SEC_RE.search(sec_title))
            p_text = _normalize_ws("".join(el.itertext()))
            for sent in _split_sentences(p_text):
                blocks.append(
                    XmlTextBlock(
                        paper_id=pid,
                        block_kind="paragraph",
                        section_title=sec_title,
                        is_methods_section=is_meth,
                        is_characterization_only=is_char,
                        text=sent,
                    )
                )
            return

        for child in el:
            _walk_sec(child, sec_stack)

    _walk_sec(root, ())
    return blocks


# ---------------------------------------------------------------------------
# Deterministic Clause-Guarded Condition Extractors
# ---------------------------------------------------------------------------

# Explicit [M]/[I] or [M]/[CTA] or M/I or target DP regexes
_RE_M_TO_I_TRIPLE = re.compile(
    r"(?i)(?:\[\s*M\s*\]\s*0?\s*/\s*\[\s*(?:CTA|I)\s*\]\s*0?\s*/\s*\[\s*(?:I|CTA)\s*\]\s*0?)\s*(?:=|of|was|is|:)?\s*"
    r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)"
)
_RE_M_TO_I_COLON = re.compile(
    r"(?i)(?:\[\s*M\s*\]\s*0?\s*/\s*\[\s*(?:I|CTA|Init|Cat(?:alyst)?|Ini)\s*\]\s*0?|"
    r"\[\s*M\s*\]\s*0?\s*:\s*\[\s*(?:I|CTA|Init|Cat)\s*\]\s*0?|"
    r"monomer[- ]to[- ](?:initiator|catalyst|cta)\s+(?:molar\s+)?ratio|"
    r"monomer\s*/\s*(?:initiator|catalyst|cta)\s*(?:molar\s+)?ratio|"
    r"\bM\s*/\s*(?:I|CTA)\b)"
    r"[^0-9.\n]{0,24}?(?:=|of|was|is|:|at)?\s*(\d+(?:\.\d+)?)(?:\s*:\s*1\b)?"
)
_RE_TARGET_DP = re.compile(
    r"(?i)(?:target(?:ed)?\s+DP|theoretical\s+DP|DP\s*target)\s*(?:of|=|:|was)?\s*(\d+(?:\.\d+)?)\b"
)

# Initiator / catalyst loading mol%
_INITIATOR_KW_RE = re.compile(
    r"(?i)(?:\binitiator\b|\bcatalyst\b|\bcatal\b|\bcta\b|\baibn\b|\bbpo\b|sn\(?oct\)?2|stannous\s+octoate|"
    r"\bdbu\b|\btbd\b|\bmtbd\b|\bdmap\b|\bdabco\b|\baps\b|\bkps\b|\bacva\b|\bv-?50\b|\bva-?044\b|\bvam-?110\b|"
    r"photoinitiator|irgacure|\btpo\b|\bdmpa\b|\bp-?tsa\b|\bptsa\b|ti\(o?bu\)4|ti\(buo\)4|\bedc\b|\bnhs\b)"
)
_RE_MOL_PCT = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:mol\s*%|%\s*mol\b)")

# Monomer feed mol% or binary feed ratio near monomer/comonomer
_RE_FEED_RATIO_PAIR = re.compile(
    r"(?i)(?:feed\s+(?:molar\s+)?ratio|molar\s+(?:feed\s+)?ratio|comonomer\s+ratio)\s*(?:of|=|:|was)?\s*"
    r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)"
)

# Reaction temperature & time
_RE_TEMP_C = re.compile(
    r"(?i)(?:^|[\s:(])(-?\d+(?:\.\d+)?)\s*(?:°\s*C|℃|degrees?\s*C)\b"
)
_RE_RT_TEMP = re.compile(r"(?i)\b(?:room\s+temperature|ambient\s+temperature|r\.?\s*t\.?)\b")
_RE_TIME_H = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?)\s*)?"
    r"(hours?|hrs?|hr|h|minutes?|mins?|min|days?|d)\b"
)


def _event_keywords(row: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    monomer_tokens: List[str] = []
    for key in ("author_monomer_literal", "canonical_parent"):
        val = str(row.get(key) or "").strip()
        if not val:
            continue
        # Extract parenthetical abbreviation too, e.g. "(EgGMA)"
        for ab in re.findall(r"\(([A-Za-z0-9-]{2,15})\)", val):
            monomer_tokens.append(ab.lower())
        base = re.sub(r"\([^)]*\)", " ", val).strip().lower()
        for tok in re.split(r"[\s,;]+", base):
            tok = tok.strip("()[]")
            if len(tok) >= 4 and tok not in ("acid", "ester", "ether", "methyl", "ethyl", "natural", "monomer"):
                monomer_tokens.append(tok)

    cat_tokens = [c.lower() for c in parse_text_list(row.get("catalysts_or_initiators")) if len(c.strip()) >= 2]
    comon_tokens = [c.lower() for c in parse_text_list(row.get("co_monomers")) if len(c.strip()) >= 3]
    return list(dict.fromkeys(monomer_tokens)), cat_tokens, comon_tokens


def _score_block_for_event(block: XmlTextBlock, row: Dict[str, Any]) -> int:
    low = block.text.lower()
    score = 0
    if block.is_methods_section:
        score += 4
    if block.block_kind == "table_row":
        score += 3
    if block.is_characterization_only:
        score -= 5
    if _POLYMER_ACTION_RE.search(block.text):
        score += 3

    mon_toks, cat_toks, comon_toks = _event_keywords(row)
    if any(t in low for t in mon_toks):
        score += 4
    if any(t in low for t in cat_toks):
        score += 2
    if any(t in low for t in comon_toks):
        score += 2

    # Exact target number match boosts exact reaction selection in multi-reaction papers
    mn = row.get("de_novo_chain_mn_kda")
    if mn is not None:
        try:
            mn_s = f"{float(mn):g}"
            if len(mn_s) >= 2 and mn_s in low:
                score += 4
        except (TypeError, ValueError):
            pass
    return score


def extract_m_to_i_from_clause(clause: str) -> Optional[Tuple[float, str]]:
    """Deterministically extract [M]/[I] molar ratio from one clause."""
    m3 = _RE_M_TO_I_TRIPLE.search(clause)
    if m3:
        m_val = float(m3.group(1))
        # Handle [M]0/[CTA]0/[I]0 = 500:5:1 -> M/I = 500/1 = 500
        i_val = float(m3.group(3))
        ratio = m_val / i_val if i_val > 0 else m_val
        if 1.0 <= ratio <= 10000.0:
            return round(ratio, 4), "m_to_cta_to_i_ratio"

    m = _RE_M_TO_I_COLON.search(clause)
    if m:
        val = float(m.group(1))
        if 1.0 <= val <= 10000.0:
            return round(val, 4), "explicit_m_to_i_regex"

    mdp = _RE_TARGET_DP.search(clause)
    if mdp:
        val = float(mdp.group(1))
        if 2.0 <= val <= 10000.0:
            return round(val, 4), "explicit_target_dp"
    return None


def extract_initiator_loading_from_clause(
    clause: str,
    cat_tokens: Sequence[str] = (),
) -> Optional[Tuple[float, str]]:
    """Deterministically extract initiator/catalyst loading in mol% within local catalyst context."""
    for m in _RE_MOL_PCT.finditer(clause):
        win = clause[max(0, m.start() - 45) : min(len(clause), m.end() + 45)]
        pre = clause[max(0, m.start() - 35) : m.start()].lower()
        post_short = clause[m.end() : min(len(clause), m.end() + 25)]
        if re.search(r"\b(?:co)?monomers?\s*[:(\[]\s*[^)\]]*$", pre) and not _INITIATOR_KW_RE.search(post_short):
            continue
        has_local_cat = bool(_INITIATOR_KW_RE.search(win)) or any(t in win.lower() for t in cat_tokens)
        if not has_local_cat:
            continue
        val = float(m.group(1))
        if 0.001 <= val <= 50.0:
            return round(val, 4), "initiator_mol_pct_regex"
    return None


def extract_monomer_feed_from_clause(
    clause: str,
    monomer_tokens: Sequence[str] = (),
    is_monomer_anchored: bool = False,
) -> Optional[Tuple[float, str]]:
    """Deterministically extract target monomer feed mol% when anchored to monomer context."""
    if re.search(r"(?i)(?:depolymeriz|degradat|\bio4\b|naio4|periodate|oxidant|cpticl3|metal\s+catalyst)", clause):
        return None
    low = clause.lower()
    has_mon = is_monomer_anchored or any(t in low for t in monomer_tokens)
    has_feed_kw = bool(re.search(r"(?i)\b(?:feed|molar\s+ratio|copolymeriz|comonomer|monomer)\b", clause))
    if not (has_mon and has_feed_kw):
        # Also allow explicit '<pct> mol% <monomer>'
        if has_mon:
            for m in _RE_MOL_PCT.finditer(clause):
                if _INITIATOR_KW_RE.search(clause[max(0, m.start() - 35) : m.end() + 35]):
                    continue
                val = float(m.group(1))
                if 0.5 <= val <= 99.5:
                    return round(val, 4), "monomer_mol_pct_anchored"
        return None

    m_pair = _RE_FEED_RATIO_PAIR.search(clause)
    if m_pair:
        a = float(m_pair.group(1))
        b = float(m_pair.group(2))
        if a > 0 and b > 0:
            pct = 100.0 * a / (a + b)
            if 0.5 <= pct <= 99.5:
                return round(pct, 4), "monomer_feed_ratio_pair"

    for m in _RE_MOL_PCT.finditer(clause):
        # Skip initiator mol% in same clause
        win = clause[max(0, m.start() - 25) : m.end() + 25]
        if _INITIATOR_KW_RE.search(win):
            continue
        val = float(m.group(1))
        if 0.5 <= val <= 99.5:
            return round(val, 4), "monomer_feed_mol_pct"
    return None


def extract_temp_c_from_clause(clause: str) -> Optional[Tuple[float, str]]:
    """Extract reaction temp_c while guarding against GPC column / oven drying / Tg / Tm / cell assay temps."""
    if _TEMP_NEG_RE.search(clause):
        return None
    has_rxn_ctx = bool(_POSITIVE_TEMP_VERB_RE.search(clause)) or bool(_POLYMER_ACTION_RE.search(clause))
    for m in _RE_TEMP_C.finditer(clause):
        val = float(m.group(1))
        if -80.0 <= val <= 320.0:
            # Skip obvious ambient mention if GPC/TGA already screened, require action or temperature cue
            if has_rxn_ctx or re.search(r"(?i)(?:reaction|polymeriz|heated|at)\s+-?\d", clause):
                return round(val, 2), "temp_celsius_regex"
    if has_rxn_ctx and _RE_RT_TEMP.search(clause):
        return 25.0, "temp_room_temperature"
    return None


def extract_time_h_from_clause(clause: str) -> Optional[Tuple[float, str]]:
    """Extract reaction time_h while guarding against drying / dialysis / precipitate / storage durations."""
    if _TIME_NEG_RE.search(clause):
        return None
    if not (_POSITIVE_TIME_VERB_RE.search(clause) or re.search(r"(?i)\b(?:reaction\s+time|polymerization\s+time|time\s*:)\b", clause)):
        return None
    for m in _RE_TIME_H.finditer(clause):
        v1 = float(m.group(1))
        unit = m.group(3).lower()
        if m.group(2) is not None:
            v2 = float(m.group(2))
            val = 0.5 * (v1 + v2)
        else:
            val = v1
        if unit.startswith("min"):
            hours = val / 60.0
            rule = "time_minutes_to_hours"
        elif unit.startswith("d"):
            hours = val * 24.0
            rule = "time_days_to_hours"
        else:
            hours = val
            rule = "time_hours_regex"
        if 0.01 <= hours <= 720.0:
            return round(hours, 4), rule
    return None


def extract_conditions_for_event(
    row: Dict[str, Any],
    blocks: Sequence[XmlTextBlock],
) -> Tuple[Dict[str, Optional[float]], List[Dict[str, Any]]]:
    """Extract missing B2 conditions deterministically from a paper's XML blocks.

    Never overwrites existing non-null feed_ratio_m_to_i, monomer_feed_mol_pct,
    temp_c, or time_h values.
    """
    need_m_i = row.get("feed_ratio_m_to_i") is None
    need_mon_feed = row.get("monomer_feed_mol_pct") is None
    need_temp = row.get("temp_c") is None
    need_time = row.get("time_h") is None
    need_init_load = True

    scored = sorted(
        ((_score_block_for_event(b, row), idx, b) for idx, b in enumerate(blocks)),
        key=lambda x: (-x[0], x[1]),
    )
    mon_toks, cat_toks, _ = _event_keywords(row)
    role = str(row.get("monomer_role") or "").strip().upper()

    best_matches: Dict[str, BackfillMatch] = {}

    for blk_score, _, blk in scored:
        if blk_score < 0:
            continue
        clauses = _extract_clauses(blk.text) if blk.block_kind == "paragraph" else [blk.text]
        blk_has_mon = any(t in blk.text.lower() for t in mon_toks)

        for clause in clauses:
            if need_m_i and "feed_ratio_m_to_i" not in best_matches:
                res = extract_m_to_i_from_clause(clause)
                if res is not None:
                    best_matches["feed_ratio_m_to_i"] = BackfillMatch(
                        "feed_ratio_m_to_i", res[0], res[1], _truncate_quote(blk.text), blk_score
                    )

            if need_init_load and "initiator_loading_mol_pct" not in best_matches:
                res = extract_initiator_loading_from_clause(clause, cat_toks)
                if res is not None:
                    best_matches["initiator_loading_mol_pct"] = BackfillMatch(
                        "initiator_loading_mol_pct", res[0], res[1], _truncate_quote(blk.text), blk_score
                    )

            if need_mon_feed and "monomer_feed_mol_pct" not in best_matches:
                res = extract_monomer_feed_from_clause(clause, mon_toks, is_monomer_anchored=blk_has_mon)
                if res is not None:
                    best_matches["monomer_feed_mol_pct"] = BackfillMatch(
                        "monomer_feed_mol_pct", res[0], res[1], _truncate_quote(blk.text), blk_score
                    )

            if need_temp and "temp_c" not in best_matches:
                res = extract_temp_c_from_clause(clause)
                if res is not None:
                    best_matches["temp_c"] = BackfillMatch(
                        "temp_c", res[0], res[1], _truncate_quote(blk.text), blk_score
                    )

            if need_time and "time_h" not in best_matches:
                res = extract_time_h_from_clause(clause)
                if res is not None:
                    best_matches["time_h"] = BackfillMatch(
                        "time_h", res[0], res[1], _truncate_quote(blk.text), blk_score
                    )

        if len(best_matches) == (
            int(need_m_i) + int(need_init_load) + int(need_mon_feed) + int(need_temp) + int(need_time)
        ):
            break

    extracted_vals: Dict[str, Optional[float]] = {k: m.value for k, m in best_matches.items()}
    audit_records: List[Dict[str, Any]] = [
        {
            "event_id": row["event_id"],
            "paper_id": row["paper_id"],
            "field": m.field,
            "extracted_value": m.value,
            "match_rule": m.match_rule,
            "context_score": m.context_score,
            "verbatim_quote": m.verbatim_quote,
        }
        for m in best_matches.values()
    ]

    # Separate inferred M/I ratio ONLY when initiator_loading_mol_pct exists and体系 fits single-initiator ROP/homopolymer
    init_load = extracted_vals.get("initiator_loading_mol_pct")
    if init_load is not None and init_load > 0 and role in ("HOMOPOLYMERIZATION", "DERIVATIVE_POLYMERIZATION"):
        inferred = round(100.0 / init_load, 4)
        extracted_vals["inferred_m_to_i_from_mol_pct"] = inferred
        audit_records.append(
            {
                "event_id": row["event_id"],
                "paper_id": row["paper_id"],
                "field": "inferred_m_to_i_from_mol_pct",
                "extracted_value": inferred,
                "match_rule": "inferred_100_div_initiator_loading_mol_pct",
                "context_score": best_matches["initiator_loading_mol_pct"].context_score,
                "verbatim_quote": best_matches["initiator_loading_mol_pct"].verbatim_quote,
            }
        )

    return extracted_vals, audit_records


# ---------------------------------------------------------------------------
# Task 2: UNVERIFIED_HOLD 3-Tier Offline Audit & XML Proof Rescue
# ---------------------------------------------------------------------------

BUCKET_A = "BUCKET_A_STRONG_NATURAL_MONOMER_CANDIDATE"
BUCKET_B = "BUCKET_B_NATURAL_DERIVATIVE_NEEDS_PARENT_MAPPING"
BUCKET_C = "BUCKET_C_CORRECTLY_HELD_NON_NATURAL_OR_POLYMER"

# Explicit macromolecules, polysaccharides, oils, mixtures -> Bucket C
_MACRO_OIL_MIXTURE_RE = re.compile(
    r"(?i)(?:\bcellulose\b|\bchitosan\b|\bstarch\b|\blignin\b|\bamylose\b|\bamylopectin\b|\bdextrin\b|"
    r"\bcastor\s+oil\b|\brosin\b|\bsoybean\s+oil\b|\bgelatin\b|\balginate\b|\bpectin\b|\bpullulan\b|"
    r"\bheparin\b|\bcollagen\b|\bxanthan\b|\bpolypeptide\b|\bpeptide\s+fragment\b|emulsion\s+copolymer|"
    r"poly\s*\(ethylene\s+glycol\)|polyethylene\s+glycol|\bpeg[- ]\w*|\bpolysaccharide\b|\bhumic\b|\bbitumen\b)"
)

# Explicit petrochemical/synthetic monomers -> Bucket C unless caught earlier as natural derivative
_PETROCHEMICAL_BLACKLIST_RE = re.compile(
    r"(?i)(?:^acrylic\s+acid$|^methacrylic\s+acid$|^methacrylic\s+anhydride$|(?<![a-z-])2-hydroxyethyl\s+methacrylate|"
    r"(?<![a-z-])glycidyl\s+methacrylate|^styrene$|^methyl\s+methacrylate$|^ethylene\s+glycol$|"
    r"maleimide|diglycidyl\s+ether|oxazoline|epichlorohydrin|vinylcyclohexene\s+oxide|"
    r"(?<![a-z-])glycidyl\s+ether\b|\bbisphenol\b|propionamidophenylboronic|methylthiazole$)"
)

# Explicit clinical synthetic / semi-synthetic drugs & synthetic pesticides -> Bucket C
_SYNTHETIC_CLINICAL_DRUG_RE = re.compile(
    r"(?i)(?:\bdoxorubicin\b|\bgemcitabine\b|\bcloxacillin\b|\bampicillin\b|\bacetaminophen\b|"
    r"2,4-dichlorophenoxyacetic\s+acid|\bpaclitaxel\b|\bcamptothecin\b|\bcisplatin\b|\bfloxuridine\b|"
    r"\bmethotrexate\b|\bibuprofen\b|\bnaproxen\b|\baminosalicylic\b|\bp-aminosalicylic\b|"
    r"\bganciclovir\b|\bnaloxone\b|\bdeferoxamine\b|\bdexamethasone\b|\bsimvastatin\b|"
    r"1-methyl-l-tryptophan|\bcurcumin-drug\b|\bantibiotic\b)"
)

# Synthetic derivatization suffixes that disqualify a molecule from being raw Bucket A even if HMDB/LOTUS matched a parentless fragment
_DERIVATIVE_OR_SYNTHETIC_CHEM_RE = re.compile(
    r"(?i)(?:\bmethacrylate\b|\bacrylate\b|\bacrylamide\b|vinylbenzene|vinylphenoxy|itaconate|"
    r"benzyl-l-glutamate|carvomenthide|menthide|furanoate|oxazoline|maleimide|azido|bromide|dibutyl)"
)

# Explicit curated derivative -> known natural parent suggestions for Bucket B
_NATURAL_DERIVATIVE_RULES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\beugenyl|\begma\b|eggma|4-allyl-2-methoxyphen"), "eugenol"),
    (re.compile(r"(?i)\bitaconate\b|hexylitaconic"), "itaconic acid"),
    (re.compile(r"(?i)benzyl-l-glutamate"), "L-glutamic acid"),
    (re.compile(r"(?i)\bcarvomenthide\b"), "carvone"),
    (re.compile(r"(?i)\bmenthide\b"), "menthol"),
    (re.compile(r"(?i)\bmethyl\s+oleate\b|\boleate\b"), "oleic acid"),
    (re.compile(r"(?i)glucopyranoside"), "D-glucose"),
    (re.compile(r"(?i)vinylphenoxy|methoxy-4-vinylbenzene|guaiacol"), "guaiacol"),
    (re.compile(r"(?i)hydroxymethyl\s*furano|furanoate|mhmf"), "5-(hydroxymethyl)furfural"),
    (re.compile(r"(?i)lipoic\s+acid.*salt|lipoate\s+salt"), "(R)-lipoic acid"),
    (re.compile(r"(?i)acrylamido|methacrylate|acrylate"), ""),  # synthetic graft unless natural parent matched above
)


def _normalize_lookup_variants(name: str) -> List[str]:
    """Generate strict deterministic lowercase string variants without fuzzy substring guessing."""
    raw = (name or "").strip()
    if not raw:
        return []
    # Normalize curly quotes / prime / unicode dashes
    norm = (
        raw.replace("′", "'")
        .replace("−", "-")
        .replace("–", "-")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
    )
    cands = [norm.lower()]
    # Strip trailing parenthetical abbreviation e.g. "Carvone (CV)"
    no_paren = re.sub(r"\s*\([^)]{1,24}\)\s*$", "", norm).strip().lower()
    if no_paren and no_paren not in cands:
        cands.append(no_paren)
    # Strip leading stereo marker like "(+)-" or "(E)-" for exact chemical name lookup
    no_stereo = re.sub(r"^(?:\([+\-REZS]+\)-?)+", "", no_paren or norm, flags=re.I).strip().lower()
    if no_stereo and no_stereo not in cands:
        cands.append(no_stereo)
    return [c for c in cands if c]


def build_nsml_exact_name_index(
    screening_core_parquet: Path,
    candidate_names: Sequence[str],
    nsml_sqlite_db: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build exact lowercase lookup index from NSML v0.2 preferred_name & SQLite raw_name."""
    needed_keys = set()
    for nm in candidate_names:
        needed_keys.update(_normalize_lookup_variants(nm))

    cols = [
        "parent_inchikey",
        "parent_isomeric_smiles",
        "preferred_name",
        "molecular_weight",
        "is_tier1_organic_small",
        "in_lotus",
        "in_hmdb_endogenous_detected",
        "in_npass",
        "in_npatlas",
        "in_cmnpd",
        "in_mimedb",
        "in_npbs_atlas",
        "source_count",
    ]
    sc = pd.read_parquet(screening_core_parquet, columns=cols)
    sc["_pname_low"] = sc["preferred_name"].fillna("").astype(str).str.strip().str.lower()

    # Exact hits in preferred_name
    matched_sc = sc[sc["_pname_low"].isin(needed_keys)].copy()
    ik_map: Dict[str, Dict[str, Any]] = {}

    def _to_record(r: Any) -> Dict[str, Any]:
        bio_sources = []
        for flag, label in (
            ("in_lotus", "LOTUS"),
            ("in_hmdb_endogenous_detected", "HMDB_endogenous_detected"),
            ("in_npass", "NPASS"),
            ("in_npatlas", "NPAtlas"),
            ("in_cmnpd", "CMNPD"),
            ("in_mimedb", "MiMeDB"),
            ("in_npbs_atlas", "NPBS_Atlas"),
        ):
            if bool(r[flag]):
                bio_sources.append(label)
        return {
            "parent_inchikey": str(r["parent_inchikey"]),
            "parent_isomeric_smiles": str(r["parent_isomeric_smiles"]),
            "preferred_name": str(r["preferred_name"] or ""),
            "molecular_weight": float(r["molecular_weight"]),
            "is_tier1_organic_small": bool(r["is_tier1_organic_small"]),
            "bio_sources": bio_sources,
            "source_count": int(r["source_count"]),
        }

    def _better(cand: Dict[str, Any], cur: Optional[Dict[str, Any]]) -> bool:
        if cur is None:
            return True
        return (
            cand["is_tier1_organic_small"],
            len(cand["bio_sources"]),
            cand["source_count"],
        ) > (
            cur["is_tier1_organic_small"],
            len(cur["bio_sources"]),
            cur["source_count"],
        )

    for _, r in matched_sc.iterrows():
        key = str(r["_pname_low"])
        rec = _to_record(r)
        if _better(rec, ik_map.get(key)):
            ik_map[key] = rec

    # Secondary exact lowercase synonym fallback from SQLite source_records.raw_name
    missing_keys = needed_keys - set(ik_map.keys())
    if missing_keys and nsml_sqlite_db is not None and Path(nsml_sqlite_db).exists():
        # Index all screening_core by parent_inchikey for joining matched raw_names
        sc_by_ik: Dict[str, Dict[str, Any]] = {}
        uri = f"file:{nsml_sqlite_db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            syn_hits: Dict[str, List[str]] = {}
            for raw_nm, pik in cur.execute("SELECT raw_name, parent_inchikey FROM source_records WHERE raw_name IS NOT NULL AND parent_inchikey IS NOT NULL"):
                low = str(raw_nm).strip().lower()
                if low in missing_keys:
                    syn_hits.setdefault(low, []).append(str(pik))
            if syn_hits:
                all_piks = {ik for piks in syn_hits.values() for ik in piks}
                sub = sc[sc["parent_inchikey"].isin(all_piks)]
                for _, r in sub.iterrows():
                    sc_by_ik[str(r["parent_inchikey"])] = _to_record(r)
                for key, piks in syn_hits.items():
                    for pik in piks:
                        rec = sc_by_ik.get(pik)
                        if rec is not None and _better(rec, ik_map.get(key)):
                            ik_map[key] = rec
        finally:
            conn.close()

    return ik_map


def classify_unpinned_natural_candidates(
    unpinned_rows: Sequence[Dict[str, Any]],
    screening_core_parquet: Path,
    nsml_sqlite_db: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Classify the 271 MONOMER_PARENT_NOT_PINNED_NATURAL rows into strict Bucket A/B/C."""
    all_names = [
        str(r.get("canonical_parent") or r.get("author_monomer_literal") or "").strip()
        for r in unpinned_rows
    ]
    # Include curated suggested parent names for Bucket B validation
    curated_parents = [p for _, p in _NATURAL_DERIVATIVE_RULES if p]
    name_index = build_nsml_exact_name_index(
        screening_core_parquet, all_names + curated_parents, nsml_sqlite_db=nsml_sqlite_db
    )

    classified_rows: List[Dict[str, Any]] = []
    bucket_counts = {BUCKET_A: 0, BUCKET_B: 0, BUCKET_C: 0}
    bucket_mn_counts = {BUCKET_A: 0, BUCKET_B: 0, BUCKET_C: 0}

    for r in unpinned_rows:
        name = str(r.get("canonical_parent") or r.get("author_monomer_literal") or "").strip()
        has_mn = r.get("de_novo_chain_mn_kda") is not None
        variants = _normalize_lookup_variants(name)

        nsml_hit = None
        matched_key = ""
        for v in variants:
            if v in name_index:
                nsml_hit = name_index[v]
                matched_key = v
                break

        bucket = BUCKET_C
        reason = "NO_NSML_NATURAL_MATCH"
        suggested_parent = None

        if _MACRO_OIL_MIXTURE_RE.search(name):
            bucket = BUCKET_C
            reason = "BLACKLIST_MACROMOLECULE_POLYSACCHARIDE_OR_OIL_MIXTURE"
        elif _SYNTHETIC_CLINICAL_DRUG_RE.search(name):
            bucket = BUCKET_C
            reason = "BLACKLIST_SYNTHETIC_OR_CLINICAL_DRUG"
        elif _PETROCHEMICAL_BLACKLIST_RE.search(name):
            bucket = BUCKET_C
            reason = "BLACKLIST_PETROCHEMICAL_OR_SYNTHETIC_MONOMER"
        else:
            # Check explicit Bucket B natural derivative rules before trusting accidental synthetic/derivative hits as Bucket A
            deriv_parent = None
            for pat, parent_nm in _NATURAL_DERIVATIVE_RULES:
                if pat.search(name) and parent_nm:
                    deriv_parent = parent_nm
                    break

            if deriv_parent:
                p_hit = name_index.get(deriv_parent.lower())
                bucket = BUCKET_B
                reason = "NATURAL_DERIVATIVE_MONOMER_NEEDS_PARENT_MAPPING"
                suggested_parent = {
                    "suggested_parent_name": deriv_parent,
                    "nsml_parent_inchikey": p_hit["parent_inchikey"] if p_hit else None,
                    "nsml_preferred_name": p_hit["preferred_name"] if p_hit else None,
                }
            elif nsml_hit is not None:
                if _DERIVATIVE_OR_SYNTHETIC_CHEM_RE.search(name):
                    bucket = BUCKET_B if "itacon" in name.lower() or "glutamate" in name.lower() else BUCKET_C
                    reason = "DERIVATIVE_SUFFIX_BLOCKED_FROM_BUCKET_A"
                elif (
                    nsml_hit["is_tier1_organic_small"]
                    and nsml_hit["molecular_weight"] >= 70.0
                    and len(nsml_hit["bio_sources"]) >= 1
                ):
                    bucket = BUCKET_A
                    reason = "EXACT_NSML_V02_TIER1_MW_GE70_BIO_CONFIRMED"
                else:
                    bucket = BUCKET_C
                    reason = (
                        f"FAILED_BUCKET_A_GATES(tier1={nsml_hit['is_tier1_organic_small']},"
                        f"mw={nsml_hit['molecular_weight']:.1f},bio={len(nsml_hit['bio_sources'])})"
                    )

        bucket_counts[bucket] += 1
        if has_mn:
            bucket_mn_counts[bucket] += 1

        classified_rows.append(
            {
                "event_id": r["event_id"],
                "paper_id": r["paper_id"],
                "canonical_parent": name,
                "author_monomer_literal": r.get("author_monomer_literal"),
                "monomer_role": r.get("monomer_role"),
                "reaction_type": r.get("reaction_type"),
                "de_novo_chain_mn_kda": r.get("de_novo_chain_mn_kda"),
                "monomer_conversion_pct": r.get("monomer_conversion_pct"),
                "bucket": bucket,
                "decision_reason": reason,
                "matched_variant": matched_key or None,
                "nsml_match": nsml_hit,
                "suggested_parent": suggested_parent,
                "proposed_outcome_if_approved": "RETAIN_CURRENT_OUTCOME_UNMODIFIED",
            }
        )

    summary = {
        "total_unpinned_only": len(unpinned_rows),
        "bucket_counts": bucket_counts,
        "bucket_with_de_novo_mn_counts": bucket_mn_counts,
    }
    return classified_rows, summary


# ---------------------------------------------------------------------------
# Task 2.2: Missing GPC/MW or NMR/FTIR Proof Local XML Rescue Scan
# ---------------------------------------------------------------------------

_RE_GPC_PROOF = re.compile(
    r"(?i)(?:\b(?:gpc|sec)\b.{0,120}\b(?:mn|mw|m\s*n|m\s*w|đ|pdi|dispersity|mol(?:ecular)?\s+weight)\b|"
    r"\b(?:m\s*n|m\s*w|number[- ]average\s+molecular\s+weight)\b\s*(?:=|of|was|were|:)\s*\d+(?:\.\d+)?\s*(?:kda|g/mol|da))"
)
_RE_SPECTRO_PROOF = re.compile(
    r"(?i)(?:(?:\b1h\s*nmr\b|\b13c\s*nmr\b|¹h\s*nmr|\bnmr\b).{0,100}\b(?:δ|ppm|peak|signal|resonance|triplet|singlet|multiplet)\b|"
    r"(?:\bft-?ir\b|\batr-?ftir\b|\bir\b).{0,100}(?:cm[-−]1|cm-1|cm\s*−\s*1|peak|band|stretch|absorption|c=o|oh|nh))"
)


def scan_missing_proof_xml_evidence(
    hold_rows: Sequence[Dict[str, Any]],
    xml_index: Dict[str, Path],
    parsed_xml_cache: Dict[str, List[XmlTextBlock]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan local XMLs of holds lacking MW/incorporation or spectroscopic proofs."""
    target_holds = [
        r
        for r in hold_rows
        if "HOLD_MISSING_MW_OR_INCORPORATION_PROOF" in str(r.get("hold_reasons") or "")
        or "HOLD_MISSING_COVALENT_SPECTROSCOPIC_PROOF" in str(r.get("hold_reasons") or "")
    ]

    results: List[Dict[str, Any]] = []
    papers_with_gpc = 0
    papers_with_spectro = 0
    events_with_any_proof = 0

    for r in target_holds:
        pid = r["paper_id"]
        blocks = parsed_xml_cache.get(pid)
        if blocks is None and pid in xml_index:
            blocks = parse_xml_contexts(xml_index[pid], paper_id=pid)
            parsed_xml_cache[pid] = blocks
        blocks = blocks or []

        mon_toks, _, _ = _event_keywords(r)
        need_gpc = "HOLD_MISSING_MW_OR_INCORPORATION_PROOF" in str(r.get("hold_reasons") or "")
        need_spec = "HOLD_MISSING_COVALENT_SPECTROSCOPIC_PROOF" in str(r.get("hold_reasons") or "")

        gpc_quotes: List[Dict[str, Any]] = []
        spectro_quotes: List[Dict[str, Any]] = []

        scored = sorted(
            ((int(any(t in b.text.lower() for t in mon_toks)), idx, b) for idx, b in enumerate(blocks)),
            key=lambda x: (-x[0], x[1]),
        )
        for mon_hit, _, b in scored:
            if need_gpc and len(gpc_quotes) < 2 and _RE_GPC_PROOF.search(b.text):
                gpc_quotes.append(
                    {
                        "monomer_mentioned": bool(mon_hit),
                        "section": b.section_title,
                        "verbatim_quote": _truncate_quote(b.text),
                    }
                )
            if need_spec and len(spectro_quotes) < 2 and _RE_SPECTRO_PROOF.search(b.text):
                spectro_quotes.append(
                    {
                        "monomer_mentioned": bool(mon_hit),
                        "section": b.section_title,
                        "verbatim_quote": _truncate_quote(b.text),
                    }
                )
            if (not need_gpc or len(gpc_quotes) >= 2) and (not need_spec or len(spectro_quotes) >= 2):
                break

        if gpc_quotes:
            papers_with_gpc += 1
        if spectro_quotes:
            papers_with_spectro += 1
        if (need_gpc and gpc_quotes) or (need_spec and spectro_quotes):
            events_with_any_proof += 1

        results.append(
            {
                "event_id": r["event_id"],
                "paper_id": pid,
                "canonical_parent": r.get("canonical_parent") or r.get("author_monomer_literal"),
                "hold_reasons": r.get("hold_reasons"),
                "has_xml": pid in xml_index,
                "gpc_mw_xml_hits": gpc_quotes,
                "spectroscopic_nmr_ftir_xml_hits": spectro_quotes,
            }
        )

    summary = {
        "total_missing_proof_hold_events": len(target_holds),
        "unique_papers": len({r["paper_id"] for r in target_holds}),
        "events_with_gpc_mw_xml_hit": papers_with_gpc,
        "events_with_spectro_xml_hit": papers_with_spectro,
        "events_with_any_requested_proof_xml_hit": events_with_any_proof,
    }
    return results, summary

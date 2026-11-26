"""BIO-R031 R06: source-first scientific negative / relative evidence.

All rules are *general author-source local predicates*. No PMCID mapping or
SCI gold labels inside this module. Contrasts are never manufactured from
percentages, figures, Seahorse Mito Stress OCR or genetic comparisons.

Scope: original JATS paragraph or original figure-caption (source SHA and
anchor are required). Absent paired absolute Y is a bounded source HOLD, not
a claim that an image could never be independently digitised.
"""
from __future__ import annotations
import hashlib,re,xml.etree.ElementTree as ET
from pathlib import Path

VERSION="ATP_R06_SOURCE_CLAIM_SCIENCE_V1"
REL="AUTHOR_RELATIVE_ATP_EFFECT_ONLY"
GENE="GENETIC_MUTATION_VS_WILDTYPE_NOT_SMALL_MOLECULE_INTERVENTION"
MITO="SEAHORSE_MITO_STRESS_DERIVED_OCR_NOT_ATP_PER_TIME_SOURCE"
METHOD="METHODS_CRITIQUE_NO_AUTHOR_TREATMENT_ATP_PAIR"
CLASS_TYPES=(REL,GENE,MITO,METHOD)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def stable(*bits):
    return hashlib.sha256("|".join(map(str,bits)).encode()).hexdigest()[:24]
def norm(s):
    return " ".join((s or "").split())

def source_elements(path,source_sha):
    path=Path(path)
    if sha(path)!=source_sha:raise ValueError("R06_ORIGINAL_SOURCE_SHA_DRIFT")
    if path.suffix.lower() not in (".xml",".nxml"):
        return []
    root=ET.parse(path).getroot()
    parent={child:el for el in root.iter() for child in el}
    records=[];paragraph_counter=0
    for element in root.iter():
        tag=element.tag.split("}")[-1]
        if tag not in ("p","caption"):continue
        if tag=="p":
            # JATS <caption><p> and <fig><caption><p> often repeat
            # the exact figure caption; emit once under fig/caption.
            cursor=parent.get(element)
            skip=False
            while cursor is not None:
                if cursor.tag.split("}")[-1] in ("caption","table-wrap"):
                    skip=True;break
                cursor=parent.get(cursor)
            if skip:continue
            paragraph_counter+=1
        else:
            cursor=parent.get(element)
            while cursor is not None and cursor.tag.split("}")[-1] not in ("fig","table-wrap","table-wrap-foot"):
                cursor=parent.get(cursor)
        literal=norm("".join(element.itertext()))
        if not literal:continue
        if tag=="p":
            anchor="JATS:p#"+element.attrib["id"] if element.attrib.get("id") else "JATS:p:literal-"+stable(literal)
            scope="BODY_PARAGRAPH"
        else:
            ctag=cursor.tag.split("}")[-1] if cursor is not None else "caption"
            cid=cursor.attrib.get("id") if cursor is not None else None
            anchor="JATS:"+ctag+"#"+str(cid) if cid else "JATS:caption:literal-"+stable(literal)
            scope="FIGURE_CAPTION" if ctag=="fig" else "TABLE_OR_OTHER_CAPTION"
        records.append({"source_anchor":anchor,"source_scope":scope,"verbatim":literal,
            "source_sha256":source_sha,"text_sha256":hashlib.sha256(literal.encode()).hexdigest(),
            "parent_node_type":tag,"original_bytes_are_unmodified":True})
    # When an original non-identified paragraph occurs twice, treat the anchor
    # as shared text evidence, not as two distinct experimental replicates.
    unique={}
    for item in records:unique.setdefault(item["source_anchor"],item)
    return list(unique.values())

def classify_local_claim(element):
    """At most four science signals per *single* source paragraph/caption.

    A signal is not itself a whole-paper rejection. Each reason carries its
    own evidence and candidates must be grouped under an authorized asset.
    """
    s=element["verbatim"]
    low=s.lower()
    claims=[]
    def emit(kind,endpoint,why,measurement,comparison,metric,flags=None,original_relative=None):
        claims.append({
            "scientific_reason":kind,"endpoint_task":endpoint,
            "source_anchor":element["source_anchor"],"source_sha256":element["source_sha256"],
            "source_scope":element["source_scope"],
            "source_verbatim":s[:3200],"source_text_sha256":element["text_sha256"],
            "observation_kind":measurement,"author_comparison_type":comparison,
            "author_metric_kind":metric,"qualifying_author_matched_absolute_pair":False,
            "author_absolute_treated_mean":None,"author_absolute_control_mean":None,
            "author_relative_effect":original_relative,"figure_only_numeric":bool(element["source_scope"]=="FIGURE_CAPTION"),
            "hold_reason":why,"scientific_gold":False,"status":"SOURCE_HOLD",
            "source_claim_id":"r06:claim:"+stable(element["source_sha256"],element["source_anchor"],kind),
            "flags":flags or []})
    # Author directly describes relative percentage vs an explicit comparator
    # and identifies ATP. No unseen absolute-treated/control values inferred.
    m=re.search(r"(?:reduc(?:ed|es|tion)|decreas(?:ed|es)|lower(?:ed)?|drop|increase(?:d|s)?)\s+"
                r"(?:intracellular\s+)?ATP\s+(?:levels?|contents?|amounts?)\s+(?:by\s+|of\s+)"
                r"(\d+(?:\.\d+)?)\s*%",s,re.I)
    if m is None:
        m=re.search(r"\bATP\s+(?:levels?|contents?|amounts?)\s+(?:were|was|have\s+been|are)\s+"
                    r"(?:reduced|decreased|lowered|increased)\s+by\s+(\d+(?:\.\d+)?)\s*%",s,re.I)
    rel_base=bool(re.search(r"(?:compared\s+to|relative\s+to)\s+(?:the\s+)?(?:value\s+for\s+)?control\s+cells?|control\s+cells?\s*\(100%\)",s,re.I))
    if m and rel_base and not re.search(r"\b(?:mutant|mutation|wild.type|G561R|genotype)\b",s,re.I):
        word=m.group(0).lower()
        direction="DECREASE" if any(z in word for z in ("reduc","decreas","lower","drop")) else "INCREASE"
        emit(REL,"ATP_CONTENT","AUTHOR_RELATIVE_PERCENTAGE_ONLY_NO_MATCHED_ABSOLUTE_TREATED_CONTROL",
             "RELATIVE_PERCENT_AUTHOR_TEXT","TREATMENT_VS_CONTROL","ATP_LEVEL_RELATIVE",
             ["SOURCE_PERCENT_NOT_ABSOLUTE_ATP_Y"],
             {"percentage":float(m.group(1)),"direction":direction,"original_literal":m.group(0)})
    # Figure-only source with explicit normalized-to-control percentage; never
    # manufacture numeric treatment absolute values or two arm means.
    if element["source_scope"]=="FIGURE_CAPTION" and re.search(r"\bATP\s+(?:levels?|contents?)\b",s,re.I) and re.search(r"relative\s+to\s+(?:the\s+)?value\s+for\s+control\s+cells?\s*\(100%\)",s,re.I):
        emit(REL,"ATP_CONTENT","FIGURE_CONTROL_NORMALIZED_NOT_ORIGINAL_ABSOLUTE_PAIR",
             "FIGURE_ONLY_RELATIVE_NORMALIZED_TO_CONTROL","TREATMENT_VS_CONTROL","ATP_LEVEL_RELATIVE",
             ["FIGURE_VALUES_REQUIRE_DIRECT_SOURCE_REVIEW_NOT_INFERRED"])
    # Genetic condition vs wild type, in a local author ATP evidence unit.
    gene=bool(re.search(r"\b(?:G\d+[A-Z]|mutat(?:ion|ed|ant)|genotyp(?:e|ic)|knockout|deficien(?:t|cy))\b",s,re.I))
    wild=bool(re.search(r"\bwild[- ]?type\b|\bWT\b",s,re.I))
    atp=bool(re.search(r"\bATP\b",s,re.I))
    if gene and wild and atp and bool(re.search(r"\b(?:fibroblast|cells?|intracellular|levels?|ADP)\b",s,re.I)):
        emit(GENE,"ATP_CONTENT","GENETIC_CONTRAST_NOT_AUTHOR_SMALL_MOLECULE_TREATMENT_ARM",
             "GENOTYPE_RELATIVE_OR_FIGURE_SOURCE","GENOTYPE_VS_WILDTYPE","ATP_GENETIC_CONDITION",
             ["DRUG_ADDITION_ELSEWHERE_CANNOT_REWRITE_GENETIC_COMPARISON"])
    # Explicit Mito Stress method + local OCR/ATP-linked respiration = OCR
    # derived, not verified Seahorse XF ATP Rate pmol/min.
    mito=bool(re.search(r"(?:cell\s+)?mito\s+stress\s+test|mitochondrial\s+respiratory\s+capacity",s,re.I))
    ocr=bool(re.search(r"\bOCR\b|oxygen\s+consumption\s+rate",s,re.I))
    atplink=bool(re.search(r"ATP[- ]linked\s+(?:production|respirat(?:ion|ory))|ATP\s+production",s,re.I))
    if mito and ocr and atplink:
        emit(MITO,"ATP_LINKED_OCR_AUX","SEAHORSE_MITO_STRESS_OCR_CANNOT_BE_CONVERTED_TO_XF_ATP_RATE_OR_CONTENT",
             "OCR_DERIVED_MITO_STRESS_MODEL","PHARMACOLOGICAL_PERTURBATION_OCR",
             "ATP_LINKED_OXYGEN_CONSUMPTION",
             ["RATE_REAL_AUTHOR_PAIR_HOLD","NO_AMPHETAMINE_OR_OTHER_UNIT_CONVERSION"])
    # Methodology audit: same source paragraph must state that studies or
    # profiles were analysed *as a scientific method comparison*, with
    # original OCR/Seahorse Mito Stress, not merely using a routine assay.
    audit=bool(re.search(r"(?:we\s+examined|examined|analysis\s+of|analyse[ds]?|evaluate\s+its\s+reliability)",s,re.I)
      and re.search(r"\b(?:publications|studies|profiles|test\s+profiles|assays)\b",s,re.I))
    meta=bool(re.search(r"\b(?:mito\s+stress\s+test|cell\s+stimulation\s+test)\b",s,re.I))
    methods=bool(re.search(r"\bOCR\b|oxygen\s+consumption\s+rate|maximal\s+respirat",s,re.I))
    if audit and meta and methods and re.search(r"\b(?:\d{2,4}\s+(?:recent\s+)?publications|\d{2,4}\s+cell\s+mito\s+stress\s+tests|\d{2,4}\s+cell\s+mito\s+stress\s+test\s+profiles|reliability|limitations?\s+of\s+this\s+approach)\b",s,re.I):
        emit(METHOD,"ATP_LINKED_OCR_AUX","METHOD_VALIDATION_REVIEW_OF_OCR_NOT_AUTHOR_TREATMENT_ATP_PAIRED_Y",
             "METHODS_CRITIQUE_OR_SURVEY","MULTI_PAPER_METHOD_VALIDATION","OCR_METHOD",
             ["METHODS_DISCUSSION_NOT_AUTHOR_SMALL_MOLECULE_ATP_PAIR"])
    return claims

def evidence_for_source(path,source_sha,paper_key,asset_key):
    elements=source_elements(path,source_sha)
    claims=[]
    for element in elements:
        for item in classify_local_claim(element):
            item.update({"paper_key":paper_key,"asset_key":asset_key,
                "paper_id":"r06:paper:"+stable(paper_key),
                "asset_id":"r06:asset:"+stable(paper_key,asset_key),
                "assay_instance_id":None,"evidence_link_status":"UNLINKED_UNTIL_EXACT_AUTHOR_ASSAY_ANCHOR"})
            claims.append(item)
    # Source-level selection preserves *all* separately sourced local claims;
    # cannot infer paper-wide invalidity if genuine absolute pair also exists.
    return elements,claims

def science_hold_for_source(paper_key,asset_key,sha256,claims,absolute_pairs):
    out=[]
    if not absolute_pairs:
        for item in claims:
            hold={"source_sha256":sha256,"paper_key":paper_key,"asset_key":asset_key,
              "evidence_claim_id":item["source_claim_id"],
              "original_source_anchor":item["source_anchor"],
              "source_excerpt":item["source_verbatim"][:650],
              "scientific_reason":item["scientific_reason"],
              "endpoint_task":item["endpoint_task"],
              "observation_kind":item["observation_kind"],
              "author_matched_absolute_pair_available":False,
              "author_absolute_treated_mean":None,"author_absolute_control_mean":None,
              "source_claim_scope":"LOCAL_PARAGRAPH_OR_FIGURE_CAPTION",
              "scientific_qualification":"NOT_A_GOLD_PAIRED_ATP_CONTENT_OR_RATE_Y",
              "status":"SOURCE_HOLD","gold_approved":False}
            out.append(hold)
        if not out:
            out.append({"source_sha256":sha256,"paper_key":paper_key,"asset_key":asset_key,
              "evidence_claim_id":None,"original_source_anchor":None,
              "source_excerpt":None,
              "scientific_reason":"BOUNDED_AUTHOR_JATS_NO_SUPPORTED_SOURCE_CLAIM_FOUND",
              "endpoint_task":"UNKNOWN_HOLD","observation_kind":"NOT_CLASSIFIED",
              "author_matched_absolute_pair_available":False,
              "author_absolute_treated_mean":None,"author_absolute_control_mean":None,
              "source_claim_scope":"NOT_VERIFIED",
              "scientific_qualification":"DO_NOT_INFER_NEGATIVE_FROM_MISSING_PAIRS",
              "status":"SOURCE_HOLD","gold_approved":False})
    return out

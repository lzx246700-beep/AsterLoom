"""R15 generic author-native table scope; no paper identifiers or inferred numeric values.

A physical superheader is allowed to qualify a child ATP metric only when its
JATS colspan covers that metric column. Table footnotes have table-local scope.
Every recognized source literal keeps its original author anchor.
"""
from __future__ import annotations
import re
def literal(x):
    return " ".join(str(x or "").split())

# A multiplicative denominator such as µmol × (g liver weight)−1 is
# dimensional division. Normalize only the unit notation, not the number.
_AMOUNT=r"(?:µmol|μmol|umol|nmol|pmol|mmol|fmol|mol)"
_INV=re.compile(
    r"^\s*(?P<amount>"+_AMOUNT+r")\s*[×x*]\s*"
    r"\(\s*(?P<denominator>g\s+(?:liver|tissue|body|organ)\s+weight|"
    r"mg\s+protein|g\s+protein|10\s*\^?\s*\{?\d+\}?\s+cells)\s*\)"
    r"\s*[−–-]\s*1\s*$",re.I)
_DIV=re.compile(r"^\s*(?P<amount>"+_AMOUNT+r")\s*(?:/|per)\s*(?P<denominator>[^;]+)$",re.I)
_MEANSEM=re.compile(r"\b(?:SEM|S\.E\.M\.|SE|standard\s+errors?)(?:\s+of\s+(?:the\s+)?mean)?\b",re.I)
_MEANSD=re.compile(r"\b(?:SD|S\.D\.|standard\s+deviations?)\b",re.I)
_FASTED=re.compile(r"\bfasted\s+(?:rats?|mice|animals?|livers?)\b",re.I)
_FED=re.compile(r"\bfed\s+(?:rats?|mice|animals?|livers?)\b",re.I)

def authored_unit(metric,table):
    """Return a scoped physical JATS header, or None; never scan sibling metrics."""
    if not metric.get("is_header"):return None
    matches=[]
    for h in table["cells"]:
        if not h.get("is_header") or h["row"]>=metric["row"]:continue
        if not (h["col"]<=metric["col"]<h["col"]+h.get("colspan",1)):continue
        raw=literal(h["raw"])
        semantic=literal(h.get("semantic_literal") or raw)
        unit=(_INV.fullmatch(raw) or _DIV.fullmatch(raw)
              or _INV.fullmatch(semantic.replace("^{−1}","−1").replace("^{-1}","-1"))
              or _DIV.fullmatch(semantic))
        if unit:
            amount=unit.group("amount")
            den=" ".join(unit.group("denominator").split())
            matches.append((h["row"],h["source_anchor"],raw,amount,den))
    if not matches:return None
    nearest=max(x[0] for x in matches)
    scoped=[x for x in matches if x[0]==nearest]
    if len(scoped)!=1:return None
    _,anchor,raw,amount,den=scoped[0]
    return {"semantic":f"{amount}/{den}","raw":raw,
        "locator":anchor,"amount":amount,"denominator":den,
        "entity":"liver weight" if "liver weight" in den.lower() else
        "protein" if "protein" in den.lower() else None}

def table_statistic(table):
    """Table-local author convention, with explicit original footnote anchor."""
    raw=literal(table.get("footnote_semantic") or table.get("footnotes") or "")
    if not raw:return None
    sem=_MEANSEM.search(raw)
    sd=_MEANSD.search(raw)
    if bool(sem)==bool(sd):return None
    return {"statistic":"SEM" if sem else "SD",
        "source_locator":table["table_id"]+"/footnote",
        "original_literal":raw}

def table_experiment_scope(table):
    """Never mix fasted and fed liver states or invent exposure times."""
    raw=literal(table.get("caption_semantic") or table.get("caption") or "")
    f=_FASTED.search(raw);e=_FED.search(raw)
    if bool(f)==bool(e):return None
    return {"state":"FASTED" if f else "FED",
        "source_locator":table["table_id"]+"/caption",
        "original_literal":raw}

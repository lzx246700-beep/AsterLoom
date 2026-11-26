"""Incremental JATS result/arm extraction. Scientific approval stays with R41."""
import argparse
import hashlib
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from .r40_atp_relations import relations as parallel_atp_arms
from .r41_model_data import ARM_RULES, DEPLETED_PAIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS r42_extraction_job(
 task TEXT, paper TEXT, sha TEXT, status TEXT NOT NULL, detail TEXT, relation_count INTEGER NOT NULL,
 rule_sha TEXT NOT NULL, seconds REAL, updated TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(task,paper,sha));
CREATE TABLE IF NOT EXISTS r42_relation(
 id TEXT PRIMARY KEY, task TEXT NOT NULL,paper TEXT NOT NULL,doi TEXT,sha TEXT NOT NULL,locator TEXT NOT NULL,
 molecule TEXT NOT NULL,arm TEXT NOT NULL, level TEXT NOT NULL CHECK(level IN ('L1','L2','L3')),
 endpoint TEXT NOT NULL,direction TEXT,value REAL,unit TEXT,comparator TEXT,quote TEXT NOT NULL,
 value_kind TEXT, value_literal TEXT, cell TEXT, stimulus TEXT, dose TEXT, exposure_time TEXT,
 method TEXT, normalization TEXT, statistic TEXT,source_kind TEXT NOT NULL,
 evidence_json TEXT NOT NULL, missing TEXT NOT NULL, science_state TEXT NOT NULL DEFAULT 'CANDIDATE',
 FOREIGN KEY(task,paper,sha) REFERENCES r42_extraction_job(task,paper,sha));
CREATE INDEX IF NOT EXISTS r42_relation_molecule ON r42_relation(task,molecule,paper);
CREATE TABLE IF NOT EXISTS r42_passage(
 task TEXT,paper TEXT,sha TEXT,locator TEXT,quote TEXT, endpoint TEXT, molecule_mentions TEXT,
 reason TEXT,PRIMARY KEY(task,paper,sha,locator,quote),
 FOREIGN KEY(task,paper,sha) REFERENCES r42_extraction_job(task,paper,sha));
CREATE TABLE IF NOT EXISTS r42_discovered(
 molecule TEXT PRIMARY KEY,literal TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'DISCOVERED_LITERAL_IDENTITY_UNRESOLVED');
CREATE VIEW IF NOT EXISTS r42_molecule_coverage AS SELECT task,molecule,level,endpoint,count(distinct paper) papers,
 count(*) relations FROM r42_relation GROUP BY task,molecule,level,endpoint;
DROP VIEW IF EXISTS r42_relation_scope;
CREATE VIEW r42_relation_scope AS SELECT id,task,paper,molecule,endpoint,direction,value,unit,cell,stimulus,
 json_extract(evidence_json,'$.endpoint_scope') AS endpoint_scope,
 json_extract(evidence_json,'$.endpoint_component') AS endpoint_component,
 json_extract(evidence_json,'$.quantity_qualifier') AS quantity_qualifier,
 json_extract(evidence_json,'$.source_context') AS source_context,
 json_extract(evidence_json,'$.source_attribution') AS source_attribution,
 json_extract(evidence_json,'$.author_direct_experiment') AS author_direct_experiment,
 json_extract(evidence_json,'$.effect_semantics') AS effect_semantics
 FROM r42_relation;
"""
AT = re.I
NUMBER = r"\d+(?:\.\d+)?"
DOSE = rf"[≥≤<>~]?\s*{NUMBER}\s*(?:[μµunm]M|(?:[μµunm]?g|[μµunm]?mol)/(?:m[Ll]|[Ll]|kg))"
TIME = rf"{NUMBER}\s*(?:hours?|hrs?|h|days?|min(?:utes?)?)\b"
CELL = r"\b(?:RAW\s*264[. ]7|HepG2|HEK[- ]?293T?|H9c2|PC[- ]?12|SH[- ]?SY5Y|HUVECs?|A549|BV[- ]?2|MCF[- ]?7|HeLa|C2C12|HK[- ]?2|primary (?:macrophages|hepatocytes|neurons|astrocytes)|macrophages|hepatocytes|cardiomyocytes|neurons|astrocytes)\b"
CAVEAT = re.compile(r"\b(?:could|may|might|would|hypothes\w*|speculat\w*|previous(?:ly)?|reportedly|reviewed|other studies|has been (?:shown|reported)|suggesting|in order to|to determine whether)\b",AT)
SENTENCE = re.compile(r"(?<!Fig\.)(?<!Figs\.)(?<!vs\.)(?<=[.!?])\s+(?=[A-Z(])")
# Names shorter than four characters must have their definition in the article itself.
STOP_NAMES = {"atp","adp","amp","lps","nad","nadh","ros","dmso","control","water","oxygen","para","vehicle","protein","cell","cells","test","iron","calcium"}
CHEMICAL = re.compile(r"\b([A-Za-z][A-Za-z0-9-]{3,35}(?:mycin|ericin|cycline|flavone|flavonol|cetin|ferol|flavin|saponin|sterol|statin|formin|etaxel|platin|ubicin|glitazone|cysteine|kinase inhibitor))\b",AT)
VERBS = {
 "NOT_SIGNIFICANT": r"did not significantly (?:change|alter|affect|increase|decrease)|was not significantly different|were not significantly different|had no significant (?:effect on|impact on)",
 "UNCHANGED_REPORTED": r"did not (?:change|alter|affect)|remained unchanged|was unchanged|were unchanged",
 "INCREASE": r"increas(?:ed|es|e)|elevat(?:ed|es|e)|enhanc(?:ed|es|e)|raised|augmented|upregulated|higher",
 "DECREASE": r"decreas(?:ed|es|e)|reduc(?:ed|es|e)|lowered|depleted|diminished|suppressed|inhibited|lower",
}
DIR = "(?P<dir>"+"|".join("(?:"+v+")" for v in VERBS.values())+")"
ADV = r"(?:(?:significantly|markedly|strongly|substantially|strikingly|dose-dependently|considerably|dramatically|also|further|selectively|slightly|greatly|notably)\s+){0,3}"
ARG = rf"(?:\s*\(\s*{DOSE}\s*\)|\s+at\s+{DOSE})?"
M = r"(?P<m>~m\d+~)"
E = r"(?P<e>~e\d+~)"
SUBJECT = rf"{M}{ARG}(?:\s+for\s+{TIME})?(?: alone)?(?: (?:pre-?treatment|administration|exposure|supplementation|treatment))?(?: was (?:found|observed|shown) to| led to| resulted in)?"
DET = r"(?:the |both |cellular |intracellular |mitochondrial |total )*"
ACT = rf"{SUBJECT}\s+{ADV}{DIR}\s+{DET}{E}"
PASS = rf"{E}\s+(?:was |were |is |are )?{ADV}{DIR}\s+(?:following (?:treatment with )?|after (?:treatment with )?|by (?:treatment with )?|upon (?:exposure to )?|in (?:the )?){M}"
IN_GROUP = rf"{E}\s+(?:of|in) (?:the )?{M}[- ]treated (?:group|cells?|animals?|rats|mice)\s+(?:was |were )?{ADV}{DIR}"
EXPOSED = rf"{E} (?:in|of) (?:the )?(?:cells?|macrophages|neurons|rats|mice|animals) (?:pretreated|treated|incubated|supplemented) with {M}{ARG} (?:was |were )?{ADV}{DIR}"
GROUP_FIRST = rf"(?:in|for) (?:the )?{M}[- ](?:treated )?(?:group|cells|rats|mice),? {DET}{E} (?:was |were )?{ADV}{DIR}"
PATTERNS = [re.compile(x,AT) for x in (ACT,PASS,IN_GROUP,EXPOSED,GROUP_FIRST)]
CONTROL = re.compile(r"(?:compared (?:with|to)|relative to|versus|vs\.?)\s+(?:that (?:of|in) |those (?:of|in) )?(?:the |a )?((?:(?:blank|healthy|negative|normal|untreated|vehicle(?:-treated)?|saline(?:-treated)?|DMSO(?:-treated)?|LPS(?:-treated)?|H2O2-only treatment|model|controls?)[- ]?){1,3}(?:group|cells|animals|rats|mice|controls)?)\b",AT)
# A named preparation is not a pure-compound identity. "extracted" (the verb) does not match.
PREPARATION = re.compile(r"\b(?:aqueous|ethanolic|methanolic|crude|plant|herbal|leaf|root|polyherbal)?\s*(?:extracts?|fractions?|decoctions?|mixtures?|formulations?)\b",AT)
PROTECTED_LEDGERS = (
    "qualified_training_labels","r41_observation","r41_assay_arm",
    "r16_parsed_asset","r16_source_cell","r17_measurement",
)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"))


def spaced(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def flowed(node):
    """Keep element boundaries from gluing a heading onto the next molecule name."""
    if node is None:
        return ""
    chunks=[]
    if node.text and node.text.strip():
        chunks.append(node.text.strip())
    for child in node:
        text=flowed(child)
        if text:
            chunks.append(text)
        if child.tail and child.tail.strip():
            chunks.append(child.tail.strip())
    return " ".join(chunks)


def first(pattern,text):
    m=re.search(pattern,text,AT)
    return m[0] if m else None


class Contract:
    def __init__(self, path):
        self.spec=json.loads(path.read_bytes())
        self.id=self.spec["id"]
        self.hash=sha256(path.read_bytes()+Path(__file__).read_bytes())
        self.patterns=[(k,re.compile(r"(?<!\w)(?:"+v+r")(?!\w)",AT)) for k,v in self.spec["endpoints"].items()]
        self.exclude=re.compile(self.spec["exclude"],AT)
        self.methods=re.compile(self.spec["methods"],AT)
        self.unit=re.compile(self.spec["absolute_unit"],AT)

    def endpoints(self,text):
        if self.exclude.search(text):
            return []
        hits=[]
        for code,p in self.patterns:
            for match in p.finditer(text):
                if not any(i<match.end() and j>match.start() for i,j,_ in hits):
                    hits.append((match.start(),match.end(),code))
        return sorted(hits)


def load_contract(name):
    path=Path(name)
    if not path.is_file():
        if not re.fullmatch(r"[a-z0-9_]+",name):
            raise ValueError("Unknown task or contract file")
        path=Path(__file__).parent/"profiles"/f"r42_{name}.json"
    return Contract(path)


class Names:
    def __init__(self, rows):
        self.mapping={}
        for mol,alias in rows:
            key=alias.strip().casefold()
            if len(key)>=4 and key not in STOP_NAMES and len(key)<=100:
                self.mapping[key]=mol
        self.pattern=re.compile(r"(?<![\w-])(?:"+"|".join(map(re.escape,sorted(self.mapping,key=len,reverse=True)))+r")(?![\w]|-(?!treated\b)|\s+(?:hydrochloride|phosphate|sulfate|sodium salt|glycoside)\b)",AT) if self.mapping else re.compile(r"(?!)")

    def document(self,text):
        local=defaultdict(set)
        for match in self.pattern.finditer(text):
            alias=match[0]
            local[alias.casefold()].add(self.mapping[alias.casefold()])
            abbr=re.match(r"\s*\(([A-Za-z][A-Za-z0-9 -]{1,8})\)",text[match.end():match.end()+14])
            if abbr and abbr[1].upper()==abbr[1] and abbr[1].casefold() not in STOP_NAMES:
                local[abbr[1].casefold()].add(self.mapping[alias.casefold()])
        for match in CHEMICAL.finditer(text):
            key=match[0].casefold()
            if not local.get(key):
                local[key].add("DISC:"+key)
        valid={k:next(iter(v)) for k,v in local.items() if len(v)==1}
        if not valid:
            return valid,re.compile(r"(?!)")
        # Permit -treated but never shorten another compound, salt or ester to a parent.
        pattern=r"(?<![\w-])(?:"+"|".join(map(re.escape,sorted(valid,key=len,reverse=True)))+r")(?![\w]|-(?!treated\b)|\s+(?:hydrochloride|phosphate|sulfate|sodium salt|glycoside)\b)"
        return valid,re.compile(pattern,AT)


def parts(root):
    """Emit replayable paths; omit bibliography, subarticles and discussion-only claims."""
    def walk(node,loc,in_result=False,excluded=False):
        counts=Counter()
        for child in node:
            tag=child.tag
            counts[tag]+=1
            path=f"{loc}/{tag}[{counts[tag]}]"
            if tag in {"ref-list","sub-article","back","front","supplementary-material"}:
                continue
            if tag=="sec":
                title=spaced(child.find("title"))+" "+str(child.get("sec-type",""))
                result=in_result or bool(re.search(r"result|findings",title,AT))
                skip=excluded or bool(re.search(r"discussion|conclus|introduct|methods|material",title,AT) and not re.search("result",title,AT))
                yield from walk(child,path,result,skip)
            elif tag=="table-wrap":
                if not excluded:
                    yield "table",path,child
            elif tag=="p":
                if in_result and not excluded:
                    body=(child.text or "")+"".join(("" if k.tag in {"fig","table-wrap","supplementary-material"} else "".join(k.itertext()))+(k.tail or "") for k in child)
                    for sentence in SENTENCE.split(" ".join(body.split())):
                        if sentence:yield "result",path,sentence
                for name in ("fig","table-wrap"):
                    for i,embedded in enumerate(child.findall(name),1):
                        if name=="table-wrap" and not excluded:yield "table",f"{path}/{name}[{i}]",embedded
                        elif not excluded:yield from walk(embedded,f"{path}/{name}[{i}]",True)
            elif tag=="fig":
                if not excluded:yield from walk(child,path,True)
            elif tag in {"body","caption","floats-group"}:
                yield from walk(child,path,in_result,excluded)
    yield from walk(root,"/article")


def direction(word):
    return next(key for key,rx in VERBS.items() if re.fullmatch(rx,word,AT))


def preparation_block(text):
    return bool(PREPARATION.search(text or ""))


def stimulus_of(text):
    found=[]
    if re.search(r"\bLPS\b", text or "", AT):
        found.append("LPS")
    if re.search(r"\blipopolysaccharide\b", text or "", AT):
        found.append("lipopolysaccharide")
    return found[0] if len(found)==1 else None


def row_data(molecule,arm,endpoint,word,quote,**values):
    data=dict(molecule=molecule,arm=arm,endpoint=endpoint,direction=word,quote=quote,level="L1",
              source_kind="AUTHOR_RESULT_TEXT",value=None,unit=None,comparator=None,value_kind=None,value_literal=None,
              cell=None,stimulus=None,dose=None,exposure_time=None,method=None,normalization=None,statistic=None,
              evidence_json={})
    data.update(values)
    binding="DISCOVERED_LITERAL_UNRESOLVED" if str(data["molecule"]).startswith("DISC:") else "SINGLE_REGISTRY_ALIAS"
    evidence=dict(data["evidence_json"])
    evidence.update(identity_binding=binding, purity="NOT_ESTABLISHED",
                    preparation_cue="NO_MIXTURE_EXTRACT_FRACTION_CUE_IN_LOCAL_TEXT",
                    normalization_basis="AUTHOR_UNIT_TEXT_ONLY_NOT_CONVERTED")
    data["evidence_json"]=evidence
    return data


def mark(clause,endpoints,names,pattern):
    spans=[(i,j,f"~e{k}~") for k,(i,j,e) in enumerate(endpoints)]
    arms={}
    for m in pattern.finditer(clause):
        if any(i<m.end() and m.start()<j for i,j,_ in spans):continue
        token=f"~m{len(arms)}~"
        arms[token]=(names[m[0].casefold()],m[0])
        spans.append((m.start(),m.end(),token))
    masked=clause
    for i,j,token in sorted(spans,reverse=True):masked=masked[:i]+token+masked[j:]
    return masked,arms


BY_PERCENT = re.compile(rf"\bby\s+({NUMBER})\s*%", AT)
OF_CONTROL = re.compile(rf"({NUMBER})\s*%\s+of\s+(?:its\s+|the\s+)?(?:stated\s+|matched\s+|vehicle\s+)?control\b", AT)
TO_PERCENT = re.compile(rf"\bto\s+({NUMBER})\s*%", AT)
BY_FOLD = re.compile(rf"\bby\s+({NUMBER})\s*[- ]fold\b", AT)


def unique_span(hits):
    found=[]
    for hit in hits:
        text=hit.strip()
        if text and text not in found:
            found.append(text)
    if len(found)==1:
        return found[0], "BOUND_SAME_CLAUSE"
    if len(found)>1:
        return None, "AMBIGUOUS_IN_CLAUSE"
    return None, "MISSING_NO_SAME_CLAUSE_SPAN"


def local_dose(clause, arm):
    escaped=re.escape(arm)
    hits=[]
    for pattern in (rf"(?<={escaped})\s*\(\s*({DOSE})", rf"(?<={escaped})\s+at\s+({DOSE})"):
        hits.extend(match.group(1) for match in re.finditer(pattern, clause, AT))
    return unique_span(hits)


def shared_treatment_prefix(clause, arm):
    """Dose and duration that introduce this molecule in the same sentence."""
    hits=list(re.finditer(rf"({TIME})\s+treatment with\s+({DOSE})\s+{re.escape(arm)}\b", clause, AT))
    if len(hits)!=1:
        return None, None
    return hits[0].group(2).strip(), hits[0].group(1).strip()


ARM_PERCENT = re.compile(
    rf"\bin\s+((?:[A-Z][\w.-]*\s+){{0,5}}[A-Za-z][\w.-]*)\s*\(\s*([+\-−])\s*({NUMBER})\s*%\s*\)",
    AT)
IN_MODEL = re.compile(
    r"\bin\s+((?:[A-Z][\w.-]*\s+){0,5}[A-Za-z][\w.-]*)(?=\s+by\b|\s+compared\b|\s+versus\b|\s+relative\b|\s+during\b|\s*,|\s*\.|\s*;|$)",
    AT)


def comparator_span(clause):
    patterns=(
        r"compared with\s+(?:the |a )?(untreated (?:[\w.-]+ ){0,6}cells)\b",
        r"(?:versus|compared with)\s+(?:its |the |a )?(matched vehicle control)\b",
        r"(?:versus|vs\.?|compared with)\s+(?:the |a )?(vehicle(?: control)?)\b",
    )
    found=[]
    for pattern in patterns:
        for match in re.finditer(pattern, clause, AT):
            text=match.group(1).strip()
            if text not in found:
                found.append(text)
    if len(found)==1:
        return found[0]
    if len(found)>1:
        longest=max(found, key=len)
        if all(item==longest or item in longest for item in found):
            return longest
        return None
    match=CONTROL.search(clause)
    return match.group(1).strip() if match else None


def local_time(clause):
    hits=[]
    for pattern in (rf"(?<=for )({TIME})", rf"(?<=after )({TIME})"):
        hits.extend(re.findall(pattern, clause, AT))
    return unique_span(hits)


def _overlap(left, right):
    return left.start()<right.end() and right.start()<left.end()


def bind_percent(clause, molecules):
    """Keep printed percents in their own basis. Never convert one into the other."""
    changes=list(BY_PERCENT.finditer(clause))
    controls=list(OF_CONTROL.finditer(clause))
    targets=list(TO_PERCENT.finditer(clause))
    folds=list(BY_FOLD.finditer(clause))
    attached=bool(targets) and all(any(_overlap(target, control) for control in controls) for target in targets)
    if controls and (changes or folds or (targets and not attached)):
        return dict(value=None, unit=None, value_literal=None, value_kind=None, status="AMBIGUOUS_PERCENT_BASIS")
    if controls:
        return dict(value=None, unit=None, value_literal=controls[0].group(0).strip(), value_kind="PERCENT_OF_CONTROL_NOT_CONVERTED", status="PERCENT_OF_CONTROL_NOT_CONVERTED")
    if targets and not changes and not folds:
        return dict(value=None, unit=None, value_literal=targets[0].group(0).strip(), value_kind="PERCENT_TARGET_AMBIGUOUS", status="PERCENT_TARGET_AMBIGUOUS")
    if len(changes)==1 and molecules==1 and not targets and not folds:
        hit=changes[0]
        return dict(value=float(hit.group(1)), unit="%", value_literal=hit.group(0).strip(), value_kind="PERCENT_CHANGE", status="PERCENT_CHANGE_SAME_CLAUSE")
    if len(folds)==1 and molecules==1 and not changes and not targets:
        hit=folds[0]
        return dict(value=float(hit.group(1)), unit="fold", value_literal=hit.group(0).strip(), value_kind="FOLD_CHANGE", status="FOLD_CHANGE_SAME_CLAUSE")
    if changes or targets or folds:
        return dict(value=None, unit=None, value_literal=None, value_kind=None, status="SHARED_PERCENT_UNBOUND")
    return dict(value=None, unit=None, value_literal=None, value_kind=None, status="MISSING_NO_SAME_CLAUSE_SPAN")


def context(clause,arm):
    known=list(dict.fromkeys(re.findall(CELL,clause,AT)))
    dose,dose_status=local_dose(clause, arm)
    duration,duration_status=local_time(clause)
    prefix_dose,prefix_time=shared_treatment_prefix(clause, arm)
    if dose is None and prefix_dose:
        dose,dose_status=prefix_dose,"BOUND_SAME_SENTENCE_SHARED_PREFIX"
    elif dose and prefix_dose and re.sub(r"\s+"," ",dose).casefold()!=re.sub(r"\s+"," ",prefix_dose).casefold():
        dose,dose_status=None,"AMBIGUOUS_DOSE_IN_CLAUSE"
    if duration is None and prefix_time:
        duration,duration_status=prefix_time,"BOUND_SAME_SENTENCE_SHARED_PREFIX"
    elif duration and prefix_time and re.sub(r"\s+"," ",duration).casefold()!=re.sub(r"\s+"," ",prefix_time).casefold():
        duration,duration_status=None,"AMBIGUOUS_DURATION_IN_CLAUSE"
    if dose_status=="AMBIGUOUS_IN_CLAUSE":
        dose_status="AMBIGUOUS_DOSE_IN_CLAUSE"
    if duration_status=="AMBIGUOUS_IN_CLAUSE":
        duration_status="AMBIGUOUS_DURATION_IN_CLAUSE"
    spans=list(dict.fromkeys(match.group(1).strip(" \t.,;") for match in IN_MODEL.finditer(clause)))
    if len(known)==1 and (not spans or all(known[0].casefold() in span.casefold() for span in spans)):
        cell,cell_status=known[0],"BOUND_SAME_CLAUSE"
    elif len(spans)==1 and not known:
        cell,cell_status=spans[0],"BOUND_SAME_CLAUSE"
    elif len(known)>1 or len(spans)>1:
        cell,cell_status=None,"AMBIGUOUS_CELL_IN_CLAUSE"
    else:
        cell,cell_status=None,"MISSING_NO_SAME_CLAUSE_SPAN"
    return dict(cell=cell,
                dose=dose,
                exposure_time=duration,
                statistic=first(r"\bp\s*[<=>≤≥]\s*0?\.\d+",clause),
                stimulus=stimulus_of(clause),
                _field_status={"dose":dose_status,"exposure_time":duration_status,"cell":cell_status})


def source_context(clause):
    hits=[]
    for match in re.finditer(r"\bduring\s+([A-Z][A-Za-z0-9_-]{2,40})\b", clause):
        token=match.group(1)
        if token not in hits:
            hits.append(token)
    if len(hits)==1:
        return hits[0], "SAME_SENTENCE"
    if len(hits)>1:
        return None, "AMBIGUOUS_CONTEXT"
    return None, "MISSING_NO_SAME_CLAUSE_SPAN"


_MODEL_NOUN = r"(?:cultures|cells|macrophages|hepatocytes|neurons|astrocytes|cardiomyocytes)"
_MODEL_NAME = rf"[A-Z][A-Za-z0-9.-]*(?:\s+[A-Z][A-Za-z0-9.-]*){{0,3}}\s+{_MODEL_NOUN}"
_ARM_MODIFIER = r"(?!(?:the|and|its|with|for|from|after|before|under|during|total|both|these|those|this|that|same|all|other|such|their|each)\b)[a-z]{2,}(?:-[a-z]{2,})*"
_MODEL_NP = re.compile(rf"(?:({_ARM_MODIFIER})\s+)?({_MODEL_NAME})\b")
CLAUSE_SPLIT = re.compile(r"\s*;\s*|,?\s+(?:whereas|but|while)\s+|,\s+and\s+(?=(?:treatment|stimulation) with)", AT)


def _clauses(sentence):
    start=0
    for match in CLAUSE_SPLIT.finditer(sentence):
        yield sentence[start:match.start()], start
        start=match.end()
    yield sentence[start:], start


def coordinated_model_arms(text):
    """Bind a lowercase modifier only to the model head it directly precedes.

    Coordinated heads stay separate. A second, unconnected model phrase in the
    same text is left unresolved instead of copied onto every arm.
    """
    chains=[]
    consumed=0
    for lead in re.finditer(r"\bin\s+", text):
        if lead.start()<consumed:
            continue
        pos=lead.end()
        found=[]
        while True:
            head=_MODEL_NP.match(text, pos)
            if not head:
                break
            found.append(dict(modifier=head.group(1), model=head.group(2),
                              modifier_span=(head.start(1), head.end(1)) if head.group(1) else None,
                              model_span=(head.start(2), head.end(2))))
            pos=head.end()
            nxt=re.match(r"\s+and\s+(?:in\s+)?", text[pos:])
            if not nxt:
                break
            pos+=nxt.end()
        if not found:
            continue
        consumed=pos
        chains.append(found)
    if len(chains)!=1:
        return []
    return chains[0]


def endpoint_structure(clause, endpoint):
    glycolytic=bool(re.search(r"\bglycolytic\b", clause, AT))
    mitochondrial=bool(re.search(r"\bmitochondrial\b", clause, AT))
    total=bool(re.search(r"\btotal\b", clause, AT))
    rate=endpoint=="ATP_RATE" or bool(re.search(r"\brates?\b", clause, AT))
    if (glycolytic or mitochondrial) and total:
        return dict(endpoint_scope=None, endpoint_component=None, quantity_qualifier=None, scope_status="AMBIGUOUS_TOTAL_AND_COMPONENT")
    if glycolytic or mitochondrial:
        return dict(endpoint_scope="COMPONENT", endpoint_component="GLYCOLYTIC" if glycolytic else "MITOCHONDRIAL",
                    quantity_qualifier="RATE_REPORTED" if rate else None, scope_status="SAME_CLAUSE")
    if total:
        return dict(endpoint_scope="TOTAL", endpoint_component=None, quantity_qualifier="RATE_REPORTED" if rate else None, scope_status="SAME_CLAUSE")
    if endpoint=="ATP_RATE":
        return dict(endpoint_scope="RATE", endpoint_component=None, quantity_qualifier="RATE_REPORTED", scope_status="SAME_CLAUSE")
    return dict(endpoint_scope=None, endpoint_component=None, quantity_qualifier=None, scope_status="UNSPECIFIED")


def leave_unmodified_arm_unknown(row, clause):
    """A model head with no direct modifier stays unknown. Do not borrow another sentence."""
    arms=coordinated_model_arms(clause)
    if len(arms)!=1 or arms[0]["modifier"] or row["evidence_json"].get("source_context"):
        return
    row["evidence_json"]["source_context"]=None
    row["evidence_json"]["source_context_status"]="UNKNOWN"


def annotate(row, clause):
    evidence=row["evidence_json"]
    evidence.update(endpoint_structure(clause, row["endpoint"]))
    context, status=source_context(clause)
    evidence["source_context"]=context
    evidence["source_context_status"]=status
    evidence["natural_origin"]="UNKNOWN"
    evidence["purity"]="UNKNOWN"
    if re.search(r"\b(cited as claiming|were cited)\b", clause, AT):
        evidence["source_attribution"]="CITED_PRIOR_WORK"
        evidence["author_direct_experiment"]=False
    else:
        evidence["source_attribution"]="SAME_SENTENCE"
        evidence["author_direct_experiment"]=True
    return row


def clause_direction(clause):
    for key, expression in VERBS.items():
        if re.search(expression, clause, AT):
            return key
    return None


def prose(task,sentence,names,pattern):
    if CAVEAT.search(sentence) or task.exclude.search(sentence):return
    if task.id=="small_molecule_atp":
        for old in parallel_atp_arms(sentence):
            key=names.get(old["arm"].casefold())
            if key and old["endpoint"]=="ATP_CONTENT_RELATIVE_AUTHOR_CHANGE" and old["comparator"]:
                yield row_data(key,old["arm"],"ATP_CONTENT",direction(old["direction"]),sentence,level="L2",
                               value=float(old["value"].strip("%")),value_literal=old["value"],unit="%",
                               comparator=old["comparator"],value_kind="CHANGE_BY",evidence_json={"clause":old["clause"]})
        for pattern_rule, state in ARM_RULES:
            hit=re.search(pattern_rule,sentence,AT)
            if hit and hit["mol"].casefold() in names:
                yield row_data(names[hit["mol"].casefold()],hit["mol"],"ATP_CONTENT",
                    "INCREASE" if state=="AUTHOR_INCREASE_DOSE_DEPENDENT" else "UNCHANGED_REPORTED",
                    sentence,evidence_json={"clause":hit[0]})
        pair=DEPLETED_PAIR.search(sentence)
        if pair:
            for n in ("1","2"):
                name=pair["m"+n]
                if name.casefold() in names:
                    yield row_data(names[name.casefold()],name,"ATP_CONTENT","DECREASE",sentence,dose=pair["d"+n],
                                   value=float(pair["v"+n]),unit="%",value_literal=pair["v"+n]+"%",value_kind="CHANGE_BY",
                                   exposure_time=first(TIME,sentence),evidence_json={"clause":pair[0],"incomplete":"COMPARATOR_UNBOUND"})
            return
    # Coordinate conjunctions split where the author begins another explicit arm.
    carry=None
    for clause, clause_at in _clauses(sentence):
        ep=task.endpoints(clause)
        if not ep:continue
        masked,arms=mark(clause,ep,names,pattern)
        arm_effects=list(ARM_PERCENT.finditer(clause))
        molecule_names=list(pattern.finditer(clause))
        distinct_molecules={item[0].casefold() for item in molecule_names}
        distinct_endpoints={code for _start,_end,code in ep}
        if not distinct_molecules and carry and re.match(r"\s*its\b", clause, AT) and len(distinct_endpoints)==1:
            surface, mol = carry["surface"], carry["mol"]
            local=context(clause, surface)
            field_status=local.pop("_field_status")
            if re.search(r"\bin the same experimental arm\b", clause, AT):
                local["cell"]=carry.get("cell")
                field_status["cell"]="SAME_SENTENCE_EXPERIMENTAL_ARM"
            else:
                local["cell"]=None
                field_status["cell"]="MISSING_NO_SAME_CLAUSE_SPAN"
            row=row_data(mol, surface, ep[0][2], clause_direction(clause), sentence, comparator=comparator_span(clause),
                         evidence_json={"clause":clause,"field_source":"SAME_SENTENCE_ANAPHORA","percent_basis":"NOT_CONVERTED"}, **local)
            annotate(row, clause)
            if carry.get("source_context") and re.search(r"\bin the same experimental arm\b", clause, AT):
                row["evidence_json"]["source_context"]=carry["source_context"]
                row["evidence_json"]["source_context_status"]="SAME_SENTENCE_EXPERIMENTAL_ARM"
            row["evidence_json"]["field_status"]=field_status
            yield row
            continue
        rescue=re.search(r"\b([A-Za-z][\w-]{2,60}) reversed the ([A-Z][A-Za-z0-9_-]{2,40})-induced reduction of\b", clause)
        if rescue and rescue.group(1).casefold() in names and len(distinct_endpoints)==1 and len(distinct_molecules)<=1:
            surface=rescue.group(1)
            local=context(clause, surface)
            field_status=local.pop("_field_status")
            row=row_data(names[surface.casefold()], surface, ep[0][2], None, sentence, comparator=None,
                         evidence_json={"clause":clause,"field_source":"SAME_RESULT_CLAUSE","effect_semantics":"RESCUE_OF_STRESS_INDUCED_LOSS",
                                        "effect_subject":surface,"loss_subject":rescue.group(2),"reference_state":"STRESSED_STATE",
                                        "reference_record_id":None,"percent_basis":"NOT_CONVERTED"}, **local)
            annotate(row, clause)
            row["evidence_json"]["source_context"]=rescue.group(2)
            row["evidence_json"]["source_context_status"]="SAME_SENTENCE_STRESSOR"
            row["evidence_json"]["effect_semantics"]="RESCUE_OF_STRESS_INDUCED_LOSS"
            row["evidence_json"]["field_status"]=field_status
            row.update(value=None, unit=None, direction=None)
            yield row
            carry={"surface":surface,"mol":names[surface.casefold()],"cell":row["cell"],"source_context":rescue.group(2)}
            continue
        if len(arm_effects)>=2 and len(distinct_molecules)==1 and len(distinct_endpoints)==1:
            surface=molecule_names[0][0]
            mol=names[surface.casefold()]
            endpoint=ep[0][2]
            local=context(clause,surface)
            field_status=local.pop("_field_status")
            comparator=comparator_span(clause)
            for effect in arm_effects:
                sign=effect.group(2)
                arm_direction="INCREASE" if sign=="+" else "DECREASE" if sign in "-−" else None
                arm_span=effect.group(1).strip()
                row=row_data(mol,surface,endpoint,arm_direction,sentence,comparator=comparator,
                             evidence_json={"clause":clause,"bound_surface":effect.group(0),"field_source":"SAME_RESULT_CLAUSE",
                                            "arm_span":arm_span,"arm_role":"EXPLICIT_PARENTHETICAL_ARM",
                                            "parent_scalar_reason":"MULTIPLE_DISTINCT_ARM_EFFECTS",
                                            "matched_control_record_id":None,"percent_basis":"AUTHOR_RELATIVE_CHANGE_NOT_CONVERTED"},
                             **local)
                row["cell"]=arm_span
                row["evidence_json"]["field_status"]=dict(field_status,cell="BOUND_EXPLICIT_ARM_SPAN",value="PERCENT_CHANGE_SAME_ARM")
                row.update(value=float(effect.group(3)),unit="%",value_literal=effect.group(0).strip(),
                           value_kind="PERCENT_CHANGE",level="L2" if comparator else "L1")
                annotate(row, clause)
                carry={"surface":surface,"mol":mol,"cell":row["cell"],"source_context":row["evidence_json"].get("source_context")}
                yield row
            continue
        local_arms=coordinated_model_arms(clause)
        if local_arms and not distinct_molecules:
            continue
        if (local_arms and len(distinct_molecules)==1 and len(distinct_endpoints)==1
                and (len(local_arms)>1 or any(arm["modifier"] for arm in local_arms))):
            surface=molecule_names[0][0]
            mol=names[surface.casefold()]
            endpoint=ep[0][2]
            word=clause_direction(clause)
            comparator=comparator_span(clause)
            shared=context(clause, surface)
            for arm in local_arms:
                local=dict(shared)
                field_status=dict(local.pop("_field_status"))
                local["cell"]=arm["model"]
                field_status["cell"]="BOUND_ARM_LOCAL_MODEL"
                row=row_data(mol, surface, endpoint, word, sentence, comparator=comparator,
                             evidence_json={"clause":clause,"field_source":"ARM_LOCAL_MODEL","percent_basis":"NOT_CONVERTED"}, **local)
                row["cell"]=arm["model"]
                row["stimulus"]=stimulus_of(clause)
                annotate(row, clause)
                row["evidence_json"]["field_status"]=field_status
                row["evidence_json"]["cell_anchor"]={"start":clause_at+arm["model_span"][0], "end":clause_at+arm["model_span"][1], "text":arm["model"]}
                during=row["evidence_json"].get("source_context")
                if arm["modifier"] and len(local_arms)==1 and during in (None, arm["modifier"]):
                    if during is None:
                        row["evidence_json"]["source_context"]=arm["modifier"]
                        row["evidence_json"]["source_context_status"]="ARM_LOCAL_MODIFIER"
                    start,end=arm["modifier_span"]
                    row["evidence_json"]["source_context_anchor"]={"start":clause_at+start, "end":clause_at+end, "text":arm["modifier"]}
                elif arm["modifier"] and len(local_arms)==1:
                    row["evidence_json"]["source_context"]=None
                    row["evidence_json"]["source_context_status"]="AMBIGUOUS_CONTEXT"
                elif arm["modifier"]:
                    start,end=arm["modifier_span"]
                    row["evidence_json"]["source_context"]=arm["modifier"]
                    row["evidence_json"]["source_context_status"]="ARM_LOCAL_MODIFIER"
                    row["evidence_json"]["source_context_anchor"]={"start":clause_at+start, "end":clause_at+end, "text":arm["modifier"]}
                else:
                    row["evidence_json"]["source_context"]=None
                    row["evidence_json"]["source_context_status"]="UNKNOWN"
                yield row
            carry=None
            continue
        yielded=False
        for rule in PATTERNS:
            for hit in rule.finditer(masked):
                mol,name=arms[hit["m"]]
                preceding=masked[:hit.start()]
                if re.search(r"(?:co[- ]?treatment with|combination (?:of|with)|and|plus|induced by|prevented|reversed|inhibited|attenuated)\s*$",preceding,AT):
                    continue
                if re.search(r"(?:induced|caused|triggered|mediated)\s+by\b|[- ]induced\b",masked[hit.end():hit.end()+35],AT):continue
                endpoint=ep[int(hit["e"][2:-1])][2]
                ctrl_text=comparator_span(clause)
                word=direction(hit["dir"])
                local=context(clause,name)
                field_status=local.pop("_field_status")
                row=row_data(mol,name,endpoint,word,sentence,comparator=ctrl_text,
                             evidence_json={"clause":clause,"bound_surface":hit[0],"field_source":"SAME_RESULT_CLAUSE"},**local)
                # A percent is bound only when this clause has one molecule and one basis.
                binding=bind_percent(clause, len({item[0] for item in arms.values()}))
                field_status["value"]=binding["status"]
                row["evidence_json"]["field_status"]=field_status
                row["evidence_json"]["percent_basis"]=(
                    "AUTHOR_RELATIVE_CHANGE_NOT_CONVERTED" if binding["value_kind"] in ("PERCENT_CHANGE","FOLD_CHANGE")
                    else "NOT_CONVERTED")
                if endpoint=="OXYGEN_CONSUMPTION" and re.search(r"ATP[-\s]linked\s+oxygen consumption", clause, AT):
                    row["evidence_json"]["endpoint_qualifier"]="ATP_LINKED_NOT_ATP_CONTENT"
                if binding["value"] is not None and word in ("INCREASE","DECREASE"):
                    row.update(value=binding["value"], unit=binding["unit"], value_literal=binding["value_literal"],
                               value_kind=binding["value_kind"], level="L2" if ctrl_text else "L1")
                elif binding["value_literal"]:
                    row.update(value=None, value_literal=binding["value_literal"], value_kind=binding["value_kind"])
                yielded=True
                annotate(row, clause)
                leave_unmodified_arm_unknown(row, clause)
                carry={"surface":name,"mol":mol,"cell":row["cell"],"source_context":row["evidence_json"].get("source_context")}
                yield row
        if yielded or len(distinct_molecules)!=1 or len(distinct_endpoints)!=1:
            continue
        surface=molecule_names[0][0]
        binding=bind_percent(clause, 1)
        if binding["status"]!="PERCENT_OF_CONTROL_NOT_CONVERTED":
            continue
        local=context(clause,surface)
        field_status=local.pop("_field_status")
        field_status["value"]=binding["status"]
        row=row_data(names[surface.casefold()],surface,ep[0][2],None,sentence,comparator=comparator_span(clause),
                     evidence_json={"clause":clause,"field_source":"SAME_RESULT_CLAUSE","percent_basis":"NOT_CONVERTED",
                                    "matched_control_record_id":None},**local)
        row["evidence_json"]["field_status"]=field_status
        row.update(value=None,unit=None,value_literal=binding["value_literal"],value_kind=binding["value_kind"])
        annotate(row, clause)
        leave_unmodified_arm_unknown(row, clause)
        yield row


def indexed_path(root, target):
    def walk(node):
        seen=Counter()
        for child in node:
            seen[child.tag]+=1
            step=f"{child.tag}[{seen[child.tag]}]"
            if child is target:
                return step
            nested=walk(child)
            if nested:
                return step+"/"+nested
        return None
    return walk(root)


def resolve_author_table(node):
    """Use the one author table. A table under alternatives is still that table; several candidates are not guessed."""
    direct=[child for child in node if child.tag=="table"]
    if len(direct)==1:
        return direct[0], None
    nested=[item for item in node.iter("table") if item is not node]
    if direct:
        return None, "AMBIGUOUS_NESTED_TABLE"
    if len(nested)==1:
        return nested[0], None
    if len(nested)>1:
        return None, "AMBIGUOUS_NESTED_TABLE"
    return None, None


def native_table(task,node,loc,names,pattern):
    caption=spaced(node.find("caption"))
    tab,problem=resolve_author_table(node)
    if problem:
        yield ("ambiguous",)
        return
    if tab is None or any(c.get("rowspan","1")!="1" or c.get("colspan","1")!="1" for c in tab.iter()):
        return
    table_path=indexed_path(node, tab)
    if preparation_block(caption):
        yield ("mixture",)
        return
    head=tab.findall("./thead/tr")
    body=tab.findall("./tbody/tr")
    if len(head)!=1 or not body:return
    headers=[spaced(c) for c in head[0] if c.tag in ("td","th")]
    for rix,tr in enumerate(body,1):
        cells=[c for c in tr if c.tag in ("td","th")]
        if len(cells)!=len(headers) or len(cells)<2:continue
        literals=[spaced(c) for c in cells]
        for i in range(1,len(cells)):
            # A single rectangular axis carries the arm, the other the measured endpoint.
            for arm_text,measure in ((literals[0],headers[i]),(headers[i],literals[0])):
                if preparation_block(arm_text):
                    yield ("mixture",)
                    continue
                arms=list(pattern.finditer(arm_text))
                if len(arms)!=1 or re.search(r"\b(?:control|without|untreated|inhibitor of)\b",arm_text,AT):continue
                arm=arms[0][0]
                residual=arm_text[:arms[0].start()]+" "+arm_text[arms[0].end():]
                residual=re.sub(DOSE+"|"+TIME,"",residual,flags=AT)
                if not re.fullmatch(r"[\s(),.:/-]*(?:(?:treated|treatment|group|cells)[\s(),.:/-]*)*",residual,AT):continue
                eps=task.endpoints(measure)
                units=list(task.unit.finditer(measure))
                if len(eps)!=1 or len(units)!=1:continue
                m=re.fullmatch(rf"\s*({NUMBER})(?:\s*[±]\s*({NUMBER}))?\s*[*†‡]*\s*",literals[i])
                if not m:continue
                context_text=caption+" "+arm_text
                method=task.methods.search(context_text)
                model=first(CELL,context_text)
                if not method or not model:continue
                unit=units[0][0]
                # Build the locator for the actual element type, including header-like first cells.
                col=sum(c.tag==cells[i].tag for c in cells[:i+1])
                where=f"{loc}/{table_path}/tbody[1]/tr[{rix}]/{cells[i].tag}[{col}]"
                yield ("fact", where, row_data(names[arm.casefold()],arm,eps[0][2],None,literals[i],level="L3",
                    value=float(m[1]),unit=unit,value_literal=literals[i],value_kind="AUTHOR_GROUP_VALUE",
                    method=method[0],cell=model,dose=first(DOSE,arm_text),exposure_time=first(TIME,arm_text),
                    stimulus=stimulus_of(context_text),
                    normalization=unit,source_kind="NATIVE_JATS_TABLE",statistic="AUTHOR_PLUS_MINUS_UNSPECIFIED" if m[2] else None,
                    evidence_json={"table_locator":loc,"caption":caption,"arm_header":arm_text,"endpoint_header":measure,
                                   "native_row":rix,"native_column":i+1}))


def parse(task,raw,registry):
    root=ET.fromstring(raw)
    if root.tag.split("}")[-1]!="article":
        raise ValueError("Expected a single JATS article")
    for node in root.iter():
        if "}" in node.tag:
            node.tag=node.tag.split("}",1)[1]
    if root.attrib.get("article-type","").lower() in {"review-article","systematic-review","editorial","retraction","letter","correction","protocol","commentary"}:
        return [],[],"ARTICLE_TYPE_EXCLUDED"
    body=root.find("body")
    if body is None:return [],[],"BODY_MISSING"
    names,pattern=registry.document(flowed(body))
    rows={}
    passages=[]
    for kind,loc,item in parts(root):
        if kind=="table":
            saw_mixture=False
            saw_ambiguous=False
            for event in native_table(task,item,loc,names,pattern):
                if event[0]=="mixture":
                    saw_mixture=True
                    continue
                if event[0]=="ambiguous":
                    saw_ambiguous=True
                    continue
                _,anchor,row=event
                rows[(anchor,row["molecule"],row["endpoint"],row["quote"])]=row
            if saw_mixture:
                passages.append((loc,"TABLE_MIXTURE_ARM",canonical([]),canonical([]),"MIXTURE_OR_PREPARATION_UNRESOLVED"))
            if saw_ambiguous:
                passages.append((loc,"AMBIGUOUS_NESTED_TABLE",canonical([]),canonical([]),"AMBIGUOUS_NESTED_TABLE"))
            continue
        eps=task.endpoints(item)
        if not eps:continue
        mentions=sorted({names[m[0].casefold()] for m in pattern.finditer(item)})
        if preparation_block(item):
            passages.append((loc,item,canonical(sorted({e for _,_,e in eps})),canonical(mentions),"MIXTURE_OR_PREPARATION_UNRESOLVED"))
            continue
        found=list(prose(task,item,names,pattern))
        fragment_only=not found and not mentions and any(arm["modifier"] for arm in coordinated_model_arms(item))
        for row in found:
            # Parallel clauses with the same molecule/different dose remain distinct.
            arm_span=(row.get("evidence_json") or {}).get("arm_span")
            k=(loc,row["molecule"],row["endpoint"],row["quote"],row["dose"],row["direction"],row["value"],row.get("cell"),arm_span)
            if k not in rows or row["level"]>rows[k]["level"]:
                rows[k]=row
        passages.append((loc,item,canonical(sorted({e for _,_,e in eps})),canonical(mentions),
                         "CONTEXT_FRAGMENT_MOLECULE_UNKNOWN" if fragment_only else "BOUND_CANDIDATE" if found else "ARM_OR_VALUE_UNBOUND"))
    return [(k[0],r) for k,r in rows.items()],passages,None


def persist(conn,task,paper,doi,sha,rows,passages,detail,seconds):
    for loc,row in rows:
        row=dict(row)
        row.update(task=task.id,paper=paper,doi=doi,sha=sha,locator=loc)
        row["missing"]=canonical({field:"NO_UNAMBIGUOUS_LOCAL_AUTHOR_BINDING" for field in
            ("cell","stimulus","dose","exposure_time","comparator","method","normalization","statistic","value")
            if row[field] is None})
        row["evidence_json"]=canonical(row["evidence_json"])
        row["id"]=sha256(canonical(row).encode())
        if row["molecule"].startswith("DISC:"):
            conn.execute("INSERT OR IGNORE INTO r42_discovered(molecule,literal) VALUES(?,?)",(row["molecule"],row["arm"]))
        fields=tuple(row)
        conn.execute("INSERT OR IGNORE INTO r42_relation("+",".join(fields)+") VALUES("+",".join(":"+f for f in fields)+")",row)
    conn.executemany("INSERT OR IGNORE INTO r42_passage VALUES(?,?,?,?,?,?,?,?)",
        ((task.id,paper,sha,*row) for row in passages))
    count=conn.execute("SELECT count(*) FROM r42_relation WHERE task=? AND paper=? AND sha=?",(task.id,paper,sha)).fetchone()[0]
    conn.execute("UPDATE r42_extraction_job SET status='DONE',detail=?,relation_count=?,seconds=?,updated=CURRENT_TIMESTAMP WHERE task=? AND paper=? AND sha=?",
                 (detail,count,seconds,task.id,paper,sha))
    return count


def paper_detail(detail, rows, passages):
    if detail or rows:
        return detail
    reasons={item[-1] for item in passages}
    if "AMBIGUOUS_NESTED_TABLE" in reasons:
        return "AMBIGUOUS_NESTED_TABLE"
    if "MIXTURE_OR_PREPARATION_UNRESOLVED" in reasons:
        return "MIXTURE_OR_PREPARATION_UNRESOLVED"
    if passages:
        return "UNBOUND_MENTION_NO_FACT"
    return "NO_RELEVANT_FACT"


def ledger_counts(c):
    counts={}
    for name in PROTECTED_LEDGERS:
        if c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",(name,)).fetchone():
            counts[name]=c.execute("SELECT count(*) FROM "+name).fetchone()[0]
    return counts


def registry_rows(c, extra_names):
    rows=[]
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='r42_names'").fetchone():
        rows.extend(tuple(r) for r in c.execute("SELECT molecule,name FROM r42_names ORDER BY molecule,name"))
    rows.extend(tuple(item) for item in (extra_names or ()))
    if not rows:
        raise ValueError("Seed identity registry is empty")
    return rows


def extract(home,task="small_molecule_atp",limit=100,*,retry=False,extra_names=None,after_paper=None,only=None):
    if only is None and not 1<=limit<=10000:raise ValueError("limit must be 1..10000")
    if only is not None and not 1<=len(only)<=25:raise ValueError("REPLAY_SELECTION_1_TO_25")
    db=Path(home)/"state/science.sqlite3"
    if not db.is_file():raise ValueError(f"Missing Evidence Core: {db}")
    from .r42_collect import ensure_available_view
    contract=load_contract(task)
    with closing(sqlite3.connect(db,timeout=60)) as c:
        c.execute("PRAGMA foreign_keys=ON");c.executescript(SCHEMA)
        ensure_available_view(c)
        names=registry_rows(c, extra_names)
        registry=Names(names)
        rule=sha256((contract.hash+canonical(names)).encode())
        frozen=ledger_counts(c)
        stats=Counter();failures=[];outcomes=[]
        if only is None:
            pending=c.execute("""SELECT a.paper,a.doi,a.path,a.sha FROM r42_available a
              LEFT JOIN r42_extraction_job j ON j.paper=a.paper AND j.sha=a.sha AND j.task=?
              WHERE j.paper IS NULL OR j.rule_sha!=? OR (? AND j.status='HOLD')
              ORDER BY a.paper LIMIT ?""",(contract.id,rule,int(retry),limit)).fetchall()
        else:
            pending=[]
            for paper,sha in only:
                row=c.execute("SELECT paper,doi,path,sha FROM r42_available WHERE paper=? AND sha=?",(paper,sha)).fetchone()
                if not row:
                    failures.append({"paper":paper,"reason":"SELECTED_SOURCE_NOT_AVAILABLE"})
                    outcomes.append({"paper":paper,"state":"REJECTED","detail":"SELECTED_SOURCE_NOT_AVAILABLE","relations":0})
                    continue
                job=c.execute("SELECT status,rule_sha FROM r42_extraction_job WHERE task=? AND paper=? AND sha=?",(contract.id,paper,sha)).fetchone()
                if job and job[0]=="DONE" and job[1]==rule and not retry:
                    outcomes.append({"paper":paper,"state":"ALREADY_DONE","detail":None,"relations":None,"rewritten":False})
                    continue
                if job and job[0]=="HOLD" and not retry:
                    outcomes.append({"paper":paper,"state":"HOLD_NOT_RETRIED","detail":"HOLD","relations":0,"rewritten":False})
                    continue
                pending.append((row[0],row[1],row[2],row[3],bool(job)))
        for item in pending:
            rewritten=False
            if only is None:
                paper,doi,path,sha=item
            else:
                paper,doi,path,sha,rewritten=item
            start=time.monotonic()
            try:
                raw=Path(path).read_bytes()
                if sha256(raw)!=sha:raise ValueError("Author source SHA mismatch")
                rows,passages,detail=parse(contract,raw,registry)
                detail=paper_detail(detail, rows, passages)
            except (ET.ParseError,OSError,ValueError) as e:
                with c:
                    c.execute("""INSERT INTO r42_extraction_job(task,paper,sha,status,detail,relation_count,rule_sha)
                      VALUES(?,?,?,'HOLD',?,0,?) ON CONFLICT(task,paper,sha) DO UPDATE SET status='HOLD',detail=excluded.detail,rule_sha=excluded.rule_sha""",
                        (contract.id,paper,sha,str(e),rule))
                failures.append({"paper":paper,"reason":str(e)})
                outcome={"paper":paper,"state":"HOLD","detail":str(e),"relations":0}
                outcomes.append(outcome)
                if after_paper:after_paper(outcome)
                continue
            skipped=False
            with c:
                # Recheck after the write lock so concurrent ticks cannot count a paper twice.
                c.execute("BEGIN IMMEDIATE")
                previous=c.execute("SELECT status,rule_sha FROM r42_extraction_job WHERE task=? AND paper=? AND sha=?",(contract.id,paper,sha)).fetchone()
                if previous==("DONE",rule):
                    skipped=True
                else:
                    c.execute("""INSERT INTO r42_extraction_job(task,paper,sha,status,detail,relation_count,rule_sha)
                      VALUES(?,?,?,'PROCESSING',NULL,0,?) ON CONFLICT(task,paper,sha) DO UPDATE SET rule_sha=excluded.rule_sha""",(contract.id,paper,sha,rule))
                    c.execute("DELETE FROM r42_relation WHERE task=? AND paper=? AND sha=?",(contract.id,paper,sha))
                    c.execute("DELETE FROM r42_passage WHERE task=? AND paper=? AND sha=?",(contract.id,paper,sha))
                    n=persist(c,contract,paper,doi,sha,rows,passages,detail,time.monotonic()-start)
                    if ledger_counts(c)!=frozen:
                        raise RuntimeError("PROTECTED_LEDGER_CHANGED")
            if skipped:continue
            stats.update(papers=1,relations=n,passages=len(passages),papers_with_relation=int(n>0))
            if detail=="NO_RELEVANT_FACT":stats["no_relevant_fact"]+=1
            elif detail=="UNBOUND_MENTION_NO_FACT":stats["unbound"]+=1
            elif detail=="MIXTURE_OR_PREPARATION_UNRESOLVED":stats["mixture_unresolved"]+=1
            elif detail=="AMBIGUOUS_NESTED_TABLE":stats["ambiguous_table"]+=1
            outcome={"paper":paper,"state":"REWRITTEN" if rewritten else "DONE","detail":detail,"relations":n,"rewritten":rewritten}
            outcomes.append(outcome)
            if after_paper:after_paper(outcome)
        return dict(task=contract.id,rule_sha=rule,papers=stats["papers"],relations=stats["relations"],
                    passages=stats["passages"],papers_with_relation=stats["papers_with_relation"],
                    no_relevant_fact=stats["no_relevant_fact"],unbound=stats["unbound"],
                    mixture_unresolved=stats["mixture_unresolved"],ambiguous_table=stats["ambiguous_table"],
                    held=failures,outcomes=outcomes,
                    protected_ledgers=frozen)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home",type=Path,required=True)
    p.add_argument("--task",default="small_molecule_atp",help="Packaged task name or JSON contract path")
    p.add_argument("--limit",type=int,default=100);p.add_argument("--retry",action="store_true")
    a=p.parse_args(argv);print(json.dumps(extract(a.home,a.task,a.limit,retry=a.retry),ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

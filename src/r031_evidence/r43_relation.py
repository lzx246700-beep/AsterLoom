"""R43 U1/U2 shadow-only source-anchored relation extraction prototype.

No training label, scientific approval, or R42/R41/R17 data is modified.
Candidates may be scientifically wrong; positive/negative metrics remain NULL.
Unreviewed snippets never borrow methods, doses or controls from other panels.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from . import r42_extract

RULE = "r43.entity-arm-source/0.1.0"
SCHEMA = """
CREATE TABLE IF NOT EXISTS r43_meta(
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r43_document_block(
 id TEXT PRIMARY KEY, paper TEXT NOT NULL, author_sha TEXT NOT NULL,
 locator TEXT NOT NULL, quote TEXT NOT NULL, kind TEXT NOT NULL,
 r42_reason TEXT NOT NULL, source_verified INTEGER NOT NULL CHECK(source_verified IN (0,1)),
 rule TEXT NOT NULL, UNIQUE(paper,author_sha,locator,quote));
CREATE TABLE IF NOT EXISTS r43_entity_mention(
 id TEXT PRIMARY KEY, block_id TEXT NOT NULL REFERENCES r43_document_block(id),
 kind TEXT NOT NULL, original_literal TEXT NOT NULL,
 span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
 registry_identity TEXT, identity_state TEXT NOT NULL,
 original_sha TEXT NOT NULL, locator TEXT NOT NULL, rule TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r43_experiment_arm(
 id TEXT PRIMARY KEY, block_id TEXT NOT NULL REFERENCES r43_document_block(id),
 role TEXT NOT NULL CHECK(role IN ('TREATMENT_CANDIDATE','COMPARATOR_CANDIDATE')),
 author_literal TEXT NOT NULL, span_start INTEGER NOT NULL,span_end INTEGER NOT NULL,
 source_binding_state TEXT NOT NULL, rule TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r43_candidate(
 id TEXT PRIMARY KEY, block_id TEXT NOT NULL REFERENCES r43_document_block(id),
 inherited_r42_relation_id TEXT, molecular_identity TEXT,
 endpoint TEXT NOT NULL, direction TEXT, quantity_literal TEXT,
 treatment_arm_id TEXT REFERENCES r43_experiment_arm(id),
 comparator_arm_id TEXT REFERENCES r43_experiment_arm(id),
 evidence_state TEXT NOT NULL DEFAULT 'CANDIDATE' CHECK(evidence_state='CANDIDATE'),
 hold_reason TEXT NOT NULL, source_basis TEXT NOT NULL,
 rule TEXT NOT NULL, training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0));
CREATE TABLE IF NOT EXISTS r43_experiment_relation(
 id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES r43_candidate(id),
 treatment_arm_id TEXT NOT NULL REFERENCES r43_experiment_arm(id),
 comparator_arm_id TEXT NOT NULL REFERENCES r43_experiment_arm(id),
 same_source_block_proved INTEGER NOT NULL CHECK(same_source_block_proved=1),
 source_status TEXT NOT NULL DEFAULT 'SOURCE_ANCHORED_UNREVIEWED',
 scientist_approved INTEGER NOT NULL DEFAULT 0 CHECK(scientist_approved=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0));
CREATE TABLE IF NOT EXISTS r43_field_provenance(
 id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES r43_candidate(id),
 field_name TEXT NOT NULL, original_literal TEXT NOT NULL,
 source_sha TEXT NOT NULL, locator TEXT NOT NULL, quote TEXT NOT NULL,
 span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
 binding_state TEXT NOT NULL, source_kind TEXT NOT NULL,rule TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r43_human_review(
 review_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES r43_candidate(id),
 human_reviewer_id TEXT NOT NULL, human_signature TEXT NOT NULL,
 adjudication_json TEXT NOT NULL, record_state TEXT NOT NULL
 CHECK(record_state IN ('PENDING_SECOND_REVIEW','SCIENTIFICALLY_REVIEWED')));
"""
CELL_RX = re.compile(r"\b(?:RAW\s*264[.]?7|THP[- ]?1|BMDMs?|bone.marrow.derived macrophages?|macrophages?)\b",re.I)
PHENOTYPE_RX = re.compile(r"\bM[12]\s+(?:phenotype|polarization|macrophages?)\b",re.I)
STIM_RX = re.compile(r"\b(?:LPS|lipopolysaccharide|IFN[- ]?[γγ]|IFN[- ]?gamma|IL[- ]?4|TNF[- ]?[αα])\b",re.I)
DOSE_RX = re.compile(r"(?<![\w.])\d+(?:[.]\d+)?\s*(?:μM|µM|nM|mM|mg/mL|mg/kg|µg/mL|μg/mL)\b",re.I)
CONTROL_RX = re.compile(
 r"\b(?:compared\s+(?:with|to)|relative\s+to|versus|vs\.?)\s+"
 r"(?:(?:the|a|those of|that of)\s+)?"
 r"(?:untreated|vehicle[- ]?treated|saline[- ]?treated|DMSO[- ]?treated|"
 r"model|control|normal|blank|healthy|LPS[- ]?treated)"
 r"(?:\s+(?:group|cells|controls?|mice|macrophages?))?",re.I)
MIXTURE_RX = re.compile(r"\b(?:extracts?|fractions?|mixtures?|nanoparticles?|"
 r"liposomes?|hydrogels?|loaded\s+nanoparticles?|formulations?)\b|NP@|-loaded\b",re.I)
STRESS_RX = re.compile(r"\b(?:rescue|rescued|restored|damaged|injured|LPS|"
 r"challenged|P301L|disease[- ]associated|deficits?)\b",re.I)
TREAT_RX = re.compile(r"\b(?:treated|treatment|incubated|incubation|pretreated|exposed|administration|supplemented)\b",re.I)
DIR_RX = re.compile(r"\b(?:increased|enhanced|elevated|raised|"
 r"decreased|reduced|suppressed|diminished|restored|rescued)\b",re.I)
PERCENT_RX = re.compile(r"\(?[+-]?\d+(?:[.]\d+)?\s*%\)?")
NEGATION_RX = re.compile(r"\b(?:did\s+not|does\s+not|was\s+not|were\s+not|no\s+(?:significant\s+)?(?:change|effect|increase|decrease)|not\s+significantly)\b",re.I)
DOSE_VARIANT_RX = re.compile(r"\b(?:varied\s+among\s+concentrations|dose[- ]dependent|multiple\s+doses|various\s+concentrations)\b",re.I)

def stable(prefix,*tokens):
 return prefix+hashlib.sha256(("|".join(map(str,tokens))).encode()).hexdigest()[:36]

def obj(o):return json.dumps(o,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def insert_immutable(c, table, record):
 cols=list(record)
 existing=c.execute("SELECT "+",".join(cols)+" FROM "+table+" WHERE "+cols[0]+"=?",
                    (record[cols[0]],)).fetchone()
 expected=tuple(record.values())
 if existing is not None:
  if tuple(existing)!=expected:
   raise RuntimeError("R43_IMMUTABLE_IDENTITY_DRIFT:"+table+":"+str(record[cols[0]]))
  return False
 c.execute("INSERT INTO "+table+"("+",".join(cols)+") VALUES("+",".join("?" for _ in cols)+")",expected)
 return True

def rule_sha():
 return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def source_index(raw):
 root=ET.fromstring(raw)
 if root.tag.rsplit("}",1)[-1]!="article":
  raise ValueError("SOURCE_NOT_AUTHOR_JATS")
 for element in root.iter():
  if "}" in element.tag:element.tag=element.tag.rsplit("}",1)[-1]
 indexed=defaultdict(set)
 for kind,locator,part in r42_extract.parts(root):
  if isinstance(part,str):
   indexed[locator].add(" ".join(part.split()))
 return indexed

def aliases(c):
 d=defaultdict(set)
 # R42 name registry is only a candidate alias source; collisions must HOLD.
 for identity,name in c.execute("SELECT molecule,name FROM r42_seed_name"):
  key=name.strip().casefold()
  if 4<=len(key)<=100 and key not in r42_extract.STOP_NAMES:
   d[key].add(identity)
 if not d:raise ValueError("R42_NO_SEED_NAMES")
 pattern=re.compile(r"(?<![\w-])(?:"+ "|".join(re.escape(x) for x in sorted(d,key=len,reverse=True))+r")(?!\w|-(?!loaded\b|treated\b))",re.I)
 return d,pattern

def annotate_block(c,paper,author_sha,locator,quote,reason,index,aliases_by_name,chemical_pat,r42_relations):
 source_verified=int(" ".join(quote.split()) in index.get(locator,set()))
 kind=("FIGURE_CAPTION" if "/caption" in locator or "/fig" in locator else
       "JATS_RESULT_TEXT" if "/p[" in locator else "UNKNOWN_BLOCK")
 block_id=stable("r43:block:",paper,author_sha,locator,quote)
 insert_immutable(c,"r43_document_block",dict(id=block_id,paper=paper,
  author_sha=author_sha,locator=locator,quote=quote,kind=kind,r42_reason=reason,
  source_verified=source_verified,rule=RULE))
 if not source_verified:
  return {"blocks":1,"source_hold":1,"chemicals":0,"arms":0,"candidates":0,"pairs":0}
 entities=[]
 for m in CELL_RX.finditer(quote):
  entities.append(("CELL_MODEL",m,None,"LOCAL_ORIENTATION_UNREVIEWED"))
 for m in PHENOTYPE_RX.finditer(quote):
  entities.append(("POLARIZATION_PHENOTYPE",m,None,"PHENOTYPE_NOT_CELL_LINE"))
 for m in STIM_RX.finditer(quote):
  entities.append(("STIMULUS",m,None,"ARM_SCOPE_UNCONFIRMED"))
 for m in DOSE_RX.finditer(quote):
  entities.append(("DOSE_LITERAL",m,None,"NO_TREATMENT_ARM_BOUND"))
 for m in chemical_pat.finditer(quote):
  identity_set=aliases_by_name[m.group(0).casefold()]
  mixture=bool(MIXTURE_RX.search(quote))
  salt_suffix=bool(re.match(r"\s+(?:hydrochloride|sodium\s+salt|sulfate|phosphate|glycoside)\b",
                   quote[m.end():m.end()+32],re.I))
  identity=(next(iter(identity_set)) if len(identity_set)==1 and not mixture and not salt_suffix else None)
  hold=("MIXTURE_OR_CARRIER_CHEMICAL_HOLD" if mixture else
        "CHEMICAL_SALT_OR_DERIVATIVE_HOLD" if salt_suffix else
        "ALIAS_COLLISION_HOLD" if len(identity_set)>1 else
        "NO_REGISTRY_MATCH_HOLD" if not identity_set else
        "EXACT_ALIAS_SINGLE_SEED_ID_UNREVIEWED")
  entities.append(("CHEMICAL_CANDIDATE",m,identity,hold))
 for kind,m,identity,state in entities:
  eid=stable("r43:mention:",block_id,kind,m.start(),m.end(),m.group(0),identity)
  insert_immutable(c,"r43_entity_mention",dict(id=eid,block_id=block_id,kind=kind,
   original_literal=m.group(0),span_start=m.start(),span_end=m.end(),
   registry_identity=identity,identity_state=state,original_sha=author_sha,locator=locator,rule=RULE))
 chemicals=[(m,id) for kind,m,id,state in entities if kind=="CHEMICAL_CANDIDATE" and id]
 controls=list(CONTROL_RX.finditer(quote))
 treatment=None
 comparator=None
 if len({id for m,id in chemicals})==1 and TREAT_RX.search(quote) and not MIXTURE_RX.search(quote):
  # This is a literal same-block treatment mention, not verified role binding.
  match=chemicals[0][0]
  treatment=dict(id=stable("r43:arm:",block_id,"TREATMENT",match.start(),match.end()),
   block_id=block_id,role="TREATMENT_CANDIDATE",author_literal=match.group(0),
   span_start=match.start(),span_end=match.end(),source_binding_state="SAME_BLOCK_CHEMICAL_TREATMENT_CUE",rule=RULE)
  insert_immutable(c,"r43_experiment_arm",treatment)
 if len(controls)==1:
  match=controls[0]
  comparator=dict(id=stable("r43:arm:",block_id,"COMPARATOR",match.start(),match.end()),
   block_id=block_id,role="COMPARATOR_CANDIDATE",author_literal=match.group(0),
   span_start=match.start(),span_end=match.end(),source_binding_state="EXPLICIT_SAME_BLOCK_CONTROL_CUE",rule=RULE)
  insert_immutable(c,"r43_experiment_arm",comparator)
 eps=[]
 for source in r42_relations:
  eps.append((source["id"],source["molecule"],source["endpoint"],source["direction"],"INHERITED_R42_CANDIDATE",source.get("value_literal")))
 # Previously unbound passages may produce an additional review proposal,
 # but ONLY with exact same-block chemical, ATP endpoint, comparator, treatment
 # and direction. It remains unreviewed even if source SHA is fully verified.
 if not eps and treatment and comparator and DIR_RX.search(quote) and not NEGATION_RX.search(quote):
  if re.search(r"\bATP\s+(?:content|levels?|concentration)\b",quote,re.I):
   if not re.search(r"\b(?:oxygen\s+consumption|OCR|ECAR|ATP[- ]linked\s+OCR)\b",quote,re.I):
    eps.append((None,chemicals[0][1],"ATP_CONTENT",
     "DIRECTION_MENTION_NOT_SCIENCE_REVIEWED","NEW_SAME_BLOCK_REVIEW_PROPOSAL",None))
 count=0;pairs=0
 for legacy,chemical,endpoint,direction,basis,quantity in eps:
  flags=[]
  if not treatment:flags.append("NO_UNAMBIGUOUS_TREATMENT_ARM")
  if not comparator:flags.append("NO_EXPLICIT_SINGLE_COMPARATOR")
  if len({id for m,id in chemicals})>1:flags.append("MULTIPLE_CHEMICAL_IDENTITIES")
  if MIXTURE_RX.search(quote):flags.append("MIXTURE_OR_CARRIER")
  if STRESS_RX.search(quote):flags.append("STRESS_OR_RESCUE_NOT_HEALTHY_BASELINE")
  if len(PERCENT_RX.findall(quote))>1:flags.append("MULTIPLE_PERCENT_EFFECTS_SAME_BLOCK")
  if DOSE_VARIANT_RX.search(quote):flags.append("UNMATCHED_DOSE_VARIANTS")
  if NEGATION_RX.search(quote):flags.append("NEGATED_OR_NONSIGNIFICANT_EFFECT")
  if endpoint!="ATP_CONTENT":flags.append("ENDPOINT_NOT_DIRECT_ATP_CONTENT")
  if not re.search(r"\b(?:ATP|adenosine\s+triphosphate)\b",quote,re.I):
   flags.append("NO_LOCAL_AUTHOR_ATP_LITERAL")
  cid=stable("r43:candidate:",block_id,legacy or basis,chemical,endpoint,direction)
  candidate=dict(id=cid,block_id=block_id,inherited_r42_relation_id=legacy,
   molecular_identity=chemical,endpoint=endpoint,direction=direction,
   quantity_literal=quantity,treatment_arm_id=treatment["id"] if treatment else None,
   comparator_arm_id=comparator["id"] if comparator else None,
   evidence_state="CANDIDATE",hold_reason=obj(sorted(set(flags))),
   source_basis=basis,rule=RULE,training_approved=0)
  insert_immutable(c,"r43_candidate",candidate)
  count+=1
  # A same-block edge is merely a suggestion; scientific pair verification
  # and independent author data review are still needed.
  if treatment and comparator and not flags:
   relation=dict(id=stable("r43:pair:",cid),candidate_id=cid,
    treatment_arm_id=treatment["id"],comparator_arm_id=comparator["id"],
    same_source_block_proved=1,source_status="SOURCE_ANCHORED_UNREVIEWED",
    scientist_approved=0,training_approved=0)
   insert_immutable(c,"r43_experiment_relation",relation)
   pairs+=1
  # Only literal spans inside the same author sentence receive provenance.
  for field,pattern in (("cell_model",CELL_RX),("stimulation",STIM_RX),("dose_mention",DOSE_RX),
                         ("chemical_candidate_mention",chemical_pat),
                        ("comparator_mention",CONTROL_RX),("direction_mention",DIR_RX),
                        ("percent_mention_not_absolute_y",PERCENT_RX)):
   for m in pattern.finditer(quote):
    pv=dict(id=stable("r43:field:",cid,field,m.start(),m.end()),
        candidate_id=cid,field_name=field,original_literal=m.group(0),
        source_sha=author_sha,locator=locator,quote=quote,span_start=m.start(),span_end=m.end(),
        binding_state="AUTHOR_LITERAL_IN_SAME_BLOCK_NEEDS_ARM_REVIEW",
        source_kind=kind,rule=RULE)
    insert_immutable(c,"r43_field_provenance",pv)
 return {"blocks":1,"source_hold":0,"chemicals":len(chemicals),
         "arms":int(bool(treatment))+int(bool(comparator)),"candidates":count,"pairs":pairs}

def analyze(shadow_db, manifest_path, split_names=("DEVELOPMENT_TRAIN","DEVELOPMENT")):
 entries=[json.loads(s) for s in manifest_path.read_text().splitlines() if s]
 entries=[e for e in entries if e["split"] in split_names]
 with closing(sqlite3.connect(shadow_db)) as c:
  c.execute("PRAGMA foreign_keys=ON")
  c.executescript(SCHEMA)
  policy=rule_sha()
  c.execute("INSERT OR IGNORE INTO r43_meta(key,value) VALUES('schema','r43-relation/1')")
  c.execute("INSERT OR IGNORE INTO r43_meta(key,value) VALUES('rule_sha256',?)",(policy,))
  if c.execute("SELECT value FROM r43_meta WHERE key='rule_sha256'").fetchone()[0]!=policy:
   raise RuntimeError("R43_RULE_CHANGED_ON_EXISTING_SHADOW_ABORT")
  c.commit()  # Finish meta setup before the first per-paper bounded transaction.
  lookup,chemical_pat=aliases(c)
  stats=Counter()
  for entry in entries:
   paper,src_sha=entry["paper"],entry["source_sha256"]
   source=Path(entry["source_path"])
   raw=source.read_bytes()
   if hashlib.sha256(raw).hexdigest()!=src_sha:
    raise RuntimeError("R43_SOURCE_SHA_DRIFT:"+paper)
   index=source_index(raw)
   passages=list(c.execute("""SELECT locator,quote,reason,sha FROM r42_passage
                           WHERE paper=? ORDER BY locator,quote""",(paper,)))
   relations=defaultdict(list)
   for row in c.execute("""SELECT id,locator,quote,molecule,endpoint,direction,sha,
                             value_literal FROM r42_relation WHERE paper=?""",(paper,)):
    id,loc,quote,mol,endpoint,direction,sha,quantity=row
    if sha!=src_sha:raise ValueError("R42_RELATION_SOURCE_SHA_DRIFT:"+paper)
    relations[(loc,quote)].append(dict(id=id,molecule=mol,endpoint=endpoint,
                                      direction=direction,value_literal=quantity))
   accounted=set()
   c.execute("BEGIN IMMEDIATE")
   try:
    for loc,quote,reason,sha in passages:
     if sha!=src_sha:raise ValueError("R42_PASSAGE_SOURCE_SHA_DRIFT:"+paper)
     r=annotate_block(c,paper,src_sha,loc,quote,reason,index,lookup,chemical_pat,
                      relations.get((loc,quote),[]))
     stats.update(r)
     accounted.update(x["id"] for x in relations.get((loc,quote),[]))
    if any(x["id"] not in accounted for x in sum(relations.values(),[])):
     raise ValueError("R42_RELATION_WITHOUT_ORIGINAL_PASSAGE_BLOCK:"+paper)
    c.commit()
   except BaseException:
    c.rollback()
    raise
   stats["papers"]+=1
  stats.update({"legacy_R17_unchanged_count":c.execute("SELECT count(*) FROM r17_measurement").fetchone()[0],
                "legacy_R41_unchanged_count":c.execute("SELECT count(*) FROM r41_observation").fetchone()[0],
                "legacy_R42_unchanged_count":c.execute("SELECT count(*) FROM r42_relation").fetchone()[0],
                "gold":c.execute("SELECT count(*) FROM qualified_training_labels").fetchone()[0],
                "human_scientific_review":c.execute("SELECT count(*) FROM r43_human_review").fetchone()[0]})
  if (stats["legacy_R17_unchanged_count"]!=1175 or stats["legacy_R41_unchanged_count"]!=47
      or stats["legacy_R42_unchanged_count"]!=65 or stats["gold"] or stats["human_scientific_review"]):
   raise RuntimeError("R43_LEGACY_OR_GOLD_BOUNDS_FAILED")
  if c.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or c.execute("PRAGMA foreign_key_check").fetchall():
   raise RuntimeError("R43_SQLITE_INTEGRITY_FAILED")
  # No model-readiness or human SCI approval is permitted by this prototype.
  stats["status"]="SHADOW_PROVENANCE_AND_ARM_SUGGESTIONS_UNREVIEWED"
  stats["precision"]=stats["recall"]=stats["f1"]=None
  return dict(stats)

def main(argv=None):
 import argparse
 parser=argparse.ArgumentParser()
 parser.add_argument("--shadow-db",required=True,type=Path)
 parser.add_argument("--sources",required=True,type=Path)
 parser.add_argument("--receipt",required=True,type=Path)
 options=parser.parse_args(argv)
 if options.receipt.exists():raise RuntimeError("R43_RECEIPT_ALREADY_EXISTS")
 result=analyze(options.shadow_db,options.sources)
 options.receipt.parent.mkdir(parents=True,exist_ok=True)
 options.receipt.write_text(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True)+"\n")
 print(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()

"""R44 Scientific Review Engine: blind dual models -> source check -> hold/Silver.

No calls without explicit flags. Reviewer answers never go into production,
R43 gold or training tables; sidecar is a new SQLite with SQL-level zero-Gold.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import sqlite3
import time
from .r44_packets import verify_packet
from .r44_arbiter import validate,compare
from .r44_models import Provider,call,SYSTEM

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE r44_review_run (
 run_id TEXT PRIMARY KEY,paper TEXT NOT NULL,packet_sha TEXT NOT NULL,
 source_sha TEXT NOT NULL,model_a TEXT NOT NULL,model_b TEXT NOT NULL,
 mode TEXT NOT NULL,provenance_state TEXT NOT NULL,
 Gold INTEGER NOT NULL DEFAULT 0 CHECK(Gold=0),
 scientifically_approved INTEGER NOT NULL DEFAULT 0 CHECK(scientifically_approved=0)
);
CREATE TABLE r44_model_output (
 run_id TEXT NOT NULL REFERENCES r44_review_run(run_id),reviewer TEXT NOT NULL,
 model TEXT NOT NULL,provider TEXT NOT NULL,answer_sha TEXT NOT NULL,
 source_validation_status TEXT NOT NULL,
 answer_json TEXT NOT NULL,
 Gold INTEGER NOT NULL DEFAULT 0 CHECK(Gold=0),
 PRIMARY KEY(run_id,reviewer));
CREATE TABLE r44_candidate_comparison (
 comparison_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES r44_review_run(run_id),
 status TEXT NOT NULL,reason_json TEXT NOT NULL,
 validated_source_sha TEXT NOT NULL,
 scientific_truth_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(scientific_truth_confirmed=0),
 training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0)
);
"""
def sha(body:bytes):return hashlib.sha256(body).hexdigest()
def j(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def write_new(path:Path,data):
 raw=(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode()
 if path.exists():raise RuntimeError("R44_OUTPUT_ALREADY_EXISTS")
 path.write_bytes(raw)
 return sha(raw)

def safe_output(folder:Path,packet_path:Path):
 folder=folder.resolve()
 if folder.exists():raise RuntimeError("R44_OUTPUT_DIRECTORY_EXISTS")
 # Deliberately refuse any output inside the source authors or frozen
 # production locations, even if invoked through a symlink.
 if folder==packet_path.resolve().parent or folder in packet_path.resolve().parents:
  raise RuntimeError("R44_OUTPUT_MUST_BE_NEW_INDEPENDENT_DIRECTORY")
 bad=("phase1b_r42/production_home","phase1b_r43/full_replay_home",
      "phase1b_r43/shadow_home","state/current")
 if any(x in str(folder) for x in bad):
  raise RuntimeError("R44_PROTECTED_PRODUCTION_OR_HISTORICAL_HOME")
 return folder

def run(packet_path:Path,expected_packet_sha:str,output:Path,a:Provider,b:Provider,
        *,fixture_a:Path|None=None,fixture_b:Path|None=None,
        allow_network:bool=False,allow_billed_remote:bool=False,
        allow_source_disclosure:bool=False)->dict:
 packet_path=packet_path.resolve()
 packet=verify_packet(packet_path,expected_packet_sha)
 out=safe_output(output,packet_path)
 if a.name==b.name and a.model==b.model:
  raise RuntimeError("R44_TWO_IDENTICAL_MODELS_NOT_INDEPENDENT")
 if (fixture_a is None)!=(fixture_b is None):
  raise RuntimeError("R44_FIXTURE_MIXED_WITH_REAL_CALL_NOT_ALLOWED")
 offline=fixture_a is not None
 if offline and (allow_network or allow_billed_remote or allow_source_disclosure):
  raise RuntimeError("R44_FIXTURE_MUST_BE_STRICT_NO_NETWORK")
 if not offline and a.name==b.name:
  raise RuntimeError("R44_REAL_INDEPENDENCE_REQUIRES_DISTINCT_PROVIDERS")
 if not offline:
  if not allow_network or not allow_source_disclosure:
   raise RuntimeError("R44_BOTH_EXPLICIT_APPROVAL_FLAGS_REQUIRED")
  if (a.name!="local" or b.name!="local") and not allow_billed_remote:
   raise RuntimeError("R44_REMOTE_PAID_OPERATION_NOT_APPROVED")
 # No model sees the other's response, R42/R43 predictions, or hidden labels.
 if offline:
  first={"answer":json.loads(fixture_a.read_text()),"provider":a.name,
         "model":a.model,"usage":{},"is_fixture":True}
  second={"answer":json.loads(fixture_b.read_text()),"provider":b.name,
          "model":b.model,"usage":{},"is_fixture":True}
 else:
  first=call(a,packet,allow_network=allow_network,
      allow_billed_remote=allow_billed_remote,
      allow_source_disclosure=allow_source_disclosure)
  second=call(b,packet,allow_network=allow_network,
      allow_billed_remote=allow_billed_remote,
      allow_source_disclosure=allow_source_disclosure)
 checks=[];failures=[]
 for review in (first,second):
  try:
   checks.append(validate(packet,review["answer"]))
   failures.append(None)
  except (ValueError,KeyError,TypeError) as exc:
   checks.append(None)
   # Fail-closed: output is retained privately for debugging, not Silver.
   failures.append(type(exc).__name__+":"+str(exc)[:160])
 if not all(x is not None for x in checks):
  audit={"schema":"asterloom.r44.dual-model-science-arbitration/1",
   "status":"AI_SOURCE_SCHEMA_VALIDATION_HOLD","paper":packet["paper"],
   "validation_failures":failures,"comparisons":[],
   "states":{"CITATION_OR_SCHEMA_HOLD":1},
   "scientific_precision":None,"scientific_recall":None,"training_gold":0,
   "model_ready":False}
 else:
  audit=compare(packet,checks[0],checks[1],real_independent_models=not offline)
 mode="OFFLINE_FIXTURE_PROTOCOL_NOT_A_REAL_LLM" if offline else "ACTUAL_MODEL_CALL_NOT_HUMAN_REFERENCE"
 run_id=sha((expected_packet_sha+"|"+a.name+"|"+a.model+"|"+b.name+"|"+b.model+"|"+mode+"|"+j(first["answer"])+"|"+j(second["answer"])).encode())[:32]
 result={"schema":"asterloom.r44.ai-review-run/1","run_id":run_id,
    "paper":packet["paper"],"author_sha256":packet["source_sha256"],
    "packet_sha256":expected_packet_sha,"mode":mode,
    "model_a":{"provider":a.name,"model":a.model,"usage":first.get("usage",{})},
    "model_b":{"provider":b.name,"model":b.model,"usage":second.get("usage",{})},
    "system_prompt_sha256":sha(SYSTEM.encode()),
    "validation_failures":failures,
    "summary_states":audit["states"],
    "sci_accuracy_benchmark":None,"precision":None,"recall":None,"f1":None,
    "human_gold":0,"model_ready":False,
    "external_source_disclosure_approved":bool(allow_source_disclosure),
    "billed_remote_approved":bool(allow_billed_remote),
    "was_external_network_used":bool(not offline and (a.name!="local" or b.name!="local")),
    "run_is_not_evidence_of_scientific_truth":True}
 out.mkdir(parents=True)
 write_new(out/"REVIEWER_A_RAW.json",first["answer"])
 write_new(out/"REVIEWER_B_RAW.json",second["answer"])
 write_new(out/"ARBITRATION.json",audit)
 write_new(out/"RECEIPT.json",result)
 db=sqlite3.connect(out/"science_review.sqlite3")
 try:
  db.executescript(SCHEMA)
  db.execute("""INSERT INTO r44_review_run
      (run_id,paper,packet_sha,source_sha,model_a,model_b,mode,provenance_state)
      VALUES (?,?,?,?,?,?,?,?)""",(run_id,packet["paper"],expected_packet_sha,
        packet["source_sha256"],a.name+":"+a.model,b.name+":"+b.model,mode,audit["status"]))
  for who,raw,checked in zip(("A","B"),(first,second),checks):
   db.execute("""INSERT INTO r44_model_output
      (run_id,reviewer,model,provider,answer_sha,source_validation_status,answer_json)
      VALUES (?,?,?,?,?,?,?)""",(run_id,who,raw["model"],raw["provider"],
         sha(j(raw["answer"]).encode()),"AUTHOR_QUOTE_VERIFIED" if checked else "HOLD",
         j(raw["answer"])))
  for idx,relation in enumerate(audit.get("comparisons",[])):
   db.execute("""INSERT INTO r44_candidate_comparison
     (comparison_id,run_id,status,reason_json,validated_source_sha)
     VALUES (?,?,?,?,?)""",(run_id+":"+str(idx),run_id,relation["status"],
         j(relation.get("reasons",[])),packet["source_sha256"]))
  db.commit()
  if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchall():
   raise RuntimeError("R44_REVIEW_SIDECAR_INTEGRITY_FAILED")
 finally:db.close()
 cards=["# R44 AI Scientific Review｜"+packet["paper"],
        "状态："+audit["status"],
        "模式："+mode+"；这不是人工Gold或科研准确率证明。",
        "作者原文SHA："+packet["source_sha256"],
        "原文片段均保留在独立作者证据包中；模型输出经字段级原句/locator核验。"]
 for num,line in enumerate(audit.get("comparisons",[]),1):
  cards.append("## 关系审议 "+str(num))
  cards.append("状态："+line["status"]+"；分歧字段："+", ".join(line.get("field_disagreements",[])))
  cards.append("HOLD："+", ".join(line.get("reasons",[])))
  for source in line.get("a_evidence",[])[:12]:
   cards.append("来源："+source["locator"]+"；原文："+source["source_literal"])
 (out/"CHINESE_REVIEW_SUMMARY.md").write_text("\n\n".join(cards)+"\n",encoding="utf-8")
 return {"status":audit["status"],"paper":packet["paper"],"run_mode":mode,
   "out":str(out),"states":audit["states"],"Gold":0,
   "scientific_precision":None,"scientific_recall":None}

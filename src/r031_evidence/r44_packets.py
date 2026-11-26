"""R44 independent scientific source packet builder: author bytes, not model predictions.

Development manifest only. No hidden test access, no R42/R43 candidate answers in
model-visible packet. An extracted paragraph is source text, not verified ground truth.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

FORMAT="asterloom.r44.author-evidence-packet/1"
RULE="r44-source-packet/1"
SIGNAL=re.compile(r"\b(?:ATP|adenosine\s+triphosphate|bioenergetic|oxidative\s+phosphorylation|Seahorse|OCR|ECAR|glycolysis|mitochondrial\s+respiration)\b",re.I)
MODEL=re.compile(r"\b(?:macrophage|RAW\s*264|THP[- ]?1|BMDM|LPS|P301L)\b",re.I)
CONTROL=re.compile(r"\b(?:compared|versus|control|vehicle|untreated|treated|stimulation|increase|decrease|rescue)\b",re.I)

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def stored_new(path:Path,content:bytes):
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists():raise RuntimeError("R44_IMMUTABLE_PACKET_ALREADY_EXISTS:"+str(path))
 path.write_bytes(content)

def normalize(text:str)->str:return " ".join((text or "").split())

def tree_chunks(raw:bytes):
 """Author original XML, with replayable tree-index locator and section scope."""
 root=ET.fromstring(raw)
 if root.tag.rsplit("}",1)[-1]!="article":raise ValueError("R44_NOT_JATS_ARTICLE")
 for node in root.iter():
  if "}" in node.tag:node.tag=node.tag.rsplit("}",1)[-1]
 chunks=[]
 def walk(node,path,section="",in_excluded=False):
  counts=Counter()
  for child in node:
   counts[child.tag]+=1
   loc=path+"/"+child.tag+"["+str(counts[child.tag])+"]"
   if child.tag in ("ref-list","back","front","sub-article","fn-group"):
    continue
   newsection=section
   if child.tag=="sec":
    title=normalize(" ".join(child.find("title").itertext())) if child.find("title") is not None else ""
    if title:newsection=title
   if child.tag=="p" and not in_excluded:
    text=normalize("".join(child.itertext()))
    if len(text)>=25:
     chunks.append({"kind":"PARAGRAPH","locator":loc,"section":newsection,"text":text})
   elif child.tag in ("caption","table-wrap"):
    # Treat caption as one panel-level source; don't separately emit its
    # descendant paragraphs as if they were independent experiment results.
    text=normalize("".join(child.itertext()))
    if len(text)>=25:
     chunk={"kind":"FIGURE_OR_TABLE_CAPTION" if child.tag=="caption" else "AUTHOR_TABLE",
            "locator":loc,"section":newsection,"text":text}
     if child.tag=="table-wrap":
      # Keep the cell boundary: flattening prints a row dose and its first cell
      # as "0.00130.1", which no exact-literal check can split afterwards.  The
      # chunk id stays sha(paper|source|locator|text), so this is additive.
      cells=[normalize("".join(cell.itertext())) for cell in child.iter()
             if cell.tag in ("td","th")]
      cells=[c for c in cells if c]
      if cells:chunk["cells"]=cells
     chunks.append(chunk)
   if child.tag in ("caption","table-wrap"):
    continue
   walk(child,loc,newsection,in_excluded)
 walk(root,"/article")
 # Multiple XML extraction paths sometimes reuse the same literal; dedupe by
 # locator, not by text, retaining distinct Figure panels.
 return chunks

def kind_score(row):
 s=row["text"];section=row["section"].lower()
 signal=len(SIGNAL.findall(s))
 if not signal:return -1
 score=signal*12 + min(len(MODEL.findall(s)),5)*6 + min(len(CONTROL.findall(s)),5)*3
 if "result" in section or "finding" in section:score+=18
 if "method" in section or "materials" in section:score+=4
 if row["kind"]=="FIGURE_OR_TABLE_CAPTION":score+=13
 if row["kind"]=="AUTHOR_TABLE":score+=5
 return score

def create(entry:dict,*,max_chars:int=17000,max_chunks:int=36)->dict:
 if entry.get("split") not in ("DEVELOPMENT_TRAIN","DEVELOPMENT"):
  raise ValueError("R44_HIDDEN_OR_UNTRUSTED_SPLIT_REJECTED")
 if entry.get("annotation_state")!="UNLABELED":
  raise ValueError("R44_DEV_INPUT_NOT_FROZEN_UNLABELED")
 paper=entry["paper"];source=Path(entry["source_path"]).resolve()
 raw=source.read_bytes();actual=sha(raw)
 if actual!=entry["source_sha256"]:raise ValueError("R44_AUTHOR_XML_SHA_DRIFT:"+paper)
 allchunks=tree_chunks(raw)
 scored=[(kind_score(x),x) for x in allchunks]
 # Include the most relevant Results, Figure captions and Methods across the
 # entire original paper; no R42 relation answers are consulted here.
 scored=[(score,x) for score,x in scored if score>=0]
 scored.sort(key=lambda sx:(-sx[0],sx[1]["locator"]))
 chosen=[];total=0;truncated=0
 for score,row in scored:
  if len(chosen)>=max_chunks:truncated+=1;continue
  t=row["text"]
  if len(t)>2200:
   # A truncated passage is not suitable for exact full evidence binding.
   # Drop it rather than pretending a partial sentence is complete.
   truncated+=1;continue
  if total+len(t)>max_chars:
   truncated+=1;continue
  record=dict(row)
  record["chunk_id"]="r44:"+sha((paper+"|"+actual+"|"+row["locator"]+"|"+t).encode())[:20]
  record["author_sha256"]=actual
  chosen.append(record);total+=len(t)
 if not chosen:
  raise ValueError("R44_NO_RELEVANT_AUTHOR_ATP_SOURCE_CHUNKS:"+paper)
 chosen.sort(key=lambda x:x["locator"])
 packed={"schema":FORMAT,"paper":paper,"paper_title":entry["title"],
  "source_sha256":actual,"source_license_literal":entry.get("source_license_literal"),
  "source_kind":"AUTHOR_JATS_ORIGINAL_BYTES_VERIFIED",
  "selection_scope":"DEVELOPMENT_ONLY_NOT_HIDDEN",
  "packet_rule":RULE,"source_total_chunks":len(allchunks),
  "keyword_candidate_chunks":len(scored),"included_chunks":len(chosen),
  "omitted_relevant_chunks":truncated,"context_truncated":bool(truncated),
  "review_instructions":"Each non-null field must cite exact source quote and chunk_id. Absence is UNKNOWN, never invent. No model Gold.",
  "chunks":chosen,
  "scientific_state":"SOURCE_ONLY_NOT_GOLD"}
 packed["packet_content_sha256"]=sha(canonical({k:v for k,v in packed.items() if k!="packet_content_sha256"}).encode())
 return packed

def freeze(manifest_file:Path,expected_manifest_sha:str,out:Path,papers:list[str],limit:int=3):
 if sha(manifest_file.read_bytes())!=expected_manifest_sha:
  raise ValueError("R44_FROZEN_DEVELOPMENT_MANIFEST_SHA_REQUIRED")
 entries=[json.loads(s) for s in manifest_file.read_text().splitlines() if s]
 if not entries or any(x.get("split") not in ("DEVELOPMENT","DEVELOPMENT_TRAIN") for x in entries):
  raise ValueError("R44_TEST_SPLIT_OR_BAD_MANIFEST")
 available={x["paper"]:x for x in entries}
 if len(available)!=len(entries):raise ValueError("R44_DUPLICATE_SOURCE_ID")
 if not 1<=limit<=5:raise ValueError("R44_PER_RUN_LIMIT_1_TO_5")
 selection=papers or sorted(available)[:limit]
 if len(selection)>limit or len(set(selection))!=len(selection) or any(x not in available for x in selection):
  raise ValueError("R44_OUTSIDE_DEV_OR_LIMIT")
 if out.exists():raise RuntimeError("R44_FROZEN_PACKET_OUTPUT_EXISTS")
 packets=[create(available[p]) for p in selection]
 # Atomic directory creation after all bytes/source SHA checks pass.
 out.mkdir(parents=True)
 rows=[]
 for packet in packets:
  path=out/(packet["paper"]+".author_packet.json")
  raw=(json.dumps(packet,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode()
  stored_new(path,raw)
  rows.append({"paper":packet["paper"],"packet_file":str(path),
     "packet_sha256":sha(raw),"original_author_sha256":packet["source_sha256"],
     "included_chunks":packet["included_chunks"],"omitted_relevant_chunks":packet["omitted_relevant_chunks"],
     "split":"DEVELOPMENT_ONLY"})
 summary={"schema":"asterloom.r44.packet-manifest/1","status":"BLIND_AUTHOR_SOURCE_PACKET_FROZEN",
   "developer_manifest_sha256":expected_manifest_sha,"source_paper_count":len(rows),
   "packets":rows,"human_Gold":0,"model_proposals":0,"hidden_test_papers_accessed":0}
 stored_new(out/"MANIFEST.json",(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode())
 return summary

def verify_packet(path:Path,expected_sha:str,*,allow_frozen_holdout:bool=False)->dict:
 raw=path.read_bytes()
 if sha(raw)!=expected_sha:raise ValueError("R44_PACKET_SHA_MISMATCH")
 packet=json.loads(raw)
 allowed_scopes={'DEVELOPMENT_ONLY_NOT_HIDDEN'}
 if allow_frozen_holdout:allowed_scopes.add('FROZEN_HOLDOUT_NO_GOLD')
 if packet.get('schema')!=FORMAT or packet.get('selection_scope') not in allowed_scopes:
  raise ValueError('R44_ONLY_DEV_OR_EXPLICIT_FROZEN_HOLDOUT_PACKET')
 checksum=packet["packet_content_sha256"]
 if sha(canonical({k:v for k,v in packet.items() if k!="packet_content_sha256"}).encode())!=checksum:
  raise ValueError("R44_PACKET_CONTENT_DRIFT")
 ids=[x["chunk_id"] for x in packet["chunks"]]
 if not ids or len(ids)!=len(set(ids)):raise ValueError("R44_REPEATED_CHUNK_ID")
 return packet

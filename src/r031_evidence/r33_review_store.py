"""Append-only evaluation annotation history, never science or Gold writes."""
from pathlib import Path
import datetime,fcntl,hashlib,json,os,uuid
from .r33_evaluation import load_packet,FIELDS,DECISIONS,ERRORS
from .r30_ledger import canonical_json,digest

def history(directory):
 p=Path(directory)/'events.jsonl'
 if not p.exists():return []
 raw=p.read_bytes()
 if raw and not raw.endswith(b'\n'):raise ValueError('R33_ANNOTATION_TORN_APPEND_HOLD_DO_NOT_TRUNCATE')
 rows=[];previous='0'*64
 for line in raw.splitlines():
  r=json.loads(line);h=r.pop('event_sha256')
  if r['previous_sha256']!=previous or digest(canonical_json(r))!=h:raise ValueError('R33_ANNOTATION_HISTORY_SHA_CHAIN_BROKEN')
  r['event_sha256']=h;rows.append(r);previous=h
 return rows

def annotate(packet_file,packet_sha,directory,entry,test_mode=False,candidates_exposed=False):
 packet=load_packet(packet_file,packet_sha);cases={x['case_id']:x for x in packet['cases']};cid=entry.get('case_id');field=entry.get('field')
 if cid not in cases or field not in FIELDS or entry.get('decision') not in DECISIONS:raise ValueError('R33_INVALID_ANNOTATION_CASE_FIELD_DECISION')
 person=entry.get('reviewer_identity');person=person.strip() if isinstance(person,str) else person
 if not isinstance(person,str) or not 2<=len(person.strip())<=100 or any(ord(c)<32 for c in person):raise ValueError('R33_EXPLICIT_REVIEWER_IDENTITY_REQUIRED')
 if entry.get('error_code') not in (None,'',*ERRORS):raise ValueError('R33_ERROR_TAXONOMY_INVALID')
 case=cases[cid];source_sha=entry.get('source_sha256') or case['source_sha256'];asset=packet['source_assets'].get(source_sha)
 if not asset or asset['paper_key']!=case['paper_key']:raise ValueError('R33_ANNOTATION_SOURCE_NOT_BOUND_TO_PAPER')
 source=Path(asset['path'])
 if not source.is_file() or digest(source.read_bytes())!=source_sha:raise ValueError('R33_ORIGINAL_SOURCE_SHA_DRIFT')
 folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
 with (folder/'append.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  rows=history(folder)
  if any(r['packet_sha256']!=packet_sha for r in rows):raise ValueError('R33_ANNOTATION_STORE_DIFFERENT_CORPUS')
  previous=[r for r in rows if r['case_id']==cid and r['field']==field and r['reviewer_identity']==person]
  revision=len(previous)
  if entry.get('expected_revision')!=revision:raise ValueError('R33_ANNOTATION_STALE_REVISION')
  r=dict(schema='asterloom.evaluation-annotation-event/1',event_id=uuid.uuid4().hex,packet_sha256=packet_sha,case_id=cid,field=field,revision=revision+1,decision=entry['decision'],proposed_value=entry.get('proposed_value'),reason=str(entry.get('reason') or '')[:4000],error_code=entry.get('error_code') or None,reviewer_identity=person.strip(),actor_kind='AUTOMATED_TEST' if test_mode else 'HUMAN_UI_ASSERTION_NOT_IDENTITY_AUTHENTICATED',source_sha256=source_sha,source_locator=str(entry.get('source_locator') or case['source_locator'])[:2000],at=datetime.datetime.now(datetime.timezone.utc).isoformat(),previous_sha256=rows[-1]['event_sha256'] if rows else '0'*64,candidate_visibility='EXPOSED' if candidates_exposed else 'BLIND_CANDIDATES_HIDDEN',scientific_approval=False,evaluation_reference_release=False,training_approved=False,Gold=0)
  r['event_sha256']=digest(canonical_json(r))
  with (folder/'events.jsonl').open('ab') as f:f.write((canonical_json(r)+'\n').encode());f.flush();os.fsync(f.fileno())
  return r

def draft_reference(packet_file,packet_sha,directory):
 packet=load_packet(packet_file,packet_sha);rows=[r for r in history(directory) if r['actor_kind']!='AUTOMATED_TEST'];latest={}
 for r in rows:latest[(r['case_id'],r['field'],r['reviewer_identity'])]=r
 cases=[]
 for case in packet['cases']:
  fields={}
  for field in FIELDS:
   observations=[r for (cid,k,person),r in latest.items() if cid==case['case_id'] and k==field]
   fields[field]=dict(state='UNREVIEWED',value=None,proposed_annotations=observations)
  cases.append(dict(case_id=case['case_id'],fields=fields))
 return dict(schema='asterloom.human-evaluation-reference/1',packet_sha256=packet_sha,reference_status='HUMAN_REVIEW_PENDING',annotators=[],human_owner_release=None,cases=cases,reference_only_not_training_Gold=True,excluded_automated_test_events=sum(r['actor_kind']=='AUTOMATED_TEST' for r in history(directory)),Gold=0)

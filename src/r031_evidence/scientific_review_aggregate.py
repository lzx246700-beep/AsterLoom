"""Combine versioned review exports without modifying any source database."""
from __future__ import annotations
import csv,json,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from . import r44_arbiter, scientific_review_export
from .scientific_review_export import sha


def aggregate(pipeline_dirs,registry,registry_sha,out):
 registry=Path(registry).resolve();out=Path(out).resolve();dirs=[Path(p).resolve() for p in pipeline_dirs]
 if not dirs or len(dirs)!=len(set(dirs)):raise ValueError('AGGREGATE_DISTINCT_INPUTS_REQUIRED')
 if out.exists() or out.is_relative_to(registry.parent) or any(out.is_relative_to(p) for p in dirs):raise ValueError('AGGREGATE_NEW_ISOLATED_OUTPUT_REQUIRED')
 if sha(registry)!=registry_sha:raise ValueError('AGGREGATE_REGISTRY_SHA_MISMATCH')
 with closing(sqlite3.connect(registry.as_uri()+'?mode=ro',uri=True)) as db:
  schemas={t:db.execute('select sql from sqlite_master where type="table" and name=?',(t,)).fetchone()[0] for t in ('molecules','papers','observations')}
 bundle=[];allrows={'molecules':[],'papers':[],'observations':[]};seen_papers=set();source_count=native_count=numeric_count=complete_count=0
 for directory in dirs:
  receipt=json.loads((directory/'PIPELINE_RECEIPT.json').read_text());manifest=json.loads((directory/'three_table/MANIFEST.json').read_text());rep=json.loads((directory/'review_bundle/REPLAY_RECEIPT.json').read_text());packets=directory/'review_bundle/REVIEW_PACKETS.jsonl';source_db=directory/'three_table/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
  if receipt.get('status')!='REVIEW_PACKETS_READY_SCIENCE_HOLD' or sha(source_db)!=receipt['database_sha256'] or rep.get('existing_database_sha256')!=receipt['database_sha256'] or rep.get('reproduced_database_sha256')!=receipt['database_sha256'] or manifest.get('files',{}).get('ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')!=receipt['database_sha256'] or sha(packets)!=rep['evidence_packets_sha256']:
   raise ValueError('AGGREGATE_INPUT_REPLAY_OR_SHA_MISMATCH')
  if manifest.get('validation_fingerprint')!=scientific_review_export.validation_fingerprint():raise ValueError('AGGREGATE_REVALIDATION_REQUIRED')
  if any(receipt.get(k)!=0 for k in ('independent_gold','training_approved')):raise ValueError('AGGREGATE_APPROVAL_FORBIDDEN')
  packet_rows=[json.loads(x) for x in packets.read_text().splitlines() if x.strip()]
  for x in packet_rows:
   if sha(x['author_source_path'])!=x['author_source_sha256'] or sha(x['model_output_path'])!=x['model_output_sha256'] or sha(x['rule_packet_path'])!=x['rule_packet_sha256']:
    raise ValueError('AGGREGATE_EVIDENCE_SHA_MISMATCH')
  source_observations=[]
  with closing(sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True)) as src:
   src.row_factory=sqlite3.Row
   if src.execute('pragma integrity_check').fetchone()[0]!='ok' or src.execute('pragma foreign_key_check').fetchall():raise ValueError('AGGREGATE_SOURCE_INTEGRITY_FAILURE')
   for t in schemas:
    if src.execute('select sql from sqlite_master where type="table" and name=?',(t,)).fetchone()[0]!=schemas[t]:raise ValueError('AGGREGATE_SCHEMA_MISMATCH')
    rows=[dict(x) for x in src.execute('select * from '+t+' order by 1')]
    if t=='papers':
     for r in rows:
      if r['paper_id'] in seen_papers:raise ValueError('AGGREGATE_DUPLICATE_PAPER')
      seen_papers.add(r['paper_id'])
    if t=='observations':source_observations=rows
    if t=='observations' and any(r['review_tier']!='Review-B' or r['independent_gold']!=0 or r['training_approved']!=0 or r['train_eligible']!=0 for r in rows):raise ValueError('AGGREGATE_APPROVAL_FORBIDDEN')
    allrows[t].extend(rows)
  if len(packet_rows)!=receipt['review_b_records'] or {x['observation_id'] for x in packet_rows}!={x['observation_id'] for x in source_observations}:
   raise ValueError('AGGREGATE_PACKET_COUNT_OR_ID_MISMATCH')
  for item in packet_rows:
   matching=next(r for r in source_observations if r['observation_id']==item['observation_id'])
   locator=json.loads(matching['source_locator'])
   if item['paper_id']!=matching['paper_id'] or item['author_source_sha256']!=matching['author_original_sha256'] or item['model_output_sha256']!=locator['model_output_sha256']:
    raise ValueError('AGGREGATE_PACKET_BINDING_MISMATCH')
  bundle.extend(packet_rows);source_count+=manifest['source_level_candidates'];native_count+=manifest['native_numeric_pairs'];numeric_count+=manifest['source_supported_numeric_relation_candidates'];complete_count+=manifest['core_xy_fields_complete_unreviewed']
 out.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.review-aggregate-',dir=out.parent) as scratch:
  tmp=Path(scratch);database=tmp/'ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
  with closing(sqlite3.connect(database)) as db:
   db.execute('pragma foreign_keys=ON')
   for t in ('molecules','papers','observations'):db.execute(schemas[t])
   for t in ('molecules','papers','observations'):
    for row in allrows[t]:
     cols=list(row);db.execute('insert into '+t+'('+','.join(cols)+') values ('+','.join('?' for _ in cols)+')',[row[k] for k in cols])
   db.commit()
   if db.execute('pragma integrity_check').fetchone()[0]!='ok' or db.execute('pragma foreign_key_check').fetchall():raise ValueError('AGGREGATE_OUTPUT_INTEGRITY_FAILURE')
   for t in ('molecules','papers','observations'):
    cursor=db.execute('select * from '+t+' order by 1');cols=[c[0] for c in cursor.description];rows=cursor.fetchall()
    with (tmp/(t+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:w=csv.writer(f);w.writerow(cols);w.writerows(rows)
    (tmp/(t+'.jsonl')).write_text(''.join(json.dumps(dict(zip(cols,r)),ensure_ascii=False)+'\n' for r in rows))
  (tmp/'REVIEW_PACKETS.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in bundle))
  manifest={'schema':'asterloom.scientific-review-aggregate/1','status':'DERIVED_REVIEW_B_NOT_AUTHORITATIVE',
    'registry_sha256':registry_sha,'inputs':[{'directory':str(p),'pipeline_receipt_sha256':sha(p/'PIPELINE_RECEIPT.json'),'database_sha256':sha(p/'three_table/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'),'replay_sha256':sha(p/'review_bundle/REPLAY_RECEIPT.json')} for p in dirs],
    'paper_count':len(seen_papers),'source_level_candidates':source_count,'review_b_records':len(allrows['observations']),
    'source_supported_numeric_relation_candidates':numeric_count,'native_numeric_pairs':native_count,
    'core_xy_fields_complete_unreviewed':complete_count,'independent_gold':0,'training_approved':0,
    'evidence_packets':len(bundle),'files':{p.name:sha(p) for p in tmp.iterdir()}}
  if sha(registry)!=registry_sha:raise ValueError('AGGREGATE_REGISTRY_CHANGED')
  (tmp/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');tmp.rename(out)
 return manifest

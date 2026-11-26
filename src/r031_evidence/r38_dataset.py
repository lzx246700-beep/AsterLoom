"""Versioned read-only task dataset derived from the single canonical Evidence Core.
Exports are snapshots, not a second authoritative scientific database. No Gold or
review import here; reviewed view remains empty until separately authorized SCI work.
"""
from pathlib import Path
from contextlib import closing
import csv,io,json,os,sqlite3,time,uuid,re
from . import r30_ledger as L
from .r23_profiles import SourceProjector,load,NAMES
from .r36_pilot import task_papers,audit_records,verify_proposal
from .r33_evaluation import write_new

FLAT=('row_id','row_kind','paper_key','doi','native_endpoint','endpoint','compound','model','dose','exposure_time','method_locator','normalization','unit','author_literal','author_numeric','source_sha256','source_locator','core_decision','review_state','hold_reasons','record_json')
VIEWS=('source_only','atp_content_candidates','profile_complete_candidates','reviewed','evidence_bound_candidates','macrophage_core_candidates','relative_atp_candidates','author_arm_relations')

def _flat_r39(r,original=None):
 f=lambda x:r['fields'].get(x,{}).get('value')
 out=dict(original or {},row_id=r['native_measurement_id'] or r['candidate_id'],row_kind='R39_BOUND_NATIVE_R17' if r['native_measurement_id'] else 'R39_AUTHOR_NARRATIVE_CANDIDATE',paper_key=r['paper_key'],doi=(original or {}).get('doi'),native_endpoint=(original or {}).get('native_endpoint'),endpoint=r['endpoint'],compound=f('chemical.tested_form_literal'),model=f('biological.model_literal'),dose=f('conditions.dose_literal'),exposure_time=f('conditions.exposure_time_literal'),method_locator=f('assay.method_locator'),normalization=f('assay.normalization_basis'),unit=r['unit'],author_literal=r['author_literal'],author_numeric=r['author_numeric'],source_sha256=r['author_sha256'],source_locator=r['source_locator'],core_decision=(original or {}).get('core_decision','SOURCE_ONLY_HOLD'),review_state='HUMAN_REVIEW_PENDING',hold_reasons=json.dumps(r['hold_reasons'],ensure_ascii=False))
 rec=json.loads(original['record_json']) if original else {};rec['r39_evidence']=r;out['record_json']=json.dumps(rec,ensure_ascii=False);return out

def create_task(home,profile,max_papers=20,max_requests=40,compound_terms='quercetin OR resveratrol OR curcumin'):
 if type(max_papers) is not int or type(max_requests) is not int or profile not in NAMES or not 20<=max_papers<=50 or not 1<=max_requests<=60 or len(compound_terms)>500:raise ValueError('R38_PROFILE_TASK_BOUNDS')
 p=load(profile,home);terms=p.manifest['retrieval_policy'].get('assay_first') or ['covalent polymer','bioactive hydrogel'];query='('+ ' OR '.join('TITLE_ABS:"'+str(t).replace('"','')+'"' for t in terms[:5])+') AND ('+compound_terms+') AND OPEN_ACCESS:Y AND NOT PUB_TYPE:review'
 folder=Path(home)/'tasks/r38';folder.mkdir(parents=True,exist_ok=True);dest=folder/(uuid.uuid4().hex+'.json');task=dict(schema='asterloom.profile-task/1',profile=profile,profile_version=p.version,max_papers=max_papers,max_requests=max_requests,max_bytes=80000000,max_parallel=1,max_wall_seconds=900,query_preview=query,status='DRAFT_NOT_EXECUTED',network_started=False,automatic_Gold=False,created_unix=time.time());write_new(dest,task);return dict(task,path=str(dest),sha256=L.digest(dest.read_bytes()))

def _flat_core(a):
 r=a['record'];o=r['observation'];endpoint=o['endpoint'];holds=a['hold_reasons']
 if 'SOURCE_COORDINATE_IS_TIME_NOT_ATP_Y' in holds:endpoint='SOURCE_COORDINATE_NOT_MEASUREMENT'
 elif 'AUTHOR_RELATIVE_PERCENT_NOT_ABSOLUTE_ATP_CONTENT' in holds:endpoint='RELATIVE_PERCENT_NOT_ABSOLUTE_ATP_CONTENT'
 return dict(row_id=o['native_measurement_id'],row_kind='CANONICAL_R17_SOURCE_ONLY',paper_key=r['paper']['paper_key'],doi=r['paper']['doi'],native_endpoint=o['endpoint'],endpoint=endpoint,compound=r['chemical']['tested_form_literal'],model=r['biological']['model_literal'],dose=r['conditions']['dose_literal'],exposure_time=r['conditions']['exposure_time_literal'],method_locator=r['assay']['method_locator'],normalization=r['assay']['normalization_basis'],unit=o['native_unit'],author_literal=o['author_literal'],author_numeric=o['author_numeric'],source_sha256=r['provenance']['author_sha256'],source_locator=r['provenance']['original_locator'],core_decision=r['task']['decision'],review_state='HUMAN_REVIEW_PENDING',hold_reasons=json.dumps(holds,ensure_ascii=False),record_json=json.dumps(r,ensure_ascii=False))

def _flat_proposal(r):
 f=lambda x:r['fields'].get(x,{}).get('value')
 return dict(row_id=r['candidate_id'],row_kind='NARRATIVE_PROPOSAL_NOT_NATIVE_R17',paper_key=r['paper_key'],doi='10.3390/molecules29092266',native_endpoint=None,endpoint=r['endpoint'],compound=f('chemical.tested_form_literal'),model=f('biological.model_literal'),dose=f('conditions.dose_literal'),exposure_time=f('conditions.exposure_time_literal'),method_locator=f('assay.method_locator'),normalization=f('assay.normalization_basis'),unit=f('observation.native_unit'),author_literal=r['author_value_literal'],author_numeric=r['author_numeric'],source_sha256=r['author_sha256'],source_locator=r['source_locator'],core_decision='SOURCE_ONLY_HOLD',review_state='HUMAN_REVIEW_PENDING',hold_reasons=json.dumps(r['hold_reasons'],ensure_ascii=False),record_json=json.dumps(r,ensure_ascii=False))

def build(home,task_file,expected_task_sha,output,version='v001'):
 if not re.fullmatch(r'v[0-9]{3,6}',version):raise ValueError('R38_VERSION_IDENTIFIER_REQUIRED')
 task_path=Path(task_file)
 if L.digest(task_path.read_bytes())!=expected_task_sha:raise ValueError('R38_PINNED_TASK_SHA_REQUIRED')
 plan=json.loads(task_path.read_text());out=Path(output)
 if plan.get('profile')!='small_molecule_atp_content_v1':raise ValueError('R38_PILOT_EXPORT_ATP_CONTENT_ONLY_USE_EXISTING_PROFILE_EXPORT_FOR_OTHERS')
 if out.exists():raise ValueError('R38_IMMUTABLE_DATASET_VERSION_EXISTS')
 before=L.digest(L.db_path(home).read_bytes());papers=task_papers(home,plan);keys=[p['paper_key'] for p in papers if p.get('paper_key')];audits=audit_records(home,keys);rows=[_flat_core(a) for a in audits];assets={};rejected=[]
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  staged=c.execute('select * from r36_source_candidate order by candidate_id') if c.execute("select 1 from sqlite_master where name='r36_source_candidate'").fetchone() else []
  staged=list(staged);latest={}
  for x in staged:
   r=json.loads(x['record_json'])
   if r.get('protocol_version','').startswith('r39.evidence-bound/'):
    key=(r['paper_key'],r.get('native_measurement_id') or r['source_locator'],r['author_literal'],r.get('author_sentence'),r.get('arm_index'))
    if key not in latest or int(r['protocol_version'].rsplit('/',1)[1])>latest[key][0]:latest[key]=(int(r['protocol_version'].rsplit('/',1)[1]),r['candidate_id'])
  for x in staged:
   r=json.loads(x['record_json'])
   if r.get('protocol_version','').startswith('r39.evidence-bound/'):
    key=(r['paper_key'],r.get('native_measurement_id') or r['source_locator'],r['author_literal'],r.get('author_sentence'),r.get('arm_index'))
    if latest[key][1]!=r['candidate_id']:
     rejected.append(dict(candidate_id=r['candidate_id'],reason='SUPERSEDED_APPEND_ONLY_FIELD_PROPOSAL',selected_candidate_id=latest[key][1]));continue
   if r['paper_key'] not in keys:continue
   try:
    if r.get('protocol_version') in ('r39.evidence-bound/1','r39.evidence-bound/2','r39.evidence-bound/3','r39.evidence-bound/4'):
     from .r39_evidence import verify
     verify(home,r);prior=next((x for x in rows if x['row_id']==r['native_measurement_id']),None)
     if prior:rows.remove(prior)
     rows.append(_flat_r39(r,prior));continue
    if r.get('protocol_version')!='r36.source-proposal/2':raise ValueError('SUPERSEDED_PROTOTYPE_FIELD_BINDING')
    verify_proposal(home,r);rows.append(_flat_proposal(r))
   except ValueError as e:rejected.append(dict(candidate_id=x['candidate_id'],reason=str(e)))
  for key in keys:
   for a in c.execute('select * from r16_parsed_asset where paper_key=?',(key,)):
    if not Path(a['original_path']).is_file() or L.digest(Path(a['original_path']).read_bytes())!=a['original_sha']:raise ValueError('R38_REGISTERED_ORIGINAL_SHA_DRIFT')
    assets[a['original_sha']]=dict(paper_key=key,path=a['original_path'],sha256=a['original_sha'],format=a['source_format'],asset_id=a['parsed_asset_id'],IR_path=a['ir_path'],IR_sha256=a['ir_sha'])
  for key in keys:
   for x in c.execute('select x.* from r17_si_attachment x join r16_parsed_asset a on a.parsed_asset_id=x.parent_parsed_asset_id where a.paper_key=?',(key,)):
    path=Path(x['local_original_path']);sha=x['official_original_sha']
    if not path.is_file() or L.digest(path.read_bytes())!=sha:raise ValueError('R38_REGISTERED_SI_SHA_DRIFT')
    if sha in assets and assets[sha]['paper_key']!=key:raise ValueError('R38_SHARED_SOURCE_HASH_PAPER_BINDING_REVIEW_REQUIRED')
    assets.setdefault(sha,dict(paper_key=key,path=str(path),sha256=sha,format=path.suffix.lower(),asset_id=x['link_id'],IR_path=None,IR_sha256=None,source_kind='OFFICIAL_SI_ORIGINAL_NOT_AUTOMATIC_SCIENCE_APPROVAL',permission_state=x['permission_state'],parser_state=x['pipeline_state'],parent_source_sha256=x['parent_author_sha']))
  for a in audits:
   r=a['record'];mid=r['observation']['native_measurement_id'];native=c.execute('select author_sha,original_locator,source_literal,numeric_value from r17_measurement where measurement_id=?',(mid,)).fetchone()
   if tuple(native)!=(r['provenance']['author_sha256'],r['provenance']['original_locator'],r['observation']['author_literal'],r['observation']['author_numeric']) or not r['provenance']['byte_sha_verified'] or not r['provenance']['cell_literal_verified']:raise ValueError('R38_SOURCE_VALUE_PROVENANCE_NOT_VERIFIED')
 out.mkdir(parents=True);db=out/'R031_ATP_CONTENT_PILOT.sqlite3';temp=out/'dataset.building'
 try:
  with closing(sqlite3.connect(temp)) as c:
   columns=','.join(k+(' REAL' if k=='author_numeric' else ' TEXT') for k in FLAT);c.execute('create table dataset_rows('+columns+',primary key(row_id))')
   c.execute('create table review_receipts(row_id TEXT,source_sha256 TEXT,reviewer_a TEXT,signature_a TEXT,reviewer_b TEXT,signature_b TEXT,adjudicator TEXT,release_signature TEXT,decision TEXT,version TEXT)')
   c.execute('create table dataset_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)');c.execute('insert into dataset_metadata values(?,?)',('authority','DERIVED_READ_ONLY_EXPORT_NOT_CANONICAL_DATABASE'));c.execute('insert into dataset_metadata values(?,?)',('training_allowed','false'))
   c.executemany('insert into dataset_rows values('+','.join('?' for _ in FLAT)+')',([r[k] for k in FLAT] for r in rows));c.execute('create view source_only as select * from dataset_rows');c.execute("create view atp_content_candidates as select * from dataset_rows where endpoint='ATP_CONTENT'");c.execute("create view profile_complete_candidates as select * from atp_content_candidates where core_decision='TASK_READY_CANDIDATE'")
   c.execute("create view evidence_bound_candidates as select * from dataset_rows where row_kind like 'R39_%'")
   c.execute("create view macrophage_core_candidates as select * from evidence_bound_candidates where json_extract(record_json,'$.r39_evidence.scientific_core_gate')='PROFILE_COMPLETE_CORE_CANDIDATE'")
   c.execute("""create view relative_atp_candidates as select * from dataset_rows where endpoint='ATP_CONTENT_RELATIVE_AUTHOR_CHANGE' and (json_extract(record_json,'$.r39_evidence.author_relation') is null or compound is not null)""")
   c.execute("""create view author_arm_relations as select row_id,paper_key,endpoint,author_literal,unit,
       json_extract(record_json,'$.r39_evidence.author_relation.arm') as exposure,
       json_extract(record_json,'$.r39_evidence.author_relation.direction') as direction,
       json_extract(record_json,'$.r39_evidence.author_relation.comparator') as comparator_literal,
       source_sha256,source_locator,review_state from dataset_rows
       where json_extract(record_json,'$.r39_evidence.author_relation') is not null""")
   c.execute("create view reviewed as select d.* from dataset_rows d join review_receipts r on r.row_id=d.row_id and r.source_sha256=d.source_sha256 where r.reviewer_a<>r.reviewer_b and length(r.signature_a)>0 and length(r.signature_b)>0 and length(r.release_signature)>0 and r.decision='SCIENTIFIC_REVIEW_APPROVED' and r.version="+"'"+version+"'")
   if c.execute('select count(*) from reviewed').fetchone()[0]!=0:raise ValueError('R38_NO_REAL_SCI_REVIEW_RECEIPTS_PROVIDED')
   c.commit();counts={v:c.execute('select count(*) from '+v).fetchone()[0] for v in VIEWS};assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
  if L.digest(L.db_path(home).read_bytes())!=before:raise ValueError('R38_CANONICAL_SOURCE_CHANGED_DURING_EXPORT')
  os.replace(temp,db);db.chmod(0o444)
 except BaseException:temp.unlink(missing_ok=True);raise
 csvcols=[k for k in FLAT if k!='record_json']
 with (out/'SOURCE_ONLY.csv').open('x',newline='') as f:
  w=csv.DictWriter(f,fieldnames=csvcols,extrasaction='ignore');w.writeheader();w.writerows(rows)
 with (out/'SOURCE_ONLY.jsonl').open('x') as f:
  for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 (out/'REVIEWED.csv').write_text(','.join(csvcols)+'\n')
 write_new(out/'TASK_AND_SOURCE_SCOPE.json',dict(task=plan,papers=papers,version=version));write_new(out/'EVIDENCE_MANIFEST.json',assets);write_new(out/'REJECTED_PROTOTYPE_PROPOSALS.json',rejected)
 (out/'DATA_DICTIONARY.md').write_text('Read-only derived task snapshot. Canonical source: '+str(L.db_path(home))+'\n\nSOURCE_ONLY contains raw values and HOLD reasons, not training labels. atp_content_candidates excludes known time coordinates and relative percent; native_endpoint retains historical taxonomy. author_literal and source SHA/locator are immutable; blank CSV fields mean NULL, never0. profile_complete_candidates means existing Contract field completeness, not natural eligibility or SCI approval. Narrative proposals have their own source kind and cannot qualify as native R17. reviewed contains only matching independent review receipts; currently empty, no receipt import authority is provided by this exporter. Versions cannot overwrite.\n\n'+ '\n'.join('- '+k for k in FLAT)+'\n')
 # Build a review-compatible task packet; it is not the immutable R33 human benchmark.
 cases=[]
 for r in rows:
  ev=json.loads(r['record_json']).get('r39_evidence',{});control=ev.get('control');treatment=r['compound'] if ev.get('role')=='TREATMENT' else None
  cases.append(dict(case_id=r['row_id'],paper_key=r['paper_key'],stratum='ATP_CONTENT_PILOT_SOURCE_ONLY',existing_measurement_id=r['row_id'] if r['row_kind'] in ('CANONICAL_R17_SOURCE_ONLY','R39_BOUND_NATIVE_R17') else None,source_sha256=r['source_sha256'],source_locator=r['source_locator'],source_literal=r['author_literal'],field_candidates=dict(entity=r['compound'],treatment=treatment,control=control['native_measurement_id'] if control else None,model=r['model'],dose=r['dose'],time=r['exposure_time'],method=r['method_locator'],endpoint=r['endpoint'],value=r['author_numeric'],unit=r['unit'],source_locator=r['source_locator']),scientific_status='SOURCE_ONLY_HOLD',training_approved=False))
 packet=dict(schema='asterloom.evaluation-packet/1',paper_count=len(papers),case_count=len(cases),papers=[dict(identity=dict(paper_key=p['paper_key'] or p['requested_identity'])) for p in papers],cases=cases,source_assets=assets,reference_state='ATP_CONTENT_PILOT_SOURCE_ONLY_TASK_NOT_FROZEN_BENCHMARK',Gold=0);psha=write_new(out/'packet.json',packet);write_new(out/'candidate_baseline.json',dict(schema='asterloom.evaluation-predictions/1',packet_sha256=psha,cases=[dict(case_id=c['case_id'],fields=c['field_candidates']) for c in cases],Gold=0));write_new(out/'packet.sha256.json',dict(sha256=psha))
 manifest=dict(schema='asterloom.versioned-task-dataset/1',task_id=plan['task_id'],profile=plan['profile'],version=version,canonical_science_db=str(L.db_path(home)),canonical_db_sha256=before,task_sha256=expected_task_sha,counts=counts,paper_scope=len(papers),originals=len(assets),reviewed_real_receipts=0,training_allowed=False,Gold=0,packet_sha256=psha,files={p.name:dict(sha256=L.digest(p.read_bytes()),bytes=p.stat().st_size) for p in out.iterdir() if p.is_file()});ms=write_new(out/'MANIFEST.json',manifest);return dict(status='READY_FOR_HUMAN_REVIEW_SOURCE_ONLY_PILOT',dataset=str(out.resolve()),manifest_sha256=ms,counts=counts,canonical_database_unchanged=True,Gold=0)

def query(dataset,expected_manifest_sha,view='source_only',paper=None,limit=100):
 if view not in VIEWS or not 1<=limit<=500:raise ValueError('R38_ONLY_NAMED_READ_ONLY_VIEWS')
 folder=Path(dataset);manifest=folder/'MANIFEST.json'
 if L.digest(manifest.read_bytes())!=expected_manifest_sha:raise ValueError('R38_DATASET_MANIFEST_PIN_REQUIRED')
 m=json.loads(manifest.read_text());db=folder/'R031_ATP_CONTENT_PILOT.sqlite3'
 if L.digest(db.read_bytes())!=m['files'][db.name]['sha256']:raise ValueError('R38_DATASET_BYTE_SHA_DRIFT')
 for name in ('TASK_AND_SOURCE_SCOPE.json','EVIDENCE_MANIFEST.json'):
  if L.digest((folder/name).read_bytes())!=m['files'][name]['sha256']:raise ValueError('R38_DATASET_SOURCE_MANIFEST_SHA_DRIFT')
 with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  if not c.execute("select 1 from sqlite_master where type='view' and name=?",(view,)).fetchone():
   raise ValueError('DATASET_VERSION_DOES_NOT_CONTAIN_REQUESTED_VIEW')
  total=c.execute('select count(*) from '+view+' where (? is null or paper_key=?)',(paper,paper)).fetchone()[0];rows=[dict(x) for x in c.execute('select * from '+view+' where (? is null or paper_key=?) order by paper_key,row_id limit ?',(paper,paper,limit))]
 return dict(manifest=m,view=view,total=total,rows=rows,Gold=0)

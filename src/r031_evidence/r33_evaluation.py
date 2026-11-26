"""Source-bound evaluation references, separate from R26/R27 science/Gold.
Interface ideas: MetaBeeAI GUI answers vs predictions; no LLM judge as truth.
"""
from pathlib import Path
from contextlib import closing
from collections import Counter
import datetime,hashlib,json,math,os,sqlite3
from .r30_ledger import db_path,digest,canonical_json
ERRORS=('SOURCE_MISSING','SI_MISSING','PARSE_FAILURE','ENTITY_MISMATCH','ARM_CONTROL_MISMATCH','ENDPOINT_MISMATCH','UNIT_OR_VALUE_ERROR','SOURCE_UNSUPPORTED','SCHEMA_INCOMPLETE','LICENSE_HOLD')
FIELDS=('entity','treatment','control','model','dose','time','method','endpoint','value','unit','source_locator')
DECISIONS=('CORRECT','INCORRECT','MISSING','UNVERIFIED')

def write_new(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2);f.write('\n')
 return digest(p.read_bytes())

def load_packet(path,expected_sha):
 p=Path(path).expanduser().resolve();raw=p.read_bytes()
 if not expected_sha or digest(raw)!=expected_sha:raise ValueError('R33_FROZEN_PACKET_SHA_REQUIRED_OR_CHANGED')
 d=json.loads(raw)
 if d.get('schema')!='asterloom.evaluation-packet/1' or len({x['case_id'] for x in d['cases']})!=len(d['cases']):raise ValueError('R33_PACKET_SCHEMA_OR_DUPLICATE_CASE')
 return d

def freeze(home,output,count=24,max_per_paper=3):
 if not 20<=count<=30 or not 1<=max_per_paper<=3:raise ValueError('R33_REFERENCE_PACKET_BOUNDS')
 root=Path(output).resolve()
 if root.exists():raise ValueError('R33_FROZEN_PACKET_DIRECTORY_EXISTS')
 db=db_path(home);before=digest(db.read_bytes());cases=[];papers=[];assets={}
 with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  rows=c.execute('select paper_key,count(*) n,group_concat(distinct endpoint) endpoints from r17_measurement group by paper_key').fetchall()
  groups={k:[] for k in ['ATP','POLYMER','SMALL_MOLECULE_ACTIVITY']}
  for r in rows:
   eps=r['endpoints'];key='ATP' if 'ATP_' in eps else 'POLYMER' if any(t in eps for t in ['MATERIAL_','MECHANICAL','SWELLING','FORMULATION_','PORE_DIAMETER']) else 'SMALL_MOLECULE_ACTIVITY'
   groups[key].append(r)
  chosen=[]
  for group,target in [('ATP',6),('POLYMER',8),('SMALL_MOLECULE_ACTIVITY',count-14)]:
   ordered=sorted(groups[group],key=lambda r:(0 if 'ATP_GENERATION_RATE' in r['endpoints'] else 1,digest('R33_REFERENCE_V1|'+r['paper_key'])))
   chosen.extend((group,r) for r in ordered[:target])
  if len(chosen)!=count:raise ValueError('R33_NOT_ENOUGH_REAL_SOURCE_PAPERS_FOR_STRATA')
  for group,paper in chosen:
   key=paper['paper_key'];identity=dict(c.execute('select paper_key,doi,pmcid,study_family_state from paper_ref where paper_key=?',(key,)).fetchone());originals=c.execute('select * from r16_parsed_asset where paper_key=?',(key,)).fetchall()
   if not originals:raise ValueError('R33_SOURCE_ORIGINAL_REQUIRED:'+key)
   for a in originals:
    p=Path(a['original_path'])
    if not p.is_file() or digest(p.read_bytes())!=a['original_sha']:raise ValueError('R33_SOURCE_BYTES_MISSING_OR_DRIFT:'+key)
    assets[a['original_sha']]=dict(asset_id=a['parsed_asset_id'],paper_key=key,sha256=a['original_sha'],path=str(p),format=a['source_format'],IR_path=a['ir_path'],IR_sha256=a['ir_sha'])
   papers.append(dict(stratum=group,identity=identity,selection_scope='CONVENIENCE_EXISTING_AUTHOR_SOURCES_NOT_UNSEEN_POPULATION_OR_HUMAN_ELIGIBILITY',original_source_assets=[a['original_sha'] for a in originals]))
   measurements=c.execute('select * from r17_measurement where paper_key=? order by measurement_id',(key,)).fetchall();measurements=sorted(measurements,key=lambda m:digest('R33_CASE_V1|'+m['measurement_id']))[:max_per_paper]
   for m in measurements:
    try:ctx=c.execute('select * from r19_measurement_context where measurement_id=?',(m['measurement_id'],)).fetchone()
    except sqlite3.Error:ctx=None
    ctx=dict(ctx) if ctx else {}
    f=dict(entity=ctx.get('subject_literal'),treatment=m['author_group_literal'] if m['role']!='CONTROL' else None,control=None,model=ctx.get('model_literal'),dose=ctx.get('dose_literal') or m['dose_author_literal'],time=ctx.get('time_literal') or m['time_author_literal'],method=ctx.get('assay_literal'),endpoint=m['endpoint'],value=m['numeric_value'],unit=m['unit_author_literal'],source_locator=m['original_locator'])
    cases.append(dict(case_id='r33:case:'+digest(m['measurement_id'])[:24],paper_key=key,stratum=group,existing_measurement_id=m['measurement_id'],existing_experiment_id=m['experiment_id'],source_sha256=m['author_sha'],source_locator=m['original_locator'],source_literal=m['source_literal'],source_cell_key=m['source_cell_key'],field_candidates=f,field_state={k:'EXISTING_CORE_CANDIDATE_NOT_HUMAN_TRUTH' if v is not None else 'UNKNOWN_NOT_YET_ADJUDICATED' for k,v in f.items()},scientific_status='SOURCE_ONLY_HOLD',training_approved=False))
 after=digest(db.read_bytes())
 if before!=after:raise ValueError('R33_SOURCE_DATABASE_CHANGED_DURING_FREEZE')
 root.mkdir(parents=True);packet=dict(schema='asterloom.evaluation-packet/1',created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_database_sha256=before,paper_count=len(papers),case_count=len(cases),papers=papers,cases=cases,source_assets=assets,reference_state='HUMAN_ANSWERS_NOT_AVAILABLE',reference_not_training_Gold=True,Gold=0,annotation_fields=FIELDS,error_taxonomy=ERRORS,blind_mode_available=True,thresholds=dict(exact_source_identity_required=True,numeric_author_literal_preserved=True,scientific_inference_from_unknown=False),selection='Deterministic SHA stratified existing sources; development benchmark, no claim of independent holdout')
 p=root/'packet.json';lock=write_new(p,packet);write_new(root/'packet.sha256.json',dict(path=str(p),sha256=lock))
 baseline=dict(schema='asterloom.evaluation-predictions/1',packet_sha256=lock,strategy='EXISTING_DETERMINISTIC_CORE',cases=[dict(case_id=x['case_id'],fields=x['field_candidates'],abstained_fields=[k for k,v in x['field_candidates'].items() if v is None]) for x in cases],Gold=0)
 write_new(root/'candidate_baseline.json',baseline)
 template=dict(schema='asterloom.human-evaluation-reference/1',packet_sha256=lock,reference_status='HUMAN_REVIEW_PENDING',annotators=[],human_owner_release=None,reference_only_not_training_Gold=True,cases=[dict(case_id=x['case_id'],fields={k:dict(state='UNREVIEWED',value=None,source_sha256=x['source_sha256'],source_locator=x['source_locator'],reviewer_identity=None,signature=None) for k in FIELDS}) for x in cases],Gold=0)
 write_new(root/'reference_template.json',template)
 return dict(status='FROZEN_SOURCE_PACKET_AWAITING_REAL_HUMAN_REFERENCE',packet=str(p),packet_sha256=lock,papers=len(papers),cases=len(cases),source_assets=len(assets),reference_status='HUMAN_REVIEW_PENDING',Gold=0)

def proportion(n,d):
 if not d:return dict(numerator=n,denominator=d,value=None,CI95=None)
 p=n/d;z=1.959963984540054;den=1+z*z/d;center=(p+z*z/(2*d))/den;half=z*math.sqrt(p*(1-p)/d+z*z/(4*d*d))/den
 return dict(numerator=n,denominator=d,value=p,CI95=[max(0,center-half),min(1,center+half)],CI_method='Wilson_binomial_descriptive_not_paper_cluster_inference')

def _equal(field,a,b):
 if a is None or b is None:return a is b
 if field=='value':
  if isinstance(a,bool) or isinstance(b,bool):return False
  try:return float(a)==float(b) and math.isfinite(float(a))
  except (ValueError,TypeError):return False
 if isinstance(a,str) and isinstance(b,str):return ' '.join(a.split())==' '.join(b.split())
 return a==b

def field_counts(field,expected,actual):
 """Pure arithmetic for unit tests; not a source of real human truth."""
 filled=actual not in (None,'');positive=expected not in (None,'');same=_equal(field,actual,expected)
 c=Counter(TP=0,FP=0,FN=0,TN=0,N=1,CORRECT=int(same),PRED_FILLED=int(filled),UNSUPPORTED=int(filled and not positive))
 if positive:
  if filled and same:c['TP']=1
  else:c['FN']=1;c['FP']=int(filled)
 elif filled:c['FP']=1
 else:c['TN']=1
 return c

def adjudicated_metrics(rows):
 """Pure scoring arithmetic; rows must be source-bound released human labels.
 Synthetic unit vectors may exercise arithmetic but never become references.
 """
 arm_n=arm_ok=uncertain_n=rejected=support_n=unsupported=xy_n=xy_ok=0
 for row in rows:
  truth=row.get('relation')
  if truth is not None:
   arm_n+=1;arm_ok+=int(truth==row.get('predicted_relation'))
  if row.get('source_uncertain') is True:
   uncertain_n+=1;rejected+=int(row.get('abstained_case') is True)
  if row.get('profile_eligible') is True:
   xy_n+=1;xy_ok+=int(row.get('human_complete_XY') is True and row.get('predicted_complete_XY') is True)
  for supported in row.get('support_of_filled_fields',[]):
   if isinstance(supported,bool):support_n+=1;unsupported+=int(not supported)
 def metric(n,d):return proportion(n,d) if d else None
 return dict(arm_control_accuracy=metric(arm_ok,arm_n),uncertainty_rejection_rate=metric(rejected,uncertain_n),unsupported_extraction_rate=metric(unsupported,support_n),complete_XY_yield=metric(xy_ok,xy_n))

def evaluate(packet_file,packet_sha,predictions_file,reference_file=None):
 packet=load_packet(packet_file,packet_sha)
 for source_sha,asset in packet['source_assets'].items():
  if digest(Path(asset['path']).read_bytes())!=source_sha:raise ValueError('R33_EVALUATION_ORIGINAL_SHA_DRIFT')
 p=json.loads(Path(predictions_file).read_text())
 if p.get('packet_sha256')!=packet_sha:raise ValueError('R33_PREDICTION_CORPUS_SHA_MISMATCH')
 ids={x['case_id'] for x in packet['cases']};pred={x['case_id']:x for x in p.get('cases',[])}
 if len(pred)!=len(p.get('cases',[])) or set(pred)-ids:raise ValueError('R33_PREDICTION_DUPLICATE_OR_OUTSIDE_FROZEN_CORPUS')
 base=dict(schema='asterloom.scientific-evaluation/1',packet_sha256=packet_sha,papers=packet['paper_count'],cases=packet['case_count'],prediction_cases=len(pred),precision=None,recall=None,F1=None,numeric_accuracy=None,arm_control_accuracy=None,endpoint_accuracy=None,unit_accuracy=None,evidence_locator_accuracy=None,unsupported_extraction_rate=None,uncertainty_rejection_rate=None,complete_XY_yield=None,retrieval_coverage=None,field_counts=None,error_taxonomy=ERRORS,error_counts={},Gold=0,reference_not_training_Gold=True,small_sample_limits='Convenience corpus; fields within papers correlated; descriptive intervals are not independent-study significance tests')
 if reference_file is None:return dict(base,status='HOLD_NO_HUMAN_REFERENCE',reason='Real human answers and human-owner evaluation release not provided; no model-generated truth accepted')
 r=json.loads(Path(reference_file).read_text())
 if r.get('schema')!='asterloom.human-evaluation-reference/1' or r.get('packet_sha256')!=packet_sha:raise ValueError('R33_REFERENCE_SCHEMA_OR_FROZEN_SHA_MISMATCH')
 if r.get('Gold')!=0 or r.get('reference_only_not_training_Gold') is not True:raise ValueError('R33_EVALUATION_CANNOT_PROMOTE_GOLD')
 owner=r.get('human_owner_release') or {};people=r.get('annotators') or []
 if r.get('reference_status')!='HUMAN_OWNER_RELEASED_FOR_EVALUATION' or not owner.get('signature') or not owner.get('human_identity') or not owner.get('explicit_human_reference_confirmation') or len({x.get('identity') for x in people if x.get('kind')=='HUMAN' and x.get('signature')})<2:
  return dict(base,status='HOLD_HUMAN_REFERENCE_NOT_RELEASED',reason='Two signed distinct real human annotations plus explicit human-owner release required; professional identity not authenticated by this software')
 references={x['case_id']:x for x in r['cases']}
 if len(references)!=len(r['cases']) or set(references)-ids:raise ValueError('R33_REFERENCE_OUTSIDE_OR_DUPLICATE_CASE')
 counts={k:Counter(TP=0,FP=0,FN=0,TN=0,UNRESOLVED=0,N=0,CORRECT=0,PRED_FILLED=0,UNSUPPORTED=0) for k in FIELDS};errors=Counter();eligible_cases=0;complete=0
 by_id={x['case_id']:x for x in packet['cases']};adjudicated=[]
 for case in packet['cases']:
  cid=case['case_id'];truth=references.get(cid,{}).get('fields',{});prediction=pred.get(cid,{}).get('fields',{});eligible=False;all_exact=True
  signed=references.get(cid,{}).get('adjudications',{});prediction_case=pred.get(cid,{})
  def label(name):
   t=signed.get(name,{})
   if t.get('state')!='PRESENT':return None
   if t.get('source_sha256') not in {a['sha256'] for a in packet['source_assets'].values() if a['paper_key']==case['paper_key']} or not t.get('source_locator') or not t.get('signature') or not t.get('reviewer_identity'):raise ValueError('R33_ADJUDICATION_HUMAN_OR_SOURCE_BINDING_REQUIRED')
   return t.get('value')
  relation=label('arm_control_relation')
  if relation is not None and (not isinstance(relation,dict) or set(relation)!={'treatment_arm_id','control_arm_id','experiment_id','granularity'} or relation.get('granularity') not in ('GROUP_LEVEL_ONLY','REPLICATE_PAIR_AUTHOR_PROVEN') or not isinstance(relation.get('experiment_id'),str) or relation.get('experiment_id')!=case.get('existing_experiment_id')):raise ValueError('R33_EXPLICIT_EXISTING_EXPERIMENT_RELATION_REQUIRED')
  uncertainty=label('source_uncertain');eligible=label('profile_eligible');human_xy=label('complete_profile_XY');support=label('field_source_support');support={} if support is None else support
  if any(x is not None and not isinstance(x,bool) for x in (uncertainty,eligible,human_xy)) or not isinstance(support,dict) or any(k not in FIELDS or not isinstance(v,bool) for k,v in support.items()):raise ValueError('R33_ADJUDICATION_TYPED_LABEL_REQUIRED')
  adjudicated.append(dict(relation=relation,predicted_relation=prediction_case.get('arm_control_relation'),source_uncertain=uncertainty,abstained_case=prediction_case.get('abstained_case'),profile_eligible=eligible,human_complete_XY=human_xy,predicted_complete_XY=prediction_case.get('complete_profile_XY'),support_of_filled_fields=[v for k,v in support.items() if prediction.get(k) not in (None,'')]))

  for field in FIELDS:
   t=truth.get(field,{})
   if t.get('state') not in ('PRESENT','AUTHOR_NOT_REPORTED'):
    counts[field]['UNRESOLVED']+=1;all_exact=False;continue
   if t.get('source_sha256') not in {a['sha256'] for a in packet['source_assets'].values() if a['paper_key']==case['paper_key']} or not t.get('source_locator') or not t.get('signature') or not t.get('reviewer_identity'):raise ValueError('R33_REFERENCE_FIELD_MISSING_HUMAN_OR_SOURCE_BINDING')
   eligible=True;expected=t.get('value') if t['state']=='PRESENT' else None;actual=prediction.get(field);filled=actual not in (None,'');positive=expected not in (None,'');same=_equal(field,actual,expected)
   counts[field].update(field_counts(field,expected,actual))
   if not same:
    all_exact=False;error='ARM_CONTROL_MISMATCH' if field in ('treatment','control') else 'ENDPOINT_MISMATCH' if field=='endpoint' else 'UNIT_OR_VALUE_ERROR' if field in ('unit','value') else 'ENTITY_MISMATCH' if field in ('entity','model') else 'SOURCE_UNSUPPORTED' if field=='source_locator' else 'SCHEMA_INCOMPLETE';errors[error]+=1
  if eligible:eligible_cases+=1
  if all_exact:complete+=1
 total=sum(counts.values(),Counter());tp=total['TP'];fp=total['FP'];fn=total['FN'];precision=proportion(tp,tp+fp);recall=proportion(tp,tp+fn);den=2*tp+fp+fn
 metrics={k:dict(v,precision=proportion(v['TP'],v['TP']+v['FP']),recall=proportion(v['TP'],v['TP']+v['FN'])) for k,v in counts.items()}
 def acc(field):v=counts[field];return proportion(v['CORRECT'],v['N'])
 base.update(status='EVALUATION_REFERENCE_SCORED_NOT_TRAINING_APPROVED',precision=precision,recall=recall,F1=2*tp/den if den else None,field_counts=metrics,numeric_accuracy=acc('value'),endpoint_accuracy=acc('endpoint'),unit_accuracy=acc('unit'),evidence_locator_accuracy=acc('source_locator'),fully_matched_evaluation_case_rate=proportion(complete,eligible_cases),author_not_reported_fill_rate=proportion(total['UNSUPPORTED'],total['PRED_FILLED']),error_counts=dict(errors),human_reference_release_identity_asserted_not_software_authenticated=True)
 base.update(adjudicated_metrics(adjudicated))
 # Reference-scoped discovery coverage only. Unreviewed/outside populations never inferred.
 paper_labels=r.get('retrieval_reference') or [];eligible_papers=[]
 packet_papers={x['paper_key'] for x in packet['cases']}
 for t in paper_labels:
  if t.get('state')!='PRESENT':continue
  key=t.get('paper_key')
  if key not in packet_papers or t.get('eligible') is not True or not t.get('signature') or not t.get('reviewer_identity') or t.get('source_sha256') not in {a['sha256'] for a in packet['source_assets'].values() if a['paper_key']==key}:raise ValueError('R33_EXPLICIT_REFERENCE_PAPER_SCOPE_REQUIRED')
  eligible_papers.append(key)
 if len(eligible_papers)!=len(set(eligible_papers)):raise ValueError('R33_DUPLICATE_REFERENCE_PAPER_DENOMINATOR')
 if eligible_papers:
  discovered=p.get('discovered_paper_keys')
  if discovered is not None:
   if not isinstance(discovered,list) or any(not isinstance(x,str) for x in discovered):raise ValueError('R33_DISCOVERED_REFERENCE_IDENTITIES_REQUIRED')
   base['retrieval_coverage']=dict(proportion(len(set(eligible_papers)&set(discovered)),len(eligible_papers)),scope='EXPLICIT_SIGNED_FROZEN_PAPER_REFERENCE_ONLY_NOT_POPULATION_RECALL')
 base['denominator_protocol']='Relation: reviewed existing-ID tuples; rejection: human-uncertain cases; unsupported: reviewed source support of filled fields; X/Y: reviewed profile-eligible cases and human-complete matches. Unresolved labels excluded.'
 return base

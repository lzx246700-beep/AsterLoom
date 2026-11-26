"""Read-only stage attribution; missing extraction never means author silence."""
from pathlib import Path
from contextlib import closing
from collections import Counter
import json,sqlite3,xml.etree.ElementTree as ET
from .r30_ledger import digest,db_path,audit
from .r23_profiles import SourceProjector,load,NAMES
ROUTE={'ATP_CONTENT':('small_molecule_atp_content_v1','atp_content_absolute_v1'),'ATP_PRODUCTION_RATE':('small_molecule_atp_rate_v1','atp_rate_total_mito_glyco_v1'),'BIOACTIVE_COVALENT_POLYMER':('bioactive_polymer_v1','covalent_polymer_cell_response_v1'),'POLYMER_PROPERTY':('bioactive_polymer_v1','polymer_reaction_property_v1')}

def attribute(*,metadata_found,lawful_fulltext,parsed,si_declared,si_acquired,target_numeric,complete_XY):
 if not metadata_found:return 'METADATA_NOT_FOUND_IN_EXPLICIT_REFERENCE_SCOPE'
 if not lawful_fulltext:return 'METADATA_WITHOUT_LAWFUL_FULLTEXT'
 if not parsed:return 'ORIGINAL_ACQUIRED_PARSE_HOLD'
 if si_declared and not si_acquired:return 'PARSED_DECLARED_SI_NOT_ACQUIRED'
 if not target_numeric:return 'PARSED_TARGET_NUMERIC_NOT_EXTRACTED'
 if not complete_XY:return 'NUMERIC_WITHOUT_COMPLETE_PROFILE_EXPERIMENT_RELATION'
 return 'COMPLETE_XY_CANDIDATE_SCI_REVIEW_HOLD'

def report(home,manifest,expected_sha):
 raw=Path(manifest).read_bytes()
 if not expected_sha or digest(raw)!=expected_sha:raise ValueError('R34_FROZEN_REFERENCE_SHA_REQUIRED')
 m=json.loads(raw);rows=[];projector=SourceProjector(home);ready={};observation_counts={}
 try:
  for profile in NAMES:
   for r in projector.project(profile):
    key=(r['paper']['paper_key'],r['view']);observation_counts[key]=observation_counts.get(key,0)+1
    if r['task']['decision']=='TASK_READY_CANDIDATE':ready.setdefault(key,set()).add(r['dataset']['study_group'])
 finally:projector.close()
 with closing(sqlite3.connect('file:'+str(db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  for s in m['studies']:
   w=c.execute('select * from r30_work where paper_id=?',(s['paper_id'],)).fetchone();w=dict(w) if w else {};key=w.get('science_paper_key');profile,view=ROUTE[s['stratum']];v=next(x for x in load(profile,home).views if x['id']==view);allowed=v['endpoint_allow']
   assets=c.execute("select * from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml')",(key,)).fetchall() if key else []
   lawful=bool(w.get('source_sha') and assets);parsed=w.get('state')=='PARSED';native_refs=0;IR_refs=0;verified_original=False
   for a in assets:
    p=Path(a['original_path'])
    if p.is_file() and digest(p.read_bytes())==a['original_sha']:
     verified_original=True
     try:native_refs+=sum(e.tag.rsplit('}',1)[-1]=='supplementary-material' for e in ET.fromstring(p.read_bytes()).iter())
     except ET.ParseError:pass
    IR_refs+=a['supplement_count']
   si=c.execute('select count(distinct x.official_original_sha),count(distinct case when x.child_parsed_asset_id is not null then x.official_original_sha end) from r17_si_attachment x join r16_parsed_asset a on a.parsed_asset_id=x.parent_parsed_asset_id where a.paper_key=?',(key,)).fetchone() if key else (0,0)
   target=c.execute('select count(*) from r17_measurement where paper_key=? and endpoint in ('+','.join('?' for _ in allowed)+')',(key,*allowed)).fetchone()[0] if key else 0;complete=len(ready.get((key,view),set()));category=attribute(metadata_found=bool(w),lawful_fulltext=lawful,parsed=parsed,si_declared=bool(native_refs or IR_refs),si_acquired=si[0],target_numeric=target,complete_XY=complete)
   reasons=[]
   if native_refs>IR_refs:reasons.append('SOFTWARE_SI_REFERENCE_OMISSION_DETECTED')
   if not native_refs and not IR_refs:reasons.append('SI_NOT_IN_INDEXED_NATIVE_MARKUP_AUTHOR_AVAILABILITY_NOT_ADJUDICATED')
   if not target and parsed:reasons.append('NO_TARGET_Y_IN_CURRENT_EXTRACTOR_NOT_PROOF_AUTHOR_DID_NOT_REPORT')
   if target and not complete:reasons.append('PROFILE_REQUIRED_FIELDS_OR_RELATIONS_UNRESOLVED')
   rows.append(dict(paper_id=s['paper_id'],paper_key=key,stratum=s['stratum'],profile=profile,view=view,primary_gap=category,secondary_reasons=reasons,work_state=w.get('state'),work_error=w.get('last_error'),source_sha256=w.get('source_sha'),original_bytes_verified=verified_original,native_declared_supplements=native_refs,indexed_supplements=IR_refs,verified_SI_originals=si[0],parsed_native_SI_assets=si[1],target_numeric_rows=target,Profile_observations=observation_counts.get((key,view),0),complete_XY_candidate_study_groups=complete,author_missing_vs_software_missing='Only explicit native SI markup omission detected; experimental absence requires human adjudication',Gold=0))
 return dict(schema='asterloom.coverage-gap-attribution/1',frozen_manifest_sha256=expected_sha,denominator=len(rows),scope='Same previously frozen candidate set, not population recall or adjudicated relevance',primary_gap_counts=dict(Counter(x['primary_gap'] for x in rows)),per_stratum={k:dict(Counter(x['primary_gap'] for x in rows if x['stratum']==k)) for k in ROUTE},papers_with_verified_SI=sum(x['verified_SI_originals']>0 for x in rows),verified_SI_assets=sum(x['verified_SI_originals'] for x in rows),parsed_native_SI_assets=sum(x['parsed_native_SI_assets'] for x in rows),rows=rows,queue=audit(home,initialize=False),Gold=0)

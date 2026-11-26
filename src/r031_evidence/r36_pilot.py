"""Profile-driven pilot audit and source-only proposal staging in one Evidence Core.
No changes to Contract/Profile/Gold; XML narrative proposals never masquerade as
native R17 table cells. All source values and field anchors are independently replayed.
"""
from pathlib import Path
from contextlib import closing
import datetime,hashlib,json,re,sqlite3,xml.etree.ElementTree as ET
from . import r30_ledger as L
from .r23_profiles import SourceProjector,load,_f
from .r34_online import save
from .r36_native_evidence import xpath_map,xml,literal
DDL=('''CREATE TABLE IF NOT EXISTS r36_source_candidate(candidate_id TEXT PRIMARY KEY,paper_key TEXT NOT NULL REFERENCES paper_ref(paper_key),parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),author_sha TEXT NOT NULL,record_json TEXT NOT NULL,science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0),training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0))''',
'''CREATE TRIGGER IF NOT EXISTS r36_candidate_source_guard BEFORE INSERT ON r36_source_candidate WHEN NOT EXISTS(SELECT 1 FROM r16_parsed_asset a WHERE a.parsed_asset_id=NEW.parsed_asset_id AND a.paper_key=NEW.paper_key AND a.original_sha=NEW.author_sha) BEGIN SELECT RAISE(ABORT,'R36_CANDIDATE_CANONICAL_SOURCE_MISMATCH');END''',
'''CREATE TRIGGER IF NOT EXISTS r36_candidate_no_update BEFORE UPDATE ON r36_source_candidate BEGIN SELECT RAISE(ABORT,'R36_IMMUTABLE_SOURCE_PROPOSAL');END''',
'''CREATE TRIGGER IF NOT EXISTS r36_candidate_no_delete BEFORE DELETE ON r36_source_candidate BEGIN SELECT RAISE(ABORT,'R36_IMMUTABLE_SOURCE_PROPOSAL');END''')

def migrate_staging(home,backup_path,expected_db_sha,*,fault=False):
 db=L.db_path(home);marker=db.parent/'ISOLATED_CANDIDATE_STAGING_AUTHORIZATION.json'
 if not marker.exists() or json.loads(marker.read_text()).get('home')!=str(Path(home).resolve()):raise ValueError('R36_EXPLICIT_ISOLATED_HOME_MARKER_REQUIRED')
 if L.digest(db.read_bytes())!=expected_db_sha:raise ValueError('R36_MIGRATION_SOURCE_SHA_DRIFT')
 back=Path(backup_path)
 if back.exists():raise ValueError('R36_BACKUP_ALREADY_EXISTS')
 back.parent.mkdir(parents=True,exist_ok=True)
 with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(back)) as b:a.backup(b)
 with closing(sqlite3.connect(db)) as c:
  if c.execute('pragma user_version').fetchone()[0]!=832:raise ValueError('R36_ISOLATED_SCHEMA832_ONLY')
  native=c.execute('select * from r17_measurement order by measurement_id').fetchall();c.execute('BEGIN IMMEDIATE')
  try:
   if L.digest(db.read_bytes())!=expected_db_sha:raise ValueError('R36_DATABASE_CHANGED_BEFORE_MIGRATION')
   for sql in DDL:c.execute(sql)
   if fault:raise RuntimeError('R36_INJECTED_MIGRATION_FAILURE')
   if native!=c.execute('select * from r17_measurement order by measurement_id').fetchall():raise ValueError('R36_NATIVE_VALUES_CHANGED')
   if c.execute('select count(*) from qualified_training_labels').fetchone()[0]:raise ValueError('R36_GOLD_NOT_AUTHORIZED')
   c.commit()
  except BaseException:c.rollback();raise
 return dict(status='ISOLATED_ADDITIVE_CANDIDATE_STAGING_ONLY',DDL_sha256=L.digest('\n'.join(DDL)),backup=str(back),backup_sha256=L.digest(back.read_bytes()),source_sha256_before=expected_db_sha,native_measurements_unchanged=True,user_version=832,Gold=0)

def _resolve(root,locator):
 if locator.startswith('/'+root.tag+'/'):return root.find(locator[len(root.tag)+2:])
 nodes=root.findall(locator)
 if len(nodes)!=1:raise ValueError('R36_NONUNIQUE_SOURCE_LOCATOR')
 return nodes[0]

def verify_proposal(home,record):
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  a=c.execute('select paper_key,original_sha,original_path from r16_parsed_asset where parsed_asset_id=?',(record['existing_parsed_asset_id'],)).fetchone()
 if not a or a[:2]!=(record['paper_key'],record['author_sha256']) or L.digest(Path(a[2]).read_bytes())!=a[1]:raise ValueError('R36_PROPOSAL_SOURCE_BINDING')
 root=xml(Path(a[2]).read_bytes());node=_resolve(root,record['source_locator']);text=' '.join(''.join(node.itertext()).split());begin,end=record['numeric_char_span']
 if text[begin:end]!=record['author_value_literal'] or float(record['author_value_literal'])!=record['author_numeric'] or record['source_quote'] not in text:raise ValueError('R36_PROPOSAL_NATIVE_NUMERIC_SPAN_DRIFT')
 for field,entry in record['fields'].items():
  if entry['value'] is None:continue
  anchor=entry['anchor']
  if anchor['kind']=='AUTHOR_XML':
   if anchor['sha256']!=a[1] or anchor['quote'] not in ' '.join(''.join(_resolve(root,anchor['locator']).itertext()).split()):raise ValueError('R36_FIELD_SOURCE_QUOTE_DRIFT:'+field)
   val=str(entry['value'])
   exact=val.casefold() in anchor['quote'].casefold() or field=='assay.method_locator' and val==anchor['locator']
   if field=='conditions.dose_literal' and val in ('1 MIC','2 MIC'):
    exact='(1 and 2 MIC)' in anchor['quote'] and '4.01' in anchor['quote'] and '1.17' in anchor['quote'] and 'respectively' in anchor['quote'] and record['author_value_literal']=={'1 MIC':'4.01','2 MIC':'1.17'}[val]
   if not exact:raise ValueError('R36_FIELD_VALUE_NOT_IN_OWN_SOURCE_QUOTE:'+field)

  elif anchor['kind']=='PUBCHEM_JSON':
   if L.digest(Path(anchor['path']).read_bytes())!=anchor['sha256']:raise ValueError('R36_CHEMICAL_REFERENCE_SHA_DRIFT')
   prop=json.loads(Path(anchor['path']).read_text())['PropertyTable']['Properties'][0]
   if prop['CID']!=5280343 or entry['value']!=(prop.get('ConnectivitySMILES') or prop.get('SMILES')) or prop['InChIKey']!=record['chemical_parent_InChIKey'] or '5280343' not in record['chemical_author_CID_quote']:raise ValueError('R36_CHEMICAL_AUTHOR_CID_MISMATCH')
  else:raise ValueError('R36_UNSUPPORTED_FIELD_ANCHOR')
 if record['science_state']!='SOURCE_ONLY_HOLD' or record['training_approved'] is not False:raise ValueError('R36_CANDIDATE_CANNOT_APPROVE_SCIENCE')
 return True

def quercetin_narrative_candidates(home,reference_json):
 key='PMC11123838'
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row;a=c.execute("select * from r16_parsed_asset where paper_key=? and source_format='.xml'",(key,)).fetchone()
 if not a:raise ValueError('R36_PRIMARY_AUTHOR_XML_REQUIRED')
 a=dict(a);root=xml(Path(a['original_path']).read_bytes());paths=xpath_map(root)
 def paragraph(fragment):
  matches=[e for e in root.iter('p') if fragment in ' '.join(''.join(e.itertext()).split())]
  if len(matches)!=1:raise ValueError('R36_UNIQUE_AUTHOR_PASSAGE_REQUIRED:'+fragment)
  e=matches[0];return dict(kind='AUTHOR_XML',sha256=a['original_sha'],locator=paths[id(e)],quote=' '.join(''.join(e.itertext()).split()))
 model=paragraph('Staphylococcus aureus ATCC 27217 was purchased');y=paragraph('ATP content to 6.46');methods=paragraph('The intracellular ATP and pyruvate contents were measured');prep=paragraph('Quercetin solution was added');chemical=paragraph('Quercetin (≥95%)');cid=paragraph('(CID: 5280343)');natural=paragraph('Quercetin is a common dietary flavonol')
 prop=json.loads(Path(reference_json).read_text())['PropertyTable']['Properties'][0]
 if prop['CID']!=5280343:raise ValueError('R36_WRONG_PARENT_CID')
 ref=dict(kind='PUBCHEM_JSON',sha256=L.digest(Path(reference_json).read_bytes()),path=str(Path(reference_json).resolve()),url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/5280343/property/CanonicalSMILES,InChIKey/JSON')
 values=[('0.5 MIC','6.46'),('1 MIC','4.01'),('2 MIC','1.17')];rows=[]
 for dose,value in values:
  begin=y['quote'].index(value);fields={}
  for name,val,anchor in [('chemical.tested_form_literal','quercetin',chemical),('chemical.parent_structure',prop.get('ConnectivitySMILES') or prop.get('SMILES'),ref),('chemical.natural_provenance','Quercetin is a common dietary flavonol found in various fruits, vegetables, and tea beverages.',natural),('biological.model_literal','Staphylococcus aureus ATCC 27217',model),('conditions.dose_literal',dose,y),('conditions.exposure_time_literal','24 h',prep),('assay.method_locator',methods['locator'],methods),('assay.normalization_basis','protein concentration of the samples',methods),('observation.native_unit','µmol/g prot',y)]:fields[name]=dict(value=val,anchor=anchor,state='SOURCE_BOUND_CANDIDATE_NOT_HUMAN_TRUTH')
  record=dict(protocol_version='r36.source-proposal/2',candidate_id='candidate:'+L.digest(a['original_sha']+'|'+y['locator']+'|'+str(begin)+'|r36-source-proposal-v2')[:32],paper_key=key,existing_parsed_asset_id=a['parsed_asset_id'],author_sha256=a['original_sha'],source_locator=y['locator'],source_quote=y['quote'],author_value_literal=value,author_numeric=float(value),numeric_char_span=[begin,begin+len(value)],endpoint='ATP_CONTENT',source_kind='AUTHOR_NARRATIVE_NUMERIC_NOT_R17_TABLE_MEASUREMENT',fields=fields,chemical_author_CID_quote=cid['quote'],chemical_parent_InChIKey=prop['InChIKey'],chemical_reference_scope='AUTHOR_CID_TO_REFERENCE_PARENT_NOT_SUPPLIER_HYDRATE_OR_LOT',dose_mapping_rule='EXPLICIT_AUTHOR_ORDERED_ENUMERATION_WITH_RESPECTIVELY; no MIC-to-mass conversion',control_author_literal='control',control_numeric=None,control_state='NUMERIC_CONTROL_ONLY_FIGURE_NOT_TRANSCRIBED',biological_n=None,technical_n=None,study_group='doi:10.3390/molecules29092266',science_state='SOURCE_ONLY_HOLD',hold_reasons=['NARRATIVE_CANDIDATE_NOT_CANONICAL_R17_TABLE_MEASUREMENT','INDEPENDENT_SCI_REVIEW_PENDING','CONTROL_NUMERIC_AND_REPLICATE_TYPE_UNRESOLVED','ACTUAL_TESTED_FORM_HYDRATE_LOT_NOT_REPORTED_IN_INSPECTED_MATERIALS'],training_approved=False)
  verify_proposal(home,record);rows.append(record)
 with closing(sqlite3.connect(L.db_path(home))) as c:
  c.execute('pragma foreign_keys=on')
  for r in rows:
   prior=c.execute('select record_json from r36_source_candidate where candidate_id=?',(r['candidate_id'],)).fetchone();raw=json.dumps(r,sort_keys=True,ensure_ascii=False)
   if prior and prior[0]!=raw:raise ValueError('R36_EXISTING_CANDIDATE_DRIFT')
   c.execute('insert or ignore into r36_source_candidate(candidate_id,paper_key,parsed_asset_id,author_sha,record_json) values(?,?,?,?,?)',(r['candidate_id'],key,a['parsed_asset_id'],a['original_sha'],raw))
  c.commit()
 return rows

def task_papers(home,plan):
 rows=[]
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  for item in plan['target_papers']:
   w=dict(c.execute('select * from r30_work where paper_id=?',(item['paper_id'],)).fetchone())
   if w['state']=='PARSED' and not w.get('source_path'):
    found=c.execute("select a.paper_key,a.original_sha,a.original_path from r16_parsed_asset a join paper_ref p using(paper_key) where (p.pmcid=? or p.paper_key=? or lower(p.doi)=?) and a.source_format in ('.xml','.nxml')",(item['pmcid'],item['pmcid'],str(item['metadata'].get('doi') or '').lower())).fetchall()
    keys={z['paper_key'] for z in found}
    valid=[z for z in found if Path(z['original_path']).is_file() and L.digest(Path(z['original_path']).read_bytes())==z['original_sha']]
    if len(keys)==1 and valid:
     z=valid[0];w.update(science_paper_key=z['paper_key'],source_sha=z['original_sha'],source_path=z['original_path'])
   rows.append(dict(paper_id=w['paper_id'],requested_identity=item['pmcid'],paper_key=w['science_paper_key'],state=w['state'],source_sha256=w['source_sha'],source_path=w['source_path'],last_error=w['last_error'],title=item['metadata']['title']))
  for key in plan['existing_related_studies']:
   a=c.execute("select original_sha,original_path from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml') limit 1",(key,)).fetchone();rows.append(dict(paper_id=None,requested_identity=key,paper_key=key,state='PARSED_LEGACY_RELATED',source_sha256=a[0] if a else None,source_path=a[1] if a else None,title='Existing ATP evidence; eligibility audited separately'))
 return rows

def audit_records(home,papers=None):
 p=SourceProjector(home)
 try:rows=list(p.project('small_molecule_atp_content_v1',papers=papers))
 finally:p.close()
 manifest=load('small_molecule_atp_content_v1',home).manifest;required=manifest['base_fields']+manifest['views'][0]['required'];out=[]
 for r in rows:
  holds=list(r['task']['reason_codes'])+['INDEPENDENT_SCI_REVIEW_PENDING'];kind=r['biological'].get('model_kind') or ''
  if 'EX_VIVO' in kind:holds.append('RELATED_EX_VIVO_NOT_CELL_CULTURE_PILOT')
  if r['chemical'].get('natural_source_state') not in ('AUTHOR_NATURAL_COMPOUND','NATURAL_COMPOUND_VERIFIED'):holds.append('NATURAL_OCCURRENCE_OR_TESTED_ORIGIN_NOT_INDEPENDENTLY_ADJUDICATED')
  if '2-deoxy' in str(r['chemical'].get('tested_form_literal')).lower():holds.append('GLUCOSE_ANALOG_NOT_AUTOMATIC_NATURAL_SMALL_MOLECULE_ELIGIBILITY')
  out.append(dict(record=r,field_matrix={f:dict(present=_f(r,f) is not None,missing_reason=r['missing_fields'].get(f)) for f in required},contract_field_complete=r['task']['decision']=='TASK_READY_CANDIDATE',pilot_scientific_eligibility='HUMAN_REVIEW_PENDING',hold_reasons=sorted(set(holds))))
 return out

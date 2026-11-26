"""Bounded R29 author S18 source-cell recovery with explicit endpoint semantics.

Reuses the installed Docling Worker and existing native r16/r17 Evidence Core.
Only the independently inspected 5x3 S18 author table is staged; no figures,
hemolysis column, batch identities, inferred active fraction or Gold labels.
The frozen covalent Profile does not allow this percentage endpoint; it is
retained as CELL_VIABILITY source evidence rather than relabeled OD450/ATP.
"""
from pathlib import Path
import hashlib,json,re,sqlite3
import xml.etree.ElementTree as ET
from . import repository as repo
from .r29_field_recovery import sha,_check_anchor
PAPER='PMC13204639'
PDF_SHA='0d4a7e63e63a98547fe32e3a4ac18b88175bcf6847ecdded794bb17c23ac43e0'
# Direct transcription checked against rendered original SI p14, Table S18.
AUTHOR_S18=(('Cur-PU-1',('94.97±1.92','99.01±1.03','98.78±1.34')),
 ('Cur-PU-2',('99.02±0.83','97.71±0.89','98.82±2.01')),
 ('Cur-PU-3',('98.23±1.02','99.29±1.19','95.49±1.73')),
 ('Cur-PU-4',('96.58±1.27','99.79±1.21','96.94±0.82')),
 ('BDO-PU',('96.27%±1.02','93.75%±0.98','100.84%±2.09')))

def _id(kind,*parts):return kind+':'+hashlib.sha256('\0'.join(map(str,parts)).encode()).hexdigest()[:32]
def stage(home):
 root=Path(home).resolve();db=root/'state/science.sqlite3'
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=on')
 try:
  if c.execute('pragma user_version').fetchone()[0]!=832:raise ValueError('R29_SCHEMA832_COPY_REQUIRED')
  asset=c.execute("select * from r16_parsed_asset where paper_key=? and original_sha=? and backend='docling-standard-cpu-no-ocr'",(PAPER,PDF_SHA)).fetchone()
  if not asset or sha(asset['original_path'])!=PDF_SHA or sha(asset['ir_path'])!=asset['ir_sha']:raise ValueError('R29_AUTHOR_PDF_OR_WORKER_IR_SHA_DRIFT')
  table=c.execute("select * from r16_source_table where parsed_asset_id=? and table_id='#/tables/22'",(asset['parsed_asset_id'],)).fetchone()
  if not table:raise ValueError('R29_AUTHOR_S18_TABLE_MISSING')
  cells={(r['cell_row'],r['cell_column']):r for r in c.execute('select * from r16_source_cell where table_key=?',(table['table_key'],))}
  for col,t in [(1,'24h'),(2,'48h'),(3,'72h')]:
   if cells[(1,col)]['original_literal'].strip()!=t or cells[(2,col)]['original_literal'].strip()!='OD (%)':raise ValueError('R29_AUTHOR_S18_TIME_OR_PERCENT_HEADER_DRIFT')
  if cells[(1,4)]['original_literal'].strip()!='hemolysis':raise ValueError('R29_AUTHOR_HEMOLYSIS_COLUMN_SEPARATION_FAILED')
  jats=c.execute("select * from r16_parsed_asset where paper_key=? and source_format='.xml'",(PAPER,)).fetchone()
  if not jats or sha(jats['original_path'])!=jats['original_sha']:raise ValueError('R29_JATS_SOURCE_DRIFT')
  doc=ET.parse(jats['original_path']).getroot()
  def proof(section):
   locator=".//*[@id='"+section+"']";nodes=doc.findall(locator)
   if len(nodes)!=1:raise ValueError('R29_METHOD_NODE_NOT_UNIQUE')
   a=dict(kind='AUTHOR_XML',path=jats['original_path'],sha256=jats['original_sha'],locator=locator,quote=' '.join(''.join(nodes[0].itertext()).split()))
   _check_anchor(c,PAPER,a);return a
  method=proof('sec2dot9-biomolecules-16-00640');reaction=proof('sec3dot1-biomolecules-16-00640')
  if 'CCK-8' not in method['quote'] or 'C2C12' not in method['quote']:raise ValueError('R29_SPECIFIC_CELL_ASSAY_MISSING')
  experiment=_id('r17:experiment',PAPER,PDF_SHA,'S18_CELL_VIABILITY')
  native=[];links=[]
  for ridx,(material,values) in enumerate(AUTHOR_S18,3):
   group=cells[(ridx,0)];canonical=re.sub(r'\s+','',group['original_literal']).replace('‐','-')
   if canonical!=material:raise ValueError('R29_AUTHOR_S18_ROW_LABEL_DRIFT')
   for col,literal in enumerate(values,1):
    cell=cells[(ridx,col)];raw=cell['original_literal']
    if raw.strip()!=literal or cell['page_number']!=14 or not cell['bbox_json']:raise ValueError('R29_EXACT_AUTHOR_PDF_CELL_LITERAL_OR_PAGE_DRIFT')
    parsed=re.fullmatch(r'(\d+\.\d+)%?±(\d+\.\d+)',literal)
    if not parsed:raise ValueError('R29_AUTHOR_PERCENT_NUMERIC_PARSE_HOLD')
    mid=_id('r17:measurement',PAPER,PDF_SHA,cell['cell_key'],'CELL_VIABILITY')
    time={1:'24h',2:'48h',3:'72h'}[col]
    native.append(dict(measurement_id=mid,experiment_id=experiment,parsed_asset_id=asset['parsed_asset_id'],source_cell_key=cell['cell_key'],paper_key=PAPER,author_sha=PDF_SHA,endpoint='CELL_VIABILITY',metric_author_literal='OD (%)',author_group_literal=material,group_source_cell_key=group['cell_key'],role='COMPARATOR_MATERIAL' if material=='BDO-PU' else 'TREATMENT',source_literal=raw,numeric_value=float(parsed[1]),uncertainty=float(parsed[2]),uncertainty_kind='AUTHOR_PM_UNTYPED',unit_author_literal='%',dose_author_literal=None,time_author_literal=time,biological_n=None,cell_row=cell['cell_row'],cell_column=cell['cell_column'],original_locator=cell['original_locator'],page_number=14,bbox_json=cell['bbox_json'],missing_fields_json=json.dumps(['AUTHOR_INDEPENDENT_BATCH_NOT_REPORTED','ACTIVE_CURCUMIN_INCORPORATION_NOT_QUANTIFIED','EXTRACT_MATERIAL_MASS_DOSE_NOT_REPORTED','BIOLOGICAL_N_NOT_REPORTED','FROZEN_COVALENT_PROFILE_ENDPOINT_NOT_ALLOWED','NO_INDEPENDENT_SCI_REVIEW']),science_state='SOURCE_ONLY_REVIEW_HOLD',training_approved=0))
    links.append(dict(measurement_id=mid,paper_key=PAPER,material_literal=material,material_batch_id=None,reaction_anchor=reaction,method_anchor=method,time_literal=time,source_scope='SAME_AUTHOR_FORMULATION_NOT_PROVEN_SAME_SYNTHESIS_BATCH',active_unit='curcumin' if material!='BDO-PU' else None,active_measured_incorporation=None,other_measured_composition='SI Table S1 measures PCL/PEG segment ratio, not curcumin incorporation',biological_model='C2C12',assay='CCK-8',concurrent_comparator_material='BDO-PU',control_role='BDO-PU formulation comparison is not per-run matched untreated control',normalization='AUTHOR_OD_PERCENT_NO_RENORMALIZATION',endpoint='CELL_VIABILITY',native_unit='%',source_pdf_sha256=PDF_SHA,source_cell=cell['original_locator'],source_page=14,bbox=json.loads(cell['bbox_json']),independent_review=False,Gold=0))
  old=[tuple(x) for x in c.execute('select * from r17_measurement order by measurement_id')];old_ids={r[0] for r in old}
  c.execute('BEGIN IMMEDIATE')
  c.execute('''CREATE TABLE IF NOT EXISTS r29_material_observation_link (
    measurement_id TEXT PRIMARY KEY REFERENCES r17_measurement(measurement_id),
    paper_key TEXT NOT NULL, material_literal TEXT NOT NULL,
    material_batch_author_id TEXT, source_link_json TEXT NOT NULL,
    science_approved INTEGER NOT NULL DEFAULT 0 CHECK(science_approved=0))''')
  for op in ('UPDATE','DELETE'):
   c.execute(f"CREATE TRIGGER IF NOT EXISTS r29_material_link_no_{op.lower()} BEFORE {op} ON r29_material_observation_link BEGIN SELECT RAISE(ABORT,'R29_APPEND_ONLY');END")
  # repository.put performs exact immutable tuple comparison on replay.
  c.row_factory=None
  repo.put(c,'r17_experiment',dict(experiment_id=experiment,parsed_asset_id=asset['parsed_asset_id'],table_key=table['table_key'],paper_key=PAPER,author_sha=PDF_SHA,endpoint='CELL_VIABILITY',metric_author_literal='OD (%)',table_caption='Author SI Table S18, p14: C2C12 cell viability 24/48/72 h; relative hemolysis excluded',model_author_literal='C2C12',methods_source_locator=method['locator'],article_type='research-article',research_state='ORIGINAL_RESEARCH',evidence_status='AUTHOR_ORIGINAL_SOURCE_CANDIDATE',science_approved=0))
  added=0
  for m,link in zip(native,links):
   added+=repo.put(c,'r17_measurement',m)
   row=(m['measurement_id'],PAPER,m['author_group_literal'],None,json.dumps(link,ensure_ascii=False,sort_keys=True),0)
   previous=c.execute('select * from r29_material_observation_link where measurement_id=?',(m['measurement_id'],)).fetchone()
   if previous and tuple(previous)!=row:raise ValueError('R29_MATERIAL_LINK_DRIFT')
   if not previous:c.execute('insert into r29_material_observation_link values(?,?,?,?,?,?)',row)
  if old!=[tuple(x) for x in c.execute('select * from r17_measurement order by measurement_id') if x[0] in old_ids]:raise ValueError('R29_HISTORIC_NATIVE_Y_CHANGED')
  if c.execute('pragma foreign_key_check').fetchall():raise ValueError('R29_FK_FAILURE')
  if c.execute('select count(*) from qualified_training_labels').fetchone()[0]:raise ValueError('R29_NO_GOLD_AUTHORIZED')
  c.commit()
  return dict(schema='asterloom.r29.polymer-author-s18-source/1',paper=PAPER,new_author_native_measurements=added,source_rows=len(native),independent_original_studies=1,endpoint='CELL_VIABILITY',unit='%',frozen_covalent_profile_endpoint_eligible=False,complete_XY_studies=0,native_author_comparator='BDO-PU',author_PDF_sha256=PDF_SHA,author_JATS_sha256=jats['original_sha'],author_cell_visual_check='CURRENT_EXECUTOR_SOURCE_PAGE_RECONCILIATION_NOT_INDEPENDENT_HUMAN_REVIEW',method_source=method,reaction_source=reaction,links=links,Gold=0)
 except BaseException:
  c.rollback();raise
 finally:c.close()

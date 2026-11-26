"""Explicit source-locked Figure 2 development-review packet, not a science DB.
Demonstrates SI -> original numeric candidates, including author-source ambiguity.
No measured endpoint inferred from nearby glycolytic columns, no unit inference.
"""
from pathlib import Path
from contextlib import closing
import sqlite3
from .r30_ledger import db_path,digest
from .r33_evaluation import write_new
from .r36_native_evidence import tables
PAPER='PMC12371010'
XLSX_SHA='f7815cd688a35681b6f60cda4a3a3ad051c4977a8b2a396836a63ee532a11115'
XML_SHA='e9862e15be86f3074350b434c8f3f3a0b1ce036445f5cd12dd19dec85c8aacd0'

def build(home,output):
 with closing(sqlite3.connect('file:'+str(db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row;a=c.execute('select * from r16_parsed_asset where paper_key=? and original_sha=?',(PAPER,XLSX_SHA)).fetchone();parent=c.execute('select * from r16_parsed_asset where paper_key=? and original_sha=?',(PAPER,XML_SHA)).fetchone()
  if not a or not parent or digest(Path(parent['original_path']).read_bytes())!=XML_SHA:raise ValueError('R35_REAL_OFFICIAL_SI_AND_PARENT_REQUIRED')
  d=tables(a['original_path'],XLSX_SHA);sheet=next(t for t in d['tables'] if t['table_id']=='Figure 2');cells={x['a1']:x for x in sheet['cells']};refs={r['original_locator']:dict(r) for r in c.execute('select * from r16_source_cell where parsed_asset_id=?',(a['parsed_asset_id'],))};methods=[dict(r) for r in c.execute('select * from r16_source_method where parsed_asset_id=? order by method_key',(parent['parsed_asset_id'],))]
  if cells['A1']['literal']!='ATP production rate' or cells['I1']['literal']!='Glycolytic rate':raise ValueError('R35_AUTHOR_LAYOUT_DRIFT')
  records=[]
  for block,header,rows in [('GlcSph20',2,(3,4)),('GlcSph200',7,(8,9)),('LysoGb3_20',12,(13,14))]:
   control=cells['B'+str(header)];treatment=cells['E'+str(header)]
   if control['literal']!='DMSO' or not ('GlcSph' in treatment['literal'] or 'Lyso-Gb3' in treatment['literal']):raise ValueError('R35_AUTHOR_ARM_LAYOUT_DRIFT')
   for row,condition in zip(rows,('Basal','Induced')):
    if cells['A'+str(row)]['literal']!=condition:raise ValueError('R35_AUTHOR_CONDITION_LAYOUT_DRIFT')
    for col in 'BCDEFG':
     x=cells[col+str(row)];native=refs[x['locator']];group=control if col in 'BCD' else treatment;group_native=refs[group['locator']]
     if native['original_literal']!=x['literal']:raise ValueError('R35_EXISTING_IR_NATIVE_VALUE_DRIFT')
     value=float(x['literal']);role='CONTROL' if col in 'BCD' else 'TREATMENT'
     records.append(dict(candidate_id='r35:author-si:'+digest(native['cell_key'])[:24],paper_key=PAPER,existing_source_cell_key=native['cell_key'],existing_parsed_asset_id=a['parsed_asset_id'],source_sha256=XLSX_SHA,source_locator=x['locator'],source_literal=x['literal'],numeric_value=value,endpoint_candidate='ATP_GENERATION_RATE',endpoint_original_literal=cells['A1']['literal'],endpoint_source_locator=cells['A1']['locator'],group_author_literal=group['literal'],group_source_cell_key=group_native['cell_key'],group_source_locator=group['locator'],role=role,condition_author_literal=condition,condition_source_locator=cells['A'+str(row)]['locator'],group_comparison_scope=block+'|'+condition,comparator_group_literal='DMSO',replicate_pairing='UNKNOWN_NO_AUTHOR_REPLICATE_PAIR_MAPPING',unit_author_literal=None,unit_missing_reason='WORKBOOK_NO_UNIT_FIGURE_AXIS_CHECK_PENDING_NOT_AUTHOR_NONDISCLOSURE',dose_author_literal=treatment['literal'] if role=='TREATMENT' else None,normalization_context='AUTHOR_METHOD_SAYS_NORMALISED_TO_CELL_VIABILITY_REQUIRES_CASE_LINK_REVIEW',author_context_sha256=XML_SHA,scientific_state='SOURCE_ONLY_HOLD',hold_reasons=['UNIT_AND_FIGURE_AXIS_CHECK_PENDING','TESTED_FORM_AND_CHEMICAL_ELIGIBILITY_UNVERIFIED','GROUP_LEVEL_ONLY_NO_PAIRED_REPLICATE_INFERENCE','INDEPENDENT_SCI_REVIEW_PENDING'],training_approved=False))
  # A suspicious native block is retained as a conflict, never corrected by guessing.
  ambiguous=[cells[col+str(row)] for row in (18,19) for col in 'BCDEFG'];gly=[cells[col+str(row)] for row in (8,9) for col in 'JKLMNO'];duplicate=[x['literal'] for x in ambiguous]==[x['literal'] for x in gly]
  conflict=dict(state='ENDPOINT_OR_AUTHOR_LAYOUT_CONFLICT_HOLD',reason='ATP-labelled Lyso-Gb3 200 block uses Compensatory row and duplicates earlier glycolytic numeric region',row19_label=cells['A19']['literal'],identical_to_glycolytic_region=duplicate,source_sha256=XLSX_SHA,cells=ambiguous,not_promoted_to_ATP=True)
 result=dict(schema='asterloom.source-only-author-candidate-packet/1',paper_key=PAPER,author_XLSX_sha256=XLSX_SHA,author_XML_sha256=XML_SHA,scope='NEW_UNFROZEN_DEVELOPMENT_SOURCE_NOT_ADDED_TO_FROZEN24_OR_HUMAN_TRUTH',candidates=records,author_source_conflicts=[conflict],method_references=methods,candidate_count=len(records),new_r17_measurements=0,new_complete_Profile_XY=0,Gold=0)
 write_new(output,result);return dict(status='SOURCE_ONLY_CANDIDATES_WITH_AUTHOR_AMBIGUITY_HOLD',candidates=len(records),excluded_ambiguous_values=len(ambiguous),duplicate_glycolytic_region=duplicate,packet=str(Path(output).resolve()),packet_sha256=digest(Path(output).read_bytes()),new_r17_measurements=0,Gold=0)

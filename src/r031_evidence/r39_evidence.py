"""Append-only field proposals over existing R17/R24/R36 author evidence.
No author Y updates, image estimates, human truth, unit conversion or Gold.
"""
from pathlib import Path
from contextlib import closing
import csv,json,re,sqlite3
from . import r30_ledger as L
from .r36_native_evidence import xml,xpath_map,native_jats,literal
from .r39_targeting import registry,molecules_in,MACRO
from .r36_pilot import DDL
from . import r40_atp_relations as atp_relations
PROTOCOL='r39.evidence-bound/3'
def norm(s):return ' '.join(str(s).split())
def node(root,path):return root.find(path[len('/article/'):]) if path.startswith('/article/') else None
def anchor(e,paths,sha):return dict(kind='AUTHOR_XML',source_sha256=sha,source_locator=paths[id(e)],author_quote=norm(literal(e)))
def field(value,*anchors,rule='EXACT_AUTHOR_LITERAL',state='EVIDENCE_BOUND_CANDIDATE'):
 return dict(value=value,anchors=list(anchors),transform_rule=rule,state=state,confidence='DETERMINISTIC_RULE_NOT_HUMAN_TRUTH',contradictions=[])
def empty(reason):return dict(value=None,anchors=[],transform_rule=None,state=reason,confidence=None,contradictions=[])

def task_gate(record):
 """Versioned stricter macrophage task gate; original absolute Profile unchanged."""
 needed=['chemical.tested_form_literal','chemical.registry_parent_structure','biological.model_literal','conditions.dose_literal','conditions.exposure_time_literal','assay.method_locator','assay.normalization_basis']
 gaps=[k for k in needed if record['fields'].get(k,{}).get('value') is None]
 model=record['fields'].get('biological.model_literal',{}).get('value') or ''
 if not re.search(MACRO,model,re.I):gaps.append('TARGET_MACROPHAGE_MODEL_UNPROVEN')
 if record['endpoint']!='ATP_CONTENT' or record['value_semantics']!='ABSOLUTE_AUTHOR_REPORT':gaps.append('TARGET_ABSOLUTE_ATP_Y_UNPROVEN')
 if not re.search(r'[pnμµu]?mol\s*/\s*(?:m?g|(?:10\^?\d+\s*)?cells?)',record['unit'] or '',re.I):gaps.append('AUTHOR_CELL_OR_PROTEIN_DENOMINATOR_UNPROVEN')
 if not record.get('control'):gaps.append('TASK_REQUIRES_SAME_ASSAY_MATCHED_CONTROL')
 if any('COMBINATION' in x for x in record['hold_reasons']):gaps.append('NOT_SINGLE_MOLECULE')
 return dict(version='macrophage_direct_atp_content/1',status='PROFILE_COMPLETE_CORE_CANDIDATE' if not gaps else 'HOLD',missing=sorted(set(gaps)),human_science_approved=False)

def table_rows(raw):
 """Native logical columns/span blocks; zero concentration is not zero time."""
 out={}
 for table in native_jats(raw):
  cols=[x['column'] for x in table['cells'] if x['role']=='HEADER' and re.search(r'\bATP\b',x['literal']) and not re.search(r'ATPase|synthase|rate|/min',x['literal'],re.I)]
  doseheaders=[x for x in table['cells'] if x['role']=='HEADER' and re.search('concentrations?|dose',x['literal'],re.I)]
  if len(set(cols))!=1 or len(doseheaders)!=1:continue
  atpcol=cols[0];dh=doseheaders[0];unit=re.search(r'\(([^)]+)\)',dh['literal']);grid={}
  for x in table['cells']:
   for rr in range(x['row'],x['row']+x['rowspan']):
    for cc in range(x['column'],x['column']+x['colspan']):grid[rr,cc]=x
  grouped={}
  for x in table['cells']:
   if x['role']!='BODY' or x['column']!=atpcol:continue
   dose=grid.get((x['row'],dh['column']));group=grid.get((x['row'],1))
   if not dose or not group:continue
   block=group['locator'];role='CONTROL' if re.fullmatch(r'0(?:[.]0+)?|CON|control|vehicle|untreated',dose['literal'],re.I) else 'TREATMENT'
   rec=dict(table=table['locator'],Y=x,dose=dose,dose_header=dh,group=group,unit=unit[1] if unit else None,block=block,role=role)
   grouped.setdefault(block,[]).append(rec);out[x['locator']]=rec
  for rows in grouped.values():
   controls=[x for x in rows if x['role']=='CONTROL']
   for x in rows:x['control']=controls[0] if len(controls)==1 else None;x['control_state']='SAME_AUTHOR_SPAN_BLOCK_GROUP_LEVEL' if len(controls)==1 else 'HOLD_AMBIGUOUS_CONTROL' if controls else 'HOLD_NO_SAME_BLOCK_CONTROL'
 return out

def _context(root,sha):
 paths=xpath_map(root);methods=[];paragraphs=[]
 for e in root.iter('p'):paragraphs.append((e,anchor(e,paths,sha)))
 for sec in root.findall('./body//sec'):
  if re.search(r'method|material',sec.findtext('title') or '',re.I):
   methods.extend((p,anchor(p,paths,sha)) for p in sec.iter('p'))
 seen=set();methods=[x for x in methods if not (id(x[0]) in seen or seen.add(id(x[0])))]
 return paths,paragraphs,methods

def molecule_doses(sentence,name):
 """Only adjacent explicit drug-dose syntax; a stimulus elsewhere is not dose."""
 dose=r'(?<![\d.\-–])\d+(?:\.\d+)?\s*(?:μM|µM|mM|μg/mL|µg/mL)'
 alias=r'(?<![A-Za-z0-9])'+re.escape(name)+r'(?![A-Za-z0-9])'
 patterns=[alias+r'\s*(?:\(\s*)?(?:at\s+)?(?:a\s+concentration\s+of\s+)?(?P<dose>'+dose+r')',r'(?P<dose>'+dose+r')\s+(?:of\s+)?'+alias+r'\b']
 return sorted({m['dose'] for pattern in patterns for m in re.finditer(pattern,sentence,re.I)})

def _fields(root,sha,reg,group_literal=None,dose_info=None,narrative=None):
 paths,paras,methods=_context(root,sha);f={};atp=[]
 for e,a in methods:
  q=a['author_quote']
  if re.search(r'ATP\s+(?:content|levels?|assay)|(?:content|levels)\s+of\s+ATP|(?:cellular|intracellular)\s+ATP',q,re.I) and re.search(r'assay|kit|lucifer|lumin|spectrophot',q,re.I):
   if re.search(r'(?:measur|assess|determin)\w*|\busing\b',q,re.I) and not re.search(r'content of ATP synthase|ATP synthase assay',q,re.I):atp.append(a)
 f['assay.method_locator']=field(atp[0]['source_locator'],atp[0],rule='EXACT_ATP_METHOD_LOCATOR') if len(atp)==1 else empty('HOLD_AMBIGUOUS_ASSAY_METHOD' if atp else 'ASSAY_METHOD_SOURCE_NOT_BOUND')
 # Model must be in author Methods; raw THP-1 alone is not differentiated.
 models=[]
 for e,a in methods:
  mt=re.search(MACRO,a['author_quote'],re.I)
  if mt:models.append((mt[0],a))
  elif re.search(r'\bsperm(?:s|atozoa)?\b',a['author_quote'],re.I):models.append(('sperm',a))
 explicit=[]
 for _,a in methods:
  mt=re.search(r'RAW\s*264[. ]?7|BMDM\w*|J774(?:[.]?A[.]?1)?|NR8383',a['author_quote'],re.I)
  if mt:explicit.append((mt[0],a))
  thp=re.search(r'THP[ -]?1',a['author_quote'],re.I)
  if thp and re.search(r'differentiat\w*|PMA|phorbol',a['author_quote'],re.I):explicit.append((thp[0],a))
 if explicit:models=explicit
 families={re.sub(r'[^a-z0-9]','',m.casefold()) for m,a in models}
 f['biological.model_literal']=field(models[0][0],models[0][1]) if len(families)==1 else empty('HOLD_AMBIGUOUS_CELL_MODEL' if models else 'CELL_MODEL_SOURCE_NOT_BOUND')
 # Tested name is tied to an author table group or actual Methods, not a reference list.
 candidates=[]
 if group_literal:
  for _,a in paras:
   for m in molecules_in(a['author_quote'],reg):
    if re.search(re.escape(m['alias'])+r'\s*\('+re.escape(group_literal)+r'\b',a['author_quote'],re.I):candidates.append((m,a))
 elif narrative:
  # A result sentence identifies the intervention locally. Require its exact
  # registry identity also in body Methods; whole-paper reagent lists cannot vote.
  a=narrative['anchor']
  for m in molecules_in(narrative['sentence'],reg):
   if any(any(x['molecule_id']==m['molecule_id'] for x in molecules_in(b['author_quote'],reg)) for _,b in methods):candidates.append((m,a))
 else:
  for _,a in methods:
   if re.search(r'treat|incubat|expos|pre.?treat|purchased|obtained',a['author_quote'],re.I):candidates.extend((m,a) for m in molecules_in(a['author_quote'],reg))
 unique={m['molecule_id'] for m,a in candidates}
 chosen=candidates[0] if len(unique)==1 else None
 if chosen:
  m,a=chosen;f['chemical.tested_form_literal']=field(m['alias'],a);f['chemical.registry_parent_structure']=field(m['parent_SMILES'],dict(kind='REGISTRY_CSV',source_sha256=reg['file_SHA']['structures.csv'],source_locator='molecule_id='+m['molecule_id'],path=str(Path(reg['folder'])/'structures.csv'),author_quote=m['parent_SMILES']),rule='REGISTERED_PARENT_ONLY_NOT_TESTED_SALT',state='PARENT_REFERENCE_CANDIDATE');f['chemical.registry_molecule_id']=field(m['molecule_id'],a,rule='EXACT_AUTHOR_ALIAS_TO_SM297_CANDIDATE');f['chemical.natural_provenance_state']=field(m['natural_state'],dict(kind='REGISTRY_CSV',source_sha256=reg['file_SHA']['molecules.csv'],source_locator='molecule_id='+m['molecule_id'],path=str(Path(reg['folder'])/'molecules.csv'),author_quote=m['natural_state']),rule='REGISTERED_NATURALITY_NOT_AUTHOR_BATCH_ORIGIN')
 else:f['chemical.tested_form_literal']=empty('HOLD_AMBIGUOUS_TESTED_COMPOUND' if candidates else 'TESTED_COMPOUND_NOT_BOUND');f['chemical.registry_parent_structure']=empty('PARENT_REFERENCE_NOT_BOUND')
 if dose_info:
  d=dose_info;value=d['dose']['literal'];f['conditions.dose_literal']=field(value+((' '+d['unit']) if d['unit'] and re.fullmatch(r'[0-9.]+',value) else ''),dict(kind='AUTHOR_XML',source_sha256=sha,source_locator=d['dose']['locator'],author_quote=norm(value)),dict(kind='AUTHOR_XML',source_sha256=sha,source_locator=d['dose_header']['locator'],author_quote=norm(d['dose_header']['literal'])),rule='AUTHOR_DOSE_CELL_WITH_EXPLICIT_HEADER_UNIT')
 else:f['conditions.dose_literal']=empty('DOSE_NOT_UNIQUELY_BOUND_TO_ATP_ASSAY')
 f['conditions.exposure_time_literal']=empty('TOTAL_TREATMENT_EXPOSURE_NOT_UNIQUELY_BOUND')
 f['assay.normalization_basis']=empty('NORMALIZATION_NOT_BOUND_NOT_ASSUMED_FROM_KIT')
 if len(atp)==1:
  a=atp[0];q=a['author_quote'];mt=re.search(r'(?:normali[sz]\w*|per)[^.]{0,100}(?:protein|cell number|number of cells)',q,re.I)
  if mt:f['assay.normalization_basis']=field(mt[0],a)
  # Follow explicit section references; do not take arbitrary times elsewhere.
  refs=re.findall(r'[Ss]ection\s+(\d+(?:\.\d+)+)',q);prep=[]
  for sec in root.iter('sec'):
   title=sec.findtext('title') or ''
   if any(re.match(re.escape(s)+r'\b',title) for s in refs):prep.extend((p,anchor(p,paths,sha)) for p in sec.findall('p'))
  if prep:
   f['conditions.treatment_sequence_literal']=field(' | '.join(a['author_quote'] for _,a in prep),*[a for _,a in prep],rule='EXPLICIT_REFERENCED_AUTHOR_SEQUENCE_NOT_SUMMED')
  if chosen:
   name=chosen[0]['alias'];doses=[];times=[]
   for _,b in prep:
    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',b['author_quote']):
     if name.casefold() not in sentence.casefold():continue
     doses.extend((x,b) for x in molecule_doses(sentence,name));times.extend((x[0],b) for x in re.finditer(r'\d+(?:\.\d+)?\s*(?:h\b|hours?\b|min\b)',sentence))
   if len({x[0] for x in doses})==1:f['conditions.dose_literal']=field(doses[0][0],doses[0][1],rule='EXPLICIT_REFERENCED_PREPARATION_SINGLE_DOSE')
   if len({x[0] for x in times})==1:f['conditions.exposure_time_literal']=field(times[0][0],times[0][1],rule='EXPLICIT_REFERENCED_PREPARATION_SINGLE_TIME')
 if narrative and chosen:
  a=narrative['anchor'];sentence=narrative['sentence']
  doses=molecule_doses(sentence,chosen[0]['alias'])
  if len(set(doses))==1:f['conditions.dose_literal']=field(doses[0],a)
  # Explicit figure references supply time only when one referenced caption
  # names this intervention and ATP and reports one unambiguous duration.
  captions=[]
  for fig in root.iter('fig'):
   if fig.get('id') not in narrative['figure_ids']:continue
   cap=fig.find('caption')
   if cap is None:continue
   q=norm(literal(cap));lead=re.split(r'\bon\b',q,maxsplit=1,flags=re.I)[0];cs=molecules_in(lead,reg)
   if re.search(r'\bATP\b',q) and {m['molecule_id'] for m in cs}=={chosen[0]['molecule_id']}:captions.append(anchor(cap,paths,sha))
  times=[(m[0],b) for b in captions for m in re.finditer(r'\d+(?:\.\d+)?\s*(?:h\b|hours?\b)',b['author_quote'])]
  if len({x[0] for x in times})==1:f['conditions.exposure_time_literal']=field(times[0][0],times[0][1])
  f['experimental.comparison_author_sentence']=field(sentence,a)
 f['assay.independent_biological_n']=empty('REPLICATE_TYPE_NOT_INFERRED_FROM_N')
 # Preserve real pooled-batch wording, never turn it into donor n.
 pooled=[a for _,a in methods if re.search(r'pooled|pooling',a['author_quote'],re.I) and re.search(r'batch|independent|replicat',a['author_quote'],re.I)]
 if len(pooled)==1:f['assay.replicate_author_description']=field(pooled[0]['author_quote'],pooled[0])
 return f

def verify(home,r):
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  a=c.execute('select paper_key,original_sha,original_path from r16_parsed_asset where parsed_asset_id=?',(r['existing_parsed_asset_id'],)).fetchone()
  native=c.execute('select source_literal,numeric_value,author_sha,original_locator from r17_measurement where measurement_id=?',(r.get('native_measurement_id'),)).fetchone() if r.get('native_measurement_id') else None
  ctrl=r.get('control');control_native=c.execute('select paper_key,author_sha,original_locator,source_literal,numeric_value from r17_measurement where measurement_id=?',(ctrl['native_measurement_id'],)).fetchone() if ctrl and ctrl.get('native_measurement_id') else None
 if not a or a[:2]!=(r['paper_key'],r['author_sha256']) or L.digest(Path(a[2]).read_bytes())!=a[1]:raise ValueError('R39_CANDIDATE_SOURCE_SHA_BINDING_HOLD')
 root=xml(Path(a[2]).read_bytes());e=node(root,r['source_locator'])
 if e is None or r['author_literal'] not in norm(literal(e)):raise ValueError('R39_ORIGINAL_Y_LITERAL_DRIFT')
 if native and native!=(r['author_literal'],r['author_numeric'],r['author_sha256'],r['source_locator']):raise ValueError('R39_NATIVE_Y_CANNOT_CHANGE')
 for path,f in r['fields'].items():
  if f['value'] is None:continue
  allowed={'EXACT_AUTHOR_LITERAL','EXACT_ATP_METHOD_LOCATOR','REGISTERED_PARENT_ONLY_NOT_TESTED_SALT','EXACT_AUTHOR_ALIAS_TO_SM297_CANDIDATE','REGISTERED_NATURALITY_NOT_AUTHOR_BATCH_ORIGIN','AUTHOR_DOSE_CELL_WITH_EXPLICIT_HEADER_UNIT','EXPLICIT_REFERENCED_PREPARATION_SINGLE_DOSE','EXPLICIT_REFERENCED_PREPARATION_SINGLE_TIME','EXPLICIT_REFERENCED_AUTHOR_SEQUENCE_NOT_SUMMED'}
  if f['transform_rule'] not in allowed:raise ValueError('R39_UNSUPPORTED_FIELD_TRANSFORM_HOLD')
  for b in f['anchors']:
   if b['kind']=='REGISTRY_CSV':
    if not Path(b['path']).is_file() or L.digest(Path(b['path']).read_bytes())!=b['source_sha256']:raise ValueError('R39_REGISTRY_SHA_DRIFT')
    rid=b['source_locator'].removeprefix('molecule_id=');rows=[x for x in csv.DictReader(Path(b['path']).read_text().splitlines()) if x['molecule_id']==rid]
    if len(rows)!=1 or b['author_quote'] not in rows[0].values():raise ValueError('R39_REGISTRY_ROW_VALUE_DRIFT')
   else:
    source=node(root,b['source_locator'])
    if b['source_sha256']!=a[1] or source is None or norm(b['author_quote']) not in norm(literal(source)):raise ValueError('R39_FIELD_OWN_ANCHOR_QUOTE_DRIFT:'+path)
  rule=f['transform_rule'];quotes=' '.join(b['author_quote'] for b in f['anchors'])
  if rule in ('EXACT_AUTHOR_LITERAL','EXPLICIT_REFERENCED_PREPARATION_SINGLE_DOSE','EXPLICIT_REFERENCED_PREPARATION_SINGLE_TIME') and str(f['value']).casefold() not in quotes.casefold():raise ValueError('R39_FIELD_VALUE_UNSUPPORTED:'+path)
  if path=='conditions.dose_literal' and rule in ('EXACT_AUTHOR_LITERAL','EXPLICIT_REFERENCED_PREPARATION_SINGLE_DOSE'):
   name=r['fields'].get('chemical.tested_form_literal',{}).get('value')
   if not name or f['value'] not in molecule_doses(quotes,name):raise ValueError('R39_DOSE_BELONGS_TO_DIFFERENT_ARM_HOLD')
  if rule=='EXPLICIT_REFERENCED_AUTHOR_SEQUENCE_NOT_SUMMED' and f['value']!=' | '.join(b['author_quote'] for b in f['anchors']):raise ValueError('R39_AUTHOR_SEQUENCE_DRIFT')
  if rule=='EXACT_ATP_METHOD_LOCATOR' and f['value']!=f['anchors'][0]['source_locator']:raise ValueError('R39_METHOD_LOCATOR_MISMATCH')
  if rule in ('REGISTERED_PARENT_ONLY_NOT_TESTED_SALT','REGISTERED_NATURALITY_NOT_AUTHOR_BATCH_ORIGIN') and f['value']!=f['anchors'][0]['author_quote']:raise ValueError('R39_REGISTERED_REFERENCE_VALUE_DRIFT')
  if rule=='EXACT_AUTHOR_ALIAS_TO_SM297_CANDIDATE' and f['value']!=r['fields']['chemical.registry_parent_structure']['anchors'][0]['source_locator'].removeprefix('molecule_id='):raise ValueError('R39_REGISTRY_IDENTITY_BINDING_HOLD')
  if rule=='AUTHOR_DOSE_CELL_WITH_EXPLICIT_HEADER_UNIT':
   b=f['anchors'];number=b[0]['author_quote'];unit=re.search(r'\(([^)]+)\)',b[1]['author_quote']);expected=number+' '+unit[1] if unit and re.fullmatch(r'[0-9.]+',number) else number
   if f['value']!=expected:raise ValueError('R39_DOSE_TRANSFORM_DRIFT')
 control=r.get('control')
 if control and control.get('native_measurement_id'):
  mapping=table_rows(Path(a[2]).read_bytes());own=mapping.get(r['source_locator']);other=mapping.get(control['source_locator'])
  if not own or not other or own['block']!=other['block'] or other['role']!='CONTROL' or own['control'] is None:raise ValueError('R39_CROSS_BLOCK_OR_AMBIGUOUS_CONTROL_HOLD')
  if control['author_literal']!=norm(other['Y']['literal']):raise ValueError('R39_CONTROL_LITERAL_DRIFT')
  if control_native!=(r['paper_key'],r['author_sha256'],control['source_locator'],control['author_literal'],control['author_numeric']):raise ValueError('R39_CONTROL_IDENTITY_BINDING_HOLD')
 if native and (r.get('task_gate')!=task_gate(r) or r['scientific_core_gate']!=task_gate(r)['status']):raise ValueError('R39_TASK_GATE_CANNOT_SELF_APPROVE')
 if r.get('protocol_version') == 'r39.evidence-bound/4':
  sentence = r['author_sentence']
  sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', norm(literal(e)))
  options = atp_relations.relations(sentence)
  idx = r['arm_index']
  if sentence not in sentences or type(idx) is not int or not 0 <= idx < len(options):
   raise ValueError('R40_NARRATIVE_ARM_OR_SENTENCE_DRIFT')
  rel = options[idx]
  compound = r['fields']['chemical.tested_form_literal']['value']
  if (rel != r['author_relation'] or (compound and compound.casefold() != rel['arm'].casefold())
      or (rel['value'], rel['unit'], rel['endpoint']) != (r['author_literal'], r['unit'], r['endpoint'])
      or r['author_numeric'] != float(re.match(r'[0-9.]+', rel['value'])[0])
      or r['control'] is not None or r['native_measurement_id'] is not None
      or r['role'] != 'AUTHOR_EXPLICIT_EXPOSURE' or r.get('task_gate') != task_gate(r)
      or any(r['fields'][key]['value'] is not None for key in ('conditions.dose_literal','conditions.exposure_time_literal'))):
   raise ValueError('R40_AUTHOR_RELATION_DRIFT')
 elif not native:
  match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*%',r['author_literal'])
  if not match or r['author_numeric']!=float(match[1]) or r['unit']!='%' or r['endpoint']!='ATP_CONTENT_RELATIVE_AUTHOR_CHANGE' or r.get('author_sentence','') not in norm(literal(e)):raise ValueError('R39_NARRATIVE_VALUE_OR_CONTEXT_DRIFT')
 if not native and r['scientific_core_gate']!='HOLD':raise ValueError('R39_UNADJUDICATED_NARRATIVE_CORE_HOLD')
 if r['science_state']!='EVIDENCE_BOUND_CANDIDATE' or r['training_approved'] is not False:raise ValueError('R39_CANNOT_APPROVE_SCIENCE')
 return True

def _explicit_changes(root, asset, paper, registry_data, sentence, source):
    records = []
    for index, relation in enumerate(atp_relations.relations(sentence)):
        fields = _fields(root, asset['original_sha'], registry_data, narrative={
            'sentence': relation['arm'], 'anchor': source, 'figure_ids': []
        })
        model = fields['biological.model_literal']['value']
        if not model:
            if fields['biological.model_literal']['state'] != 'HOLD_AMBIGUOUS_CELL_MODEL':
                continue
            methods = _context(root, asset['original_sha'])[2]
            if not any(re.search(MACRO, a['author_quote'], re.I) for _, a in methods):
                continue
        elif not re.search(MACRO, model, re.I):
            continue
        fields['experimental.comparison_author_sentence'] = field(sentence, source)
        # A preparation discussed elsewhere cannot supply the dose or duration
        # for an individual arm without an explicit link.
        fields['conditions.dose_literal'] = empty('ARM_SPECIFIC_DOSE_NOT_BOUND')
        fields['conditions.exposure_time_literal'] = empty('ARM_SPECIFIC_DURATION_NOT_BOUND')
        fields.pop('conditions.treatment_sequence_literal', None)
        reason = ['ABSOLUTE_Y_AND_NUMERIC_MATCHED_CONTROL_NOT_REPORTED_IN_SENTENCE',
                  'INDEPENDENT_SCI_REVIEW_PENDING']
        if relation['endpoint'] == atp_relations.SYNTHESIS_HOLD:
            reason.append('ATP_SYNTHESIS_WORDING_CONTENT_VERSUS_RATE_UNRESOLVED')
        if not model:
            reason.append('MODEL_NOT_UNIQUELY_BOUND_TO_THIS_RESULT')
        value = relation['value']
        identifier = '|'.join([asset['original_sha'], source['source_locator'], sentence, str(index), '4'])
        record = dict(protocol_version='r39.evidence-bound/4', candidate_id='r40:relation:'+L.digest(identifier)[:32],
                      native_measurement_id=None, paper_key=paper, existing_parsed_asset_id=asset['parsed_asset_id'],
                      author_sha256=asset['original_sha'], source_locator=source['source_locator'],
                      author_literal=value, author_numeric=float(re.match(r'[0-9.]+', value)[0]),
                      unit=relation['unit'], endpoint=relation['endpoint'], value_semantics='AUTHOR_REPORTED_RELATIVE_CHANGE',
                      source_kind='AUTHOR_NARRATIVE_NUMERIC_PROPOSAL', fields=fields, author_sentence=sentence,
                      author_relation=relation, arm_index=index, block_anchor=None, role='AUTHOR_EXPLICIT_EXPOSURE',
                      control=None, scientific_core_gate='HOLD', hold_reasons=reason,
                      science_state='EVIDENCE_BOUND_CANDIDATE', training_approved=False, Gold=0)
        record['task_gate'] = task_gate(record)
        records.append(record)
    return records


def bind(home,paper_key,registry_dir):
 reg=registry(registry_dir);records=[];method_locs={}
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row;assets=[dict(x) for x in c.execute("select * from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml')",(paper_key,))];native=[dict(x) for x in c.execute("select * from r17_measurement where paper_key=? and endpoint='ATP_CONTENT'",(paper_key,))]
 for a in assets:
  raw=Path(a['original_path']).read_bytes()
  if L.digest(raw)!=a['original_sha']:raise ValueError('R39_ORIGINAL_BYTES_DRIFT')
  root=xml(raw);method_locs[a['parsed_asset_id']]={b['source_locator'] for _,b in _context(root,a['original_sha'])[2]};mapping=table_rows(raw);native_by_loc={m['original_locator']:m for m in native if m['author_sha']==a['original_sha']}
  for loc,m in native_by_loc.items():
   d=mapping.get(loc);fields=_fields(root,a['original_sha'],reg,d['group']['literal'] if d else None,d);control=None
   if d and d['role']=='TREATMENT' and d['control']:
    ctrl=native_by_loc.get(d['control']['Y']['locator'])
    if ctrl:control=dict(native_measurement_id=ctrl['measurement_id'],source_locator=ctrl['original_locator'],author_literal=ctrl['source_literal'],author_numeric=ctrl['numeric_value'],source_sha256=ctrl['author_sha'],block_anchor=d['block'],granularity='GROUP_LEVEL_ONLY_NOT_PAIRED_REPLICATES')
   holds=['INDEPENDENT_SCI_REVIEW_PENDING','REGISTERED_PARENT_NOT_TESTED_SALT_OR_BATCH_PROOF'];model=fields['biological.model_literal']['value'];macro=bool(model and re.search(MACRO,model,re.I))
   if not macro:holds.append('NON_MACROPHAGE_OR_AMBIGUOUS_MODEL_CORE_HOLD')
   unit=m['unit_author_literal'] or '';kind='ABSOLUTE_AUTHOR_REPORT' if re.search(r'[pnμµu]?mol',unit) else 'RELATIVE_AUTHOR_REPORT' if '%' in unit or 'fold' in unit.lower() else 'UNIT_SEMANTICS_HOLD'
   if re.search(r'/m[Ll]',unit):holds.append('EXTRACT_CONCENTRATION_NOT_PER_CELL_OR_PROTEIN_HOLD')
   if not control and (not d or d['role']!='CONTROL'):holds.append(d['control_state'] if d else 'SAME_ASSAY_CONTROL_NOT_BOUND')
   if d and '+' in d['dose']['literal']:holds.append('COMBINATION_NOT_SINGLE_MOLECULE')
   holds.extend(v['state'] for v in fields.values() if v['value'] is None)
   r=dict(protocol_version=PROTOCOL,candidate_id='r39:bound:'+L.digest(m['measurement_id']+'|'+PROTOCOL)[:32],native_measurement_id=m['measurement_id'],paper_key=paper_key,existing_parsed_asset_id=a['parsed_asset_id'],author_sha256=a['original_sha'],source_locator=loc,author_literal=m['source_literal'],author_numeric=m['numeric_value'],unit=unit,endpoint='ATP_CONTENT',value_semantics=kind,source_kind='ORIGINAL_R17_WITH_APPEND_ONLY_FIELD_PROPOSALS',fields=fields,block_anchor=d['block'] if d else None,role=d['role'] if d else m['role'],control=control,scientific_core_gate='HOLD',hold_reasons=sorted(set(holds)),science_state='EVIDENCE_BOUND_CANDIDATE',training_approved=False,Gold=0)
   r['task_gate']=task_gate(r);r['scientific_core_gate']=r['task_gate']['status'];verify(home,r);records.append(r)
  # Paragraph percentages are original changes, never reconstructed absolute ATP.
  paths,paras,methods=_context(root,a['original_sha'])
  for e,b in paras:
   if '/body/' not in b['source_locator'] or any(b['source_locator']==x[1]['source_locator'] for x in methods):continue
   for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',b['author_quote']):
    explicit = _explicit_changes(root, a, paper_key, reg, sentence, b)
    if explicit:
     for record in explicit:
      verify(home, record)
     records.extend(explicit)
     continue
    if not re.search(r'ATP (?:content|levels?)|(?:content|levels?) of ATP',sentence,re.I):continue
    perc=list(re.finditer(r'\b\d+(?:\.\d+)?\s*%',sentence))
    if len(perc)!=1:continue
    if not re.search(r'increas|decreas|reduc|compar',sentence,re.I):continue
    match=perc[0];context=dict(sentence=sentence,anchor=b,figure_ids=[x.get('rid') for x in e.iter('xref') if x.get('ref-type')=='fig']);f=_fields(root,a['original_sha'],reg,narrative=context);model=f['biological.model_literal']['value']
    if not model or not re.search(MACRO,model,re.I):continue
    val=match[0];r=dict(protocol_version=PROTOCOL,candidate_id='r39:paragraph:'+L.digest(a['original_sha']+'|'+b['source_locator']+'|'+sentence+'|'+PROTOCOL)[:32],native_measurement_id=None,paper_key=paper_key,existing_parsed_asset_id=a['parsed_asset_id'],author_sha256=a['original_sha'],source_locator=b['source_locator'],author_literal=val,author_numeric=float(re.search(r'[0-9.]+',val)[0]),unit='%',endpoint='ATP_CONTENT_RELATIVE_AUTHOR_CHANGE',value_semantics='AUTHOR_REPORTED_RELATIVE_CHANGE_NOT_ABSOLUTE_ATP',source_kind='AUTHOR_NARRATIVE_NUMERIC_PROPOSAL',fields=f,author_sentence=sentence,block_anchor=None,role='UNRESOLVED_AUTHOR_SENTENCE_CONTEXT',control=None,scientific_core_gate='HOLD',hold_reasons=['ABSOLUTE_Y_AND_NUMERIC_MATCHED_CONTROL_NOT_REPORTED_IN_SENTENCE','RELATIVE_CHANGE_NOT_ABSOLUTE_CONTENT','INTERVENTION_DIRECTION_AND_COMPARATOR_REQUIRE_REVIEW','INDEPENDENT_SCI_REVIEW_PENDING'],science_state='EVIDENCE_BOUND_CANDIDATE',training_approved=False,Gold=0)
    r['task_gate']=task_gate(r);r['scientific_core_gate']=r['task_gate']['status'];verify(home,r);records.append(r)
 if records:
  with closing(sqlite3.connect(L.db_path(home))) as c:
   c.execute('pragma foreign_keys=on');c.execute('begin immediate')
   for ddl in DDL:c.execute(ddl)
   for r in records:
    payload=json.dumps(r,sort_keys=True,ensure_ascii=False);prior=c.execute('select record_json from r36_source_candidate where candidate_id=?',(r['candidate_id'],)).fetchone()
    if prior and prior[0]!=payload:raise ValueError('R39_IMMUTABLE_PROPOSAL_REVISION_REQUIRES_NEW_PROTOCOL')
    c.execute('insert or ignore into r36_source_candidate(candidate_id,paper_key,parsed_asset_id,author_sha,record_json) values(?,?,?,?,?)',(r['candidate_id'],paper_key,r['existing_parsed_asset_id'],r['author_sha256'],payload))
    if r['native_measurement_id']:
     for name,f in r['fields'].items():
      if f['value'] is None:continue
      for b in f['anchors']:
       if b['kind']!='AUTHOR_XML':continue
       # R24 supports Methods/caption/cell only. Other exact XML fields remain
       # in the R36 proposal with their true XPath, never mislabeled Methods.
       if '/table/' not in b['source_locator'] and '/caption/' not in b['source_locator']:
        if b['source_locator'] not in method_locs[r['existing_parsed_asset_id']]:continue
       policy=json.dumps(dict(rule=f['transform_rule'],quote=b['author_quote'],proposal=r['candidate_id'],state='UNAPPROVED'),sort_keys=True,ensure_ascii=False);eid='r39:field:'+L.digest(r['native_measurement_id']+name+b['source_locator']+str(f['value']))[:32]
       kind='AUTHOR_SOURCE_CELL' if '/table/' in b['source_locator'] else 'AUTHOR_TABLE_CAPTION' if '/caption/' in b['source_locator'] else 'AUTHOR_METHODS'
       c.execute("insert or ignore into r24_field_evidence(evidence_id,measurement_id,field_path,asserted_literal,source_sha,source_locator,source_kind,match_policy,state) values(?,?,?,?,?,?,?,?,'AUTHOR_CONTEXT_MATCHED_UNAPPROVED')",(eid,r['native_measurement_id'],name,str(f['value']),b['source_sha256'],b['source_locator'],kind,policy))
   c.commit()
 return dict(paper_key=paper_key,source_bound_candidates=len(records),native_Y_unchanged=True,records=records,scientific_approved=0,Gold=0)

def bind_task(home,task_file,expected_sha,registry_dir):
 """Offline append-only refinement of a pinned executed scope, no acquisition."""
 from .r39_home import check
 from .r36_pilot import task_papers
 if L.digest(Path(task_file).read_bytes())!=expected_sha:raise ValueError('R39_BIND_TASK_SHA_HOLD')
 plan=json.loads(Path(task_file).read_text())
 if plan.get('profile')!='small_molecule_atp_content_v1' or not plan.get('task_id'):raise ValueError('R39_EXECUTED_ATP_TASK_REQUIRED')
 reg=registry(registry_dir)
 if plan.get('SM297_file_SHA') and reg['file_SHA']!=plan['SM297_file_SHA']:raise ValueError('R39_BIND_REGISTRY_SHA_HOLD')
 before=check(home);rows=[]
 for p in task_papers(home,plan):
  if p.get('paper_key'):rows.append(bind(home,p['paper_key'],registry_dir))
 return dict(status='OFFLINE_APPEND_ONLY_FIELD_REFINEMENT',protocol_version=PROTOCOL,task_sha256=expected_sha,canonical_db_sha256_before=before['db_sha256'],canonical_db_sha256_after=L.digest(L.db_path(home).read_bytes()),papers=rows,network_requests=0,human_science_approved=0,Gold=0)

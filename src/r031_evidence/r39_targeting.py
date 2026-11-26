"""Explainable macrophage/ATP screening; metadata never proves tested compound."""
from pathlib import Path
from contextlib import closing
import csv,hashlib,html,json,re,sqlite3
from .r30_ledger import identifiers,db_path,digest

MACRO=r'macrophag\w*|RAW\s*264[. ]?7|BMDM\w*|bone[ -]marrow[ -]derived|J774(?:[.]?A[.]?1)?|NR8383'
DIRECT=r'(?:intracellular|cellular|total|cell)\s+ATP|ATP\s+(?:content|levels?|pool|concentration|assay)|(?:luciferase|luminescen\w*)[^.]{0,60}ATP'
NONCORE=r'\bsperm(?:s|atozo\w*|atocyte\w*|atogen\w*)?\b|\boocytes?\b|\bembryo\w*|\bCandida\b|\bfungal\b|\bfungi\b|\bStaphylococcus\b|\bGardnerella\b|\bbacteri\w*|\bLeishmania\b'
MATERIAL=r'\bnano\w*|hydrogel|emulgel|liposom\w*|biomimetic|extracellular vesicles|formula\b|\w+-loaded|polymers?\b|titanium'
ALIAS_STOP={'natural','compound','metabolite','antioxidant','extract','plant','inhibitor','standard','vitamin'}

def text(x):return ' '.join(html.unescape(re.sub('<[^>]+>',' ',str(x or ''))).split())
def registry(folder):
 p=Path(folder);names={};m=list(csv.DictReader((p/'molecules.csv').read_text().splitlines()));s={r['molecule_id']:r for r in csv.DictReader((p/'structures.csv').read_text().splitlines())};n=list(csv.DictReader((p/'natural_provenance.csv').read_text().splitlines()))
 if len(m)!=297 or len({r['molecule_id'] for r in m})!=297 or len(s)!=297 or len({r['inchi_key'] for r in s.values()})!=297:raise ValueError('R39_SM297_IDENTITY_COUNTS_HOLD')
 for row in m:
  aliases=[row['preferred_name_en']]+[a.strip() for a in re.split(r'[;|]',row.get('synonyms') or '') if a.strip()]
  for a in aliases:
   if len(a)>=4 and a.casefold() not in ALIAS_STOP:names.setdefault(a.casefold(),[]).append(dict(molecule_id=row['molecule_id'],name=row['preferred_name_en'],parent_InChIKey=s[row['molecule_id']]['inchi_key'],parent_SMILES=s[row['molecule_id']]['standardized_smiles'],natural_state=row['natural_product_status'],scope='REGISTRY_PARENT_REFERENCE_NOT_AUTHOR_TESTED_FORM_OR_POSITIVE_EXPERIMENT'))
 return dict(folder=str(p.resolve()),file_SHA={f:digest((p/f).read_bytes()) for f in ('molecules.csv','structures.csv','natural_provenance.csv','README.md')},names=names,unique_molecules=297,natural_provenance_rows=len(n))

def molecules_in(content,reg):
 out={}
 for alias,values in reg['names'].items():
  matches=re.finditer(r'(?<![\w-])'+re.escape(alias)+r'(?=$|[^\w-]|-(?:treated|induced|exposed|supplemented|mediated|containing)\b)',content,re.I)
  for match in matches:
   following=content[match.end():match.end()+30];before=content[max(0,match.start()-20):match.start()]
   if alias=='adenosine' and re.match(r'\s*(?:tri|di|mono)phosphate|\s*5.?[′\'-]?',following,re.I):continue
   if alias=='glutathione' and re.match(r'\s+peroxidase',following,re.I):continue
   if alias=='ribose' and re.search(r'ADP\s*$',before,re.I):continue
   for v in values:out[v['molecule_id']]=dict(v,alias=content[match.start():match.end()],span=[match.start(),match.end()])
 return list(out.values())

def score(hit,reg,known=None):
 raw=hit.get('provider_record') or {};title=text(hit.get('title'));abstract=text(raw.get('abstractText') or hit.get('abstract'));joined=title+' '+abstract
 matches=molecules_in(joined,reg);model=bool(re.search(MACRO,joined,re.I));thp=bool(re.search(r'THP[ -]?1',joined,re.I));differentiated=thp and bool(re.search(r'differentiat\w*|PMA|phorbol|macrophag',joined,re.I));direct=bool(re.search(DIRECT,joined,re.I));other=bool(re.search(NONCORE,title,re.I));review=any('review' in str(x).lower() for x in (raw.get('pubTypeList') or {}).get('pubType',[])) or bool(re.search(r'\breview\b',title,re.I));stimulus=bool(re.search(r'ATP[ -]induced|ATP[ -]binding|ATPase|ATP[ -]stimulat',title,re.I) or re.search(r'real.time ATP rate',joined,re.I)) and not direct
 title_ids={m['molecule_id'] for m in molecules_in(title,reg)};foreground=[]
 for m in matches:
  alias=re.escape(m['alias']);tested=bool(re.search(r'(?:treat\w*|incubat\w*|supplement\w*|expos\w*|administr\w*)[^.;]{0,75}'+alias+'|'+alias+r'[^.;]{0,75}(?:treat\w*|supplement\w*)',abstract,re.I))
  if m['molecule_id'] in title_ids or tested:foreground.append(m)
 matches=foreground
 model_text=re.sub(r'macrophage\s+(?:migration\s+inhibitory\s+factor|stimulating\s*1)', ' MOLECULAR_PROTEIN_NAME ',joined,flags=re.I);model=bool(re.search(MACRO,model_text,re.I))
 material=bool(re.search(MATERIAL,title,re.I));tokens=identifiers(hit);prior=[known[t] for t in tokens if known and t in known];old=bool(prior);states=sorted({x['state'] for x in prior});processed=any(x in ('PARSED','HOLD_LICENSE') for x in states)
 reasons=[];s=0
 for condition,value,reason in [(model or differentiated,40,'MACROPHAGE_TITLE_ABSTRACT'),(direct,30,'DIRECT_ATP_CONTENT_LANGUAGE'),(bool(matches),20,'SM297_ALIAS_IN_TITLE_ABSTRACT_NOT_TESTED_PROOF'),(bool(matches) and bool(re.search(r'treat\w*|incubat\w*|expos\w*|pretreat\w*|administr\w*',joined,re.I)),10,'INTERVENTION_LANGUAGE_REQUIRES_METHODS_CONFIRMATION'),(bool(hit.get('pmcid')),5,'OFFICIAL_PMC_LOCATOR_NOT_LICENSE_PROOF'),(not old,12,'NEW_IDENTITY'),(processed,-15,'GAP_RECOVERY_PREVIOUSLY_PROCESSED'),(other,-100,'NONCORE_MODEL_IN_TITLE'),(review,-100,'REVIEW_NOT_ORIGINAL_RESEARCH'),(stimulus,-60,'ATP_STIMULUS_BINDING_OR_ENZYME_NOT_CONTENT'),(thp and not differentiated,-30,'THP1_DIFFERENTIATION_UNVERIFIED')]:
  if condition:s+=value;reasons.append(reason)
 if material:s-=70;reasons.append('MATERIAL_OR_FORMULATION_SECONDARY_NOT_FREE_MOLECULE')
 model_compound=(model or differentiated) and bool(matches) and not (other or review or stimulus or material)
 core=model_compound and direct
 route='CORE_SCREENING_CANDIDATE' if core else 'MACROPHAGE_COMPOUND_ASSAY_REFERENCE_PENDING' if model_compound else 'SECONDARY_TRANSFER' if direct and matches and not other and not review else 'BACKGROUND_OR_NONCORE'
 return dict(pmcid=hit.get('pmcid'),doi=hit.get('doi'),pmid=hit.get('pmid'),title=title,score=s,route=route,model_metadata_present=model or differentiated,direct_ATP_metadata_present=direct,matched_SM297=matches,reasons=reasons,novelty='GAP_RECOVERY' if processed else 'EXISTING_METADATA_UNPROCESSED' if old else 'NEW_IDENTITY',prior_states=states,author_tested_compound_verified=False,independent_human_reference=False,hit=hit)

def known_identities(home):
 with closing(sqlite3.connect('file:'+str(db_path(home))+'?mode=ro',uri=True)) as c:
  return {(ns,value):dict(paper_id=pid,state=state) for ns,value,pid,state in c.execute('select a.namespace,a.value,a.paper_id,w.state from r30_alias a join r30_work w using(paper_id)')}

def queries(compound_names):
 if not compound_names or len(compound_names)>20 or any(not re.fullmatch(r'[\w -]{3,60}',x) for x in compound_names):raise ValueError('R39_QUERY_SAFE_REGISTRY_NAMES_REQUIRED')
 compound='('+' OR '.join('TITLE_ABS:"'+x+'"' for x in compound_names)+')';model='(TITLE_ABS:macrophage OR TITLE_ABS:"RAW264.7" OR TITLE_ABS:"RAW 264.7" OR TITLE_ABS:BMDM OR TITLE_ABS:"THP-1")';atp='(TITLE_ABS:"ATP content" OR TITLE_ABS:"intracellular ATP" OR TITLE_ABS:"ATP levels" OR TITLE_ABS:"cellular ATP")';tail=' AND OPEN_ACCESS:Y AND NOT PUB_TYPE:review'
 return [dict(stage=1,query=model+' AND '+compound+' AND '+atp+tail,scope='TITLE_ABSTRACT_SCREENING_NOT_TESTED_COMPOUND_PROOF'),dict(stage=2,query=model+' AND '+compound+' AND ("ATP content" OR "ATP assay" OR "intracellular ATP")'+tail,scope='EXPLICIT_FULLTEXT_ATP_FALLBACK_LOWER_CONFIDENCE')]

def read_selection(file,expected_sha):
 p=Path(file)
 if digest(p.read_bytes())!=expected_sha:raise ValueError('R39_SELECTION_SHA_DRIFT')
 d=json.loads(p.read_text());rows=d.get('selected',[])
 if d.get('schema')!='asterloom.macrophage-selection/1' or not 20<=len(rows)<=50:raise ValueError('R39_FROZEN_SELECTION_20_TO_50')
 ids=[r.get('pmcid') for r in rows]
 if any(not re.fullmatch(r'PMC[0-9]+',str(x)) for x in ids) or len(set(ids))!=len(ids):raise ValueError('R39_SELECTION_IDENTITIES_INVALID_OR_DUPLICATE')
 if any(r.get('route') not in ('CORE_SCREENING_CANDIDATE','MACROPHAGE_COMPOUND_ASSAY_REFERENCE_PENDING') for r in rows):raise ValueError('R39_NONCORE_SELECTION_DENIED')
 return d

def targeted_task(home,selection_file,selection_sha,registry_dir,max_requests=40,si_plan=None,si_sha=None):
 from .r38_dataset import create_task
 if not 1<=max_requests<=40:raise ValueError('R39_MACROPHAGE_TASK_MAX_40_REQUESTS')
 selection=read_selection(selection_file,selection_sha);reg=registry(registry_dir)
 if reg['file_SHA']!=selection['SM297_file_SHA']:raise ValueError('R39_REGISTRY_VERSION_DRIFT')
 # Recompute metadata eligibility; a hand-written core label is not trusted.
 for x in selection['selected']:
  if score(x['hit'],reg)['route'] not in ('CORE_SCREENING_CANDIDATE','MACROPHAGE_COMPOUND_ASSAY_REFERENCE_PENDING'):raise ValueError('R39_SELECTION_METADATA_SCREEN_NO_LONGER_MATCHES')
 query=' OR '.join('PMCID:'+x['pmcid'] for x in selection['selected'])
 task=create_task(home,'small_molecule_atp_content_v1',len(selection['selected']),max_requests)
 p=Path(task['path']);draft=json.loads(p.read_text());draft.update(query_preview=query,selection_contract='asterloom.macrophage-selection/1',selection_file=str(Path(selection_file).resolve()),selection_sha256=selection_sha,selected_pmcids=[x['pmcid'] for x in selection['selected']],scientific_task_variant='macrophage_direct_atp_content/1',SM297_registry=str(Path(registry_dir).resolve()),SM297_file_SHA=reg['file_SHA'],core_gate_requires_matched_control=True,metadata_screen_not_scientific_approval=True)
 if si_plan or si_sha:
  from .r40_si import read_selection as read_si_selection
  if not si_plan or not si_sha:raise ValueError('SI_SELECTION_FILE_AND_SHA_REQUIRED')
  draft['selected_supplements']=read_si_selection(si_plan,si_sha,draft['selected_pmcids'])
 p.write_text(json.dumps(draft,ensure_ascii=False,indent=2)+'\n');return dict(draft,path=str(p),sha256=digest(p.read_bytes()))

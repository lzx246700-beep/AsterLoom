"""Private portable source evidence, with no DB, credentials or executable code.
Interface ideas: AIPOCH reproducibility evidence, independently implemented.
"""
from pathlib import Path
from contextlib import closing
import hashlib,json,os,sqlite3,zipfile
from . import __version__
from .r30_ledger import db_path,digest
from .source_intake import original_metadata
from .r23_profiles import SourceProjector,NAMES,load,contract

def encode(d):return (json.dumps(d,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()

def export(home,papers,output):
 if not 1<=len(papers)<=10 or len(papers)!=len(set(papers)):raise ValueError('R32_ONE_TO_TEN_DISTINCT_PAPERS')
 out=Path(output).expanduser().resolve()
 if out.exists():raise ValueError('R32_EXPORT_REFUSES_OVERWRITE')
 db=db_path(home);before=digest(db.read_bytes());entries={};assets=[];external=set()
 def portable(value):
  if isinstance(value,dict):return {k:portable(v) for k,v in value.items()}
  if isinstance(value,list):return [portable(v) for v in value]
  if isinstance(value,str) and value.startswith(('/Users/','/home/','/private/')):
   if value in paths:return paths[value]
   external.add(digest(value));return 'UNBUNDLED_EXTERNAL_REFERENCE'
  return value
 paths={}
 with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row
  for paper in papers:
   sources=c.execute("select * from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml') order by original_sha",(paper,)).fetchall()
   if not sources:raise ValueError('R32_AUTHOR_NATIVE_XML_REQUIRED:'+paper)
   for a in sources:
    raw=Path(a['original_path']).read_bytes();irraw=Path(a['ir_path']).read_bytes()
    if digest(raw)!=a['original_sha'] or digest(irraw)!=a['ir_sha']:raise ValueError('R32_SOURCE_OR_IR_SHA_DRIFT')
    info=original_metadata(raw)
    if info['paper']!=paper or info['license_status']!='AUTHOR_NATIVE_CC_BY_VERIFIED':raise ValueError('R32_NATIVE_LICENSE_OR_IDENTITY_HOLD')
    source='sources/'+a['original_sha']+'.xml';irfile='ir/'+a['ir_sha']+'.portable.json';paths[a['original_path']]=source;paths[a['ir_path']]=irfile
    entries[source]=raw;ir=json.loads(irraw);ir['asset']['path']=source;ir=portable(ir);entries[irfile]=encode(ir)
    assets.append(dict(paper=paper,doi=info['doi'],source=source,source_sha256=a['original_sha'],license=info['license'],portable_ir=irfile,original_ir_sha256=a['ir_sha'],portable_ir_sha256=digest(entries[irfile]),backend=a['backend'],IR_derivation='Only absolute asset/source paths redacted; original IR SHA retained as reference'))
 records=[];projector=SourceProjector(home)
 try:
  for name in NAMES:records.extend(projector.project(name,papers=papers))
 finally:projector.close()
 included={a['source_sha256'] for a in assets}
 # Preserve SI/other source omissions explicitly; never silently call them verified.
 for row in records:
  row=portable(row);row['bundle_source_included']=row['provenance']['author_sha256'] in included
  entries.setdefault('observations.jsonl',b'');entries['observations.jsonl']+=(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode()
 if not records:entries['observations.jsonl']=b''
 entries['contract.json']=encode(contract())
 for name in NAMES:entries['profiles/'+name+'.json']=encode(load(name,home).manifest)
 checklist=dict(schema='asterloom.independent-review-packet/1',scientific_review_status='AWAITING_INDEPENDENT_SCIENTIFIC_REVIEW',reviewer_identity=None,human_signature=None,precision=None,recall=None,F1=None,Gold=0,questions=['Confirm original research and independence of study','Verify molecule identity and actual tested form','Check source-scoped dose, time, model, assay and normalization','Distinguish content, ATP/min, ATP-linked OCR and ATPase','Verify treatment-control pairing and native numeric source','For covalent materials verify bond, batch and measured composition'],machine_integrity_is_not_scientific_approval=True)
 entries['REVIEW_PACKET.json']=encode(checklist)
 after=digest(db.read_bytes())
 if before!=after:raise ValueError('R32_DATABASE_CHANGED_DURING_EXPORT_RETRY_SNAPSHOT')
 total=sum(map(len,entries.values()))
 if total>64000000 or len(entries)>100:raise ValueError('R32_BUNDLE_SIZE_BUDGET')
 manifest=dict(schema='asterloom.evidence-bundle/1',core_version=__version__,source_database_sha256=before,papers=papers,assets=assets,observation_count=len(records),unbundled_external_reference_count=len(external),file_table={n:dict(sha256=digest(b),bytes=len(b)) for n,b in sorted(entries.items())},scientific_state='SOURCE_ONLY_UNAPPROVED',Gold=0,executes_code=False,contains_scientific_database=False)
 payload=encode(manifest);entries['MANIFEST.json']=payload
 out.parent.mkdir(parents=True,exist_ok=True);temp=out.with_suffix(out.suffix+'.part')
 try:
  with zipfile.ZipFile(temp,'x',zipfile.ZIP_DEFLATED) as z:
   for n,b in sorted(entries.items()):z.writestr(n,b)
  os.replace(temp,out)
 finally:temp.unlink(missing_ok=True)
 return dict(status='R32_PRIVATE_SOURCE_EVIDENCE_BUNDLE_EXPORTED',path=str(out),sha256=digest(out.read_bytes()),manifest_sha256=digest(payload),source_database_sha256=before,papers=len(papers),observations=len(records),assets=len(assets),unbundled_external_references=len(external),Gold=0)

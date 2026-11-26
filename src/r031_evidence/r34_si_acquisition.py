"""Unified bounded official SI formats on existing R30/R17 source tables.
No publisher key, no model, no alternate science DB; native bytes stay source-only.
"""
from pathlib import Path,PurePosixPath
from contextlib import closing
import hashlib,io,json,re,sqlite3,stat,time,urllib.parse,zipfile
from . import repository as repo
from . import r30_ledger as L
from .r16_mainchain import parse_source
from .source_intake import original_metadata
BUCKET='pmc-oa-opendata.s3.amazonaws.com'
FORMATS=('.xlsx','.csv','.zip','.pdf')

def media_link(uri,pmcid,version,filename):
 u=urllib.parse.urlsplit(uri);q=urllib.parse.parse_qs(u.query);md5=q.get('md5',[None])[0]
 if u.scheme!='s3' or u.netloc!='pmc-oa-opendata' or not u.path.startswith('/'+pmcid+'.'+str(version)+'/') or urllib.parse.unquote(u.path.rsplit('/',1)[-1])!=filename or not md5 or not re.fullmatch('[a-fA-F0-9]{32}',md5):return None
 return 'https://'+BUCKET+urllib.parse.quote(urllib.parse.unquote(u.path),safe='/._-')+'?md5='+md5,md5.lower()

def zip_inventory(raw):
 if not zipfile.is_zipfile(io.BytesIO(raw)):raise ValueError('SI_ZIP_MAGIC_HOLD')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  entries=z.infolist()
  if len(entries)>80 or sum(i.file_size for i in entries)>30000000:raise ValueError('SI_ZIP_EXPANSION_OR_MEMBER_BUDGET')
  names=set();out=[]
  for i in entries:
   name=i.filename.replace('\\','/');p=PurePosixPath(name)
   if p.is_absolute() or '..' in p.parts or re.match('^[A-Za-z]:',name) or stat.S_ISLNK(i.external_attr>>16) or name in names:raise ValueError('SI_ZIP_UNSAFE_OR_DUPLICATE_MEMBER')
   names.add(name)
   if i.is_dir():continue
   ext=p.suffix.lower();allowed=ext in ('.pdf','.csv','.xlsx','.txt','.png','.jpg','.jpeg','.tif','.tiff')
   b=z.read(i) if allowed else None;out.append(dict(name=name,bytes=i.file_size,sha256=L.digest(b) if b is not None else None,source_file_supported=allowed,state='SOURCE_ONLY_NO_EXECUTION' if allowed else 'SOURCE_UNSUPPORTED',nested_archive=ext=='.zip'))
  return out

def acquire_one(home,item,reader,worker_python=None,formats=FORMATS):
 """reader is an explicit bounded official HTTP adapter; caller owns budgets."""
 root=Path(home).resolve();state=root if root.name=='state' else root/'state';parent=item['parent_asset'];path=Path(parent['original_path']);payload=path.read_bytes()
 if L.digest(payload)!=parent['original_sha']:raise ValueError('SI_PARENT_ORIGINAL_SHA_DRIFT')
 info=original_metadata(payload)
 if info['license_status']!='AUTHOR_NATIVE_CC_BY_VERIFIED':return dict(paper_id=item['paper_id'],state='LICENSE_HOLD',reason='NATIVE_PARENT_CC_BY_NOT_VERIFIED')
 candidates=[]
 for s in item['declared_SI']:
  name=urllib.parse.unquote(str(s.get('href_literal') or '').rsplit('/',1)[-1]);ext=Path(name).suffix.lower()
  if ext in formats and re.fullmatch(r'[A-Za-z0-9_.+ -]{3,180}',name):candidates.append((formats.index(ext),name,s))
 if not candidates:return dict(paper_id=item['paper_id'],paper_key=item['paper_key'],state='SI_LOCATOR_OR_FORMAT_HOLD',reason='NO_SUPPORTED_DECLARED_FILENAME',declared=len(item['declared_SI']))
 pmcid=item['metadata_identity'].get('pmcid')
 if not pmcid or not re.fullmatch('PMC[0-9]+',pmcid):return dict(paper_id=item['paper_id'],state='SI_LOCATOR_OR_FORMAT_HOLD',reason='PUBLISHER_DOI_ADAPTER_REQUIRED')
 meta=None;meta_reply=None;version=None;http=[]
 for v in (1,2,3):
  for url in [f'https://{BUCKET}/{pmcid}.{v}/{pmcid}.{v}.json',f'https://{BUCKET}/metadata/{pmcid}.{v}.json']:
   r=reader(url,4000000);http.append({k:x for k,x in r.items() if k not in ('body','headers')})
   if r['status']!=200:continue
   try:d=json.loads(r['body'])
   except ValueError:continue
   if d.get('pmcid')!=pmcid or int(d.get('version',0))!=v or str(d.get('doi') or '').lower()!=str(info['doi'] or '').lower():continue
   if d.get('is_retracted') or d.get('license_code')!='CC BY':return dict(paper_id=item['paper_id'],state='LICENSE_HOLD',reason='OFFICIAL_VERSION_LICENSE_OR_RETRACTION_HOLD',http=http)
   native_md5=urllib.parse.parse_qs(urllib.parse.urlsplit(d.get('xml_url','')).query).get('md5',[None])[0]
   if not native_md5 or hashlib.md5(payload).hexdigest()!=native_md5:continue
   meta=d;meta_reply=r;version=v;break
  if meta:break
 if not meta:return dict(paper_id=item['paper_id'],state='SI_VERSION_HOLD',reason='NO_EXACT_PARENT_XML_MD5_AND_DOI_VERSION_MATCH',http=http)
 chosen=None
 for _,name,s in sorted(candidates,key=lambda x:(x[0],x[1])):
  candidate=next((m for m in (media_link(uri,pmcid,version,name) for uri in meta.get('media_urls',[])) if m),None)
  if candidate:chosen=(name,s,candidate);break
 if not chosen:return dict(paper_id=item['paper_id'],state='SI_LOCATOR_HOLD',reason='DECLARED_FILENAME_NOT_IN_EXACT_VERSION_OFFICIAL_MEDIA',http=http)
 name,s,(url,expected_md5)=chosen;reply=reader(url,12500000);http.append({k:x for k,x in reply.items() if k not in ('body','headers')})
 if reply['status']!=200:return dict(paper_id=item['paper_id'],state='SI_ACCESS_HOLD',reason=reply.get('reason'),http=http)
 blob=reply['body'];ext=Path(name).suffix.lower()
 if hashlib.md5(blob).hexdigest()!=expected_md5:raise ValueError('SI_OFFICIAL_MEDIA_MD5_MISMATCH')
 if ext in ('.doc','.docx','.tif','.tiff'):
  from .r40_si import check_document
  check_document(blob,ext)
 inventory=None
 if ext=='.pdf' and not blob.lstrip().startswith(b'%PDF-'):raise ValueError('SI_PDF_MAGIC_HOLD')
 if ext=='.zip':inventory=zip_inventory(blob)
 if ext=='.xlsx':
  if not zipfile.is_zipfile(io.BytesIO(blob)):raise ValueError('SI_XLSX_MAGIC_HOLD')
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
   if sum(i.file_size for i in z.infolist())>30000000 or any('vbaproject' in i.filename.lower() or '..' in PurePosixPath(i.filename).parts for i in z.infolist()):raise ValueError('SI_XLSX_MACRO_OR_EXPANSION_HOLD')
 if ext in ('.xml','.nxml') and not blob.lstrip().startswith((b'<?xml',b'<')):raise ValueError('SI_XML_MAGIC_HOLD')
 if ext in ('.png',) and not blob.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('SI_IMAGE_MAGIC_HOLD')
 if ext in ('.jpg','.jpeg') and not blob.startswith(b'\xff\xd8'):raise ValueError('SI_IMAGE_MAGIC_HOLD')
 if ext=='.csv' and (not blob or b'\x00' in blob[:256]):raise ValueError('SI_CSV_TEXT_HOLD')
 sha=L.digest(blob);folder=state/'r33_official_si';folder.mkdir(exist_ok=True);dst=folder/(sha+ext)
 if dst.exists() and L.digest(dst.read_bytes())!=sha:raise ValueError('SI_IMMUTABLE_SOURCE_HASH_PATH_CONFLICT')
 if not dst.exists():
  with dst.open('xb') as f:f.write(blob)
 with closing(L.connect(root)) as c:
  parent_version=c.execute('select asset_id from r30_source_version where paper_id=? and sha256=?',(item['paper_id'],parent['original_sha'])).fetchone();before=c.execute('select count(*) from r17_si_attachment where parent_author_sha=? and official_original_sha=?',(parent['original_sha'],sha)).fetchone()[0]
 asset=L.add_source(root,item['paper_id'],url,sha,dst,reply.get('type'),'CC BY',version=f'PMC-{version}-SI:{name}',parent=parent_version[0] if parent_version else parent['parsed_asset_id'])
 parsed=None;child=None
 if ext in ('.xlsx','.csv','.xml','.nxml'):
  parsed=parse_source(state,dst,item['paper_key'],worker_python=worker_python)
  with closing(sqlite3.connect(state/'science.sqlite3')) as c:
   found=c.execute('select parsed_asset_id from r16_parsed_asset where paper_key=? and original_sha=?',(item['paper_key'],sha)).fetchone();child=found[0] if found else None
 if not before:
  repo.configure(state);c=repo.connect(state/'science.sqlite3')
  try:
   c.execute('BEGIN IMMEDIATE');repo.put(c,'r17_si_attachment',dict(link_id='r33:si:'+L.digest(s['supplement_key']+'|'+sha)[:32],supplement_key=s['supplement_key'],parent_parsed_asset_id=parent['parsed_asset_id'],parent_author_sha=parent['original_sha'],official_provider='PMC_S3_2026_CC_BY',provider_file_url=url,official_original_sha=sha,local_original_path=str(dst),mime_type=reply.get('type'),child_parsed_asset_id=child,permission_state='PARENT_CC_BY_AND_MEDIA_MD5_VERIFIED',pipeline_state='SOURCE_IR_LINKED' if child else 'SOURCE_PARSER_HOLD',note='R33 exact parent XML MD5 and DOI/version; metadata SHA='+meta_reply['sha'],training_approved=0));c.commit()
  except BaseException:c.rollback();raise
  finally:c.close()
 return dict(paper_id=item['paper_id'],paper_key=item['paper_key'],state='SI_SOURCE_VERIFIED',new_attachment=not bool(before),format=ext,official_filename=name,path=str(dst),sha256=sha,official_md5=expected_md5,official_metadata_sha=meta_reply['sha'],parent_xml_md5_verified=True,parent_source_sha=parent['original_sha'],version=version,asset_id=asset,worker_IR=parsed,parser_state='SOURCE_IR_LINKED' if child else 'SOURCE_PARSER_HOLD',ZIP_inventory=inventory,http=http,scientific_measurements_created=0,Gold=0)

def existing_paper_item(home,paper_key):
 with closing(sqlite3.connect('file:'+str(L.db_path(home))+'?mode=ro',uri=True)) as c:
  c.row_factory=sqlite3.Row;p=c.execute('select * from paper_ref where paper_key=?',(paper_key,)).fetchone()
  if not p:raise ValueError('R34_EXISTING_PAPER_REQUIRED')
  w=c.execute("select w.* from r30_work w where w.science_paper_key=? or w.paper_id in (select paper_id from r30_alias where (namespace='pmcid' and value=?) or (namespace='doi' and value=?)) order by paper_id",(paper_key,p['pmcid'],str(p['doi'] or '').lower())).fetchall()
  if len(w)!=1:raise ValueError('R34_UNAMBIGUOUS_EXISTING_ACQUISITION_ID_REQUIRED')
  a=c.execute("select * from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml') order by parsed_asset_id limit 1",(paper_key,)).fetchone()
  if not a:raise ValueError('R34_PARSED_NATIVE_PARENT_REQUIRED')
  refs=[dict(r) for r in c.execute('select supplement_key,href_literal,original_locator from r16_source_supplement where parsed_asset_id=? order by supplement_key',(a['parsed_asset_id'],))]
  return dict(paper_id=w[0]['paper_id'],paper_key=paper_key,parent_asset=dict(a),metadata_identity=dict(pmcid=p['pmcid'],doi=p['doi']),declared_SI=refs)

def acquire(home,paper_key,max_requests=3,worker_python=None):
 from .r30_http import request
 import uuid
 if not 1<=max_requests<=4:raise ValueError('R34_INTERACTIVE_MAX4_REQUESTS')
 item=existing_paper_item(home,paper_key);receipts=[];calls=0;started=time.time()
 def reader(url,limit):
  nonlocal calls
  if calls>=max_requests or time.time()-started>90:raise RuntimeError('R34_BOUNDED_SI_BUDGET_HOLD')
  calls+=1;r=request(home,url,limit=limit,timeout=15);receipts.append({k:v for k,v in r.items() if k not in ('body','headers')});return r
 try:result=acquire_one(home,item,reader,worker_python)
 except Exception as e:result=dict(paper_id=item['paper_id'],paper_key=paper_key,state='SI_HOLD',reason=type(e).__name__+':'+str(e)[:500])
 result.setdefault('paper_key',paper_key);result.update(requests=calls,wall_seconds=time.time()-started,HTTP_receipts=receipts,Gold=0)
 root=Path(home);root=root.parent if root.name=='state' else root;folder=root/'logs/r34_si';folder.mkdir(parents=True,exist_ok=True);out=folder/(uuid.uuid4().hex+'.json');out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');result['receipt']=str(out);return result

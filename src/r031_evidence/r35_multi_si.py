"""Multi-attachment official SI, bounded and source-only, existing R30/R17/R16.
ZIP members retain archive-parent chain; no scripts, macros, nested ZIP execution.
"""
from pathlib import Path,PurePosixPath
from contextlib import closing
import io,json,re,sqlite3,stat,time,zipfile
from . import r30_ledger as L
from .r34_si_acquisition import acquire_one,existing_paper_item
from .r34_online import save
from .r16_mainchain import parse_source
FORMATS=('.xlsx','.csv','.xml','.nxml','.zip','.pdf','.html','.htm','.png','.jpg','.jpeg')

def archive_members(raw,max_ratio=200):
 out=[];seen=set()
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  infos=z.infolist()
  if len(infos)>80 or sum(i.file_size for i in infos)>30000000:raise ValueError('R35_ZIP_TOTAL_BUDGET')
  for i in infos:
   name=i.filename.replace('\\','/');p=PurePosixPath(name)
   if p.is_absolute() or '..' in p.parts or name in seen or re.match('^[A-Za-z]:',name) or stat.S_ISLNK(i.external_attr>>16) or i.flag_bits&1:raise ValueError('R35_UNSAFE_ENCRYPTED_OR_DUPLICATE_ZIP_MEMBER')
   seen.add(name)
   if i.file_size/max(1,i.compress_size)>max_ratio:raise ValueError('R35_ZIP_COMPRESSION_RATIO_HOLD')
   if i.is_dir():continue
   supported=p.suffix.lower() in FORMATS and p.suffix.lower()!='.zip'
   out.append(dict(name=name,supported=supported,bytes=i.file_size,sha256=L.digest(z.read(i)) if supported else None,reason=None if supported else 'CODE_OR_NESTED_ARCHIVE_NOT_EXTRACTED'))
 return out

def unpack(home,result,paper_id,paper_key,worker_python=None):
 root=Path(home);state=L.db_path(root).parent;raw=Path(result['path']).read_bytes()
 if L.digest(raw)!=result['sha256']:raise ValueError('R35_ARCHIVE_ORIGINAL_SHA_DRIFT')
 inventory=archive_members(raw);out=[];folder=state/'r35_zip_members';folder.mkdir(exist_ok=True)
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  for member in inventory:
   if not member['supported']:out.append(member);continue
   blob=z.read(member['name']);ext=Path(member['name']).suffix.lower();dst=folder/(member['sha256']+ext)
   if not dst.exists():
    with dst.open('xb') as f:f.write(blob)
   if L.digest(dst.read_bytes())!=member['sha256']:raise ValueError('R35_MEMBER_SHA_DRIFT')
   aid=L.add_source(root,paper_id,'zip-member:'+result['sha256']+'/'+member['name'],member['sha256'],dst,None,'DERIVED_FROM_EXACT_VERSION_CC_BY_OFFICIAL_SI',version='ARCHIVE_MEMBER_BYTES',parent=result['asset_id']);parsed=None
   if ext=='.xlsx':
    with zipfile.ZipFile(io.BytesIO(blob)) as nested:
     if any('vbaproject' in i.filename.lower() or '/activex/' in i.filename.lower() for i in nested.infolist()):
      out.append(dict(member,path=str(dst),parent_archive_sha256=result['sha256'],asset_id=aid,IR=None,science_state='SOURCE_ONLY_HOLD',reason='XLSX_MACRO_OR_ACTIVEX_HOLD_NOT_PARSED'));continue
   if ext in ('.xlsx','.csv','.xml','.nxml'):
    try:parsed=parse_source(state,dst,paper_key,worker_python=worker_python)
    except Exception as e:parsed=dict(state='PARSE_HOLD',reason=type(e).__name__+':'+str(e)[:200])
   out.append(dict(member,path=str(dst),parent_archive_sha256=result['sha256'],asset_id=aid,IR=parsed,science_state='SOURCE_ONLY_HOLD'))
 return out

def acquire_many(home,item,reader,*,max_files=3,worker_python=None):
 if not 1<=max_files<=5:raise ValueError('R35_MAX5_FILES_PER_PAPER')
 cache={};attempts=[];names=set()
 def memo(url,limit):
  if url in cache:return dict(cache[url],cache_replay_for_same_exact_version=True)
  r=reader(url,limit)
  if r['status']==200 and url.split('?',1)[0].endswith('.json'):cache[url]=r
  return r
 candidates=[]
 for s in item['declared_SI']:
  from urllib.parse import unquote
  name=unquote(str(s.get('href_literal') or '').rsplit('/',1)[-1]);ext=Path(name).suffix.lower()
  if ext in FORMATS and name not in names:names.add(name);candidates.append((FORMATS.index(ext),name,s))
 for _,name,s in sorted(candidates)[:max_files]:
  single=dict(item,declared_SI=[s]);t=time.time()
  try:
   result=acquire_one(home,single,memo,worker_python,formats=FORMATS)
   if result.get('state')=='SI_SOURCE_VERIFIED' and result['format']=='.zip':
    try:result['members']=unpack(home,result,item['paper_id'],item['paper_key'],worker_python)
    except Exception as e:result['member_state']='ARCHIVE_HOLD:'+type(e).__name__+':'+str(e)[:250]
  except Exception as e:result=dict(state='SI_HOLD',reason=type(e).__name__+':'+str(e)[:300])
  result.update(paper_id=item['paper_id'],paper_key=item['paper_key'],declared_filename=name,seconds=time.time()-t);attempts.append(result)
 return dict(paper_key=item['paper_key'],eligible_declared_filenames=len(candidates),selected=len(attempts),attempts=attempts,source_availability='LOCATOR_OR_UNSUPPORTED_FORMAT_HOLD' if not candidates else 'PER_FILE_RESULTS',Gold=0)

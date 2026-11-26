"""Bounded official-host HTTP, durable host pacing and atomic source downloads.
No background loop; Retry-After is persisted and returned to the work ledger.
"""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import hashlib,json,os,shutil,time,urllib.request,urllib.error,urllib.parse,uuid
from .r30_ledger import connect,db_path,canonical_json,digest
PLOS_CORPUS_HOST='corpus-prod.f505aadad1e46610c8f8563037eb3c4b.r2.cloudflarestorage.com'
HOSTS={PLOS_CORPUS_HOST:('/10.1371/journal.',),'www.ebi.ac.uk':('/europepmc/webservices/rest/',),'api.openalex.org':('/works','/rate-limit'),'api.crossref.org':('/works',),'journals.plos.org':('/plosone/article/file','/plosbiology/article/file','/ploscompbiol/article/file','/plosgenetics/article/file','/plosmedicine/article/file','/plospathogens/article/file'),'api.unpaywall.org':('/v2/',),'api.semanticscholar.org':('/graph/v1/',),'pmc-oa-opendata.s3.amazonaws.com':('/metadata/PMC','/PMC')}
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args):return None

def validate_url(url):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443) or not any(u.path.startswith(p) for p in HOSTS[u.hostname]):raise ValueError('R30_UNAPPROVED_OFFICIAL_SOURCE_URL')
 if len(url)>5000 or any(x in urllib.parse.parse_qs(u.query) for x in ['api_key','key','token']):raise ValueError('R30_NO_CREDENTIALS_IN_LOGGED_URL')
 return u

def retry_delay(value,now=None,attempt=1,seed=''):
 now=time.time() if now is None else now
 try:
  if value is not None:return max(0,min(86400,float(value)))
 except ValueError:
  try:return max(0,min(86400,parsedate_to_datetime(value).timestamp()-now))
  except (ValueError,TypeError,OverflowError):pass
 jitter=int(digest(seed+str(attempt))[:8],16)/2**32
 return min(3600,2**min(attempt,10)+jitter)

def public_url(url):
 u=urllib.parse.urlsplit(url)
 query=urllib.parse.urlencode([(k,v) for k,v in urllib.parse.parse_qsl(u.query) if not k.lower().startswith('x-amz-')])
 return urllib.parse.urlunsplit((u.scheme,u.netloc,u.path,query,''))

def classify(status):
 if status==200:return 'OK'
 if status==429:return 'HTTP_RATE_LIMIT'
 if status==0 or status in (408,425) or status>=500:return 'HTTP_TRANSIENT'
 if status in (401,403):return 'HTTP_AUTH_OR_ACCESS'
 if status==404:return 'HTTP_NOT_FOUND'
 return 'HTTP_FORMAT_OR_POLICY'

def _setup(c):
 c.execute('''CREATE TABLE IF NOT EXISTS r30_host(host TEXT PRIMARY KEY,next_at REAL NOT NULL DEFAULT 0,blocked_until REAL NOT NULL DEFAULT 0,active_token TEXT,active_until REAL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS r30_http_receipt(request_id TEXT PRIMARY KEY,url TEXT NOT NULL,status INTEGER NOT NULL,taxonomy TEXT NOT NULL,sha256 TEXT,path TEXT,bytes INTEGER NOT NULL,retry_at REAL,elapsed REAL NOT NULL,headers_json TEXT NOT NULL,error TEXT,at REAL NOT NULL)''')

def _reserve(home,host,clock=time.time,min_interval=0.55):
 now=clock()
 with closing(connect(home)) as c:
  _setup(c);c.execute('BEGIN IMMEDIATE')
  try:
   c.execute('INSERT OR IGNORE INTO r30_host(host) VALUES(?)',(host,));row=c.execute('SELECT * FROM r30_host WHERE host=?',(host,)).fetchone()
   until=max(row['next_at'],row['blocked_until'],row['active_until'] if row['active_token'] and row['active_until']>now else 0)
   if until>now:c.rollback();return None,until
   token=uuid.uuid4().hex;c.execute('UPDATE r30_host SET next_at=?,active_token=?,active_until=? WHERE host=?',(now+min_interval,token,now+45,host));c.commit();return token,None
  except BaseException:c.rollback();raise

class CurlTransport:
 """Optional platform TLS transport; no redirects, no insecure flags or keys."""
 def __init__(self,limit,diagnostics_dir=None):self.limit=limit;self.diagnostics_dir=diagnostics_dir
 def __call__(self,req,timeout=15):
  import subprocess,tempfile,email,io
  if req.has_header('Authorization'):raise ValueError('R30_CURL_NO_AUTH_HEADER_IN_PROCESS_ARGUMENTS')
  with tempfile.TemporaryDirectory(prefix='asterloom_https_') as t:
   head=Path(t)/'headers';body=Path(t)/'body'
   p=subprocess.run(['curl']+(['--http1.1'] if os.environ.get('R031_CURL_HTTP1')=='1' else [])+['--silent','--show-error','--proto','=https','--max-redirs','0','--connect-timeout',str(min(timeout,10)),'--max-time',str(timeout),'--max-filesize',str(self.limit),'--dump-header',str(head),'--output',str(body),req.full_url],capture_output=True,text=True)
   if p.returncode:
    if p.returncode==63:raise ValueError('BODY_SIZE_LIMIT')
    detail='PLATFORM_CURL_'+str(p.returncode)+':'+p.stderr[-300:].replace('\n',' ')
    if self.diagnostics_dir:
     folder=Path(self.diagnostics_dir);folder.mkdir(parents=True,exist_ok=True);partial=body.read_bytes() if body.exists() else b'';headers_raw=head.read_bytes() if head.exists() else b'';record=dict(state='PARTIAL_TRANSPORT_RESPONSE_NOT_SOURCE_EVIDENCE',url=public_url(req.full_url),exit_code=p.returncode,partial_bytes=len(partial),partial_sha256=digest(partial),headers=headers_raw.decode(errors='replace')[-4000:],stderr=p.stderr[-1000:]);name=uuid.uuid4().hex
     (folder/(name+'.json')).write_text(json.dumps(record,indent=2)+'\n')
     if partial:(folder/(name+'.partial')).write_bytes(partial)
    raise urllib.error.URLError(detail)
   blocks=[x for x in head.read_bytes().split(b'\r\n\r\n') if x.startswith(b'HTTP/')]
   if not blocks:raise urllib.error.URLError('CURL_NO_HTTP_HEADERS')
   lines=blocks[-1].split(b'\r\n',1);status=int(lines[0].split()[1]);headers=email.message_from_bytes(lines[1])
   class Response(io.BytesIO):pass
   result=Response(body.read_bytes() if body.exists() else b'');result.status=status;result.headers=headers
   return result

def request(home,url,*,limit=8000000,timeout=15,attempt=1,transport=None,clock=time.time,wait=time.sleep):
 u=validate_url(url)
 if not 1<=limit<=20000000 or not 1<=timeout<=30:raise ValueError('R30_HTTP_EXPLICIT_BUDGET_REQUIRED')
 token,retry=_reserve(home,u.hostname,clock)
 if not token and retry-clock()<=1.2:
  wait(max(0,retry-clock()));token,retry=_reserve(home,u.hostname,clock)
 if not token:return dict(status=429,body=b'',sha=None,type=None,url=url,reason='LOCAL_HOST_THROTTLE',retry_at=retry,bytes=0)
 started=clock();rid=uuid.uuid4().hex;root=db_path(home).parent;folder=root/'r30_http';folder.mkdir(exist_ok=True)
 temp=folder/('.'+rid+'.part');headers={};code=0;mime=None;err=None;total=0;digestor=hashlib.sha256();target=None
 try:
  if shutil.disk_usage(root).free<max(1500*1024*1024,limit*3):raise ValueError('DISK_WATERMARK_HOLD')
  req=urllib.request.Request(url,headers={'User-Agent':'AsterLoom/0.28 bounded open research source acquisition','Accept-Encoding':'identity'})
  # No new account or key acquisition. Optional configured OpenAlex key stays
  # in an authorization header and is never persisted in source receipts.
  if u.hostname=='api.openalex.org' and os.environ.get('R031_OPENALEX_API_KEY'):
   req.add_header('Authorization','Bearer '+os.environ['R031_OPENALEX_API_KEY'])
  open_fn=transport or (CurlTransport(limit,folder/'transport_failures') if os.environ.get('R031_HTTP_TRANSPORT')=='curl' else urllib.request.build_opener(NoRedirect()).open)
  with open_fn(req,timeout=timeout) as response:
   code=response.status;mime=response.headers.get_content_type()
   headers={k:response.headers.get(k) for k in ['Content-Length','Retry-After','Content-Type','ETag','Last-Modified','X-RateLimit-Remaining','Location'] if response.headers.get(k) is not None}
   length=response.headers.get('Content-Length')
   if length and int(length)>limit:raise ValueError('BODY_SIZE_LIMIT')
   with temp.open('xb') as out:
    while True:
     chunk=response.read(min(65536,limit-total+1))
     if not chunk:break
     total+=len(chunk)
     if total>limit:raise ValueError('BODY_SIZE_LIMIT')
     out.write(chunk);digestor.update(chunk)
    out.flush();os.fsync(out.fileno())
   if length and total!=int(length):raise ValueError('TRUNCATED_CONTENT_LENGTH')
   if code==200:
    suffix='.json' if mime=='application/json' else '.xml' if mime in ('application/xml','text/xml') or temp.read_bytes()[:100].lstrip().startswith((b'<?xml',b'<article')) else '.bin'
    target=folder/(digestor.hexdigest()+suffix)
    if target.exists():
     if digest(target.read_bytes())!=digestor.hexdigest():raise ValueError('HASH_PATH_COLLISION')
     temp.unlink()
    else:os.replace(temp,target)
 except urllib.error.HTTPError as exc:
  code=exc.code;err='HTTP_'+str(code);headers={k:exc.headers.get(k) for k in ['Retry-After','Content-Type'] if exc.headers and exc.headers.get(k)}
 except (OSError,ValueError,TimeoutError) as exc:
  err=type(exc).__name__+':'+str(exc)[:250]
  if isinstance(exc,ValueError):code=413 if 'SIZE' in str(exc) else 422
  else:code=0
 finally:
  temp.unlink(missing_ok=True)
 taxonomy=classify(code);retry_at=(clock()+retry_delay(headers.get('Retry-After'),clock(),attempt,url)) if taxonomy in ('HTTP_TRANSIENT','HTTP_RATE_LIMIT') else None
 with closing(connect(home)) as c:
  _setup(c);c.execute('BEGIN IMMEDIATE')
  try:
   c.execute('UPDATE r30_host SET active_token=NULL,active_until=NULL,blocked_until=MAX(blocked_until,?) WHERE host=? AND active_token=?',(retry_at or 0,u.hostname,token))
   c.execute('INSERT INTO r30_http_receipt VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,public_url(url),code,taxonomy,digestor.hexdigest() if target else None,str(target) if target else None,total,retry_at,clock()-started,canonical_json({k:public_url(v) if k=='Location' else v for k,v in headers.items()}),err,clock()));c.commit()
  except BaseException:c.rollback();raise
 return dict(status=code,body=target.read_bytes() if target else b'',sha=digestor.hexdigest() if target else None,path=str(target) if target else None,type=mime,url=public_url(url),reason=err or taxonomy,retry_at=retry_at,bytes=total,request_id=rid,headers=headers)

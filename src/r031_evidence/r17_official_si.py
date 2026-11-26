"""R17 official PMC OA 2026 versioned S3 SI acquisition.

Follows already indexed JATS supplementary href -> official article-version
JSON -> exact media URL and checksum -> native Worker R16 source IR.
No publisher page scraping, no credentials, no trust in arbitrary URLs,
no license promotion or inferred scientific measurements.
"""
from __future__ import annotations
from pathlib import Path
import urllib.parse,urllib.request,urllib.error,hashlib,json,re,time,zipfile,io
from . import repository as repo
from .r16_mainchain import parse_source
BUCKET="pmc-oa-opendata.s3.amazonaws.com"
def digest(data):return hashlib.sha256(data).hexdigest()
class _NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,url):return None

def _official_get(url,limit=13000000):
 u=urllib.parse.urlsplit(url)
 if u.scheme!="https" or u.hostname!=BUCKET or u.username or u.password or u.port:
  raise ValueError("PMC_S3_HOST_NOT_OFFICIAL")
 if not (u.path.startswith("/metadata/PMC") or re.fullmatch(r"/PMC[0-9]+\.[0-9]+/[A-Za-z0-9_.+%-]+",u.path)):
  raise ValueError("PMC_S3_PATH_NOT_AUTHORIZED")
 if len(url)>1500:raise ValueError("PMC_S3_URL_LENGTH_HOLD")
 opener=urllib.request.build_opener(_NoRedirect())
 last=None
 for attempt in range(3):
  try:
   with opener.open(urllib.request.Request(url,headers={"User-Agent":"R031-SourceEvidence/0.17"}),timeout=18) as resp:
    data=resp.read(limit+1)
    if len(data)>limit:raise ValueError("PMC_SI_EXCEEDS_LIMIT")
    return data,resp.headers.get_content_type()
  except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,OSError) as exc:
   last=exc
   if isinstance(exc,urllib.error.HTTPError) and exc.code in (403,404):break
   time.sleep(0.5*(attempt+1))
 raise RuntimeError("PMC_S3_OFFICIAL_PROVIDER_HOLD:"+type(last).__name__+":"+str(last)[:90])

def _metadata(pmcid,reader=None):
 for v in (1,2,3):
  try:raw,_=(reader or _official_get)(f"https://{BUCKET}/metadata/{pmcid}.{v}.json",4000000)
  except RuntimeError:continue
  data=json.loads(raw)
  if data.get("pmcid")==pmcid and str(data.get("version"))==str(v):
   if str(data.get("license_code","")).strip() not in ("CC BY","CC0","CC BY-SA"):
    return data,raw,"SOURCE_LICENSE_NOT_PERMITTED"
   return data,raw,"LICENSED"
 raise RuntimeError("PMC_OFFICIAL_ARTICLE_VERSION_NOT_AVAILABLE_HOLD")

def _url_for_media(item,pmcid,version,filename):
 u=urllib.parse.urlsplit(item)
 if u.scheme!="s3" or u.netloc!="pmc-oa-opendata":return None
 prefix=f"/{pmcid}.{version}/"
 if not u.path.startswith(prefix):return None
 if urllib.parse.unquote(u.path.rsplit("/",1)[-1])!=filename:return None
 params=urllib.parse.parse_qs(u.query)
 md5=params.get("md5",[None])[0]
 if not md5 or not re.fullmatch(r"[a-fA-F0-9]{32}",md5):return None
 return f"https://{BUCKET}"+urllib.parse.quote(urllib.parse.unquote(u.path),safe="/._-")+'?md5='+md5,md5

def acquire_si(workspace,paper,max_files=2,reader=None):
 if not re.fullmatch(r"PMC[0-9]{4,12}",paper):raise ValueError("R17_VALID_PMCID_REQUIRED")
 if not 1<=max_files<=4:raise ValueError("R17_SI_MAX_4")
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 if not db.is_file():raise ValueError("EXISTING_CUMULATIVE_DATABASE_REQUIRED")
 repo.configure(home)
 c=repo.connect(db)
 try:
  parents=c.execute("""SELECT parsed_asset_id,original_sha,original_path FROM r16_parsed_asset
    WHERE paper_key=? AND source_format IN ('.xml','.nxml') ORDER BY parsed_asset_id LIMIT 1""",(paper,)).fetchall()
  if not parents:return {"status":"PARENT_AUTHOR_JATS_IR_NOT_FOUND_HOLD","paper":paper,"Gold":0}
  parent_id,parent_sha,parent_path=parents[0]
  if digest(Path(parent_path).read_bytes())!=parent_sha:
   raise RuntimeError("R17_PARENT_ORIGINAL_JATS_SHA_MISMATCH")
  refs=c.execute("""SELECT supplement_key,href_literal,original_locator FROM r16_source_supplement
    WHERE parsed_asset_id=? AND href_literal IS NOT NULL
    ORDER BY supplement_key""",(parent_id,)).fetchall()
 finally:c.close()
 refs=list({x[1]:x for x in reversed(refs) if x[1].lower().endswith((".xlsx",".csv")) and re.fullmatch(r"[A-Za-z0-9_.+-]{3,180}",x[1])}.values())
 if not refs:return {"status":"NO_SUPPORTED_NATIVE_SI_HREF","paper":paper,"Gold":0}
 meta,meta_raw,license_state=_metadata(paper,reader)
 meta_sha=digest(meta_raw);license_literal=str(meta.get("license_code"))
 if license_state!="LICENSED":
  return {"status":"PARENT_LICENSE_HOLD","paper":paper,"license":license_literal,"Gold":0}
 version=meta["version"]
 done=[]
 for key,filename,locator in refs[:max_files]:
  match=next((a for a in
    (_url_for_media(u,paper,version,filename) for u in meta.get("media_urls") or [])
    if a is not None),None)
  if match is None:
   done.append({"supplement_key":key,"href":filename,"state":"OFFICIAL_MEDIA_NOT_FOUND_HOLD"});continue
  url,expected_md5=match
  try:
   blob,mime=(reader or _official_get)(url,12500000)
   if hashlib.md5(blob).hexdigest().lower()!=expected_md5.lower():
    raise ValueError("PMC_MEDIA_OFFICIAL_MD5_MISMATCH")
   ext=Path(filename).suffix.lower()
   if ext==".xlsx":
    if not blob.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(io.BytesIO(blob)):
     raise ValueError("PMC_AUTHOR_SI_XLSX_MAGIC_HOLD")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
     if any(x.filename.startswith("/") or ".." in Path(x.filename).parts for x in z.infolist()):
      raise ValueError("PMC_SI_ZIP_TRAVERSAL_HOLD")
     if sum(x.file_size for x in z.infolist())>25000000:raise ValueError("PMC_SI_EXCEL_EXPAND_SIZE_HOLD")
   elif ext==".csv":
    # Preserve original publisher bytes and let Worker/codec bridge resolve
    # declared source rows (including cp1252 semicolon-delimited ACS SI).
    if not blob or b"\x00" in blob[:256]:raise ValueError("SI_NOT_TEXT_CSV")
   srcsha=digest(blob)
   archive=home/"r17_official_si"/paper
   archive.mkdir(parents=True,exist_ok=True)
   dst=archive/(Path(filename).stem+"_"+srcsha[:14]+ext)
   if dst.exists() and digest(dst.read_bytes())!=srcsha:
    raise RuntimeError("R17_SI_ARCHIVE_FILE_SHA_CONFLICT")
   if not dst.exists():dst.write_bytes(blob)
   parsed=parse_source(home,dst,paper)
   parsed_id=None
   conn=repo.connect(db)
   try:
    row=conn.execute("SELECT parsed_asset_id FROM r16_parsed_asset WHERE paper_key=? AND original_sha=?",
      (paper,srcsha)).fetchone()
    parsed_id=row[0] if row else None
    conn.execute("BEGIN IMMEDIATE")
    repo.put(conn,"r17_si_attachment",{
       "link_id":"r17:si:"+digest((key+"|"+srcsha).encode())[:32],
       "supplement_key":key,"parent_parsed_asset_id":parent_id,
       "parent_author_sha":parent_sha,"official_provider":"PMC_S3_2026_CC_BY",
       "provider_file_url":url,"official_original_sha":srcsha,
       "local_original_path":str(dst),"mime_type":mime,
       "child_parsed_asset_id":parsed_id,
       "permission_state":"PARENT_CC_BY_AND_MEDIA_MD5_VERIFIED",
       "pipeline_state":"SOURCE_IR_LINKED" if parsed_id else "SOURCE_PARSER_HOLD",
       "note":"PMC official metadata sha="+meta_sha+"; parent JATS href="+filename,
       "training_approved":0})
    conn.commit()
   except BaseException:conn.rollback();raise
   finally:conn.close()
   done.append({"supplement_key":key,"href":filename,"state":"SOURCE_IR_LINKED" if parsed_id else "SOURCE_PARSER_HOLD",
    "author_sha":srcsha,"parent_sha":parent_sha,"official_md5":expected_md5,
    "official_metadata_sha":meta_sha,"source_local_path":str(dst),"parsed_asset_id":parsed_id,
    "worker":parsed})
  except (ValueError,RuntimeError,UnicodeError,zipfile.BadZipFile) as exc:
   done.append({"supplement_key":key,"href":filename,"state":"SOURCE_HOLD","reason":str(exc)[:180]})
 return {"status":"R17_REAL_OFFICIAL_PMC_SOURCE_SI_ACQUISITION",
    "paper":paper,"parent_author_sha":parent_sha,
    "license":license_literal,"metadata_sha":meta_sha,
    "attachments":done,"Gold":0}

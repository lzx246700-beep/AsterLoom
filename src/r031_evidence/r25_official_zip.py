"""R25.1 lawful official PMC OA ZIP SI intake, source-only, no code execution.

Checks JATS linked .zip, parent original SHA, official CC BY metadata,
official media MD5, uncompressed limits and traversal/symlink safeguards.
Only .xlsx/.csv inner files are eligible for later original-cell parsing.
Never infers a Y from an image, PDF or figure caption.
"""
from __future__ import annotations
from pathlib import Path,PurePosixPath
import re,io,json,zipfile,hashlib,sqlite3,stat
from .r17_official_si import _metadata,_official_get,_url_for_media
def checksum(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def intake(home,paper,output=None,max_compressed=13000000):
 if not re.fullmatch(r"PMC[0-9]{4,12}",paper):raise ValueError("INVALID_PMCID")
 root=Path(home).expanduser().absolute()
 if root.name=="state":root=root.parent
 db=root/"state/science.sqlite3"
 con=sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True)
 try:
  a=con.execute("""SELECT parsed_asset_id,original_sha,original_path
   FROM r16_parsed_asset WHERE paper_key=? AND source_format IN ('.xml','.nxml')
   ORDER BY parsed_asset_id LIMIT 1""",(paper,)).fetchone()
  if not a:raise ValueError("R25_PARENT_JATS_NOT_INDEXED")
  source_id,parent_sha,parent_path=a
  if not Path(parent_path).is_file() or checksum(parent_path)!=parent_sha:
   raise ValueError("R25_PARENT_AUTHOR_JATS_SHA_MISMATCH")
  items=con.execute("""SELECT supplement_key,href_literal,original_locator
    FROM r16_source_supplement WHERE parsed_asset_id=?
    AND href_literal LIKE '%.zip' ORDER BY supplement_key LIMIT 5""",(source_id,)).fetchall()
  if not items:raise ValueError("NO_AUTHOR_ZIP_LINK_IN_JATS")
 finally:con.close()
 meta,raw,license_state=_metadata(paper)
 if license_state!="LICENSED":
  return {"status":"OFFICIAL_PARENT_LICENSE_HOLD","paper":paper,"Gold":0}
 version=int(meta["version"])
 output=Path(output or root/"r25_official_zip_si").absolute()
 output.mkdir(parents=True,exist_ok=True)
 reports=[]
 seen=set()
 for source_supplement_key,name,original_locator in items:
  if name in seen:continue
  seen.add(name)
  if not re.fullmatch(r"[A-Za-z0-9_.+-]{3,160}",name):continue
  candidate=next((x for x in (_url_for_media(u,paper,version,name)
      for u in meta.get("media_urls") or []) if x is not None),None)
  if candidate is None:
   reports.append({"author_filename":name,"state":"OFFICIAL_MEDIA_NOT_LINKED_HOLD"})
   continue
  url,expected_md5=candidate
  blob,typ=_official_get(url,max_compressed)
  if hashlib.md5(blob).hexdigest().lower()!=expected_md5.lower():
   raise RuntimeError("R25_OFFICIAL_ZIP_MEDIA_MD5_MISMATCH")
  if not blob.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(io.BytesIO(blob)):
   raise RuntimeError("R25_ORIGINAL_MEDIA_NOT_ZIP")
  sha=hashlib.sha256(blob).hexdigest()
  archive=output/(paper+"_"+sha[:18]+"_"+name)
  if archive.exists():
   if checksum(archive)!=sha:raise RuntimeError("R25_ARCHIVE_SHA_DRIFT")
  else:archive.write_bytes(blob)
  contents=[]
  with zipfile.ZipFile(io.BytesIO(blob)) as f:
   children=f.infolist()
   if len(children)>80:raise RuntimeError("R25_SI_ZIP_TOO_MANY_FILES")
   if sum(z.file_size for z in children)>30000000:raise RuntimeError("R25_SI_ZIP_EXPAND_GT_30MB_HOLD")
   for z in children:
    clean=z.filename.replace("\\","/")
    parts=PurePosixPath(clean).parts
    if (clean.startswith("/") or ".." in parts or
       re.search(r"^[a-zA-Z]:",clean) or
       stat.S_ISLNK(z.external_attr>>16)):
     raise RuntimeError("R25_SI_ZIP_UNSAFE_ENTRY_HOLD")
    if z.is_dir():continue
    extension=PurePosixPath(clean).suffix.lower()
    qualified=extension in (".xlsx",".csv")
    nested_zip=extension==".zip"
    contents.append({"author_relative_filename":clean,
     "uncompressed_size_bytes":z.file_size,"source_sha256":None,
     "candidate_original_numeric_cell_asset":qualified,
     "nested_zip_unsupported":nested_zip,
     "interpretation":"SOURCE_ONLY_NOT_MEASURED_Y"})
  reports.append({"paper":paper,"JATS_source_original_SHA":parent_sha,
   "JATS_supplement_anchor":original_locator,
   "original_author_supplement_href":name,
   "official_article_version":version,"official_parent_license":meta["license_code"],
   "official_metadata_original_sha":hashlib.sha256(raw).hexdigest(),
   "official_media_MD5_verified":True,"official_media_MD5":expected_md5,
   "official_original_archive_sha256":sha,
   "local_immutable_archive_path":str(archive),
   "content_type":typ,"children":contents,
   "supported_data_children":sum(x["candidate_original_numeric_cell_asset"] for x in contents),
   "status":"OFFICIAL_LICENSE_MD5_SHA_ARCHIVE_VERIFIED_SOURCE_ONLY"})
 result={"schema":"r031.official-zip-si/1","status":"R25_OFFICIAL_AUTHOR_ZIP_ARCHIVE_EVIDENCE_SOURCE_ONLY",
  "paper":paper,"article_original_JATS_SHA":parent_sha,
  "archives":reports,"author_numeric_measurement_extracted":0,
  "child_files_not_auto_executed":True,"training_approved":0,"Gold":0}
 out=output/(paper+"_official_archive_manifest.json")
 if out.exists():raise ValueError("R25_OFFICIAL_ZIP_MANIFEST_EXISTS_DO_NOT_RETRY")
 out.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 result["manifest"]=str(out)
 return result

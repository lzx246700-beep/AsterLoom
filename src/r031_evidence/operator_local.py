"""R10 operator-only LOCAL provenance; NEVER SCI independent release.

A local operator may register an independently SHA-verified public or owned
author XML source, with explicit acknowledgement. Receipt is a local OS-user
file (0600) and is NOT an independent cryptographic/SCI approval signature.
"""
from pathlib import Path
import hashlib, json, os, stat
from xml.etree import ElementTree as ET
from .contracts import AuthorizationHold,SourceDrift
from .registry import file_sha,stable
LOCAL_ROLE="LOCAL_OPERATOR_SOURCE_HOLD"
ACK="LOCAL_SOURCE_ONLY_NOT_SCI_OR_GOLD"
def _receipt_path(operator_root,release_id):
 if not release_id.startswith("local-") or len(release_id)>80 or not release_id[6:].replace("-","").isalnum():
  raise AuthorizationHold("BAD_LOCAL_RELEASE_ID")
 return Path(operator_root).resolve()/"operator_releases"/(release_id+".json")
def _native_license(source):
 root=ET.parse(source).getroot()
 from .source_intake import native_license_literals
 text=native_license_literals(root)
 if not text:raise AuthorizationHold("LOCAL_SOURCE_AUTHOR_LICENSE_NOT_PRESENT")
 # Original publisher JATS may print the CC BY license in author prose,
 # without a URL. Never accept BY-NC, BY-ND or generic open access text.
 from .source_intake import LICENSE_CC_BY,LICENSE_RESTRICTED
 combined=" ".join(text)
 if LICENSE_RESTRICTED.search(combined):return None
 return next((s for s in text if LICENSE_CC_BY.search(s)),None)
def issue(operator_root,release_id,paper_key,doi,source,ack):
 if ack!=ACK:raise AuthorizationHold("OPERATOR_LOCAL_ONLY_ACK_REQUIRED")
 from .contracts import PMCID_RE,doi_paper_key
 if not (PMCID_RE.fullmatch(paper_key) or paper_key==doi_paper_key(doi)):raise AuthorizationHold("LOCAL_PAPER_PMCID_OR_DOI_INVALID")
 src=Path(source).resolve()
 if not src.is_file() or src.suffix.lower() not in (".xml",".nxml"):
  raise AuthorizationHold("LOCAL_SOURCE_MUST_BE_AUTHOR_JATS_XML")
 from .source_intake import original_metadata
 native=original_metadata(src.read_bytes(),paper_key if paper_key.startswith("PMC") else None,doi)
 if native["paper"]!=paper_key:raise AuthorizationHold("LOCAL_AUTHOR_IDENTITY_MISMATCH")
 license=_native_license(src)
 if not license:raise AuthorizationHold("LOCAL_SOURCE_REQUIRES_JATS_CC_BY_LICENSE")
 path=_receipt_path(operator_root,release_id)
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists():raise AuthorizationHold("OPERATOR_RELEASE_RECEIPT_EXISTS_REFUSE_REWRITE")
 row={"source_id":paper_key+"|author_JATS","paper_key":paper_key,
  "doi":doi,"asset_key":"author_JATS","source_path":str(src),
  "sha256":file_sha(src),"license":license,
  "role":LOCAL_ROLE,"authorization_basis":"OS_LOCAL_OWNER_OPERATOR_RECEIPT_NOT_INDEPENDENT_SCI",
  "source_type":src.suffix.lower()}
 payload={"contract":"R031_LOCAL_OPERATOR_RELEASE_V1","release_id":release_id,
  "role":LOCAL_ROLE,"operator_os_uid":os.getuid(),
  "operator_acknowledged":ACK,"author_source":[row],
  "science_approved":False,"training_approved":False,"Gold":0}
 fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 try:
  with os.fdopen(fd,"w") as f:json.dump(payload,f,ensure_ascii=False,sort_keys=True,indent=2)
  os.chmod(path,0o600)
 except BaseException:
  path.unlink(missing_ok=True);raise
 return {"operator_receipt":str(path),"sha256":file_sha(path),
  "role":LOCAL_ROLE,"source_sha256":row["sha256"],"SCI_approved":False,"Gold":0}
def verify(operator_root,release_id,source_ids,source_root=None):
 path=_receipt_path(operator_root,release_id)
 if not path.is_file():raise AuthorizationHold("LOCAL_OPERATOR_RECEIPT_NOT_ISSUED")
 st=path.stat()
 if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o077:
  raise AuthorizationHold("LOCAL_OPERATOR_RECEIPT_OWNER_OR_MODE_UNSAFE")
 grant=json.loads(path.read_text())
 if (grant.get("contract")!="R031_LOCAL_OPERATOR_RELEASE_V1" or
  grant.get("role")!=LOCAL_ROLE or grant.get("release_id")!=release_id or
  grant.get("operator_os_uid")!=os.getuid() or
  grant.get("operator_acknowledged")!=ACK or
  grant.get("science_approved") is not False or
  grant.get("training_approved") is not False or
  grant.get("Gold")!=0):
  raise AuthorizationHold("LOCAL_OPERATOR_RECEIPT_INVALID_OR_ELEVATED")
 found={x["source_id"]:x for x in grant["author_source"]}
 if not source_ids or not set(source_ids)<=set(found):raise AuthorizationHold("SOURCE_NOT_OPERATOR_REGISTERED")
 output=[]
 for key in source_ids:
  x=dict(found[key])
  if x["role"]!=LOCAL_ROLE:raise AuthorizationHold("LOCAL_RELEASE_CANNOT_ASSERT_SCI_ROLE")
  loc=(Path(source_root)/Path(x["source_path"]).name) if source_root else Path(x["source_path"])
  if file_sha(loc)!=x["sha256"]:raise SourceDrift("LOCAL_ORIGINAL_SOURCE_SHA_DRIFT")
  x["source_path"]=str(loc.resolve())
  output.append(x)
 return output,file_sha(path)

"""Trusted source-release registry, independent of caller BatchManifest.

An unprivileged batch cannot grant itself a source license or SCI access:
catalog SHA is pinned by the *installed package* and its source identities are
bound to real original-file SHA and explicit release metadata. New SCI release
requires operator-managed catalog pin update, not a parser code change.
"""
from __future__ import annotations
import hashlib,json,importlib.resources as resources
from pathlib import Path
from .contracts import Batch,SourceAsset,AuthorizationHold,SourceDrift
def file_sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
 return h.hexdigest()
def stable(*parts):return hashlib.sha256("|".join(map(str,parts)).encode()).hexdigest()[:28]
def load_json(path):return json.loads(Path(path).read_text())
def open_trust(catalog_path, pin_path=None):
 if pin_path is not None:
  raise AuthorizationHold("CUSTOM_SCI_TRUST_PIN_REFUSED_BY_NORMAL_INGEST")
 with resources.files("r031_evidence").joinpath("data/trusted_catalog_pin.json").open("r") as f:
  pin=json.load(f)
 real=file_sha(catalog_path)
 if pin.get("registry_contract")!="R031_TRUSTED_SOURCE_RELEASE_V1" or pin.get("catalog_sha256")!=real:
  raise AuthorizationHold("TRUST_CATALOG_SHA_NOT_APPROVED")
 data=load_json(catalog_path)
 if data.get("registry_contract")!=pin["registry_contract"]:
  raise AuthorizationHold("TRUST_CATALOG_CONTRACT_CHANGED")
 if not data.get("releases"):raise AuthorizationHold("NO_TRUSTED_RELEASES")
 return data,real
def validate_batch(manifest_path,catalog_path,pin_path=None,source_root=None,operator_root=None):
 manifest_path=Path(manifest_path)
 m=load_json(manifest_path)
 if pin_path is not None:raise AuthorizationHold("CUSTOM_PIN_NOT_ALLOWED_IN_USER_INGEST")
 if not isinstance(m.get("release_id"),str):raise AuthorizationHold("INVALID_RELEASE_ID_TYPE")
 if m.get("batch_contract")!="R031_R09_BATCH_V1":
  raise AuthorizationHold("UNREGISTERED_BATCH_CONTRACT")
 if m.get("science_Gold")!=0 or m.get("user_training_approved") is not False or m.get("new_unseen_sci") is not False:
  raise AuthorizationHold("SELF_ASSERTED_SCI_OR_TRAINING_RIGHTS_REFUSED")
 allowed={"batch_contract","batch_id","release_id","source_ids","science_Gold",
    "user_training_approved","new_unseen_sci"}
 if set(m)!=allowed:raise AuthorizationHold("EXTRA_SELF_DECLARED_AUTHORITY_FIELDS_DENIED")
 if m["release_id"].startswith("local-"):
  if operator_root is None:raise AuthorizationHold("LOCAL_OPERATOR_ROOT_REQUIRED")
  from .operator_local import verify,LOCAL_ROLE
  rows,receipt_sha=verify(operator_root,m["release_id"],m["source_ids"],source_root)
  assets=tuple(SourceAsset(x["paper_key"],x.get("doi"),x["asset_key"],
    Path(x["source_path"]),x["sha256"],x["license"],x["role"],
    x["authorization_basis"],x["source_type"]) for x in rows)
  return Batch(m["batch_id"],m["release_id"],receipt_sha,
   LOCAL_ROLE,assets,file_sha(manifest_path))
 registry,regsha=open_trust(catalog_path,pin_path)
 release=next((x for x in registry["releases"] if x.get("release_id")==m.get("release_id")),None)
 if release is None:raise AuthorizationHold("RELEASE_NOT_REGISTERED_BY_SCI_MASTER")
 if release.get("release_role") not in ("R06_FROZEN_DEV_OR_POSTEVAL","SCI_R06_RELEASED_PUBLIC_DEV_SOURCE_ONLY"):
  raise AuthorizationHold("UNRECOGNIZED_SCI_RELEASE_ROLE")
 known={x["source_id"]:x for x in release["sources"]}
 ids=m.get("source_ids")
 if not isinstance(ids,list) or not ids or not all(isinstance(x,str) for x in ids):
  raise AuthorizationHold("EMPTY_OR_INVALID_BATCH_SELECTION")
 if len(ids)!=len(set(ids)):raise AuthorizationHold("DUPLICATE_SOURCES_IN_BATCH")
 missing=[x for x in ids if x not in known]
 if missing:raise AuthorizationHold("SOURCE_NOT_IN_APPROVED_RELEASE:"+",".join(missing[:2]))
 assets=[]
 for key in ids:
  x=known[key]
  # Role, original SHA, license, location are all taken from the trusted catalog,
  # never from the user batch. Original file bytes are independently rechecked.
  orig=(Path(source_root)/Path(x["source_path"]).name) if source_root else Path(x["source_path"])
  a=SourceAsset(x["paper_key"],x.get("doi"),x["asset_key"],
    orig,x["sha256"],x["license"],x["role"],
    x["authorization_basis"],x["source_type"])
  if not a.source_path.exists():raise SourceDrift("TRUSTED_AUTHOR_ORIGINAL_FILE_ABSENT:"+key)
  if file_sha(a.source_path)!=a.sha256:raise SourceDrift("TRUSTED_AUTHOR_ORIGINAL_SHA_DRIFT:"+key)
  if a.source_type in (".xml",".nxml"):
   from xml.etree import ElementTree as ET
   root=ET.parse(a.source_path).getroot()
   lic=[" ".join("".join(v.itertext()).split()) for v in root.iter()
     if v.tag.rsplit("}",1)[-1]=="license"]
   # 3 SCI open JATS must independently expose a public native CC-BY.
   if a.role=="SCI_R06_RELEASED_PUBLIC_DEV_SOURCE_ONLY" and not any(
      "creativecommons.org/licenses/by/" in v.lower() for v in lic):
     raise AuthorizationHold("SCI_RELEASE_SOURCE_NATIVE_LICENSE_NOT_CONFIRMED")
  assets.append(a)
 return Batch(m["batch_id"],release["release_id"],
   release["release_source_SHA256"],release["release_role"],
   tuple(assets),file_sha(manifest_path))

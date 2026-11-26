"""Strict, source-first contracts for R031 evidence staging v0.9.

These dataclasses do not carry scientific or training approval; untrusted
manifest values cannot elevate an author's source to SCI-approved Gold.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re,hashlib
from urllib.parse import unquote
SHA_RE=re.compile(r"^[a-f0-9]{64}$")
PMCID_RE=re.compile(r"^PMC[0-9]+$")
def doi_paper_key(doi):
    value=unquote(str(doi or "")).strip().lower()
    value=re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)","",value)
    return "DOI_"+hashlib.sha256(value.encode()).hexdigest()[:24] if re.fullmatch(r"10\.\d{4,9}/\S+",value) else None
SAFE_ID=re.compile(r"^[A-Za-z0-9._-]{1,80}$")
class EvidenceError(RuntimeError):
    code="EVIDENCE_ERROR"
class AuthorizationHold(EvidenceError):
    code="AUTHORITY_HOLD"
class SourceDrift(EvidenceError):
    code="AUTHOR_SOURCE_SHA_DRIFT"
class IdentityConflict(EvidenceError):
    code="PAPER_ASSET_IDENTITY_CONFLICT"
class ScienceHold(EvidenceError):
    code="SOURCE_SCIENCE_HOLD"
class ExcelDependencyHold(EvidenceError):
    code="NEEDS_EXCEL_DEPENDENCY_SOURCE_HOLD"
class MigrationHold(EvidenceError):
    code="SCHEMA_MIGRATION_HOLD"
@dataclass(frozen=True)
class SourceAsset:
    paper_key: str
    doi: str | None
    asset_key: str
    source_path: Path
    sha256: str
    license: str
    role: str
    authorization_basis: str
    source_type: str
    def __post_init__(self):
        if not (PMCID_RE.fullmatch(self.paper_key) or self.paper_key==doi_paper_key(self.doi)):raise AuthorizationHold("SOURCE_PAPER_PMCID_OR_VERIFIED_DOI_INVALID")
        if not SAFE_ID.fullmatch(self.asset_key):raise AuthorizationHold("SOURCE_ASSET_KEY_INVALID")
        if not SHA_RE.fullmatch(self.sha256):raise AuthorizationHold("SOURCE_SHA_INVALID")
        if self.source_type not in (".xml",".nxml",".xlsx",".csv"):
            raise AuthorizationHold("SOURCE_FORMAT_NOT_NATIVE_SUPPORTED")
        if self.source_path.suffix.lower()!=self.source_type:
            raise AuthorizationHold("SOURCE_TYPE_AND_PATH_MISMATCH")
        if not self.role or not self.license:raise AuthorizationHold("SOURCE_ROLE_OR_LICENSE_ABSENT")
    def record(self):
        return {"paper_key":self.paper_key,"doi":self.doi,
          "asset_key":self.asset_key,"source_path":str(self.source_path),
          "sha256":self.sha256,"role":self.role,"license":self.license,
          "authorization_basis":self.authorization_basis,
          "source_type":self.source_type}
@dataclass(frozen=True)
class Batch:
    batch_id: str
    release_id: str
    release_registry_sha: str
    source_role: str
    assets: tuple[SourceAsset,...]
    manifest_sha: str
    def __post_init__(self):
        if not SAFE_ID.fullmatch(self.batch_id):raise AuthorizationHold("BATCH_ID_INVALID")
        if not self.assets:raise AuthorizationHold("EMPTY_SOURCE_BATCH")
        if not SHA_RE.fullmatch(self.manifest_sha):raise AuthorizationHold("MANIFEST_SHA_INVALID")
        names=[(s.paper_key,s.asset_key) for s in self.assets]
        if len(set(names))!=len(names):raise IdentityConflict("DUPLICATE_PAPER_ASSET_IN_BATCH")
@dataclass(frozen=True)
class ReviewState:
    science_approved: bool=False
    training_approved: bool=False
    qualified_y: float | None=None
    def __post_init__(self):
        if self.science_approved or self.training_approved or self.qualified_y is not None:
            raise AuthorizationHold("R09_CANNOT_APPROVE_SCI_OR_TRAINING")
@dataclass(frozen=True)
class StageReceipt:
    stage_id:str
    source_sha:str
    code_sha:str
    stage_kind:str
    output_digest:str
    status:str
    def __post_init__(self):
        if self.status not in ("SOURCE_STAGED","SOURCE_HOLD"):
            raise EvidenceError("INVALID_STAGE_STATUS")
@dataclass(frozen=True)
class MeasurementCandidate:
    source_literal:str
    original_locator:str
    endpoint:str
    qualified_y:float|None=None
    def __post_init__(self):
        if self.qualified_y is not None:raise AuthorizationHold("SOURCE_CANDIDATE_NOT_GOLD")
@dataclass(frozen=True)
class ScientificHold:
    reason:str
    source_anchor:str|None
    qualified_y:float|None=None
    def __post_init__(self):
        if self.qualified_y is not None:raise AuthorizationHold("SCIENCE_HOLD_NOT_GOLD")

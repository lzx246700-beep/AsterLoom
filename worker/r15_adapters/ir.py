from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class AssetSHA:
    sha256: str
    path: str
    bytes: int

@dataclass
class SourceAnchor:
    asset_sha256: str
    locator: str
    page: int | None = None
    bbox: list[float] | None = None
    coordinate_system: str | None = None

@dataclass
class PaperHit:
    provider: str
    provider_id: str
    title: str
    doi: str | None
    pmcid: str | None
    pmid: str | None
    raw_sha256: str
    query_sha256: str

@dataclass
class LicensedAuthorSource:
    paper_id: str
    asset: AssetSHA
    source_url: str
    license_literal: str
    license_locator: str
    revision: str | None = None
    state: str = "SCI_REVIEW_HOLD"

@dataclass
class SupplementAsset:
    parent_paper_id: str
    parent_asset_sha256: str
    href_literal: str
    href_anchor: SourceAnchor
    asset: AssetSHA | None = None
    state: str = "ACCESS_HOLD"

@dataclass
class SourceCell:
    row: int
    column: int
    raw_text: str
    anchor: SourceAnchor
    rowspan: int = 1
    colspan: int = 1
    role: str = "data"
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class TableIR:
    table_id: str
    cells: list[SourceCell]
    headers: list[dict]
    footnotes: list[dict]
    anchor: SourceAnchor
    caption: str = ""
    state: str = "SCI_REVIEW_HOLD"

@dataclass
class SourceMethod:
    raw_text: str
    anchor: SourceAnchor
    heading: str
    state: str = "CONTEXT_CANDIDATE_REVIEW_HOLD"

@dataclass
class PaperIR:
    paper_id: str
    asset: AssetSHA
    backend: str
    tables: list[TableIR] = field(default_factory=list)
    methods: list[SourceMethod] = field(default_factory=list)
    supplements: list[SupplementAsset] = field(default_factory=list)
    holds: list[str] = field(default_factory=list)
    text_blocks: list[SourceMethod] = field(default_factory=list)
    schema: str = "r031-source-ir/0.1"
    def to_dict(self):
        return asdict(self)

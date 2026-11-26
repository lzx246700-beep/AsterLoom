"""Optional Pydantic contract, separate from the generic gateway and old callers."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from . import r44_arbiter
from .scientific_extraction_harness import SCOPED_SYSTEM
from .llm_harness import SchemaFailure, TaskProfile, sha


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


fields = {name: (str | None, Field(..., max_length=500)) for name in r44_arbiter.FIELDS}
fields['endpoint'] = (Literal[tuple(sorted(r44_arbiter.ENUMS['endpoint']))] | None, ...)
fields['direction'] = (Literal[tuple(sorted(r44_arbiter.ENUMS['direction']))] | None, ...)
ATPFields = create_model('ATPFields', __base__=Strict, **fields)


class Evidence(Strict):
    field: Literal[tuple(r44_arbiter.FIELDS)]
    chunk_id: str
    quote: str


class Relation(Strict):
    fields: ATPFields
    evidence: list[Evidence] = Field(max_length=120)
    uncertainties: list[str]


class ATPResponse(Strict):
    paper: str
    relations: list[Relation] = Field(max_length=12)
    reason_if_none: str | None


def parse_atp(answer):
    try:
        return ATPResponse.model_validate(answer).model_dump()
    except ValidationError:
        raise SchemaFailure('HARNESS_ATP_SHAPE_INVALID') from None


def parse_atp_candidate(obj):
    normalized = dict(obj)
    if isinstance(obj.get('fields'), dict):
        normalized['fields'] = {**{k:None for k in r44_arbiter.FIELDS}, **obj['fields']}
    normalized.setdefault('evidence', [])
    normalized.setdefault('uncertainties', [])
    return Relation.model_validate(normalized).model_dump()


def atp_content_profile() -> TaskProfile:
    # Fingerprint owning verifier and alias module: changed verification cannot
    # silently reuse an earlier result under an unchanged hand-written version.
    from pathlib import Path
    from importlib.metadata import version
    from . import author_alias_resolution
    verifier = sha(Path(r44_arbiter.__file__).read_bytes() + Path(author_alias_resolution.__file__).read_bytes()
                   + Path(__file__).read_bytes() + version('pydantic').encode())
    return TaskProfile('ATP_CONTENT', 'r44-compatible/1', ATPResponse.model_json_schema(),
                       'scoped-r44/1', SCOPED_SYSTEM, verifier, parse_atp, r44_arbiter.validate,
                       parse_atp_candidate, Relation.model_json_schema(),
                       ('cell_model','treatment','comparator','dose','exposure_time','value'))

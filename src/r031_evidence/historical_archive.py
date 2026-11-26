"""R14 permitted R12 author-source span archive inside the cumulative R14 database.

No promotion to live asset_ref or numerical claims; no copying of restricted source
literals. Frozen input is opened read-only and source SHA and license checked.
"""
from __future__ import annotations
import hashlib,re,sqlite3
from pathlib import Path
from r031_evidence import repository

RESTRICTED=re.compile(r"(?:by[- ]?nc|by[- ]?nd|noncommercial|no\s+derivatives)",re.I)
PERMITTED=re.compile(r"creativecommons\.org/licenses/by/[234]\.0|creative\s+commons\s+attribution\s+\d|\bcc\s*by\b|\bcc0\b",re.I)

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def archive(workspace:Path,db:Path,frozen:Path,known_frozen_sha:str):
    workspace=workspace.resolve();db=db.resolve();frozen=frozen.resolve()
    if not db.is_relative_to(workspace) or db==frozen:
        raise RuntimeError("R14_CUMULATIVE_ARCHIVE_WRITES_ONLY_TO_LIVE")
    if sha(frozen)!=known_frozen_sha:
        raise RuntimeError("R14_FROZEN_SOURCE_DATABASE_SHA_DRIFT")
    repository.configure(workspace)
    src=sqlite3.connect("file:"+str(frozen)+"?mode=ro&immutable=1",uri=True)
    dst=repository.connect(db)
    inserted_assets=0;inserted_spans=0;restricted=[]
    try:
        if src.execute("pragma user_version").fetchone()[0]!=822:
            raise RuntimeError("R14_FROZEN_SOURCE_SCHEMA_NOT_822")
        repository.migrate(dst)
        dst.execute("BEGIN IMMEDIATE")
        try:
            data=src.execute("""SELECT f.asset_id,p.paper_key,f.original_sha,f.source_kind,
            f.original_path,f.role,f.license_literal,f.authorization_basis,
            f.source_revision FROM asset_ref f JOIN paper_ref p ON p.paper_id=f.paper_id
            ORDER BY p.paper_key""").fetchall()
            for aid,pm,author_sha,kind,path,role,lic,authorization,revision in data:
                if RESTRICTED.search(lic):
                    # No source literals, spans, atoms or conditions copied.
                    old_count=src.execute(
                        "SELECT count(*) FROM source_proposed_author_values WHERE paper_key=?",(pm,)).fetchone()[0]
                    repository.put(dst,"r14_restricted_historical_reference",{
                        "paper_key":pm,"original_source_sha":author_sha,
                        "frozen_database_sha":known_frozen_sha,"historical_candidate_count":old_count,
                        "license_gate":"ACCESS_HOLD_NON_CC_BY",
                        "restricted_numeric_copied":0,"scientific_approved":0})
                    restricted.append({"paper_key":pm,"historical_candidate_count":old_count,
                                       "license_gate":"ACCESS_HOLD_NON_CC_BY"})
                    continue
                if not PERMITTED.search(lic):
                    raise RuntimeError("R14_HISTORICAL_LICENSE_NOT_CONFIRMED_CC_BY:"+pm)
                native=Path(path)
                if not native.is_file() or sha(native)!=author_sha:
                    raise RuntimeError("R14_OLD_AUTHOR_SOURCE_BYTES_MISSING_OR_DRIFT:"+pm)
                inserted_assets+=repository.put(dst,"r14_historical_permitted_asset",{
                    "frozen_asset_id":aid,"frozen_paper_key":pm,"frozen_original_sha":author_sha,
                    "frozen_source_kind":kind,"frozen_source_path":str(native),
                    "frozen_source_role":role,"frozen_license_literal":lic,
                    "frozen_authorization_basis":authorization,"frozen_source_revision":revision,
                    "source_frozen_db_sha":known_frozen_sha,
                    "archive_status":"ARCHIVAL_SOURCE_REFERENCE_ONLY",
                    "science_approved":0,"training_approved":0})
                rows=src.execute("""SELECT span_id,original_sha,locator,span_kind,
                 source_literal,literal_sha,original_value_state
                 FROM source_span WHERE asset_id=?""",(aid,)).fetchall()
                for sid,h,loc,sk,raw,rawsha,state in rows:
                    if h!=author_sha:
                        raise RuntimeError("R14_FROZEN_SPAN_AUTHOR_SHA_MISMATCH:"+pm)
                    if raw is not None and hashlib.sha256(raw.encode()).hexdigest()!=rawsha:
                        raise RuntimeError("R14_FROZEN_SPAN_LITERAL_SHA_MISMATCH:"+pm+":"+loc)
                    inserted_spans+=repository.put(dst,"r14_historical_permitted_span",{
                        "frozen_span_id":sid,"frozen_asset_id":aid,
                        "source_frozen_db_sha":known_frozen_sha,"original_author_sha":h,
                        "source_anchor":loc,"span_kind":sk,"original_author_literal":raw,
                        "original_literal_sha":rawsha,"original_value_state":state,
                        "archive_status":"ARCHIVAL_SOURCE_REFERENCE_ONLY",
                        "science_approved":0,"training_approved":0})
            if len(restricted)!=1:
                raise RuntimeError("R14_RESTRICTED_LICENSE_SET_UNEXPECTED_REVIEW_REQUIRED")
            if dst.execute("select count(*) from qualified_training_labels").fetchone()[0]!=0:
                raise RuntimeError("R14_GOLD_BOUNDARY_NOT_ZERO")
            dst.commit()
        except BaseException:dst.rollback();raise
        return {"status":"R14_R12_HISTORICAL_PERMITTED_AUTHOR_SPANS_ARCHIVED",
         "historical_author_assets":dst.execute("select count(*) from r14_historical_permitted_asset").fetchone()[0],
         "historical_source_spans":dst.execute("select count(*) from r14_historical_permitted_span").fetchone()[0],
         "new_author_assets":inserted_assets,"new_author_spans":inserted_spans,
         "restricted_historical_references":restricted,"Gold":0,"source_data_only":True}
    finally:dst.close();src.close()

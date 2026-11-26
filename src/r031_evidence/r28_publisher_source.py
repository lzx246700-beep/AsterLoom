"""R28 bounded publisher-original source-data ZIP intake (source-only).

This provider adapter accepts exactly one HTTPS Springer official CC-BY article
supplement per call; the DOI must match the SHA-verified author JATS. A vendor
attachment is not equivalent to the PMC S3 MD5-verified channel, so both
verification levels remain separate and no measured Y is inferred from cells.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError
import hashlib
import io
import json
import re
import sqlite3
import stat
import zipfile

from .source_intake import original_metadata

MAX_ZIP_BYTES = 5_000_000
MAX_EXPANDED_BYTES = 30_000_000
MAX_MEMBERS = 80


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _author_jats_proof(home, paper):
    root = Path(home).expanduser().resolve()
    if root.name == "state":
        root = root.parent
    con = sqlite3.connect("file:" + str((root / "state/science.sqlite3").resolve()) + "?mode=ro",
                          uri=True)
    try:
        row = con.execute(
            """SELECT original_path,original_sha FROM r16_parsed_asset
               WHERE paper_key=? AND source_format IN ('.xml','.nxml')
               ORDER BY parsed_asset_id LIMIT 1""",
            (paper,)).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("AUTHOR_JATS_PARSED_SOURCE_REQUIRED")
    path = Path(row[0])
    if not path.is_file() or sha(path.read_bytes()) != row[1]:
        raise ValueError("AUTHOR_JATS_ORIGINAL_SHA_DRIFT")
    info = original_metadata(path.read_bytes(), paper)
    if info["license_status"] != "AUTHOR_NATIVE_CC_BY_VERIFIED":
        raise ValueError("AUTHOR_JATS_CC_BY_LICENSE_NOT_VERIFIED")
    if not info.get("doi"):
        raise ValueError("AUTHOR_JATS_DOI_MISSING")
    return root, {"pmcid": paper, "doi": info["doi"], "jats_sha256": row[1],
                  "license": info["license_status"]}


def _validate_url(url: str, doi: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "static-content.springer.com"
            or parsed.username or parsed.password or parsed.port or parsed.query
            or parsed.fragment):
        raise ValueError("PUBLISHER_HOST_QUERY_OR_CREDENTIAL_HOLD")
    if not re.fullmatch(r"[a-z0-9./_-]{6,200}", doi.lower()):
        raise ValueError("AUTHOR_DOI_FORMAT_HOLD")
    path = unquote(parsed.path)
    prefix = "/esm/art:" + doi + "/MediaObjects/"
    if not path.startswith(prefix):
        raise ValueError("PUBLISHER_MEDIA_DOI_DOES_NOT_MATCH_AUTHOR_JATS")
    rest = path[len(prefix):]
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{3,180}[.]zip", rest, re.I):
        raise ValueError("PUBLISHER_MEDIA_FILENAME_OR_EXTENSION_HOLD")


def intake(home, paper, publisher_url):
    root, article = _author_jats_proof(home, paper)
    _validate_url(publisher_url, article["doi"])
    request = Request(publisher_url,
                      headers={"User-Agent": "AsterLoom-R28-OriginalEvidence/0.26"})
    opener = build_opener(_NoRedirect)
    data = None
    for attempt in range(2):
        try:
            with opener.open(request, timeout=22) as response:
                if response.status != 200:
                    raise ValueError("PUBLISHER_SOURCE_HTTP_NOT_200")
                data = response.read(MAX_ZIP_BYTES + 1)
            break
        except HTTPError as exc:
            raise RuntimeError("PUBLISHER_SOURCE_HTTP_HOLD_" + str(exc.code)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            # Limited retry for transport failures before any source is saved.
            if attempt:
                raise RuntimeError("PUBLISHER_TRANSPORT_HOLD_" + type(exc).__name__) from exc
    if data is None or len(data) > MAX_ZIP_BYTES:
        raise ValueError("PUBLISHER_COMPRESSED_ZIP_EXCEEDS_LIMIT")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("PUBLISHER_RESPONSE_NOT_REAL_ZIP")
    archive_sha = sha(data)
    target_root = root / "state/r28_publisher_source_data"
    archive = target_root / "archives" / (archive_sha + ".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_bytes_verified = False
    if archive.exists():
        if sha(archive.read_bytes()) != archive_sha:
            raise ValueError("PUBLISHER_ARCHIVE_EXISTING_SHA_DRIFT")
        archive_bytes_verified = True
    else:
        archive.write_bytes(data)
        archive_bytes_verified = sha(archive.read_bytes()) == archive_sha
    if not archive_bytes_verified:
        raise ValueError("PUBLISHER_ARCHIVE_WRITE_NOT_VERIFIED")
    members = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()
        if len(info) > MAX_MEMBERS or sum(e.file_size for e in info) > MAX_EXPANDED_BYTES:
            raise ValueError("PUBLISHER_ZIP_EXPANSION_BUDGET_HOLD")
        if z.testzip() is not None:
            raise ValueError("PUBLISHER_ZIP_CRC_FAILED")
        for item in info:
            name = item.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (name.startswith("/") or ".." in parts or
                    re.search(r"^[a-zA-Z]:", name) or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("PUBLISHER_ZIP_UNSAFE_MEMBER_HOLD")
            if item.is_dir() or name.startswith("__MACOSX/") or PurePosixPath(name).name.startswith("._"):
                continue
            extension = PurePosixPath(name).suffix.lower()
            qualifying = extension in (".csv", ".xlsx")
            source_sha = None
            asset_path = None
            if qualifying:
                body = z.read(item)
                source_sha = sha(body)
                target = target_root / "assets" / (source_sha + extension)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if sha(target.read_bytes()) != source_sha:
                        raise ValueError("PUBLISHER_SI_ASSET_SHA_COLLISION")
                else:
                    target.write_bytes(body)
                asset_path = str(target)
            members.append({
                "author_zip_member": name,
                "uncompressed_bytes": item.file_size,
                "raw_asset_sha256": source_sha,
                "raw_asset_path": asset_path,
                "original_numeric_asset_candidate": qualifying,
                "scientific_measurement_extracted": 0,
                "status": "SOURCE_ONLY_NOT_AUTOMATIC_Y"})
    receipt = {
        "schema": "asterloom.publisher-source-zip/1",
        "status": "OFFICIAL_PUBLISHER_JATS_DOI_CC_BY_SOURCE_SHA_VERIFIED_NOT_PMC_MD5",
        "original_paper": article,
        "publisher_source_url": publisher_url,
        "archive_sha256": archive_sha,
        "archive_path": str(archive),
        "publisher_byte_SHA_verified": True,
        "PMC_S3_official_media_MD5_verified": False,
        "raw_numeric_asset_candidates": sum(m["original_numeric_asset_candidate"] for m in members),
        "members": members,
        "new_qualified_Measurement": 0,
        "automatic_Gold": False,
        "training_approval": False,
        "review_status": "SOURCE_ONLY_HOLD",
    }
    destination = target_root / "manifests" / (archive_sha + ".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if destination.exists():
        if destination.read_text() != encoded:
            raise ValueError("PUBLISHER_SOURCE_RECEIPT_IDENTITY_DRIFT")
    else:
        destination.write_text(encoded)
    receipt["manifest"] = str(destination)
    return receipt

"""Continuous targeted supplementary-data stage, using the R34/R40 official media contract."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import zipfile
from contextlib import closing
from pathlib import Path

from .r30_http import request
from .r30_ledger import db_path, digest
from .r34_si_acquisition import BUCKET, media_link
from .r36_native_evidence import native_xlsx, xml, xpath_map
from .r41_model_data import licensed, xml_identity

DDL = """
CREATE TABLE IF NOT EXISTS r42_si_scan(paper TEXT, parent TEXT, status TEXT, PRIMARY KEY(paper,parent));
CREATE TABLE IF NOT EXISTS r42_si_job(paper TEXT, parent TEXT, filename TEXT, locator TEXT, caption TEXT,
 priority INTEGER, status TEXT, attempts INTEGER DEFAULT 0, after_time REAL DEFAULT 0, receipt TEXT,
 PRIMARY KEY(paper,parent,filename));
CREATE TABLE IF NOT EXISTS r42_si_cell(paper TEXT,parent TEXT,filename TEXT,asset_sha TEXT,locator TEXT,literal TEXT,
 retention TEXT NOT NULL DEFAULT 'LEGACY_UNSPECIFIED', formula TEXT,
 PRIMARY KEY(paper,parent,filename,asset_sha,locator));
"""
# Rows are source evidence, not assay measurements. Formula text is kept and never evaluated.
STORED_RETENTION = ("NONBLANK_LITERAL","FORMULA_CACHED_NOT_A_MEASUREMENT","FORMULA_CACHE_MISSING_HOLD")
MEASUREMENT_RETENTION = ("NONBLANK_LITERAL",)
EXTS = {".xlsx", ".csv", ".pdf"}
ENERGY = re.compile(r"\bATP\b|adenosine triphosphate|respiration|energy metabolism", re.I)
RIGHTS = re.compile(r"copyright|all rights reserved|permission|third.party", re.I)


def declarations(tree):
    paths = xpath_map(tree)
    for item in tree.iter("supplementary-material"):
        caption = " ".join("".join(item.itertext()).split())
        local_rights = RIGHTS.search(caption) or any(n.tag in ("permissions","attrib","license","copyright-statement") for n in item.iter())
        for node in item.iter():
            name = node.get("{http://www.w3.org/1999/xlink}href","")
            if not re.fullmatch(r"[\w.+ -]{3,180}",name) or Path(name).suffix.lower() not in EXTS:
                continue
            yield name, paths[id(node)], caption, bool(local_rights)


def scan(c):
    rows = c.execute("""SELECT paper,path,sha FROM r42_available a WHERE NOT EXISTS
          (SELECT 1 FROM r42_si_scan s WHERE s.paper=a.paper AND s.parent=a.sha)""").fetchall()
    for paper,path,sha in rows:
        with c:
            try:
                data = Path(path).read_bytes()
                if digest(data) != sha:
                    raise ValueError("source digest drift")
                root = xml(data)
                for name,loc,caption,rights in declarations(root):
                    explicit = bool(ENERGY.search(caption))
                    numeric = Path(name).suffix.lower() in (".xlsx",".csv")
                    status = "RIGHTS_HOLD" if rights else "PENDING" if explicit or numeric else "LOW_RELEVANCE"
                    rank = (4 if explicit else 0) + (2 if numeric else 0) + int(bool(re.search("source data|raw data",caption,re.I)))
                    c.execute("INSERT OR IGNORE INTO r42_si_job(paper,parent,filename,locator,caption,priority,status) VALUES(?,?,?,?,?,?,?)",
                              (paper,sha,name,loc,caption,rank,status))
                state="SCANNED"
            except (ValueError,OSError,SyntaxError) as exc:
                state=str(exc)
            c.execute("INSERT INTO r42_si_scan VALUES(?,?,?)",(paper,sha,state))


def ensure_cell_contract(c):
    c.executescript(DDL)
    cols={row[1] for row in c.execute("PRAGMA table_info(r42_si_cell)")}
    if "retention" not in cols:
        c.execute("ALTER TABLE r42_si_cell ADD COLUMN retention TEXT NOT NULL DEFAULT 'LEGACY_UNSPECIFIED'")
    if "formula" not in cols:
        c.execute("ALTER TABLE r42_si_cell ADD COLUMN formula TEXT")
    if "phonetic" not in cols:
        c.execute("ALTER TABLE r42_si_cell ADD COLUMN phonetic TEXT")
    return {"backfilled":0,"legacy_rows_unchanged":c.execute("SELECT count(*) FROM r42_si_cell WHERE retention='LEGACY_UNSPECIFIED'").fetchone()[0]}


def plan_cells(data, ext):
    """Count every authored cell. Store literals and formula evidence; do not store blanks."""
    counts={"NONBLANK_LITERAL":0,"WHITESPACE_ONLY":0,"EMPTY":0,
            "FORMULA_CACHED_NOT_A_MEASUREMENT":0,"FORMULA_CACHE_MISSING_HOLD":0}
    stored=[]
    if ext==".xlsx":
        seen=set()
        for sheet in native_xlsx(data):
            for cell in sheet["cells"]:
                kind=cell["retention"]
                counts[kind]=counts.get(kind,0)+1
                if cell["locator"] in seen:
                    raise ValueError("duplicate OOXML locator")
                seen.add(cell["locator"])
                if kind in STORED_RETENTION:
                    stored.append({"locator":cell["locator"],"literal":cell["literal"] or None,
                                   "retention":kind,"formula":cell.get("formula"),"phonetic":cell.get("phonetic")})
    elif ext==".csv":
        for i,row in enumerate(csv.reader(io.StringIO(data.decode("utf-8-sig"))),1):
            for j,value in enumerate(row,1):
                kind="NONBLANK_LITERAL" if value.strip() else "WHITESPACE_ONLY" if value else "EMPTY"
                counts[kind]+=1
                if kind in STORED_RETENTION:
                    stored.append({"locator":f"row={i},column={j}","literal":value,"retention":kind,"formula":None})
    elif ext==".pdf":
        if not data.lstrip().startswith(b"%PDF-"):
            raise ValueError("invalid PDF signature")
        raise ValueError("PDF has no cell table in this stage")
    elif not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("invalid PDF signature")
    return {"counts":counts,"stored":stored,
            "authored_cells":sum(counts.values()),
            "stored_rows":len(stored),
            "literal_rows":counts["NONBLANK_LITERAL"],
            "whitespace_rows":counts["WHITESPACE_ONLY"],
            "empty_rows":counts["EMPTY"],
            "formula_rows":counts["FORMULA_CACHED_NOT_A_MEASUREMENT"]+counts["FORMULA_CACHE_MISSING_HOLD"],
            "measurement_rows":0,
            "contract":"SI cell rows are source evidence. NONBLANK_LITERAL is still not an assay measurement. Formula text is retained and never executed. Whitespace and empty cells are counted and not stored."}


class TemporaryFailure(Exception):
    pass


def retrieve(home,c,job):
    paper,parent,name=job
    receipts=[]
    def read(url,limit):
        # Persisted R30 cache is checked by digest; ephemeral failures never become assets.
        entry=c.execute("SELECT request_id,path,sha256 FROM r30_http_receipt WHERE url=? AND status=200 ORDER BY at DESC LIMIT 1",(url,)).fetchone() if c.execute("SELECT 1 FROM sqlite_master WHERE name='r30_http_receipt'").fetchone() else None
        if entry and Path(entry[1]).is_file():
            raw=Path(entry[1]).read_bytes()
            if digest(raw)==entry[2]:
                receipts.append(entry[0])
                return raw
        reply=request(home,url,limit=limit,timeout=18)
        receipts.append(reply.get("request_id"))
        if reply["status"] in (0,429,503,502,504) or reply.get("retry_at"):
            raise TemporaryFailure(reply["reason"])
        if reply["status"]!=200:
            raise ValueError("HTTP "+str(reply["status"]))
        return reply["body"]
    try:
        if not re.fullmatch(r"PMC\d+",paper):
            raise ValueError("PMC identifier required")
        path,doi=c.execute("SELECT path,doi FROM r42_available WHERE paper=? AND sha=?",(paper,parent)).fetchone()
        raw=Path(path).read_bytes()
        if digest(raw)!=parent or not licensed(xml(raw)):
            raise ValueError("parent license or source drift")
        version_id=xml(raw).findtext("./front/article-meta/article-id[@pub-id-type='pmcid-ver']","")
        version=int(version_id.split(".")[-1]) if re.fullmatch(r"PMC\d+\.\d+",version_id or "") else 1
        doc=json.loads(read(f"https://{BUCKET}/{paper}.{version}/{paper}.{version}.json",2000000))
        if (doc.get("pmcid")!=paper or int(doc.get("version",0))!=version or doc.get("license_code")!="CC BY" or
                doc.get("is_retracted") or not doi or str(doc.get("doi","")).lower()!=doi.lower()):
            raise ValueError("official version, DOI or license mismatch")
        xml_spec=media_link(doc["xml_url"],paper,version,f"{paper}.{version}.xml")
        if not xml_spec:
            raise ValueError("official XML digest absent")
        if hashlib.md5(raw).hexdigest()!=xml_spec[1]:
            raw=read(xml_spec[0],8000000)
        tree=xml(raw)
        if hashlib.md5(raw).hexdigest()!=xml_spec[1] or not licensed(tree) or xml_identity(tree)[0]!=paper:
            raise ValueError("official XML integrity or license mismatch")
        matches=[d for d in declarations(tree) if d[0]==name]
        if len(matches)!=1 or matches[0][-1]:
            raise ValueError("ambiguous or restricted official declaration")
        links=[link for uri in doc["media_urls"] if (link:=media_link(uri,paper,version,name))]
        if len(links)!=1:
            raise ValueError("ambiguous or missing official attachment")
        url,md5=links[0]
        raw_asset=read(url,10000000)
        if hashlib.md5(raw_asset).hexdigest()!=md5:
            raise ValueError("media checksum mismatch")
        sha=digest(raw_asset)
        dest=db_path(home).parent/"r42_si_assets"/(sha+Path(name).suffix.lower())
        dest.parent.mkdir(exist_ok=True)
        if dest.exists() and digest(dest.read_bytes())!=sha:
            raise ValueError("local source drift")
        if not dest.exists():
            dest.write_bytes(raw_asset)
        ref=dict(path=str(dest),sha=sha,url=url,md5=md5,version=version,official_xml_sha=digest(raw),
                 official_declaration=matches[0][1],http_receipts=receipts)
        try:
            plan=plan_cells(raw_asset,dest.suffix)
            if plan["authored_cells"]>100000:
                raise ValueError("cell budget exceeded")
            ensure_cell_contract(c)
            with c:
                for record in plan["stored"]:
                    c.execute("""INSERT OR IGNORE INTO r42_si_cell
                        (paper,parent,filename,asset_sha,locator,literal,retention,formula,phonetic)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                        (paper,parent,name,sha,record["locator"],record["literal"],record["retention"],record["formula"],record.get("phonetic")))
            return ("PDF_SOURCE_ONLY" if dest.suffix==".pdf" else "NATIVE_CELLS"),dict(ref,cells=plan["stored_rows"],cell_contract=plan)
        except (OSError,ValueError,KeyError,SyntaxError,IndexError,csv.Error,zipfile.BadZipFile) as e:
            return "PARSER_HOLD",dict(ref,reason=str(e))
    except TemporaryFailure as e:
        return "RETRY",dict(reason=str(e),http_receipts=receipts)
    except (OSError,ValueError,TypeError,KeyError,SyntaxError,IndexError) as e:
        return "SOURCE_HOLD",dict(reason=str(e),http_receipts=receipts)


def run(home,limit=5):
    if not 0<=limit<=100:
        raise ValueError("SI batch must be 0..100")
    path=db_path(home)
    if not path.is_file():
        raise ValueError("existing Evidence Core required")
    with (path.parent/".r42_si.lock").open("a") as lock, closing(sqlite3.connect(path,timeout=60)) as c:
        fcntl.flock(lock,fcntl.LOCK_EX)
        ensure_cell_contract(c)
        scan(c)
        jobs=c.execute("SELECT paper,parent,filename,attempts FROM r42_si_job WHERE status IN ('PENDING','RETRY') AND after_time<=? ORDER BY priority DESC,attempts,paper,filename LIMIT ?",(time.time(),limit)).fetchall()
        result=[]
        for paper,parent,name,attempt in jobs:
            status,receipt=retrieve(home,c,(paper,parent,name))
            if status=="RETRY" and attempt>=3:
                status="TRANSPORT_HOLD"
            with c:
                c.execute("UPDATE r42_si_job SET status=?,receipt=?,attempts=attempts+1,after_time=? WHERE paper=? AND parent=? AND filename=?",
                          (status,json.dumps(receipt),time.time()+30*2**attempt,paper,parent,name))
            result.append(dict(paper=paper,filename=name,status=status))
        return result


def _safe_local(root, relative):
    if not isinstance(relative, str) or relative.startswith(("/", "\\")) or "://" in relative or ".." in Path(relative).parts:
        raise ValueError("LOCAL_RELATIVE_PATH_REQUIRED")
    path = root / relative
    if path.is_symlink():
        raise ValueError("SYMLINK_REJECTED")
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("PATH_ESCAPE")
    if not resolved.is_file():
        raise ValueError("NOT_A_REGULAR_FILE")
    return resolved


def _parent_audit(path, filename):
    raw = path.read_bytes()
    try:
        root = xml(raw)
    except Exception as exc:
        raise ValueError("PARENT_XML_UNREADABLE") from exc
    if root.tag != "article":
        raise ValueError("PARENT_NOT_JATS_ARTICLE")
    terms = licensed(root)
    if not terms:
        raise ValueError("PARENT_LICENSE_NOT_CC_BY_OR_CC0_IN_FILE")
    found = [item for item in declarations(root) if item[0] == filename]
    if len(found) != 1:
        raise ValueError("SI_DECLARATION_MISSING_OR_AMBIGUOUS")
    if found[0][3]:
        raise ValueError("SI_RIGHTS_HOLD_IN_AUTHOR_DECLARATION")
    return {"license_checked_from": "AUTHOR_LICENSE_ELEMENT", "declaration_locator": found[0][1]}


def commit_local(home, manifest, expected_sha, *, sandbox=False):
    """Ingest already-local SI files. No network and no queue advance."""
    if sandbox is not True:
        raise ValueError("SANDBOX_FLAG_REQUIRED")
    manifest = Path(manifest)
    raw_manifest = manifest.read_bytes()
    digest_manifest = hashlib.sha256(raw_manifest).hexdigest()
    if digest_manifest != str(expected_sha).lower():
        raise ValueError("MANIFEST_SHA_MISMATCH")
    doc = json.loads(raw_manifest)
    if doc.get("schema") != "asterloom.r42-si-local/1":
        raise ValueError("SI_LOCAL_MANIFEST_SCHEMA")
    items = doc.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 5:
        raise ValueError("SI_LOCAL_BATCH_1_TO_5")
    root = manifest.parent.resolve()
    database = db_path(home)
    if not database.is_file():
        raise ValueError("existing Evidence Core required")
    planned = []
    seen = set()
    for item in items:
        paper = str(item.get("paper") or "")
        filename = str(item.get("filename") or "")
        if not paper or not filename or (paper, filename) in seen:
            raise ValueError("DUPLICATE_OR_EMPTY_SI_ITEM")
        seen.add((paper, filename))
        asset = _safe_local(root, item.get("asset"))
        parent = _safe_local(root, item.get("parent_xml"))
        asset_bytes = asset.read_bytes()
        parent_bytes = parent.read_bytes()
        asset_sha = digest(asset_bytes)
        parent_sha = digest(parent_bytes)
        if asset_sha != str(item.get("asset_sha256", "")).lower() or parent_sha != str(item.get("parent_sha256", "")).lower():
            raise ValueError("DECLARED_SHA_MISMATCH")
        audit = _parent_audit(parent, filename)
        with closing(sqlite3.connect(database)) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='r42_paper'").fetchone():
                row = c.execute("SELECT status,sha,path FROM r42_paper WHERE paper=?", (paper,)).fetchone()
                if row:
                    if row[0] != "PARSED" or (row[1] or "").lower() != parent_sha:
                        raise ValueError("REGISTERED_PARENT_CONFLICT")
                    registered = Path(row[2]) if row[2] else None
                    if registered is None or not registered.is_file() or digest(registered.read_bytes()) != parent_sha:
                        raise ValueError("REGISTERED_PARENT_BYTES_CONFLICT")
                    audit["parent_registration"] = "REGISTERED_PARSED"
                else:
                    audit["parent_registration"] = "AUDIT_XML_ONLY_NOT_PRESEEDED"
            else:
                audit["parent_registration"] = "AUDIT_XML_ONLY_NOT_PRESEEDED"
        plan = plan_cells(asset_bytes, asset.suffix.lower())
        if plan["authored_cells"] > 100000:
            raise ValueError("cell budget exceeded")
        planned.append((paper, parent_sha, filename, asset_sha, plan, audit))
    if sum(item[4]["authored_cells"] for item in planned) > 100000:
        raise ValueError("cell budget exceeded")
    with database.parent.joinpath(".r42_si.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(sqlite3.connect(database, timeout=60)) as c:
            before = {}
            for name in ("qualified_training_labels", "r17_measurement", "r16_parsed_asset", "r41_observation"):
                if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
                    before[name] = c.execute("SELECT count(*) FROM " + name).fetchone()[0]
            migration = ensure_cell_contract(c)
            c.execute("BEGIN")
            try:
                keys = []
                for paper, parent_sha, filename, asset_sha, plan, _audit in planned:
                    for record in plan["stored"]:
                        c.execute("""INSERT OR IGNORE INTO r42_si_cell
                            (paper,parent,filename,asset_sha,locator,literal,retention,formula,phonetic)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                            (paper, parent_sha, filename, asset_sha, record["locator"], record["literal"],
                             record["retention"], record["formula"], record.get("phonetic")))
                        keys.append((paper, parent_sha, filename, asset_sha, record["locator"]))
                for name, count in before.items():
                    if c.execute("SELECT count(*) FROM " + name).fetchone()[0] != count:
                        raise RuntimeError("PROTECTED_LEDGER_CHANGED")
                c.commit()
            except Exception:
                c.rollback()
                raise
            rows = c.execute("SELECT paper,parent,filename,asset_sha,locator,retention,formula,phonetic FROM r42_si_cell").fetchall()
    return {"status": "SI_LOCAL_COMMIT", "blocked": False, "network": False, "queue_advanced": False,
            "input": len(planned), "stored_rows": sum(item[4]["stored_rows"] for item in planned),
            "measurement_rows": 0, "protected_ledgers": before, "migration": migration,
            "audits": [item[5] for item in planned], "si_cells_are_not_measurements": True,
            "replay_key_count": len(keys)}


def _emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False))
    return code


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "commit-local":
        parser = argparse.ArgumentParser(prog="python -m r031_evidence.r42_si commit-local")
        parser.add_argument("--home", type=Path, required=True)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--expected-sha", required=True)
        parser.add_argument("--sandbox", action="store_true")
        args = parser.parse_args(argv[1:])
        if not args.sandbox:
            return _emit({"status": "SANDBOX_FLAG_REQUIRED", "blocked": True, "writes": False, "network": False}, 2)
        try:
            return _emit(commit_local(args.home, args.manifest, args.expected_sha, sandbox=True))
        except ValueError as exc:
            return _emit({"status": "SI_LOCAL_REJECTED", "blocked": True, "writes": False, "network": False, "reason": str(exc)}, 2)
    if argv and argv[0] == "migrate":
        parser = argparse.ArgumentParser(prog="python -m r031_evidence.r42_si migrate")
        parser.add_argument("--home", type=Path, required=True)
        parser.add_argument("--sandbox", action="store_true")
        args = parser.parse_args(argv[1:])
        if not args.sandbox:
            return _emit({"status": "SANDBOX_FLAG_REQUIRED", "blocked": True, "writes": False, "network": False}, 2)
        with closing(sqlite3.connect(db_path(args.home))) as c:
            return _emit({"status": "SI_CELL_MIGRATION", "network": False, **ensure_cell_contract(c)})
    parser = argparse.ArgumentParser(prog="python -m r031_evidence.r42_si")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-sha")
    parser.add_argument("--migrate", action="store_true")
    args = parser.parse_args(argv)
    if args.manifest or args.migrate:
        if not args.sandbox:
            return _emit({"status": "SANDBOX_FLAG_REQUIRED", "blocked": True, "writes": False, "network": False}, 2)
        if args.migrate and not args.manifest:
            with closing(sqlite3.connect(db_path(args.home))) as c:
                return _emit({"status": "SI_CELL_MIGRATION", "network": False, **ensure_cell_contract(c)})
        if not args.expected_sha:
            return _emit({"status": "MANIFEST_AND_SHA_REQUIRED", "blocked": True, "writes": False}, 2)
        try:
            return _emit(commit_local(args.home, args.manifest, args.expected_sha, sandbox=True))
        except ValueError as exc:
            return _emit({"status": "SI_LOCAL_REJECTED", "blocked": True, "writes": False, "network": False, "reason": str(exc)}, 2)
    print(json.dumps(run(args.home, args.limit), ensure_ascii=False))
    return 0
if __name__=="__main__":
    raise SystemExit(main())

"""Incremental, hash-pinned comparison and discovery ledger in the Evidence Core."""
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

TASK = "raw2647_lps100_atp_nmol_per_viable_cell/v1"


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verified(path, expected):
    raw = Path(path).read_bytes()
    if digest(raw) != expected:
        raise ValueError(f"SHA mismatch: {path}")
    return raw


SCHEMA = """
CREATE TABLE IF NOT EXISTS r41_batch(sha TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r41_source(sha TEXT PRIMARY KEY, paper TEXT, doi TEXT, license TEXT, path TEXT, raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS r41_observation(id TEXT PRIMARY KEY, origin TEXT NOT NULL, kind TEXT NOT NULL,
    source_sha TEXT NOT NULL REFERENCES r41_source, locator TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r41_batch_item(batch TEXT REFERENCES r41_batch, observation TEXT REFERENCES r41_observation,
    PRIMARY KEY(batch,observation));
CREATE TABLE IF NOT EXISTS r41_cycle_hold(sha TEXT PRIMARY KEY, kind TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS r41_feedback(sha TEXT PRIMARY KEY REFERENCES r41_batch, payload TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS r41_comparison AS
    SELECT o.id,o.kind,o.origin,s.paper,s.doi,o.source_sha,o.locator,
        json_extract(payload,'$.compound') AS molecule_literal,json_extract(payload,'$.inchi_key') AS inchi_key,
        json_extract(payload,'$.endpoint') AS endpoint,json_extract(payload,'$.dose') AS dose_literal,
        json_extract(payload,'$.author_wording') AS author_wording,
        json_extract(payload,'$.relative_magnitude_percent') AS relative_magnitude_percent,
        json_extract(payload,'$.quote') AS author_quote
    FROM r41_observation o JOIN r41_source s ON o.source_sha=s.sha WHERE o.kind!='AUTHOR_PASSAGE';
CREATE VIEW IF NOT EXISTS r41_assay_arm AS
    SELECT o.id AS comparison_id,j.key AS arm,json_extract(j.value,'$.well') AS well,
        json_extract(j.value,'$.cell') AS cell,json_extract(j.value,'$.stimulus') AS stimulus,
        json_extract(j.value,'$.lps_ng_ml') AS stimulus_ng_ml,json_extract(j.value,'$.compound_uM') AS compound_uM,
        json_extract(j.value,'$.hours') AS exposure_hours,json_extract(j.value,'$.batch') AS batch,
        json_extract(j.value,'$.atp_nmol') AS atp_nmol,json_extract(j.value,'$.viable_cells') AS viable_cells,
        o.source_sha,o.locator FROM r41_observation o,json_each(o.payload) j
    WHERE o.kind='PAIRED_ASSAY' AND j.key IN ('treated','control');
CREATE VIEW IF NOT EXISTS r41_review_queue AS SELECT o.id,s.paper,s.doi,o.source_sha,o.locator,
    json_extract(payload,'$.cues') AS discovery_cues,json_extract(payload,'$.quote') AS author_quote,
    json_extract(payload,'$.compound') AS compound FROM r41_observation o
    JOIN r41_source s ON o.source_sha=s.sha WHERE o.kind='AUTHOR_PASSAGE';
"""


def record(c, batch, origin, kind, sha, locator, data):
    payload = packed(data)
    key = digest(packed([origin, kind, sha, locator, data]).encode())
    c.execute("INSERT OR IGNORE INTO r41_observation VALUES(?,?,?,?,?,?)",
              (key, origin, kind, sha, locator, payload))
    c.execute("INSERT OR IGNORE INTO r41_batch_item VALUES(?,?)", (batch, key))


def source(c, sha, paper, doi, license_note, path, raw):
    c.execute("INSERT OR IGNORE INTO r41_source VALUES(?,?,?,?,?,?)",
              (sha, paper, doi, license_note, str(path), raw))


def xml_identity(root):
    ids = {i.get("pub-id-type"): i.text for i in root.findall("./front/article-meta/article-id")}
    paper = ids.get("pmcid") or ids.get("pmc") or ""
    return "PMC" + paper.removeprefix("PMC"), ids.get("doi")


ATP = re.compile(r"\b(?:ATP (?:levels?|content|concentrations?)|levels? of ATP|intracellular ATP)\b", re.I)
CUES = {
    "AUTHOR_NS_WORDING": r"not significantly different|no significant (?:change|difference|effect|increase)|did not significantly",
    "AUTHOR_UNCHANGED_WORDING": r"unchanged|did not (?:affect|alter|change)",
    "DECREASE_WORDING": r"decreas|reduc|deplet|declin|\bdrop\b",
    "INCREASE_WORDING": r"increas|elevat|upregulat",
    "DOSE_RESPONSE_WORDING": r"dose.depend|dose.response|concentrations? of .*? and .*?[μµ]M",
}


# Single-arm grammatical scopes. Cues from other clauses never become outcomes.
ARM_RULES = (
    (r"intracellular ATP content(?: and ADP/ATP ratio)? (?:was|were) unchanged by (?P<mol>[a-z][a-z-]+) treatment", "AUTHOR_UNCHANGED_WORDING"),
    (r"treating them with (?P<mol>[a-z][a-z-]+) alone, which did not affect ATP levels", "AUTHOR_UNCHANGED_WORDING"),
    (r"(?P<mol>[a-z][a-z-]+) supplementation increased ATP levels in a dose-dependent manner", "AUTHOR_INCREASE_DOSE_DEPENDENT"),
)

# "respectively" binds named exposures to consecutive source percentages.
# Qualifiers such as ≥ are kept as printed. No implicit control or per-cell units.
DEPLETED_PAIR = re.compile(
    r"intracellular levels of ATP were depleted by "
    r"(?P<m1>[a-z][a-z-]+) \((?P<d1>[≥≤<>]?\d+(?:\.\d+)? [µμ]M)\) and "
    r"(?P<m2>[a-z][a-z-]+) \((?P<d2>[≥≤<>]?\d+(?:\.\d+)? [µμ]M)\) by "
    r"(?P<v1>\d+(?:\.\d+)?)%(?: \(p[<=>][0-9.]+\))? and "
    r"(?P<v2>\d+(?:\.\d+)?)%(?: \(p[<=>][0-9.]+\))?, respectively", re.I)


def licensed(root):
    terms = " ".join(ET.tostring(node, encoding="unicode") for node in root.findall("./front/article-meta/permissions/license"))
    permitted = re.search(r"creativecommons.org/licenses/by/[0-9.]+/", terms, re.I)
    return terms if permitted and not re.search(r"licenses/by-(nc|nd|sa)", terms, re.I) else None


def mine(c, batch, sha, root):
    # A paragraph may describe several arms/endpoints. Cues select reading work only.
    # Traverse section paragraphs, excluding figures, references and nested panels.
    def sections(parent, prefix, in_results=False):
        for ix, sec in enumerate(parent.findall("sec"), 1):
            loc = f"{prefix}/sec[{ix}]"
            title = sec.findtext("title", "")
            if re.search(r"discussion|method", title, re.I) and not re.search("result", title, re.I):
                continue
            result = in_results or bool(re.search(r"results|findings", title, re.I))
            if result:
                for j, p in enumerate(sec.findall("p"), 1):
                    body = (p.text or "") + "".join(
                        ("" if child.tag in ("fig", "table-wrap", "supplementary-material") else "".join(child.itertext()))
                        + (child.tail or "") for child in p)
                    for match in re.finditer(r".+?(?:(?<!Fig)(?<!Figs)[.!?](?=\s+[A-Z(])|$)", " ".join(body.split())):
                        quote = match.group().strip()
                        if not ATP.search(quote) or re.search(r"previous report|has been shown|prior stud", quote, re.I):
                            continue
                        cues = [k for k, expr in CUES.items() if re.search(expr, quote, re.I)]
                        # Keep exact offsets into the normalized direct paragraph text.
                        record(c, batch, "LITERATURE", "AUTHOR_PASSAGE", sha, f"{loc}/p[{j}]",
                               dict(quote=quote, offset=match.start()+len(match.group())-len(match.group().lstrip()),
                                    cues=cues or ["BASELINE"], target_effect=None))
                        pair = DEPLETED_PAIR.search(quote)
                        if pair:
                            for arm_no in ("1", "2"):
                                record(c, batch, "LITERATURE", "ARM_WORDING", sha, f"{loc}/p[{j}]",
                                       dict(compound=pair["m"+arm_no], dose=pair["d"+arm_no],
                                            author_wording="AUTHOR_RELATIVE_DECREASE", quote=quote,
                                            exact_clause=pair.group(), relative_magnitude_percent=float(pair["v"+arm_no]),
                                            endpoint="ATP_CONTENT", target_effect=None))
                        for pattern, wording in ARM_RULES:
                            arm = re.search(pattern, quote, re.I)
                            if arm:
                                record(c, batch, "LITERATURE", "ARM_WORDING", sha, f"{loc}/p[{j}]",
                                       dict(compound=arm["mol"], author_wording=wording, quote=quote,
                                            exact_clause=arm.group(), endpoint="ATP_CONTENT", target_effect=None))
            sections(sec, loc, result)
    body = root.find("./body")
    if body is not None:
        sections(body, "/article/body")


def ingest(c, kind, path, expected):
    raw = verified(path, expected)
    data = json.loads(raw)
    if c.execute("SELECT 1 FROM r41_batch WHERE sha=? AND kind=?", (expected, kind)).fetchone():
        return dict(status="ALREADY_COMMITTED", batch=expected)
    before = c.execute("SELECT count(*) FROM r41_observation").fetchone()[0]
    c.execute("INSERT INTO r41_batch VALUES(?,?,?,?)", (expected, kind, str(path), "{}"))
    holds = []
    if kind == "feedback":
        if not isinstance(data, dict):
            raise ValueError("feedback must be a model report object")
        c.execute("INSERT INTO r41_feedback VALUES(?,?)", (expected, packed(data)))
    elif kind == "jats":
        for entry in data["sources"]:
            if entry.get("status") in ("JATS_ATTEMPT_FAILED","PMCID_UNRESOLVED_JATS_NOT_RETRIEVED"):
                holds.append(dict(paper=entry.get("pmcid"), doi=entry.get("doi"), reason=entry["status"]))
                continue
            file = path.parent / entry["file"]
            xml = verified(file, entry["sha256"])
            root = ET.fromstring(xml)
            paper, doi = xml_identity(root)
            if (paper, doi) != (entry["pmcid"], entry["doi"]):
                raise ValueError("author identity mismatch")
            terms = licensed(root)
            if not terms:
                holds.append(dict(paper=paper, reason="LICENSE_REVIEW"))
                continue
            source(c, entry["sha256"], paper, doi, terms, file, xml)
            mine(c, expected, entry["sha256"], root)
    elif kind == "pilot":
        if data.get("schema") != "asterloom.versioned-task-dataset/1":
            raise ValueError("unsupported pilot schema")
        def member(name):
            return verified(path.parent/name, data["files"][name]["sha256"])
        manifest = json.loads(member("EVIDENCE_MANIFEST.json"))
        for sha, entry in manifest.items():
            if entry["format"] not in (".xml", ".nxml"):
                continue
            xml = verified(entry["path"], sha)
            root = ET.fromstring(xml)
            paper, doi = xml_identity(root)
            if paper != entry["paper_key"] or entry["sha256"] != sha:
                raise ValueError("manifest paper identity mismatch")
            terms = licensed(root)
            if terms:
                source(c, sha, paper, doi, terms, entry["path"], xml)
                mine(c, expected, sha, root)
            else:
                holds.append(dict(paper=paper, reason="NEW_MINING_LICENSE_REVIEW"))
        for row in map(json.loads, member("SOURCE_ONLY.jsonl").splitlines()):
            sha = row["source_sha256"]
            entry = manifest[sha]
            if entry["paper_key"] != row["paper_key"] or entry["sha256"] != sha:
                raise ValueError("pilot source identity mismatch")
            xml = verified(entry["path"], sha)
            doi = xml_identity(ET.fromstring(xml))[1] if entry["format"] in (".xml", ".nxml") else row.get("doi")
            source(c, sha, row["paper_key"], doi, "UPSTREAM_FROZEN_PILOT", entry["path"], xml)
            evidence = json.loads(row["record_json"]).get("r39_evidence", {})
            record(c, expected, "LITERATURE", "PILOT_RECORD", sha, row["source_locator"],
                   dict(row_id=row["row_id"], compound=row["compound"], model=row["model"], doi=doi,
                        dose=row["dose"], time=row["exposure_time"], endpoint=row["endpoint"],
                        quote=evidence.get("author_sentence"), upstream=row))
    elif kind == "comparisons":
        if data.get("schema") != "asterloom.paired-assay/1":
            raise ValueError("unsupported paired assay")
        for row in data["comparisons"]:
            asset = row["asset"]
            origin = row["origin"]
            if origin not in ("EXPERIMENT", "LITERATURE"):
                raise ValueError("unknown origin")
            file = path.parent / asset["file"]
            original = verified(file, asset["sha256"])
            # Only ingest exact native JSON arm records, never a transcription without its parent.
            native = json.loads(original)["comparisons"][row["index"]]
            if type(row["index"]) is not int or row["index"] < 0:
                raise ValueError("invalid original index")
            if native["origin"] != origin or (origin == "LITERATURE" and not native.get("doi")):
                raise ValueError("origin identity mismatch")
            source(c, asset["sha256"], native.get("study_id"), native.get("doi"), native["rights_basis"], file, original)
            record(c, expected, origin, "PAIRED_ASSAY", asset["sha256"], f"/comparisons/{row['index']}", native)
    else:
        raise ValueError("unsupported input")
    added = c.execute("SELECT count(*) FROM r41_observation").fetchone()[0] - before
    receipt = dict(batch=expected, status="COMMITTED", observed_new=added, holds=holds)
    c.execute("UPDATE r41_batch SET receipt=? WHERE sha=?", (packed(receipt), expected))
    c.execute("DELETE FROM r41_cycle_hold WHERE sha=?", (expected,))
    return receipt


def register(sub):
    command = sub.add_parser("model-data", help="Incremental ATP model-task ledger")
    actions = command.add_subparsers(dest="model_action", required=True)
    sync = actions.add_parser("sync")
    sync.add_argument("kind", choices=("jats", "pilot", "feedback", "comparisons"))
    sync.add_argument("--input", type=Path, required=True)
    sync.add_argument("--sha", required=True)
    cycle = actions.add_parser("cycle", help="Process a bounded number of pending entries from a pinned batch list")
    cycle.add_argument("--input", type=Path, required=True)
    cycle.add_argument("--sha", required=True)
    cycle.add_argument("--limit", type=int, default=10)
    for name in ("audit", "freeze"):
        action = actions.add_parser(name)
        action.add_argument("--decisions", type=Path)
        action.add_argument("--decision-sha", help="Externally trusted digest of the current owner review release")
        if name == "freeze":
            action.add_argument("--out", type=Path, required=True)


def run(root, args):
    from .r41_model_export import assess, freeze
    db = root / "state/science.sqlite3"
    if not db.is_file():
        raise ValueError("set up a new Home or clone a verified Evidence Core first")
    with closing(sqlite3.connect(db, timeout=60)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)
        if args.model_action == "cycle":
            if not 1 <= args.limit <= 100:
                raise ValueError("cycle limit must be 1..100")
            entries = json.loads(verified(args.input, args.sha))
            if entries["schema"] != "asterloom.model-batches/1":
                raise ValueError("unsupported cycle list")
            receipts = []
            attempted = set()
            for item in entries["batches"]:
                if item["sha"] in attempted:
                    continue
                attempted.add(item["sha"])
                c.execute("BEGIN IMMEDIATE")
                try:
                    with c:
                        item_path = args.input.parent/item["path"]
                        if c.execute("SELECT 1 FROM r41_batch WHERE sha=? AND kind=?", (item["sha"],item["kind"])).fetchone():
                            continue
                        held = c.execute("SELECT reason FROM r41_cycle_hold WHERE sha=? AND kind=?", (item["sha"],item["kind"])).fetchone()
                        if held:
                            continue
                        receipts.append(ingest(c, item["kind"], item_path.resolve(), item["sha"]))
                except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error, ET.ParseError) as error:
                    with c:
                        c.execute("INSERT OR IGNORE INTO r41_cycle_hold VALUES(?,?,?)", (item["sha"],item["kind"],str(error)))
                    receipts.append(dict(batch=item["sha"], status="HOLD", reason=str(error)))
                if len(receipts) >= args.limit:
                    break
            holds = [dict(batch=h[0],reason=h[1]) for h in c.execute("SELECT sha,reason FROM r41_cycle_hold ORDER BY sha") if h[0] in {e["sha"] for e in entries["batches"]}]
            return dict(status="CYCLE_WITH_HOLDS" if holds else "CYCLE_COMPLETE", receipts=receipts, held=holds)
        c.execute("BEGIN IMMEDIATE" if args.model_action == "sync" else "BEGIN")
        with c:
            if args.model_action == "sync":
                return ingest(c, args.kind, args.input.resolve(), args.sha)
            result, samples, stages = assess(c, args.decisions, args.decision_sha)
            if args.model_action == "audit":
                return result
            return freeze(args.out, result, samples, stages)

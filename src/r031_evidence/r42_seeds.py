"""Import frozen R17/SM297 identities and traceable upstream evidence, once per Home.

Run with the chemistry worker's RDKit Python. No chemistry dependency is loaded
by the normal evidence engine. Never strip salt, choose largest fragment or infer
a stereoisomer from a search alias.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

V5_PIN = "37ab94f72e48cbebeb9794958a88d848a66c087fc7b9642105c846c1387250cd"
ALL372 = "R17_ALL372_WEAK_LABELS_AND_SOURCES_NO_FAKE_NEGATIVES.jsonl"
AUDIT452 = "R17_ALL452_ATP_SOURCE_SEMANTIC_INCLUSION_EXCLUSION_AUDIT.jsonl"
ELIGIBLE32 = "R17_MOLECULE_LEVEL_ELIGIBLE_WEAK_TRAINING_LABELS.jsonl"

# Explicitly qualified search aliases from this finite, audited registry.
# Other aliases remain visible in r42_seed_name with a HOLD.
APPROVED_ALIASES = {
    "5-cqa", "5'-ump", "indole-3-propionic acid", "4-cqa", "4-caffeoylquinic acid",
    "aurapten", "silybin", "rutecarpine", "pterosin a", "ginsenoside compound k",
    "delphinidin", "pqq", "pqq cofactor", "astaxanthine", "aicar", "alnustone",
    "cardamomin", "vitamin b2", "banisterine", "telepathine", "acteoside", "caprylic acid",
    "niacinamide", "isopentenyl pyrophosphate", "sirolimus", "norharman", "dihydronicotinamide riboside",
    "reduced nicotinamide riboside", "glucose-6-phosphate", "dextrose", "gaba", "4-aminobutanoic acid",
    "γ-aminobutyric acid", "ethyl alcohol", "trans-piceid", "polydatin", "piericidine a",
    "doconexent", "dimethyl propanedioate", "malonic acid dimethyl ester", "coleonol",
    "gentisic acid", "β-elemene", "homoorientin", "iso-orientin", "l-triiodothyronine",
    "danthron", "l-baiba", "myricanol", "majonoside r2", "tryptanthrine", "pyocyanine",
}
DDL = """
CREATE TABLE IF NOT EXISTS r42_seed_import(
 input_id TEXT PRIMARY KEY, manifest TEXT NOT NULL, rdkit_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_molecule(
 molecule TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK(origin IN ('seed','discovered')),
 preferred_name TEXT NOT NULL, isomeric_smiles TEXT, full_inchikey TEXT, identity_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_seed_record(
 cohort TEXT NOT NULL, member_id TEXT NOT NULL, molecule TEXT NOT NULL REFERENCES r42_molecule,
 name TEXT NOT NULL, natural_status TEXT, source_sha TEXT NOT NULL, source_locator TEXT NOT NULL,
 raw_smiles TEXT NOT NULL, raw_full_key TEXT NOT NULL, standardized_smiles TEXT NOT NULL,
 standard_full_key TEXT NOT NULL, pubchem_cid TEXT, identity_status TEXT NOT NULL,
 weak INTEGER CHECK(weak IN(0,1) OR weak IS NULL), weak_status TEXT, record_json TEXT NOT NULL,
 PRIMARY KEY(cohort,member_id)
);
CREATE TABLE IF NOT EXISTS r42_seed_name(
 molecule TEXT NOT NULL REFERENCES r42_molecule, cohort TEXT NOT NULL, member_id TEXT NOT NULL,
 name TEXT NOT NULL, norm TEXT NOT NULL, status TEXT NOT NULL, basis TEXT NOT NULL,
 PRIMARY KEY(cohort,member_id,name,basis)
);
CREATE VIEW IF NOT EXISTS r42_names AS
 SELECT min(molecule) AS molecule,min(name) AS name FROM r42_seed_name
 GROUP BY norm HAVING count(DISTINCT molecule)=1 AND max(status='SEARCH')=1;
CREATE VIEW IF NOT EXISTS r42_seed_scope AS
 SELECT molecule, max(cohort='R17') AS r17,max(cohort='SM297') AS sm297,
 max(weak) AS weak FROM r42_seed_record GROUP BY molecule;
CREATE TABLE IF NOT EXISTS r42_prior_evidence(
 evidence_id TEXT PRIMARY KEY, molecule TEXT REFERENCES r42_molecule, upstream_member TEXT NOT NULL,
 paper_id TEXT NOT NULL,doi TEXT,pmid TEXT,pmcid TEXT,endpoint TEXT,reported_direction TEXT,
 reported_value TEXT,reported_unit TEXT,comparison TEXT,source_location TEXT,source_excerpt TEXT,
 source_sha TEXT NOT NULL,source_locator TEXT NOT NULL,record_json TEXT NOT NULL,
 status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_weak_audit(
 evidence_id TEXT PRIMARY KEY REFERENCES r42_prior_evidence, decision TEXT NOT NULL,
 source_sha TEXT NOT NULL, source_locator TEXT NOT NULL, record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_weak_support(
 molecule TEXT NOT NULL REFERENCES r42_molecule, evidence_id TEXT NOT NULL REFERENCES r42_weak_audit,
 PRIMARY KEY(molecule,evidence_id)
);
CREATE TABLE IF NOT EXISTS r42_seed_prior_signal(
 signal_id TEXT PRIMARY KEY, molecule TEXT NOT NULL REFERENCES r42_molecule,paper_id TEXT NOT NULL,
 doi TEXT,pmcid TEXT,pmid TEXT,source_sha TEXT NOT NULL,source_locator TEXT NOT NULL,
 record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_seed_natural_source(
 provenance_id TEXT PRIMARY KEY, molecule TEXT NOT NULL REFERENCES r42_molecule,
 source_sha TEXT NOT NULL, source_locator TEXT NOT NULL,record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS r42_prior_identity(
 member_id TEXT PRIMARY KEY, molecule TEXT NOT NULL REFERENCES r42_molecule,source_sha TEXT NOT NULL,
 source_locator TEXT NOT NULL,record_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS r42_prior_export AS
 SELECT 'V5_LEGACY' AS origin,e.evidence_id,e.molecule,e.upstream_member,e.doi,e.pmid,e.pmcid,
        e.paper_id,e.endpoint,e.reported_direction,e.reported_value,e.reported_unit,e.comparison,
        json_extract(e.record_json,'$.evidence.cell_line') AS cell_line,
        json_extract(e.record_json,'$.evidence.cell_type') AS cell_type,
        json_extract(e.record_json,'$.evidence.dose_value') AS dose_literal,
        json_extract(e.record_json,'$.evidence.dose_unit') AS dose_unit,
        json_extract(e.record_json,'$.evidence.duration_value') AS duration_literal,
        json_extract(e.record_json,'$.evidence.duration_unit') AS duration_unit,
        e.source_location,e.source_excerpt,e.source_sha,e.source_locator,e.status,
        a.decision AS r17_semantic_audit, 0 AS training_approved
 FROM r42_prior_evidence e LEFT JOIN r42_weak_audit a USING(evidence_id);
CREATE VIEW IF NOT EXISTS r42_prior_coverage AS
 WITH experiments AS (
    SELECT molecule, count(*) AS rows_recorded, count(DISTINCT lower(doi)) AS papers,
       sum(reported_value GLOB '*[0-9]*') AS rows_with_numeric_text,
       count(DISTINCT endpoint) AS endpoints
    FROM r42_prior_evidence GROUP BY molecule
 ), signals AS (
    SELECT molecule, count(*) AS hints, count(DISTINCT paper_id) AS signal_papers
    FROM r42_seed_prior_signal GROUP BY molecule
 ), audited AS (
    SELECT e.molecule,count(*) AS atp_mentions
    FROM r42_prior_evidence e JOIN r42_weak_audit a USING(evidence_id) GROUP BY e.molecule
 )
 SELECT s.molecule,m.preferred_name,s.r17,s.sm297,s.weak AS prior_weak,
    ifnull(e.rows_recorded,0) AS legacy_evidence_rows, ifnull(e.papers,0) AS legacy_evidence_papers,
    ifnull(e.rows_with_numeric_text,0) AS rows_with_numeric_text, ifnull(e.endpoints,0) AS legacy_endpoints,
    ifnull(a.atp_mentions,0) AS r17_audited_atp_mentions,ifnull(h.hints,0) AS legacy_screening_signals,
    ifnull(h.signal_papers,0) AS legacy_signal_papers
 FROM r42_seed_scope s JOIN r42_molecule m USING(molecule)
 LEFT JOIN experiments e USING(molecule) LEFT JOIN signals h USING(molecule) LEFT JOIN audited a USING(molecule);
CREATE VIEW IF NOT EXISTS r42_legacy_identity_holds AS
 SELECT upstream_member,'LEGACY_OUTSIDE_SEED' AS origin,status,source_sha,
    'identity_registry/molecule_id=' || upstream_member AS registry_locator,
    count(*) AS source_rows,count(DISTINCT doi) AS distinct_papers
 FROM r42_prior_evidence WHERE molecule IS NULL GROUP BY upstream_member,status,source_sha;
CREATE INDEX IF NOT EXISTS r42_prior_molecule ON r42_prior_evidence(molecule,doi);
CREATE INDEX IF NOT EXISTS r42_signal_molecule ON r42_seed_prior_signal(molecule,doi);
"""


def packed(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def normalized(name):
    return " ".join(unicodedata.normalize("NFC", name).split()).casefold()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def index(rows, field):
    by = {r[field]: r for r in rows}
    if len(rows) != len(by):
        raise ValueError(f"duplicate upstream key {field}")
    return by


def identity(smiles):
    from rdkit import Chem
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("source SMILES cannot be parsed")
    return Chem.MolToSmiles(mol, isomericSmiles=True), Chem.MolToInchiKey(mol)


def write_inputs(c, assets, manifest):
    sha = {k:v["sha256"] for k,v in manifest.items()}
    r17 = read_lines(assets["r17"])
    labels = read_lines(assets["weak32"])
    if index(labels,"molecule_id") != {r["molecule_id"]:r for r in r17 if r["weak_label_1_increase_0_decrease"] is not None}:
        raise ValueError("R17 labels and 372-member registry disagree")
    structures = index(read_csv(assets["structures"]), "molecule_id")
    sm = read_csv(assets["molecules"])
    if set(index(sm,"molecule_id")) != set(structures):
        raise ValueError("SM297 molecule and structure members disagree")
    with closing(sqlite3.connect(assets["v5"].resolve().as_uri()+"?mode=ro&immutable=1", uri=True)) as prior:
        prior.row_factory = sqlite3.Row
        refs = index([dict(r) for r in prior.execute("SELECT * FROM molecule_reference")],"molecule_id")
        evidence = [dict(r) for r in prior.execute("SELECT * FROM raw_evidence ORDER BY evidence_id")]
        papers = index([dict(r) for r in prior.execute("SELECT * FROM raw_papers")],"paper_id")
    if set(refs) != set(index(r17,"molecule_id")):
        raise ValueError("v5 and R17 membership disagree")

    members = {}
    for cohort, rows in (("R17",r17),("SM297",sm)):
        for line,r in enumerate(rows,1):
            mid = r["molecule_id"]
            if cohort == "R17":
                ref = refs[mid]
                if (r["smiles"], r["inchi_key"]) != (ref["smiles"], ref["inchikey"]):
                    raise ValueError(f"v5 and R17 structure drift for {mid}")
                name, raw, submitted, declared = r["name"],r["smiles"],r["smiles"],r["inchi_key"]
                cid, natural = None,ref["natural_status_original"]
                src_sha, loc = sha["r17"],f"line:{line}"
                weak, weak_status = r["weak_label_1_increase_0_decrease"],r["status"]
                data = dict(label=r, v5_reference=ref)
                names = [(name,"PRIMARY")]
            else:
                s = structures[mid]
                name, raw = r["preferred_name_en"],s["raw_smiles"]
                submitted, declared = s["standardized_smiles"],s["inchi_key"]
                cid, natural = s["pubchem_cid"],r["natural_product_status"]
                src_sha, loc = sha["molecules"],f"molecule_id={mid}"
                weak, weak_status = None,None
                data = dict(member=r,structure=s,structure_source_sha=sha["structures"])
                names = [(name,"PRIMARY"),(r["preferred_name_zh"],"CHINESE")]
                names.extend((alias,"SYNONYM") for alias in r["synonyms"].split(";"))
            raw_smi, raw_key = identity(raw)
            std_smi, std_key = identity(submitted)
            if std_key != declared:
                raise ValueError(f"standardized SMILES differs from registered key for {cohort}/{mid}")
            mol_id = "IK:"+raw_key
            status = "EXACT_FULL_STRUCTURE"
            if raw_key != std_key or raw_smi != std_smi:
                mol_id = "FORM:"+cohort+":"+mid
                status = "RAW_STANDARD_FORM_DISAGREEMENT"
            prior_row = c.execute("SELECT isomeric_smiles FROM r42_molecule WHERE molecule=?",(mol_id,)).fetchone()
            if prior_row and prior_row[0] != raw_smi:
                mol_id = "FORM:"+cohort+":"+mid
                status = "FULL_KEY_STEREO_OR_TAUTOMER_COLLISION"
            c.execute("INSERT OR IGNORE INTO r42_molecule VALUES(?,?,?,?,?,?)",(mol_id,"seed",name,raw_smi,raw_key,status))
            c.execute("INSERT INTO r42_seed_record VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cohort,mid,mol_id,name,natural,src_sha,loc,raw,raw_key,submitted,std_key,cid,status,weak,weak_status,packed(data)))
            members[cohort,mid] = mol_id
            for alias,basis in names:
                alias = alias.strip()
                if not alias:
                    continue
                quality = "SEARCH" if basis != "SYNONYM" or normalized(alias) in APPROVED_ALIASES else "IDENTITY_REVIEW"
                if status != "EXACT_FULL_STRUCTURE":
                    quality = "FORM_REVIEW"
                c.execute("INSERT OR IGNORE INTO r42_seed_name VALUES(?,?,?,?,?,?,?)",
                          (mol_id,cohort,mid,alias,normalized(alias),quality,basis))

    for e in evidence:
        p = papers[e["paper_id"]]
        mol_id = members.get(("R17", e["molecule_id"]))
        status = "PRIOR_UNREVIEWED" if mol_id else "MEMBER_OUTSIDE_SEEDS_HOLD"
        # The upstream table describes existing agent extraction; preserve its complete
        # record without turning an author's excerpt or a screenshot estimate into Gold.
        c.execute("INSERT INTO r42_prior_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (e["evidence_id"],mol_id,e["molecule_id"],e["paper_id"],p["doi"],p["pmid"],p["pmcid"],
                   e["endpoint"],e["effect_direction"],e["quantitative_value"],e["quantitative_unit"],
                   e["comparator"],e["source_location"],e["source_excerpt"],sha["v5"],
                   "raw_evidence/evidence_id="+e["evidence_id"],packed(dict(evidence=e,paper=p)),status))
    evidence_by_id = index(evidence,"evidence_id")
    audited = read_lines(assets["audit452"])
    audit_by_id = index(audited,"evidence_id")
    for n,e in enumerate(audited,1):
        original = evidence_by_id[e["evidence_id"]]
        if original["molecule_id"] != e["molecule_id"] or (papers[original["paper_id"]]["doi"] or "").lower() != (e["doi"] or "").lower():
            raise ValueError(f"source identity conflict in {e['evidence_id']}")
        c.execute("INSERT INTO r42_weak_audit VALUES(?,?,?,?,?)",
                  (e["evidence_id"],e["reason"],sha["audit452"],f"line:{n}",packed(e)))
    for r in r17:
        for eid in r["source_evidence_ids"]:
            a = audit_by_id[eid]
            if a["molecule_id"] != r["molecule_id"] or a["reason"] != "WEAK_LABEL_ELIGIBLE_NOT_SCIENCE_GOLD":
                raise ValueError(f"R17 weak source integrity failure {eid}")
            c.execute("INSERT INTO r42_weak_support VALUES(?,?)",(members["R17",r["molecule_id"]],eid))
    signal_papers = index(read_csv(assets["papers"]),"paper_id")
    for r in read_csv(assets["candidate_signals"]):
        p = signal_papers[r["paper_id"]]
        c.execute("INSERT INTO r42_seed_prior_signal VALUES(?,?,?,?,?,?,?,?,?)",
                  (r["signal_id"],members["SM297",r["molecule_id"]],r["paper_id"],p["doi"],p["pmcid"],p["pmid"],
                   sha["candidate_signals"],"signal_id="+r["signal_id"],packed(dict(signal=r,paper=p,paper_source_sha=sha["papers"]))))
    for r in read_csv(assets["natural_provenance"]):
        c.execute("INSERT INTO r42_seed_natural_source VALUES(?,?,?,?,?)",
                  (r["provenance_id"],members["SM297",r["molecule_id"]],sha["natural_provenance"],
                   "provenance_id="+r["provenance_id"],packed(r)))


def import_prior_names(c, v5, sha):
    with closing(sqlite3.connect(v5.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)) as prior:
        prior.row_factory = sqlite3.Row
        for row in prior.execute("SELECT * FROM raw_evm"):
            mid = row["evidence_molecule_id"]
            seed = c.execute("SELECT molecule,standard_full_key FROM r42_seed_record WHERE cohort='R17' AND member_id=?", (mid,)).fetchone()
            if not seed or row["inchi_key"] != seed[1]:
                raise ValueError(f"EVM registry disagrees with R17 for {mid}")
            c.execute("INSERT INTO r42_prior_identity VALUES(?,?,?,?,?)",
                      (mid,seed[0],sha,"raw_evm/evidence_molecule_id="+mid,packed(dict(row))))
            for literal in (row["synonyms"] or "").split(";"):
                literal=literal.strip()
                if not literal:
                    continue
                norm=normalized(literal)
                quality="SEARCH" if norm in APPROVED_ALIASES else "IDENTITY_REVIEW"
                c.execute("INSERT OR IGNORE INTO r42_seed_name VALUES(?,?,?,?,?,?,?)",
                          (seed[0],"R17",mid,literal,norm,quality,"V5_RAW_EVM_ALIAS"))


def metrics(c):
    def scalar(sql):
        return c.execute(sql).fetchone()[0]
    counts = {
        name:scalar("SELECT count(*) FROM "+name) for name in (
        "r42_molecule","r42_seed_record","r42_names","r42_prior_evidence","r42_weak_audit","r42_weak_support",
        "r42_seed_prior_signal","r42_seed_natural_source")
    }
    return dict(counts=counts, seed_forms=scalar("SELECT count(*) FROM r42_seed_scope"),
                exact_overlap=scalar("SELECT count(*) FROM r42_seed_scope WHERE r17 AND sm297"),
                prior_evidence_papers=scalar("SELECT count(DISTINCT doi) FROM r42_prior_evidence"),
                outside_seed_rows=scalar("SELECT count(*) FROM r42_prior_evidence WHERE molecule IS NULL"),
                prior_evidence_seed_molecules=scalar("SELECT count(DISTINCT molecule) FROM r42_prior_evidence"),
                exact_id_crosswalk_holds=[r[0] for r in c.execute("SELECT member_id FROM r42_seed_record WHERE identity_status<>'EXACT_FULL_STRUCTURE' ORDER BY 1")],
                weak_status=dict(c.execute("SELECT weak_status,count(*) FROM r42_seed_record WHERE cohort='R17' GROUP BY weak_status")),
                historical_support_links_for_labeled=scalar("SELECT count(*) FROM r42_weak_support JOIN r42_seed_scope USING(molecule) WHERE weak IS NOT NULL"))


def run(db, r17_dir, sm_dir, v5):
    from rdkit import rdBase
    assets = dict(r17=r17_dir/ALL372,audit452=r17_dir/AUDIT452,weak32=r17_dir/ELIGIBLE32,v5=v5)
    assets.update({key:sm_dir/(key+".csv") for key in ("molecules","structures","candidate_signals","papers","natural_provenance")})
    manifest = {k:dict(path=str(p.resolve()),sha256=file_sha(p)) for k,p in assets.items()}
    if manifest["v5"]["sha256"] != V5_PIN:
        raise ValueError("R17 source database SHA mismatch")
    identity_sha = hashlib.sha256(packed({k:v["sha256"] for k,v in manifest.items()}).encode()).hexdigest()
    if not db.is_file():
        raise ValueError("expected initialized Evidence Core")
    with closing(sqlite3.connect(db,timeout=90)) as c:
        if c.execute("PRAGMA user_version").fetchone()[0] != 832:
            raise ValueError("expected Evidence Core schema 832")
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(DDL)
        c.execute("BEGIN IMMEDIATE")
        with c:
            pins = c.execute("SELECT input_id FROM r42_seed_import").fetchall()
            if pins and pins != [(identity_sha,)]:
                raise ValueError("seed input differs from frozen inventory; stage a reviewed registry revision")
            if not pins:
                write_inputs(c,assets,manifest)
                if any(file_sha(path) != manifest[k]["sha256"] for k,path in assets.items()):
                    raise ValueError("source changed during seed import")
                c.execute("INSERT INTO r42_seed_import VALUES(?,?,?)",(identity_sha,packed(manifest),rdBase.rdkitVersion))
            if not c.execute("SELECT 1 FROM r42_prior_identity LIMIT 1").fetchone():
                import_prior_names(c,v5,manifest["v5"]["sha256"])
            return dict(status="ALREADY_IMPORTED" if pins else "IMPORTED",input_sha=identity_sha,**metrics(c))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--r17-dir",type=Path,required=True)
    parser.add_argument("--sm297-dir",type=Path,required=True)
    parser.add_argument("--v5",type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.db,args.r17_dir,args.sm297_dir,args.v5),indent=2,ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Fail-closed qualification and immutable export for the R17 continuous target."""
import csv
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from .r41_model_data import TASK, digest, packed, verified

CONTEXT = ("cell", "stimulus", "lps_ng_ml", "hours", "pretreat_hours", "vehicle", "batch", "protocol", "medium")
STAGES = ("SOURCE_ONLY", "STRUCTURED_CANDIDATE", "SCIENCE_REVIEWED", "GOLD_APPROVED", "MODEL_READY")


def number(value):
    return type(value) in (float, int) and math.isfinite(value)


def known(value):
    return isinstance(value, str) and value.strip().lower() not in ("", "unknown", "none", "na", "n/a", "null", "hold")


def evaluate(row):
    t, c = row.get("treated", {}), row.get("control", {})
    tests = {
        "verified_identity": bool(re.fullmatch("[A-Z]{14}-[A-Z]{10}-[A-Z]", str(row.get("inchi_key")))),
        "structure_and_groups": all(known(row.get(k)) for k in ("scaffold", "study_id", "family", "structure_smiles")),
        "protocol_and_qc": all(known(row.get(k)) for k in ("assay_method", "coa_id", "qc_locator", "batch_source", "calibration_id"))
            and row.get("orthogonal_viability_pass") is True and row.get("spike_in_pass") is True,
        "target_cell": t.get("cell") == "RAW264.7",
        "stimulus_and_dose": t.get("stimulus") == "LPS" and t.get("lps_ng_ml") == 100,
        "matched_context": all(t.get(k) is not None and t.get(k) == c.get(k) for k in CONTEXT)
            and all(known(t.get(k)) for k in ("vehicle", "batch", "protocol", "medium"))
            and all(number(t.get(k)) and t[k] >= 0 for k in ("hours", "pretreat_hours")) and t.get("hours", 0) > 0,
        "paired_vehicle": c.get("role") == "MATCHED_VEHICLE" and t.get("role") == "TREATED"
            and known(t.get("well")) and known(c.get("well")) and t.get("well") != c.get("well")
            and t.get("additional_treatments") == [] and c.get("additional_treatments") == []
            and not c.get("inchi_key") and t.get("control_well") == c.get("well") and number(c.get("compound_uM")) and c["compound_uM"] == 0
            and number(t.get("compound_uM")) and t["compound_uM"] > 0 and t.get("inchi_key") == row.get("inchi_key"),
        "calibrated_atp_per_live_cell": row.get("endpoint") == "INTRACELLULAR_ATP_CONTENT"
            and all(number(arm.get("raw_luminescence")) and arm["raw_luminescence"] >= 0
                    and arm.get("count_method") == "INDEPENDENT_VIABLE_CELL_COUNT"
                    and number(arm.get("atp_nmol")) and arm["atp_nmol"] >= 0
                    and type(arm.get("viable_cells")) is int and arm["viable_cells"] > 0 for arm in (t,c)),
        "origin_and_rights": known(row.get("rights_basis")) and known(row.get("extractor"))
            and known(row.get("comparison_id")),
    }
    if tests["calibrated_atp_per_live_cell"]:
        tests["finite_effect"] = math.isfinite(t["atp_nmol"]/t["viable_cells"] - c["atp_nmol"]/c["viable_cells"])
    if row.get("origin") == "LITERATURE":
        tests["identified_paper"] = bool(re.fullmatch(r"10\.\d{4,9}/\S+", str(row.get("doi")), re.I))
    return [k for k, ok in tests.items() if not ok]


def ledger(path, sha, existing):
    if path is None and sha is None:
        return {}
    if not path or not sha:
        raise ValueError("both trusted decision release and its independently supplied SHA are required")
    data = json.loads(verified(path, sha))
    if data.get("task") != TASK or data.get("schema") != "asterloom.owner-decisions/1":
        raise ValueError("incorrect review scope")
    decisions = {}
    for event in data["decisions"]:
        key = event["id"]
        if key in decisions or key not in existing:
            raise ValueError("duplicate or unknown decision target")
        decisions[key] = event
    return decisions


def partition(samples):
    parent = list(range(len(samples)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, sample in enumerate(samples):
        for group, value in sample["groups"].items():
            key = group, value.strip().casefold()
            if key in seen:
                parent[root(i)] = root(seen[key])
            seen[key] = i
    components = defaultdict(list)
    for i, row in enumerate(samples):
        components[root(i)].append(row)
    ordered = sorted(components.values(), key=lambda part: digest(packed(sorted(r["id"] for r in part)).encode()))
    ntest = max(1, len(ordered)//5)
    for i, part in enumerate(ordered):
        for row in part:
            row["split"] = ("HOLD" if len(ordered) < 2 else "test" if i < ntest else "train")
    return len(ordered)


def assess(c, path=None, sha=None):
    rows = c.execute("SELECT id,origin,kind,source_sha,locator,payload FROM r41_observation ORDER BY id").fetchall()
    decisions = ledger(path, sha, {r[0] for r in rows})
    # Recompute each signed-off identity and replay its native pair before promotion.
    # A changed payload must never inherit the approval of an earlier observation.
    for key, origin, kind, source, loc, payload in rows:
        if key not in decisions:
            continue
        record = json.loads(payload)
        if key != digest(packed([origin, kind, source, loc, record]).encode()):
            raise ValueError("reviewed observation identity drift")
        asset = c.execute("SELECT raw FROM r41_source WHERE sha=?", (source,)).fetchone()
        if not asset or digest(asset[0]) != source:
            raise ValueError("reviewed native asset identity drift")
        if kind == "PAIRED_ASSAY":
            match = re.fullmatch(r"/comparisons/(0|[1-9]\d*)", loc)
            if not match:
                raise ValueError("reviewed native asset identity drift")
            if packed(json.loads(asset[0])["comparisons"][int(match[1])]) != payload:
                raise ValueError("reviewed pair differs from the native record")
    gaps, stages, queue, candidates, qual = Counter(), [], Counter(), [], []
    observation_ids = defaultdict(list)
    for key, origin, kind, source, loc, payload in rows:
        row = json.loads(payload)
        if kind == "PAIRED_ASSAY":
            observation_ids[(origin, row.get("study_id"), row.get("comparison_id"))].append(key)
    for key, origin, kind, source, loc, payload in rows:
        row = json.loads(payload)
        stage = STAGES[0]
        missing = []
        if kind == "AUTHOR_PASSAGE":
            queue.update(row["cues"])
        elif kind == "ARM_WORDING":
            stage = STAGES[1]
            missing = ["unverified_arm_context", "paired_assay_record", "calibrated_atp_per_live_cell"]
        elif kind == "PILOT_RECORD":
            stage = STAGES[1] if row.get("compound") and row.get("endpoint") and "RELATIVE" in row["endpoint"] else STAGES[0]
            upstream = json.loads(row["upstream"]["record_json"]).get("r39_evidence", {})
            missing = ["paired_assay_record", "calibrated_atp_per_live_cell", "verified_matched_context",
                       *upstream.get("task_gate", {}).get("missing", [])]
        else:
            stage = STAGES[1]
            missing = evaluate(row)
            if len(observation_ids[origin, row.get("study_id"), row.get("comparison_id")]) != 1:
                missing.append("conflicting_comparison_versions")
        event = decisions.get(key, {})
        reviewers = event.get("reviewers", [])
        extractor = row.get("extractor") if kind == "PAIRED_ASSAY" else "ASTERLOOM"
        if (stage == STAGES[1] and event.get("science") == "APPROVE" and isinstance(reviewers,list) and len(reviewers) == 2
                and all(known(r) for r in reviewers) and known(extractor)
                and len({r.strip().casefold() for r in reviewers+[extractor]}) == 3):
            stage = STAGES[2]
            if kind == "PAIRED_ASSAY" and event.get("gold") == "APPROVE" and known(event.get("owner")) and not missing:
                stage = STAGES[3]
                if event.get("training") == "APPROVE":
                    t, ctrl = row["treated"], row["control"]
                    study = str(row.get("doi") or row["study_id"]).strip().lower()
                    g = dict(inchi_key=row["inchi_key"], connectivity=row["inchi_key"][:14], structure=row["structure_smiles"],
                             scaffold=row["scaffold"], study=study,
                             family=row["family"], batch=t["batch"], biological_source=row["batch_source"], study_id=row["study_id"], source_asset=source)
                    sample = dict(id=key, origin=origin, source_sha=source, locator=loc,
                                  review_sha=sha, groups=g, structure_smiles=row["structure_smiles"],
                                  X={k:t[k] for k in (*CONTEXT,"compound_uM")},
                                  Y=t["atp_nmol"]/t["viable_cells"]-ctrl["atp_nmol"]/ctrl["viable_cells"],
                                  treated_nmol=t["atp_nmol"], treated_viable=t["viable_cells"],
                                  control_nmol=ctrl["atp_nmol"], control_viable=ctrl["viable_cells"])
                    condition = packed([origin,row["inchi_key"],row["endpoint"],{k:v for k,v in sample["X"].items() if k!="batch"}])
                    candidates.append((condition, sample))
        gaps.update(missing)
        stages.append(dict(id=key, origin=origin, kind=kind, stage=stage, missing=missing, source_sha=source, locator=loc))
    batches = defaultdict(set)
    batch_ids = defaultdict(set)
    for cond, sample in candidates:
        batch_ids[cond].add(sample["groups"]["batch"].strip().casefold())
        batches[cond].add(sample["groups"]["biological_source"].strip().casefold())
    qualified = {s["id"]:s for cond,s in candidates if len(batches[cond]) >= 2 and len(batch_ids[cond]) >= 2}
    for cond,s in candidates:
        if s["id"] not in qualified:
            gaps["independent_biological_batches"] += 1
    for stage in stages:
        if stage["id"] in qualified:
            stage["stage"] = STAGES[4]
            qual.append(qualified[stage["id"]])
        elif stage["stage"] == STAGES[3] and any(s["id"] == stage["id"] for _,s in candidates):
            stage["missing"].append("independent_biological_batches")
    components = partition(qual)
    counts = Counter(r["stage"] for r in stages)
    batches_info = [dict(sha=r[0],kind=r[1]) for r in c.execute("SELECT sha,kind FROM r41_batch ORDER BY sha")]
    feedback = [json.loads(r[0]) for r in c.execute("SELECT payload FROM r41_feedback ORDER BY sha")]
    imbalanced = any(type(r.get("weak_decrease")) is int and type(r.get("weak_increase")) is int
                     and r["weak_decrease"] < r["weak_increase"] for r in feedback)
    outcome_cues = ("AUTHOR_NS_WORDING","AUTHOR_UNCHANGED_WORDING","DECREASE_WORDING","DOSE_RESPONSE_WORDING")
    lanes = {}
    for cue in (("BASELINE", *outcome_cues) if imbalanced else ("BASELINE",)):
        # The baseline always samples all matched papers irrespective of effect wording.
        seen_papers = set()
        picks = []
        for key, paper, cues in c.execute("SELECT id,paper,discovery_cues FROM r41_review_queue ORDER BY id"):
            if paper not in seen_papers and (cue == "BASELINE" or cue in json.loads(cues)):
                picks.append(key)
                seen_papers.add(paper)
        lanes[cue] = picks
    searches = ["RAW264.7 AND LPS AND intracellular ATP AND vehicle",

                "macrophage AND ATP content AND (decrease OR depletion)",
                "macrophage AND ATP content AND (unchanged OR no significant difference)",
                "macrophage AND ATP content AND dose response"]
    return (dict(task=TASK, stage_counts_exclusive={k:counts[k] for k in STAGES}, paired_assay_rows=sum(r[2]=="PAIRED_ASSAY" for r in rows),
                 eligible=len(qual), gold_including_model_ready=counts[STAGES[3]]+counts[STAGES[4]],
                 gap_counts=dict(gaps), descriptor_cues=dict(queue), known_group_components=components,
                 split_gate="PASS_KNOWN_GROUPS" if components>=2 else "HOLD_INSUFFICIENT_INDEPENDENT_GROUPS",
                 decision_sha=sha, batches=batches_info, feedback_reports=len(feedback),
                 discovery_queries=searches if imbalanced else searches[:1], review_queue_by_gap=lanes, task_unit="nmol/viable cell; treated minus matched vehicle"),
            qual, stages)


def freeze(out, audit, samples, stages):
    out=Path(out)
    if out.exists():
        raise ValueError("freeze requires a new destination")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temp:
        stage=Path(temp)/"export"
        stage.mkdir()
        ready = samples if audit["split_gate"] == "PASS_KNOWN_GROUPS" else []
        fields = ("id","origin","structure_smiles","X","Y","missing_mask","split","groups","source_sha","locator","review_sha",
                  "treated_nmol","treated_viable","control_nmol","control_viable")
        with (stage/"model_ready.csv").open("w", newline="") as file:
            writer=csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for r in ready:
                writer.writerow(dict(r, X=packed(r["X"]), groups=packed(r["groups"]),
                                     missing_mask=packed({k:v is None for k,v in r["X"].items()})))
        (stage/"qualification.jsonl").write_text("".join(packed(r)+"\n" for r in stages))
        manifest=dict(audit, schema="asterloom.model-freeze/1", exported=len(ready),
                      files={f.name:digest(f.read_bytes()) for f in sorted(stage.iterdir())})
        (stage/"MANIFEST.json").write_text(packed(manifest)+"\n")
        os.rename(stage, out)
    return manifest

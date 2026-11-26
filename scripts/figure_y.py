#!/usr/bin/env python3
"""AST-V21-FIGURE-Y CLI: paper figure assets in, standardized ATP records out.

  build-manifest  spec + images -> frozen SHA-pinned manifest (needs OpenCV; box)
  redetect        re-run detectors on pinned images, diff against manifest (needs OpenCV)
  verify-assets   check every pinned asset SHA (stdlib)
  replay          manifest -> records JSONL + Work-table mapping + summary (stdlib, offline)
  sandbox-db      copy a Work DB read-only into a NEW sandbox file and insert mapped rows (stdlib)
  compare         per-paper increment vs existing Work extraction (stdlib, read-only)
  layer          admit a figure-derived numeric layer + receipts (stdlib, offline)
No network access and no model calls are made by any subcommand.
"""
import argparse
import json
import platform
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from r031_evidence import figure_y as fy  # noqa: E402
from r031_evidence import figure_y_layer as fyl  # noqa: E402


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _dump(obj, p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _freeze_panel(panel, out, root):
    yv = panel["y_axis"]["tick_values"]
    if len(out["y_ticks_px"]) != len(yv):
        raise SystemExit(f"TICK_COUNT_MISMATCH {panel['panel_key']}: {out['y_ticks_px']} vs {yv}")
    marks_in = panel["marks"]
    if len(out["marks"]) != len(marks_in):
        raise SystemExit(f"MARK_COUNT_MISMATCH {panel['panel_key']}: {len(out['marks'])} vs {len(marks_in)}")
    fp = dict(panel)
    fp["y_axis"] = {**panel["y_axis"], "ticks_px_value": [[round(p, 3), v] for p, v in zip(out["y_ticks_px"], yv)]}
    if panel.get("x_axis"):
        xv = panel["x_axis"]["tick_values"]
        if len(out.get("x_ticks_px", [])) != len(xv):
            raise SystemExit(f"X_TICK_COUNT_MISMATCH {panel['panel_key']}")
        fp["x_axis"] = {**panel["x_axis"], "ticks_px_value": [[round(p, 3), v] for p, v in zip(out["x_ticks_px"], xv)]}
    marks = []
    for spec_m, det in zip(marks_in, out["marks"]):
        m = dict(spec_m)
        m["y_px"] = round(float(det["y_px"]), 3)
        m["err_top_px"] = None if det.get("err_top_px") is None else round(float(det["err_top_px"]), 3)
        if "MARKER_ON_Y_AXIS" in (m.get("flags") or []):
            m["err_top_px"] = None
        if det.get("x_px") is not None:
            m["x_px"] = round(float(det["x_px"]), 3)
        m["detector_raw"] = {k: (round(float(v), 3) if isinstance(v, float) else v) for k, v in det.items()}
        marks.append(m)
    fp["marks"] = marks
    fp["detector"] = "r031_evidence.figure_y_detect." + panel["recipe"]["kind"]
    fp["detector_axis"] = {k: v for k, v in out.items() if k in ("axis", "baseline")}
    return fp


def cmd_build(a):
    from r031_evidence import figure_y_detect as det
    import cv2
    import numpy
    spec = _load(a.spec)
    root = Path(a.assets_root)
    man = {"schema": fy.MANIFEST_SCHEMA, "task": spec.get("task"), "spec_sha256": fy.sha256_file(a.spec),
           "detector_module_sha256": fy.sha256_file(Path(det.__file__)),
           "environment": {"python": platform.python_version(), "opencv": cv2.__version__, "numpy": numpy.__version__},
           "vision_reading_note": spec.get("vision_reading_note"), "papers": []}
    for paper in spec["papers"]:
        pp = dict(paper)
        for k, asset in paper["assets"].items():
            if fy.sha256_file(root / asset["path"]) != asset["sha256"]:
                raise SystemExit(f"ASSET_SHA_MISMATCH {paper['paper_id']} {k}")
        if fy.sha256_file(root / paper["jats"]["path"]) != paper["jats"]["sha256"]:
            raise SystemExit("JATS_SHA_MISMATCH " + paper["paper_id"])
        panels = []
        for panel in paper["panels"]:
            if not panel.get("recipe"):
                panels.append(panel)
                continue
            out = det.detect(root / paper["assets"][panel["asset"]]["path"], panel["recipe"])
            panels.append(_freeze_panel(panel, out, root))
        pp["panels"] = panels
        man["papers"].append(pp)
    _dump(man, a.out)
    print(json.dumps({"manifest": a.out, "sha256": fy.sha256_file(a.out)}))


def cmd_redetect(a):
    from r031_evidence import figure_y_detect as det
    man = _load(a.manifest)
    root = Path(a.assets_root)
    worst = 0.0
    rows = []
    for paper in man["papers"]:
        for panel in paper["panels"]:
            if not panel.get("recipe"):
                continue
            out = det.detect(root / paper["assets"][panel["asset"]]["path"], panel["recipe"])
            for m, d in zip(panel["marks"], out["marks"]):
                dv = abs(float(d["y_px"]) - m["y_px"])
                worst = max(worst, dv)
                rows.append(dv)
            for (p, _), q in zip(panel["y_axis"]["ticks_px_value"], out["y_ticks_px"]):
                worst = max(worst, abs(p - q))
    res = {"marks_checked": len(rows), "max_abs_pixel_diff": worst, "identical": worst < 1e-3}
    print(json.dumps(res))
    return 0 if res["identical"] else 3


def cmd_verify(a):
    probs = fy.verify_assets(_load(a.manifest), a.assets_root)
    print(json.dumps({"problems": probs, "ok": not probs}))
    return 0 if not probs else 4


def cmd_replay(a):
    man = _load(a.manifest)
    msha = fy.sha256_file(a.manifest)
    recs, inv = fy.replay(man)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = a.tag
    if not tag.startswith("v") or not tag[1:].isdigit():
        raise SystemExit("FIGURE_Y_BAD_TAG (expected v<N>)")
    fy.write_jsonl(recs, out / f"figure_y_records_{tag}.jsonl")
    rows = fy.work_rows(recs, msha, man)
    _dump(rows, out / f"figure_y_work_mapping_{tag}.json")
    summ = fy.summarize(recs, inv)
    summ["manifest_sha256"] = msha
    summ["records_sha256"] = fy.sha256_file(out / f"figure_y_records_{tag}.jsonl")
    summ["mapping_rows"] = {k: len(v) for k, v in rows.items()}
    summ["unrecoverable_notes"] = fy.paper_notes(man)
    _dump({"summary": summ, "panel_inventory": inv}, out / f"figure_y_summary_{tag}.json")
    print(json.dumps({"records": len(recs), "records_sha256": summ["records_sha256"], "manifest_sha256": msha}))


def cmd_sandbox(a):
    man = _load(a.manifest)
    msha = fy.sha256_file(a.manifest)
    recs, _ = fy.replay(man)
    res = fy.build_sandbox_db(a.work_db, a.out_db, fy.work_rows(recs, msha, man))
    print(json.dumps(res, indent=1))


def cmd_compare(a):
    recs = [json.loads(line) for line in Path(a.records).read_text(encoding="utf-8").splitlines() if line.strip()]
    con = sqlite3.connect(f"file:{a.work_db}?mode=ro&immutable=1", uri=True)
    papers = {r["paper_id"] for r in recs}
    if a.manifest:  # also report papers that yielded no ATP-content record
        papers |= {p["paper_id"] for p in _load(a.manifest)["papers"]}
    papers = sorted(papers)
    report = {}
    for pid in papers:
        ex = con.execute("""SELECT c.contrast_id, c.author_reported_percent, c.author_effect_literal, c.author_direction,
            e.endpoint_kind FROM contrasts c JOIN experiments e USING(experiment_id) WHERE e.paper_id=?""", (pid,)).fetchall()
        atp = [x for x in ex if x[4] == "ATP_CONTENT"]
        mine = [r for r in recs if r["paper_id"] == pid]
        referenced = {r["matched_contrast_id"] for r in mine if r.get("matched_contrast_id")}
        for r in mine:
            referenced.update(r.get("related_contrast_ids") or [])
        fig = [r for r in mine if r.get("figure_estimate")]
        new_q = [r for r in fig if r["source_type"] == "FIGURE_ESTIMATED" and r.get("entity_kind", "SMALL_MOLECULE") != "GENOTYPE_CONTEXT_NOT_MOLECULE"]
        existing_no_number = {x[0] for x in atp if x[1] is None and not x[2]}
        filled = sorted({r["matched_contrast_id"] for r in fig if r.get("matched_contrast_id") in existing_no_number})
        filled_rel = sorted({c for r in fig for c in (r.get("related_contrast_ids") or []) if c in existing_no_number} - set(filled))
        dir_new = [r for r in fig if r["figure_estimate"]["direction"] in ("INCREASE", "DECREASE") and not r.get("author_direction")]
        within = [r for r in fig if r["figure_estimate"]["direction"] == "WITHIN_DIGITIZATION_UNCERTAINTY"]
        corrob = []
        for r in mine:
            ar, fe = r.get("author_reported") or {}, r.get("figure_estimate") or {}
            if (ar.get("value") is not None or ar.get("bound") is not None) and fe:
                kind = ar.get("effect_kind")
                if kind == "PERCENT_CHANGE":
                    fig_eff, fig_unc = fe["percent_change"], fe["percent_change_uncertainty"]
                elif kind in fy.AUTHOR_ARM_KINDS:
                    fig_eff, fig_unc = fe["value"], fe["value_uncertainty"]
                elif kind == "PERCENT_OF_CONTROL":
                    fig_eff, fig_unc = fe["fold"] * 100, fe["fold_uncertainty"] * 100
                else:
                    fig_eff, fig_unc = fe["fold"], fe["fold_uncertainty"]
                item = {"relation": r["relation_key"], "author": ar["literal"], "author_value": ar.get("value"),
                        "figure_value": fig_eff, "figure_uncertainty": fig_unc}
                if kind not in (None, "PERCENT_CHANGE", "FOLD_CHANGE"):
                    item["effect_kind"] = kind
                    item["agree"] = (r.get("author_figure_check") or {}).get("agree")
                    if ar.get("bound") is not None:
                        item["author_bound"] = ar["bound"]
                corrob.append(item)
        man_notes = {}
        if a.manifest:
            man_notes = {p["paper_id"]: p.get("unrecoverable_notes") for p in _load(a.manifest)["papers"]}
        report[pid] = {
            "existing_contrasts": len(ex), "existing_atp_content": len(atp),
            "existing_atp_with_author_number": sum(1 for x in atp if x[1] is not None or x[2]),
            "existing_atp_direction_only": sum(1 for x in atp if x[1] is None and not x[2] and x[3]),
            "existing_atp_referenced_by_figure_y": len(referenced & {x[0] for x in atp}),
            "existing_atp_not_referenced": sorted({x[0] for x in atp} - referenced),
            "figure_y_records": len(mine),
            "new_traceable_quantitative_effects_FIGURE_ESTIMATED": len(new_q),
            "author_reported_records": sum(1 for r in mine if r["source_type"] == "AUTHOR_REPORTED"),
            "existing_numberless_contrasts_now_with_figure_value": filled,
            "existing_numberless_contrasts_with_figure_values_via_related_split_records": filled_rel,
            "direction_only_records": sum(1 for r in mine if r["source_type"] == "DIRECTION_ONLY"),
            "unknown_records": sum(1 for r in mine if r["source_type"] == "UNKNOWN"),
            "author_figure_discrepancies": [r["relation_key"] for r in mine if "AUTHOR_VS_FIGURE_DISCREPANCY" in r["flags"]],
            "new_figure_direction_signals_without_author_direction": len(dir_new),
            "figure_within_digitization_uncertainty": len(within),
            "author_vs_figure_corroboration": corrob,
        }
        if not mine and pid in man_notes:
            report[pid]["no_record_reason"] = man_notes[pid]
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: (vv if not isinstance(vv, list) else len(vv)) for kk, vv in v.items()} for k, v in report.items()}, indent=1))



def cmd_layer(a):
    """Admit a figure-derived numeric layer from pinned records + manifest."""
    records_path = Path(a.records)
    manifest_path = Path(a.manifest)
    records_bytes = records_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    records = [json.loads(line) for line in records_bytes.decode("utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    pins = fyl.write_layer_outputs(
        records,
        manifest,
        a.out_dir,
        tag=a.tag,
        records_sha256=fy.sha256_bytes(records_bytes),
        manifest_sha256=fy.sha256_bytes(manifest_bytes),
    )
    print(json.dumps({
        "layer_records": pins["layer_record_count"],
        "admission": pins["admission"],
        "ch1_usable_y": pins["ch1_usable_y"],
        "ch1_usable_papers": pins["ch1_usable_papers"],
        "ch1_usable_molecule_count": pins["ch1_usable_molecule_count"],
        "layer_records_sha256": pins["layer_records_sha256"],
        "source_records_sha256": pins["source_records_sha256"],
    }, indent=2, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build-manifest"); b.add_argument("--spec", required=True); b.add_argument("--assets-root", required=True); b.add_argument("--out", required=True); b.set_defaults(f=cmd_build)
    r = sp.add_parser("redetect"); r.add_argument("--manifest", required=True); r.add_argument("--assets-root", required=True); r.set_defaults(f=cmd_redetect)
    v = sp.add_parser("verify-assets"); v.add_argument("--manifest", required=True); v.add_argument("--assets-root", required=True); v.set_defaults(f=cmd_verify)
    p = sp.add_parser("replay"); p.add_argument("--manifest", required=True); p.add_argument("--out-dir", required=True); p.add_argument("--tag", default="v1", help="output file version tag, e.g. v2"); p.set_defaults(f=cmd_replay)
    s = sp.add_parser("sandbox-db"); s.add_argument("--manifest", required=True); s.add_argument("--work-db", required=True); s.add_argument("--out-db", required=True); s.set_defaults(f=cmd_sandbox)
    c = sp.add_parser("compare"); c.add_argument("--records", required=True); c.add_argument("--work-db", required=True); c.add_argument("--out", required=True); c.add_argument("--manifest", help="include zero-record papers of this manifest"); c.set_defaults(f=cmd_compare)
    ly = sp.add_parser("layer", help="admit figure-derived numeric layer (stdlib, offline)")
    ly.add_argument("--records", required=True)
    ly.add_argument("--manifest", required=True)
    ly.add_argument("--out-dir", required=True)
    ly.add_argument("--tag", default="v1")
    ly.set_defaults(f=cmd_layer)
    a = ap.parse_args(argv)
    return a.f(a) or 0


if __name__ == "__main__":
    raise SystemExit(main())

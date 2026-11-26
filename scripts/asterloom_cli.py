#!/usr/bin/env python3
"""AsterLoom (星织) Unified Platform CLI.

Provides a single unified entry point for:
  - `list-profiles`  : List registered R23 profiles, pipeline plugins, and 3-mode demand presets
  - `compile-demand` : Reverse-compile a scientific demand (`ml_dataset` | `lit_review` | `idea_discovery`) into a 3-stage funnel
  - `run-funnel`     : Execute the 3-Stage Funnel (Stage 1 Python Slicer -> Stage 2 API Screener -> Stage 3 Pixel-Ruler Harness)
  - `benchmark-gold` : Export or score the 12-item Multimodal Expert Gold Verification sheet
  - `studio`         : Launch the AsterLoom Studio 4-column Desktop GUI application or export a snapshot
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from r031_evidence import r23_profiles
from r031_evidence.demand_compiler import (
    DEMAND_MODES,
    ScientificDemandSpec,
    compile_demand_to_funnel,
    get_builtin_demand_presets,
)
from r031_evidence.platform_core import (
    build_multimodal_benchmark_sheet,
    execute_compiled_funnel,
    get_platform_audit_status,
    score_multimodal_benchmark,
    screen_molecule_dual_track,
)
from r031_evidence.task_plugin import list_plugins, register_demand_compiled_plugin


def _cmd_list_profiles(args: argparse.Namespace) -> int:
    presets = get_builtin_demand_presets()
    for p in presets:
        register_demand_compiled_plugin(p)
    payload = {
        "platform": "AsterLoom (星织) Universal Scientific Data Mining Platform",
        "supported_demand_modes": [
            {"mode": "ml_dataset", "label": "机器学习数据库逆向构建 (ML-Dataset)"},
            {"mode": "lit_review", "label": "综述写作与深度文献调研 (Lit-Review)"},
            {"mode": "idea_discovery", "label": "科研 Idea 与空白点探索 (Idea-Discovery)"},
        ],
        "r23_profile_registry": r23_profiles.list_profiles(),
        "pipeline_plugins": list_plugins(),
        "builtin_demand_presets": [
            {
                "profile_id": p["profile_id"],
                "mode": p["mode"],
                "mode_label": p["mode_label"],
                "title": p["demand_spec"]["title"],
                "domain": p["demand_spec"]["domain"],
            }
            for p in presets
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _resolve_plan(args: argparse.Namespace) -> dict:
    presets = {p["profile_id"]: p for p in get_builtin_demand_presets()}
    if getattr(args, "preset", "") and args.preset in presets:
        return presets[args.preset]
    if getattr(args, "demand_json", ""):
        raw = json.loads(Path(args.demand_json).read_text(encoding="utf-8"))
        if raw.get("schema") == "asterloom.reverse-demand-plan/1":
            return raw
        return compile_demand_to_funnel(raw)

    spec = ScientificDemandSpec(
        project_id=getattr(args, "project_id", "") or "custom_scientific_demand_v1",
        title=getattr(args, "title", "") or "自定义科研数据挖掘任务",
        mode=getattr(args, "mode", "ml_dataset"),
        domain=getattr(args, "domain", "") or "跨学科科研挖掘",
        objective_summary=getattr(args, "objective", "") or "由科研目标产出逆向驱动三轮检验漏斗挖掘。",
        primary_entities=[x.strip() for x in (getattr(args, "entities", "") or "Bioactive molecule").split(",") if x.strip()],
        target_endpoints_or_metrics=[x.strip() for x in (getattr(args, "metrics", "") or "TARGET_METRIC").split(",") if x.strip()],
        forbidden_traps=[x.strip() for x in (getattr(args, "traps", "") or "Proxy endpoint trap").split(",") if x.strip()],
        required_conditions=[x.strip() for x in (getattr(args, "conditions", "") or "Matched control").split(",") if x.strip()],
    )
    return compile_demand_to_funnel(spec)


def _cmd_compile_demand(args: argparse.Namespace) -> int:
    plan = _resolve_plan(args)
    register_demand_compiled_plugin(plan)
    text = json.dumps(plan, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        if out_path.suffix.lower() == ".json":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text + "\n", encoding="utf-8")
        else:
            out_path.mkdir(parents=True, exist_ok=True)
            (out_path / "compiled_demand_plan.json").write_text(text + "\n", encoding="utf-8")
            (out_path / f"{plan['profile_id']}.json").write_text(
                json.dumps(plan["r23_profile_manifest"], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    print(text)
    return 0


def _cmd_run_funnel(args: argparse.Namespace) -> int:
    plan = _resolve_plan(args)
    register_demand_compiled_plugin(plan)
    out_dir = Path(args.out) if args.out else None
    report = execute_compiled_funnel(plan, out_dir=out_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _cmd_benchmark_gold(args: argparse.Namespace) -> int:
    if args.score_sheet:
        sheet = json.loads(Path(args.score_sheet).read_text(encoding="utf-8"))
    else:
        sheet = build_multimodal_benchmark_sheet()

    if args.export_sheet:
        out_p = Path(args.export_sheet)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(sheet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    scored = score_multimodal_benchmark(
        sheet,
        expert_signed=bool(args.sign_reviewer),
        reviewer_name=args.sign_reviewer or "",
    )
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(scored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(scored, indent=2, ensure_ascii=False))
    return 0


def _cmd_studio(args: argparse.Namespace) -> int:
    from r031_evidence.studio_app import export_studio_bundle, run_studio_server

    if args.export_dir:
        info = export_studio_bundle(Path(args.export_dir))
        print(json.dumps(info, indent=2, ensure_ascii=False))
        if not args.serve:
            return 0

    run_studio_server(
        host=args.host,
        port=args.port,
        open_desktop_window=args.desktop_window,
        open_browser=args.open_browser,
    )
    return 0


def _cmd_audit_status(args: argparse.Namespace) -> int:
    agents_root = Path(args.agents_root) if getattr(args, "agents_root", "") else None
    payload = get_platform_audit_status(agents_root=agents_root)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    agents_root = Path(args.agents_root) if getattr(args, "agents_root", "") else None
    payload = screen_molecule_dual_track(
        args.smiles,
        name=args.name,
        dose_um=args.dose_um,
        exposure_h=args.exposure_h,
        cell_context=args.cell_context,
        paradigm_regime=args.paradigm,
        temp_c=args.temp_c,
        time_h=args.time_h,
        catalysts_or_initiators=args.catalysts,
        solvents=args.solvents,
        co_monomers=args.co_monomers,
        model_a_bundle_path=Path(args.model_a_bundle) if args.model_a_bundle else None,
        model_b_bundle_path=Path(args.model_b_bundle) if args.model_b_bundle else None,
        agents_root=agents_root,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="asterloom",
        description="AsterLoom (星织) Universal Multimodal Scientific Data Mining Platform CLI",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-profiles", help="List all registered R23 profiles, plugins, and 3-mode demand presets")
    p_list.set_defaults(func=_cmd_list_profiles)

    p_comp = sub.add_parser("compile-demand", help="Reverse-compile a scientific demand into a 3-stage funnel contract")
    p_comp.add_argument("--preset", default="", help="Built-in preset ID (e.g. ch1_small_molecule_atp_v1)")
    p_comp.add_argument("--demand-json", default="", help="Path to a demand spec JSON file")
    p_comp.add_argument("--project-id", default="custom_demand_v1")
    p_comp.add_argument("--mode", choices=DEMAND_MODES, default="ml_dataset")
    p_comp.add_argument("--title", default="")
    p_comp.add_argument("--domain", default="")
    p_comp.add_argument("--objective", default="")
    p_comp.add_argument("--entities", default="", help="Comma-separated primary entities")
    p_comp.add_argument("--metrics", default="", help="Comma-separated target endpoints or metrics")
    p_comp.add_argument("--traps", default="", help="Comma-separated forbidden scientific traps")
    p_comp.add_argument("--conditions", default="", help="Comma-separated required experimental conditions")
    p_comp.add_argument("--out", default="", help="Output directory or JSON file path")
    p_comp.set_defaults(func=_cmd_compile_demand)

    p_run = sub.add_parser("run-funnel", help="Execute the 3-Stage Funnel for a preset or compiled demand plan")
    p_run.add_argument("--preset", default="ch1_small_molecule_atp_v1", help="Preset ID to run")
    p_run.add_argument("--demand-json", default="", help="Path to demand JSON or compiled plan JSON")
    p_run.add_argument("--out", default="", help="Directory to write execution report")
    p_run.set_defaults(func=_cmd_run_funnel)

    p_bench = sub.add_parser("benchmark-gold", help="Export or score the 12-item Multimodal Expert Gold Verification sheet")
    p_bench.add_argument("--export-sheet", default="", help="Write the 12-item verification sheet JSON")
    p_bench.add_argument("--score-sheet", default="", help="Score an existing verification sheet JSON")
    p_bench.add_argument("--sign-reviewer", default="", help="Reviewer signature to unlock independent_gold count")
    p_bench.add_argument("--out", default="", help="Output path for scored benchmark report")
    p_bench.set_defaults(func=_cmd_benchmark_gold)

    p_studio = sub.add_parser("studio", help="Launch AsterLoom Studio 4-Column Desktop GUI or export interactive bundle")
    p_studio.add_argument("--export-dir", default="", help="Export self-contained Studio application bundle & ruler assets")
    p_studio.add_argument("--serve", action="store_true", help="Start live local Studio backend daemon")
    p_studio.add_argument("--host", default="127.0.0.1")
    p_studio.add_argument("--port", type=int, default=8765)
    p_studio.add_argument("--desktop-window", action="store_true", help="Open native macOS desktop application window")
    p_studio.add_argument("--open-browser", action="store_true", help="Open Studio GUI in default browser")
    p_studio.set_defaults(func=_cmd_studio)

    p_audit = sub.add_parser("audit-status", help="Inspect Ch1/Ch2 real production telemetry, model evals, and governance status")
    p_audit.add_argument("--agents-root", default="", help="Optional path to AsterLoom-Agents workspace")
    p_audit.set_defaults(func=_cmd_audit_status)

    p_screen = sub.add_parser("screen", help="Run dual-track virtual screening (Ch1 Model A v2.0 + Ch2 Model B v1.0)")
    p_screen.add_argument("--smiles", required=True, help="Candidate molecule SMILES")
    p_screen.add_argument("--name", default="query_molecule", help="Optional molecule label")
    p_screen.add_argument("--dose-um", type=float, default=10.0)
    p_screen.add_argument("--exposure-h", type=float, default=24.0)
    p_screen.add_argument(
        "--cell-context",
        default="NON_TUMOR_PRIMARY_OR_NORMAL",
        choices=("NON_TUMOR_PRIMARY_OR_NORMAL", "TUMOR_CANCER_LINE", "UNKNOWN"),
    )
    p_screen.add_argument("--paradigm", default="BASAL", choices=("BASAL", "STRESS_RESCUE"))
    p_screen.add_argument("--temp-c", type=float, default=80.0)
    p_screen.add_argument("--time-h", type=float, default=12.0)
    p_screen.add_argument("--catalysts", nargs="*", default=None)
    p_screen.add_argument("--solvents", nargs="*", default=None)
    p_screen.add_argument("--co-monomers", nargs="*", default=None)
    p_screen.add_argument("--model-a-bundle", default="", help="Optional path to model_a_v2_bundle.joblib")
    p_screen.add_argument("--model-b-bundle", default="", help="Optional path to model_b_v1_bundle.joblib")
    p_screen.add_argument("--agents-root", default="", help="Optional path to AsterLoom-Agents workspace")
    p_screen.set_defaults(func=_cmd_screen)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

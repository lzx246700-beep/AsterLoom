"""Unit tests for AsterLoom (星织) Platform Generalization, 3-Mode Demand Reverse Compiler,
Universal 3-Stage Funnel Core, Multimodal Gold Benchmark, and AsterLoom Studio GUI.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from r031_evidence.demand_compiler import (
    ScientificDemandSpec,
    compile_demand_to_funnel,
    get_builtin_demand_presets,
)
from r031_evidence.platform_core import (
    build_multimodal_benchmark_sheet,
    execute_compiled_funnel,
    get_platform_audit_status,
    recompute_bar_contrast,
    score_multimodal_benchmark,
    screen_molecule_dual_track,
    slice_text_with_keywords,
)
from r031_evidence.studio_app import export_studio_bundle, render_studio_html, build_initial_studio_state
from r031_evidence.task_plugin import get_plugin, list_plugins, register_demand_compiled_plugin


class TestPlatformGeneralization2026(unittest.TestCase):
    def test_builtin_demand_presets_cover_all_three_modes(self) -> None:
        presets = get_builtin_demand_presets()
        self.assertGreaterEqual(len(presets), 4)
        modes = {p["mode"] for p in presets}
        self.assertEqual(modes, {"ml_dataset", "lit_review", "idea_discovery"})
        for p in presets:
            self.assertEqual(p["safety_invariants"]["independent_gold"], 0)
            self.assertEqual(p["safety_invariants"]["new_paid_model_calls"], 0)
            self.assertFalse(p["safety_invariants"]["retired_holdout_20261110_referenced"])
            self.assertIn("stage1", p["funnel_stages"])
            self.assertIn("stage2", p["funnel_stages"])
            self.assertIn("stage3", p["funnel_stages"])

    def test_reverse_compile_custom_idea_discovery_and_plugin_registration(self) -> None:
        spec = ScientificDemandSpec(
            project_id="custom_cof_h2_idea_v1",
            title="光催化产氢 COFs 能带与共价连接键空白探索",
            mode="idea_discovery",
            domain="能源催化 / 光催化制氢",
            objective_summary="挖掘高产氢速率 COFs 中尚未被探索的连接键与助催化剂耦合空白点。",
            primary_entities=["Covalent organic framework", "TpPa-1", "Beta-ketoenamine"],
            target_endpoints_or_metrics=["HER_RATE_UMOL_G_H", "AQY_PERCENT"],
            forbidden_traps=["光电催化外加偏压混淆", "无牺牲剂对照缺失"],
            lab_direction_baseline="课题组专注亚胺/烯胺类 COFs 光催化产氢",
            exploration_axes_methods=["供体-受体 D-A 共价剪裁", "非贵金属单原子配位锚定"],
            bottleneck_keywords=["exciton recombination", "limited visible light absorption"],
        )
        plan = compile_demand_to_funnel(spec)
        self.assertEqual(plan["mode"], "idea_discovery")
        self.assertEqual(plan["profile_id"], "custom_cof_h2_idea_v1")
        self.assertFalse(plan["r23_profile_manifest"]["qualification"]["automatic_Gold"])

        plugin = register_demand_compiled_plugin(plan)
        self.assertEqual(plugin.profile_id, "custom_cof_h2_idea_v1")
        queries = get_plugin("custom_cof_h2_idea_v1").build_queries()
        self.assertTrue(any("Covalent organic framework" in q for q in queries))
        all_ids = {p["profile_id"] for p in list_plugins()}
        self.assertIn("small_molecule_atp_content_v1", all_ids)
        self.assertIn("bioactive_polymer_v1", all_ids)
        self.assertIn("custom_cof_h2_idea_v1", all_ids)

    def test_stage1_slicer_and_stage3_ols_ruler_recompute(self) -> None:
        sample_text = (
            "In Results 3.2, Berberine (10 uM) significantly restored intracellular ATP concentration "
            "to 11.75 nmol/mg protein in H9c2 cardiomyocytes compared with hypoxia control."
        )
        sliced = slice_text_with_keywords(
            text=sample_text,
            entity_keywords=["Berberine", "Resveratrol"],
            metric_keywords=["intracellular ATP", "nmol/mg protein"],
            trap_keywords=["CellTiter-Glo viability"],
        )
        self.assertEqual(sliced["stage1_verdict"], "STAGE1_PASS_TO_API_SCREEN")
        self.assertEqual(sliced["cost_usd"], 0.0)
        self.assertIn("Berberine", sliced["matched_entities"])

        recomp = recompute_bar_contrast(
            ticks_px=[60.0, 140.0, 220.0, 300.0],
            tick_values=[15.0, 10.0, 5.0, 0.0],
            treated_y_px=112.0,
            control_y_px=201.0,
            unit="nmol/mg protein",
        )
        self.assertAlmostEqual(recomp["recomputed_treated_value"], 11.75, places=2)
        self.assertAlmostEqual(recomp["recomputed_control_value"], 6.1875, places=3)
        self.assertGreater(recomp["recomputed_log2fc"], 0.9)

    def test_execute_funnel_across_all_modes(self) -> None:
        for plan in get_builtin_demand_presets():
            report = execute_compiled_funnel(plan)
            self.assertEqual(report["safety_invariants"]["independent_gold"], 0)
            self.assertFalse(report["safety_invariants"]["retired_holdout_20261110_referenced"])
            self.assertGreater(report["funnel_metrics"]["cost_saving_pct"], 75.0)
            self.assertTrue(len(report["records"]) >= 3)

    def test_multimodal_gold_benchmark_integrity_gates(self) -> None:
        sheet = build_multimodal_benchmark_sheet()
        self.assertEqual(sheet["independent_gold"], 0)
        self.assertFalse(sheet["retired_holdout_20261110_referenced"])

        # Unsigned preview must keep independent_gold == 0
        unsigned = score_multimodal_benchmark(sheet, expert_signed=False, reviewer_name="")
        self.assertEqual(unsigned["independent_gold"], 0)
        self.assertEqual(unsigned["metrics"]["admission_f1_pct"], 100.0)
        self.assertEqual(unsigned["metrics"]["scientific_trap_rejection_rate_pct"], 100.0)
        self.assertGreater(unsigned["metrics"]["ruler_error_reduction_pct"], 95.0)

        # Signed by human reviewer unlocks independent_gold count
        signed = score_multimodal_benchmark(sheet, expert_signed=True, reviewer_name="PI_Expert_2026")
        self.assertEqual(signed["independent_gold"], 12)
        self.assertTrue(signed["expert_signed"])

        # Referencing retired 2026-11-10 holdout must raise ValueError
        bad_sheet = dict(sheet, retired_holdout_20261110_referenced=True)
        with self.assertRaises(ValueError):
            score_multimodal_benchmark(bad_sheet)

    def test_export_studio_bundle_creates_4column_gui_and_ruler_pngs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "studio_export"
            summary = export_studio_bundle(out_dir)
            self.assertEqual(summary["independent_gold"], 0)
            self.assertFalse(summary["retired_holdout_20261110_referenced"])
            html_file = Path(summary["studio_html_path"])
            self.assertTrue(html_file.is_file())
            html_text = html_file.read_text(encoding="utf-8")
            self.assertIn("AsterLoom Studio (星织)", html_text)
            self.assertIn("ML-Dataset", html_text)
            self.assertIn("Lit-Review", html_text)
            self.assertIn("Idea-Discovery", html_text)
            ruler_pngs = list((out_dir / "ruler_assets").glob("*_ruler.png"))
            self.assertGreaterEqual(len(ruler_pngs), 5)

    def test_custom_demand_runs_real_stage1_slicer_and_offline_fallback(self) -> None:
        spec = ScientificDemandSpec(
            project_id="custom_photocat_cof_v1",
            title="光催化 COFs 性能切片验证",
            mode="ml_dataset",
            domain="能源催化",
            objective_summary="验证自定义需求真实执行 slice_text_with_keywords。",
            primary_entities=["TpPa-1", "COF"],
            target_endpoints_or_metrics=["HER_RATE", "AQY"],
            forbidden_traps=["外加偏压混淆"],
            required_conditions=["可见光照射"],
        )
        plan = compile_demand_to_funnel(spec)
        report = execute_compiled_funnel(plan)
        self.assertEqual(report["funnel_metrics"]["telemetry_source"], "stage1_live_keyword_slicer")
        self.assertEqual(len(report["records"]), 3)
        self.assertEqual(report["records"][0]["admission"], "ADMIT")
        self.assertEqual(report["records"][0]["stage1_result"]["stage1_verdict"], "STAGE1_PASS_TO_API_SCREEN")
        self.assertTrue(len(report["records"][0]["stage1_result"]["keyword_slices"]) >= 1)
        self.assertEqual(report["records"][2]["admission"], "HOLD")
        self.assertIn("外加偏压混淆", report["records"][2]["stage1_result"]["detected_trap_signals"])

        # Offline fallback when AsterLoom-Agents directory does not exist
        with tempfile.TemporaryDirectory() as missing_dir:
            status = get_platform_audit_status(agents_root=Path(missing_dir) / "nonexistent_agents")
            self.assertEqual(status["independent_gold"], 0)
            self.assertEqual(status["training_approved"], 0)
            self.assertTrue(status["ch1"]["fallback"])
            self.assertTrue(status["ch2"]["fallback"])

            screened = screen_molecule_dual_track(
                "COc1cc(/C=C/C(=O)O)ccc1O",
                name="Ferulic Acid",
                agents_root=Path(missing_dir) / "nonexistent_agents",
            )
            self.assertEqual(screened["independent_gold"], 0)
            self.assertEqual(screened["training_approved"], 0)
            self.assertFalse(screened["ch1_atp_model_a_v2"]["available"])
            self.assertFalse(screened["ch2_polymer_model_b_v1"]["available"])

    def test_cli_audit_status_screen_and_studio_html_controls(self) -> None:
        import io
        from contextlib import redirect_stdout
        import scripts.asterloom_cli as cli

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["audit-status", "--agents-root", "/nonexistent_agents_dir"])
        self.assertEqual(rc, 0)
        audit_out = json.loads(buf.getvalue())
        self.assertEqual(audit_out["independent_gold"], 0)
        self.assertEqual(audit_out["training_approved"], 0)

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cli.main(["screen", "--smiles", "OCc1ccc(CO)o1", "--name", "BHMF", "--agents-root", "/nonexistent_agents_dir"])
        self.assertEqual(rc2, 0)
        scr_out = json.loads(buf2.getvalue())
        self.assertEqual(scr_out["independent_gold"], 0)
        self.assertIn("ch1_atp_model_a_v2", scr_out)
        self.assertIn("ch2_polymer_model_b_v1", scr_out)

        html = render_studio_html(build_initial_studio_state())
        self.assertIn("/api/screen-molecule", html)
        self.assertIn("onNativeActionError", html)
        self.assertIn("load_live_state", html)

    @unittest.skip("requires scripts/ast_promote_signed_gold.py, not in the public export")
    def test_gold_promotion_gate_refusal_and_valid_signoff(self) -> None:
        import csv
        import scripts.ast_promote_signed_gold as gate

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            xml_dir = root / "xmls" / "PMC_GOLD_1"
            xml_dir.mkdir(parents=True)
            xml_p = xml_dir / "author.xml"
            xml_p.write_text("<article><body>test</body></article>", encoding="utf-8")
            import hashlib
            sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()

            blank_csv = root / "human_gold_review_blank.csv"
            cols = ["paper_id", "source_sha256", "human_gold_verdict", "reviewer_name", "reviewed_at_utc", "review_status", "independent_gold", "training_approved"]
            with blank_csv.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerow({
                    "paper_id": "PMC_GOLD_1",
                    "source_sha256": sha,
                    "human_gold_verdict": "",
                    "reviewer_name": "",
                    "reviewed_at_utc": "",
                    "review_status": "PENDING_HUMAN_SIGNOFF",
                    "independent_gold": 0,
                    "training_approved": 0,
                })

            ok_bad, rep_bad = gate.promote_signed_gold(blank_csv, root / "out_bad", xml_root=root / "xmls")
            self.assertFalse(ok_bad)
            self.assertEqual(rep_bad["independent_gold"], 0)
            self.assertEqual(rep_bad["training_approved"], 0)
            self.assertEqual(rep_bad["promotion_status"], "REJECTED_INCOMPLETE_OR_INVALID_SIGNOFF")

            signed_input = root / "human_gold_reviewed.csv"
            with signed_input.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerow({
                    "paper_id": "PMC_GOLD_1",
                    "source_sha256": sha,
                    "human_gold_verdict": "AGREE_PASS",
                    "reviewer_name": "PI_Reviewer_2026",
                    "reviewed_at_utc": "2026-11-25T04:00:00Z",
                    "review_status": "PENDING_HUMAN_SIGNOFF",
                    "independent_gold": 0,
                    "training_approved": 0,
                })

            ok_good, rep_good = gate.promote_signed_gold(signed_input, root / "out_good", xml_root=root / "xmls")
            self.assertTrue(ok_good)
            self.assertEqual(rep_good["independent_gold"], 1)
            self.assertEqual(rep_good["training_approved"], 1)
            self.assertEqual(rep_good["promotion_status"], "PROMOTED_SIGNED_GOLD")


if __name__ == "__main__":
    unittest.main()

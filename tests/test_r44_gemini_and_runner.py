"""Offline tests for Google AI Studio (Gemini) adapter and Task Bundle runner."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r031_evidence import r44_gemini as gemini
from r031_evidence import r44_models as models
from r031_evidence.task_bundle import build_multimodal_task_bundle, encode_rgb_png
from r031_evidence.task_bundle_runner import run_task_bundle_gemini


def _tiny_png() -> bytes:
    rows = [bytearray([245, 245, 245] * 60) for _ in range(100)]
    return encode_rgb_png(60, 100, rows)


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestR44GeminiAndRunner(unittest.TestCase):
    def test_defaults_and_safety_gates(self):
        self.assertEqual(gemini.DEFAULT_MODEL, "gemini-3.8-flash")
        spec = models.Provider("gemini", "gemini-3.8-flash")
        self.assertEqual(spec.name, "gemini")
        self.assertEqual(spec.model, "gemini-3.8-flash")

        with self.assertRaisesRegex(RuntimeError, "GEMINI_NETWORK_DEFAULT_OFF"):
            gemini.call_gemini("hello", source_license_literal="CC BY 4.0")

        with self.assertRaisesRegex(RuntimeError, "GEMINI_EXPLICIT_APPROVAL_REQUIRED"):
            gemini.call_gemini("hello", source_license_literal="CC BY 4.0", allow_network=True)

        with self.assertRaisesRegex(RuntimeError, "GEMINI_REMOTE_AUTHOR_LICENSE_UNVERIFIED_OR_RESTRICTED_HOLD"):
            gemini.call_gemini(
                "hello",
                source_license_literal="CC BY-NC 4.0",
                allow_network=True,
                allow_remote_api=True,
                allow_source_disclosure=True,
                api_key="test-key",
            )

        self.assertTrue(gemini.is_open_license_allowed("under the terms of http://creativecommons.org/licenses/by/4.0/ License"))
        self.assertFalse(gemini.is_open_license_allowed("under the terms of http://creativecommons.org/licenses/by-nc/4.0/ License"))
        self.assertFalse(gemini.is_open_license_allowed("under the terms of http://creativecommons.org/licenses/by-nc-nd/4.0/ License"))

    def test_multimodal_payload_header_auth_and_journal_no_key_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / "panel_ruler.png"
            img.write_bytes(_tiny_png())
            journal = root / "resp.http.json"

            fake_body = json.dumps(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": '{"paper_id":"PMC1","calibrations":{},"contrasts":[]}'}
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 120,
                        "candidatesTokenCount": 35,
                        "totalTokenCount": 155,
                    },
                }
            ).encode("utf-8")

            captured_req = {}

            def _fake_open(self_opener, req, timeout=None):
                captured_req["url"] = req.full_url
                captured_req["headers"] = dict(req.header_items())
                captured_req["body"] = json.loads(req.data.decode("utf-8"))
                return _FakeHTTPResponse(fake_body)

            with patch("urllib.request.OpenerDirector.open", new=_fake_open):
                res = gemini.call_gemini(
                    "Extract ATP contrast",
                    model="gemini-3.8-flash",
                    system_prompt="Return JSON only.",
                    image_paths=[img],
                    source_license_literal="Creative Commons Attribution 4.0 International License (CC BY 4.0)",
                    allow_network=True,
                    allow_remote_api=True,
                    allow_source_disclosure=True,
                    response_journal=journal,
                    api_key="SECRET_DUMMY_KEY_DO_NOT_LEAK",
                )

            self.assertEqual(res["model"], "gemini-3.8-flash")
            self.assertEqual(res["provider"], "gemini")
            self.assertEqual(res["answer"]["paper_id"], "PMC1")
            self.assertEqual(res["usage"]["totalTokenCount"], 155)
            # Key must be in header only, NEVER in URL or journal file
            self.assertNotIn("SECRET_DUMMY_KEY_DO_NOT_LEAK", captured_req["url"])
            self.assertIn("models/gemini-3.8-flash:generateContent", captured_req["url"])
            header_map = {k.lower(): v for k, v in captured_req["headers"].items()}
            self.assertEqual(header_map["x-goog-api-key"], "SECRET_DUMMY_KEY_DO_NOT_LEAK")
            parts = captured_req["body"]["contents"][0]["parts"]
            self.assertEqual(len(parts), 2)
            self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
            self.assertEqual(captured_req["body"]["generationConfig"]["responseMimeType"], "application/json")
            self.assertEqual(captured_req["body"]["generationConfig"]["maxOutputTokens"], 32768)
            self.assertNotIn("thinkingConfig", captured_req["body"]["generationConfig"])
            self.assertTrue(journal.is_file())
            self.assertNotIn("SECRET_DUMMY_KEY_DO_NOT_LEAK", journal.read_text(encoding="utf-8"))

    def test_task_bundle_runner_dry_run_live_repin_and_resume_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_dir = root / "xmls" / "PMC9990002"
            xml_dir.mkdir(parents=True)
            xml_bytes = (
                b'<article><front><article-meta><title-group>'
                b'<article-title>Curcumin rescues ATP content in HepG2 cells</article-title>'
                b'</title-group>'
                b'<permissions><license><license-p>This is an open-access article under CC BY 4.0.</license-p></license></permissions>'
                b'</article-meta></front><body>'
                b'<sec sec-type="methods"><p>ATP content measured by CellTiter-Glo.</p></sec>'
                b'<sec><p>See <xref ref-type="fig" rid="fig1">Figure 1</xref>.</p>'
                b'<fig id="fig1"><label>Figure 1</label><caption><p>Cellular ATP content with 10 uM curcumin.</p></caption>'
                b'<graphic xlink:href="fig1.png" xmlns:xlink="http://www.w3.org/1999/xlink"/></fig></sec>'
                b'</body></article>'
            )
            xml_path = xml_dir / "author.xml"
            xml_path.write_bytes(xml_bytes)
            xml_sha = hashlib.sha256(xml_bytes).hexdigest()

            img_path = root / "assets" / "PMC9990002" / "fig1.png"
            img_path.parent.mkdir(parents=True)
            img_bytes = _tiny_png()
            img_path.write_bytes(img_bytes)
            img_sha = hashlib.sha256(img_bytes).hexdigest()

            queue_path = root / "queue.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "paper_id": "PMC9990002",
                                "queue_state": "READY_FOR_MULTIMODAL_DIGITIZATION",
                                "priority_score": 4,
                                "xml_sha256": xml_sha,
                                "natural_molecules": [{"preferred_name": "Curcumin", "inchikey": "VFLDPWHFBUODDF-FCXRPNKRSA-N"}],
                                "atp_figure_labels": ["Figure 1"],
                                "resolved_assets": [
                                    {"href": "fig1.png", "path": str(img_path), "sha256": img_sha, "width": 60, "height": 100}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            bundle_dir = root / "bundle"
            build_multimodal_task_bundle(
                profile_id="small_molecule_atp_content_v1",
                queue_json=queue_path,
                xml_roots=[root / "xmls"],
                repo_root=root,
                out_dir=bundle_dir,
            )

            out_dir = root / "run_out"
            # 1. Dry-run by default (0 live calls)
            dry = run_task_bundle_gemini(bundle_dir=bundle_dir, out_dir=out_dir)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["live_calls_executed"], 0)
            self.assertEqual(dry["papers"][0]["status"], "READY_FOR_GEMINI_CALL")

            # 2. Mocked live call returning spoofed SHAs/paths -> runner must re-pin trusted manifest SHAs/paths
            call_counter = {"n": 0}

            def _mock_call(*args, **kwargs):
                call_counter["n"] += 1
                return {
                    "answer": {
                        "paper_id": "SPOOFED_PMC",
                        "calibrations": {
                            "PMC9990002_Fig1": {
                                "image_path": "/etc/passwd",
                                "image_sha256": "0" * 64,
                                "unit": "% of control",
                                "ticks_px": [10.0, 90.0],
                                "tick_values": [100.0, 0.0],
                                "pixel_tolerance_px": 1.5,
                            }
                        },
                        "contrasts": [
                            {
                                "contrast_key": "PMC9990002:Fig1:Cur_vs_Ctrl",
                                "paper_id": "SPOOFED_PMC",
                                "author_xml_sha256": "0" * 64,
                                "calibration_id": "PMC9990002_Fig1",
                                "figure_panel": "Figure 1",
                                "molecule": "Curcumin",
                                "tested_form": "Curcumin",
                                "cell_model": "HepG2",
                                "dose": "10 uM",
                                "exposure_time": "24 h",
                                "stimulation": "NONE",
                                "stratum": "HepG2_Cur10",
                                "treated_arm": "Curcumin 10 uM",
                                "control_arm": "Control",
                                "treated_y_px": 30.0,
                                "control_y_px": 50.0,
                                "endpoint": "ATP_CONTENT",
                                "assay_method": "CellTiter-Glo",
                                "normalization": "% of control",
                                "admission": "ADMIT_FIGURE_NUMERIC",
                                "author_significance": "* p < 0.05",
                                "unresolvable_reasons": [],
                            }
                        ],
                    },
                    "usage": {"totalTokenCount": 200},
                    "request_sha256": "a" * 64,
                    "response_sha256": "b" * 64,
                }

            live = run_task_bundle_gemini(
                bundle_dir=bundle_dir,
                out_dir=out_dir,
                allow_network=True,
                allow_remote_api=True,
                delay_seconds=0.0,
                call_fn=_mock_call,
            )
            self.assertFalse(live["dry_run"])
            self.assertEqual(live["live_calls_executed"], 1)
            self.assertEqual(call_counter["n"], 1)

            fallback_out = root / "fallback_out"
            fallback_models_seen = []

            def _mock_fallback(*args, **kwargs):
                fallback_models_seen.append(kwargs["model"])
                if kwargs["model"] == "gemini-primary":
                    raise RuntimeError("GEMINI_HTTP_ERROR_503_HOLD")
                return _mock_call(*args, **kwargs)

            fallback = run_task_bundle_gemini(
                bundle_dir=bundle_dir,
                out_dir=fallback_out,
                model="gemini-primary",
                fallback_models=["gemini-fallback"],
                allow_network=True,
                allow_remote_api=True,
                delay_seconds=0.0,
                call_fn=_mock_fallback,
            )
            self.assertEqual(fallback_models_seen, ["gemini-primary", "gemini-fallback"])
            self.assertEqual(fallback["live_calls_executed"], 2)
            self.assertEqual(fallback["models_used"], ["gemini-fallback"])
            self.assertEqual(fallback["papers"][0]["model_used"], "gemini-fallback")
            self.assertEqual(
                [x["status"] for x in fallback["papers"][0]["attempted_models"]],
                ["ERROR_HOLD", "COMPLETED"],
            )

            # Non-retryable failures (for example, model 404) must not fall back.
            def _mock_nonretryable(*args, **kwargs):
                fallback_models_seen.append(kwargs["model"])
                raise RuntimeError("GEMINI_MODEL_NOT_FOUND_404:missing")

            before = len(fallback_models_seen)
            held = run_task_bundle_gemini(
                bundle_dir=bundle_dir,
                out_dir=root / "nonretryable_out",
                model="gemini-missing",
                fallback_models=["gemini-fallback"],
                allow_network=True,
                allow_remote_api=True,
                delay_seconds=0.0,
                call_fn=_mock_nonretryable,
            )
            self.assertEqual(fallback_models_seen[before:], ["gemini-missing"])
            self.assertEqual(held["errors"], 1)
            self.assertEqual(held["live_calls_executed"], 1)
            self.assertIsNone(held["filled_results_path"])
            self.assertFalse((root / "nonretryable_out" / "filled_results.json").exists())

            # 429 rate-limit hold must also fall back to configured fallback_models.
            seen_429 = []

            def _mock_429_fallback(*args, **kwargs):
                seen_429.append(kwargs["model"])
                if kwargs["model"] == "gemini-429":
                    raise RuntimeError("GEMINI_RATE_LIMIT_429_HOLD|RESOURCE_EXHAUSTED:quota")
                return _mock_call(*args, **kwargs)

            fb429 = run_task_bundle_gemini(
                bundle_dir=bundle_dir,
                out_dir=root / "fallback_429_out",
                model="gemini-429",
                fallback_models=["gemini-fallback"],
                allow_network=True,
                allow_remote_api=True,
                delay_seconds=0.0,
                call_fn=_mock_429_fallback,
            )
            self.assertEqual(seen_429, ["gemini-429", "gemini-fallback"])
            self.assertEqual(fb429["models_used"], ["gemini-fallback"])
            self.assertIsNotNone(fb429["filled_results_path"])

            filled = json.loads((out_dir / "filled_results.json").read_text(encoding="utf-8"))
            self.assertEqual(filled["independent_gold"], 0)
            self.assertEqual(filled["training_approved"], 0)
            self.assertEqual(filled["calibrations"]["PMC9990002_Fig1"]["image_sha256"], img_sha)
            self.assertEqual(filled["calibrations"]["PMC9990002_Fig1"]["image_path"], "assets/PMC9990002/fig1.png")
            self.assertEqual(filled["contrasts"][0]["paper_id"], "PMC9990002")
            self.assertEqual(filled["contrasts"][0]["author_xml_sha256"], xml_sha)

            # 3. Second run reuses local cache -> 0 new API calls
            calls_before_resume = call_counter["n"]
            resumed = run_task_bundle_gemini(
                bundle_dir=bundle_dir,
                out_dir=out_dir,
                allow_network=True,
                allow_remote_api=True,
                delay_seconds=0.0,
                call_fn=_mock_call,
            )
            self.assertEqual(resumed["live_calls_executed"], 0)
            self.assertEqual(resumed["cached_reused"], 1)
            self.assertEqual(call_counter["n"], calls_before_resume)

    def test_output_and_thinking_budget_controls(self):
        self.assertEqual(gemini.DEFAULT_MAX_OUTPUT_TOKENS, 32768)
        p = gemini.build_generate_content_payload("x")
        self.assertEqual(p["generationConfig"]["maxOutputTokens"], 32768)
        self.assertNotIn("thinkingConfig", p["generationConfig"])
        p = gemini.build_generate_content_payload("x", max_output_tokens=16000, thinking_budget=4096)
        self.assertEqual(p["generationConfig"]["maxOutputTokens"], 16000)
        self.assertEqual(p["generationConfig"]["thinkingConfig"], {"thinkingBudget": 4096})
        for bad in (0, -1, True, "1024"):
            with self.assertRaisesRegex(ValueError, "GEMINI_THINKING_BUDGET_INVALID"):
                gemini.build_generate_content_payload("x", thinking_budget=bad)
        with self.assertRaisesRegex(ValueError, "GEMINI_THINKING_BUDGET_EXCEEDS_MAX_OUTPUT_TOKENS"):
            gemini.build_generate_content_payload("x", max_output_tokens=2048, thinking_budget=2048)
        for bad in (0, 70000):
            with self.assertRaisesRegex(ValueError, "GEMINI_MAX_OUTPUT_TOKENS_INVALID"):
                gemini.build_generate_content_payload("x", max_output_tokens=bad)
        # Invalid budget must fail before any key resolution / network activity.
        with self.assertRaisesRegex(ValueError, "GEMINI_THINKING_BUDGET_INVALID"):
            gemini.call_gemini(
                "x", source_license_literal="CC BY 4.0", allow_network=True, allow_remote_api=True,
                allow_source_disclosure=True, thinking_budget=-5,
            )

        # Truncated JSON with finishReason=MAX_TOKENS surfaces an explicit truncation hold.
        truncated = json.dumps({
            "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"paper_id":"PMC1","contr'}]}}]
        }).encode("utf-8")
        with self.assertRaisesRegex(RuntimeError, "GEMINI_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD"):
            gemini.parse_gemini_json_response(truncated)
        empty_trunc = json.dumps({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}).encode("utf-8")
        with self.assertRaisesRegex(RuntimeError, "GEMINI_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD"):
            gemini.parse_gemini_json_response(empty_trunc)

    @unittest.skip("requires scripts/ast_task_bundle_run.py, not in the public export")
    def test_runner_forwards_budgets_and_cli_exposes_flags(self):
        import importlib.util
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            (bundle / "papers").mkdir(parents=True)
            (bundle / "papers" / "PMC1.json").write_text(json.dumps({"paper_id": "PMC1"}), encoding="utf-8")
            (bundle / "TASK_BUNDLE_MANIFEST.json").write_text(json.dumps({
                "bundle_id": "b1",
                "profile_contract": {"profile_id": "small_molecule_atp_content_v1", "version": 1, "views": []},
                "papers": [{
                    "paper_id": "PMC1", "xml_sha256": "c" * 64, "context_relpath": "papers/PMC1.json",
                    "source_license_literal": "CC BY 4.0", "images": [],
                }],
            }), encoding="utf-8")
            seen = {}

            def _mock(*args, **kwargs):
                seen.update(kwargs)
                return {"answer": {"calibrations": {}, "contrasts": []}, "usage": {}, "key_tier_used": "paid"}

            rep = run_task_bundle_gemini(
                bundle_dir=bundle, out_dir=root / "out", model="gemini-4-hydrogen", key_tier="paid",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                max_output_tokens=40000, thinking_budget=8192, call_fn=_mock,
            )
            self.assertEqual(seen["max_output_tokens"], 40000)
            self.assertEqual(seen["thinking_budget"], 8192)
            self.assertEqual(seen["key_tier"], "paid")
            self.assertEqual(rep["max_output_tokens"], 40000)
            self.assertEqual(rep["thinking_budget"], 8192)

            seen.clear()
            rep = run_task_bundle_gemini(
                bundle_dir=bundle, out_dir=root / "out2", allow_network=True, allow_remote_api=True,
                delay_seconds=0.0, call_fn=_mock,
            )
            self.assertEqual(seen["max_output_tokens"], 32768)
            self.assertIsNone(seen["thinking_budget"])
            with self.assertRaisesRegex(ValueError, "GEMINI_THINKING_BUDGET_INVALID"):
                run_task_bundle_gemini(bundle_dir=bundle, out_dir=root / "out3", thinking_budget=0, call_fn=_mock)

            # CLI: flags parse and are forwarded (dry-run, no network).
            script = Path(__file__).resolve().parents[1] / "scripts" / "ast_task_bundle_run.py"
            spec = importlib.util.spec_from_file_location("ast_task_bundle_run_cli", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            captured = {}

            def _fake_run(**kwargs):
                captured.update(kwargs)
                return {"filled_results_path": None}

            argv = ["ast_task_bundle_run.py", "--bundle-dir", str(bundle), "--out-dir", str(root / "cli"),
                    "--model", "gemini-4-hydrogen", "--key-tier", "paid",
                    "--max-output-tokens", "49152", "--thinking-budget", "12000"]
            with patch.object(mod, "run_task_bundle", new=_fake_run), patch.object(sys, "argv", argv), \
                    patch("builtins.print"):
                self.assertEqual(mod.main(), 0)
            self.assertEqual(captured["max_output_tokens"], 49152)
            self.assertEqual(captured["thinking_budget"], 12000)
            self.assertEqual(captured["key_tier"], "paid")
            self.assertFalse(captured["allow_network"])

            captured.clear()
            argv = ["ast_task_bundle_run.py", "--bundle-dir", str(bundle), "--out-dir", str(root / "cli2")]
            with patch.object(mod, "run_task_bundle", new=_fake_run), patch.object(sys, "argv", argv), \
                    patch("builtins.print"):
                mod.main()
            self.assertEqual(captured["max_output_tokens"], 32768)
            self.assertIsNone(captured["thinking_budget"])
            self.assertEqual(captured["provider"], "gemini")
            self.assertEqual(captured["model"], "gemini-3.8-flash")

    def test_dual_key_tier_routing_free_vs_paid(self):
        with patch.dict(
            "os.environ",
            {"GEMINI_FREE_API_KEY": "FREE_KEY_123", "GEMINI_API_KEY": "PAID_KEY_999"},
            clear=True,
        ):
            self.assertEqual(
                gemini.resolve_api_key_with_tier(model="gemini-3.8-flash", key_tier="auto", allow_keychain=False),
                ("FREE_KEY_123", "free"),
            )
            self.assertEqual(
                gemini.resolve_api_key_with_tier(model="gemini-4-hydrogen", key_tier="auto", allow_keychain=False),
                ("PAID_KEY_999", "paid"),
            )
            self.assertEqual(
                gemini.resolve_api_key_with_tier(model="gemini-4-carbon", key_tier="auto", allow_keychain=False),
                ("PAID_KEY_999", "paid"),
            )
            self.assertEqual(
                gemini.resolve_api_key_with_tier(model="gemini-3.8-flash", key_tier="paid", allow_keychain=False),
                ("PAID_KEY_999", "paid"),
            )

        with patch.dict("os.environ", {"GEMINI_API_KEY": "PAID_KEY_999"}, clear=True):
            # Strict free tier must never silently fall back to Paid key.
            with self.assertRaisesRegex(RuntimeError, "GEMINI_FREE_API_KEY_NOT_CONFIGURED"):
                gemini.resolve_api_key_with_tier(model="gemini-3.8-flash", key_tier="free", allow_keychain=False)
            # Auto tier falls back to Paid key when Free key is not configured.
            self.assertEqual(
                gemini.resolve_api_key_with_tier(model="gemini-3.8-flash", key_tier="auto", allow_keychain=False),
                ("PAID_KEY_999", "paid"),
            )


if __name__ == "__main__":
    unittest.main()

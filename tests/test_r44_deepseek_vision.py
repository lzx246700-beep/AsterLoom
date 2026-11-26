"""Offline tests for the cost-guarded DeepSeek `deepseek-flash` Vision adapter and runner wiring.

No real network/API calls: all HTTP is mocked via `urllib.request.OpenerDirector.open`.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from r031_evidence import r44_deepseek_vision as ds
from r031_evidence.task_bundle import build_multimodal_task_bundle, encode_rgb_png
from r031_evidence.task_bundle_runner import run_task_bundle, run_task_bundle_gemini

SECRET = "sk-DS_SECRET_DUMMY_DO_NOT_LEAK"
SYSTEM = "Fixed profile rules. Return ONLY a valid JSON object."
CC_BY = "Creative Commons Attribution 4.0 International License (CC BY 4.0)"

# 2026-11-19 is a Thursday; 2026-11-21 is a Saturday. Times given in UTC (Shanghai = UTC+8).
PEAK_THU_1000_SH = datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)
OFFPEAK_THU_1900_SH = datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc)
WEEKEND_SAT_1000_SH = datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc)


def _tiny_png() -> bytes:
    rows = [bytearray([245, 245, 245] * 60) for _ in range(100)]
    return encode_rgb_png(60, 100, rows)


def _ok_body(content: dict | str | None = None, usage: dict | None = None, finish: str = "stop") -> bytes:
    if content is None:
        content = {"paper_id": "PMC1", "calibrations": {}, "contrasts": []}
    text = content if isinstance(content, str) else json.dumps(content)
    return json.dumps({
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}],
        "usage": usage if usage is not None else {
            "prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
            "completion_tokens": 100, "total_tokens": 1100,
        },
    }).encode("utf-8")


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _approved(**kw):
    base = dict(
        system_prompt=SYSTEM,
        source_license_literal=CC_BY,
        allow_network=True,
        allow_remote_api=True,
        allow_source_disclosure=True,
        api_key=SECRET,
        now_fn=lambda: OFFPEAK_THU_1900_SH,
    )
    base.update(kw)
    return base


class TestDeepSeekVisionAdapter(unittest.TestCase):
    def test_default_network_off_approvals_and_license_gates(self):
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_NETWORK_DEFAULT_OFF"):
            ds.call_deepseek_vision("x", system_prompt=SYSTEM, source_license_literal=CC_BY)
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_EXPLICIT_APPROVAL_REQUIRED"):
            ds.call_deepseek_vision("x", system_prompt=SYSTEM, source_license_literal=CC_BY, allow_network=True)
        for lic in ("CC BY-NC 4.0", "http://creativecommons.org/licenses/by-nc-nd/4.0/", "", None):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_REMOTE_AUTHOR_LICENSE_UNVERIFIED_OR_RESTRICTED_HOLD"):
                ds.call_deepseek_vision("x", **_approved(source_license_literal=lic))
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_MAX_TOKENS_INVALID"):
            ds.call_deepseek_vision("x", **_approved(max_tokens=16385))
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_MODEL_IDENTIFIER_INVALID"):
            ds.call_deepseek_vision("x", **_approved(model="../evil"))

    def test_peak_window_logic_and_gate(self):
        def sh(y, m, d, hh, mm):  # Shanghai wall clock (UTC+8, no DST)
            return datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=8)))

        self.assertFalse(ds.is_peak_window(sh(2026, 11, 19, 8, 49)))
        self.assertTrue(ds.is_peak_window(sh(2026, 11, 19, 8, 50)))
        self.assertTrue(ds.is_peak_window(sh(2026, 11, 19, 11, 59)))
        self.assertFalse(ds.is_peak_window(sh(2026, 11, 19, 12, 0)))
        self.assertFalse(ds.is_peak_window(sh(2026, 11, 19, 13, 49)))
        self.assertTrue(ds.is_peak_window(sh(2026, 11, 19, 13, 50)))
        self.assertTrue(ds.is_peak_window(sh(2026, 11, 19, 17, 59)))
        self.assertFalse(ds.is_peak_window(sh(2026, 11, 19, 18, 0)))
        self.assertTrue(ds.is_peak_window(PEAK_THU_1000_SH))
        self.assertFalse(ds.is_peak_window(WEEKEND_SAT_1000_SH))
        self.assertFalse(ds.is_peak_window(OFFPEAK_THU_1900_SH))

        opened = []

        def _fake_open(self_opener, req, timeout=None):
            opened.append(req.full_url)
            return _FakeHTTPResponse(_ok_body())

        with patch("urllib.request.OpenerDirector.open", new=_fake_open):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_PEAK_WINDOW_HOLD"):
                ds.call_deepseek_vision("x", **_approved(now_fn=lambda: PEAK_THU_1000_SH))
            self.assertEqual(opened, [])  # blocked before any HTTP

            weekend = ds.call_deepseek_vision("x", **_approved(now_fn=lambda: WEEKEND_SAT_1000_SH))
            self.assertEqual(weekend["pricing_window"], "offpeak")
            offpeak = ds.call_deepseek_vision("x", **_approved())
            self.assertEqual(offpeak["pricing_window"], "offpeak")
            peak = ds.call_deepseek_vision("x", **_approved(now_fn=lambda: PEAK_THU_1000_SH, allow_peak=True))
            self.assertEqual(peak["pricing_window"], "peak")
            self.assertAlmostEqual(peak["estimated_usd"], 2 * offpeak["estimated_usd"], places=10)
        self.assertEqual(len(opened), 3)
        self.assertTrue(all(u == "https://api.deepseek.com/chat/completions" for u in opened))

    def test_payload_thinking_disabled_image_url_auth_header_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / "panel_ruler.png"
            png = _tiny_png()
            img.write_bytes(png)
            journal = root / "resp.http.json"
            captured = {}

            def _fake_open(self_opener, req, timeout=None):
                captured["url"] = req.full_url
                captured["headers"] = {k.lower(): v for k, v in req.header_items()}
                captured["body"] = json.loads(req.data.decode("utf-8"))
                return _FakeHTTPResponse(_ok_body())

            with patch("urllib.request.OpenerDirector.open", new=_fake_open):
                res = ds.call_deepseek_vision(
                    "Per-paper context: PMC1 Figure 2", image_paths=[img], response_journal=journal, **_approved()
                )

            body = captured["body"]
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["max_tokens"], 2048)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertFalse(body["stream"])
            # Cache-friendly: system message is exactly the fixed profile rules (no per-paper data).
            self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM})
            user = body["messages"][1]
            self.assertEqual(user["role"], "user")
            self.assertEqual(user["content"][0], {"type": "text", "text": "Per-paper context: PMC1 Figure 2"})
            url = user["content"][1]["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(url.split(",", 1)[1]), png)
            self.assertNotIn("PMC1", json.dumps(body["messages"][0]))

            self.assertEqual(captured["headers"]["authorization"], f"Bearer {SECRET}")
            self.assertNotIn(SECRET, captured["url"])
            self.assertNotIn(SECRET, json.dumps(body))
            self.assertNotIn(SECRET, json.dumps(res))
            self.assertTrue(journal.is_file())
            self.assertEqual(stat.S_IMODE(os.stat(journal).st_mode), 0o600)
            self.assertNotIn(SECRET, journal.read_text(encoding="utf-8"))
            self.assertEqual(res["response_sha256"], hashlib.sha256(journal.read_bytes()).hexdigest())
            self.assertEqual(res["provider"], "deepseek")
            self.assertEqual(res["usage"]["prompt_cache_hit_tokens"], 800)
            self.assertFalse(res["thinking_mode"])

        enabled = ds.build_chat_payload("json please", system_prompt=SYSTEM, thinking_mode=True, max_tokens=16384)
        self.assertEqual(enabled["thinking"], {"type": "enabled"})
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_FIXED_SYSTEM_PROMPT_REQUIRED"):
            ds.build_chat_payload("x", system_prompt="  ")
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_JSON_MODE_PROMPT_KEYWORD_MISSING"):
            ds.build_chat_payload("x", system_prompt="rules only")

    def test_http_error_redacts_key_in_message_and_err_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "resp.http.json"
            err_body = json.dumps({"error": {
                "message": f"Authentication Fails, your api key: {SECRET} is invalid (Bearer {SECRET})",
                "type": "authentication_error",
            }}).encode("utf-8")

            def _fake_open(self_opener, req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(err_body))

            with patch("urllib.request.OpenerDirector.open", new=_fake_open):
                with self.assertRaises(RuntimeError) as cm:
                    ds.call_deepseek_vision("x", response_journal=journal, **_approved())
            msg = str(cm.exception)
            self.assertIn("DEEPSEEK_AUTH_ERROR_HOLD|authentication_error", msg)
            self.assertNotIn(SECRET, msg)
            err_file = journal.with_suffix(".err.json")
            self.assertTrue(err_file.is_file())
            self.assertEqual(stat.S_IMODE(os.stat(err_file).st_mode), 0o600)
            self.assertNotIn(SECRET, err_file.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", err_file.read_text(encoding="utf-8"))

            def _fake_429(self_opener, req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b"{}"))

            with patch("urllib.request.OpenerDirector.open", new=_fake_429):
                with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_RATE_LIMIT_429_HOLD"):
                    ds.call_deepseek_vision("x", **_approved())

    def test_cost_estimate_cache_hits_and_truncation(self):
        usage = {"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 800_000,
                 "prompt_cache_miss_tokens": 200_000, "completion_tokens": 100_000}
        # 0.2M*0.15 + 0.8M*0.003 + 0.1M*0.60 = 0.03 + 0.0024 + 0.06
        self.assertAlmostEqual(ds.estimate_cost_usd(usage), 0.0924, places=8)
        self.assertAlmostEqual(ds.estimate_cost_usd(usage, peak=True), 0.1848, places=8)
        # Missing miss field: derived from prompt - hit.
        self.assertAlmostEqual(
            ds.estimate_cost_usd({"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 1_000_000}), 0.003, places=8
        )
        self.assertEqual(ds.estimate_cost_usd({}), 0.0)

        answer, parsed_usage = ds.parse_chat_json_response(_ok_body(usage={
            "prompt_tokens": 10, "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6,
            "completion_tokens": 3, "total_tokens": 13, "completion_tokens_details": {"reasoning_tokens": 0},
        }))
        self.assertEqual(answer["paper_id"], "PMC1")
        self.assertEqual(parsed_usage["prompt_cache_hit_tokens"], 4)
        self.assertEqual(parsed_usage["reasoning_tokens"], 0)
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD"):
            ds.parse_chat_json_response(_ok_body('{"paper_id":"PMC1","contr', finish="length"))
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_NONCONFORMING_JSON_RESPONSE_HOLD"):
            ds.parse_chat_json_response(_ok_body("not json"))

    def test_resolve_api_key_env_then_keychain(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "ENV_KEY"}, clear=True):
            self.assertEqual(ds.resolve_api_key(allow_keychain=False), "ENV_KEY")
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(ds, "_lookup_keychain", return_value="KC_KEY") as kc:
                self.assertEqual(ds.resolve_api_key(), "KC_KEY")
                kc.assert_called_once_with("AsterLoom.DeepSeek", "asterloom", timeout=10)
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY_NOT_CONFIGURED"):
                ds.resolve_api_key(allow_keychain=False)


class TestDeepSeekRunnerAndCli(unittest.TestCase):
    def _build_bundle(self, root: Path) -> tuple[Path, str, str]:
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
        (xml_dir / "author.xml").write_bytes(xml_bytes)
        xml_sha = hashlib.sha256(xml_bytes).hexdigest()
        img_path = root / "assets" / "PMC9990002" / "fig1.png"
        img_path.parent.mkdir(parents=True)
        img_bytes = _tiny_png()
        img_path.write_bytes(img_bytes)
        img_sha = hashlib.sha256(img_bytes).hexdigest()
        queue_path = root / "queue.json"
        queue_path.write_text(json.dumps({"items": [{
            "paper_id": "PMC9990002",
            "queue_state": "READY_FOR_MULTIMODAL_DIGITIZATION",
            "priority_score": 4,
            "xml_sha256": xml_sha,
            "natural_molecules": [{"preferred_name": "Curcumin", "inchikey": "VFLDPWHFBUODDF-FCXRPNKRSA-N"}],
            "atp_figure_labels": ["Figure 1"],
            "resolved_assets": [{"href": "fig1.png", "path": str(img_path), "sha256": img_sha, "width": 60, "height": 100}],
        }]}), encoding="utf-8")
        bundle_dir = root / "bundle"
        build_multimodal_task_bundle(
            profile_id="small_molecule_atp_content_v1",
            queue_json=queue_path,
            xml_roots=[root / "xmls"],
            repo_root=root,
            out_dir=bundle_dir,
        )
        return bundle_dir, xml_sha, img_sha

    @staticmethod
    def _answer() -> dict:
        return {
            "paper_id": "SPOOFED",
            "calibrations": {"PMC9990002_Fig1": {
                "image_path": "/etc/passwd", "image_sha256": "0" * 64, "unit": "% of control",
                "ticks_px": [10.0, 90.0], "tick_values": [100.0, 0.0], "pixel_tolerance_px": 1.5,
            }},
            "contrasts": [{
                "contrast_key": "PMC9990002:Fig1:Cur_vs_Ctrl", "paper_id": "SPOOFED",
                "author_xml_sha256": "0" * 64, "calibration_id": "PMC9990002_Fig1",
                "molecule": "Curcumin", "treated_y_px": 30.0, "control_y_px": 50.0,
                "endpoint": "ATP_CONTENT", "admission": "ADMIT_FIGURE_NUMERIC",
                "author_significance": "* p < 0.05", "unresolvable_reasons": [],
            }],
        }

    def test_runner_deepseek_dry_run_live_peak_cost_repin_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_dir, xml_sha, img_sha = self._build_bundle(root)
            out_dir = root / "ds_out"

            dry = run_task_bundle(bundle_dir=bundle_dir, out_dir=out_dir, provider="deepseek")
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["model"], "deepseek-flash")
            self.assertEqual(dry["max_output_tokens"], 2048)
            self.assertEqual(dry["live_calls_executed"], 0)
            self.assertEqual(dry["papers"][0]["status"], "READY_FOR_DEEPSEEK_CALL")

            calls = []

            def _mock(user_text, **kwargs):
                calls.append(kwargs)
                return {
                    "answer": self._answer(), "provider": "deepseek", "model": kwargs["model"],
                    "key_tier_used": "deepseek", "usage": {"prompt_tokens": 5000, "prompt_cache_hit_tokens": 4000,
                                                           "completion_tokens": 400},
                    "estimated_usd": 0.000402, "pricing_window": "offpeak",
                    "request_sha256": "a" * 64, "response_sha256": "b" * 64,
                }

            # Peak window without --allow-peak: skipped, zero calls.
            peak = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=root / "peak_out", provider="deepseek",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: PEAK_THU_1000_SH,
            )
            self.assertEqual(calls, [])
            self.assertEqual(peak["live_calls_executed"], 0)
            self.assertEqual(peak["skipped_peak_window_hold"], 1)
            self.assertEqual(peak["papers"][0]["status"], "SKIPPED_PEAK_WINDOW_HOLD")
            self.assertIsNone(peak["filled_results_path"])

            live = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=out_dir, provider="deepseek",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: OFFPEAK_THU_1900_SH,
            )
            self.assertEqual(len(calls), 1)
            kw = calls[0]
            self.assertEqual(kw["model"], "deepseek-flash")
            self.assertEqual(kw["max_tokens"], 2048)
            self.assertFalse(kw["thinking_mode"])
            self.assertFalse(kw["allow_peak"])
            self.assertTrue(kw["offpeak_only"])
            self.assertNotIn("key_tier", kw)
            self.assertNotIn("thinking_budget", kw)
            self.assertEqual(live["papers"][0]["status"], "COMPLETED_DEEPSEEK")
            self.assertEqual(live["provider"], "deepseek")
            self.assertAlmostEqual(live["estimated_usd_total"], 0.000402)
            filled = json.loads((out_dir / "filled_results.json").read_text(encoding="utf-8"))
            self.assertEqual(filled["provider"], "deepseek")
            self.assertEqual(filled["independent_gold"], 0)
            self.assertEqual(filled["training_approved"], 0)
            self.assertEqual(filled["calibrations"]["PMC9990002_Fig1"]["image_sha256"], img_sha)
            self.assertEqual(filled["calibrations"]["PMC9990002_Fig1"]["image_path"], "assets/PMC9990002/fig1.png")
            self.assertEqual(filled["contrasts"][0]["paper_id"], "PMC9990002")
            self.assertEqual(filled["contrasts"][0]["author_xml_sha256"], xml_sha)
            cache = out_dir / "raw_responses" / "PMC9990002.deepseek.parsed.json"
            self.assertTrue(cache.is_file())
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["provider"], "deepseek")

            # Resume: cache hit -> 0 new calls, 0 new USD.
            resumed = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=out_dir, provider="deepseek",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: OFFPEAK_THU_1900_SH,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(resumed["cached_reused"], 1)
            self.assertEqual(resumed["live_calls_executed"], 0)
            self.assertEqual(resumed["estimated_usd_total"], 0.0)

            # Provider isolation: a Gemini run in the same out_dir must not reuse the DeepSeek cache.
            gem_calls = []

            def _gem_mock(user_text, **kwargs):
                gem_calls.append(kwargs["model"])
                return {"answer": self._answer(), "usage": {}, "key_tier_used": "paid"}

            gem = run_task_bundle_gemini(
                bundle_dir=bundle_dir, out_dir=out_dir, allow_network=True, allow_remote_api=True,
                delay_seconds=0.0, call_fn=_gem_mock,
            )
            self.assertEqual(gem_calls, ["gemini-3.8-flash"])
            self.assertEqual(gem["papers"][0]["status"], "COMPLETED_GEMINI")
            self.assertIsNone(gem["estimated_usd_total"])

            # allow_peak + thinking_mode are forwarded; thinking_budget is rejected for DeepSeek.
            run_task_bundle(
                bundle_dir=bundle_dir, out_dir=root / "peak_ok", provider="deepseek",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0, allow_peak=True,
                thinking_mode=True, max_output_tokens=4096, call_fn=_mock, now_fn=lambda: PEAK_THU_1000_SH,
            )
            self.assertTrue(calls[-1]["allow_peak"])
            self.assertTrue(calls[-1]["thinking_mode"])
            self.assertEqual(calls[-1]["max_tokens"], 4096)
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_THINKING_BUDGET_UNSUPPORTED"):
                run_task_bundle(bundle_dir=bundle_dir, out_dir=root / "x", provider="deepseek", thinking_budget=1024)
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_MAX_TOKENS_INVALID"):
                run_task_bundle(bundle_dir=bundle_dir, out_dir=root / "x", provider="deepseek", max_output_tokens=32768)
            with self.assertRaisesRegex(ValueError, "TASK_BUNDLE_PROVIDER_INVALID"):
                run_task_bundle(bundle_dir=bundle_dir, out_dir=root / "x", provider="openai")

    def test_runner_end_to_end_with_real_adapter_and_mocked_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_dir, _, img_sha = self._build_bundle(root)
            bodies = []

            def _fake_open(self_opener, req, timeout=None):
                bodies.append((json.loads(req.data.decode("utf-8")), {k.lower(): v for k, v in req.header_items()}))
                return _FakeHTTPResponse(_ok_body(self._answer(), usage={
                    "prompt_tokens": 2000, "prompt_cache_hit_tokens": 1500, "prompt_cache_miss_tokens": 500,
                    "completion_tokens": 300, "total_tokens": 2300,
                }))

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": SECRET}, clear=False), \
                    patch("urllib.request.OpenerDirector.open", new=_fake_open):
                rep = run_task_bundle(
                    bundle_dir=bundle_dir, out_dir=root / "e2e", provider="deepseek",
                    allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                    now_fn=lambda: WEEKEND_SAT_1000_SH,
                )
            self.assertEqual(len(bodies), 1)
            body, headers = bodies[0]
            self.assertEqual(headers["authorization"], f"Bearer {SECRET}")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertIsInstance(body["messages"][0]["content"], str)
            self.assertNotIn("PMC9990002", body["messages"][0]["content"])  # system stays cache-stable
            self.assertEqual(sum(1 for p in body["messages"][1]["content"] if p["type"] == "image_url"), 1)
            expected = (500 * 0.15 + 1500 * 0.003 + 300 * 0.60) / 1_000_000
            self.assertAlmostEqual(rep["estimated_usd_total"], expected, places=10)
            self.assertEqual(rep["papers"][0]["status"], "COMPLETED_DEEPSEEK")
            run_dir = root / "e2e"
            for f in run_dir.rglob("*"):
                if f.is_file():
                    self.assertNotIn(SECRET, f.read_text(encoding="utf-8", errors="replace"), str(f))
            filled = json.loads((run_dir / "filled_results.json").read_text(encoding="utf-8"))
            self.assertEqual(filled["calibrations"]["PMC9990002_Fig1"]["image_sha256"], img_sha)

    @unittest.skip("requires scripts/ast_task_bundle_run.py, not in the public export")
    def test_cli_provider_deepseek_defaults_and_flags(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "ast_task_bundle_run.py"
        spec = importlib.util.spec_from_file_location("ast_task_bundle_run_cli_ds", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        captured = {}

        def _fake_run(**kwargs):
            captured.clear()
            captured.update(kwargs)
            return {"filled_results_path": None}

        def _run(argv):
            with patch.object(mod, "run_task_bundle", new=_fake_run), \
                    patch.object(sys, "argv", ["ast_task_bundle_run.py", "--bundle-dir", "b", "--out-dir", "o", *argv]), \
                    patch("builtins.print"):
                self.assertEqual(mod.main(), 0)
            return dict(captured)

        c = _run(["--provider", "deepseek"])
        self.assertEqual(c["provider"], "deepseek")
        self.assertEqual(c["model"], "deepseek-flash")
        self.assertEqual(c["max_output_tokens"], 2048)
        self.assertFalse(c["allow_peak"])
        self.assertFalse(c["thinking_mode"])
        self.assertFalse(c["allow_network"])
        c = _run(["--provider", "deepseek", "--thinking-mode"])
        self.assertTrue(c["thinking_mode"])
        self.assertEqual(c["max_output_tokens"], 16384)
        c = _run(["--provider", "deepseek", "--mode", "screen", "--allow-peak", "--thinking-mode", "--max-output-tokens", "8192"])
        self.assertEqual(c["mode"], "screen")
        self.assertTrue(c["allow_peak"])
        self.assertTrue(c["thinking_mode"])
        self.assertEqual(c["max_output_tokens"], 8192)
        c = _run([])
        self.assertEqual(c["provider"], "gemini")
        self.assertEqual(c["mode"], "extract")
        self.assertEqual(c["model"], "gemini-3.8-flash")
        self.assertEqual(c["max_output_tokens"], 32768)

    def test_truncation_preserves_billed_usage_and_cost_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_dir, _, _ = self._build_bundle(root)
            usage = {
                "prompt_tokens": 6573,
                "prompt_cache_hit_tokens": 512,
                "prompt_cache_miss_tokens": 6061,
                "completion_tokens": 2048,
                "total_tokens": 8621,
            }

            def _fake_open(self_opener, req, timeout=None):
                return _FakeHTTPResponse(_ok_body('{"paper_id":"PMC9990002","contr', usage=usage, finish="length"))

            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": SECRET}, clear=False), \
                    patch("urllib.request.OpenerDirector.open", new=_fake_open):
                rep = run_task_bundle(
                    bundle_dir=bundle_dir, out_dir=root / "trunc_out", provider="deepseek",
                    allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                    now_fn=lambda: OFFPEAK_THU_1900_SH,
                )
            self.assertEqual(rep["errors"], 1)
            self.assertEqual(rep["papers"][0]["status"], "ERROR_HOLD")
            self.assertEqual(rep["papers"][0]["error"], "DEEPSEEK_OUTPUT_TRUNCATED_MAX_TOKENS_HOLD")
            self.assertEqual(rep["papers"][0]["usage"]["completion_tokens"], 2048)
            expected_usd = ds.estimate_cost_usd(usage, peak=False)
            self.assertAlmostEqual(rep["papers"][0]["estimated_usd"], expected_usd, places=8)
            self.assertAlmostEqual(rep["estimated_usd_total"], expected_usd, places=8)

    def test_extract_backgate_downgrades_missing_sig_genetic_and_bad_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_dir, _, _ = self._build_bundle(root)

            bad_answer = {
                "paper_id": "PMC9990002",
                "calibrations": {
                    "PMC9990002_Fig1": {
                        "unit": "relative",
                        "ticks_px": [50, 100, 150],
                        "tick_values": [50, 100, 150],  # invalid ascending tick_values copied from px
                        "pixel_tolerance_px": 1.5,
                    }
                },
                "contrasts": [
                    {
                        "contrast_key": "PMC9990002:Fig1:no_sig",
                        "calibration_id": "PMC9990002_Fig1",
                        "molecule": "Curcumin",
                        "dose": "10 uM",
                        "treated_y_px": 60.0,
                        "control_y_px": 80.0,
                        "endpoint": "ATP_CONTENT",
                        "admission": "ADMIT_FIGURE_NUMERIC",
                        "author_significance": "NOT_REPORTED",
                        "unresolvable_reasons": [],
                    },
                    {
                        "contrast_key": "PMC9990002:Fig1:genetic",
                        "calibration_id": "PMC9990002_Fig1",
                        "molecule": "MRPL17 (genetic knockdown)",
                        "tested_form": "shRNA kdMRPL17-sh1",
                        "dose": "N/A (genetic)",
                        "treated_y_px": 60.0,
                        "control_y_px": 80.0,
                        "endpoint": "ATP_CONTENT",
                        "admission": "ADMIT_FIGURE_NUMERIC",
                        "author_significance": "*",
                        "unresolvable_reasons": [],
                    },
                ],
            }

            def _mock(user_text, **kwargs):
                return {"answer": bad_answer, "provider": "deepseek", "model": kwargs["model"],
                        "key_tier_used": "deepseek", "usage": {}, "estimated_usd": 0.001}

            rep = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=root / "bg_out", provider="deepseek",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: OFFPEAK_THU_1900_SH,
            )
            filled = json.loads(Path(rep["filled_results_path"]).read_text(encoding="utf-8"))
            c0, c1 = filled["contrasts"]
            self.assertEqual(c0["admission"], "HOLD")
            self.assertIn("RUNNER_BACKGATE_MISSING_OR_NS_SIGNIFICANCE", c0["unresolvable_reasons"])
            self.assertIn("RUNNER_BACKGATE_INVALID_AXIS_CALIBRATION", c0["unresolvable_reasons"])
            self.assertEqual(c1["admission"], "HOLD")
            self.assertIn("RUNNER_BACKGATE_GENETIC_PERTURBATION_WITHOUT_SMALL_MOLECULE", c1["unresolvable_reasons"])

    def test_screen_mode_cache_stable_prompt_repin_backgate_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_dir, xml_sha, img_sha = self._build_bundle(root)
            out_dir = root / "screen_out"
            seen_prompts = []

            screen_answer = {
                "paper_id": "SPOOFED",
                "paper_verdict": "SEND_FOR_REVIEW",
                "genetic_perturbation_only": False,
                "rationale": "Genuine ATP luminescence figure with axis typo.",
                "figure_screen": [
                    {
                        "calibration_id": "PMC9990002_Fig1",
                        "image": "fig1.png",
                        "panel": "B",
                        "endpoint": "ATP_CONTENT",
                        "verdict": "SEND_FOR_REVIEW",
                        "molecule": "Curcumin",
                        "treated_arm": "10 uM curcumin",
                        "comparator": "Control",
                        "axis_label_observed": "Gene expression vs. Normal",
                        "axis_caption_conflict": True,
                        "significance_literal": "* p < 0.05",
                        "reason": "Y-axis label conflicts with ATP caption.",
                    },
                    {
                        "calibration_id": "PMC9990002_Fig1",
                        "image": "fig1.png",
                        "panel": "C",
                        "endpoint": "ATP_CONTENT",
                        "verdict": "SEND_FOR_REVIEW",
                        "molecule": "Curcumin",
                        "treated_arm": "10 uM curcumin",
                        "comparator": "Control",
                        "axis_label_observed": "ATP level",
                        "axis_caption_conflict": False,
                        "significance_literal": None,
                        "reason": "No star visible.",
                    },
                ],
            }

            def _mock(user_text, **kwargs):
                seen_prompts.append((kwargs["system_prompt"], user_text))
                return {
                    "answer": screen_answer, "provider": "deepseek", "model": kwargs["model"],
                    "key_tier_used": "deepseek",
                    "usage": {"prompt_tokens": 2200, "prompt_cache_hit_tokens": 768, "completion_tokens": 220},
                    "estimated_usd": 0.00035, "pricing_window": "offpeak",
                }

            rep = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=out_dir, provider="deepseek", mode="screen",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: OFFPEAK_THU_1900_SH,
            )
            self.assertEqual(rep["mode"], "screen")
            self.assertIsNone(rep["filled_results_path"])
            self.assertIsNotNone(rep["screen_results_path"])
            self.assertEqual(rep["papers"][0]["paper_verdict"], "UNCERTAIN")
            # System prompt must be cache-stable (no paper ID) and enforce key triage boundaries.
            self.assertNotIn("PMC9990002", seen_prompts[0][0])
            self.assertIn("Do NOT digitize pixel coordinates", seen_prompts[0][0])
            self.assertIn("Mammalian models INCLUDE human, mouse, rat", seen_prompts[0][0])
            self.assertIn("NEVER hold a free single-molecule panel", seen_prompts[0][0])
            self.assertIn("combination/MIX bar", seen_prompts[0][0])

            screen_doc = json.loads(Path(rep["screen_results_path"]).read_text(encoding="utf-8"))
            self.assertEqual(screen_doc["schema"], "asterloom.ch1-multimodal-screen/1")
            self.assertEqual(screen_doc["review_queue_paper_ids"], ["PMC9990002"])
            self.assertEqual(screen_doc["paper_verdict_counts"], {"SEND_FOR_REVIEW": 0, "UNCERTAIN": 1, "HOLD": 0})
            p0 = screen_doc["papers"][0]
            self.assertEqual(p0["paper_id"], "PMC9990002")
            self.assertEqual(p0["author_xml_sha256"], xml_sha)
            # Panel B downgraded from SEND_FOR_REVIEW -> UNCERTAIN due to axis_caption_conflict;
            # Panel C downgraded from SEND_FOR_REVIEW -> HOLD due to missing significance.
            self.assertEqual(p0["figure_screen"][0]["verdict"], "UNCERTAIN")
            self.assertEqual(p0["figure_screen"][0]["image_sha256"], img_sha)
            self.assertEqual(p0["figure_screen"][1]["verdict"], "HOLD")
            self.assertIn("RUNNER_BACKGATE_MISSING_OR_NS_SIGNIFICANCE", p0["figure_screen"][1]["reason"])

            # Resume in screen mode reuses screen cache without colliding with extract mode.
            resumed = run_task_bundle(
                bundle_dir=bundle_dir, out_dir=out_dir, provider="deepseek", mode="screen",
                allow_network=True, allow_remote_api=True, delay_seconds=0.0,
                call_fn=_mock, now_fn=lambda: OFFPEAK_THU_1900_SH,
            )
            self.assertEqual(len(seen_prompts), 1)
            self.assertEqual(resumed["cached_reused"], 1)
            with self.assertRaisesRegex(ValueError, "TASK_BUNDLE_MODE_INVALID"):
                run_task_bundle(bundle_dir=bundle_dir, out_dir=root / "bad_mode", provider="deepseek", mode="invalid")


if __name__ == "__main__":
    unittest.main()

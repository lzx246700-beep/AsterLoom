"""Tests for Profile-driven Task Bundle generation and Y-pixel ruler overlays."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from r031_evidence.task_bundle import (
    BUNDLE_SCHEMA,
    build_multimodal_task_bundle,
    decode_rgb_image,
    encode_rgb_png,
    load_profile_contract,
    render_agent_prompt,
    render_y_pixel_ruler_png,
)


def _synthetic_png(width: int = 80, height: int = 120) -> bytes:
    rows = [bytearray([240, 240, 240] * width) for _ in range(height)]
    # Draw a dark bar at y=40..100, x=20..35
    for y in range(40, 100):
        for x in range(20, 35):
            off = x * 3
            rows[y][off:off + 3] = bytes((40, 60, 120))
    return encode_rgb_png(width, height, rows)


class TestTaskBundle(unittest.TestCase):
    def test_profile_contract_is_pluggable_across_ch1_and_ch2(self):
        ch1 = load_profile_contract("small_molecule_atp_content_v1")
        ch2 = load_profile_contract("bioactive_polymer_v1")

        self.assertEqual(ch1["profile_id"], "small_molecule_atp_content_v1")
        self.assertIn("ATP_CONTENT", ch1["views"][0]["endpoint_allow"])
        self.assertIn("ATP_GENERATION_RATE", ch1["views"][0]["forbidden_endpoints"])

        self.assertEqual(ch2["profile_id"], "bioactive_polymer_v1")
        view_ids = {v["id"] for v in ch2["views"]}
        self.assertIn("polymerization_reaction_v1", view_ids)

        prompt_ch2 = render_agent_prompt(ch2, "ch2_demo", [])
        self.assertIn("bioactive_polymer_v1", prompt_ch2)
        self.assertIn("POLYMER_MN", prompt_ch2)
        self.assertIn("physical_loaded_not_covalent", prompt_ch2)

    def test_render_y_pixel_ruler_preserves_y_coordinates_1_to_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "orig.png"
            out = root / "orig_ruler.png"
            src.write_bytes(_synthetic_png(80, 120))

            meta = render_y_pixel_ruler_png(src, out, major_step=50, minor_step=10, margin_px=50)
            self.assertEqual(meta["original_width"], 80)
            self.assertEqual(meta["original_height"], 120)
            self.assertEqual(meta["ruler_width"], 180)
            self.assertEqual(meta["ruler_height"], 120)
            self.assertEqual(meta["y_coordinate_mapping"], "1:1_IDENTICAL_TO_ORIGINAL_IMAGE")

            w, h, _ = decode_rgb_image(out)
            self.assertEqual((w, h), (180, 120))

    def test_build_multimodal_task_bundle_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_dir = root / "xmls" / "PMC9990001"
            xml_dir.mkdir(parents=True)
            xml_bytes = (
                b'<article><front><article-meta><title-group>'
                b'<article-title>Curcumin rescues cellular ATP content in HepG2 cells</article-title>'
                b'</title-group></article-meta></front><body>'
                b'<sec sec-type="methods"><p>Intracellular ATP content was measured using CellTiter-Glo.</p></sec>'
                b'<sec><p>As shown in <xref ref-type="fig" rid="fig1">Figure 1</xref>, 10 uM curcumin '
                b'increased intracellular ATP levels after H2O2 stress.</p>'
                b'<fig id="fig1"><label>Figure 1</label>'
                b'<caption><p>Intracellular ATP levels in HepG2 cells treated with 10 uM curcumin for 24 h.</p></caption>'
                b'<graphic xlink:href="fig1.png" xmlns:xlink="http://www.w3.org/1999/xlink"/></fig></sec>'
                b'</body></article>'
            )
            xml_path = xml_dir / "author.xml"
            xml_path.write_bytes(xml_bytes)
            xml_sha = hashlib.sha256(xml_bytes).hexdigest()

            img_path = root / "assets" / "PMC9990001" / "fig1.png"
            img_path.parent.mkdir(parents=True)
            img_bytes = _synthetic_png(100, 140)
            img_path.write_bytes(img_bytes)
            img_sha = hashlib.sha256(img_bytes).hexdigest()

            queue_path = root / "queue.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "paper_id": "PMC9990001",
                                "queue_state": "READY_FOR_MULTIMODAL_DIGITIZATION",
                                "priority_score": 4,
                                "xml_sha256": xml_sha,
                                "natural_molecules": [{"preferred_name": "Curcumin", "inchikey": "VFLDPWHFBUODDF-FCXRPNKRSA-N"}],
                                "atp_figure_labels": ["Figure 1"],
                                "resolved_assets": [
                                    {
                                        "href": "fig1.png",
                                        "path": str(img_path),
                                        "sha256": img_sha,
                                        "width": 100,
                                        "height": 140,
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            out_dir = root / "bundle_01"
            manifest = build_multimodal_task_bundle(
                profile_id="small_molecule_atp_content_v1",
                queue_json=queue_path,
                xml_roots=[root / "xmls"],
                repo_root=root,
                out_dir=out_dir,
            )

            self.assertEqual(manifest["schema"], BUNDLE_SCHEMA)
            self.assertEqual(manifest["new_paid_model_calls"], 0)
            self.assertEqual(manifest["independent_gold"], 0)
            self.assertEqual(manifest["training_approved"], 0)
            self.assertEqual(manifest["paper_count"], 1)
            self.assertEqual(manifest["total_images"], 1)

            self.assertTrue((out_dir / "TASK_BUNDLE_MANIFEST.json").is_file())
            self.assertTrue((out_dir / "AGENT_PROMPT.md").is_file())
            self.assertTrue((out_dir / "response_template.json").is_file())
            self.assertTrue((out_dir / "papers/PMC9990001/context.json").is_file())
            self.assertTrue((out_dir / "papers/PMC9990001/fig1_ruler.png").is_file())

            tmpl = json.loads((out_dir / "response_template.json").read_text(encoding="utf-8"))
            self.assertEqual(tmpl["contrasts"][0]["author_xml_sha256"], xml_sha)
            self.assertEqual(tmpl["calibrations"]["PMC9990001_Fig1"]["image_sha256"], img_sha)
            self.assertEqual(tmpl["calibrations"]["PMC9990001_Fig1"]["image_path"], "assets/PMC9990001/fig1.png")


if __name__ == "__main__":
    unittest.main()

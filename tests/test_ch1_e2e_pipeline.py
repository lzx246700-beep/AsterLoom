"""Offline tests for end-to-end Chapter 1 OA discovery, XML+image acquisition, and export."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from r031_evidence.ch1_e2e_pipeline import (
    _fetch_ch1_xml_s3_first,
    acquire_and_triage_batch,
    build_open_discovery_epmc_queries,
    build_targeted_epmc_queries,
    load_query_batch_json,
    search_europe_pmc,
    select_query_molecules,
)


class TestCh1E2EPipeline(unittest.TestCase):
    def test_targeted_queries_exclude_endpoints_and_build_oa_clauses(self):
        queries = build_targeted_epmc_queries(batch_size=5, max_molecules=15)
        self.assertGreaterEqual(len(queries), 2)
        joined = "\n".join(queries)
        self.assertIn("OPEN_ACCESS:y", joined)
        self.assertIn("intracellular ATP", joined)
        self.assertNotIn('"Adenosine triphosphate"', joined)

    def test_queries_pin_molecules_in_title_abs_with_atp_and_non_mammalian_not(self):
        recs = [
            {"preferred_name": "Resveratrol", "natural_product_status": "confirmed", "provenance_sources": ["oxphos_positive_candidates_v0_2"]},
            {"preferred_name": "(+)-Dihydromyricetin", "aliases": ["Ampelopsin"], "natural_product_status": "confirmed", "provenance_sources": ["v3_evidence_molecules"]},
            {"preferred_name": "(6)-Gingerol", "natural_product_status": "provisional", "provenance_sources": ["oxphos_positive_candidates_v0_2"]},
            {"preferred_name": "Adenosine triphosphate", "natural_product_status": "confirmed"},
            {"preferred_name": "2,3-DHBA", "natural_product_status": "confirmed"},
            {"preferred_name": "Curcumin", "natural_product_status": "confirmed", "provenance_sources": ["v3_evidence_molecules"]},
        ]
        queries = build_targeted_epmc_queries(recs, batch_size=10)
        self.assertEqual(len(queries), 1)
        q = queries[0]
        self.assertTrue(q.startswith('TITLE_ABS:("Dihydromyricetin" OR "Curcumin" OR "Resveratrol" OR "6-Gingerol")'), q)
        self.assertIn('AND ("ATP content" OR "intracellular ATP" OR "cellular ATP" OR "ATP levels")', q)
        self.assertNotIn("CellTiter-Glo", q)
        self.assertIn('OPEN_ACCESS:y AND (LICENSE:"cc by" OR LICENSE:"cc0")', q)
        self.assertTrue(q.endswith(")"))
        for term in ("TITLE:plant", "TITLE:seed", "TITLE:fruit", "TITLE:yeast", "TITLE:Saccharomyces", "TITLE:bacteria"):
            self.assertIn(term, q.split(" NOT (", 1)[1])
        self.assertNotIn("Adenosine", q)
        self.assertNotIn("DHBA", q)
        self.assertNotIn("(+)", q)

        no_not = build_targeted_epmc_queries(recs, exclude_non_mammalian=False)[0]
        self.assertNotIn(" NOT ", no_not)

        # Already-covered parents (by preferred name or alias) are skipped; priority names go first.
        picked = select_query_molecules(recs, covered_names={"ampelopsin", "Curcumin"}, priority_names=["6-Gingerol"])
        self.assertEqual(picked, ["6-Gingerol", "Resveratrol"])
        with self.assertRaises(ValueError):
            build_targeted_epmc_queries(recs, batch_size=0)

    def test_registry_queries_are_batched_and_titled(self):
        recs = [
            {
                "preferred_name": f"Mockomer {i:02d}",
                "natural_product_status": "confirmed",
                "provenance_sources": ["public_example_fixture"],
            }
            for i in range(25)
        ]
        queries = build_targeted_epmc_queries(recs, batch_size=10, max_molecules=40)
        self.assertEqual(len(queries), 3)
        for q in queries:
            self.assertTrue(q.startswith("TITLE_ABS:("))
            self.assertIn(" NOT (TITLE:plant", q)
            self.assertLess(len(q), 1500)

    def test_offline_mocked_e2e_crawl_triage_image_fetch_and_export(self):
        xml_body = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta>
    <article-id pub-id-type="pmcid">PMC9000001</article-id>
    <article-id pub-id-type="doi">10.1234/test.2026.1</article-id>
    <article-title>Resveratrol enhances intracellular ATP in macrophages</article-title>
    <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"><license-p>This is an open-access article distributed under the terms of the Creative Commons Attribution License (CC BY 4.0).</license-p></license></permissions>
    <abstract><p>Resveratrol increased intracellular ATP levels.</p></abstract>
  </article-meta></front>
  <body><fig id="f2"><label>Figure 2</label><caption><p>Intracellular ATP levels (nmol/mg protein).</p></caption><graphic xlink:href="fig2_atp.jpg"/></fig></body>
</article>"""
        # Minimal valid 2x2 JPEG-like or PNG header bytes (here valid PNG header bytes won't match .jpg, so use valid JPEG SOF0 header bytes)
        img_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xc0\x00\x0b\x08\x01\xf4\x02\x58\x03\x01\x11\x00\xff\xd9"
        img_md5 = hashlib.md5(img_bytes).hexdigest()

        def fake_reader(url: str, limit: int = 8000000) -> dict:
            if "europepmc/webservices/rest/search" in url:
                payload = {
                    "resultList": {
                        "result": [
                            {"pmcid": "PMC4761398", "isOpenAccess": "Y", "license": "cc by", "title": "Holdout paper"},
                            {"pmcid": "PMC9000001", "doi": "10.1234/test.2026.1", "isOpenAccess": "Y", "license": "cc by", "title": "Resveratrol enhances intracellular ATP in macrophages"},
                        ]
                    }
                }
                return {"status": 200, "body": json.dumps(payload).encode()}
            if url.endswith("/PMC9000001/fullTextXML"):
                return {"status": 200, "body": xml_body}
            if url.endswith("/metadata/PMC9000001.1.json"):
                meta = {
                    "pmcid": "PMC9000001",
                    "is_retracted": False,
                    "media_urls": [f"s3://pmc-oa-opendata/PMC9000001.1/fig2_atp.jpg?md5={img_md5}"],
                }
                return {"status": 200, "body": json.dumps(meta).encode()}
            if "fig2_atp.jpg" in url:
                return {"status": 200, "body": img_bytes}
            return {"status": 404, "body": b""}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            excl = root / "EXCLUSIONS.json"
            excl.write_text(json.dumps({"holdout_ids": ["PMC4761398"], "known_project_pmcids": []}), encoding="utf-8")
            search_json = root / "search.json"
            search_res = search_europe_pmc(["test query"], excl, search_json, sleep_s=0, reader=fake_reader)
            pmcids = [r["pmcid"] for r in search_res["resultList"]["result"]]
            self.assertEqual(pmcids, ["PMC9000001"])

            out_dir = root / "run_out"
            manifest = acquire_and_triage_batch(search_json, excl, out_dir, limit=5, sleep_s=0, reader=fake_reader)
            self.assertEqual(manifest["new_paid_model_calls"], 0)
            self.assertEqual(manifest["independent_gold"], 0)
            self.assertEqual(manifest["acquired_papers"], 1)
            self.assertEqual(manifest["route_counts"], {"ROUTE_MULTIMODAL_AND_TEXT": 1})
            self.assertEqual(manifest["figure_y_queue_counts"], {"READY_FOR_MULTIMODAL_DIGITIZATION": 1})
            self.assertTrue((out_dir / "figure_assets/PMC9000001/fig2_atp.jpg").is_file())
            self.assertTrue((out_dir / "compliant_export/ch1_readiness_summary_v1.json").is_file())

    @unittest.skip("requires scripts/ast_ch1_fetch_pubchem_snapshot.py, not in the public export")
    def test_open_discovery_queries_saturation_cap_and_pubchem_auto_expansion(self):
        from scripts.ast_ch1_fetch_pubchem_snapshot import (
            extract_unregistered_natural_candidates,
            fetch_and_merge_targets,
        )
        from r031_evidence.ch1_enrichment import classify_cell_context, enrich_ch1_rows
        from r031_evidence.ch1_pre_llm_triage import triage_jats_paper

        # 1. Open-discovery queries break the 393 closed dictionary and include depletion hard-negative track
        open_qs = build_open_discovery_epmc_queries()
        self.assertEqual(len(open_qs), 6)
        joined = "\n".join(open_qs)
        self.assertIn("flavonoid", joined)
        self.assertIn("alkaloid", joined)
        self.assertIn("terpenoid", joined)
        self.assertIn("ATP depletion", joined)
        self.assertIn("OPEN_ACCESS:y", joined)
        self.assertIn("NOT (TITLE:plant", joined)

        # 2. Cell context classifier separates non-tumor primary/normal vs tumor/cancer lines
        self.assertEqual(classify_cell_context({"cell_model": "H9c2 cardiomyocytes"}), "NON_TUMOR_PRIMARY_OR_NORMAL")
        self.assertEqual(classify_cell_context({"cell_model": "porcine oocytes"}), "NON_TUMOR_PRIMARY_OR_NORMAL")
        self.assertEqual(classify_cell_context({"cell_model": "A549 lung carcinoma cells"}), "TUMOR_CANCER_LINE")
        self.assertEqual(classify_cell_context({"cell_model": "HepG2 hepatocytes"}), "TUMOR_CANCER_LINE")
        self.assertEqual(classify_cell_context({"cell_model": "SH-SY5Y neuronal cells"}), "TUMOR_CANCER_LINE")
        self.assertEqual(classify_cell_context({"cell_model": "normal human lung epithelial cells"}), "NON_TUMOR_PRIMARY_OR_NORMAL")
        self.assertEqual(classify_cell_context({"cell_model": ""}), "UNKNOWN")

        enriched = enrich_ch1_rows([{"record_id": "r1", "cell_model": "C2C12 myotubes", "stimulation": "H2O2"}])
        self.assertEqual(enriched[0]["cell_context"], "NON_TUMOR_PRIMARY_OR_NORMAL")
        self.assertEqual(enriched[0]["paradigm_regime"], "STRESS_RESCUE")

        # 3. Open-discovery triage routes novel natural compound (not in 393 registry) + skips saturated image fetch
        xml_novel = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta>
    <article-id pub-id-type="pmcid">PMC9000099</article-id>
    <article-id pub-id-type="doi">10.1234/test.2026.99</article-id>
    <article-title>Novel flavonoid xanthonoside X9 restores intracellular ATP in H9c2 cardiomyocytes</article-title>
    <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"><license-p>CC BY 4.0</license-p></license></permissions>
    <abstract><p>H9c2 cardiomyocytes were treated with 10 uM novel flavonoid xanthonoside X9 and intracellular ATP content increased.</p></abstract>
  </article-meta></front>
  <body><fig id="f1"><label>Figure 1</label><caption><p>Intracellular ATP levels in H9c2 cells.</p></caption><graphic xlink:href="fig1_atp.jpg"/></fig></body>
</article>"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            xml_p = root / "PMC9000099.xml"
            xml_p.write_bytes(xml_novel)
            tri_closed = triage_jats_paper(xml_p, paper_id="PMC9000099", allow_open_discovery=False)
            self.assertEqual(tri_closed["route"], "HOLD_NO_NATURAL_MOLECULE_DETECTED")
            tri_open = triage_jats_paper(xml_p, paper_id="PMC9000099", allow_open_discovery=True)
            self.assertEqual(tri_open["route"], "ROUTE_OPEN_DISCOVERY_CANDIDATE")
            self.assertEqual(tri_open["batch_priority"], "P0_OPEN_DISCOVERY_NEW_PARENT")
            self.assertEqual(tri_open["cell_context_hint"], "NON_TUMOR_PRIMARY_OR_NORMAL")

            # 4. Dynamic PubChem candidate harvesting from screen_results.json + offline registry merge
            screen_json = root / "screen_results.json"
            screen_json.write_text(json.dumps({
                "papers": [{
                    "paper_id": "PMC9000099",
                    "figure_screen": [
                        {"molecule": "Resveratrol"},           # already pinned -> skip
                        {"molecule": "Oligomycin"},            # synthetic/tool -> skip
                        {"molecule": "berberine + Glutor"},    # combination -> skip
                        {"molecule": "Astragaloside IV"},      # novel natural candidate -> keep
                    ],
                }]
            }), encoding="utf-8")
            cands = extract_unregistered_natural_candidates(screen_json)
            cand_names = [pref for pref, _ in cands]
            self.assertIn("Astragaloside IV", cand_names)
            self.assertNotIn("Resveratrol", cand_names)
            self.assertNotIn("berberine + Glutor", cand_names)

            fake_reg = root / "reg.json"
            fake_reg.write_text(json.dumps({"record_count": 0, "records": [], "source_sha256s": {}}), encoding="utf-8")
            snap_out = root / "snap.json"

            def fake_fetch(name: str) -> dict | None:
                if name == "Astragaloside IV":
                    return {
                        "cid": "13943297",
                        "smiles": "CC1(C2CCC3(C(C2(CCC1O)C)CC(C4C3(CCC4C5(CCC(O5)C(C)(C)O)C)C)O)C)C",
                        "inchikey": "QMNWTVXJQWERTY-UHFFFAOYSA-N",
                        "scaffold_group": "QMNWTVXJQWERTY",
                        "molecular_formula": "C41H68O14",
                        "iupac_name": "Astragaloside IV",
                        "source_url": "https://pubchem.ncbi.nlm.nih.gov/test",
                        "source_query_response_sha256": "a" * 64,
                    }
                return None

            res = fetch_and_merge_targets(
                cands,
                registry_path=fake_reg,
                snapshot_out=snap_out,
                provenance_tag="pubchem_open_discovery_test",
                fetcher=fake_fetch,
                sleep_s=0.0,
            )
            self.assertEqual(res["added_new"], 1)
            self.assertEqual(res["registry_total"], 1)
            merged_reg = json.loads(fake_reg.read_text(encoding="utf-8"))
            self.assertEqual(merged_reg["records"][0]["preferred_name"], "Astragaloside IV")
            self.assertEqual(merged_reg["records"][0]["pubchem_cid"], "13943297")

    def test_ch1_s3_first_concurrency_query_json_backfill_and_null_ns_sidecar(self):
        from r031_evidence.ch1_dataset_builder import (
            backfill_ch1_rows_from_author_xml,
            normalize_cell_model_literal,
            write_ch1_dataset,
        )
        from r031_evidence.ch1_pre_llm_triage import triage_jats_paper

        # 1. S3-first XML fetch (BUCKET/xml/{pmcid}.xml) + EuropePMC fallback + load_query_batch_json
        s3_xml = b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC9010001</article-id><permissions><license>CC BY 4.0</license></permissions></article-meta></front><body><p>Resveratrol 10 uM for 24 h in INS-1 beta cells (P > 0.05, no significant difference in intracellular ATP).</p></body></article>'
        epmc_xml = b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC9010002</article-id><permissions><license>CC BY 4.0</license></permissions></article-meta></front><body><p>MIN6 cells treated with Curcumin did not alter ATP content.</p></body></article>'

        def fake_reader(url: str, limit: int = 8000000) -> dict:
            if url.endswith("/xml/PMC9010001.xml"):
                return {"status": 200, "body": s3_xml}
            if url.endswith("/PMC9010002/fullTextXML"):
                return {"status": 200, "body": epmc_xml}
            return {"status": 404, "body": b""}

        raw1, _, via1, code1, _ = _fetch_ch1_xml_s3_first("PMC9010001", reader=fake_reader, max_retries=0, backoff_s=0.0)
        self.assertEqual(via1, "PMC_S3")
        self.assertEqual(code1, 200)
        self.assertEqual(raw1, s3_xml)

        raw2, _, via2, code2, _ = _fetch_ch1_xml_s3_first("PMC9010002", reader=fake_reader, max_retries=0, backoff_s=0.0)
        self.assertEqual(via2, "EUROPE_PMC")
        self.assertEqual(code2, 200)
        self.assertEqual(raw2, epmc_xml)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            q_json = root / "queries.json"
            q_json.write_text(json.dumps({"queries": ['TITLE_ABS:"Resveratrol" AND OPEN_ACCESS:y']}), encoding="utf-8")
            qs = load_query_batch_json(q_json)
            self.assertEqual(len(qs), 1)

            # 2. Neutral/negative triage signal ("no significant difference", "P > 0.05", "did not alter ATP")
            xml1 = root / "PMC9010001.xml"
            xml1.write_bytes(s3_xml)
            tri1 = triage_jats_paper(xml1, paper_id="PMC9010001")
            self.assertTrue(tri1["has_negative_or_depletion_signal"])

            # 3. Placeholder elimination + single-source author.xml backfill (INS-1/MIN6 -> NON_TUMOR) + NULL_NS export
            self.assertIsNone(normalize_cell_model_literal("mammalian cells"))
            self.assertIsNone(normalize_cell_model_literal("human cells"))
            self.assertEqual(normalize_cell_model_literal("INS-1"), "INS-1")

            rows = [
                {
                    "record_id": "probe:PMC9010001:r1",
                    "paper_id": "PMC9010001",
                    "study_group": "PMC9010001",
                    "endpoint": "ATP_CONTENT",
                    "author_molecule_raw": "Resveratrol",
                    "canonical_parent": "Resveratrol",
                    "smiles": "Oc1ccc(/C=C/c2cc(O)cc(O)c2)cc1",
                    "inchikey": "LUKBXSAWLPMMSZ-OWOJBTEDSA-N",
                    "connectivity_group": "LUKBXSAWLPMMSZ",
                    "parent_state": "PINNED_NATURAL_PARENT_MATCH",
                    "is_natural_or_endogenous": True,
                    "cell_model": "mammalian cells",
                    "dose": "per Methods",
                    "exposure_time": "per Methods",
                    "value_source_type": "FIGURE_ESTIMATED",
                    "model_usable_tier": "HOLD_EXCLUDED",
                    "direction": "NULL_NS",
                    "source_sha256": "a" * 64,
                    "independent_gold": 0,
                    "training_approved": 0,
                    "hold_reasons": ["AUTHOR_STATISTICAL_NS_WITH_NONZERO_PLOT_DIFF"],
                }
            ]
            bf_stats = backfill_ch1_rows_from_author_xml(rows, author_xml_paths=[xml1])
            self.assertEqual(bf_stats["placeholders_cleared"], 1)
            self.assertEqual(rows[0]["dose"], "10 uM")
            self.assertEqual(rows[0]["exposure_time"], "24 h")
            self.assertEqual(rows[0]["cell_model"], "INS-1")
            self.assertEqual(rows[0]["cell_context"], "NON_TUMOR_PRIMARY_OR_NORMAL")

            out_exp = root / "export"
            summary = write_ch1_dataset(out_exp, rows)
            self.assertEqual(summary["null_ns_sidecar_rows"], 1)
            null_p = out_exp / "ch1_null_ns_v1.jsonl"
            self.assertTrue(null_p.is_file())
            ns_row = json.loads(null_p.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(ns_row["direction"], "NULL_NS")
            self.assertEqual(ns_row["cell_context"], "NON_TUMOR_PRIMARY_OR_NORMAL")
            self.assertEqual(ns_row["independent_gold"], 0)
            self.assertEqual(ns_row["training_approved"], 0)


if __name__ == "__main__":
    unittest.main()

"""Tests for AsterLoom pluggable task plugin architecture and Chapter 2 (Model B1/B2) pipeline."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from r031_evidence import ch2_dataset_builder as ch2_builder
from r031_evidence import ch2_e2e_pipeline as ch2_e2e
from r031_evidence import ch2_pre_llm_triage as ch2_triage
from r031_evidence import polymer_reaction_profile as poly_rxn
from r031_evidence import task_plugin


class TestCh2PluggablePipeline(unittest.TestCase):
    def test_task_plugin_registry_exposes_ch1_and_ch2(self):
        plugins = task_plugin.list_plugins()
        ids = {p["profile_id"] for p in plugins}
        self.assertIn("small_molecule_atp_content_v1", ids)
        self.assertIn("bioactive_polymer_v1", ids)

        p1 = task_plugin.get_plugin("small_molecule_atp_content_v1")
        p2 = task_plugin.get_plugin("bioactive_polymer_v1")
        self.assertEqual(p1.load_manifest()["id"], "small_molecule_atp_content_v1")
        self.assertEqual(p2.load_manifest()["id"], "bioactive_polymer_v1")

        q2 = p2.build_queries(batch_size=5, max_molecules=10)
        self.assertEqual(len(q2), 2)
        self.assertTrue(all("polycondensation" in q and "OPEN_ACCESS:y" in q for q in q2))

    def test_ch2_pre_llm_triage_routing_and_priority_rules(self):
        with tempfile.TemporaryDirectory() as td:
            # Build existing corpus where Curcumin is saturated (5 samples)
            corpus_csv = Path(td) / "ch2_corpus.csv"
            with corpus_csv.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "event_id", "paper_id", "canonical_parent", "inchikey",
                        "smiles", "murcko_scaffold_smiles", "polymerization_outcome",
                    ],
                )
                w.writeheader()
                for i in range(5):
                    w.writerow({
                        "event_id": f"ev:{i}",
                        "paper_id": f"PMC_C{i}",
                        "canonical_parent": "Curcumin",
                        "inchikey": "VFLDPWHFBUODDF-FCXRPNKRSA-N",
                        "smiles": "COc1cc(/C=C/C(=O)CC(=O)/C=C/c2ccc(O)c(OC)c2)ccc1O",
                        "murcko_scaffold_smiles": "O=C(CC(=O)/C=C/c1ccccc1)/C=C/c1ccccc1",
                        "polymerization_outcome": "CONFIRMED_POLYMER",
                    })

            xml_new = Path(td) / "PMC_NEW.xml"
            xml_new.write_text(
                """<article><front><article-meta>
                <article-title>Polycondensation of Ferulic Acid with sebacic acid yields bioactive polyesters</article-title>
                <abstract><p>Ferulic acid copolymerized via melt polycondensation. 1H NMR and GPC confirmed Mn = 18.4 kDa, PDI = 1.58.</p></abstract>
                </article-meta></front><body><p>Synthesis and GPC characterization confirmed polyester formation.</p></body></article>""",
                encoding="utf-8",
            )
            xml_sat_fail = Path(td) / "PMC_SAT_FAIL.xml"
            xml_sat_fail.write_text(
                """<article><front><article-meta>
                <article-title>Radical scavenging of Curcumin during free-radical polymerization</article-title>
                <abstract><p>Curcumin inhibited free-radical polymerization and failed to polymerize, yielding only low-molecular-weight oligomer by GPC.</p></abstract>
                </article-meta></front><body><p>Zero conversion and oligomer only were confirmed by 1H NMR and GPC.</p></body></article>""",
                encoding="utf-8",
            )
            xml_sat_pos = Path(td) / "PMC_SAT_POS.xml"
            xml_sat_pos.write_text(
                """<article><front><article-meta>
                <article-title>Polyurethane copolymerization of Curcumin with HDI</article-title>
                <abstract><p>Curcumin underwent polyaddition with HDI to form polyurethane verified by FTIR and GPC.</p></abstract>
                </article-meta></front><body><p>Confirmed polyurethane by FTIR and Mn = 12.0 kDa.</p></body></article>""",
                encoding="utf-8",
            )
            xml_phys = Path(td) / "PMC_PHYS.xml"
            xml_phys.write_text(
                """<article><front><article-meta>
                <article-title>Curcumin-loaded PLGA nanoparticles for cancer therapy</article-title>
                <abstract><p>Curcumin was physically encapsulated into PLGA nanoparticles by non-covalent loading.</p></abstract>
                </article-meta></front><body><p>Drug-loaded nanoparticles were characterized by DLS.</p></body></article>""",
                encoding="utf-8",
            )
            xml_rev = Path(td) / "PMC_REV.xml"
            xml_rev.write_text(
                """<article><front><article-meta>
                <article-title>A systematic review of Ferulic Acid polyesters</article-title>
                <abstract><p>We review polycondensation of ferulic acid.</p></abstract>
                </article-meta></front><body><p>Review text.</p></body></article>""",
                encoding="utf-8",
            )

            res = ch2_triage.triage_ch2_directory(
                [xml_new, xml_sat_fail, xml_sat_pos, xml_phys, xml_rev],
                existing_dataset_path=corpus_csv,
            )
            by_pid = {p["paper_id"]: p for p in res["papers"]}
            self.assertEqual(by_pid["PMC_NEW"]["route"], "ROUTE_CH2_SYNTHESIS_AND_CHAR")
            self.assertEqual(by_pid["PMC_NEW"]["batch_priority"], "P0_NEW_MONOMER_COVALENT")
            self.assertEqual(by_pid["PMC_SAT_FAIL"]["batch_priority"], "P1_POLYMERIZATION_FAILURE_OR_CONTROL")
            self.assertEqual(by_pid["PMC_SAT_POS"]["batch_priority"], "SKIP_SATURATED_MONOMER")
            self.assertEqual(by_pid["PMC_PHYS"]["route"], "HOLD_PHYSICAL_LOADING_ONLY")
            self.assertEqual(by_pid["PMC_REV"]["route"], "SKIP_REVIEW_ARTICLE")
            self.assertEqual(res["independent_gold"], 0)
            self.assertEqual(res["training_approved"], 0)

    def test_jats_polymer_extraction_and_b1_b2_dataset_export(self):
        xml_pos = """<article><front><article-meta>
          <article-title>Melt polycondensation of Ferulic Acid and sebacic acid</article-title>
          <abstract><p>Bioactive poly(ferulic acid-co-sebacic acid) polyester was synthesized.</p></abstract>
        </article-meta></front><body>
          <sec><title>2.1 Synthesis of copolymer</title>
            <p>Ferulic Acid and sebacic acid underwent melt polycondensation via phenolic hydroxyl and carboxyl groups
            using Sn(Oct)2 in DMF at 140 °C for 12 h to form ester bonds.</p>
          </sec>
          <sec><title>2.2 Characterization</title>
            <p>1H NMR and FT-IR confirmed ester linkages of Ferulic Acid copolymer. GPC showed Mn = 18.4 kDa,
            Mw = 29.1 kDa, PDI = 1.58, with monomer conversion of 91.5%.</p>
          </sec>
        </body></article>"""

        xml_neg = """<article><front><article-meta>
          <article-title>Attempted free-radical polymerization of Quercetin</article-title>
          <abstract><p>Quercetin inhibited free-radical polymerization.</p></abstract>
        </article-meta></front><body>
          <sec><title>Experimental</title>
            <p>Quercetin with AIBN in DMSO at 70 °C for 24 h failed to polymerize due to radical scavenging and
            yielded only low-molecular-weight oligomer by GPC.</p>
          </sec>
        </body></article>"""

        with tempfile.TemporaryDirectory() as td:
            p_pos = Path(td) / "PMC_POLY1.xml"
            p_neg = Path(td) / "PMC_POLY2.xml"
            p_pos.write_text(xml_pos, encoding="utf-8")
            p_neg.write_text(xml_neg, encoding="utf-8")

            out_dir = Path(td) / "ch2_export"
            summary = ch2_builder.build_ch2_dataset(out_dir=out_dir, author_xml_paths=[p_pos, p_neg])
            self.assertEqual(summary["independent_gold"], 0)
            self.assertEqual(summary["training_approved"], 0)
            self.assertEqual(summary["b1_usable_summary"]["confirmed_polymer_rows"], 1)
            self.assertEqual(summary["b1_usable_summary"]["failed_or_negative_rows"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["b2_target_ready_rows"], 2)
            self.assertEqual(summary["b2_conditioned_summary"]["with_mn_kda"], 1)

            with (out_dir / "ch2_model_b2_dataset_v1.csv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            by_parent = {r["canonical_parent"]: r for r in rows}
            fa = by_parent["Ferulic Acid"]
            self.assertEqual(fa["polymerization_outcome"], "CONFIRMED_POLYMER")
            self.assertEqual(fa["b1_materializable_label"], "1")
            self.assertEqual(float(fa["mn_kda"]), 18.4)
            self.assertEqual(float(fa["pdi_value"]), 1.58)
            self.assertEqual(float(fa["conversion_or_incorp_pct"]), 91.5)
            self.assertEqual(float(fa["temp_c"]), 140.0)
            self.assertEqual(float(fa["time_h"]), 12.0)
            self.assertEqual(fa["independent_gold"], "0")
            self.assertEqual(fa["training_approved"], "0")

            qc = by_parent["Quercetin"]
            self.assertEqual(qc["polymerization_outcome"], "FAILED_OR_OLIGOMER_NEGATIVE")
            self.assertEqual(qc["b1_materializable_label"], "0")
            self.assertEqual(qc["b2_target_ready"], "True")

    def test_review_guard_flexible_reaction_inference_and_section_granularity(self):
        # 1. Review JATS XML must return [] even if abstract summarizes polymerization + Mn
        xml_review = """<article><front><article-meta>
          <article-title>A systematic review of Ferulic Acid polyesters</article-title>
          <abstract><p>Ferulic Acid polycondensation gave Mn = 18.4 kDa, 1H NMR and FT-IR confirmed ester bonds.</p></abstract>
        </article-meta></front><body><p>Review of polyesters.</p></body></article>"""
        with tempfile.TemporaryDirectory() as td:
            p_rev = Path(td) / "PMC_REV_GUARD.xml"
            p_rev.write_text(xml_review, encoding="utf-8")
            self.assertEqual(poly_rxn.extract_polymer_events_from_jats(p_rev, paper_id="PMC_REV_GUARD"), [])

        # 2. Flexible reaction phrasing ("polymerized" + "ester bonds") should infer reaction_type
        ev_flex = poly_rxn.extract_polymer_reaction_event(
            "Ferulic acid was polymerized. GPC showed Mn = 18.4 kDa, 1H NMR and FT-IR confirmed ester bonds.",
            paper_id="PMC_FLEX",
        )
        self.assertEqual(ev_flex["polymerization_outcome"], "CONFIRMED_POLYMER")
        self.assertEqual(ev_flex["reaction_type"], "POLYCONDENSATION_POLYESTERIFICATION")

        # 3. Multiple synthesis sections for same monomer: failed unmodified control + positive copolymer
        xml_multi = """<article><front><article-meta>
          <article-title>Copolymerization of Ferulic Acid vs unmodified control</article-title>
          <abstract><p>We compared unmodified and copolymerized ferulic acid.</p></abstract>
        </article-meta></front><body>
          <sec><title>2.1 Synthesis of unmodified control</title>
            <p>Unmodified Ferulic Acid failed to polymerize under AIBN in DMSO and yielded only low-molecular-weight oligomer.</p>
          </sec>
          <sec><title>2.2 Synthesis of sebacic acid copolymer</title>
            <p>Ferulic Acid and sebacic acid underwent melt polycondensation at 140 °C for 12 h to form ester bonds.</p>
          </sec>
          <sec><title>2.3 Characterization</title>
            <p>1H NMR and FT-IR confirmed ester linkages of Ferulic Acid copolymer. GPC showed Mn = 19.2 kDa, conversion of 88.5%.</p>
          </sec>
        </body></article>"""
        with tempfile.TemporaryDirectory() as td:
            p_multi = Path(td) / "PMC_MULTI.xml"
            p_multi.write_text(xml_multi, encoding="utf-8")
            evs = poly_rxn.extract_polymer_events_from_jats(p_multi, paper_id="PMC_MULTI")
            outcomes = {e["polymerization_outcome"] for e in evs}
            self.assertIn("FAILED_OR_OLIGOMER_NEGATIVE", outcomes)
            self.assertIn("CONFIRMED_POLYMER", outcomes)

    def test_ch2_query_monomer_selector_excludes_solvents_and_steroids(self):
        names = ch2_e2e.select_ch2_query_monomers(max_molecules=25)
        lowered = {n.casefold() for n in names}
        for bad in ("ethanol", "corticosterone", "allopregnanolone", "15-keto-prostaglandin f2alpha", "glutathione", "amphotericin b"):
            self.assertNotIn(bad, lowered)
        # Classic polymerizable natural monomers should be prioritized at the front
        self.assertTrue({"ferulic acid", "caffeic acid", "curcumin", "cinnamaldehyde"}.issubset(lowered))

    def test_ch2_gsh_trigger_hold_and_deepseek_backgate_verifier(self):
        from r031_evidence import ch2_deepseek_extractor as ds

        # 1. 0-LLM triage should hold GSH-responsive redox trigger papers
        xml_gsh = """<article><front><article-meta>
          <article-title>Tumor-targeted glutathione oxidation catalysis with ruthenium nanoreactors</article-title>
          <abstract><p>Glutathione-responsive nanoreactors catalyze intracellular GSH oxidation in tumors.</p></abstract>
        </article-meta></front><body><p>Polyester carrier verified by 1H NMR and GPC.</p></body></article>"""
        with tempfile.TemporaryDirectory() as td:
            p_gsh = Path(td) / "PMC_GSH.xml"
            p_gsh.write_text(xml_gsh, encoding="utf-8")
            tri = ch2_triage.triage_ch2_jats_paper(p_gsh, paper_id="PMC_GSH")
            self.assertEqual(tri["route"], "HOLD_BIOLOGICAL_TRIGGER_NOT_MONOMER")

        # 2. Deterministic back-gate must reject BIOLOGICAL_TRIGGER_OR_REAGENT & PHYSICAL_ENCAPSULATION
        #    while admitting COVALENT_MONOMER_OR_GRAFT with spectroscopic + MW proof
        fake_ctx = {"paper_id": "PMC_DS_TEST", "xml_sha256": "b" * 64, "is_review": False}
        raw_ans = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "events": [
                {
                    "monomer_name": "Cinnamaldehyde",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "DERIVATIVE_POLYMERIZATION",
                    "reaction_type": "RING_OPENING_POLYMERIZATION",
                    "reactive_sites": ["aldehyde group"],
                    "bond_linkages": ["acetal bond", "carbonate linkage"],
                    "co_monomers": ["mPEG"],
                    "temp_c": 25.0,
                    "time_h": 4.0,
                    "mn_kda": 11.2,
                    "pdi_value": 1.24,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Ring-opening polymerization of cinnamaldehyde cyclic carbonate yielded Mn = 11.2 kDa (PDI = 1.24).",
                },
                {
                    "monomer_name": "Glutathione",
                    "entity_role": "BIOLOGICAL_TRIGGER_OR_REAGENT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "HOMOPOLYMERIZATION",
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                },
                {
                    "monomer_name": "Curcumin",
                    "entity_role": "PHYSICAL_ENCAPSULATION",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "PHYSICAL_LOADING_HOLD",
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                },
            ],
        }
        verified = ds.verify_and_normalize_deepseek_events(raw_ans, fake_ctx)
        by_mol = {e["monomer_identity"]["canonical_parent"]: e for e in verified}
        self.assertEqual(by_mol["Cinnamaldehyde"]["polymerization_outcome"], "CONFIRMED_POLYMER")
        self.assertEqual(by_mol["Cinnamaldehyde"]["characterization_proof"]["mn_kda"], 11.2)
        self.assertEqual(by_mol["Cinnamaldehyde"]["independent_gold"], 0)
        self.assertEqual(by_mol["Glutathione"]["polymerization_outcome"], "BIOLOGICAL_TRIGGER_HOLD")
        self.assertEqual(by_mol["Curcumin"]["polymerization_outcome"], "PHYSICAL_LOADING_HOLD")

    def test_ch2_stereo_alias_normalization_and_crosslinked_b1_b2_decoupling(self):
        from r031_evidence import ch1_parent_registry as reg
        from r031_evidence import ch2_deepseek_extractor as ds

        # 1. Stereochemical / Greek prefixes resolve to existing pinned natural parents
        for raw_name, expected in [
            ("α-lipoic acid", "Lipoic acid"),
            ("DL-α-lipoic acid", "Lipoic acid"),
            ("D-(+)-glucosamine", "D-Glucosamine"),
        ]:
            info = reg.resolve_parent(raw_name)
            self.assertEqual(info["canonical_parent"], expected)
            self.assertTrue(info["is_natural_or_endogenous"])
            self.assertTrue(bool(info["smiles"]))

        # 2. Crosslinked insoluble polybenzoxazine enters B1 (CONFIRMED_POLYMER) with b2_target_ready=False,
        #    while small-molecule cyclodextrin coupling without polymer network stays UNVERIFIED_HOLD.
        fake_ctx = {"paper_id": "PMC_XLINK_TEST", "xml_sha256": "c" * 64, "is_review": False}
        raw_ans = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "paper_reason": "Eugenol benzoxazine thermally cured into a crosslinked polybenzoxazine thermoset.",
            "events": [
                {
                    "monomer_name": "Eugenol",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "COPOLYMERIZATION",
                    "reaction_type": "RING_OPENING_POLYMERIZATION",
                    "bond_linkages": ["C-N-C linkage"],
                    "co_monomers": ["stearyl amine", "formaldehyde"],
                    "temp_c": 230.0,
                    "monomer_precursor_yield_pct": 80.0,
                    "conversion_or_incorp_pct": None,
                    "is_crosslinked_or_insoluble_network": True,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": False,
                    "evidence_quote": "Thermal curing at 230 C formed a crosslinked polybenzoxazine network confirmed by FTIR and DSC.",
                },
                {
                    "monomer_name": "DL-α-lipoic acid",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "BACKBONE_GRAFTING",
                    "reaction_type": "COVALENT_BACKBONE_GRAFTING",
                    "bond_linkages": ["amide bond"],
                    "co_monomers": ["6-amino-6-deoxy-cyclodextrin"],
                    "monomer_precursor_yield_pct": 81.0,
                    "conversion_or_incorp_pct": None,
                    "is_crosslinked_or_insoluble_network": False,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": False,
                    "evidence_quote": "Lipoic acid was coupled with 6-amino-cyclodextrin to obtain LACD in 81% yield.",
                },
            ],
        }
        verified = ds.verify_and_normalize_deepseek_events(raw_ans, fake_ctx)
        by_mol = {e["monomer_identity"]["canonical_parent"]: e for e in verified}
        self.assertEqual(by_mol["Eugenol"]["polymerization_outcome"], "CONFIRMED_POLYMER")
        self.assertIsNone(by_mol["Eugenol"]["characterization_proof"]["conversion_or_incorp_pct"])
        self.assertEqual(by_mol["Eugenol"]["characterization_proof"]["monomer_precursor_yield_pct"], 80.0)
        self.assertEqual(by_mol["Lipoic acid"]["polymerization_outcome"], "UNVERIFIED_HOLD")
        self.assertIn("HOLD_SMALL_MOLECULE_COUPLING_NOT_POLYMER", by_mol["Lipoic acid"]["qualification"]["hold_reasons"])

        with tempfile.TemporaryDirectory() as td:
            summary = ch2_builder.write_ch2_dataset(Path(td), verified)
            self.assertEqual(summary["b1_usable_summary"]["confirmed_polymer_rows"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["b2_target_ready_rows"], 0)
            self.assertEqual(summary["independent_gold"], 0)
            self.assertEqual(summary["training_approved"], 0)

    def test_ch2_b2_physical_target_separation_and_negative_query_track(self):
        import csv
        from r031_evidence import ch2_deepseek_extractor as ds
        from r031_evidence import ch2_e2e_pipeline as e2e

        # 1. Negative / inhibition query track is available and includes phenolic inhibition clauses
        neg_queries = e2e.build_ch2_negative_epmc_queries(batch_size=4, max_molecules=8)
        self.assertEqual(len(neg_queries), 2)
        self.assertIn("inhibition period", neg_queries[0])
        self.assertIn("oligomer only", neg_queries[0])
        self.assertIn("TITLE_ABS:(", neg_queries[0])

        both_queries = e2e.build_ch2_epmc_queries(batch_size=4, max_molecules=8, include_negative_queries=True)
        self.assertEqual(len(both_queries), 4)
        self.assertIn("FAILED_OR_OLIGOMER_NEGATIVE", ds.CH2_DEEPSEEK_SYSTEM_PROMPT)

        # 2. B2 export strictly separates de_novo_chain_mn_kda, monomer_conversion_pct, and grafting_ds_pct
        fake_ctx = {"paper_id": "PMC_B2_SEP", "xml_sha256": "d" * 64, "is_review": False}
        raw_ans = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "events": [
                {
                    "monomer_name": "Lipoic acid",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "DERIVATIVE_POLYMERIZATION",
                    "reaction_type": "RADICAL_OR_ACRYLATE_POLYMERIZATION",
                    "bond_linkages": ["ester bond"],
                    "mn_kda": 29.9,
                    "conversion_or_incorp_pct": 87.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Radical polymerization achieved 87% monomer conversion and Mn = 29.9 kDa.",
                },
                {
                    "monomer_name": "D-Glucosamine",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "BACKBONE_GRAFTING",
                    "reaction_type": "COVALENT_BACKBONE_GRAFTING",
                    "bond_linkages": ["amide bond"],
                    "co_monomers": ["poly(L-glutamic acid)"],
                    "mn_kda": 8.9,
                    "conversion_or_incorp_pct": 45.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "D-Glucosamine was grafted onto poly(L-glutamic acid) with 45% grafting degree (Mn = 8.9 kDa).",
                },
            ],
        }
        verified = ds.verify_and_normalize_deepseek_events(raw_ans, fake_ctx)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            summary = ch2_builder.write_ch2_dataset(out, verified)
            b2_sum = summary["b2_conditioned_summary"]
            self.assertEqual(b2_sum["with_mn_kda"], 2)
            self.assertEqual(b2_sum["with_de_novo_chain_mn_kda"], 1)
            self.assertEqual(b2_sum["with_monomer_conversion_pct"], 1)
            self.assertEqual(b2_sum["with_grafting_ds_pct"], 1)

            rows = {r["canonical_parent"]: r for r in csv.DictReader((out / "ch2_model_b2_dataset_v1.csv").open(encoding="utf-8"))}
            self.assertEqual(rows["Lipoic acid"]["de_novo_chain_mn_kda"], "29.9")
            self.assertEqual(rows["Lipoic acid"]["monomer_conversion_pct"], "87.0")
            self.assertEqual(rows["Lipoic acid"]["grafting_ds_pct"], "")
            self.assertEqual(rows["D-Glucosamine"]["mn_kda"], "8.9")
            self.assertEqual(rows["D-Glucosamine"]["de_novo_chain_mn_kda"], "")
            self.assertEqual(rows["D-Glucosamine"]["monomer_conversion_pct"], "")
            self.assertEqual(rows["D-Glucosamine"]["grafting_ds_pct"], "45.0")

    def test_ch2_epmc_search_retries_transient_http_zero_and_503(self):
        calls = {"n": 0}

        def flaky_reader(url: str, limit: int = 4000000):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"status": 0, "body": b"", "reason": "OFFICIAL_NETWORK_HOLD:TimeoutError"}
            if calls["n"] == 2:
                return {"status": 503, "body": b"", "reason": "OFFICIAL_HTTP_503"}
            return {
                "status": 200,
                "body": json.dumps({"resultList": {"result": []}}).encode("utf-8"),
            }

        with tempfile.TemporaryDirectory() as td:
            out_json = Path(td) / "search.json"
            res = ch2_e2e.search_ch2_europe_pmc(
                ["TITLE_ABS:Curcumin"],
                out_json,
                sleep_s=0.0,
                reader=flaky_reader,
                max_retries=3,
                backoff_s=0.0,
            )
            self.assertEqual(calls["n"], 3)
            self.assertEqual(res["query_receipts"][0]["http"], 200)

    def test_ch2_quote_only_backgate_query_modes_and_gemini_provider(self):
        from unittest.mock import patch
        from r031_evidence import ch2_deepseek_extractor as ds

        # 1. paper_reason must NOT bypass back-gate when quote has no polymer/network evidence
        fake_ctx = {"paper_id": "PMC_QUOTE_ONLY", "xml_sha256": "d" * 64, "is_review": False}
        raw_ans = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "paper_reason": "Dopamine formed a covalent polymer network on ZnO nanoparticles.",
            "events": [
                {
                    "monomer_name": "dopamine",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "HOMOPOLYMERIZATION",
                    "reaction_type": "COVALENT_POLYMERIZATION_GENERAL",
                    "bond_linkages": ["C-N bond"],
                    "co_monomers": [],
                    "is_crosslinked_or_insoluble_network": False,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": False,
                    "evidence_quote": "The suspended solution is then kept under a magnetic stirrer for 5 h at 50 rpm.",
                }
            ],
        }
        verified = ds.verify_and_normalize_deepseek_events(raw_ans, fake_ctx)
        self.assertEqual(verified[0]["polymerization_outcome"], "UNVERIFIED_HOLD")
        self.assertIn("HOLD_MISSING_MW_OR_INCORPORATION_PROOF", verified[0]["qualification"]["hold_reasons"])

        # 2. Dedicated negative_only and linear_gpc_only query modes
        q_neg = ch2_e2e.build_ch2_epmc_queries(batch_size=8, max_molecules=16, query_mode="negative_only")
        self.assertEqual(len(q_neg), 2)
        self.assertTrue(all("inhibition period" in q and "polycondensation" not in q for q in q_neg))

        q_lin = ch2_e2e.build_ch2_epmc_queries(batch_size=8, max_molecules=16, query_mode="linear_gpc_only")
        self.assertEqual(len(q_lin), 2)
        self.assertTrue(all("number-average molecular weight" in q and "TITLE:hydrogel" in q for q in q_lin))

        # 3. Gemini 3.8 Flash provider routing
        with tempfile.TemporaryDirectory() as td:
            xml_p = Path(td) / "PMC_GEM.xml"
            xml_p.write_text(
                '<article><front><article-meta><article-title>Polyester from Ferulic Acid</article-title>'
                '<permissions><license>CC BY 4.0</license></permissions></article-meta></front>'
                '<body><sec><title>Synthesis</title><p>Ferulic acid copolymerized.</p></sec></body></article>',
                encoding="utf-8",
            )
            fake_reply = {
                "answer": {"paper_decision": "REJECT_NON_COVALENT", "paper_reason": "test", "events": []},
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "key_tier_used": "free",
                "usage": {"totalTokenCount": 120},
                "response_sha256": "e" * 64,
            }
            with patch.object(ds, "call_gemini", return_value=fake_reply) as mock_gem:
                out = ds.extract_ch2_paper_with_deepseek(
                    xml_p,
                    journal_dir=Path(td) / "journals",
                    provider="gemini",
                    allow_network=True,
                    allow_remote_api=True,
                    allow_source_disclosure=True,
                )
            mock_gem.assert_called_once()
            self.assertEqual(out["provider"], "gemini")
            self.assertEqual(out["model"], "gemini-3.8-flash")
            self.assertEqual(out["key_tier_used"], "free")

    def test_ch2_s3_first_acquisition_with_md5_and_epmc_fallback(self):
        import hashlib

        s3_xml = (
            b'<article article-type="research-article"><front><article-meta>'
            b'<article-id pub-id-type="pmcid">PMC9990001</article-id>'
            b'<article-title>Polycondensation of Ferulic Acid</article-title>'
            b'<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/" xmlns:xlink="http://www.w3.org/1999/xlink">CC BY</license></permissions>'
            b'</article-meta></front><body><p>Ferulic acid polycondensation.</p></body></article>'
        )
        s3_md5 = hashlib.md5(s3_xml).hexdigest()
        epmc_xml = b'<article><body>fallback</body></article>'

        def fake_reader(url: str, limit: int = 8000000):
            if url.endswith("/metadata/PMC9990001.1.json"):
                meta = {
                    "pmcid": "PMC9990001",
                    "is_retracted": False,
                    "is_pmc_openaccess": True,
                    "license_code": "CC BY",
                    "xml_url": f"s3://pmc-oa-opendata/PMC9990001.1/PMC9990001.1.xml?md5={s3_md5}",
                }
                return {"status": 200, "body": json.dumps(meta).encode("utf-8")}
            if url.endswith("/PMC9990001.1/PMC9990001.1.xml"):
                return {"status": 200, "body": s3_xml}
            if url.endswith("/metadata/PMC9990002.1.json"):
                return {"status": 404, "body": b""}
            if url.endswith("/PMC9990002/fullTextXML"):
                return {"status": 200, "body": epmc_xml}
            return {"status": 404, "body": b""}

        raw1, url1, via1, code1, _ = ch2_e2e._fetch_ch2_xml_s3_first("PMC9990001", fake_reader, max_retries=0, backoff_s=0.0)
        self.assertEqual(via1, "PMC_S3")
        self.assertEqual(code1, 200)
        self.assertEqual(raw1, s3_xml)
        self.assertIn("pmc-oa-opendata.s3.amazonaws.com", url1)

        raw2, url2, via2, code2, _ = ch2_e2e._fetch_ch2_xml_s3_first("PMC9990002", fake_reader, max_retries=0, backoff_s=0.0)
        self.assertEqual(via2, "EUROPE_PMC")
        self.assertEqual(code2, 200)
        self.assertEqual(raw2, epmc_xml)
        self.assertIn("www.ebi.ac.uk", url2)

    def test_ch2_monomer_registry_patch_is_isolated_from_ch1(self):
        from r031_evidence.ch1_parent_registry import load_registry, resolve_parent

        ch1_recs = load_registry()
        ch2_recs = ch2_builder.load_ch2_monomer_registry()
        self.assertFalse(resolve_parent("tannic acid", records=ch1_recs)["is_natural_or_endogenous"])
        self.assertFalse(resolve_parent("benzyl lipoate", records=ch1_recs)["is_natural_or_endogenous"])
        for name, exp_parent in [
            ("tannic acid", "Tannic acid"),
            ("L-valine", "L-Valine"),
            ("L-isoleucine", "L-Isoleucine"),
            ("benzyl lipoate", "Lipoic acid"),
            ("ethyl lipoate", "Lipoic acid"),
            ("sebacic acid", "Sebacic acid"),
            ("fumaric acid", "Fumarate"),
            ("genipin", "(+)-Genipin"),
            ("D-limonene", "Limonene"),
            ("linalool", "Linalool"),
            ("L-ornithine", "L-Ornithine"),
            ("4-vinylguaiacol", "4-Vinylguaiacol"),
            ("vanillin", "Vanillin"),
            ("isosorbide", "Isosorbide"),
            ("β-myrcene", "Myrcene"),
            ("2,5-furandicarboxylic acid", "2,5-Furandicarboxylic acid"),
            ("geraniol", "Geraniol"),
            ("β-ocimene", "beta-Ocimene"),
            ("β-pinene", "beta-Pinene"),
            ("farnesol", "Farnesol"),
            ("syringaldehyde", "Syringaldehyde"),
            ("4-hydroxybenzoic acid", "4-Hydroxybenzoic acid"),
            ("L-alanine", "L-Alanine"),
            ("3,4-dihydroxyphenylalanine", "L-DOPA"),
            ("camptothecin", "Camptothecin"),
            ("hydrocaffeic acid", "Dihydrocaffeic acid"),
            ("β-Phellandrene", "beta-Phellandrene"),
            ("abietic acid", "Abietic acid"),
            ("limonene oxide", "Limonene oxide"),
            ("4-propylguaiacol", "4-Propylguaiacol"),
            ("L-histidine", "L-Histidine"),
            ("chavicol", "Chavicol"),
            ("perillyl alcohol", "Perillyl alcohol"),
            ("sarcosine", "Sarcosine"),
            ("trans-β-farnesene", "beta-Farnesene"),
            ("α-Pinene", "alpha-Pinene"),
            ("α-Terpineol", "alpha-Terpineol"),
            ("borneol", "Borneol"),
            ("hydroquinone", "Hydroquinone"),
            ("vanillyl alcohol", "Vanillyl alcohol"),
            ("raspberry ketone", "Raspberry ketone"),
            ("tyrosol", "Tyrosol"),
            ("pyridoxine", "Pyridoxine"),
            ("3,4-dihydroxybenzyl alcohol", "3,4-Dihydroxybenzyl alcohol"),
            ("cardanol", "Cardanol"),
            ("furfuryl alcohol", "Furfuryl alcohol"),
            ("p-coumaric acid", "p-Coumaric acid"),
            ("erythritol", "Erythritol"),
            ("sorbitol", "D-Sorbitol"),
            ("xylitol", "Xylitol"),
            ("cinnamic acid", "Cinnamic acid"),
            ("phloroglucinol", "Phloroglucinol"),
            ("furfurylamine", "Furfurylamine"),
            ("ω-Pentadecalactone", "omega-Pentadecalactone"),
            ("sinapic acid", "Sinapic acid"),
            ("δ-Dodecalactone", "delta-Dodecalactone"),
            ("δ-Valerolactone", "delta-Valerolactone"),
            ("ricinoleic acid", "Ricinoleic acid"),
            ("ethylene brassylate", "Ethylene brassylate"),
            ("10-undecylenic acid", "10-Undecylenic acid"),
        ]:
            info = resolve_parent(name, records=ch2_recs)
            self.assertEqual(info["canonical_parent"], exp_parent)
            self.assertTrue(info["is_natural_or_endogenous"])
            self.assertTrue(bool(info["smiles"]))
        hmf = resolve_parent("5-hydroxymethylfurfural", records=ch2_recs)
        self.assertTrue(hmf["is_natural_or_endogenous"])
        self.assertTrue(bool(hmf["smiles"]))

    def test_ch2_open_discovery_queries_and_triage_route(self):
        q_open = ch2_e2e.build_ch2_epmc_queries(query_mode="open_discovery")
        self.assertGreaterEqual(len(q_open), 6)
        self.assertTrue(all("OPEN_ACCESS:y" in q for q in q_open))
        self.assertTrue(any("terpenoid" in q for q in q_open))
        self.assertTrue(any("lignin-derived" in q for q in q_open))
        q_r5 = ch2_e2e.build_ch2_epmc_queries(query_mode="open_discovery_r5")
        self.assertEqual(len(q_r5), 5)
        self.assertTrue(any("itaconic acid" in q for q in q_r5))
        self.assertTrue(any("molar ratio" in q for q in q_r5))
        q_r6 = ch2_e2e.build_ch2_epmc_queries(query_mode="open_discovery_r6")
        self.assertEqual(len(q_r6), 5)
        self.assertTrue(any("macrolactone" in q for q in q_r6))
        self.assertTrue(any("failed to polymerize" in q for q in q_r6))

        xml_unmatched = """<article><front><article-meta>
          <article-title>Ring-opening polymerization of renewable beta-myrcene lactone</article-title>
          <abstract><p>A novel bio-based terpene lactone underwent ring-opening polymerization to yield polyester with Mn = 24.5 kDa by GPC and 1H NMR.</p></abstract>
        </article-meta></front><body><p>GPC and 1H NMR confirmed covalent polyester formation.</p></body></article>"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "PMC_OPEN_DISC.xml"
            p.write_text(xml_unmatched, encoding="utf-8")
            tri_closed = ch2_triage.triage_ch2_jats_paper(p, paper_id="PMC_OPEN_DISC", allow_open_discovery=False)
            self.assertEqual(tri_closed["route"], "HOLD_NO_NATURAL_MONOMER_DETECTED")
            tri_open = ch2_triage.triage_ch2_jats_paper(p, paper_id="PMC_OPEN_DISC", allow_open_discovery=True)
            self.assertEqual(tri_open["route"], "ROUTE_CH2_OPEN_DISCOVERY")
            self.assertEqual(tri_open["batch_priority"], "P0_OPEN_DISCOVERY_COVALENT")
            self.assertEqual(tri_open["batch_priority_score"], 90)

    def test_ch2_negative_radical_scavenging_signal_routing(self):
        xml_neg = """<article><front><article-meta>
          <article-title>Effect of Thymol on acrylate polymerization kinetics</article-title>
          <abstract><p>Thymol exhibited strong radical scavenging during polymerization, yielding oligomer only.</p></abstract>
        </article-meta></front><body><p>GPC confirmed low molecular weight oligomer only.</p></body></article>"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "PMC_NEG_RAD.xml"
            p.write_text(xml_neg, encoding="utf-8")
            tri = ch2_triage.triage_ch2_jats_paper(p, paper_id="PMC_NEG_RAD")
            self.assertTrue(tri["has_failure_or_negative_signal"])
            self.assertTrue(any("radical scavenging" in h.lower() for h in tri["failure_or_negative_hits"]))

    def test_ch2_v16_failure_quote_comonomer_incorp_and_oligomer_guards(self):
        from r031_evidence import ch2_condition_features as cf
        from r031_evidence import ch2_deepseek_extractor as ds

        # 1. Helper guard functions
        self.assertTrue(
            cf.has_explicit_polymerization_failure_quote(
                "Under the tested conditions, no polymerization could be observed, even after 4 h at 190°C."
            )
        )
        self.assertTrue(
            cf.has_explicit_polymerization_failure_quote(
                "However, only trace amounts of polymer were obtained (entry 13 in Table 1)."
            )
        )
        self.assertFalse(
            cf.has_explicit_polymerization_failure_quote(
                "Polymerization proceeded smoothly with 92% monomer conversion."
            )
        )
        self.assertTrue(
            cf.is_comonomer_incorporation_pct_suspect(
                "Incorporation (mol%) = 0.78 in functional polyethylene.", 0.78, "COPOLYMERIZATION"
            )
        )
        self.assertTrue(
            cf.is_comonomer_incorporation_pct_suspect(
                "The coumarin content of PEtOx_Coum4 is the nearest to the theoretical composition (3.20%).",
                3.2,
                "DERIVATIVE_POLYMERIZATION",
            )
        )
        self.assertFalse(
            cf.is_comonomer_incorporation_pct_suspect(
                "Monomer conversion reached 88% after 12 h.", 88.0, "COPOLYMERIZATION"
            )
        )
        self.assertTrue(cf.is_oligomer_mn_lt1kda(0.275))
        self.assertFalse(cf.is_oligomer_mn_lt1kda(1.0))
        self.assertFalse(cf.is_oligomer_mn_lt1kda(10.5))

        # 2. Back-gate verification + B2 dataset export isolation
        fake_ctx = {"paper_id": "PMC_V16_GUARDS", "xml_sha256": "f" * 64, "is_review": False}
        raw_ans = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "events": [
                {
                    "monomer_name": "omega-Pentadecalactone",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "HOMOPOLYMERIZATION",
                    "reaction_type": "RING_OPENING_POLYMERIZATION",
                    "bond_linkages": ["ester bond"],
                    "conversion_or_incorp_pct": 0.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": False,
                    "evidence_quote": "Under the tested conditions, no polymerization could be observed, even after 4 h at 190°C.",
                },
                {
                    "monomer_name": "10-Undecylenic acid",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "COPOLYMERIZATION",
                    "reaction_type": "COVALENT_POLYMERIZATION_GENERAL",
                    "bond_linkages": ["C-C bond"],
                    "co_monomers": ["ethylene"],
                    "mn_kda": 104.2,
                    "conversion_or_incorp_pct": 0.78,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Copolymerization yielded Mn = 104.2 kg/mol with 0.78 mol% incorporation of polar comonomer.",
                },
                {
                    "monomer_name": "Geraniol",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "DERIVATIVE_POLYMERIZATION",
                    "reaction_type": "RING_OPENING_POLYMERIZATION",
                    "bond_linkages": ["ester bond"],
                    "mn_kda": 0.275,
                    "conversion_or_incorp_pct": 97.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Monodisperse oligo(caprolactones) achieved 97% monomer conversion with Mn = 275 Da.",
                },
            ],
        }
        verified = ds.verify_and_normalize_deepseek_events(raw_ans, fake_ctx)
        by_mol = {e["monomer_identity"]["canonical_parent"]: e for e in verified}

        pdl = by_mol["omega-Pentadecalactone"]
        self.assertEqual(pdl["polymerization_outcome"], "FAILED_OR_OLIGOMER_NEGATIVE")
        self.assertFalse(pdl["confirmed_polymer_formation"])
        self.assertIsNone(pdl["characterization_proof"]["conversion_or_incorp_pct"])

        uda = by_mol["10-Undecylenic acid"]
        self.assertEqual(uda["polymerization_outcome"], "CONFIRMED_POLYMER")
        self.assertIsNone(uda["characterization_proof"]["conversion_or_incorp_pct"])
        self.assertEqual(uda["characterization_proof"]["comonomer_incorporation_mol_pct"], 0.78)
        self.assertTrue(uda["characterization_proof"]["comonomer_incorporation_pct_hold"])

        ger = by_mol["Geraniol"]
        self.assertTrue(ger["characterization_proof"]["is_oligomer_mn_lt1kda"])

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            summary = ch2_builder.write_ch2_dataset(out, verified)
            self.assertEqual(summary["b1_usable_summary"]["failed_or_negative_rows"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["with_comonomer_incorporation_pct_hold"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["with_oligomer_mn_lt1kda"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["with_monomer_conversion_pct"], 1)
            rows = {r["canonical_parent"]: r for r in csv.DictReader((out / "ch2_model_b2_dataset_v1.csv").open(encoding="utf-8"))}
            self.assertEqual(rows["omega-Pentadecalactone"]["monomer_conversion_pct"], "")
            self.assertEqual(rows["10-Undecylenic acid"]["monomer_conversion_pct"], "")
            self.assertEqual(rows["10-Undecylenic acid"]["comonomer_incorporation_mol_pct"], "0.78")
            self.assertEqual(rows["10-Undecylenic acid"]["comonomer_incorporation_pct_hold"], "True")
            self.assertEqual(rows["Geraniol"]["is_oligomer_mn_lt1kda"], "True")


if __name__ == "__main__":
    unittest.main()

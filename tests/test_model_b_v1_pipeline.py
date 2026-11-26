"""Offline unit tests for Ch2 monomer patch, B2 condition semantics, Model B1/B2 v1.0, and Gold review packet."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from r031_evidence import ch1_parent_registry as ch1_reg
from r031_evidence import ch2_condition_features as cond_feat
from r031_evidence import ch2_dataset_builder as ch2_builder
from r031_evidence import ch2_deepseek_extractor as ch2_ds
from r031_evidence import ch2_e2e_pipeline as ch2_e2e

_HAS_RDKIT_AND_SKLEARN = (
    importlib.util.find_spec("rdkit") is not None
    and importlib.util.find_spec("sklearn") is not None
    and importlib.util.find_spec("joblib") is not None
)


class TestCh2RegistryConditionAndGoldPacket(unittest.TestCase):
    def test_ch2_monomer_patch_expansion_and_ch1_isolation(self):
        ch1_records = ch1_reg.load_registry()
        ch2_records = ch2_builder.load_ch2_monomer_registry()
        self.assertEqual(len(ch1_records), 393)
        self.assertGreaterEqual(len(ch2_records), 437)

        # Ensure zero duplicate InChIKeys in Ch2 registry
        iks = [r["inchikey"] for r in ch2_records if r.get("inchikey")]
        self.assertEqual(len(iks), len(set(iks)))

        # Verify newly added monomers & aliases resolve in Ch2 without polluting Ch1
        expected_pairs = [
            ("2,5-bis(hydroxymethyl)furan", "2,5-Bis(hydroxymethyl)furan"),
            ("BHMF", "2,5-Bis(hydroxymethyl)furan"),
            ("2-Pyrone-4,6-dicarboxylic acid", "2-Pyrone-4,6-dicarboxylic acid"),
            ("PDC", "2-Pyrone-4,6-dicarboxylic acid"),
            ("4-Hydroxybenzaldehyde", "4-Hydroxybenzaldehyde"),
            ("Deoxycholic acid", "Deoxycholic acid"),
            ("Camphoric acid", "Camphoric acid"),
            ("Glutamic acid", "L-glutamic acid"),
            ("Andrographolide", "Andrographolide"),
            ("4-Methylumbelliferone", "4-Methylumbelliferone"),
            ("L-Lactide", "L-Lactide"),
            ("Lactide", "L-Lactide"),
        ]
        for query_name, expected_parent in expected_pairs:
            info = ch1_reg.resolve_parent(query_name, records=ch2_records)
            self.assertTrue(info["is_natural_or_endogenous"], f"Expected {query_name} to resolve in Ch2")
            self.assertEqual(info["canonical_parent"], expected_parent)

        # Confirm Ch1 base registry was NOT polluted with Ch2-only monomers
        self.assertFalse(ch1_reg.resolve_parent("BHMF", records=ch1_records)["is_natural_or_endogenous"])
        self.assertFalse(ch1_reg.resolve_parent("L-Lactide", records=ch1_records)["is_natural_or_endogenous"])

    def test_open_discovery_round4_queries(self):
        q_r4 = ch2_e2e.build_ch2_epmc_queries(query_mode="open_discovery_r4")
        q_all = ch2_e2e.build_ch2_epmc_queries(query_mode="open_discovery")
        self.assertEqual(len(q_r4), 5)
        self.assertEqual(len(q_all), 11)
        joined = " ".join(q_r4)
        self.assertIn("BHMF", joined)
        self.assertIn("limonene oxide", joined)
        self.assertIn("deoxycholic acid", joined)
        self.assertIn("4-methylumbelliferone", joined)
        self.assertIn("RAFT", joined)

    def test_condition_semantic_encoder_and_pdi_guard(self):
        sem_rop = cond_feat.encode_condition_semantics(
            catalysts_or_initiators=["Sn(Oct)2", "DBU"],
            solvents=["toluene"],
            co_monomers=["ε-caprolactone"],
        )
        self.assertEqual(sem_rop["cat_cls_rop_organometallic_or_organic"], 1)
        self.assertEqual(sem_rop["solv_cls_halogenated_aromatic_toluene"], 1)
        self.assertEqual(sem_rop["comon_cls_cyclic_lactone_lactide"], 1)
        self.assertEqual(sem_rop["cat_cls_edc_nhs_coupling"], 0)

        sem_raft = cond_feat.encode_condition_semantics(
            catalysts_or_initiators=["AIBN", "CPADB RAFT CTA"],
            solvents=["DMF"],
            co_monomers=["methyl methacrylate"],
        )
        self.assertEqual(sem_raft["cat_cls_controlled_raft_atrp"], 1)
        self.assertEqual(sem_raft["cat_cls_free_radical_peroxide_or_azo"], 1)
        self.assertEqual(sem_raft["solv_cls_polar_aprotic_dmso_dmf"], 1)
        self.assertEqual(sem_raft["comon_cls_acrylate_methacrylate"], 1)
        self.assertEqual(
            cond_feat.normalize_controlled_living_mode(None, catalysts_or_initiators=["AIBN", "CPADB"]),
            "RAFT",
        )

        # PDI guard catches PMC11085065-like record (mw missing, PDI=98.0 > 20) and Mw < Mn
        self.assertTrue(cond_feat.is_pdi_suspect(4.6, None, 98.0))
        self.assertTrue(cond_feat.is_pdi_suspect(10.0, 7.0, None))
        self.assertTrue(cond_feat.is_pdi_suspect(7.0, 47.07, 1.71))
        self.assertFalse(cond_feat.is_pdi_suspect(12.0, 15.6, 1.30))

    def test_deepseek_schema_backward_compat_and_new_stoichiometry_fields(self):
        ctx = {"paper_id": "PMC_TEST_STOICH", "xml_sha256": "a" * 64, "is_review": False}
        raw = {
            "paper_decision": "ADMIT_COVALENT_POLYMER",
            "events": [
                {
                    "monomer_name": "L-Lactide",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "HOMOPOLYMERIZATION",
                    "reaction_type": "RING_OPENING_POLYMERIZATION",
                    "reactive_sites": ["cyclic ester"],
                    "bond_linkages": ["ester bond"],
                    "co_monomers": ["benzyl alcohol"],
                    "catalysts_or_initiators": ["Sn(Oct)2"],
                    "solvents": ["toluene"],
                    "temp_c": 110.0,
                    "time_h": 6.0,
                    "feed_ratio_m_to_i": 100.0,
                    "monomer_feed_mol_pct": 100.0,
                    "controlled_living_mode": "ROP_LIVING",
                    "mn_kda": 14.2,
                    "mw_kda": 16.5,
                    "pdi_value": 1.16,
                    "conversion_or_incorp_pct": 94.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Living ROP of L-lactide with Sn(Oct)2 at [M]/[I]=100 yielded Mn=14.2 kDa, PDI=1.16.",
                },
                {
                    "monomer_name": "L-Leucine",
                    "entity_role": "COVALENT_MONOMER_OR_GRAFT",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "DERIVATIVE_POLYMERIZATION",
                    "reaction_type": "COVALENT_POLYMERIZATION_GENERAL",
                    "bond_linkages": ["amide bond"],
                    "mn_kda": 4.6,
                    "pdi_value": 98.0,
                    "has_nmr_or_ftir_proof": True,
                    "has_gpc_or_sec_proof": True,
                    "evidence_quote": "Poly(L-leucine) derivative gave Mn=4.6 kDa.",
                },
            ],
        }
        evs = ch2_ds.verify_and_normalize_deepseek_events(raw, ctx)
        self.assertEqual(len(evs), 2)
        lactide_ev = next(e for e in evs if e["monomer_identity"]["canonical_parent"] == "L-Lactide")
        self.assertEqual(lactide_ev["reaction_conditions"]["feed_ratio_m_to_i"], 100.0)
        self.assertEqual(lactide_ev["reaction_conditions"]["controlled_living_mode"], "ROP_LIVING")
        self.assertFalse(lactide_ev["characterization_proof"]["pdi_suspect_hold"])

        leucine_ev = next(e for e in evs if e["monomer_identity"]["canonical_parent"] == "L-Leucine")
        self.assertTrue(leucine_ev["characterization_proof"]["pdi_suspect_hold"])

        with tempfile.TemporaryDirectory() as td:
            summary = ch2_builder.write_ch2_dataset(Path(td), evs)
            self.assertEqual(summary["independent_gold"], 0)
            self.assertEqual(summary["training_approved"], 0)
            self.assertEqual(summary["b2_conditioned_summary"]["with_pdi_suspect_hold"], 1)
            self.assertEqual(summary["b2_conditioned_summary"]["with_feed_ratio_m_to_i"], 1)

    @unittest.skip("requires scripts/ast_ch2_build_gold_review_packet.py, not in the public export")
    def test_ch2_gold_review_packet_generator(self):
        import ast_ch2_build_gold_review_packet as gold_mod

        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            csv_p = td_p / "b2_synth.csv"
            excl_p = td_p / "exclusions.json"
            excl_p.write_text(json.dumps({"holdout_ids": ["PMC_RETIRED_01"]}), encoding="utf-8")

            strata_specs = [
                ("FAILED_OR_OLIGOMER_NEGATIVE", "UNRESOLVED_ROLE", "PMC_NEG"),
                ("CONFIRMED_POLYMER", "HOMOPOLYMERIZATION", "PMC_HOMO"),
                ("CONFIRMED_POLYMER", "COPOLYMERIZATION", "PMC_COPO"),
                ("CONFIRMED_POLYMER", "DERIVATIVE_POLYMERIZATION", "PMC_DERIV"),
                ("CONFIRMED_POLYMER", "BACKBONE_GRAFTING", "PMC_GRAFT"),
            ]
            fieldnames = list(ch2_builder.B2_CSV_COLUMNS)
            with csv_p.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                # Retired paper that must be excluded
                w.writerow({
                    "event_id": "ev_retired",
                    "paper_id": "PMC_RETIRED_01",
                    "canonical_parent": "Ferulic Acid",
                    "smiles": "COc1cc(/C=C/C(=O)O)ccc1O",
                    "is_natural_or_endogenous": "True",
                    "polymerization_outcome": "CONFIRMED_POLYMER",
                    "monomer_role": "HOMOPOLYMERIZATION",
                    "independent_gold": 0,
                    "training_approved": 0,
                })
                for outc, role, prefix in strata_specs:
                    for i in range(4):
                        w.writerow({
                            "event_id": f"ev_{prefix}_{i}",
                            "paper_id": f"{prefix}_{i}",
                            "canonical_parent": f"Monomer_{prefix}_{i}",
                            "author_monomer_literal": f"Monomer_{prefix}_{i}",
                            "smiles": "COc1cc(/C=C/C(=O)O)ccc1O",
                            "inchikey": f"IK_{prefix}_{i}",
                            "is_natural_or_endogenous": "True",
                            "polymerization_outcome": outc,
                            "monomer_role": role,
                            "reaction_type": "POLYCONDENSATION_POLYESTERIFICATION",
                            "de_novo_chain_mn_kda": "12.5" if role != "BACKBONE_GRAFTING" else "",
                            "has_gpc_proof": "True",
                            "has_nmr_ftir_proof": "True",
                            "b2_target_ready": "True",
                            "independent_gold": 0,
                            "training_approved": 0,
                        })

            out_dir = td_p / "gold_out"
            manifest = gold_mod.build_ch2_gold_review_packet(
                csv_p, out_dir, exclusions_path=excl_p, papers_per_stratum=3, max_rows_per_paper=2
            )
            self.assertEqual(manifest["independent_gold"], 0)
            self.assertEqual(manifest["training_approved"], 0)
            self.assertEqual(manifest["total_unique_papers_sampled"], 15)
            self.assertTrue((out_dir / "GOLD_REVIEW_PROTOCOL.md").is_file())
            self.assertTrue((out_dir / "GOLD_SAMPLE_MANIFEST.json").is_file())
            self.assertTrue((out_dir / "human_gold_review_blank.csv").is_file())

            with (out_dir / "human_gold_review_blank.csv").open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 15)
            self.assertNotIn("PMC_RETIRED_01", {r["paper_id"] for r in rows})
            self.assertTrue(all(r["independent_gold"] == "0" and r["training_approved"] == "0" for r in rows))
            self.assertIn("human_covalent_polymer_verified", rows[0])
            self.assertIn("human_mw_conversion_separation_verified", rows[0])


@unittest.skipUnless(_HAS_RDKIT_AND_SKLEARN, "RDKit + scikit-learn + joblib required for Model B v1.0 tests")
class TestModelBV1Pipeline(unittest.TestCase):
    def test_train_evaluate_and_screen_model_b_v1_synthetic(self):
        from r031_evidence import model_b_v1

        mols = [
            ("Ferulic Acid", "COc1cc(/C=C/C(=O)O)ccc1O", "HOMOPOLYMERIZATION", "POLYCONDENSATION_POLYESTERIFICATION", ["Sn(Oct)2"], ["toluene"], ["sebacic acid"], 140.0, 12.0, 18.0, 90.0),
            ("Caffeic Acid", "O=C(O)/C=C/c1ccc(O)c(O)c1", "BACKBONE_GRAFTING", "COVALENT_BACKBONE_GRAFTING", ["EDC", "NHS"], ["water"], ["chitosan"], 25.0, 24.0, None, 35.0),
            ("Vanillin", "COc1cc(C=O)ccc1O", "DERIVATIVE_POLYMERIZATION", "RADICAL_OR_ACRYLATE_POLYMERIZATION", ["AIBN"], ["DMF"], ["methyl methacrylate"], 70.0, 8.0, 25.0, 82.0),
            ("Isosorbide", "O[C@@H]1CO[C@H]2[C@@H]1OC[C@@H]2O", "COPOLYMERIZATION", "POLYCONDENSATION_POLYESTERIFICATION", ["Ti(OBu)4"], ["bulk"], ["1,4-butanediol"], 180.0, 6.0, 22.0, 88.0),
            ("L-Lactide", "C[C@@H]1OC(=O)[C@H](C)OC1=O", "HOMOPOLYMERIZATION", "RING_OPENING_POLYMERIZATION", ["Sn(Oct)2"], ["toluene"], ["benzyl alcohol"], 110.0, 4.0, 30.0, 95.0),
            ("Eugenol", "C=CCc1ccc(O)c(OC)c1", "DERIVATIVE_POLYMERIZATION", "RADICAL_OR_ACRYLATE_POLYMERIZATION", ["AIBN", "CPADB"], ["DMF"], ["styrene"], 75.0, 10.0, 14.0, 78.0),
            ("Gallic Acid", "O=C(O)c1cc(O)c(O)c(O)c1", "BACKBONE_GRAFTING", "COVALENT_BACKBONE_GRAFTING", ["EDC", "NHS"], ["buffer"], ["gelatin"], 30.0, 18.0, None, 40.0),
            ("2,5-Furandicarboxylic acid", "O=C(O)c1ccc(C(=O)O)o1", "COPOLYMERIZATION", "POLYCONDENSATION_POLYESTERIFICATION", ["p-TSA"], ["bulk"], ["ethylene glycol"], 190.0, 5.0, 27.0, 91.0),
            ("Limonene oxide", "C=C(C)[C@H]1CC[C@]2(C)O[C@@H]2C1", "COPOLYMERIZATION", "RING_OPENING_POLYMERIZATION", ["PPNCl"], ["toluene"], ["CO2"], 60.0, 12.0, 19.0, 74.0),
            ("4-Vinylguaiacol", "C=Cc1ccc(O)c(OC)c1", "HOMOPOLYMERIZATION", "RADICAL_OR_ACRYLATE_POLYMERIZATION", ["AIBN"], ["THF"], ["butyl acrylate"], 65.0, 16.0, 11.0, 70.0),
            ("2,5-Bis(hydroxymethyl)furan", "OCc1ccc(CO)o1", "COPOLYMERIZATION", "POLYADDITION_POLYURETHANE", ["DBTDL"], ["DMSO"], ["HDI"], 80.0, 6.0, 16.0, 86.0),
            ("4-Hydroxybenzaldehyde", "O=Cc1ccc(O)cc1", "DERIVATIVE_POLYMERIZATION", "POLYADDITION_POLYURETHANE", ["DBU"], ["DMF"], ["MDI"], 85.0, 8.0, 21.0, 84.0),
        ]

        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            csv_p = td_p / "synth_b2.csv"
            with csv_p.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ch2_builder.B2_CSV_COLUMNS)
                w.writeheader()
                idx = 0
                for rep in range(3):
                    for name, smi, role, rxn, cats, solvs, comons, tc, th, mn, conv in mols:
                        idx += 1
                        is_neg = (rep == 2 and name in ("Eugenol", "Caffeic Acid", "Gallic Acid", "Vanillin", "4-Vinylguaiacol", "4-Hydroxybenzaldehyde"))
                        outcome = "FAILED_OR_OLIGOMER_NEGATIVE" if is_neg else "CONFIRMED_POLYMER"
                        is_graft = role == "BACKBONE_GRAFTING"
                        w.writerow({
                            "event_id": f"ev_{idx}",
                            "paper_id": f"PMC_{idx}",
                            "study_group": f"PMC_{idx}",
                            "author_monomer_literal": name,
                            "canonical_parent": name,
                            "smiles": smi,
                            "inchikey": f"IK_{name.replace(' ', '_')}_{rep}",
                            "scaffold_group": f"SC_{name[:6]}",
                            "murcko_scaffold_smiles": "",
                            "is_natural_or_endogenous": "True",
                            "monomer_role": role,
                            "reaction_type": rxn,
                            "reactive_sites": "[]",
                            "bond_linkages": "[]",
                            "co_monomers": json.dumps(comons),
                            "polymerization_outcome": outcome,
                            "b1_materializable_label": 0 if is_neg else 1,
                            "qualification_decision": "TASK_READY_CANDIDATE",
                            "hold_reasons": "[]",
                            "monomer_sample_weight": 0.5,
                            "monomer_cap3_selected": "True",
                            "locator": f"sec:{idx}",
                            "source_sha256": "0" * 64,
                            "independent_gold": 0,
                            "training_approved": 0,
                            "catalysts_or_initiators": json.dumps(cats),
                            "solvents": json.dumps(solvs),
                            "temp_c": tc + rep * 2.0,
                            "time_h": th,
                            "mn_kda": "" if (mn is None or is_neg) else mn * (1.0 + 0.1 * rep),
                            "conversion_or_incorp_pct": "" if is_neg else conv,
                            "de_novo_chain_mn_kda": "" if (mn is None or is_neg or is_graft) else mn * (1.0 + 0.1 * rep),
                            "monomer_conversion_pct": "" if (is_neg or is_graft) else conv,
                            "grafting_ds_pct": "" if (is_neg or not is_graft) else conv,
                            "has_gpc_proof": "True",
                            "has_nmr_ftir_proof": "True",
                            "b2_target_ready": "True",
                        })

            out_dir = td_p / "model_b_out"
            report = model_b_v1.train_and_evaluate_model_b_v1(csv_p, out_dir, n_splits=3, morgan_svd_components=8)
            self.assertEqual(report["independent_gold"], 0)
            self.assertEqual(report["training_approved"], 0)
            self.assertTrue((out_dir / "model_b_v1_bundle.joblib").is_file())
            self.assertTrue((out_dir / "model_b_v1_eval_report.json").is_file())
            self.assertIn("Desc8+FG", report["b1_monomer_role_ablation"])
            self.assertIn("Desc8+FG+MorganSVD8", report["b1_monomer_role_ablation"])
            self.assertIn("semantic_conditions_ensemble", report["b2_log10_mn_kda_ablation"]["variants"])
            self.assertIn("legacy_length_counts_ridge", report["b2_log10_mn_kda_ablation"]["variants"])
            self.assertEqual(
                report["b1_auxiliary_feasibility_eval"]["group_molecule"]["role"],
                "AUXILIARY_REFERENCE_ONLY_SMALL_NEGATIVE_SAMPLE",
            )

            # Screen in-domain and out-of-domain molecules
            preds = model_b_v1.screen_monomer_candidates(
                out_dir / "model_b_v1_bundle.joblib",
                [
                    {"name": "Ferulic Acid", "smiles": "COc1cc(/C=C/C(=O)O)ccc1O"},
                    {"name": "Perfluorooctane", "smiles": "FC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F"},
                    {"name": "BadSmiles", "smiles": "not_a_smiles"},
                ],
                catalysts_or_initiators=["Sn(Oct)2"],
                solvents=["toluene"],
                co_monomers=["sebacic acid"],
            )
            self.assertEqual(len(preds), 3)
            self.assertEqual(preds[0]["status"], "OK")
            self.assertEqual(preds[0]["ad_status"], "IN_DOMAIN")
            self.assertAlmostEqual(sum(preds[0]["monomer_role_probs"].values()), 1.0, places=2)
            self.assertIsNotNone(preds[0]["pred_mn_kda"])
            self.assertIsNotNone(preds[0]["pred_monomer_conversion_pct"])
            self.assertEqual(preds[1]["ad_status"], "OUT_OF_DOMAIN")
            self.assertEqual(preds[2]["status"], "INVALID_SMILES")


if __name__ == "__main__":
    unittest.main()

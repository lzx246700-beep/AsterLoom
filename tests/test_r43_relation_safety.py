"""R43 safety sentinels: no fabricated Gold, cross-arm, source or ATP-rate claims."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from r031_evidence import r43_relation as m


class R43ArmSafety(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(m.SCHEMA)
        self.loc = "/article/body[1]/sec[1]/p[1]"
        self.names = {"spermidine": {"IK:SPERMIDINE_SIMULATED_TEST_ONLY"},
                      "quercetin": {"IK:QUERCETIN_SIMULATED_TEST_ONLY"}}
        import re
        self.pattern = re.compile(r"(?<![\w-])(?:spermidine|quercetin)(?!\w|-(?!loaded\b|treated\b))", re.I)

    def tearDown(self):
        self.conn.close()

    def analyze(self, quote, *, indexed=True, r42=None):
        index = {self.loc: {" ".join(quote.split())}} if indexed else {}
        return m.annotate_block(
            self.conn, "PMC0000000", "0"*64, self.loc, quote,
            "ARM_OR_VALUE_UNBOUND", index, self.names,
            self.pattern, r42 or [])

    def test_explicit_single_block_proposal_still_unreviewed(self):
        quote = "Treatment with spermidine increased ATP content compared with vehicle-treated cells."
        result = self.analyze(quote)
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["pairs"], 1)
        candidate = self.conn.execute("SELECT evidence_state,training_approved,hold_reason FROM r43_candidate").fetchone()
        self.assertEqual(candidate[0], "CANDIDATE")
        self.assertEqual(candidate[1], 0)
        self.assertEqual(json.loads(candidate[2]), [])
        pair = self.conn.execute("SELECT scientist_approved,training_approved FROM r43_experiment_relation").fetchone()
        self.assertEqual(pair, (0,0))
        fields = self.conn.execute("SELECT original_literal,span_start,span_end,quote FROM r43_field_provenance").fetchall()
        self.assertTrue(fields)
        for literal,start,end,source in fields:
            self.assertEqual(source[start:end], literal)
        # Idempotent exact source, no duplicate review candidates.
        self.analyze(quote)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM r43_candidate").fetchone()[0],1)

    def test_multiple_model_rescue_and_two_values_never_auto_pair(self):
        text = ("A 48 h treatment with 0.1 μM spermidine increased ATP level in vector cells (+6%) "
                "and in P301L cells (+5%) compared with untreated cells.")
        r = self.analyze(text)
        self.assertEqual(r["pairs"],0)
        self.assertEqual(r["candidates"],1)
        flags=json.loads(self.conn.execute("SELECT hold_reason FROM r43_candidate").fetchone()[0])
        self.assertIn("MULTIPLE_PERCENT_EFFECTS_SAME_BLOCK", flags)
        self.assertIn("STRESS_OR_RESCUE_NOT_HEALTHY_BASELINE", flags)

    def test_mixture_and_carrier_is_not_single_compound(self):
        text = "Spermidine-loaded nanoparticles increased ATP content compared with untreated cells."
        r=self.analyze(text)
        self.assertEqual(r["candidates"],0)
        self.assertEqual(r["pairs"],0)
        state=self.conn.execute("""SELECT identity_state FROM r43_entity_mention
           WHERE kind='CHEMICAL_CANDIDATE'""").fetchone()[0]
        self.assertIn("MIXTURE",state)

    def test_negated_effect_must_not_be_new_positive_candidate(self):
        r=self.analyze("Treatment with spermidine did not increase ATP content compared with vehicle-treated cells.")
        self.assertEqual(r["candidates"],0)
        self.assertEqual(r["pairs"],0)

    def test_ocr_rate_is_not_atp_content(self):
        r=self.analyze("Treatment with spermidine increased ATP-linked OCR compared with vehicle-treated cells.")
        self.assertEqual(r["candidates"],0)
        self.assertEqual(r["pairs"],0)

    def test_author_source_quote_unverified_is_hold(self):
        r=self.analyze("Treatment with spermidine increased ATP content compared with vehicle-treated cells.",indexed=False)
        self.assertEqual(r["source_hold"],1)
        self.assertEqual(r["candidates"],0)
        self.assertEqual(self.conn.execute("SELECT source_verified FROM r43_document_block").fetchone()[0],0)

    def test_chemical_salt_not_promoted_to_parent(self):
        r=self.analyze("Treatment with spermidine hydrochloride increased ATP content compared with vehicle-treated cells.")
        self.assertEqual(r["candidates"],0)
        s=self.conn.execute("""SELECT identity_state FROM r43_entity_mention
          WHERE kind='CHEMICAL_CANDIDATE'""").fetchone()[0]
        self.assertIn("SALT",s)

    def test_source_relation_can_be_cited_but_not_auto_gold(self):
        text="A 2 μM quercetin treatment decreased ATP content in injured cells compared with untreated cells."
        r=self.analyze(text,r42=[{"id":"r42:synthetic","molecule":"IK:QUERCETIN_SIMULATED_TEST_ONLY",
                                  "endpoint":"ATP_CONTENT","direction":"DECREASE",
                                  "value_literal":None}])
        self.assertEqual(r["candidates"],1)
        self.assertEqual(r["pairs"],0)
        reasons=json.loads(self.conn.execute("SELECT hold_reason FROM r43_candidate").fetchone()[0])
        self.assertIn("STRESS_OR_RESCUE_NOT_HEALTHY_BASELINE",reasons)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE r43_candidate SET training_approved=1")


if __name__ == "__main__":
    unittest.main()

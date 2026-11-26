"""Arm-local context for the fictional round-5 grammar. Not a real-author gold set."""
import hashlib
import json
import unittest
from pathlib import Path

from r031_evidence.r42_extract import Names, SENTENCE, coordinated_model_arms, load_contract, parse

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "source_context_round5_synthetic_1707.json"
FIXTURE_SHA = "4ff7af929d2fd0adaf1f59037240514bf79506da105f7a592e4ac902f39df477"
UNKNOWN_STATUS = {"UNKNOWN", "MISSING_NO_SAME_CLAUSE_SPAN", "AMBIGUOUS_CONTEXT"}


def article(text, names):
    body = "<sec sec-type=\"results\"><title>Results</title><p>" + text + "</p></sec>"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<article article-type="research-article"><front><article-meta>
<article-id pub-id-type="synthetic">SYNTHETIC:round5-context</article-id>
<title-group><article-title>SYNTHETIC FIXTURE not a real publication</article-title></title-group>
</article-meta></front><body>{body}</body></article>"""
    return parse(load_contract("small_molecule_atp"), xml.encode(), Names([(name, name) for name in names]))


def scope_of(evidence):
    scope = evidence.get("endpoint_scope")
    component = evidence.get("endpoint_component")
    if scope == "COMPONENT" and component == "MITOCHONDRIAL":
        return "MITOCHONDRIAL_COMPONENT"
    return scope


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.spec = json.loads(cls.raw)

    def test_fixture_is_the_supplied_synthetic_file(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), FIXTURE_SHA)
        self.assertTrue(self.spec["synthetic"])
        self.assertEqual(len(self.spec["cases"]), 4)

    def test_decimal_point_does_not_split_the_sentence(self):
        text = "Vellun increased total ATP production to 12.5 in dim-lit Luma cultures."
        self.assertEqual(SENTENCE.split(text), [text])
        rows, _passages, _detail = article(text, ["Vellun"])
        self.assertEqual(len(rows), 1)
        row = rows[0][1]
        self.assertEqual(row["evidence_json"]["source_context"], "dim-lit")
        self.assertEqual(row["cell"], "Luma cultures")
        self.assertIsNone(row["stimulus"])
        self.assertIsNone(row["value"])

    def test_each_arm_keeps_only_its_own_modifier(self):
        for case in self.spec["cases"]:
            with self.subTest(case=case["id"]):
                names = sorted({
                    arm["fields"]["molecule"]["value"]
                    for arm in case["expected_arms"]
                    if arm["fields"]["molecule"]["status"] == "supported"
                })
                rows, passages, _detail = article(case["text"], names)
                emitted = [row for _loc, row in rows]
                for arm in case["expected_arms"]:
                    fields = arm["fields"]
                    if not arm["molecule_specific_emission_allowed"]:
                        self.assertEqual(emitted, [])
                        self.assertIn("CONTEXT_FRAGMENT_MOLECULE_UNKNOWN", {item[-1] for item in passages})
                        parsed = coordinated_model_arms(case["text"])
                        context = fields["source_context"]
                        self.assertEqual(context["status"], "supported")
                        self.assertEqual(parsed[0]["modifier"], context["value"])
                        self.assertEqual(parsed[0]["modifier_span"], (context["anchors"][0]["start"], context["anchors"][0]["end"]))
                        self.assertIsNone(fields["stimulus_agent"]["value"])
                        continue
                    matches = [
                        row for row in emitted
                        if row["molecule"] == fields["molecule"]["value"]
                        and row["cell"] == fields["cell_or_model"]["value"]
                    ]
                    self.assertEqual(len(matches), 1, emitted)
                    row = matches[0]
                    self.assertEqual(row["direction"], fields["direction"]["value"].upper())
                    self.assertEqual(scope_of(row["evidence_json"]), fields["endpoint_scope"]["value"])
                    self.assertIsNone(row["stimulus"])
                    self.assertIsNone(row["dose"])
                    self.assertIsNone(row["exposure_time"])
                    self.assertIsNone(row["value"])
                    self.assertIsNone(row["unit"])
                    self.assertIsNone(row["comparator"])
                    self.assertEqual(row["evidence_json"]["natural_origin"], "UNKNOWN")
                    self.assertEqual(row["evidence_json"]["purity"], "UNKNOWN")
                    context = fields["source_context"]
                    if context["status"] == "supported":
                        self.assertEqual(row["evidence_json"]["source_context"], context["value"])
                        anchor = row["evidence_json"]["source_context_anchor"]
                        self.assertEqual(anchor["text"], context["anchors"][0]["text"])
                        self.assertEqual((anchor["start"], anchor["end"]), (context["anchors"][0]["start"], context["anchors"][0]["end"]))
                        self.assertNotEqual(row["stimulus"], context["value"])
                    else:
                        self.assertIsNone(row["evidence_json"]["source_context"])
                        self.assertIn(row["evidence_json"]["source_context_status"], UNKNOWN_STATUS)
                        self.assertNotIn("source_context_anchor", row["evidence_json"])


if __name__ == "__main__":
    unittest.main()

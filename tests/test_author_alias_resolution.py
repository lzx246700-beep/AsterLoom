"""Author-declared alias binding: positive, negative and formulation boundaries."""
import copy
import unittest

from r031_evidence import author_alias_resolution as alias
from r031_evidence import r44_arbiter
from r031_evidence.atp_content_relations import _names

PAPER = "PMC_ALIAS_TEST"
SHA = "a" * 64
TEXT = (
    "In SKOV3 cells, 2-deoxy-D-glucose (2-DG) was tested. "
    "2-DG (10 mM) decreased ATP levels by 50% compared with the control after 48 h."
)
DEFINITION = "2-deoxy-D-glucose (2-DG)"


def packet(text=TEXT, extra=None):
    chunks = [
        {
            "chunk_id": "r44:alias",
            "locator": "/article/body[1]/sec[1]/p[1]",
            "kind": "PARAGRAPH",
            "section": "Results",
            "text": text,
            "author_sha256": SHA,
        }
    ]
    if extra:
        chunks.extend(extra)
    return {
        "schema": "asterloom.r44.author-evidence-packet/1",
        "paper": PAPER,
        "paper_title": "Synthetic alias study",
        "source_sha256": SHA,
        "source_license_literal": "cc by",
        "selection_scope": "DEVELOPMENT_ONLY_NOT_HIDDEN",
        "context_truncated": False,
        "chunks": chunks,
    }


def answer(chemical="2-deoxy-D-glucose", chemical_quote="2-DG (10 mM) decreased ATP levels"):
    fields = {k: None for k in r44_arbiter.FIELDS}
    fields.update(
        chemical_identity=chemical,
        tested_form="2-deoxy-D-glucose",
        cell_model="SKOV3 cells",
        treatment="2-DG",
        comparator="the control",
        endpoint="ATP_CONTENT",
        direction="DECREASE",
        value="50",
        unit="%",
        dose="10 mM",
        exposure_time="48 h",
        interpretation="2-DG decreased ATP levels",
    )
    support = {
        "chemical_identity": chemical_quote,
        "tested_form": DEFINITION,
        "cell_model": "In SKOV3 cells",
        "treatment": "2-DG (10 mM)",
        "comparator": "compared with the control",
        "endpoint": "ATP levels",
        "direction": "decreased ATP levels",
        "value": "50%",
        "unit": "50%",
        "dose": "2-DG (10 mM)",
        "exposure_time": "after 48 h",
        "interpretation": "2-DG (10 mM) decreased ATP levels",
    }
    return {
        "paper": PAPER,
        "relations": [
            {
                "fields": fields,
                "evidence": [
                    {"field": k, "chunk_id": "r44:alias", "quote": v}
                    for k, v in support.items()
                ],
                "uncertainties": [],
            }
        ],
        "reason_if_none": None,
    }


class AliasDefinitions(unittest.TestCase):
    def test_parenthesized_alias_including_digit_leading_abbreviation(self):
        table = alias.definitions(packet())
        self.assertIn(("2-DG", "2-deoxy-D-glucose"),
                      {(e["abbreviation"], e["full_name"]) for e in table["entries"]})

    def test_caption_colon_definition(self):
        extra = [{
            "chunk_id": "r44:caption",
            "locator": "/article/floats-group[1]/fig[1]/caption[1]",
            "kind": "FIGURE_OR_TABLE_CAPTION",
            "section": "Results",
            "text": "ATP: adenosine triphosphate; MMP: mitochondrial membrane potential, SPD: spermidine.",
            "author_sha256": SHA,
        }]
        table = alias.definitions(packet(extra=extra))
        self.assertIn(("SPD", "spermidine"),
                      {(e["abbreviation"], e["full_name"]) for e in table["entries"]})
        # Assay/statistics terms and endpoint descriptions are not substances.
        self.assertNotIn("ATP", {e["abbreviation"] for e in table["entries"]})
        self.assertNotIn("MMP", {e["abbreviation"] for e in table["entries"]})

    def test_unrelated_or_measurement_definitions_are_ignored(self):
        extra = [{
            "chunk_id": "r44:odd",
            "locator": "/article/body[1]/sec[2]/p[9]",
            "kind": "PARAGRAPH",
            "section": "Methods",
            "text": "HK (hexokinase) and OD1 (was recorded after 30 s) were noted.",
            "author_sha256": SHA,
        }]
        table = alias.definitions(packet(text="HK (hexokinase) and OD1 (was recorded after 30 s) were noted."))
        self.assertEqual(table["entries"], [])


class AliasBinding(unittest.TestCase):
    def test_full_name_is_verified_against_author_abbreviation(self):
        reviewed = r44_arbiter.validate(packet(), answer())
        relation = reviewed["relations"][0]
        self.assertIn("AUTHOR_DEFINED_ALIAS_BOUND_REVIEW_REQUIRED", relation["risk_flags"])
        provenance = [e for e in relation["evidence"] if e["field"] == "chemical_identity"]
        self.assertEqual(provenance[0]["alias_definition"]["abbreviation"], "2-DG")
        self.assertEqual(provenance[0]["alias_definition"]["definition_quote"], DEFINITION)
        self.assertEqual(relation["state"], "FIELD_SOURCE_VERIFIED_REVIEW_ONLY")

    def test_undefined_synonym_is_rejected(self):
        data = answer(chemical="spermidine", chemical_quote="2-DG (10 mM) decreased ATP levels")
        with self.assertRaisesRegex(ValueError, "AUTHOR_LITERAL_NOT_IN_CITED_SPAN"):
            r44_arbiter.validate(packet(), data)

    def test_formulation_containing_abbreviation_is_not_the_free_compound(self):
        text = TEXT + " RCEVs@SPD increased ATP levels compared with the control."
        data = answer(chemical="spermidine", chemical_quote="RCEVs@SPD increased ATP levels")
        extra = [{
            "chunk_id": "r44:caption",
            "locator": "/article/floats-group[1]/fig[1]/caption[1]",
            "kind": "FIGURE_OR_TABLE_CAPTION",
            "section": "Results",
            "text": "SPD: spermidine.",
            "author_sha256": SHA,
        }]
        with self.assertRaisesRegex(ValueError, "AUTHOR_LITERAL_NOT_IN_CITED_SPAN"):
            r44_arbiter.validate(packet(text=text, extra=extra), data)

    def test_conflicting_definitions_are_held_not_chosen(self):
        extra = [{
            "chunk_id": "r44:caption",
            "locator": "/article/floats-group[1]/fig[1]/caption[1]",
            "kind": "FIGURE_OR_TABLE_CAPTION",
            "section": "Results",
            "text": "SPD: spermidine. In another panel, SPD: spermine.",
            "author_sha256": SHA,
        }]
        text = TEXT + " SPD (10 mM) also decreased ATP levels compared with the control."
        data = answer(chemical="spermidine", chemical_quote="SPD (10 mM) also decreased ATP levels")
        with self.assertRaisesRegex(ValueError, "AUTHOR_LITERAL_NOT_IN_CITED_SPAN"):
            r44_arbiter.validate(packet(text=text, extra=extra), data)

    def test_alias_equivalence_requires_an_explicit_definition(self):
        table = alias.definitions(packet())
        self.assertTrue(alias.equivalent("2-DG", "2-deoxy-D-glucose", table))
        self.assertFalse(alias.equivalent("2-DG", "spermidine", table))
        self.assertFalse(alias.equivalent("2-DG", "2-deoxy-D-glucose", None))


class DeterministicNameExpansion(unittest.TestCase):
    def block(self, text):
        return {"text": text, "path": "/article/body[1]/sec[2]/p[1]"}

    def test_digit_leading_parenthesized_acronym_is_expanded(self):
        names, evidence = _names(
            "2-deoxy-D-glucose",
            [self.block("2-deoxy-D-glucose (2-DG) was purchased.")],
            "SYNTHETIC",
        )
        self.assertIn("2-DG", names)
        self.assertEqual(evidence[0]["quote"], "2-deoxy-D-glucose (2-DG)")

    def test_reverse_caption_definition_is_expanded(self):
        names, _ = _names("spermidine", [self.block("SPD: spermidine.")], "SYNTHETIC")
        self.assertIn("SPD", names)

    def test_ordinary_prose_is_not_an_alias_definition(self):
        names, _ = _names(
            "spermidine", [self.block("control spermidine was added.")], "SYNTHETIC"
        )
        self.assertEqual(names, ["spermidine"])


if __name__ == "__main__":
    unittest.main()

"""A read-only *pre-admission* audit for evidence-first quantitative pairs.

The author-text pair extractor emits tier *hints*, not T1 Science Verify or
Auto Silver records.  This module identifies still-unmet scientific contracts.
It deliberately cannot promote data to A/B/C Silver: that operation belongs to
scientific_scope_store.normalize_candidate + auto_silver.classify with source-
checked intervention, scope, measured endpoint, paired arm and tested form.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

VALID_SHA = re.compile(r"^[0-9a-f]{64}$")


def audit_pair(pair: dict, *, author_xml: bytes | None = None) -> dict:
    """Preserve real author pair metadata and enumerate unresolved T1 gates."""
    tier = pair.get("tier_hint") or "HOLD"
    reasons = set(pair.get("reasons") or ())
    if tier not in ("A_CANDIDATE", "B_CANDIDATE", "HOLD"):
        reasons.add("UNKNOWN_SOURCE_PAIR_TIER")
    if not VALID_SHA.fullmatch(pair.get("source_sha") or ""):
        reasons.add("SOURCE_SHA_MISSING_OR_INVALID")
    treated, control = pair.get("treated") or {}, pair.get("control") or {}
    if not treated or not control:
        reasons.add("EXPERIMENT_ARM_MISSING")
    if treated.get("unit") != control.get("unit"):
        reasons.add("UNIT_MISMATCH")
    if not pair.get("stratum") and (treated.get("ordered_count") or 1) > 1:
        reasons.add("EXPERIMENT_STRATUM_UNRESOLVED")
    if treated.get("stratum") != control.get("stratum"):
        reasons.add("EXPERIMENT_STRATUM_CONFLICT")
    if tier == "B_CANDIDATE":
        reasons.add("RESCUE_COMPARATOR_REQUIRES_SCIENTIFIC_SCOPE")
    if tier == "HOLD":
        reasons.add("AUTHOR_PAIR_NOT_ESTABLISHED")

    ctx = pair.get("context") or {}
    assay = ctx.get("assay_method")
    norm = ctx.get("normalization")
    if not isinstance(assay, dict) or not assay.get("chunk_id") or not assay.get("locator"):
        reasons.add("ATP_ASSAY_METHOD_NOT_SOURCE_BOUND")
    if (not isinstance(norm, dict) or not norm.get("quote")
        or (pair.get("basis") == "SAME_TABLE_COLUMN"
            and not norm.get("chunk_id"))):
        reasons.add("ATP_NORMALIZATION_NOT_SOURCE_BOUND")
    if "NORMALIZATION_SUPERSCRIPT_LOST" in reasons or any(
        re.search(r"\b10[456]\s*(?:cells|sperm)\b", str(x or ""), flags=re.I)
        for x in (treated.get("unit"), control.get("unit"), (norm or {}).get("quote"))
    ):
        reasons.add("NORMALIZATION_SUPERSCRIPT_REQUIRES_CHECK")
    # Current JATS table extraction preserves the caption and source SHA but
    # does not yet bind a specific author table cell to a verified chunk/locator.
    if pair.get("basis") == "SAME_TABLE_COLUMN":
        from .quantitative_evidence import verify_author_table_cell
        # Both source cells must match the SAME locked original author file;
        # a table caption alone cannot justify a numeric measurement.
        if author_xml is None or not all(
            verify_author_table_cell(author_xml, arm.get("author_cell_ref"))
            for arm in (treated, control)
        ):
            reasons.add("TABLE_CELL_SOURCE_LOCATOR_REQUIRED")
    if tier == "A_CANDIDATE" and control.get("arm") != "CONTROL":
        reasons.add("CONTROL_NOT_VERIFIED_UNTREATED")
    if tier == "B_CANDIDATE" and control.get("arm") != "COMPARATOR":
        reasons.add("RESCUE_COMPARATOR_ROLE_UNCONFIRMED")

    # Critical data are not supplied by a numeric-only pair extractor.
    # They must come from author-grounded chemical/form/experiment context.
    if not pair.get("verified_chemical_identity"):
        reasons.add("CHEMICAL_IDENTITY_AND_NATURAL_ORIGIN_UNVERIFIED")
    if not pair.get("verified_tested_form"):
        reasons.add("TESTED_FORM_UNVERIFIED")
    if not pair.get("scientific_scope_verdict"):
        reasons.add("SCIENTIFIC_EXPERIMENT_SCOPE_NOT_VERIFIED")

    return dict(
        paper=pair.get("paper"), source_sha=pair.get("source_sha"),
        original_tier_hint=tier, pairing_basis=pair.get("basis"),
        source_treated_label=treated.get("arm_label"),
        source_control_label=control.get("arm_label"),
        stratum=pair.get("stratum"),
        unit=treated.get("unit"),
        status="SOURCE_CANDIDATE_ONLY",
        auto_silver_tier=None,
        training_approved=False,
        required_science_checks=sorted(reasons),
    )


def audit_population(cases: list[dict], sources: dict[str, bytes] | None = None) -> dict:
    """Input is flattened real author pairs; never converts to T1 records."""
    sources = sources or {}
    rows = [audit_pair(c, author_xml=sources.get(c.get("paper"))) for c in cases]
    tiers = Counter(r["original_tier_hint"] for r in rows)
    issues = Counter(s for r in rows for s in r["required_science_checks"])
    by_paper = defaultdict(Counter)
    for r in rows:
        by_paper[r["paper"]][r["original_tier_hint"]] += 1
    return dict(
        schema="asterloom.evidence-first-science-pregate/1",
        source_pairs=len(rows),
        source_tier_hints=dict(sorted(tiers.items())),
        by_paper={k: dict(sorted(v.items())) for k,v in sorted(by_paper.items())},
        unresolved_science_gates=dict(sorted(issues.items())),
        silver_promoted=0, independent_gold=0, training_approved=0,
        status="INTEGRATION_SCIENTIFIC_SCOPE_PENDING",
        notes="A_CANDIDATE/B_CANDIDATE are parser hints, NOT T1 Silver A/B. "
              "Only normalised source-bound model claims can enter the "
              "existing scientific_scope_store + auto_silver path.",
        rows=rows,
    )

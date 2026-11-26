"""Absolute-axis ATP panel digitization as a source-bound review sidecar.

Values emitted here are pixel estimates from author figures, never author-
reported means and never training labels. Method-arm and comparator matching
remain explicit HOLDs until independently reviewed.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .atp_content_plot import _colored_bars, fit_linear_ticks
from .atp_plot_pixels import pixel, read_image
from .atp_content_relations import verify_relations


def _tick_number(text: str) -> float | None:
    """Read OCR numbers with only surrounding nonnumeric artifacts."""
    value = re.fullmatch(r"\s*[₴$£€¥|]*\s*([0-9]+(?:\.[0-9]+)?)\s*[-–—|]*\s*", text)
    return float(value.group(1)) if value else None


def _tick_column(tokens: list[dict], width: int, height: int) -> list[dict]:
    candidates = []
    for i, token in enumerate(tokens):
        value = _tick_number(token.get("text", ""))
        if value is None:
            continue
        x, y, w, h = token["bbox"]
        candidates.append({"token_id": i, "text": token["text"], "value": value,
                           "center_x": (x + w / 2) * width,
                           "center_y": (y + h / 2) * height,
                           "bbox": token["bbox"]})
    # Group numbers that occupy one vertical tick-label column. Other numbers
    # elsewhere in a multi-panel crop must not join its calibration.
    best = []
    for anchor in candidates:
        cluster = [t for t in candidates
                   if abs(t["center_x"] - anchor["center_x"]) <= width * .075]
        distinct = {t["value"] for t in cluster}
        span = max((t["center_y"] for t in cluster), default=0) - min(
            (t["center_y"] for t in cluster), default=0)
        if len(distinct) >= 3 and span >= height * .25 and len(cluster) > len(best):
            best = cluster
    # Multiple equally plausible tick columns are ambiguous.
    clusters = []
    for anchor in candidates:
        c = [t for t in candidates if abs(t["center_x"] - anchor["center_x"]) <= width * .075]
        if len({t["value"] for t in c}) >= 3 and max(t["center_y"] for t in c) - min(t["center_y"] for t in c) >= height * .25:
            signature = tuple(sorted((round(t["center_x"]), t["value"]) for t in c))
            if signature not in [x[0] for x in clusters]:
                clusters.append((signature, c))
    if not best or len(clusters) != 1:
        raise ValueError("ATP_ABSOLUTE_AXIS_TICK_COLUMN_AMBIGUOUS")
    if len({t["value"] for t in best}) != len(best):
        raise ValueError("ATP_ABSOLUTE_AXIS_DUPLICATE_TICK_VALUES")
    return sorted(best, key=lambda t: t["center_y"])


def _is_dark(image: dict, x: int, y: int) -> bool:
    return max(pixel(image, x, y)) < 220


def _line_end_left(image: dict, axis_x: int, y: int) -> int:
    """Length of a horizontal tick stroke connected to the vertical axis."""
    length = 0
    for x in range(axis_x - 2, max(-1, axis_x - 70), -1):
        if not _is_dark(image, x, y):
            break
        length += 1
    return length


def _absolute_axis(image: dict, ticks: list[dict]) -> dict:
    width, height = image["width"], image["height"]
    min_y = round(min(t["center_y"] for t in ticks))
    max_y = round(max(t["center_y"] for t in ticks))
    tick_x = statistics_median([t["center_x"] for t in ticks])
    candidates = []
    for x in range(max(0, round(tick_x) + 8), min(width, round(tick_x + width * .14))):
        fraction = sum(_is_dark(image, x, y) for y in range(min_y, max_y + 1)) / (max_y - min_y + 1)
        candidates.append((fraction, x))
    score, axis_x = max(candidates, default=(0, 0))
    if score < .75:
        raise ValueError("ATP_ABSOLUTE_AXIS_VERTICAL_LINE_UNVERIFIED")
    calibrated = []
    for token in ticks:
        center = round(token["center_y"])
        rows = []
        for y in range(max(0, center - 35), min(height, center + 36)):
            run = _line_end_left(image, axis_x, y)
            if run >= 8:
                rows.append((run, y))
        if not rows:
            raise ValueError("ATP_ABSOLUTE_AXIS_TICK_STROKE_UNVERIFIED")
        longest = max(run for run, _ in rows)
        y_pixels = sorted(y for run, y in rows if run >= max(8, longest - 2))
        line_y = y_pixels[len(y_pixels) // 2]
        calibrated.append({"token_id": token["token_id"], "ocr_literal": token["text"],
                           "value": token["value"], "tick_line_y_pixel": line_y,
                           "connected_tick_stroke_px": longest, "bbox": token["bbox"]})
    if min(t["value"] for t in calibrated) != 0:
        raise ValueError("ATP_ABSOLUTE_AXIS_ZERO_TICK_REQUIRED")
    fit = fit_linear_ticks([{"value": t["value"], "y": t["tick_line_y_pixel"]} for t in calibrated])
    if not 0 <= fit["zero_y_pixel"] < height:
        raise ValueError("ATP_ABSOLUTE_AXIS_BASELINE_OUTSIDE_CROP")
    return {"axis_x_pixel": axis_x, "vertical_line_fraction": score,
            "ticks": calibrated, "scale": fit}


def _fill_kind(rgb: tuple[int, int, int]) -> bool:
    spread = max(rgb) - min(rgb)
    colored = spread >= 45 and max(rgb) >= 110
    neutral_filled = spread <= 30 and min(rgb) >= 100 and sum(255 - x for x in rgb) >= 25
    return colored or neutral_filled


def _localized_unit_tokens(tokens: list[dict], crop_rect: dict,
                           source_dimensions: tuple[int, int]) -> list[dict]:
    source_width, source_height = source_dimensions
    crop_left, crop_top = crop_rect["x"], crop_rect["y"]
    crop_right = crop_left + crop_rect["width"]
    crop_bottom = crop_top + crop_rect["height"]
    found = []
    for token_id, token in enumerate(tokens):
        text = token.get("text", "")
        if not re.search(r"ATP\s*\([^)]*(?:u|µ|μ)mol\s*/\s*l", text, re.I):
            continue
        bbox = token.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x, y, w, h = bbox
        left, right = x * source_width, (x + w) * source_width
        top = source_height - (y + h) * source_height
        bottom = source_height - y * source_height
        if right > crop_left and left < crop_right and bottom > crop_top and top < crop_bottom:
            found.append({"token_id": token_id, "text": text, "bbox": bbox,
                          "intersection_px_top_left_origin": [
                              max(left, crop_left), max(top, crop_top),
                              min(right, crop_right) - max(left, crop_left),
                              min(bottom, crop_bottom) - max(top, crop_top)]})
    return found


def _bars(image: dict, axis: dict, labels: list[dict]) -> list[dict]:
    width, height = image["width"], image["height"]
    scale = axis["scale"]
    baseline = round(scale["zero_y_pixel"])
    max_tick_y = max(t["tick_line_y_pixel"] for t in axis["ticks"])
    centers = sorted(l["center_x"] for l in labels)
    if len(centers) < 3:
        raise ValueError("ATP_ABSOLUTE_AXIS_MINIMUM_THREE_GROUP_LABELS_REQUIRED")
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    typical_gap = statistics_median(gaps)
    right = min(width - 1, round(centers[-1] + typical_gap * .55))
    left = axis["axis_x_pixel"] + 2
    if right <= left:
        raise ValueError("ATP_ABSOLUTE_AXIS_BAR_REGION_INVALID")
    baseline_rows = range(max(0, baseline - 2), min(height, baseline + 3))
    baseline_score = max(sum(_is_dark(image, x, y) for x in range(left, right)) / (right - left)
                         for y in baseline_rows)
    if baseline_score < .65:
        raise ValueError("ATP_ABSOLUTE_AXIS_ZERO_BASELINE_NOT_VERIFIED")
    # Colored bars use the established detector; gray control bars need a
    # separate neutral-fill detector because they have no chroma.
    colored = _colored_bars(image, [left, baseline, right, min(height, max_tick_y + 8)])
    colored = [b for b in colored if b["center_x"] > left + 12]
    gray_columns = []
    for x in range(left, right):
        count = sum(_fill_kind(pixel(image, x, y)) and
                    max(pixel(image, x, y)) - min(pixel(image, x, y)) <= 30
                    for y in range(baseline + 2, min(height, max_tick_y + 8)))
        if count >= 18:
            gray_columns.append(x)
    runs = []
    for x in gray_columns:
        if not runs or x > runs[-1][-1] + 1:
            runs.append([x])
        else:
            runs[-1].append(x)
    gray = []
    for run in runs:
        x0, x1 = run[0], run[-1]
        if x1 - x0 + 1 < max(8, round(width * .018)):
            continue
        inset = max(1, (x1 - x0 + 1) // 8)
        inside = range(x0 + inset, x1 + 1 - inset)
        filled_rows = []
        for y in range(baseline + 2, min(height, max_tick_y + 8)):
            fraction = sum(_fill_kind(pixel(image, x, y)) and
                           max(pixel(image, x, y)) - min(pixel(image, x, y)) <= 30
                           for x in inside) / max(1, len(inside))
            if fraction >= .55:
                filled_rows.append(y)
        contiguous = []
        for y in filled_rows:
            if not contiguous or y <= contiguous[-1] + 2:
                contiguous.append(y)
            else:
                break
        if len(contiguous) >= 12 and contiguous[0] <= baseline + 4:
            gray.append({"pixel_bbox": [x0, baseline + 2, x1 - x0 + 1, contiguous[-1] - baseline - 1],
                         "center_x": (x0 + x1) / 2, "top_y_pixel": contiguous[-1]})
    bars = sorted(colored + gray, key=lambda b: b["center_x"])
    if len(bars) != len(labels):
        raise ValueError("ATP_ABSOLUTE_AXIS_BAR_LABEL_COUNT_MISMATCH")
    if any(abs(bar["center_x"] - label["center_x"]) > typical_gap * .6
           for bar, label in zip(bars, sorted(labels, key=lambda x: x["center_x"]))):
        raise ValueError("ATP_ABSOLUTE_AXIS_GROUP_LABEL_BAR_ALIGNMENT_HOLD")
    return bars


def statistics_median(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("ATP_ABSOLUTE_AXIS_EMPTY_MEDIAN")
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def digitize_absolute_panel(crop_path, crop_receipt_path, figure_packet_path,
                            relations_packet_path, relations_packet_sha, candidate_id=None):
    crop_path = Path(crop_path).resolve()
    receipt_path = Path(crop_receipt_path).resolve()
    figure_path = Path(figure_packet_path).resolve()
    relations_path = Path(relations_packet_path).resolve()
    crop_sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    receipt = json.loads(receipt_path.read_text())
    figure = json.loads(figure_path.read_text())
    relations = json.loads(relations_path.read_text())
    verify_relations(relations, relations["source_path"])
    if crop_sha(relations_path) != relations_packet_sha:
        raise ValueError("ATP_ABSOLUTE_AXIS_RELATIONS_SHA_MISMATCH")
    if (receipt.get("schema") != "asterloom.figure-panel-crop-receipt/1" or
        receipt.get("crop_sha256") != crop_sha(crop_path) or
        receipt.get("source_sha256") != figure.get("image_sha256") or
        figure.get("source_sha256") != relations.get("source_sha256") or
        receipt.get("figure_id") != figure.get("figure_id") or
        not re.fullmatch(r"[A-Z][A-Z0-9]?", str(receipt.get("panel", ""))) or
        not (str(crop_path) == receipt.get("crop_path") or
             str(crop_path).endswith(str(receipt.get("crop_path", ""))))):
        raise ValueError("ATP_ABSOLUTE_AXIS_CROP_SOURCE_BINDING_HOLD")
    source_width, source_height = receipt.get("source_dimensions_px", [0, 0])
    crop_width, crop_height = receipt.get("crop_dimensions_px", [0, 0])
    rect = receipt.get("crop_rect_px_top_left_origin", {})
    if (rect.get("x", -1) < 0 or rect.get("y", -1) < 0 or
        rect.get("width") != crop_width or rect.get("height") != crop_height or
        rect.get("x", 0) + crop_width > source_width or
        rect.get("y", 0) + crop_height > source_height):
        raise ValueError("ATP_ABSOLUTE_AXIS_CROP_RECTANGLE_INVALID")
    candidates = [c for c in relations.get("candidates", [])
                  if c.get("panel") == receipt["panel"] and
                  (candidate_id is None or c.get("candidate_id") == candidate_id)]
    if len(candidates) != 1:
        raise ValueError("ATP_ABSOLUTE_AXIS_UNIQUE_PANEL_RELATION_REQUIRED")
    candidate = candidates[0]
    if (candidate.get("panel_label_evidence", {}).get("quote") != receipt["panel"] or
        not re.search(r"ATP", candidate.get("panel_endpoint_evidence", {}).get("quote", ""), re.I)):
        raise ValueError("ATP_ABSOLUTE_AXIS_PANEL_ENDPOINT_BINDING_HOLD")
    endpoint = str(candidate.get("endpoint_kind", ""))
    if "ATP_CONTENT" not in endpoint:
        raise ValueError("ATP_ABSOLUTE_AXIS_DIRECT_ATP_CONTENT_PARENT_REQUIRED")
    unit_evidence = _localized_unit_tokens(
        figure.get("index", {}).get("relevant_tokens", []), rect,
        tuple(receipt.get("source_dimensions_px", [0, 0])))
    if len(unit_evidence) != 1:
        raise ValueError("ATP_ABSOLUTE_AXIS_AUTHOR_UNIT_OCR_EVIDENCE_REQUIRED")
    unit_literal = unit_evidence[0]["text"]
    image = read_image(crop_path)
    swift = shutil.which("swift")
    if not swift:
        raise ValueError("ATP_ABSOLUTE_AXIS_VISION_BACKEND_REQUIRED")
    script = Path(__file__).parent / "data/atp_figure_ocr.swift"
    run = subprocess.run([swift, str(script), str(crop_path)], capture_output=True,
                         text=True, timeout=45, check=True)
    ocr = json.loads(run.stdout)
    if (ocr.get("width"), ocr.get("height")) != (image["width"], image["height"]):
        raise ValueError("ATP_ABSOLUTE_AXIS_OCR_PIXEL_DIMENSIONS_MISMATCH")
    ticks = _tick_column(ocr["tokens"], image["width"], image["height"])
    axis = _absolute_axis(image, ticks)
    baseline = round(axis["scale"]["zero_y_pixel"])
    labels = []
    for i, token in enumerate(ocr["tokens"]):
        text = token.get("text", "").strip().rstrip("'’\"”")
        if not re.fullmatch(r"(?:Control|Model|[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+)", text, re.I):
            continue
        x, y, w, h = token["bbox"]
        cx, cy = (x + w / 2) * image["width"], (y + h / 2) * image["height"]
        if axis["axis_x_pixel"] + 8 < cx < image["width"] and baseline - image["height"] * .25 < cy < baseline - 8:
            labels.append({"token_id": i, "literal": token["text"], "normalized_label": text,
                           "center_x": cx, "bbox": token["bbox"]})
    labels.sort(key=lambda x: x["center_x"])
    if len({x["normalized_label"].casefold() for x in labels}) != len(labels):
        raise ValueError("ATP_ABSOLUTE_AXIS_DUPLICATE_GROUP_LABEL_HOLD")
    bars = _bars(image, axis, labels)
    slope = axis["scale"]["slope_value_per_pixel"]
    # Conservative pixel-only interval; it is not biological SD/SEM.
    halfwidth = (3 + axis["scale"]["max_tick_residual_px"]) * slope
    estimates = []
    for label, bar in zip(labels, bars):
        value = bar["top_y_pixel"] * slope + axis["scale"]["intercept"]
        if value <= 0:
            raise ValueError("ATP_ABSOLUTE_AXIS_NONPOSITIVE_BAR_ESTIMATE")
        estimates.append({"author_visible_group_label": label["normalized_label"],
                          "group_label_ocr_literal": label["literal"],
                          "group_label_token_id": label["token_id"],
                          "bar_pixel_bbox": bar["pixel_bbox"],
                          "estimated_value": round(value, 1),
                          "estimated_value_halfwidth": round(halfwidth, 1),
                          "unit_ocr_literal": unit_literal,
                          "numeric_origin": "GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE",
                          "uncertainty_kind": "PIXEL_CALIBRATION_BOUND_NOT_BIOLOGICAL_SD_OR_SEM",
                          "dose_binding": "HOLD_METHOD_ARM_TO_PANEL_NOT_INDEPENDENTLY_REVIEWED",
                          "comparator_binding": "HOLD_REVIEW_AUTHOR_GLYPH_KEY_BEFORE_NORMALIZATION"})
    return {"schema": "asterloom.atp-content.absolute-graph-estimates/1",
            "paper_id": receipt.get("paper_id"), "figure_id": receipt["figure_id"],
            "panel": receipt["panel"], "candidate_id": candidate["candidate_id"],
            "source_sha256": relations["source_sha256"],
            "author_figure_sha256": receipt["source_sha256"],
            "panel_crop_sha256": receipt["crop_sha256"],
            "panel_crop_rect_px_top_left_origin": receipt["crop_rect_px_top_left_origin"],
            "relations_packet_sha256": relations_packet_sha,
            "figure_packet_sha256": crop_sha(figure_path),
            "crop_receipt_sha256": crop_sha(receipt_path),
            "vision_backend": ocr.get("backend", "Apple Vision"),
            "image_dimensions_px": [image["width"], image["height"]],
            "axis_calibration": {"unit_literal_from_source_figure_ocr": unit_evidence,
                                 "ticks": axis["ticks"], "scale": axis["scale"],
                                 "vertical_axis_x_pixel": axis["axis_x_pixel"],
                                 "vertical_axis_line_fraction": axis["vertical_line_fraction"]},
            "group_estimates": estimates,
            "holds": ["GRAPH_ESTIMATES_ARE_NOT_AUTHOR_REPORTED_NUMERIC_VALUES",
                      "OCR_TICK_NUMBERS_REQUIRE_INDEPENDENT_REVIEW",
                      "EXPERIMENT_ARM_DOSE_BINDING_REQUIRES_INDEPENDENT_REVIEW",
                      "COMPARATOR_NORMALIZATION_AND_SAMPLE_SUMMARY_NOT_RESOLVED",
                      "INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED"],
            "database_write": False, "review_tier": "Review-B", "training_approved": False,
            "status": "ABSOLUTE_AXIS_GROUP_ESTIMATES_REVIEW_ONLY"}

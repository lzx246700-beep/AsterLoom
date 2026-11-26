import binascii
import struct
import zlib

from r031_evidence.atp_content_absolute_plot import (
    _absolute_axis, _bars, _localized_unit_tokens, _tick_column, _tick_number,
)
from r031_evidence.atp_plot_pixels import read_png


def _png(width, height, pixels):
    raw = bytearray()
    for y_top in range(height):
        raw.append(0)
        y = height - 1 - y_top
        for x in range(width):
            raw.extend(pixels.get((x, y), (255, 255, 255)))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def test_tick_ocr_normalization_only_accepts_one_numeric_token():
    assert _tick_number("₴ 20-") == 20.0
    assert _tick_number("40-") == 40.0
    assert _tick_number("ATP 20 and 40") is None
    assert _tick_number("p<0.001") is None


def test_source_figure_unit_token_must_intersect_the_receipted_crop():
    tokens = [
        {"text": "ATP (umol/L)", "bbox": [.30, .60, .10, .10]},
        {"text": "ATP (umol/L)", "bbox": [.05, .60, .10, .10]},
    ]
    found = _localized_unit_tokens(tokens, {"x": 250, "y": 250, "width": 200, "height": 200}, (1000, 1000))
    assert [x["token_id"] for x in found] == [0]


def test_absolute_axis_uses_tick_strokes_and_detects_gray_plus_colored_arms(tmp_path):
    width, height = 400, 300
    pixels = {}

    def line(x0, y0, x1, y1, color=(0, 0, 0)):
        if x0 == x1:
            for y in range(min(y0, y1), max(y0, y1) + 1):
                for x in range(x0 - 1, x0 + 2):
                    pixels[(x, y)] = color
        elif y0 == y1:
            for x in range(min(x0, x1), max(x0, x1) + 1):
                for y in range(y0 - 1, y0 + 2):
                    pixels[(x, y)] = color

    axis_x = 80
    tick_y = {0: 50, 20: 100, 40: 150, 60: 200}
    line(axis_x, 50, axis_x, 200)
    line(axis_x, 50, 357, 50)
    for y in tick_y.values():
        line(axis_x - 16, y, axis_x, y)
    ocr = []
    for value, y in tick_y.items():
        ocr.append({"text": str(value), "bbox": [.08, (y - 3) / height, .065, 6 / height]})

    group_centers = [120, 170, 220, 270, 320]
    labels = ["Control", "Model", "PA-L", "PA-M", "PA-H"]
    bars = [(100, 0, (205, 205, 205)), (150, 0, (240, 110, 90)),
            (200, 0, (0, 160, 200)), (250, 0, (0, 110, 180)),
            (300, 0, (0, 75, 120))]
    for (x0, _, color), top in zip(bars, [70, 110, 130, 145, 175]):
        for x in range(x0, x0 + 20):
            for y in range(52, top):
                pixels[(x, y)] = color
    for center, label in zip(group_centers, labels):
        ocr.append({"text": label, "bbox": [(center - 20) / width, 12 / height, 40 / width, 16 / height]})

    image = read_png(_png(width, height, pixels))
    ticks = _tick_column(ocr, width, height)
    axis = _absolute_axis(image, ticks)
    assert [t["value"] for t in axis["ticks"]] == [0, 20, 40, 60]
    assert axis["scale"]["max_tick_residual_px"] <= 0.01

    label_rows = []
    for token_id, label in enumerate(labels, start=4):
        label_rows.append({"token_id": token_id, "literal": label, "normalized_label": label,
                           "center_x": group_centers[token_id - 4], "bbox": ocr[token_id]["bbox"]})
    detected = _bars(image, axis, label_rows)
    assert len(detected) == 5
    assert detected[0]["center_x"] == 109.5  # neutral gray control is retained
    assert detected[-1]["center_x"] == 309.5


"""Optional OpenCV/numpy pixel detectors for AST-V21-FIGURE-Y.

Only used to (re)derive the pixel coordinates frozen in the figure manifest.
Replay of records never imports this module, so it needs no OpenCV.
All functions are deterministic for a given image and recipe.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - stdlib fallback used when cv2/numpy absent
    cv2 = None  # type: ignore
    np = None  # type: ignore


def load_gray_stdlib(path: str | Path) -> list[list[int]]:
    """Decode PNG or JPEG (via macOS /usr/bin/sips + stdlib zlib) into a 2D grayscale matrix [H][W]."""
    import struct
    import zlib

    p = Path(path)
    raw = p.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        sips = shutil.which("sips") or "/usr/bin/sips"
        with tempfile.TemporaryDirectory() as td:
            out_png = Path(td) / "decoded.png"
            subprocess.run([sips, "-s", "format", "png", str(p), "--out", str(out_png)], check=True, capture_output=True)
            raw = out_png.read_bytes()
    pos = 8
    data = bytearray()
    header = None
    while pos + 12 <= len(raw):
        n = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            data.extend(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if not header:
        raise ValueError("FIGURE_Y_INVALID_PNG_HEADER")
    w, h, depth, color, _, _, _ = header
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or bpp is None:
        raise ValueError(f"FIGURE_Y_UNSUPPORTED_PNG_COLOR:{color}")
    stride = w * bpp
    stream = zlib.decompress(bytes(data))
    gray: list[list[int]] = []
    previous = bytearray(stride)
    for y in range(h):
        start = y * (stride + 1)
        mode = stream[start]
        row = bytearray(stream[start + 1:start + 1 + stride])
        if mode:
            for x in range(stride):
                a = row[x - bpp] if x >= bpp else 0
                b = previous[x]
                c = previous[x - bpp] if x >= bpp else 0
                if mode == 1:
                    pred = a
                elif mode == 2:
                    pred = b
                elif mode == 3:
                    pred = (a + b) // 2
                else:
                    pr = a + b - c
                    pa, pb, pc = abs(pr - a), abs(pr - b), abs(pr - c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pred) & 255
        previous = row
        if bpp in (1, 2):
            gray.append([int(row[x * bpp]) for x in range(w)])
        else:
            gray.append([
                (299 * row[x * bpp] + 587 * row[x * bpp + 1] + 114 * row[x * bpp + 2]) // 1000
                for x in range(w)
            ])
    return gray


def runs(mask1d):
    out, start = [], None
    for i, v in enumerate(mask1d):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask1d) - 1))
    return out


def load(path):
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("FIGURE_Y_IMAGE_UNREADABLE")
    return bgr, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- axes
def y_axis(gray, roi, dark=110):
    x0, y0, x1, y1 = roi
    sub = gray[y0:y1, x0:x1] < dark
    best = (0, None, None)
    for x in range(sub.shape[1]):
        for a, b in runs(sub[:, x]):
            if b - a > best[0]:
                best = (b - a, x, (a, b))
    _, x, (a, b) = best
    cols = [c for c in range(max(0, x - 8), min(sub.shape[1], x + 9))
            if sub[a + (b - a) // 2, c] and sub[a + 3:b - 3, c].mean() > 0.9]
    return {"x_left": x0 + min(cols), "x_right": x0 + max(cols), "y_top": y0 + a, "y_bottom": y0 + b}


def y_ticks(gray, ax, dark=110, reach=(1, 5), frac=0.75):
    xl = ax["x_left"]
    band = gray[ax["y_top"] - 3:ax["y_bottom"] + 4, xl - reach[1]:xl - reach[0]] < dark
    rows = band.mean(axis=1) >= frac
    return [ax["y_top"] - 3 + (a + b) / 2 for a, b in runs(rows)]


def x_axis(gray, roi, dark=110):
    x0, y0, x1, y1 = roi
    sub = gray[y0:y1, x0:x1] < dark
    best = (0, None, None)
    for y in range(sub.shape[0]):
        for a, b in runs(sub[y, :]):
            if b - a > best[0]:
                best = (b - a, y, (a, b))
    _, y, (a, b) = best
    rows = [r for r in range(max(0, y - 8), min(sub.shape[0], y + 9)) if sub[r, a + 3:b - 3].mean() > 0.9]
    return {"y_top": y0 + min(rows), "y_bottom": y0 + max(rows), "x_left": x0 + a, "x_right": x0 + b}


def x_ticks(gray, ax, dark=110, reach=(1, 5), frac=0.75):
    yb = ax["y_bottom"]
    band = gray[yb + reach[0]:yb + reach[1], ax["x_left"] - 3:ax["x_right"] + 4] < dark
    cols = band.mean(axis=0) >= frac
    return [ax["x_left"] - 3 + (a + b) / 2 for a, b in runs(cols)]


# ---------------------------------------------------------------- bars
def ink_mask(bgr, gray, mode, thr):
    if mode == "color":
        return bgr.min(axis=2) < thr
    return gray < thr


def bar_spans(ink, x_from, x_to, row):
    return [(a + x_from, b + x_from) for a, b in runs(ink[int(row), x_from:x_to])]


def _run_up(ink, x, base):
    y = base
    while y > 0 and ink[y, x]:
        y -= 1
    return y + 1


def measure_bar(ink, gray, xl, xr, base, dark=110, open_edge=None, max_outline=6, default_half=1.0, fixed_half=None):
    """Top-line centre of one bar plus the whisker top above its centre."""
    if open_edge:
        cols = [open_edge[0], open_edge[1]]
    else:
        w = xr - xl
        cols = [xl + max(1, int(round(w * f))) for f in (0.15, 0.85)]
    top = min(_run_up(ink, c, base) for c in cols)
    y = top
    while ink[y, xl:xr + 1].mean() < 0.8 and y < base:
        y += 1
    top = y
    if fixed_half is not None:
        half = float(fixed_half)
    elif open_edge:
        half = (open_edge[2]) / 2.0
    else:
        probe = xl + max(1, int(round((xr - xl) * 0.25)))
        th = 0
        while top + th < base and gray[top + th, probe] < dark and th <= max_outline:
            th += 1
        half = th / 2.0 if 0 < th <= max_outline else default_half
    xc = (xl + xr) // 2
    wt = whisker_top(ink, xc, top - 2)
    return {"y_px": top + half, "y_outer_px": top, "err_top_px": wt, "x_left": int(xl), "x_right": int(xr)}


def whisker_top(ink, xc, y_from, maxup=600, halfw=2, maxgap=3):
    y, last, gap = int(y_from), None, 0
    while y > 0 and y_from - y < maxup:
        if ink[y, xc - halfw:xc + halfw + 1].any():
            last, gap = y, 0
        else:
            gap += 1
            if gap > maxgap:
                break
        y -= 1
    return last


def detect_bars(bgr, gray, r):
    roi = r["roi"]
    dark = r.get("dark", 110)
    ax = y_axis(gray, roi, dark)
    ticks = y_ticks(gray, ax, dark, tuple(r.get("tick_reach", (1, 5))), r.get("tick_frac", 0.75))
    xroi = (ax["x_left"], max(roi[1], ax["y_bottom"] - r.get("baseline_search", 15)), roi[2], min(roi[3], ax["y_bottom"] + 12))
    xa = x_axis(gray, xroi, dark)
    base = xa["y_top"] - 1
    ink = ink_mask(bgr, gray, r.get("ink", "gray"), r.get("ink_thr", 200))
    spans = bar_spans(ink, ax["x_right"] + 3, xa["x_right"] - r.get("right_margin", 0), xa["y_top"] - r.get("probe", 6))
    spans = [s for s in spans if s[1] - s[0] + 1 >= r.get("min_span", 2)]
    marks = []
    fh = r.get("outline_half_px")
    if r.get("bar_ranges"):
        ew = r.get("edge_width", 2)
        for xl, xr in r["bar_ranges"]:
            marks.append(measure_bar(ink, gray, xl, xr, base, dark, open_edge=(xl + ew // 2, xr - ew // 2, ew), fixed_half=fh))
    elif r.get("style") == "open":
        if len(spans) % 2:
            raise ValueError("FIGURE_Y_OPEN_BAR_EDGE_PAIRING")
        for i in range(0, len(spans), 2):
            (a1, b1), (a2, b2) = spans[i], spans[i + 1]
            marks.append(measure_bar(ink, gray, a1, b2, base, dark, open_edge=((a1 + b1) // 2, (a2 + b2) // 2, b1 - a1 + 1), fixed_half=fh))
    else:
        spans = [s for s in spans if s[1] - s[0] + 1 >= r.get("min_bar_width", 8)]
        windows = r.get("top_windows") or [None] * len(spans)
        if len(windows) != len(spans):
            raise ValueError("FIGURE_Y_TOP_WINDOWS_COUNT")
        for (a, b), win in zip(spans, windows):
            if r.get("top") == "full_row":
                marks.append(measure_bar_full_row(gray, a, b, base, r.get("row_dark", 60), r.get("row_frac", 0.95), r.get("row_inset", 3),
                                                  y_min=ax["y_top"], window=win))
            else:
                marks.append(measure_bar(ink, gray, a, b, base, dark, fixed_half=fh))
    return {"y_ticks_px": ticks, "axis": ax, "baseline": xa, "marks": marks}


def measure_bar_full_row(gray, xl, xr, base, row_dark=60, row_frac=0.95, inset=3, y_min=0, window=None):
    """Bar top = centre of the topmost run of rows that are dark across the whole bar width.

    For bars overlaid with scatter dots (dots never span the full width), the run-up
    from the base can stop on a dot; the bar's top outline is the topmost row whose
    dark fraction over [xl+inset, xr-inset] is >= row_frac.
    """
    def frac_at(yy):
        return (gray[yy, xl + inset:xr - inset + 1] < row_dark).mean()

    if window is not None:
        # visually seeded search window (top outline partly hidden by scatter dots):
        # rows within the window whose dark fraction is >= 90 % of the window maximum
        lo, hi = int(window[0]), int(window[1])
        fr = [frac_at(yy) for yy in range(lo, hi + 1)]
        mx = max(fr)
        rows = [lo + i for i, f in enumerate(fr) if f >= 0.9 * mx]
        top, bot = rows[0], rows[0]
        while bot + 1 in rows:
            bot += 1
        return {"y_px": (top + bot) / 2.0, "y_outer_px": top, "err_top_px": None, "x_left": int(xl), "x_right": int(xr),
                "top_rule": "window_max_dark_row", "window": [lo, hi], "row_frac_max": round(float(mx), 3)}
    best = None
    y = base
    while y >= max(1, int(y_min)):
        frac = frac_at(y)
        if frac >= row_frac:
            best = y
        y -= 1
    if best is None:
        raise ValueError("FIGURE_Y_BAR_TOP_ROW_NOT_FOUND")
    # ``best`` is the topmost dark row of the outline run; find the run's lower edge
    bottom = best
    while bottom + 1 < base and (gray[bottom + 1, xl + inset:xr - inset + 1] < row_dark).mean() >= row_frac:
        bottom += 1
    return {"y_px": (best + bottom) / 2.0, "y_outer_px": best, "err_top_px": None, "x_left": int(xl), "x_right": int(xr),
            "top_rule": "full_row"}


def _row_dark_fraction(gray, y: int, x_start: int, x_end_inclusive: int, row_dark: int) -> float:
    if np is not None and isinstance(gray, np.ndarray):
        return float((gray[y, x_start:x_end_inclusive + 1] < row_dark).mean())
    row = gray[y][x_start:x_end_inclusive + 1]
    if not row:
        return 0.0
    return sum(1 for v in row if v < row_dark) / float(len(row))


def measure_bar_first_full_row_upward(gray, xl, xr, base, row_dark=145, row_frac=0.60, inset=1, y_min=0, baseline_skip=3):
    """Bar top = first full-width dark horizontal run encountered scanning upward from baseline.

    Works with both numpy 2D arrays and pure-stdlib ``list[list[int]]`` grayscale matrices.
    Unlike ``measure_bar_full_row`` (which keeps overwriting ``best`` up to ``y_min`` and can
    latch onto significance brackets above scatter-overlaid bars), this stops at the first
    horizontal bar-top border above ``base - baseline_skip``.
    """
    y = int(base) - int(baseline_skip)
    lo = max(1, int(y_min))
    x0 = int(xl) + int(inset)
    x1 = int(xr) - int(inset)
    while y >= lo:
        if _row_dark_fraction(gray, y, x0, x1, row_dark) >= row_frac:
            bottom = y
            while y - 1 >= lo and _row_dark_fraction(gray, y - 1, x0, x1, row_dark) >= row_frac:
                y -= 1
            return {
                "y_px": (y + bottom) / 2.0,
                "y_outer_px": y,
                "err_top_px": None,
                "x_left": int(xl),
                "x_right": int(xr),
                "top_rule": "first_full_row_upward",
            }
        y -= 1
    raise ValueError("FIGURE_Y_BAR_TOP_ROW_NOT_FOUND")


# -------------------------------------------------------------- points
def refine_filled(gray, x, y, r=3, win=4, dark=110):
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = (xx ** 2 + yy ** 2) <= r * r
    best = None
    for dy in range(-win, win + 1):
        for dx in range(-win, win + 1):
            cx, cy = int(round(x)) + dx, int(round(y)) + dy
            patch = gray[cy - r:cy + r + 1, cx - r:cx + r + 1]
            if patch.shape != disk.shape:
                continue
            key = ((patch[disk] < dark).mean(), -(dx * dx + dy * dy))
            if best is None or key > best[0]:
                best = (key, cx, cy)
    _, cx, cy = best
    patch = gray[cy - r - 1:cy + r + 2, cx - r - 1:cx + r + 2] < dark
    ys, xs = np.nonzero(patch)
    return float(cx - r - 1 + xs.mean()), float(cy - r - 1 + ys.mean())


def refine_hollow(gray, x, y, win=7, dark=128):
    x, y = int(round(x)), int(round(y))
    sub = (gray[y - win:y + win + 1, x - win:x + win + 1] >= dark).astype(np.uint8)
    n, _, st, cen = cv2.connectedComponentsWithStats(sub, 4)
    best = None
    for i in range(1, n):
        bx, by, w, h, a = st[i]
        if bx == 0 or by == 0 or bx + w >= sub.shape[1] or by + h >= sub.shape[0]:
            continue
        d = (cen[i][0] - win) ** 2 + (cen[i][1] - win) ** 2
        if best is None or (a, -d) > best[0]:
            best = ((a, -d), cen[i])
    if best is None:
        raise ValueError("FIGURE_Y_HOLLOW_MARKER_NOT_FOUND")
    return float(x - win + best[1][0]), float(y - win + best[1][1])


def vertical_extent(gray, x, y, skip, dark, maxlen=300, halfw=1, maxgap=2):
    xi = int(round(x))

    def go(step):
        yy, last, gap = int(round(y)) + step * skip, None, 0
        while 0 < yy < gray.shape[0] - 1 and abs(yy - y) < maxlen:
            if (gray[yy, xi - halfw:xi + halfw + 1] < dark).any():
                last, gap = yy, 0
            else:
                gap += 1
                if gap > maxgap:
                    break
            yy += step
        return last
    return go(-1), go(1)


def detect_points(bgr, gray, r):
    roi = r["roi"]
    dark = r.get("dark", 110)
    ax = y_axis(gray, roi, dark)
    ticks = y_ticks(gray, ax, dark, tuple(r.get("tick_reach", (1, 5))), r.get("tick_frac", 0.75))
    out = {"y_ticks_px": ticks, "axis": ax, "marks": []}
    if r.get("x_axis"):
        xa = x_axis(gray, roi, dark)
        out["x_ticks_px"] = x_ticks(gray, xa, dark, tuple(r.get("x_tick_reach", (1, 5))))
        out["baseline"] = xa
    for sx, sy in r["seeds"]:
        if r.get("marker") == "hollow":
            cx, cy = refine_hollow(gray, sx, sy, dark=dark)
            top, _ = vertical_extent(gray, cx, cy, r.get("ring_skip", 4), dark)
        else:
            cx, cy = refine_filled(gray, sx, sy, dark=dark)
            top, _ = vertical_extent(gray, cx, cy, r.get("ring_skip", 4), dark)
        out["marks"].append({"x_px": cx, "y_px": cy, "err_top_px": top})
    return out


# -------------------------------------------------------- pdf vectors
def pdf_vector_bars(pdf_path, page=1, colors=None):
    """Exact bar tops from a vector PDF page (pdftocairo -svg). Returns axis-space values."""
    svg = subprocess.run(["pdftocairo", "-svg", "-f", str(page), "-l", str(page), str(pdf_path), "-"],
                         capture_output=True, check=True, timeout=60).stdout.decode("utf-8", "replace")
    tr = re.search(r'transform="matrix\(([^)]*)\)"', svg)
    a, b, c, d, e, f = [float(t) for t in tr.group(1).replace(",", " ").split()]
    ticks = []
    for m in re.finditer(r'd="M ([\d.]+) ([\d.]+) L ([\d.]+) ([\d.]+) "', svg):
        x1, y1, x2, y2 = map(float, m.groups())
        if abs(y1 - y2) < 1e-6 and 0 < abs(x2 - x1) < 6:
            ticks.append(y1)
    ticks = sorted(set(round(t, 4) for t in ticks))
    bars = []
    for m in re.finditer(r'fill="rgb\(([^)]*)\)" fill-opacity="1" d="M ([\d.]+) ([\d.]+) L ([\d.]+) [\d.]+ L [\d.]+ ([\d.]+)', svg):
        col, x0, ytop, x1, ybot = m.groups()
        if colors and col not in colors:
            continue
        x0, ytop, x1, ybot = map(float, (x0, ytop, x1, ybot))
        bars.append({"color": col, "x_page": (x0 + x1) / 2, "top_local": (f - ytop) / d * -1 if d < 0 else (ytop - f) / d,
                     "bottom_page": ybot})
    errs = []
    for m in re.finditer(r'd="M ([\d.]+) ([\d.]+) L ([\d.]+) ([\d.]+) M [\d.]+ ([\d.]+) L', svg):
        lx, y1, _, y2, cap = map(float, m.groups())
        errs.append({"x_page": a * lx + e, "cap_local": max(y1, y2)})
    return {"ticks_local": ticks, "bars": bars, "errs": errs, "matrix": [a, b, c, d, e, f]}


def detect_hlines(bgr, gray, r):
    """Horizontal marker lines (box-plot medians, mean lines) refined from visual seeds.

    For each seed (x, y) the rows within +-win whose ink fraction over [x-hw, x+hw]
    is >= frac are grouped into runs; the run closest to the seed gives y_px (centre).
    """
    roi = r["roi"]
    dark = r.get("dark", 110)
    ax = y_axis(gray, roi, dark)
    ticks = y_ticks(gray, ax, dark, tuple(r.get("tick_reach", (1, 5))), r.get("tick_frac", 0.75))
    ink = ink_mask(bgr, gray, r.get("ink", "color"), r.get("ink_thr", 215))
    hw, win, frac = r.get("half_width", 20), r.get("win", 8), r.get("frac", 0.9)
    marks = []
    for sx, sy in r["seeds"]:
        xi, yi = int(round(sx)), int(round(sy))
        rows = [ink[y, xi - hw:xi + hw + 1].mean() >= frac for y in range(yi - win, yi + win + 1)]
        cand = [(yi - win + a, yi - win + b) for a, b in runs(rows)]
        if not cand:
            raise ValueError("FIGURE_Y_HLINE_NOT_FOUND")
        a, b = min(cand, key=lambda ab: abs((ab[0] + ab[1]) / 2.0 - sy))
        marks.append({"x_px": float(xi), "y_px": (a + b) / 2.0, "err_top_px": None, "line_rows": [int(a), int(b)]})
    return {"y_ticks_px": ticks, "axis": ax, "marks": marks}


def detect(image_path, recipe):
    bgr, gray = load(image_path)
    kind = recipe["kind"]
    if kind == "bars":
        return detect_bars(bgr, gray, recipe)
    if kind == "points":
        return detect_points(bgr, gray, recipe)
    if kind == "hlines":
        return detect_hlines(bgr, gray, recipe)
    raise ValueError("FIGURE_Y_UNKNOWN_RECIPE")

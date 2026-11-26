"""AsterLoom Studio (星织工作台) — 4-Column Scientific Data Mining Desktop GUI & Local Daemon.

Provides an Antigravity / Codex-inspired 4-column scientific IDE interface:
  1. Left Sidebar       : Workspace & Pluggable Demand Projects (ML-Dataset / Lit-Review / Idea-Discovery)
  2. Center-Top Canvas  : Pluggable Demand Reverse-Compiler Designer (Demand -> 3-Stage Funnel Contract)
  3. Center-Bottom      : 3-Stage Funnel Live Execution Console & Mode-Specific Deliverable Views
  4. Right Inspector    : 1:1 Y-Pixel Ruler (`*_ruler.png`) OLS Visual Auditor & 12-Item Expert Gold Benchmark
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from r031_evidence.demand_compiler import (
    ScientificDemandSpec,
    compile_demand_to_funnel,
    get_builtin_demand_presets,
)
from r031_evidence.platform_core import (
    build_multimodal_benchmark_sheet,
    execute_compiled_funnel,
    get_platform_audit_status,
    score_multimodal_benchmark,
    screen_molecule_dual_track,
)
from r031_evidence.task_bundle import (
    _draw_text,
    encode_rgb_png,
    render_y_pixel_ruler_png,
)


def _generate_synthetic_bar_chart_png(
    *,
    width: int = 320,
    height: int = 280,
    ticks_px: list[int],
    tick_values: list[int],
    control_y_px: int,
    treated_y_px: int,
    bar_color: tuple[int, int, int] = (46, 160, 128),
    is_trap: bool = False,
) -> bytes:
    """Create a clean synthetic scientific bar chart PNG so `render_y_pixel_ruler_png` can overlay 1:1 Y-pixel rulers."""
    bg = (255, 255, 255)
    canvas: list[bytearray] = [bytearray(bytes(bg) * width) for _ in range(height)]

    def _fill_rect(x0: int, y0: int, x1: int, y1: int, col: tuple[int, int, int]) -> None:
        r, g, b = col
        for yy in range(max(0, y0), min(height, y1)):
            row = canvas[yy]
            for xx in range(max(0, x0), min(width, x1)):
                off = xx * 3
                row[off] = r
                row[off + 1] = g
                row[off + 2] = b

    axis_x = 64
    bottom_y = max(ticks_px)
    top_y = min(ticks_px)

    # Draw Y axis and X baseline
    _fill_rect(axis_x, top_y - 10, axis_x + 2, bottom_y + 2, (40, 45, 55))
    _fill_rect(axis_x, bottom_y, width - 24, bottom_y + 2, (40, 45, 55))

    # Draw Y axis ticks and numeric labels
    for y_px, val in zip(ticks_px, tick_values):
        _fill_rect(axis_x - 6, y_px, axis_x + 1, y_px + 2, (40, 45, 55))
        label = str(val)
        _draw_text(canvas, width, height, label, max(6, axis_x - 10 - len(label) * 12), y_px - 6, (40, 45, 55), scale=2)

    # Control bar (gray-blue)
    c_x0, c_x1 = 105, 160
    _fill_rect(c_x0, control_y_px, c_x1, bottom_y, (140, 152, 170))
    # Control error bar (intentionally taller than bar top to demonstrate bar-top vs error-bar discipline)
    c_mid = (c_x0 + c_x1) // 2
    _fill_rect(c_mid - 1, max(top_y, control_y_px - 14), c_mid + 1, control_y_px, (50, 55, 65))
    _fill_rect(c_mid - 8, max(top_y, control_y_px - 14), c_mid + 8, max(top_y, control_y_px - 12), (50, 55, 65))

    # Treated bar
    t_x0, t_x1 = 200, 255
    t_col = (215, 75, 75) if is_trap else bar_color
    _fill_rect(t_x0, treated_y_px, t_x1, bottom_y, t_col)
    t_mid = (t_x0 + t_x1) // 2
    _fill_rect(t_mid - 1, max(top_y, treated_y_px - 16), t_mid + 1, treated_y_px, (50, 55, 65))
    _fill_rect(t_mid - 8, max(top_y, treated_y_px - 16), t_mid + 8, max(top_y, treated_y_px - 14), (50, 55, 65))

    return encode_rgb_png(width, height, canvas)


def generate_demo_ruler_assets(
    out_dir: Path | None = None,
    reports: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Generate authentic 1:1 `*_ruler.png` images from REAL repo figure JPGs + mode fixtures."""
    repo_root = Path(__file__).resolve().parents[2]
    specs = {
        "PMC10128450:Fig2C:FerulicAcid_CovalentCopolymer": {
            "ticks_px": [50, 150, 250],
            "tick_values": [100, 50, 0],
            "control_y_px": 210,
            "treated_y_px": 77,
            "height": 290,
            "bar_color": (37, 99, 235),
            "is_trap": False,
        },
        "PMC9601184:Fig1B:Curcumin_PhysicalBlend_Trap": {
            "ticks_px": [50, 150, 250],
            "tick_values": [100, 50, 0],
            "control_y_px": 70,
            "treated_y_px": 95,
            "height": 290,
            "bar_color": (220, 38, 38),
            "is_trap": True,
        },
        "REVIEW:PMC10084211:SchiffBase_GallicHydrogel": {
            "ticks_px": [40, 140, 240],
            "tick_values": [80, 40, 0],
            "control_y_px": 210,
            "treated_y_px": 95,
            "height": 280,
            "bar_color": (124, 58, 237),
            "is_trap": False,
        },
        "REVIEW:PMC9731044:HRP_Enzymatic_FerulicHydrogel": {
            "ticks_px": [60, 160, 260],
            "tick_values": [40, 20, 0],
            "control_y_px": 225,
            "treated_y_px": 118,
            "height": 295,
            "bar_color": (79, 70, 229),
            "is_trap": False,
        },
        "GAP:PMC10219304:RadicalInhibition_Bottleneck": {
            "ticks_px": [50, 150, 250],
            "tick_values": [100, 50, 0],
            "control_y_px": 68,
            "treated_y_px": 177,
            "height": 290,
            "bar_color": (217, 119, 6),
            "is_trap": False,
        },
        "GAP:PMC10388120:UrolithinA_MetabolicPotency": {
            "ticks_px": [40, 140, 240],
            "tick_values": [20, 10, 0],
            "control_y_px": 142,
            "treated_y_px": 64,
            "height": 280,
            "bar_color": (5, 150, 105),
            "is_trap": False,
        },
    }

    uris: dict[str, str] = {}
    rendered_by_relpath: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        if out_dir is not None:
            (out_dir / "ruler_assets").mkdir(parents=True, exist_ok=True)

        # 1. Render 1:1 Y-pixel rulers directly on REAL Europe PMC figure JPGs from the repo!
        if reports:
            for rep in reports.values():
                for rec in rep.get("records") or []:
                    rel = rec.get("real_image_relpath")
                    if not rel:
                        continue
                    if rel in rendered_by_relpath:
                        uris[rec["record_id"]] = rendered_by_relpath[rel]
                        continue
                    src_img = repo_root / rel
                    if src_img.is_file():
                        safe_name = Path(rel).stem
                        ruler_p = tmp_root / f"{safe_name}_ruler.png"
                        try:
                            render_y_pixel_ruler_png(src_img, ruler_p)
                            ruler_bytes = ruler_p.read_bytes()
                            if out_dir is not None:
                                (out_dir / "ruler_assets" / f"{safe_name}_ruler.png").write_bytes(ruler_bytes)
                            uri = "data:image/png;base64," + base64.b64encode(ruler_bytes).decode("ascii")
                            rendered_by_relpath[rel] = uri
                            uris[rec["record_id"]] = uri
                        except Exception:
                            pass

        # 2. Render remaining mode figures
        for rec_id, cfg in specs.items():
            safe_name = rec_id.replace(":", "_")
            raw_p = tmp_root / f"{safe_name}.png"
            ruler_p = tmp_root / f"{safe_name}_ruler.png"
            raw_bytes = _generate_synthetic_bar_chart_png(
                width=310,
                height=cfg["height"],
                ticks_px=cfg["ticks_px"],
                tick_values=cfg["tick_values"],
                control_y_px=cfg["control_y_px"],
                treated_y_px=cfg["treated_y_px"],
                bar_color=cfg["bar_color"],
                is_trap=cfg["is_trap"],
            )
            raw_p.write_bytes(raw_bytes)
            render_y_pixel_ruler_png(raw_p, ruler_p)
            ruler_bytes = ruler_p.read_bytes()
            if out_dir is not None:
                (out_dir / "ruler_assets" / f"{safe_name}_ruler.png").write_bytes(ruler_bytes)
            uris[rec_id] = "data:image/png;base64," + base64.b64encode(ruler_bytes).decode("ascii")
    return uris


def build_initial_studio_state(out_dir: Path | None = None) -> dict[str, Any]:
    """Assemble full initial state for AsterLoom Studio across all 3 demand modes + benchmark sheet."""
    plans = get_builtin_demand_presets()
    reports = {p["profile_id"]: execute_compiled_funnel(p) for p in plans}
    ruler_uris = generate_demo_ruler_assets(out_dir=out_dir, reports=reports)
    bench_sheet = build_multimodal_benchmark_sheet()
    bench_preview = score_multimodal_benchmark(bench_sheet, expert_signed=False, reviewer_name="")
    audit_status = get_platform_audit_status()
    return {
        "app_name": "AsterLoom Studio (星织)",
        "app_version": "2.0.0-AI4S",
        "plans": plans,
        "reports": reports,
        "ruler_uris": ruler_uris,
        "benchmark_sheet": bench_sheet,
        "benchmark_preview": bench_preview,
        "audit_status": audit_status,
    }


def render_studio_html(state: dict[str, Any]) -> str:
    """Render the minimalist Antigravity / Codex Light-Mode Scientific IDE GUI HTML."""
    state_json = json.dumps(state, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AsterLoom Studio (星织)</title>
<style>
  :root {{
    --bg-root: #f8fafc;
    --bg-panel: #ffffff;
    --bg-subtle: #f1f5f9;
    --border: #e2e8f0;
    --text-main: #0f172a;
    --text-muted: #64748b;
    --accent: #2563eb;
    --green: #059669;
    --red: #dc2626;
    --font-mono: 'SF Mono', 'JetBrains Mono', 'Menlo', monospace;
    --font-sans: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Inter', sans-serif;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg-root);
    color: var(--text-main);
    font-family: var(--font-sans);
    height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    font-size: 13.5px;
    -webkit-font-smoothing: antialiased;
  }}

  /* Minimalist Top Bar */
  .titlebar {{
    height: 52px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    user-select: none;
  }}
  .brand {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 700;
    font-size: 15px;
    color: var(--text-main);
  }}
  .brand-logo {{
    width: 26px;
    height: 26px;
    border-radius: 7px;
    background: var(--accent);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    color: #fff;
    font-size: 13.5px;
  }}

  /* Segmented Step Control (Antigravity / Apple style) */
  .seg-control {{
    display: inline-flex;
    background: var(--bg-subtle);
    padding: 3px;
    border-radius: 8px;
    border: 1px solid var(--border);
    gap: 2px;
  }}
  .seg-btn {{
    padding: 5px 16px;
    border-radius: 6px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }}
  .seg-btn:hover {{ color: var(--text-main); }}
  .seg-btn.active {{
    background: #ffffff;
    color: var(--text-main);
    box-shadow: 0 1px 2px rgba(15,23,42,0.08);
  }}

  .toolbar-actions {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .btn {{
    padding: 6px 13px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: #ffffff;
    color: var(--text-main);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }}
  .btn:hover {{ background: var(--bg-subtle); border-color: #cbd5e1; }}
  .btn-primary {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }}
  .btn-primary:hover {{ background: #1d4ed8; border-color: #1d4ed8; }}

  /* Clean 3-Column Workspace (Left Sidebar + Single Main Canvas + Clean Right Inspector) */
  .ide-workspace {{
    flex: 1;
    display: grid;
    grid-template-columns: 235px 1fr 370px;
    overflow: hidden;
  }}

  /* Left Sidebar */
  .col-left {{
    background: var(--bg-root);
    border-right: 1px solid var(--border);
    padding: 14px 10px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
  }}
  .new-task-btn {{
    width: 100%;
    padding: 8px 12px;
    border-radius: 8px;
    border: 1px dashed #94a3b8;
    background: #ffffff;
    color: var(--text-main);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    text-align: left;
    transition: all 0.15s;
  }}
  .new-task-btn:hover {{ border-color: var(--accent); color: var(--accent); background: #eff6ff; }}
  .nav-group-label {{
    font-size: 11px;
    font-weight: 700;
    color: var(--text-muted);
    padding: 0 6px;
    letter-spacing: 0.4px;
  }}
  .project-list {{
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .proj-item {{
    padding: 9px 11px;
    border-radius: 8px;
    border: 1px solid transparent;
    cursor: pointer;
    transition: all 0.12s;
  }}
  .proj-item:hover {{ background: #eef2f6; }}
  .proj-item.active {{
    background: #ffffff;
    border-color: var(--border);
    box-shadow: 0 1px 3px rgba(15,23,42,0.05);
  }}
  .proj-mode-tag {{
    display: inline-block;
    font-size: 10.5px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    margin-bottom: 4px;
  }}
  .tag-ml_dataset {{ background: #dbeafe; color: #1d4ed8; }}
  .tag-lit_review {{ background: #ede9fe; color: #6d28d9; }}
  .tag-idea_discovery {{ background: #d1fae5; color: #047857; }}
  .proj-title {{
    font-size: 12.5px;
    font-weight: 600;
    line-height: 1.38;
    color: var(--text-main);
  }}

  /* Single Unified Main Canvas */
  .col-center {{
    background: var(--bg-panel);
    overflow-y: auto;
    padding: 24px 32px;
  }}
  .canvas-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .canvas-title {{
    font-size: 18px;
    font-weight: 700;
    color: var(--text-main);
    margin-bottom: 4px;
  }}
  .canvas-sub {{
    font-size: 12.5px;
    color: var(--text-muted);
  }}

  /* Clean Cards & Pills */
  .clean-card {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    margin-bottom: 14px;
  }}
  .clean-card-title {{
    font-size: 12.5px;
    font-weight: 700;
    color: var(--text-muted);
    margin-bottom: 10px;
  }}
  .pill-list {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .chip {{
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-subtle);
    color: #334155;
    font-size: 12px;
    font-weight: 500;
  }}
  .chip.green {{ background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }}
  .chip.red {{ background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }}

  /* Minimalist 1-Line 3-Stage Funnel Bar */
  .funnel-bar {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 18px;
  }}
  .funnel-step {{
    background: var(--bg-root);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
  }}
  .funnel-step-num {{
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-muted);
  }}
  .funnel-step-val {{
    font-size: 17px;
    font-weight: 800;
    font-family: var(--font-mono);
    margin-top: 3px;
    color: var(--text-main);
  }}
  .funnel-step-meta {{
    font-size: 11.5px;
    color: var(--green);
    margin-top: 2px;
    font-weight: 600;
  }}

  /* Clean 4-Column Data Table */
  table.clean-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  table.clean-table th {{
    text-align: left;
    padding: 10px 12px;
    color: var(--text-muted);
    font-size: 11.5px;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
  }}
  table.clean-table td {{
    padding: 12px 12px;
    border-bottom: 1px solid #f1f5f9;
    vertical-align: middle;
  }}
  table.clean-table tr {{ cursor: pointer; transition: background 0.1s; }}
  table.clean-table tr:hover {{ background: var(--bg-root); }}
  table.clean-table tr.selected-row {{ background: #eff6ff; }}

  .badge-status {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
  }}
  .badge-ADMIT {{ background: #dcfce7; color: #15803d; }}
  .badge-HOLD {{ background: #fee2e2; color: #b91c1c; }}

  /* Right Inspector (Focused on 1 Figure + 1 Summary Card) */
  .col-right {{
    background: var(--bg-root);
    border-left: 1px solid var(--border);
    padding: 18px 18px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }}
  .ruler-card {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    text-align: center;
  }}
  .ruler-card img {{
    max-width: 100%;
    max-height: 310px;
    border-radius: 6px;
    border: 1px solid var(--border);
  }}
  .num-compare {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }}
  .num-box {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 10px 12px;
  }}
  .num-label {{ font-size: 11px; color: var(--text-muted); font-weight: 600; }}
  .num-val {{ font-size: 17px; font-weight: 800; font-family: var(--font-mono); margin-top: 3px; }}

  details.tech-drawer {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 12px;
  }}
  details.tech-drawer summary {{
    cursor: pointer;
    font-weight: 600;
    color: var(--text-muted);
    user-select: none;
  }}
  .mono-block {{
    font-family: var(--font-mono);
    font-size: 11px;
    color: #334155;
    background: var(--bg-subtle);
    padding: 8px 10px;
    border-radius: 6px;
    margin-top: 8px;
    white-space: pre-wrap;
    word-break: break-all;
    line-height: 1.55;
  }}

  /* Modals */
  .modal-backdrop {{
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.35);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 999;
  }}
  .modal {{
    width: 640px;
    max-height: 86vh;
    overflow-y: auto;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 22px 24px;
    box-shadow: 0 20px 45px rgba(15,23,42,0.15);
  }}
  .form-input, .form-select {{
    width: 100%;
    padding: 8px 11px;
    border-radius: 7px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    color: var(--text-main);
    font-size: 13px;
    margin-top: 4px;
    margin-bottom: 12px;
  }}
</style>
</head>
<body>

<!-- Minimalist Top Bar -->
<header class="titlebar">
  <div class="brand">
    <div class="brand-logo">星</div>
    <span>AsterLoom</span>
    <span id="activeModeTag" class="proj-mode-tag tag-ml_dataset" style="margin:0 0 0 4px;">ML-Dataset</span>
  </div>

  <!-- 3-Step Segmented Switcher -->
  <div class="seg-control">
    <button class="seg-btn" id="stepBtn1" onclick="switchStep('demand')">1. 需求定义</button>
    <button class="seg-btn active" id="stepBtn2" onclick="switchStep('funnel')">2. 挖掘列表</button>
    <button class="seg-btn" id="stepBtn3" onclick="switchStep('deliverable')">3. 最终成果</button>
  </div>

  <div class="toolbar-actions">
    <span id="govStatusBadge" class="chip" style="font-size:11px;font-family:var(--font-mono);">Silver · independent_gold=0</span>
    <button class="btn" onclick="openScreenModal()">🧪 双轨分子筛选</button>
    <button class="btn" onclick="openBenchmarkModal()">🎯 专家抽查对答案</button>
    <button class="btn btn-primary" onclick="exportCurrentDeliverable()">⬇ 导出数据</button>
  </div>
</header>

<div id="studioAlertBanner" style="display:none;padding:8px 20px;font-size:12.5px;border-bottom:1px solid var(--border);justify-content:space-between;align-items:center;">
  <span id="studioAlertText"></span>
  <button class="btn" style="padding:2px 8px;font-size:11px;" onclick="hideStudioAlert()">关闭</button>
</div>

<!-- Clean 3-Column Workspace -->
<main class="ide-workspace">

  <!-- Left Sidebar: Only Projects & New Button -->
  <aside class="col-left">
    <button class="new-task-btn" onclick="openNewDemandModal()">+ 新建科研挖掘任务</button>
    <div class="nav-group-label">研究项目</div>
    <div class="project-list" id="projectList"></div>
  </aside>

  <!-- Center Single Unified Canvas -->
  <section class="col-center" id="mainCanvas"></section>

  <!-- Right Focused Figure & Ruler Inspector -->
  <aside class="col-right" id="rightInspectorPane"></aside>

</main>

<!-- Modal 1: Create New Reverse Demand -->
<div class="modal-backdrop" id="newDemandModal">
  <div class="modal">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
      <h3 style="font-size:16px;">新建科研挖掘任务</h3>
      <button class="btn" onclick="closeNewDemandModal()">✕</button>
    </div>
    <label class="nav-group-label">科研模式</label>
    <select class="form-select" id="newModeSelect">
      <option value="ml_dataset">机器学习数据库构建 (ML-Dataset)</option>
      <option value="lit_review">综述写作与深度调研 (Lit-Review)</option>
      <option value="idea_discovery">科研 Idea 与空白点探索 (Idea-Discovery)</option>
    </select>

    <label class="nav-group-label">任务名称</label>
    <input class="form-input" id="newTitleInput" value="光催化产氢共价有机框架 (COFs) 性能数据库" />

    <label class="nav-group-label">学科领域</label>
    <input class="form-input" id="newDomainInput" value="能源催化 / 太阳能制氢" />

    <label class="nav-group-label">核心研究对象关键词（逗号分隔）</label>
    <input class="form-input" id="newEntitiesInput" value="Covalent organic framework, COF photocatalyst, TpPa-1" />

    <label class="nav-group-label">目标提取指标（逗号分隔）</label>
    <input class="form-input" id="newMetricsInput" value="HER_RATE_UMOL_G_H, AQY_PERCENT, BANDGAP_EV" />

    <label class="nav-group-label">自动排除的假阳性陷阱（逗号分隔）</label>
    <input class="form-input" id="newTrapsInput" value="光电催化外加偏压混淆, 质量与面积归一化单位混用" />

    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px;">
      <button class="btn" onclick="closeNewDemandModal()">取消</button>
      <button class="btn btn-primary" onclick="createNewReverseDemand()">开始逆向挖掘</button>
    </div>
  </div>
</div>

<!-- Modal 2: 12-Paper Expert Gold Benchmark (Kept out of main workspace until needed) -->
<div class="modal-backdrop" id="benchmarkModal">
  <div class="modal" style="width:760px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:16px;">🎯 12 篇真实带图文献专家抽查对答案 (Gold Benchmark)</h3>
      <button class="btn" onclick="closeBenchmarkModal()">✕</button>
    </div>
    <div id="benchmarkModalBody"></div>
  </div>
</div>

<!-- Modal 3: Ch1 + Ch2 Dual-Track Virtual Screening -->
<div class="modal-backdrop" id="screenModal">
  <div class="modal" style="width:760px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <h3 style="font-size:16px;">🧪 Ch1 × Ch2 双轨分子虚拟筛选 (Model A v2.0 + Model B v1.0)</h3>
        <div style="font-size:11.5px;color:var(--text-muted);margin-top:2px;">Provisional Silver 预测仅作科研辅助，严格保持 independent_gold=0, training_approved=0</div>
      </div>
      <button class="btn" onclick="closeScreenModal()">✕</button>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">
      <button class="btn" style="font-size:11.5px;padding:4px 9px;" onclick="fillScreenPreset('Resveratrol','Oc1ccc(/C=C/c2cc(O)cc(O)c2)cc1')">Resveratrol</button>
      <button class="btn" style="font-size:11.5px;padding:4px 9px;" onclick="fillScreenPreset('Ferulic Acid','COc1cc(/C=C/C(=O)O)ccc1O')">Ferulic Acid</button>
      <button class="btn" style="font-size:11.5px;padding:4px 9px;" onclick="fillScreenPreset('BHMF','OCc1ccc(CO)o1')">2,5-Bis(hydroxymethyl)furan</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:10px;">
      <div>
        <label class="nav-group-label">分子名称</label>
        <input class="form-input" id="screenNameInput" value="Ferulic Acid" />
      </div>
      <div>
        <label class="nav-group-label">Canonical SMILES</label>
        <input class="form-input" id="screenSmilesInput" value="COc1cc(/C=C/C(=O)O)ccc1O" />
      </div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;">
      <div>
        <label class="nav-group-label">Ch1 细胞背景</label>
        <select class="form-select" id="screenCellCtxSelect">
          <option value="NON_TUMOR_PRIMARY_OR_NORMAL">正常/原代</option>
          <option value="TUMOR_CANCER_LINE">肿瘤细胞系</option>
        </select>
      </div>
      <div>
        <label class="nav-group-label">Ch1 范式</label>
        <select class="form-select" id="screenParadigmSelect">
          <option value="BASAL">BASAL</option>
          <option value="STRESS_RESCUE">STRESS_RESCUE</option>
        </select>
      </div>
      <div>
        <label class="nav-group-label">Ch1 剂量 (uM)</label>
        <input class="form-input" id="screenDoseInput" type="number" value="10" />
      </div>
      <div>
        <label class="nav-group-label">Ch2 反应温度 (°C)</label>
        <input class="form-input" id="screenTempInput" type="number" value="80" />
      </div>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-bottom:12px;">
      <button class="btn btn-primary" onclick="runDualTrackScreen()">运行双轨推理</button>
    </div>
    <div id="screenResultBox" class="clean-card" style="margin-bottom:0;background:var(--bg-root);">
      <div style="color:var(--text-muted);font-size:12.5px;">输入 SMILES 后点击「运行双轨推理」，同屏查看 Ch1 胞内 ATP 三分类与 Ch2 共价聚合推荐路径。</div>
    </div>
  </div>
</div>

<script>
const STUDIO_STATE = {state_json};
let activeProfileId = STUDIO_STATE.plans[0].profile_id;
let activeRecordIndex = 0;
let activeStep = 'funnel'; // 'demand' | 'funnel' | 'deliverable'
let statusFilter = 'ALL';  // 'ALL' | 'ADMIT' | 'HOLD'

function getActivePlan() {{
  return STUDIO_STATE.plans.find(p => p.profile_id === activeProfileId) || STUDIO_STATE.plans[0];
}}
function getActiveReport() {{
  return STUDIO_STATE.reports[activeProfileId] || Object.values(STUDIO_STATE.reports)[0];
}}

function showStudioAlert(msg, kind = 'error') {{
  const bar = document.getElementById('studioAlertBanner');
  const txt = document.getElementById('studioAlertText');
  if (!bar || !txt) return;
  bar.style.display = 'flex';
  if (kind === 'error') {{
    bar.style.background = '#fef2f2';
    bar.style.color = '#b91c1c';
  }} else if (kind === 'warn') {{
    bar.style.background = '#fffbeb';
    bar.style.color = '#92400e';
  }} else {{
    bar.style.background = '#ecfdf5';
    bar.style.color = '#065f46';
  }}
  txt.textContent = msg;
}}
function hideStudioAlert() {{
  const bar = document.getElementById('studioAlertBanner');
  if (bar) bar.style.display = 'none';
}}
window.onNativeActionError = function(msg) {{
  showStudioAlert('原生执行失败：' + (msg || '未知错误'), 'error');
}};
window.onNativeStateLoaded = function(liveState) {{
  if (liveState && Array.isArray(liveState.plans) && liveState.plans.length > 0) {{
    STUDIO_STATE.plans = liveState.plans;
    STUDIO_STATE.reports = liveState.reports || STUDIO_STATE.reports;
    STUDIO_STATE.ruler_uris = liveState.ruler_uris || STUDIO_STATE.ruler_uris;
    STUDIO_STATE.audit_status = liveState.audit_status || STUDIO_STATE.audit_status;
    if (!STUDIO_STATE.reports[activeProfileId]) {{
      activeProfileId = STUDIO_STATE.plans[0].profile_id;
    }}
    updateGovBadge();
    renderProjectList();
    selectProject(activeProfileId);
  }}
}};

function updateGovBadge() {{
  const el = document.getElementById('govStatusBadge');
  const au = STUDIO_STATE.audit_status;
  if (!el) return;
  if (au && au.ch1 && au.ch2 && !au.ch1.fallback && !au.ch2.fallback) {{
    el.textContent = `Silver · Ch1:${{au.ch1.usable_numeric_rows || 0}} Ch2:${{au.ch2.b1_total_rows || 0}} · gold=0`;
  }} else {{
    el.textContent = 'Provisional Silver · independent_gold=0';
  }}
}}

function initStudio() {{
  updateGovBadge();
  renderProjectList();
  selectProject(activeProfileId);
  loadLiveStateOnStartup();
}}

async function loadLiveStateOnStartup() {{
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.asterloom) {{
    window.webkit.messageHandlers.asterloom.postMessage({{ action: 'load_live_state' }});
    return;
  }}
  if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {{
    try {{
      const res = await fetch('/api/state');
      if (res.ok) {{
        window.onNativeStateLoaded(await res.json());
      }}
    }} catch (e) {{
      showStudioAlert('无法连接本地后台获取最新实产状态：' + e.message, 'warn');
    }}
  }}
}}

function renderProjectList() {{
  const container = document.getElementById('projectList');
  container.innerHTML = STUDIO_STATE.plans.map(plan => {{
    const isAct = plan.profile_id === activeProfileId ? 'active' : '';
    const modeName = plan.mode === 'ml_dataset' ? 'ML-Dataset 建库'
                   : plan.mode === 'lit_review' ? 'Lit-Review 综述'
                   : 'Idea-Discovery 探索';
    return `
      <div class="proj-item ${{isAct}}" onclick="selectProject('${{plan.profile_id}}')">
        <span class="proj-mode-tag tag-${{plan.mode}}">${{modeName}}</span>
        <div class="proj-title">${{plan.demand_spec.title}}</div>
      </div>
    `;
  }}).join('');
}}

function selectProject(profileId) {{
  activeProfileId = profileId;
  activeRecordIndex = 0;
  statusFilter = 'ALL';
  const plan = getActivePlan();
  const tagEl = document.getElementById('activeModeTag');
  tagEl.className = `proj-mode-tag tag-${{plan.mode}}`;
  tagEl.textContent = plan.mode === 'ml_dataset' ? 'ML-Dataset 建库'
                    : plan.mode === 'lit_review' ? 'Lit-Review 综述'
                    : 'Idea-Discovery 探索';
  renderProjectList();
  renderMainCanvas();
  renderRightInspector();
}}

function switchStep(step) {{
  activeStep = step;
  document.getElementById('stepBtn1').className = 'seg-btn ' + (step === 'demand' ? 'active' : '');
  document.getElementById('stepBtn2').className = 'seg-btn ' + (step === 'funnel' ? 'active' : '');
  document.getElementById('stepBtn3').className = 'seg-btn ' + (step === 'deliverable' ? 'active' : '');
  renderMainCanvas();
}}

function setStatusFilter(f) {{
  statusFilter = f;
  renderMainCanvas();
}}

function renderMainCanvas() {{
  const plan = getActivePlan();
  const report = getActiveReport();
  const spec = plan.demand_spec;
  const fm = report.funnel_metrics;
  const canvas = document.getElementById('mainCanvas');

  const headerHtml = `
    <div class="canvas-header">
      <div>
        <div class="canvas-title">${{spec.title}}</div>
        <div class="canvas-sub">${{spec.domain}} · ${{spec.objective_summary}}</div>
      </div>
      <button class="btn btn-primary" onclick="switchStep('deliverable')">查看最终产出 →</button>
    </div>
  `;

  if (activeStep === 'demand') {{
    const s1 = plan.funnel_stages.stage1;
    const s2 = plan.funnel_stages.stage2;
    const s3 = plan.funnel_stages.stage3;
    canvas.innerHTML = headerHtml + `
      <div class="clean-card">
        <div class="clean-card-title">研究对象与检索锚点 (Entities)</div>
        <div class="pill-list">${{spec.primary_entities.map(e => `<span class="chip">${{e}}</span>`).join('')}}</div>
      </div>
      <div class="clean-card">
        <div class="clean-card-title">目标提取指标 / 综述对比维度 (Target Metrics)</div>
        <div class="pill-list">${{spec.target_endpoints_or_metrics.map(m => `<span class="chip green">${{m}}</span>`).join('')}}</div>
      </div>
      <div class="clean-card">
        <div class="clean-card-title">自动拦截的假阳性科学陷阱 (Excluded Traps)</div>
        <div class="pill-list">${{spec.forbidden_traps.map(t => `<span class="chip red">✕ ${{t}}</span>`).join('')}}</div>
      </div>
      <details class="tech-drawer">
        <summary>▸ 查看逆向自动生成的三轮底层管线规则 (Europe PMC 检索式 / 初筛标准 / 标尺合同)</summary>
        <div class="mono-block">【第 1 轮 Python 检索与关键词切割 ($0)】
Query: ${{s1.epmc_boolean_queries[0]}}
Sections: ${{s1.keyword_slicers.target_xml_sections.join(', ')}}

【第 2 轮 外接轻量 API 初筛 (~$0.00065/篇)】
Model: ${{s2.recommended_provider}} / ${{s2.recommended_model}}
Rubric: ${{s2.screening_rubric.join(' | ')}}

【第 3 轮 多模态 Agent + 1:1 像素标尺重算】
Deliverable: ${{s3.target_deliverable}}</div>
      </details>
    `;
  }} else if (activeStep === 'funnel') {{
    const allRecs = report.records;
    const filtered = allRecs.map((r, idx) => ({{r, idx}})).filter(item =>
      statusFilter === 'ALL' || item.r.admission === statusFilter
    );

    const rowsHtml = filtered.map(({{r, idx}}) => {{
      const sel = idx === activeRecordIndex ? 'selected-row' : '';
      const cal = r.ruler_calibration;
      const labelStatus = r.admission === 'ADMIT' ? '准入' : '拦截';
      const resultText = (r.admission === 'ADMIT' && cal)
        ? `<b style="color:#059669;font-family:var(--font-mono);">${{cal.recomputed_treated_value}} ${{cal.unit}}</b> <span style="color:var(--text-muted);font-size:12px;">(log2FC ${{cal.recomputed_log2fc >= 0 ? '+' : ''}}${{cal.recomputed_log2fc}})</span>`
        : `<span style="color:#b91c1c;font-size:12.5px;">${{r.audit_note.replace(/^【.*?】/, '')}}</span>`;

      return `
        <tr class="${{sel}}" onclick="selectRecord(${{idx}})">
          <td style="width:72px;"><span class="badge-status badge-${{r.admission}}">${{labelStatus}}</span></td>
          <td style="width:120px;font-family:var(--font-mono);font-weight:700;color:var(--accent);">${{r.paper_id}}</td>
          <td>
            <div style="font-weight:600;">${{r.entity_x}}</div>
            <div style="font-size:12px;color:var(--text-muted);">${{r.cell_model}}</div>
          </td>
          <td>${{resultText}}</td>
        </tr>
      `;
    }}).join('');

    canvas.innerHTML = headerHtml + `
      <div class="funnel-bar">
        <div class="funnel-step">
          <div class="funnel-step-num">① Python 爬取与关键词粗筛</div>
          <div class="funnel-step-val">${{fm.stage1_crawled_oa_papers}} → ${{fm.stage1_after_python_keyword_slicing}} 篇</div>
          <div class="funnel-step-meta">$0 成本 · 0-LLM 规则切片</div>
        </div>
        <div class="funnel-step">
          <div class="funnel-step-num">② 轻量 API 快速初筛</div>
          <div class="funnel-step-val">${{fm.stage2_api_screened_papers}} → ${{fm.stage2_sent_to_stage3_agent}} 篇</div>
          <div class="funnel-step-meta">过滤 ${{fm.stage2_filter_rate_pct}}% 无效文献 ($${{fm.stage2_total_cost_usd}})</div>
        </div>
        <div class="funnel-step">
          <div class="funnel-step-num">③ 1:1 像素标尺复选入库</div>
          <div class="funnel-step-val">${{fm.stage3_admitted_records}} 准入 / ${{fm.stage3_held_scientific_traps}} 拦截</div>
          <div class="funnel-step-meta">总花费 $${{fm.total_pipeline_cost_usd}} (省 ${{fm.cost_saving_pct}}%)</div>
        </div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <div class="seg-control">
          <button class="seg-btn ${{statusFilter==='ALL'?'active':''}}" onclick="setStatusFilter('ALL')">全部 (${{allRecs.length}})</button>
          <button class="seg-btn ${{statusFilter==='ADMIT'?'active':''}}" onclick="setStatusFilter('ADMIT')">已准入</button>
          <button class="seg-btn ${{statusFilter==='HOLD'?'active':''}}" onclick="setStatusFilter('HOLD')">已拦截陷阱</button>
        </div>
        <span style="font-size:12px;color:var(--text-muted);">点击任意文献，右侧查看论文原图与 1:1 像素标尺</span>
      </div>

      <table class="clean-table">
        <thead>
          <tr>
            <th>状态</th>
            <th>文献 ID</th>
            <th>研究对象 / 实验条件</th>
            <th>标尺核验结果 / 拦截原因</th>
          </tr>
        </thead>
        <tbody>${{rowsHtml}}</tbody>
      </table>
    `;
  }} else {{
    // Step 3: Deliverable view
    const deliv = report.deliverable_summary;
    if (deliv.deliverable_type === 'ML_DATASET_TABLE') {{
      canvas.innerHTML = headerHtml + `
        <div class="clean-card">
          <div class="clean-card-title">📊 数据集实产规模与分层统计</div>
          <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:8px;">
            ${{Object.entries(deliv.stratification).map(([k, v]) => `
              <div style="padding:10px 12px;background:var(--bg-root);border-radius:8px;border:1px solid var(--border);">
                <div style="font-size:12px;color:var(--text-muted);">${{k}}</div>
                <div style="font-size:18px;font-weight:800;font-family:var(--font-mono);color:var(--accent);margin-top:3px;">${{v}}</div>
              </div>
            `).join('')}}
          </div>
        </div>
        <div class="clean-card">
          <div class="clean-card-title">🛡️ 化学骨架防泄漏切分验证 (${{deliv.anti_leakage_validation.strategy}})</div>
          <p style="line-height:1.65;color:#334155;font-size:13px;">${{deliv.anti_leakage_validation.why_not_random_split}}</p>
        </div>
      `;
    }} else if (deliv.deliverable_type === 'LIT_REVIEW_COMPARISON_MATRIX') {{
      const rows = deliv.comparison_matrix_rows.map(r => `
        <tr>
          <td style="font-family:var(--font-mono);font-weight:700;color:var(--accent);">${{r.paper_id}}</td>
          <td><b>${{r.route}}</b></td>
          <td>${{r.monomer_and_ds}}</td>
          <td style="color:var(--green);font-weight:700;font-family:var(--font-mono);">${{r.ruler_verified_modulus}}</td>
          <td>${{r.bio_activity_evidence}}</td>
          <td style="color:var(--red);">${{r.author_limitation}}</td>
        </tr>
      `).join('');
      canvas.innerHTML = headerHtml + `
        <div class="clean-card">
          <div class="clean-card-title">📝 零幻觉综述合成结论</div>
          <p style="line-height:1.65;font-size:13.5px;">${{deliv.review_draft_conclusion}}</p>
        </div>
        <table class="clean-table">
          <thead>
            <tr>
              <th>文献</th>
              <th>技术路线</th>
              <th>单体与实测取代度</th>
              <th>标尺核验模量</th>
              <th>生物/代谢实证</th>
              <th>作者自述局限</th>
            </tr>
          </thead>
          <tbody>${{rows}}</tbody>
        </table>
      `;
    }} else {{
      const gapRows = deliv.research_gap_matrix.map(r => `
        <tr>
          <td><b>${{r.monomer_entity}}</b></td>
          <td>${{r.physical_blend}}</td>
          <td>${{r.free_radical_polymerization}}</td>
          <td>${{r.dynamic_reversible_covalent}}</td>
          <td style="background:#ecfdf5;color:#065f46;font-weight:700;">${{r.metabolic_atp_coupled_degradation}}</td>
        </tr>
      `).join('');
      const ideas = deliv.ranked_idea_proposals.map(id => `
        <div class="clean-card" style="border-left:4px solid var(--green);">
          <div style="font-weight:700;color:#047857;margin-bottom:6px;">💡 ${{id.rank}}：${{id.idea_title}}</div>
          <div style="font-size:13px;line-height:1.6;color:#334155;">
            <div><b>破解瓶颈</b>：${{id.core_pain_point_solved}}</div>
            <div style="margin-top:4px;"><b>创新方案</b>：${{id.methodology_innovation}}</div>
          </div>
        </div>
      `).join('');
      canvas.innerHTML = headerHtml + `
        <div class="clean-card">
          <div class="clean-card-title">🗺️ 课题组方向研究空白热力矩阵</div>
          <table class="clean-table">
            <thead>
              <tr>
                <th>候选单体族</th>
                <th>物理共混</th>
                <th>传统自由基共聚</th>
                <th>动态可逆共价</th>
                <th>降解耦合 ATP 修复</th>
              </tr>
            </thead>
            <tbody>${{gapRows}}</tbody>
          </table>
        </div>
        ${{ideas}}
      `;
    }}
  }}
}}

function selectRecord(idx) {{
  activeRecordIndex = idx;
  renderMainCanvas();
  renderRightInspector();
}}

function renderRightInspector() {{
  const pane = document.getElementById('rightInspectorPane');
  const report = getActiveReport();
  const rec = report.records[activeRecordIndex] || report.records[0];
  const rulerUri = STUDIO_STATE.ruler_uris[rec.record_id];
  const cal = rec.ruler_calibration;
  const labelStatus = rec.admission === 'ADMIT' ? '准入' : '拦截';

  const imgCard = rulerUri
    ? `<div class="ruler-card">
         <img src="${{rulerUri}}" alt="1:1 Pixel Ruler" />
         <div style="font-size:11.5px;color:var(--text-muted);margin-top:6px;">
           1:1 像素标尺叠加图 (${{rec.ruler_image_label}})
         </div>
       </div>`
    : `<div class="ruler-card" style="padding:24px;color:var(--text-muted);">
         📄 原生 JATS 表格 / 文本证据 (${{rec.ruler_image_label}})
       </div>`;

  const compareCard = (cal && cal.treated_y_px !== undefined && cal.treated_y_px !== null)
    ? `<div class="num-compare">
         <div class="num-box">
           <div class="num-label">✅ 星织标尺重算</div>
           <div class="num-val" style="color:var(--green);">${{cal.recomputed_treated_value}}</div>
           <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${{cal.unit}} (Y=${{cal.treated_y_px}}px)</div>
         </div>
         <div class="num-box">
           <div class="num-label">❌ 裸模型直接盲猜</div>
           <div class="num-val" style="color:var(--red);">${{rec.raw_vlm_uncalibrated_guess}}</div>
           <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">易误读误差线顶点</div>
         </div>
       </div>`
    : '';

  pane.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <span style="font-family:var(--font-mono);font-weight:800;font-size:14px;color:var(--accent);">${{rec.paper_id}}</span>
      <span class="badge-status badge-${{rec.admission}}">${{labelStatus}} · ${{rec.source_tier}}</span>
    </div>
    <div style="font-size:13px;font-weight:600;line-height:1.45;">${{rec.title}}</div>

    ${{imgCard}}
    ${{compareCard}}

    <div class="clean-card" style="margin-bottom:0;">
      <div class="clean-card-title">核验结论</div>
      <div style="font-size:12.5px;line-height:1.55;color:#334155;">${{rec.audit_note}}</div>
    </div>

    <details class="tech-drawer">
      <summary>▸ 查看技术溯源细节 (OLS 回归 / SHA256 / 结构)</summary>
      <div class="mono-block">Stage 1: ${{rec.stage1_slice}}
Stage 2: ${{rec.stage2_screen}}
${{cal && cal.slope ? `OLS Fit: y = ${{cal.slope}} * px + ${{cal.intercept}} (log2FC=${{cal.recomputed_log2fc}})\n` : ''}}SMILES : ${{rec.canonical_smiles}}
Murcko : ${{rec.murcko_scaffold}}
XML SHA: ${{rec.author_xml_sha256.slice(0,28)}}...</div>
    </details>
  `;
}}

function openBenchmarkModal() {{
  document.getElementById('benchmarkModal').style.display = 'flex';
  renderBenchmarkModal();
}}
function closeBenchmarkModal() {{
  document.getElementById('benchmarkModal').style.display = 'none';
}}

function renderBenchmarkModal() {{
  const sheet = STUDIO_STATE.benchmark_sheet;
  const score = STUDIO_STATE.benchmark_preview;
  const m = score.metrics;
  const rows = sheet.items.map((it, idx) => `
    <tr>
      <td style="font-family:var(--font-mono);font-weight:700;color:var(--accent);">${{it.paper_id}}</td>
      <td><b>${{it.candidate_entity}}</b><div style="font-size:11px;color:var(--text-muted);">${{it.trap_category}}</div></td>
      <td><span class="badge-status badge-${{it.asterloom_decision}}">${{it.asterloom_decision}}</span></td>
      <td>
        <select onchange="updateBenchVerdict(${{idx}}, this.value)" style="padding:3px 6px;border-radius:5px;border:1px solid #cbd5e1;">
          <option value="ADMIT" ${{it.expert_verdict==='ADMIT'?'selected':''}}>ADMIT (真阳性)</option>
          <option value="HOLD" ${{it.expert_verdict==='HOLD'?'selected':''}}>HOLD (陷阱拦截)</option>
        </select>
      </td>
      <td style="font-family:var(--font-mono);font-size:12px;">
        <b style="color:var(--green);">${{it.asterloom_recomputed_value ?? '—'}}</b> / <span style="color:var(--red);">${{it.raw_vlm_guess_value ?? '—'}}</span>
      </td>
    </tr>
  `).join('');

  document.getElementById('benchmarkModalBody').innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;">
      <div class="num-box">
        <div class="num-label">准入 F1 准确率</div>
        <div class="num-val" style="color:var(--green);">${{m.admission_f1_pct}}%</div>
      </div>
      <div class="num-box">
        <div class="num-label">科学陷阱拦截率</div>
        <div class="num-val" style="color:var(--green);">${{m.scientific_trap_rejection_rate_pct}}%</div>
      </div>
      <div class="num-box">
        <div class="num-label">标尺读图误差降低</div>
        <div class="num-val" style="color:var(--accent);">↓${{m.ruler_error_reduction_pct}}%</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:14px;">
      <input class="form-input" id="reviewerSignInput" style="margin:0;flex:1;"
             placeholder="输入专家签名确认 (例如: PI_Verified_2026)" value="${{score.reviewer_name || ''}}" />
      <button class="btn btn-primary" onclick="signAndScoreBenchmark()">✔ 签字确认 (independent_gold=${{score.independent_gold}})</button>
    </div>
    <table class="clean-table">
      <thead>
        <tr><th>文献</th><th>核验对象</th><th>星织</th><th>专家答案</th><th>星织重算 / 裸猜</th></tr>
      </thead>
      <tbody>${{rows}}</tbody>
    </table>
  `;
}}

function updateBenchVerdict(idx, val) {{
  STUDIO_STATE.benchmark_sheet.items[idx].expert_verdict = val;
  recomputeBenchmarkInBrowser(false);
}}
function signAndScoreBenchmark() {{
  const reviewer = (document.getElementById('reviewerSignInput').value || '').trim();
  if (!reviewer) {{ alert('请输入专家签名'); return; }}
  recomputeBenchmarkInBrowser(true, reviewer);
}}
function recomputeBenchmarkInBrowser(signed, reviewer = '') {{
  const items = STUDIO_STATE.benchmark_sheet.items;
  let tp = 0, fp = 0, fn = 0, tn = 0, astErrs = [], rawErrs = [];
  items.forEach(it => {{
    if (it.asterloom_decision === 'ADMIT' && it.expert_verdict === 'ADMIT') tp++;
    else if (it.asterloom_decision === 'ADMIT' && it.expert_verdict !== 'ADMIT') fp++;
    else if (it.asterloom_decision !== 'ADMIT' && it.expert_verdict === 'ADMIT') fn++;
    else tn++;
    if (typeof it.expert_verified_value === 'number') {{
      if (typeof it.asterloom_recomputed_value === 'number') astErrs.push(Math.abs(it.asterloom_recomputed_value - it.expert_verified_value));
      if (typeof it.raw_vlm_guess_value === 'number') rawErrs.push(Math.abs(it.raw_vlm_guess_value - it.expert_verified_value));
    }}
  }});
  const prec = (tp + fp) ? tp / (tp + fp) : 0;
  const rec = (tp + fn) ? tp / (tp + fn) : 0;
  const f1 = (prec + rec) ? (2 * prec * rec / (prec + rec)) : 0;
  const trapRate = (tn + fp) ? tn / (tn + fp) : 0;
  const astMae = astErrs.length ? astErrs.reduce((a,b)=>a+b,0)/astErrs.length : 0;
  const rawMae = rawErrs.length ? rawErrs.reduce((a,b)=>a+b,0)/rawErrs.length : 0;
  const redPct = rawMae > 0 ? ((rawMae - astMae) / rawMae * 100) : 0;
  STUDIO_STATE.benchmark_preview = {{
    expert_signed: signed,
    reviewer_name: signed ? reviewer : '',
    independent_gold: signed ? items.length : 0,
    metrics: {{
      admission_precision_pct: (prec * 100).toFixed(2),
      admission_recall_pct: (rec * 100).toFixed(2),
      admission_f1_pct: (f1 * 100).toFixed(2),
      scientific_trap_rejection_rate_pct: (trapRate * 100).toFixed(2),
      pixel_ruler_recomputed_mae: astMae.toFixed(4),
      uncalibrated_raw_vlm_mae: rawMae.toFixed(4),
      ruler_error_reduction_pct: redPct.toFixed(2),
    }}
  }};
  renderBenchmarkModal();
}}

function openNewDemandModal() {{ document.getElementById('newDemandModal').style.display = 'flex'; }}
function closeNewDemandModal() {{ document.getElementById('newDemandModal').style.display = 'none'; }}

window.onNativeCompileComplete = function(data) {{
  if (data && data.plan && data.report) {{
    STUDIO_STATE.plans.unshift(data.plan);
    STUDIO_STATE.reports[data.plan.profile_id] = data.report;
    closeNewDemandModal();
    selectProject(data.plan.profile_id);
  }}
}};

async function createNewReverseDemand() {{
  const mode = document.getElementById('newModeSelect').value;
  const title = document.getElementById('newTitleInput').value.trim() || '自定义科研任务';
  const domain = document.getElementById('newDomainInput').value.trim() || '交叉学科';
  const entities = document.getElementById('newEntitiesInput').value.split(',').map(s=>s.trim()).filter(Boolean);
  const metrics = document.getElementById('newMetricsInput').value.split(',').map(s=>s.trim()).filter(Boolean);
  const traps = document.getElementById('newTrapsInput').value.split(',').map(s=>s.trim()).filter(Boolean);
  const pid = 'custom_' + mode + '_' + (STUDIO_STATE.plans.length + 1) + '_v1';
  const specPayload = {{
    project_id: pid, title, mode, domain,
    objective_summary: `由 [${{title}}] 逆向驱动三轮检验挖掘。`,
    primary_entities: entities, target_endpoints_or_metrics: metrics, forbidden_traps: traps
  }};
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.asterloom) {{
    window.webkit.messageHandlers.asterloom.postMessage({{ action: 'compile_demand', spec: specPayload }});
    return;
  }}
  if (window.location.protocol === 'file:') {{
    showStudioAlert('当前为静态离线 HTML 预览（无本地 Python 后台或原生 Bridge），请运行 `asterloom studio --serve` 或打开 `AsterLoom Studio.app` 执行实时逆向编译。', 'warn');
    return;
  }}
  try {{
    const res = await fetch('/api/compile-and-run', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(specPayload)
    }});
    const payload = await res.json();
    if (!res.ok) {{
      showStudioAlert('逆向编译失败：' + (payload.error || res.status), 'error');
      return;
    }}
    window.onNativeCompileComplete(payload);
  }} catch (e) {{
    showStudioAlert('请求本地后台失败：' + e.message, 'error');
  }}
}}

function openScreenModal() {{ document.getElementById('screenModal').style.display = 'flex'; }}
function closeScreenModal() {{ document.getElementById('screenModal').style.display = 'none'; }}
function fillScreenPreset(name, smiles) {{
  document.getElementById('screenNameInput').value = name;
  document.getElementById('screenSmilesInput').value = smiles;
}}
window.onNativeScreenComplete = function(data) {{
  const box = document.getElementById('screenResultBox');
  if (!box || !data) return;
  const c1 = data.ch1_atp_model_a_v2 || {{}};
  const c2 = data.ch2_polymer_model_b_v1 || {{}};
  const c1Html = c1.available
    ? `<div><b>预测方向</b>: <span class="badge-status badge-${{c1.predicted_direction==='UP'?'ADMIT':'HOLD'}}">${{c1.predicted_direction}}</span></div>
       <div style="margin-top:4px;font-family:var(--font-mono);font-size:12px;">P(UP)=${{c1.p_up}} | P(NEUTRAL)=${{c1.p_neutral}} | P(DOWN)=${{c1.p_down}}</div>
       <div style="margin-top:4px;font-family:var(--font-mono);font-size:12px;">pred_log2FC=${{c1.pred_log2fc}} | 非肿瘤选择性Δ=${{c1.nontumor_selectivity_delta}}</div>
       <div style="margin-top:4px;font-size:11.5px;color:var(--text-muted);">AD: ${{c1.ad_status}} (Tanimoto=${{c1.max_tanimoto}}, z=${{c1.desc8_z_rms}})</div>`
    : `<div style="color:#92400e;">不可用 (${{c1.status || 'UNAVAILABLE'}}): ${{c1.reason || '缺少依赖或模型文件'}}</div>`;
  const c2Html = c2.available
    ? `<div><b>推荐聚合角色</b>: <span class="badge-status badge-ADMIT">${{c2.recommended_monomer_role}}</span></div>
       <div style="margin-top:4px;font-size:12px;"><b>推荐反应类型</b>: ${{c2.recommended_reaction_type}}</div>
       <div style="margin-top:4px;font-family:var(--font-mono);font-size:12px;">预期 Mn=${{c2.pred_mn_kda}} kDa | 转化率=${{c2.pred_monomer_conversion_pct}}%</div>
       <div style="margin-top:4px;font-size:11.5px;color:var(--text-muted);">AD: ${{c2.ad_status}} (Tanimoto=${{c2.max_tanimoto}}, sites=${{c2.fg_total_reactive_sites}})</div>`
    : `<div style="color:#92400e;">不可用 (${{c2.status || 'UNAVAILABLE'}}): ${{c2.reason || '缺少依赖或模型文件'}}</div>`;
  box.innerHTML = `
    <div style="font-size:11.5px;color:var(--text-muted);margin-bottom:8px;">${{data.name}} (${{data.smiles}}) · ${{data.governance_notice}}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:12px;">
        <div class="clean-card-title">Ch1 · Model A v2.0 (胞内 ATP 代谢)</div>
        ${{c1Html}}
      </div>
      <div style="background:#fff;border:1px solid var(--border);border-radius:8px;padding:12px;">
        <div class="clean-card-title">Ch2 · Model B v1.0 (天然单体聚合)</div>
        ${{c2Html}}
      </div>
    </div>
  `;
}};
async function runDualTrackScreen() {{
  const payload = {{
    name: document.getElementById('screenNameInput').value.trim() || 'query_molecule',
    smiles: document.getElementById('screenSmilesInput').value.trim(),
    cell_context: document.getElementById('screenCellCtxSelect').value,
    paradigm_regime: document.getElementById('screenParadigmSelect').value,
    dose_um: parseFloat(document.getElementById('screenDoseInput').value || '10'),
    temp_c: parseFloat(document.getElementById('screenTempInput').value || '80'),
  }};
  if (!payload.smiles) {{ showStudioAlert('请输入有效的 SMILES 字符串', 'warn'); return; }}
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.asterloom) {{
    window.webkit.messageHandlers.asterloom.postMessage({{ action: 'screen_molecule', spec: payload }});
    return;
  }}
  if (window.location.protocol === 'file:') {{
    showStudioAlert('当前为静态离线 HTML 预览（无本地 Python 后台或原生 Bridge），请运行 `asterloom studio --serve` 或打开 `AsterLoom Studio.app` 进行双轨分子推理。', 'warn');
    return;
  }}
  try {{
    const res = await fetch('/api/screen-molecule', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload)
    }});
    const out = await res.json();
    if (!res.ok) {{
      showStudioAlert('双轨分子筛选失败：' + (out.error || res.status), 'error');
      return;
    }}
    window.onNativeScreenComplete(out);
  }} catch (e) {{
    showStudioAlert('请求本地后台失败：' + e.message, 'error');
  }}
}}

function exportCurrentDeliverable() {{
  const plan = getActivePlan();
  const report = getActiveReport();
  const jsonText = JSON.stringify({{
    exported_by: "AsterLoom Studio v2.0",
    compiled_demand_plan: plan,
    funnel_execution_report: report,
    multimodal_gold_benchmark: STUDIO_STATE.benchmark_preview,
  }}, null, 2);
  const filename = `${{plan.profile_id}}_asterloom_deliverable.json`;
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.asterloom) {{
    window.webkit.messageHandlers.asterloom.postMessage({{ action: 'save_file', filename, content: jsonText }});
    return;
  }}
  const blob = new Blob([jsonText], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}}

window.addEventListener('DOMContentLoaded', initStudio);
</script>
</body>
</html>"""


def export_studio_bundle(out_dir: Path) -> dict[str, Any]:
    """Export the self-contained AsterLoom Studio application bundle, JSON state, and 1:1 `*_ruler.png` assets."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state = build_initial_studio_state(out_dir=out_dir)
    html = render_studio_html(state)
    index_path = out_dir / "asterloom_studio.html"
    index_path.write_text(html, encoding="utf-8")

    state_path = out_dir / "studio_state.json"
    state_summary = {
        "app_name": state["app_name"],
        "app_version": state["app_version"],
        "project_count": len(state["plans"]),
        "projects": [
            {
                "profile_id": p["profile_id"],
                "mode": p["mode"],
                "title": p["demand_spec"]["title"],
            }
            for p in state["plans"]
        ],
        "ruler_asset_count": len(state["ruler_uris"]),
        "benchmark_items": len(state["benchmark_sheet"]["items"]),
        "independent_gold": state["benchmark_preview"]["independent_gold"],
        "retired_holdout_20261110_referenced": False,
        "studio_html_path": str(index_path),
    }
    state_path.write_text(json.dumps(state_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return state_summary


def _try_launch_desktop_app_window(url: str) -> bool:
    """Launch AsterLoom Studio in a standalone native-style desktop app window on macOS if available."""
    chrome_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for binary in chrome_candidates:
        if Path(binary).is_file():
            try:
                subprocess.Popen(
                    [binary, f"--app={url}", "--window-size=1480,920"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError:
                continue
    return False


def run_studio_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_desktop_window: bool = False,
    open_browser: bool = False,
) -> None:
    """Start the live AsterLoom Studio Python backend server."""
    state = build_initial_studio_state()

    class StudioHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html", "/asterloom_studio.html"):
                body = render_studio_html(state).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                body = json.dumps(state, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/audit-status":
                body = json.dumps(get_platform_audit_status(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            def _send_json(code: int, obj: dict[str, Any]) -> None:
                resp = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if self.path == "/api/compile-and-run":
                    spec = ScientificDemandSpec(
                        project_id=raw.get("project_id") or "custom_demand_v1",
                        title=raw.get("title") or "自定义科研挖掘任务",
                        mode=raw.get("mode") or "ml_dataset",
                        domain=raw.get("domain") or "交叉学科",
                        objective_summary=raw.get("objective_summary") or "由目标产出逆向驱动三轮检验挖掘。",
                        primary_entities=raw.get("primary_entities") or ["Bioactive compound"],
                        target_endpoints_or_metrics=raw.get("target_endpoints_or_metrics") or ["TARGET_METRIC"],
                        forbidden_traps=raw.get("forbidden_traps") or ["Proxy endpoint trap"],
                    )
                    plan = compile_demand_to_funnel(spec)
                    report = execute_compiled_funnel(plan)
                    state["plans"].insert(0, plan)
                    state["reports"][plan["profile_id"]] = report
                    _send_json(200, {"plan": plan, "report": report})
                elif self.path == "/api/screen-molecule":
                    res = screen_molecule_dual_track(
                        raw.get("smiles") or "",
                        name=raw.get("name") or "query_molecule",
                        dose_um=float(raw.get("dose_um") or 10.0),
                        exposure_h=float(raw.get("exposure_h") or 24.0),
                        cell_context=raw.get("cell_context") or "NON_TUMOR_PRIMARY_OR_NORMAL",
                        paradigm_regime=raw.get("paradigm_regime") or "BASAL",
                        temp_c=float(raw.get("temp_c") or 80.0),
                        time_h=float(raw.get("time_h") or 12.0),
                    )
                    _send_json(200, res)
                else:
                    _send_json(404, {"error": f"UNKNOWN_ENDPOINT:{self.path}"})
            except Exception as exc:
                _send_json(400, {"error": f"{type(exc).__name__}: {exc}"})

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = None
    actual_port = port
    for candidate_port in [port, *range(port + 1, port + 35), 0]:
        try:
            server = ReusableHTTPServer((host, candidate_port), StudioHandler)
            actual_port = server.server_address[1]
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("UNABLE_TO_BIND_ANY_LOCAL_PORT")

    url = f"http://{host}:{actual_port}"
    print(f"AsterLoom Studio GUI running at {url}")
    if open_desktop_window:
        if not _try_launch_desktop_app_window(url):
            webbrowser.open(url)
    elif open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

#!/usr/bin/env python3
"""Offline ATP-content figure screen for the AST-CH1-G1 50-paper OA pool."""

from __future__ import annotations

import argparse
import os
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from r031_evidence.oa_figure_screen import compact_paper, screen_pool, write_json


def render_markdown(result: dict) -> str:
    counts = result["counts"]
    papers = result["papers"]
    by_decision: dict[str, list] = {"SELECT": [], "HOLD_POOL": [], "EXCLUDE": []}
    for row in papers:
        by_decision[row["decision"]].append(row)

    def _sort_select(rows):
        return sorted(rows, key=lambda r: (-r.get("score") or 0, r["paper_id"]))

    lines = [
        "# AST-CH1-G1｜50 篇 OA 图 panel 清点与定向扩展样本（增量 2）",
        "",
        "这是离线筛选结果，不是数字化后的数值表。没有 invent 图上的 Y 值，也没有改 Figure-Y v2 记录或生产库。",
        "",
        f"- 筛选模式：`{result['schema']}`",
        f"- 上限：{result['limit']} 篇",
        f"- 池分母：{counts['pool_total']}（池 A 历史 {counts['pool_a']} + 池 B H4 {counts['pool_b']}）",
        f"- 本地可核 SHA 的 author.xml：{counts['xml_found']}；缺失：{counts['xml_missing']}",
        f"- 入选 SELECT：{counts['SELECT']}；合格但未进上限 HOLD_POOL：{counts['HOLD_POOL']}；排除 EXCLUDE：{counts['EXCLUDE']}",
        f"- DeepSeek 优先种子入选：{counts['deepseek_seeds_selected']} / 5",
        f"- 入选且包内已有图文件：{counts['selected_with_on_disk_image']}；入选但本地暂无图文件：{counts['selected_without_local_image']}",
        f"- H4 回放包 SHA：`{result['h4_replay_zip_sha256']}`",
        "",
        "## 入选论文",
        "",
    ]
    if not by_decision["SELECT"]:
        lines.append("没有达到 ATP 含量图标准的论文。不凑数。")
        lines.append("")
    for row in _sort_select(by_decision["SELECT"]):
        lines.append(f"### {row['paper_id']}  ·  score {row['score']}  ·  {row['pool']}")
        if row.get("title"):
            lines.append("")
            lines.append(row["title"])
        lines.append("")
        for reason in row["reasons"]:
            lines.append(f"- {reason}")
        if row.get("xml_sha256"):
            lines.append(f"- XML SHA-256：`{row['xml_sha256']}`")
        if row.get("xml_path"):
            lines.append(f"- 源路径：`{row['xml_path']}`")
        lines.append("")
        for fig in (compact_paper(row).get("strong_figures") or []):
            cue = "；有定量线索" if fig.get("quantitative_cue") else ""
            lines.append(f"- {fig['label']}{cue}：{fig['caption']}")
            if fig.get("graphics"):
                lines.append(f"  - graphic：{', '.join(fig['graphics'])}")
        lines.append("")

    lines.extend(["## 合格但未进入 30 篇上限", "",])
    if not by_decision["HOLD_POOL"]:
        lines.append("无。本次合格篇数未超过上限，因此没有因名额被放下的论文。")
        lines.append("")
    for row in _sort_select(by_decision["HOLD_POOL"]):
        lines.append(f"- {row['paper_id']}（score {row['score']}）：" + "； ".join(row["reasons"]))
    lines.append("")

    lines.extend(["## 排除与分母", "",])
    reason_key = Counter()
    for row in by_decision["EXCLUDE"]:
        primary = row["reasons"][0] if row.get("reasons") else "unspecified"
        # bucket
        text = primary.lower()
        if "license" in text or "http_hold" in text or "http hold" in text:
            bucket = "license_or_http_hold"
        elif "no verifiable" in text or "not found" in text:
            bucket = "missing_xml"
        elif "no atp-content figure" in text:
            bucket = "no_atp_content_figure"
        elif "mention" in text:
            bucket = "atp_mention_only"
        elif "ratio" in text:
            bucket = "atp_adp_ratio_only"
        elif "table" in text:
            bucket = "table_only"
        else:
            bucket = "other"
        reason_key[bucket] += 1
    lines.append("排除原因分桶：")
    lines.append("")
    for bucket, n in reason_key.most_common():
        lines.append(f"- {bucket}：{n}")
    lines.append("")
    for row in sorted(by_decision["EXCLUDE"], key=lambda r: r["paper_id"]):
        why = "； ".join(row.get("reasons") or [])
        xml = "有 XML" if row.get("xml_path") else "无 XML"
        lines.append(f"- {row['paper_id']}（{row['pool']}，{xml}）：{why}")
    lines.append("")
    lines.extend([
        "## 使用与限制",
        "",
        "- 未把 FIGURE_ESTIMATED 写成 AUTHOR_REPORTED，本文件不含任何读出的柱高或对照数值。",
        "- 入选只表明该篇有可进入下一轮规格/检测的 ATP 含量图；不等于已经准入数值层，也不等于 Gold 或训练标签。",
        "- 多数入选论文的期刊图 JPEG 不在当前 package 目录中。下一增量应只使用已登记的 OA 图资产或已核 SHA 的回放包，不发起新的付费 API。",
        "- 分子身份只做了名称提示，需与 DeepSeek 的化学/实验身份字段对齐后再计第一章父体。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output directory (default: $ASTERLOOM_OUTPUT_DIR or ./figure_outputs)",
        help="Directory for the screen artifacts",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else Path((os.environ.get('ASTERLOOM_OUTPUT_DIR') or '').strip() or (Path.cwd() / 'figure_outputs'))

    result = screen_pool()
    compact = {
        "schema": result["schema"],
        "limit": result["limit"],
        "search_roots": result["search_roots"],
        "h4_replay_zip_sha256": result["h4_replay_zip_sha256"],
        "counts": result["counts"],
        "papers": [compact_paper(row) for row in result["papers"]],
    }
    full_sha = write_json(out_dir / "OA_FIGURE_EXPANSION_SCREEN_v1.json", compact)
    # keep a slim selected list too
    selected = [p for p in compact["papers"] if p["decision"] == "SELECT"]
    selected_sha = write_json(
        out_dir / "OA_FIGURE_EXPANSION_SELECTION_v1.json",
        {
            "schema": result["schema"],
            "source_screen_sha256": full_sha,
            "n_selected": len(selected),
            "papers": selected,
        },
    )
    md = render_markdown(result)
    md_path = out_dir / "OA_FIGURE_EXPANSION_SELECTION_v1.md"
    md_path.write_text(md, encoding="utf-8")
    import hashlib
    md_sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
    pins = {
        "schema": "asterloom-oa-figure-expansion-screen-pins/1",
        "screen_json": "OA_FIGURE_EXPANSION_SCREEN_v1.json",
        "screen_sha256": full_sha,
        "selection_json": "OA_FIGURE_EXPANSION_SELECTION_v1.json",
        "selection_sha256": selected_sha,
        "selection_md": "OA_FIGURE_EXPANSION_SELECTION_v1.md",
        "selection_md_sha256": md_sha,
        "h4_replay_zip_sha256": result["h4_replay_zip_sha256"],
        "counts": result["counts"],
    }
    pins_sha = write_json(out_dir / "OA_FIGURE_EXPANSION_SCREEN_PINS.json", pins)
    summary = {
        k: result["counts"][k]
        for k in result["counts"]
    }
    print(json.dumps({"pins": pins, "pins_sha256": pins_sha, "summary": summary}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

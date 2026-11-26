# AsterLoom · 星织

**Source-First Multimodal Scientific Evidence Extraction Framework**

A pipeline for turning scientific literature — text, JATS XML, tables, and figures — into structured, source-traced, and auditable evidence records.

面向科研文献的图文多模态、可溯源、可审计结构化抽取框架：把论文中的实验事实（正文、JATS XML、表格与图表）转化为可复核的结构化记录。

## What it is / 定位

AsterLoom (星织) is built around a simple rule: **every structured claim should be replayable from an author-original source**. The default mode is offline. Networked or paid model calls are opt-in, must be explicitly authorized, and can be recorded in a cost ledger.

核心原则是：**每条结构化断言都应当能回溯到作者原始来源并被复现**。默认离线运行；联网或付费模型调用需要显式授权，并可记入成本账本。

The installable Python package name remains `r031-evidence`. The user-facing command-line entry points are `r031` and `r031-evidence`.

当前可安装的 Python 包名仍为 `r031-evidence`，命令行入口为 `r031` 与 `r031-evidence`。

## Architecture / 架构

- **JATS XML intake** — parse author-original JATS (and related supplements) into a source-scoped intermediate representation, keeping SHA and license metadata with the record.
- **Figure digitization** — optional, calibrated reading of already-acquired figure assets (tick/axis calibration, panel classification). Journal figure binaries are **not** shipped in this repository; point the tools at your own data directory.
- **Evidence validation gates** — strict checks for identity, units, control arms, endpoint semantics, and source quotes before a record can be treated as reviewable evidence.
- **Offline-first runtime and cost ledger** — local SQLite workspaces, no implicit network; optional shared ledger paths are configured with environment variables rather than machine-specific hardcoded paths.

主要模块：

- **JATS XML 摄入**：解析作者原始 JATS（及补充材料），生成带来源范围的中间表示，并保留 SHA 与许可信息。
- **图表数字化**：对已经获取的图资产做可选的标定读取（刻度/坐标轴标定、panel 分类）。本仓库**不包含**期刊原文图片；请通过数据目录自行提供。
- **证据校验门禁**：在记录进入可评审状态前，严格检查对象身份、单位、对照臂、终点语义与源文引用。
- **离线优先与成本账本**：本地 SQLite 工作区，默认不联网；共享账本路径通过环境变量配置，而不是写死本机路径。

A sibling Worker package under `worker/` (`r031-source-adapters`) isolates JATS / spreadsheet adapters.

`worker/` 下的配套包 `r031-source-adapters` 用于隔离 JATS / 表格适配器。

## Installation / 安装

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .
```

Spreadsheet helpers are optional:

```bash
python -m pip install -e ".[excel]"
```

## Command-line interfaces / 命令行

```bash
r031 --help
r031-evidence --help
r031 --version
```

`r031` is the product-level CLI (workspace, local configuration, role-first workflows).
`r031-evidence` is the portable source-only evidence engine (ingest, retry, QA).

`r031` 是产品级命令行（工作区、本地配置、角色优先流程）。
`r031-evidence` 是可移植的源文证据引擎（摄入、重试、质检）。

Specialized scripts live under `scripts/`, for example:

- `scripts/asterloom_cli.py` — unified platform entry (profiles, demand compilation)
- `scripts/screen_oa_figures.py` — offline JATS figure screening
- `scripts/digitize_oa_expansion.py` — calibrated figure-record builder. The packaged seed is the synthetic fixture `examples/mock_paper/seed_panels.json`. Set `ASTERLOOM_SEED_PANELS` to your own panel JSON and `ASTERLOOM_FIGURE_DIR` to a local asset manifest. Journal figures are not included.

## Minimal offline example / 最小离线示例

This repository includes one **synthetic** JATS fixture. It is not a published paper.

本仓库提供一份**合成** JATS 样例，不是已发表论文。

```bash
python - <<'PY'
from pathlib import Path
from r031_evidence.oa_figure_screen import screen_xml

xml = Path("examples/mock_paper/author.xml")
row = screen_xml("PMC00000000", xml)
print("title:", row.get("title"))
print("strong ATP-content figures:", row.get("n_strong_atp_content_figures"))
for fig in row.get("figures") or []:
    if fig.get("strong_atp_content"):
        print("-", fig.get("label"), fig.get("kind"))
PY
```

To scan a local corpus, set a data directory instead of hardcoding a machine path:

若要筛选本地语料，请设置数据目录，不要把本机绝对路径写进代码：

```bash
export ASTERLOOM_DATA_DIR=/path/to/your/acquired-jats
export ASTERLOOM_OUTPUT_DIR=/path/to/output
python scripts/screen_oa_figures.py --out-dir "$ASTERLOOM_OUTPUT_DIR"
```

Useful environment variables / 常用环境变量：

| Variable | Purpose |
| --- | --- |
| `ASTERLOOM_DATA_DIR` | Root(s) of acquired author XML (use `os.pathsep` for multiple roots) |
| `ASTERLOOM_FIGURE_DIR` | Optional figure-asset manifests (binaries not included) |
| `ASTERLOOM_OUTPUT_DIR` | Writable output directory |
| `ASTERLOOM_DEEPSEEK_BUDGET_DIR` | Shared budget directory, when a ledger is used |
| `ASTERLOOM_CANONICAL_LEDGER` | Absolute path of the canonical shared ledger file, when required |

## Tests / 测试

The default suite is offline and does not depend on host-specific directories:

默认测试集是离线的，不依赖某台机器上的本地目录：

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

## Data that is not in this repository / 本仓库不包含的数据

- Journal figure images extracted from publications, and real figure-calibration seeds (pixel ticks, author quotes, journal filenames)
- Unpublished or project-specific compound screens, PubChem supplement snapshots, and monomer research patches
- Model weights (`.jsonl`, `.joblib`), local SQLite ledgers, and production workspaces
- Private operator notes, internal handoff documents, and preview-release packaging scripts

`src/r031_evidence/data/ch1_natural_parent_registry_v1.json` is a small public-domain example (common natural products) used by offline tests. It is not a screened candidate list. `src/r031_evidence/data/atp_content_external_structure_aliases.json` is a five-compound public PubChem name crosswalk for the offline identity checks. SQL files under `data/` are schema, not datasets.

期刊原文图片、真实图表标定种子、未发表或项目专用化合物清单、PubChem 补充快照、单体研究补丁、模型权重、本地账本、生产工作区、内部交接文档和预览打包脚本均不在本仓库中。`ch1_natural_parent_registry_v1.json` 只是离线测试用的小型公有领域示例，不是筛选后的候选库。

## License / 许可

[MIT](LICENSE)

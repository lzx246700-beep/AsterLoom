# Contributing to AsterLoom · 星织

Thank you for considering a contribution. This repository is a **source-first**, offline-friendly scientific evidence extraction framework. Please keep contributions auditable, reproducible, and free of private data.

感谢你考虑贡献。本仓库是一套**源文优先**、可离线运行的科学证据抽取框架。请保证改动可追溯、可复现，并且不要提交私有数据。

## Ground rules / 基本原则

1. **Do not commit secrets** — API keys, tokens, `.env` files, credentials, or machine-specific absolute paths.
2. **Do not commit private or copyrighted corpora** — raw journal figures, figure-calibration seeds taken from published panels, paywalled PDFs, unpublished candidate lists, PubChem supplement snapshots, `.jsonl` training dumps, `.joblib` weights, or `.sqlite` ledgers. Offline tests should use the synthetic fixtures under `examples/mock_paper/` or a small public-domain example.
3. **Prefer fixtures** — add a small synthetic XML/JSON fixture under `examples/` or `tests/fixtures/` instead of pointing tests at a local disk path.
4. **Keep the default offline** — paid or networked model calls must be explicit, documented, and ledger-backed. Tests must pass without network access and without host-specific directories.
5. **Configure data with environment variables**, not hardcoded paths:
   - `ASTERLOOM_DATA_DIR` — acquired author JATS XML and optional assets
   - `ASTERLOOM_FIGURE_DIR` — optional figure-asset manifests (binaries are not in this repo)
   - `ASTERLOOM_OUTPUT_DIR` — writable output directory
   - `ASTERLOOM_DEEPSEEK_BUDGET_DIR` / `ASTERLOOM_CANONICAL_LEDGER` — shared cost ledger, when used

## Development setup / 开发环境

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Optional extras: `python -m pip install -e '.[excel]'` for spreadsheet I/O.

Python 3.11+ is required. Some optional modules (OpenCV, scikit-learn, RDKit, pydantic, pydantic-ai, langextract, and so on) are imported only by specialized pipelines and are not required for the default offline test suite.

## Tests / 测试

```bash
python -m unittest discover -s tests -p 'test_*.py'
r031 --help
r031-evidence --help
```

All retained tests must pass offline. If a change needs a new fixture, keep it synthetic and tiny.

## Pull requests / 提交说明

- Write a concise PR description: what changed, why, and how it was tested.
- Do not include generated caches (`__pycache__`, `.venv`, `dist`, `.egg-info`).
- If you add a dependency, put it in `pyproject.toml` only when the default install or test suite actually needs it; otherwise use an optional extra and document it.

## License / 许可

Contributions are accepted under the MIT License (`LICENSE`).

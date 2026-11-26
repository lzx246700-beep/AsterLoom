"""Pluggable Task Profile pipeline registry for AsterLoom.

Unifies domain-specific literature mining modules behind a thin, backward-compatible
4-hook interface:
  1. build_queries(...)    -> list[str]
  2. triage_paper(...)     -> dict
  3. extract_records(...)  -> list[dict]
  4. export_dataset(...)   -> dict

Existing Chapter 1 modules are wrapped without modifying their public APIs;
Chapter 2 (bioactive_polymer_v1 / Model B1 & B2) plugs in natively.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from r031_evidence.r23_profiles import load as load_profile_manifest


@dataclass(frozen=True)
class TaskPipelinePlugin:
    """Standard pluggable pipeline contract bound to a registered Task Profile."""

    profile_id: str
    chapter: str
    description: str
    build_queries_fn: Callable[..., list[str]]
    triage_paper_fn: Callable[..., dict[str, Any]]
    extract_records_fn: Callable[..., list[dict[str, Any]]]
    export_dataset_fn: Callable[..., dict[str, Any]]

    def load_manifest(self) -> dict[str, Any]:
        return load_profile_manifest(self.profile_id).manifest

    def build_queries(self, **kwargs: Any) -> list[str]:
        return self.build_queries_fn(**kwargs)

    def triage_paper(self, xml_path: Path, **kwargs: Any) -> dict[str, Any]:
        return self.triage_paper_fn(xml_path, **kwargs)

    def extract_records(self, xml_path: Path, **kwargs: Any) -> list[dict[str, Any]]:
        return self.extract_records_fn(xml_path, **kwargs)

    def export_dataset(self, out_dir: Path, **kwargs: Any) -> dict[str, Any]:
        return self.export_dataset_fn(out_dir=out_dir, **kwargs)


_PLUGIN_REGISTRY: dict[str, TaskPipelinePlugin] = {}
_DEFAULTS_LOADED = False


def register_plugin(plugin: TaskPipelinePlugin) -> None:
    _PLUGIN_REGISTRY[plugin.profile_id] = plugin


def _ensure_builtin_plugins() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    _DEFAULTS_LOADED = True

    # Chapter 1 adapter (zero changes to existing Ch1 modules)
    def _ch1_build_queries(**kwargs: Any) -> list[str]:
        from r031_evidence.ch1_e2e_pipeline import build_targeted_epmc_queries

        return build_targeted_epmc_queries(**kwargs)

    def _ch1_triage(xml_path: Path, **kwargs: Any) -> dict[str, Any]:
        from r031_evidence.ch1_pre_llm_triage import triage_jats_paper

        return triage_jats_paper(xml_path, **kwargs)

    def _ch1_extract(xml_path: Path, **kwargs: Any) -> list[dict[str, Any]]:
        from r031_evidence.ch1_dataset_builder import load_experiment_evidence_rows
        from r031_evidence.ch1_parent_registry import load_registry

        records = kwargs.get("records") or load_registry()
        return load_experiment_evidence_rows([xml_path], records)

    def _ch1_export(out_dir: Path, **kwargs: Any) -> dict[str, Any]:
        from r031_evidence.ch1_dataset_builder import build_ch1_dataset

        return build_ch1_dataset(out_dir=out_dir, **kwargs)

    register_plugin(
        TaskPipelinePlugin(
            profile_id="small_molecule_atp_content_v1",
            chapter="ch1_model_a",
            description="Natural/endogenous small molecules -> mammalian intracellular ATP_CONTENT (Model A).",
            build_queries_fn=_ch1_build_queries,
            triage_paper_fn=_ch1_triage,
            extract_records_fn=_ch1_extract,
            export_dataset_fn=_ch1_export,
        )
    )

    # Chapter 2 plugin (native bioactive_polymer_v1 / Model B1 & B2)
    def _ch2_build_queries(**kwargs: Any) -> list[str]:
        from r031_evidence.ch2_e2e_pipeline import build_ch2_epmc_queries

        return build_ch2_epmc_queries(**kwargs)

    def _ch2_triage(xml_path: Path, **kwargs: Any) -> dict[str, Any]:
        from r031_evidence.ch2_pre_llm_triage import triage_ch2_jats_paper

        return triage_ch2_jats_paper(xml_path, **kwargs)

    def _ch2_extract(xml_path: Path, **kwargs: Any) -> list[dict[str, Any]]:
        from r031_evidence.polymer_reaction_profile import extract_polymer_events_from_jats

        return extract_polymer_events_from_jats(xml_path, **kwargs)

    def _ch2_export(out_dir: Path, **kwargs: Any) -> dict[str, Any]:
        from r031_evidence.ch2_dataset_builder import build_ch2_dataset

        return build_ch2_dataset(out_dir=out_dir, **kwargs)

    register_plugin(
        TaskPipelinePlugin(
            profile_id="bioactive_polymer_v1",
            chapter="ch2_model_b",
            description="Natural bioactive monomers -> covalent polymerization potential & conditioned outcomes (Model B1 & B2).",
            build_queries_fn=_ch2_build_queries,
            triage_paper_fn=_ch2_triage,
            extract_records_fn=_ch2_extract,
            export_dataset_fn=_ch2_export,
        )
    )


def get_plugin(profile_id: str) -> TaskPipelinePlugin:
    _ensure_builtin_plugins()
    if profile_id not in _PLUGIN_REGISTRY:
        raise KeyError(f"TASK_PLUGIN_NOT_REGISTERED:{profile_id}")
    return _PLUGIN_REGISTRY[profile_id]


def list_plugins() -> list[dict[str, Any]]:
    _ensure_builtin_plugins()
    return [
        {
            "profile_id": p.profile_id,
            "chapter": p.chapter,
            "description": p.description,
        }
        for p in sorted(_PLUGIN_REGISTRY.values(), key=lambda x: x.profile_id)
    ]


def register_demand_compiled_plugin(compiled_plan: dict[str, Any]) -> TaskPipelinePlugin:
    """Dynamically register any reverse-compiled demand plan (`ml_dataset` / `lit_review` / `idea_discovery`) as a plugin."""
    _ensure_builtin_plugins()
    from r031_evidence.platform_core import execute_compiled_funnel, slice_text_with_keywords

    profile_id = compiled_plan["profile_id"]
    mode = compiled_plan["mode"]
    spec = compiled_plan["demand_spec"]
    stage1 = compiled_plan["funnel_stages"]["stage1"]
    slicers = stage1["keyword_slicers"]

    def _build_queries(**kwargs: Any) -> list[str]:
        return list(stage1["epmc_boolean_queries"])

    def _triage(xml_path: Path, **kwargs: Any) -> dict[str, Any]:
        text = xml_path.read_text(encoding="utf-8", errors="ignore") if xml_path.is_file() else str(kwargs.get("text") or "")
        return slice_text_with_keywords(
            text=text,
            entity_keywords=slicers["entity_must_match_any"],
            metric_keywords=slicers["metric_must_match_any"],
            trap_keywords=slicers["hard_exclude_trap_patterns"],
        )

    def _extract(xml_path: Path, **kwargs: Any) -> list[dict[str, Any]]:
        report = execute_compiled_funnel(compiled_plan)
        return list(report["records"])

    def _export(out_dir: Path, **kwargs: Any) -> dict[str, Any]:
        return execute_compiled_funnel(compiled_plan, out_dir=out_dir)

    plugin = TaskPipelinePlugin(
        profile_id=profile_id,
        chapter=f"reverse_{mode}",
        description=f"[{compiled_plan['mode_label']}] {spec['title']} ({spec['domain']})",
        build_queries_fn=_build_queries,
        triage_paper_fn=_triage,
        extract_records_fn=_extract,
        export_dataset_fn=_export,
    )
    register_plugin(plugin)
    return plugin


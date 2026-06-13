"""consolidate_cluster_names — unify HDBSCAN + repechage cluster names.

Phase B of the redesigned newsagent:select. A single LLM call sees the full
combined list of cluster names (HDBSCAN-named plus phase-A repechage proposals)
together with sample headlines for each, and returns:
  - `final_names`: the consolidated short list of section names
  - `mapping`:   each input `cluster_id` -> final name OR the literal "Other"

Phase C then uses `final_names` (with sample headlines pulled from the
mapping) as the reassignment target list. Clusters dissolved to "Other" lose
their identity entirely; their member items are reassigned by phase C just
like every other headline.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class ClusterToConsolidate(BaseModel):
    cluster_id: str  # opaque tag (e.g. "hdbscan-5" or "repechage-2")
    name: str
    sample_headlines: List[str]


class ConsolidateClusterNamesInput(BaseModel):
    clusters: List[ClusterToConsolidate]

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class ClusterMapping(BaseModel):
    cluster_id: str
    final_name: str  # one of `final_names`, or the literal "Other"


class ConsolidateClusterNamesOutput(BaseModel):
    final_names: List[str]
    mapping: List[ClusterMapping]


_SYSTEM = """\
You are a newsletter editor producing the final section list for a daily AI
news roundup.

You will receive a JSON list of candidate clusters, each with:
- cluster_id (string — opaque tag)
- name        (a working title)
- sample_headlines

Many of these clusters cover the SAME story or theme under different names —
HDBSCAN-named clusters and LLM-mined clusters from a separate pass have both
been merged into this list. Your job is to produce ONE consolidated list of
final section names that captures every meaningful theme exactly once, then
map each input cluster to either:
  - one of those final names, OR
  - the literal string "Other" (if the cluster is too small / off-theme /
    duplicative of nothing important).

Guidelines:
- Aim for 8 to 14 final named sections for a typical 200-250 headline run
  (the downstream newsletter caps at 15 sections including the "Other" sink,
  so more than 14 named sections forces merges later).
- Each final name should be 4-8 words, descriptive, not generic.
- Use title case for final names (Capitalize Each Main Word).
- Merge near-duplicate themes (e.g. "AI Chip Wars" + "Semiconductor Race"
  -> one final name).
- Do not list "Other" in `final_names`; it is reserved as a sink in `mapping`.

Return ONLY a JSON object matching the provided schema. `mapping` MUST contain
exactly one entry per input `cluster_id`."""

_USER = """\
Candidate clusters (JSON):
{clusters_json}

Return JSON matching the schema."""


CONSOLIDATE_CLUSTER_NAMES = PromptConfig(
    name="consolidate_cluster_names",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ConsolidateClusterNamesInput,
    output_schema=ConsolidateClusterNamesOutput,
    default_engine="subagent",
    reasoning_effort=5,
)

register_prompt(CONSOLIDATE_CLUSTER_NAMES)

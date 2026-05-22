"""extract_noise_clusters — mine HDBSCAN noise headlines for new themes.

Phase A of the redesigned newsagent:select. Each batch holds up to ~50 noise
headlines (id + title + short_summary). The model proposes groups of 2+
headlines sharing a theme; remaining items are returned as `unclustered_ids`
and feed into phase B's "Other" bucket via phase C reassignment.

Cross-batch deduplication of proposed cluster names is delegated to phase B
(consolidate_cluster_names); this prompt does not need global context.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class NoiseHeadlineToCluster(BaseModel):
    id: str
    title: str
    short_summary: str


class ExtractNoiseClustersInput(BaseModel):
    headlines: List[NoiseHeadlineToCluster]

    @computed_field
    @property
    def headlines_json(self) -> str:
        return json.dumps([h.model_dump() for h in self.headlines], indent=2)


class ProposedCluster(BaseModel):
    name: str
    member_ids: List[str]


class ExtractNoiseClustersOutput(BaseModel):
    proposed_clusters: List[ProposedCluster]
    unclustered_ids: List[str]


_SYSTEM = """\
You are a newsletter editor mining a pile of unclustered news headlines for
thematic groups.

You will receive a JSON list of headlines, each with:
- id (string)
- title
- short_summary

Find groups of headlines that share a clear theme. Each proposed cluster MUST
have at least 2 member headlines. Group only headlines that meaningfully
belong together — do not force singletons into a group just to use them.

For each proposed cluster, write a concise descriptive name (4-8 words) that
captures the shared theme. Do not use generic phrases like "AI News" or
"Tech Updates". Each name must be unique within your response.

Headlines that do not fit any group of 2+ headlines should go into
`unclustered_ids`.

Return ONLY a JSON object matching the provided schema. Every input id MUST
appear exactly once across `proposed_clusters[*].member_ids` and
`unclustered_ids` combined (no duplicates, no extras, no missing ids)."""

_USER = """\
Headlines to cluster (JSON):
{headlines_json}

Return JSON matching the schema."""


EXTRACT_NOISE_CLUSTERS = PromptConfig(
    name="extract_noise_clusters",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ExtractNoiseClustersInput,
    output_schema=ExtractNoiseClustersOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(EXTRACT_NOISE_CLUSTERS)

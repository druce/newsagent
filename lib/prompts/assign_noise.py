"""assign_noise — assign a noise headline to an existing cluster.

New prompt (no legacy equivalent): used in newsagent:select to assign HDBSCAN noise
points (cluster_id=-1) to the nearest thematically relevant cluster, create a
new cluster, or discard the headline.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class ClusterDescriptor(BaseModel):
    id: str
    name: str
    sample_headlines: List[str]


class AssignNoiseInput(BaseModel):
    headline_title: str
    headline_summary: str
    clusters: List[ClusterDescriptor]
    allow_new: bool = True

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters])


class AssignNoiseOutput(BaseModel):
    assignment: str  # cluster id, "new", or "none"


_SYSTEM = """\
You are an editor assigning an unclustered news headline to one of the day's news clusters.

You will receive:
- One headline (title + summary)
- A list of existing clusters with names and sample headlines

For each headline, decide:
- The id of the best-matching existing cluster, OR
- "new" if the headline is significantly different from all existing clusters but plausibly worth its own section
- "none" if the headline is unrelated to the newsletter's topic

Return only a JSON object matching the provided schema with field "assignment"."""

_USER = """\
Headline:
title: {headline_title}
summary: {headline_summary}

Existing clusters (JSON):
{clusters_json}

Allow new cluster: {allow_new}

Return the cluster id, "new", or "none" in the provided schema."""


ASSIGN_NOISE = PromptConfig(
    name="assign_noise",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=AssignNoiseInput,
    output_schema=AssignNoiseOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(ASSIGN_NOISE)

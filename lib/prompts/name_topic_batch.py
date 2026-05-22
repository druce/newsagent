"""name_topic_batch — name multiple article clusters in a single Agent call.

Batched variant of NAME_TOPIC. Used by newsagent:cluster in --prepare-batches
mode so one Haiku Agent can name all of a session's non-noise clusters in one
round-trip instead of N separate calls.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class ClusterToName(BaseModel):
    cluster_id: str
    entities: str
    headlines: str


class NameTopicBatchInput(BaseModel):
    clusters: List[ClusterToName]

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class ClusterName(BaseModel):
    cluster_id: str
    name: str


class NameTopicBatchOutput(BaseModel):
    names: List[ClusterName]


_SYSTEM = """\
You are a newsletter editor naming several topic sections at once.

You will receive a JSON list of clusters, each with:
- cluster_id (string)
- entities (comma-separated central entities)
- headlines (newline-separated sample headlines, highest-rated first)

For EACH cluster, write a concise descriptive section title (4-8 words) that
captures the main story or theme. Do not use generic phrases like "AI News"
or "Tech Update". Each title must be unique.

Return ONLY a JSON object matching the provided schema, with exactly one entry
per input cluster_id (no duplicates, no extras)."""

_USER = """\
Clusters to name (JSON):
{clusters_json}

Return JSON matching the schema."""


NAME_TOPIC_BATCH = PromptConfig(
    name="name_topic_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=NameTopicBatchInput,
    output_schema=NameTopicBatchOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(NAME_TOPIC_BATCH)

"""assign_noise_batch — assign multiple noise headlines to clusters in one call.

Batched variant of ASSIGN_NOISE. Used by newsagent:select in --prepare-batches
mode so one Haiku Agent handles up to N (default 25) noise-point assignments
in one round-trip.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts.assign_noise import ClusterDescriptor


class NoiseHeadline(BaseModel):
    id: str
    title: str
    summary: str


class AssignNoiseBatchInput(BaseModel):
    headlines: List[NoiseHeadline]
    clusters: List[ClusterDescriptor]
    allow_new: bool = True

    @computed_field
    @property
    def headlines_json(self) -> str:
        return json.dumps([h.model_dump() for h in self.headlines], indent=2)

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class NoiseAssignment(BaseModel):
    id: str
    assignment: str  # cluster id, "new", or "none"


class AssignNoiseBatchOutput(BaseModel):
    assignments: List[NoiseAssignment]


_SYSTEM = """\
You are an editor assigning unclustered news headlines to one of today's news clusters.

You will receive:
- A JSON list of headlines (each with id, title, summary)
- A JSON list of existing clusters (each with id, name, sample_headlines)

For EACH headline, decide:
- The id of the best-matching existing cluster, OR
- "new" if the headline is significantly different from all existing clusters
  but plausibly worth its own section (only if allow_new is true), OR
- "none" if the headline is unrelated to the newsletter's topic

Return ONLY a JSON object matching the provided schema, with exactly one
entry per input id (no duplicates, no extras)."""

_USER = """\
Headlines to assign (JSON):
{headlines_json}

Existing clusters (JSON):
{clusters_json}

Allow new cluster: {allow_new}

Return JSON matching the schema."""


ASSIGN_NOISE_BATCH = PromptConfig(
    name="assign_noise_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=AssignNoiseBatchInput,
    output_schema=AssignNoiseBatchOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(ASSIGN_NOISE_BATCH)

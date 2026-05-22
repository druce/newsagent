"""reassign_to_clusters — assign every headline to a consolidated cluster name.

Phase C of the redesigned newsagent:select. Unlike the prior `assign_noise`
flow (which targeted only HDBSCAN noise), this prompt sees every rated
headline and the consolidated cluster name list from phase B; for each
headline it returns either a final name or the literal "Other".

The PromptConfig name is `reassign_to_clusters` (the module filename is kept
as `assign_noise_batch.py` for git history continuity).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class HeadlineToReassign(BaseModel):
    id: str
    title: str
    short_summary: str


class ClusterChoice(BaseModel):
    name: str
    sample_headlines: List[str]


class ReassignToClustersInput(BaseModel):
    headlines: List[HeadlineToReassign]
    clusters: List[ClusterChoice]

    @computed_field
    @property
    def headlines_json(self) -> str:
        return json.dumps([h.model_dump() for h in self.headlines], indent=2)

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class HeadlineAssignment(BaseModel):
    id: str
    assignment: str  # one of clusters[*].name, or the literal "Other"


class ReassignToClustersOutput(BaseModel):
    assignments: List[HeadlineAssignment]


_SYSTEM = """\
You are a newsletter editor assigning every headline of the day to one of the
final newsletter sections.

You will receive:
- A JSON list of headlines (id, title, short_summary)
- A JSON list of section choices (each with name + sample_headlines)

For EACH headline, return:
- The exact `name` of the best-matching section, OR
- The literal string "Other" if the headline does not fit any section
  meaningfully.

Match decisively — every headline belongs somewhere. Use "Other" only when
nothing else is a reasonable fit. Do not invent new section names beyond the
provided list and "Other".

Return ONLY a JSON object matching the provided schema, with exactly one
entry per input id (no duplicates, no extras)."""

_USER = """\
Headlines to assign (JSON):
{headlines_json}

Section choices (JSON):
{clusters_json}

Return JSON matching the schema."""


REASSIGN_TO_CLUSTERS = PromptConfig(
    name="reassign_to_clusters",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ReassignToClustersInput,
    output_schema=ReassignToClustersOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(REASSIGN_TO_CLUSTERS)

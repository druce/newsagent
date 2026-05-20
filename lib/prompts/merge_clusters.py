"""merge_clusters — decide whether two clusters should be merged.

New prompt (no legacy equivalent): used in newsagent:select to deduplicate clusters
that cover the same story or theme before MMR selection.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class ClusterPair(BaseModel):
    id: str
    name: str
    top_headlines: List[str]


class MergeClustersInput(BaseModel):
    a: ClusterPair
    b: ClusterPair

    @computed_field
    @property
    def pair_json(self) -> str:
        return json.dumps({"a": self.a.model_dump(), "b": self.b.model_dump()})


class MergeClustersOutput(BaseModel):
    merge: bool
    merged_name: Optional[str] = None  # if merge=True, the better name for the combined cluster


_SYSTEM = """\
You are an editor deciding whether two news clusters cover the same story and should be merged into one newsletter section.

You will receive two clusters, each with a name and 3-5 representative headlines.

Decide:
- merge=true if both clusters cover substantively the same story or theme. Provide merged_name (the better of the two names, or a new title that better captures both).
- merge=false if they cover distinct stories that deserve separate sections.

Return only a JSON object matching the provided schema."""

_USER = """\
Two clusters to compare:
{pair_json}

Should they be merged? Return JSON matching the schema."""


MERGE_CLUSTERS = PromptConfig(
    name="merge_clusters",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=MergeClustersInput,
    output_schema=MergeClustersOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(MERGE_CLUSTERS)

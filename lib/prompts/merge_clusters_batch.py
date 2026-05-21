"""merge_clusters_batch — decide multiple merge candidates in a single call.

Batched variant of MERGE_CLUSTERS. Used by newsagent:select in --prepare-batches
mode so one Haiku Agent decides all candidate near-duplicate pairs at once.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts.merge_clusters import ClusterPair


class PairToDecide(BaseModel):
    pair_id: str
    a: ClusterPair
    b: ClusterPair


class MergeClustersBatchInput(BaseModel):
    pairs: List[PairToDecide]

    @computed_field
    @property
    def pairs_json(self) -> str:
        return json.dumps(
            [{"pair_id": p.pair_id, "a": p.a.model_dump(), "b": p.b.model_dump()}
             for p in self.pairs],
            indent=2,
        )


class MergeDecision(BaseModel):
    pair_id: str
    merge: bool
    merged_name: Optional[str] = None


class MergeClustersBatchOutput(BaseModel):
    decisions: List[MergeDecision]


_SYSTEM = """\
You are an editor deciding whether candidate cluster pairs cover the same story.

You will receive a JSON list of candidate pairs. Each pair has a pair_id and
two clusters (a, b), each with id, name, and top_headlines.

For EACH pair, decide:
- merge=true if both clusters cover substantively the same story or theme.
  Provide merged_name (the better of the two names, or a new title that
  better captures both).
- merge=false if they cover distinct stories that deserve separate sections.
  merged_name may be null.

Return ONLY a JSON object matching the provided schema, with exactly one
decision per input pair_id."""

_USER = """\
Pairs to decide (JSON):
{pairs_json}

Return JSON matching the schema."""


MERGE_CLUSTERS_BATCH = PromptConfig(
    name="merge_clusters_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=MergeClustersBatchInput,
    output_schema=MergeClustersBatchOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(MERGE_CLUSTERS_BATCH)

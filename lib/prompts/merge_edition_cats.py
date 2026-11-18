"""merge_edition_cats — first pass of newsagent:edition: one cluster per curated story.

The curate window spans several pipeline sessions (normally the prior 18:00 run and
the 07:00 run), and each session's `select` names its own categories, so one topic
arrives under several names. This pass assigns every story a merged working cluster
before the section writers run, and flags likely duplicate stories (never drops them).
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts._dispatch_schemas import EditionMerge
from lib.utilities import _strip_no_content


class MergeEditionCatsInput(BaseModel):
    stories: List[Dict[str, Any]]  # {id, cat, session_id, title, short_summary, rating}

    @computed_field
    @property
    def stories_text(self) -> str:
        """Stories grouped under `## <cat>  [run <session>]`, one `- id [rating] text`
        line each. Plain lines, not JSON: ~180 stories must stay well under the
        subagent Read cap (~60KB); indented JSON was ~71KB."""
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for st in self.stories:
            groups.setdefault((st.get("cat") or "Other", st.get("session_id") or "?"),
                              []).append(st)
        out: List[str] = []
        for (cat, sid), members in groups.items():
            out.append(f"## {cat}  [run {sid}, {len(members)}]")
            for st in members:
                r = st.get("rating")
                text = _strip_no_content(st.get("short_summary")) or st.get("title") or ""
                out.append(f"- {st['id']} [{'-' if r is None else f'{r:.1f}'}] {text}")
            out.append("")
        return "\n".join(out).rstrip()


_SYSTEM = """\
You are the managing editor of Skynet and Chill, a daily AI newsletter. Before the items are written, you group today's curated stories into the issue's clusters."""

_USER = """\
CONTEXT
The editor has curated these stories from two or more pipeline runs. Each run named its own categories independently, so the same topic often appears under different names (for example "Jensen Huang Downfall and Reinstatement Debate" in one run and "Jensen Huang Ouster and Nvidia Leadership Crisis" in the next). One writer will draft each cluster you return, seeing only that cluster, so a topic split across clusters gets drafted twice, with repeated context and clashing jokes.

TASK
1. Assign every story to exactly one cluster.
   - Merge categories that cover the same topic, across runs.
   - Judge each story on its own: move a story out of "Other", or out of a category where it does not fit, into the cluster it belongs with. A story may also start a new cluster.
   - Keep a distinct sub-story separate when it would read as its own section (e.g. a chip-export probe vs. a CEO succession fight at the same company), unless it has only one or two stories that read naturally with the larger cluster.
   - Aim for about 15–25 clusters. Single-story clusters are fine. The day's dominant topic may have 3–10 stories; avoid clusters over 12 unless the stories are truly inseparable.
   - Cluster names are short working labels (2–6 words, plain, not jokes); they are not printed. Use "Other" only for stories that fit nowhere, and keep it small.
2. List likely duplicates: groups of stories that report the same specific news, so that a reader who read one would learn nothing new from the other (e.g. two outlets on the same announcement, or the same event picked by both runs). Follow-ups with new facts, analysis, explainers and reactions are NOT duplicates. Only flag; nothing is dropped. Give a short reason.
3. Optional notes: anything the editor should know about the grouping (at most 3, one line each).

STORIES
Grouped under `## <the run's category>  [run <session id>, <count>]`; each line is `- <id> [<rating, higher = more important>] <one-line summary>`.

{stories_text}

OUTPUT
Return ONLY a JSON object:
{{"assignments": [{{"id": int, "cat": str}}, ...], "duplicates": [{{"ids": [int, int, ...], "reason": str}}, ...], "notes": [str, ...]}}
Every input `id` appears in `assignments` exactly once. `duplicates` may be empty."""


MERGE_EDITION_CATS = PromptConfig(
    name="merge_edition_cats",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=MergeEditionCatsInput,
    output_schema=EditionMerge,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(MERGE_EDITION_CATS)


def render_user_prompt(inp: MergeEditionCatsInput) -> str:
    return _USER.format(**inp.model_dump())

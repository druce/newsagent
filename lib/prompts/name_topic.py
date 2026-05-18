"""name_topic — name an article cluster.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:1365 (NAME_TOPIC).
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt


class NameTopicInput(BaseModel):
    entities: str
    headlines: str


class NameTopicOutput(BaseModel):
    title: str


_SYSTEM = """\
You are a newsletter editor naming a topic section. You will receive the central entities and a sample of today's headlines for a news cluster. Write a concise, descriptive section title (4-8 words) that captures the main story or theme. Do not use generic phrases like "AI News" or "Tech Update". Return only the title, nothing else."""

_USER = """\
Central entities: {entities}

Sample headlines:
{headlines}

Section title:"""


NAME_TOPIC = PromptConfig(
    name="name_topic",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=NameTopicInput,
    output_schema=NameTopicOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(NAME_TOPIC)

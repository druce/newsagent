"""filter_urls — classify headlines as AI-related.

Originally ported from ~/projects/OpenAIAgentsSDK/prompts.py:43 (FILTER_URLS).
Rewritten 2026-11-11 from the eval candidate evals/prompts/filter_urls_v2.py: the
user's editorial policy for borderline topics (data centers, chips, stock pieces,
roundups, wearables, surveillance, deepfakes) and an optional feed excerpt per item.
Gold set: evals/sets/filter_urls.jsonl.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class HeadlineItem(BaseModel):
    id: str
    title: str
    # feed summary/dek; omitted from the prompt when absent
    excerpt: Optional[str] = None


class FilterUrlsInput(BaseModel):
    items: List[HeadlineItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump(exclude_none=True) for item in self.items], ensure_ascii=False)


class HeadlineClassification(BaseModel):
    id: str
    is_ai: bool


class FilterUrlsOutput(BaseModel):
    classifications: List[HeadlineClassification]


_SYSTEM = """\
You are a content-classification assistant that labels news items as AI-related or not.
You will receive a list of JSON objects with fields "id", "title" and, when the feed provides one, "excerpt" (the feed's short summary of the article).
Return a single JSON object matching the provided schema exactly, with no surrounding text or code fences — the result is parsed directly as JSON.
Include exactly one element per input item, with the same id, in the same order as the input; results are matched back to headlines by id and a missing id fails the batch.
"""

_USER = """\
Classify every item below. Judge what the article is about, using the title and the excerpt when present.

AI-related if the article is about:
- Core AI technologies: machine learning, neural / deep / transformer networks
- AI applications: computer vision, NLP, autonomous driving, generative media
- AI hardware and infrastructure (AI accelerators/GPUs, AI compute build-outs)
- Companies or labs known for AI: OpenAI, DeepMind, Anthropic, xAI, NVIDIA, etc.
- AI models & products: ChatGPT, Gemini, Claude, Grok, DeepSeek, etc.
- New AI products and AI integration into existing products/services
- AI policy / ethics / safety / regulation / analysis
- Research results related to AI
- AI industry figures (Sam Altman, Demis Hassabis, etc.)
- AI market and business developments, funding rounds, partnerships centered on AI
- Automation and robotics, including drones, military autonomy, and society, culture and history pieces about automation
- Data centers when they are the main subject (construction, siting, power, water, emissions, local opposition, regulation, financing, data-center companies and REITs, and the data-center boom itself: its supply chains, firms built around AI data-center infrastructure)
- AI wearables and smart glasses
- AI surveillance and biometrics: Flock and license-plate readers, facial recognition, face-based age verification
- Fake or manipulated images and video, and fact-checks of them (presumed deepfakes)
- Any other news where AI is the subject or a main thread

NOT AI-related, unless AI is the subject or a main thread:
- General Big Tech, market or stock pieces (indexes, earnings roundups, IPOs)
- Crypto, ordinary software and SaaS, non-AI hardware and devices.

NOT AI-related even when AI is mentioned:
- News roundups / compilation stories (newsletters, "Plus, …" digests, multi-topic briefings) that are only partly about AI
- Pure stock recommendations or forecasts not of interest to tech readers (price targets, "how to play X stock", buy/sell pieces, routine earnings commentary), even for AI-first companies like NVIDIA: routine Nvidia earnings commentary, forecasts and stock moves are NOT AI-related. Significant AI product news (smart glasses, AI models) is AI-related even when framed as a stock piece.
- A single non-AI, non-data-center company's earnings or guidance that merely cite data-center demand (utilities, telecom gear, electrical equipment, real-estate brokers)
- A passing mention such as "amid the AI boom" does not make an article AI-related.

Chips, fabs, semiconductor and memory business (earnings, equipment, capacity, export controls, chip stocks) are NOT AI-related unless there is an AI angle e.g: AI chips/accelerators/GPUs, chips competing with Nvidia (e.g. Huawei's leading-edge AI chips), an AI-first company (e.g. NVIDIA), AI demand as the story (e.g. AI-driven memory shortages and prices, AI-driven fab expansion), or an AI application. A chip story with no stated AI link (e.g. a new fab with no AI mention) is NOT AI-related.
Retrospective or historical tech stories are NOT AI-related unless they are about AI or automation.

Input:
{input_text}"""


FILTER_URLS = PromptConfig(
    name="filter_urls",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=FilterUrlsInput,
    output_schema=FilterUrlsOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(FILTER_URLS)

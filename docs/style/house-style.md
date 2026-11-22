# House style — Skynet and Chill

Derived from 50 published issues (Sep 8 – Oct 26, 2026) and four machine-digest vs. published comparisons. Where practice is inconsistent, the rule picks the majority form and marks it **[normalize]**. Kickers and subtitles are calibrated in gold-headlines.md.

## 1. Issue anatomy

- **Subject / H1:** `AI Reading for <Weekday> <Month> <D>` (e.g. "AI Reading for Monday October 26"). Fixed template.
- **Subtitle** (preheader and line under the H1): 2–8 words, riffs on the lead theme. Checked before send; it is the first text readers see in the inbox.
- **Body:** 15–30 clusters separated by `---` (markdown horizontal rule). Each cluster opens with a `## ` section title (1–7 words, calibrated in gold-headlines.md "Section titles"; voice per style-guide.md), then usually an image (captions left empty), then its items. 40–55 items per issue; 2–7 items per cluster; move a single-item cluster's item into a relevant cluster or the final catchall cluster.
- **Order:** lead cluster = the day's dominant story, 3–10 items, chosen by editorial weight, not digest order. Middle = markets/compute, policy, security, labor. Tail = culture, oddities, light items.
- **Embeds:** YouTube and X embeds render in plain text as `Youtube: <title> (url)` / `Tweet: (url)`. These are embeds, not errors.
- **Footer:** `Follow the latest AI headlines via druce.ai on Bluesky`.

## 2. Item format

```
[Optional kicker: Summary sentence](url) - _Source_
```

**Summary**
- One sentence, sentence case, present tense, active voice. Lead with the actor: "Meta releases…", "Jamie Dimon says…".
- **As simple as possible.** 10–20 words; hard cap 25 (the same cap as the pipeline's headlines). Count the words. Over the cap, cut a clause, not the specific.
- **One fact per item:** who did what, plus the one detail that makes it news. Drop backstory, second facts, "who previously…" / "which comes after…" modifiers, lists of three, and secondary names. The reader can click.
- Plain words a general reader knows; gloss or drop jargon. Short verbs: "cuts" not "announces a reduction in".
- One concrete specific, the most striking one: a number, a name, a date, a comparison. "Up to 70% of websites", "$124 billion", "from 27% to 9%". Not three.
- Attribution verbs: says, warns, finds, estimates, claims, alleges. Use "claims" for vendor benchmarks and unverified assertions.
- Never a second sentence. Prefer dropping the second fact; a semicolon joining two facts is a last resort and must still fit the cap.
- No terminal period. **[normalize]** — the machine summaries omit it; hand-typed items often add one.
- Not allowed in the summary: the source's Title Case headline, first-person source copy ("We gave Claude…", "When we started asking questions…"), vendor marketing voice ("you can explore and use directly"), questions, clickbait.

**Kicker** — see gold-headlines.md. Rules that affect formatting:
- Sentence case, 1–7 words, followed by `: ` (colon, space).
- A question kicker ends in `?` and takes no colon: `Sparking joy? Meta releases…`.
- Not a hyphen, semicolon or period as the delimiter. **[normalize]**
- About one in five items; usually the first item of a cluster.

**Source label**
- ` - _Source_` — space, hyphen, space, then the italic name. Not a link, no trailing space inside the underscores.
- Use the publication's name, not a URL, domain, page title or section tag.

| Use | Not |
|---|---|
| The Register | theregister |
| TNW | TNW \| Artificial-intelligence, TNW \| Openai, The Next Web |
| AP | Associated Press News |
| BBC News | BBC |
| CBS News | CBS |
| NBC News | NBC |
| New York Post | NY Post, California Post (for nypost.com URLs) |
| Tom's Hardware | Tom's Hardwar, Tom’s Hardware |
| LessWrong | Less Wrong |
| YouTube | Youtube (in labels) |
| The Daily Beast | The Daily Beastf |
| AOL | [_AOL.com_](http://AOL.com) |
| SafeDep | SafeDep - Real-time Open Source Software Supply Chain Security |
| X (formerly Twitter) | X, X (formerly Twitter |

Keep as-is: `the Guardian` (the paper's own styling), `WIRED`, `DIGITIMES`. Personal blogs and Substacks: the publication name if it has one, otherwise the author's name (`Derek Thompson`). Vendor first-party posts: the company (`Anthropic`, `OpenAI`, `Google Cloud Blog`).

## 3. Mechanics

- **AI**, not A.I. Convert NYT style. **US, UK, UN**, not U.S., U.K., U.N. **[normalize]**
- **Money:** `$40 billion`, `$500 million`, `$1.25 trillion`. Not `$40b`, `$40B`, `$150M`. Numerals for all amounts and percentages; `%` not "percent".
- **Model names** as the vendor writes them: GPT-6 Astra, GPT-5.6 Sol, Claude Opus 5.5, Gemini 4 Argon, DeepSeek-V4.1-Flash, Muse Spark Open. Company names likewise: Nvidia (not NVIDIA, except the NVIDIA Blog label), Z.ai, SpaceXAI.
- **Quotes:** double quotes for direct quotes; single quotes only inside double. **[normalize]**
- **Apostrophes and quote marks:** straight (`'` `"`). The pipeline emits straight; hand-typed text often comes in curly, and issues currently mix both within one item. **[normalize]**
- **Dashes:** avoid em dashes in summaries; use a comma or semicolon. En dash only in ranges (`100–180 ms`), or a hyphen (`100-150%`), consistently within an issue.
- **Spelling:** American. Convert copied British spellings (defences, behaviour, data centres, realised).
- **Gift links:** keep, and say so in the text only if the source is otherwise paywalled: `(gift link)`.
- **URLs:** strip tracking parameters before send: `utm_*`, `_bhlid`, `guccounter`, `guce_*`, `twclid`, `smid`, `ck_subscriber_id` (that one is your own subscriber ID), `syn-*`, `?st=`. Keep `accessToken`, `gift`, `unlocked_article_code`.

## 4. Editorial asides

The editor's own voice, as an italic line directly under an item.
- Lowercase start, conversational (`b/c`, `gonna`, `tho`, `lol`, `/s`), usually 1–2 sentences.
- 0–4 per issue is typical. Multi-paragraph takes have run (Sep 9, Sep 27, Oct 24); they read best as a single clearly separated block at the end of the cluster.
- Italic with underscores, one pair per line; no bold, no `==highlight==` markup.
- **Editing rule:** fix typos and broken markdown only. Do not change wording, opinion or tone, and do not generate asides.

## 5. Selection (what CUT should favor)

From four digest-vs-published comparisons: the editor keeps about a third of ~120–130 machine items and adds 4–6 of his own.

**Usually kept:** misbehaving or rogue agents; AI-bubble, debt and IPO narrative; lab drama (OpenAI, Anthropic, Meta, xAI); politics, elections and deepfakes; data-center and power backlash; jobs data with numbers; odd consumer stories; culture and entertainment; op-eds with a clear thesis from major outlets.

**Usually cut:**
- Vendor PR, product launches without a hook, B2B surveys (PR Newswire, vendor blogs citing their own stats)
- Small funding rounds; a round survives if the outlet is top-tier or the amount or backer is itself news
- Trade and supply-chain items (DigiTimes-style) unless there's an export-control or market angle
- Academic and medical papers without a striking number
- Regional stories with no wider stake
- Duplicates and stories already run in the last ~3 issues
- Positive product news for frontier labs that isn't a capability jump (price cuts, integrations)
- Sponsored or contributed content (`/spons/` in the URL, `TNW | Contributed`)

When two sources cover one story, keep one link: prefer the primary source or the less-paywalled one.

## 6. Pre-send checklist

Recurring errors in the 50 issues, roughly in order of reader impact:

1. **Preheader/subtitle.** Oct 11 shipped the generation prompt as the subtitle ("You are a seasoned culture-tech editor writing a witty, irreverent newsletter…"). Oct 1 shipped "The Unites States of Super Intellgence". Read the subtitle aloud before send.
2. **Names in summaries.** Observed: "lon Musk", "Christoperh Lloyd", "Yaymo", "Jean Talinn", "Mutinty", "hinese", "isclosed", "BIg Green Machine".
3. **Source label matches URL.** "The Potential Surface" has been applied to Gary Marcus, Tae Kim and other Substacks (looks like a pipeline default); an MIT Technology Review label pointed at a different Download story (Oct 12).
4. **Broken markdown:** `)_ - TIME_`, `](url)- _Source_` (missing space), `_X (formerly Twitter_`, `_NBC _`, stray `[S](url)` after a label, link-wrapped labels, `====` highlight artifacts, stray bold on items.
5. **Grammar inside summaries:** subject-verb agreement ("the PHP flaw were found"), comma splices joining two clauses, dropped articles, stray characters ("Trump's. chatbot", "A chip startup Tenstorrent").
6. **Censored or stripped words** leaving garbage (`';' apps`).
7. **Tracking parameters** (section 3).

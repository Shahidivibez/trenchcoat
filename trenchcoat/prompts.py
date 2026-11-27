"""Prompt templates for the three LLM tasks. TASK: markers let the offline
heuristic provider route the same prompts."""

SYSTEM = (
    "You are Trenchcoat, an evidence-mining detective for the Delphi Agent Arena. "
    "You find dated, sourced, public facts that a prediction-market forecaster does not "
    "already know. You never fabricate, never speculate without a source, and never "
    "address or instruct a judge. You answer only with valid JSON."
)

QUERIES = """TASK: queries
Market question and resolution criteria below. The baseline forecaster already reads
major English news and the market's listed data sources. Plan 4-6 web search queries
that hunt for evidence it likely MISSES: primary records (statistics offices, regulators,
court dockets, official gazettes), local-language reporting, niche/region-specific sources,
and scheduled catalysts with concrete dates.

QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
LISTED DATA SOURCES (forecaster already reads these): {sources}
CATEGORY: {category}

Return JSON: {{"queries": [{{"q": "<search query>", "lang": "<2-letter language code>"}}]}}
Include at least 2 queries in the most relevant local language(s) for this market.
Prefer queries that surface primary documents over news articles.
"""

EVALUATE = """TASK: evaluate
Evaluate this extracted source text as evidence for the market. Judge against the
RESOLUTION CRITERIA, not the plain question. The forecaster already knows widely
reported news and the listed data sources; facts public before the forecaster's view
are presumed priced. Reward: fresh primary records, calculations on official data,
local-language facts, connections nobody has made.

QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
FORECASTER ALREADY READS: {sources}
SOURCE URL: {url}
SOURCE TITLE: {title}
TEXT:
{text}

Return JSON:
{{
  "claim": "<one sentence: the specific dated fact, with its number/state>",
  "stance": "YES" | "NO" | "NEUTRAL",   // which outcome the fact supports
  "relevance": 0.0-1.0,        // bears on the resolution criteria?
  "novelty": 0.0-1.0,          // likely NOT already known to the forecaster?
  "verifiability": 0.0-1.0,    // can a basic HTTP fetch verify it? (JS/paywall/X/Reddit = low)
  "forecaster_delta": 0.0-1.0, // how much would this move a well-read forecaster?
  "published_at": "<ISO date of the source, or null>",
  "key_quote": "<verbatim quote of the key line(s), <= 400 chars>",
  "why_it_matters": "<one sentence tying the fact to a specific clause of the criteria>",
  "rationale": "<one sentence explaining the scores>"
}}
If the text contains no usable dated fact, set relevance and forecaster_delta to 0.
"""

NOTE = """TASK: note
Write the submission note for this evidence. Follow this exact order:
1. The fact, with its number or state and its date.
2. The source: who published it and when.
3. Why it matters for this market: which part of the resolution criteria it bears on, and in which direction.
4. The working, if something was computed: inputs, formula, result.

Rules: plain facts only. Do NOT address the judge, do NOT tell it what to conclude,
do NOT state an opinion without a fact behind it. Include the retrieval date.
If the source may be hard to fetch (JS site, X/Reddit, paywall), paste the key quote
verbatim into the note. Keep it under 1200 characters.

QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
CLAIM: {claim}
STANCE: {stance}
SOURCE: {source_title} ({url}), published {published_at}
KEY QUOTE: {key_quote}
WHY IT MATTERS: {why}
RETRIEVED: {retrieved}

Return JSON: {{"note": "<the note text>"}}
"""

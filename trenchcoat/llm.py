"""Pluggable LLM router: Gemini (free tier) → Groq → GitHub Models → heuristic.

All providers return text; JSON tasks use a tolerant extractor. When no keys
exist, a deterministic heuristic keeps the pipeline functional for demos/tests
(clearly labelled in outputs — never pretend heuristic scores are LLM judgments).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from .config import Config
from .store import Store


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


def _extract_json(text: str) -> dict | list | None:
    """Tolerant JSON extraction: fenced blocks, bare objects, trailing prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                continue
    return None


class GeminiProvider:
    name = "gemini"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        return bool(self.cfg.gemini_api_key)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> LLMResponse:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.cfg.gemini_model}:generateContent"
        )
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = httpx.post(url, headers={"x-goog-api-key": self.cfg.gemini_api_key,
                                        "Content-Type": "application/json"},
                          json=body, timeout=60)
        if resp.status_code == 429:
            raise LLMError("gemini rate-limited")
        if resp.status_code >= 400:
            raise LLMError(f"gemini HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            text = "".join(part.get("text", "")
                           for part in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as e:
            raise LLMError(f"gemini unexpected response: {e}") from e
        return LLMResponse(text, self.name, self.cfg.gemini_model)


class OpenAICompatProvider:
    """Groq and GitHub Models both speak the OpenAI chat-completions dialect."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages,
                  "temperature": 0.2, "max_tokens": max_tokens},
            timeout=60,
        )
        if resp.status_code == 429:
            raise LLMError(f"{self.name} rate-limited")
        if resp.status_code >= 400:
            raise LLMError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"{self.name} unexpected response: {e}") from e
        return LLMResponse(text, self.name, self.model)


class HeuristicProvider:
    """Offline fallback. Handles the three structured tasks with simple rules.
    Outputs are tagged provider='heuristic' so the board shows they're not LLM-grade."""

    name = "heuristic"

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> LLMResponse:
        task = ""
        m = re.search(r"TASK:\s*(\w+)", prompt)
        if m:
            task = m.group(1)
        if task == "queries":
            return LLMResponse(self._queries(prompt), self.name, "rules-v1")
        if task == "evaluate":
            return LLMResponse(self._evaluate(prompt), self.name, "rules-v1")
        if task == "note":
            return LLMResponse(self._note(prompt), self.name, "rules-v1")
        return LLMResponse("{}", self.name, "rules-v1")

    @staticmethod
    def _queries(prompt: str) -> str:
        q = re.search(r"QUESTION:\s*(.+)", prompt)
        question = q.group(1).strip() if q else "news"
        # strip to key noun phrases
        words = re.findall(r"[A-Za-zÀ-ÿ0-9']+", question)
        stop = {"will", "the", "be", "in", "of", "at", "on", "a", "an", "per", "its",
                "and", "or", "for", "to", "is", "are", "by", "with", "than", "that"}
        key = [w for w in words if w.lower() not in stop][:8]
        base = " ".join(key)
        return json.dumps({"queries": [
            {"q": base, "lang": "en"},
            {"q": f"{base} official", "lang": "en"},
            {"q": f"{base} site:gov OR site:admin.ch OR site:destatis.de", "lang": "en"},
        ]})

    @staticmethod
    def _evaluate(prompt: str) -> str:
        # crude keyword overlap relevance; novelty unknown offline → conservative
        text_m = re.search(r"TEXT:\s*(.+)", prompt, re.S)
        q_m = re.search(r"QUESTION:\s*(.+)", prompt)
        text = (text_m.group(1) if text_m else "").lower()
        question = (q_m.group(1) if q_m else "").lower()
        q_tokens = set(re.findall(r"[a-zà-ÿ0-9]{4,}", question))
        t_tokens = set(re.findall(r"[a-zà-ÿ0-9]{4,}", text))
        overlap = len(q_tokens & t_tokens) / max(1, len(q_tokens))
        relevance = min(1.0, overlap * 1.6)
        has_date = bool(re.search(r"20\d\d", text))
        has_num = bool(re.search(r"\d+[.,]?\d*\s*%|\d{3,}", text))
        return json.dumps({
            "claim": (text[:160].strip() or "undated claim"),
            "stance": "NEUTRAL",
            "relevance": round(relevance, 2),
            "novelty": 0.5,
            "verifiability": 0.6 if has_date else 0.3,
            "forecaster_delta": 0.4,
            "published_at": None,
            "rationale": "heuristic offline estimate (no LLM key configured)",
            "key_numbers": has_num,
        })

    @staticmethod
    def _note(prompt: str) -> str:
        return json.dumps({"note": ""})  # scout composes notes itself offline


class LLMRouter:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.providers = [
            GeminiProvider(cfg),
            OpenAICompatProvider("groq", "https://api.groq.com/openai/v1",
                                 cfg.groq_api_key, cfg.groq_model),
            OpenAICompatProvider("github-models", "https://models.github.ai/inference",
                                 cfg.github_models_token, cfg.github_models_model),
            HeuristicProvider(),
        ]
        self.last_provider = "heuristic"

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> LLMResponse:
        errors = []
        for p in self.providers:
            if not p.available():
                continue
            try:
                res = p.complete(prompt, system, max_tokens)
                self.last_provider = p.name
                if p.name != "heuristic":
                    time.sleep(0.3)  # gentle on free tiers
                return res
            except (LLMError, httpx.HTTPError) as e:
                errors.append(f"{p.name}: {e}")
                self.store.log("llm-fallback", str(e))
                continue
        raise LLMError("all providers failed: " + "; ".join(errors))

    def json_task(self, prompt: str, system: str = "", max_tokens: int = 2048) -> tuple[dict | list, str]:
        res = self.complete(prompt, system, max_tokens)
        data = _extract_json(res.text)
        if data is None:
            raise LLMError(f"{res.provider} returned non-JSON: {res.text[:120]}")
        return data, res.provider

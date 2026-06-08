"""
LLM provider abstraction — plug any local or cloud model.

Supported providers
-------------------
ollama      Local models via Ollama REST API (http://localhost:11434)
openai      OpenAI / OpenAI-compatible endpoints  (requires OPENAI_API_KEY or base_url)
anthropic   Anthropic Claude API                  (requires ANTHROPIC_API_KEY)
disabled    No LLM — InsightsEngine falls back to rule-based text

Switching providers requires only a config change; the rest of the
application is untouched (open/closed principle).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


# ── Abstract base ──────────────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """Minimal interface every provider must implement."""

    #: Human-readable identifier shown in the UI
    provider_id: str = "base"

    @abstractmethod
    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 512) -> str:
        """Send *prompt* and return the model's text response."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider can currently accept requests."""

    @property
    def model_name(self) -> str:
        return getattr(self, "_model", "unknown")

    def info(self) -> dict:
        return {
            "provider":   self.provider_id,
            "model":      self.model_name,
            "available":  self.is_available(),
        }


# ── Ollama ─────────────────────────────────────────────────────────────────────

class OllamaProvider(BaseLLMProvider):
    """
    Local LLM via Ollama (https://ollama.ai).

    Start with:  ollama serve
    Pull model:  ollama pull llama3.2
    """

    provider_id = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2"):
        self.base_url = base_url.rstrip("/")
        self._model   = model

    def is_available(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return model names currently pulled in Ollama."""
        try:
            import requests
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 512) -> str:
        import requests
        payload = {
            "model":  self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()


# ── OpenAI / compatible ────────────────────────────────────────────────────────

class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI ChatGPT or any OpenAI-compatible endpoint (e.g. LM Studio, LocalAI).

    Set base_url to point at a local server to avoid sending data to the cloud.
    """

    provider_id = "openai"

    def __init__(
        self,
        api_key:  str = "",
        model:    str = "gpt-4o-mini",
        base_url: str = "",
    ):
        self._api_key  = api_key  or os.getenv("OPENAI_API_KEY", "")
        self._model    = model
        self._base_url = base_url or None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 512) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package required. Run: pip install openai"
            ) from exc
        kwargs = dict(api_key=self._api_key)
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()


# ── Anthropic ──────────────────────────────────────────────────────────────────

class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude API (cloud)."""

    provider_id = "anthropic"

    def __init__(self, api_key: str = "", model: str = "claude-haiku-4-5-20251001"):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model   = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 512) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package required. Run: pip install anthropic"
            ) from exc
        client = anthropic.Anthropic(api_key=self._api_key)
        msg = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()


# ── Disabled / rule-based ──────────────────────────────────────────────────────

class DisabledProvider(BaseLLMProvider):
    """
    No external LLM. The InsightsEngine falls back to deterministic
    rule-based text derived directly from the cluster statistics.
    """

    provider_id = "disabled"
    _model      = "rule-based"

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 512) -> str:
        return ""   # signal to InsightsEngine to use rule-based fallback


# ── Factory ────────────────────────────────────────────────────────────────────

def get_provider(cfg) -> BaseLLMProvider:
    """
    Return the appropriate provider for the given LLMConfig.

    cfg — a LLMConfig dataclass instance from config.py
    """
    pid = (cfg.provider or "disabled").lower()

    if pid == "ollama":
        return OllamaProvider(
            base_url=cfg.base_url or "http://localhost:11434",
            model=cfg.model or "llama3.2",
        )
    if pid == "openai":
        return OpenAIProvider(
            api_key=cfg.api_key,
            model=cfg.model or "gpt-4o-mini",
            base_url=cfg.base_url or "",
        )
    if pid == "anthropic":
        return AnthropicProvider(
            api_key=cfg.api_key,
            model=cfg.model or "claude-haiku-4-5-20251001",
        )
    return DisabledProvider()

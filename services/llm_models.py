"""Models a teacher can generate with using their own API key.

The model list matches the assessment agent's (`assessment_agent/llm/models.py`)
so the instructor UI can offer one dropdown for both agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.llm import ChatLLMClient, LLMError

Vendor = Literal["anthropic", "openai", "gemini"]


@dataclass(frozen=True)
class Model:
    id: str
    vendor: Vendor
    name: str


SUPPORTED_MODELS: dict[str, Model] = {
    m.id: m
    for m in (
        Model("claude-opus-5-5", "anthropic", "Claude Opus 5.5"),
        Model("claude-sonnet-5-5", "anthropic", "Claude Sonnet 5.5"),
        Model("claude-haiku-5-5", "anthropic", "Claude Haiku 5.5"),
        Model("gpt-6-astra", "openai", "GPT-6 Astra"),
        Model("gpt-6.1-sol", "openai", "GPT-6.1 Sol"),
        Model("gpt-6-luna", "openai", "GPT-6 Luna"),
        Model("gemini-4-argon", "gemini", "Gemini 4 Argon"),
        Model("gemini-3.8-flash", "gemini", "Gemini 3.8 Flash"),
        Model("gemini-3.5-flash-lite", "gemini", "Gemini 3.5 Flash-Lite"),
    )
}


def _chat_model(model: Model, api_key: str):
    """Build the LangChain chat model for one vendor.

    Vendor packages are imported here so a deployment that only uses the
    server-side default does not need them installed.
    """
    try:
        if model.vendor == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=model.id, api_key=api_key, temperature=0.3)
        if model.vendor == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=model.id, api_key=api_key, temperature=0.3)
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model.id, google_api_key=api_key, temperature=0.3)
    except ImportError as exc:
        raise LLMError(
            f"The {model.vendor} client library is not installed on this server: {exc}"
        ) from None


def build_client_for_key(model_id: str, api_key: str) -> ChatLLMClient:
    """A client that calls `model_id` with the teacher's own key."""
    model = SUPPORTED_MODELS[model_id]
    return ChatLLMClient(_chat_model(model, api_key), secret=api_key)

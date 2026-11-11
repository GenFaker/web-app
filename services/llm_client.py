"""Optional AI narration strategies; the domain report remains rule-driven.

Providers receive only curated service facts, never API credentials in a prompt,
project titles, or raw answers. Model text is supplemental and unverified: schema
validation cannot establish factual correctness. Failures always yield a usable,
explicitly labelled offline explanation without changing a recommendation.
"""
from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlsplit

import httpx

from models.domain import AssessmentResult, ProviderSettings


class ProviderError(Exception):
    """A safe, user-facing provider failure without response text or credentials."""


DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-mini",
    "claude": "claude-sonnet-4-5",
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.2",
    "mock": "offline",
}
MAX_RESPONSE_BYTES = 96 * 1024
MAX_EXPLANATION_LENGTH = 1800
_SYSTEM_PROMPT = """You explain an existing pre-deployment AWS advisory for a university project.
The supplied services, selection reasons, risks, actions, and framework mappings are
fixed authoritative input. Do not select services or change any of those facts.
Write one short plain-language explanation per supplied service of why the stated
requirements led to that service and why reviewing its existing risk entries matters.
Do not add services, facts, controls, risk ratings, citations, URLs, compliance claims,
or guarantees of security. Do not imply a deployed environment was scanned or audited.
Unknown requirements are unresolved; planned controls are not verified controls.
Return ONLY a JSON object with this exact structure:
{"explanations": {"supplied_service_id": "One concise plain-text paragraph."}}
Include exactly the supplied service IDs. No HTML, Markdown, or additional keys.
"""


def _facts(result: AssessmentResult) -> str:
    """Serialize the minimum curated facts needed for a supplemental explanation."""
    services = [
        {
            "service_id": service.service_id,
            "name": service.name,
            "selection_reason": service.reason,
            "conditional": service.conditional,
            "risk_entries": [
                {
                    "dimension": risk.dimension,
                    "risk": risk.title,
                    "status": risk.status,
                    "action": risk.action,
                    "framework_mapping": [
                        {"framework": ref.framework, "reference": ref.reference}
                        for ref in risk.frameworks
                    ],
                }
                for risk in service.risks
            ],
        }
        for service in result.recommendations
    ]
    return json.dumps({"services": services}, ensure_ascii=False)


def _validate_explanations(payload: Any, result: AssessmentResult) -> dict[str, str]:
    """Fail closed on unsupported IDs, markup, links, and unsafe security claims.

    This is a structural and limited content filter, not semantic verification.
    The UI must continue to identify all remote narration as unverified AI text.
    """
    if not isinstance(payload, dict) or set(payload) != {"explanations"}:
        raise ProviderError("AI output did not match the required explanation format.")
    entries = payload["explanations"]
    expected = {service.service_id for service in result.recommendations}
    if not isinstance(entries, dict) or set(entries) != expected:
        raise ProviderError("AI output changed the set of rule-selected services.")
    forbidden = re.compile(
        r"https?\s*:|www\.|<[^>]*>|```|\[[^]]*\]\(|"
        r"(?:disable|turn off)\s+(?:all\s+)?(?:logging|encryption|mfa|authentication)|"
        r"(?:guaranteed|100%|fully)\s+(?:secure|safe|compliant)|"
        r"(?:allow|enable)\s+(?:unrestricted|anonymous)\s+(?:access|inbound)|"
        r"(?:ignore|disregard)\s+(?:the\s+)?(?:rules|instructions)",
        re.IGNORECASE,
    )
    validated: dict[str, str] = {}
    for service_id, value in entries.items():
        if not isinstance(value, str) or not (20 <= len(value.strip()) <= MAX_EXPLANATION_LENGTH):
            raise ProviderError("AI output contained an invalid explanation length.")
        if any(ord(char) < 32 and char not in "\n\t" for char in value) or forbidden.search(value):
            raise ProviderError("AI output contained unsupported content.")
        validated[service_id] = value.strip()
    return validated


def validate_ollama_url(value: str) -> str:
    """Accept only literal HTTP loopback hosts with no credentials or URL extras."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ProviderError("Ollama address must be an HTTP loopback URL with a valid port.") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ProviderError("Ollama is restricted to http://localhost or http://127.0.0.1 with an optional port.")
    # Canonicalization avoids alternate hostname spelling and URL interpretation.
    return f"http://127.0.0.1:{port or 80}"


class LLMProvider(ABC):
    """Strategy interface for optional, supplemental narrative generation."""

    @abstractmethod
    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        """Return plain-text explanations keyed by the existing service IDs."""
        raise NotImplementedError


class MockProvider(LLMProvider):
    """Deterministic offline narration; this strategy does not invoke an LLM."""

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        """Explain the existing selection without transmitting any data."""
        return {
            service.service_id: (
                f"{service.reason} "
                + ("This is a conditional candidate until the unresolved requirement is clarified. " if service.conditional else "")
                + "Review the listed controls before deployment. Planned measures reflect your answers and have not been verified in AWS."
            )
            for service in result.recommendations
        }


class _HTTPProvider(LLMProvider):
    """Shared bounded HTTP transport with safe errors and no automatic retries."""

    provider_name: str

    def __init__(self, settings: ProviderSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.model = settings.model or DEFAULT_MODELS[self.provider_name]
        self._client = client

    async def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        """Read at most 96 KiB and avoid redirects, retry loops, and raw error text."""
        try:
            async with asyncio.timeout(30):
                if self._client is not None:
                    return await self._read(self._client, url, headers, body)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(25.0, connect=5.0),
                    follow_redirects=False,
                    trust_env=self.provider_name != "ollama",
                ) as client:
                    return await self._read(client, url, headers, body)
        except TimeoutError as exc:
            raise ProviderError("The AI provider request timed out.") from exc

    async def _read(self, client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        try:
            async with client.stream(
                "POST", url, headers=headers, json=body,
                timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=False,
            ) as response:
                if response.status_code in {401, 403}:
                    raise ProviderError("The AI provider rejected authentication or access.")
                if response.status_code == 429:
                    raise ProviderError("The AI provider rate or quota limit was reached.")
                if not 200 <= response.status_code < 300:
                    raise ProviderError("The AI provider returned an unsuccessful response.")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ProviderError("The AI provider response exceeded the safe size limit.")
                return json.loads(raw)
        except httpx.TimeoutException as exc:
            raise ProviderError("The AI provider request timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("The AI provider could not be reached.") from exc
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("The AI provider returned an unreadable response.") from exc

    def _validate_content(self, value: Any, result: AssessmentResult) -> dict[str, str]:
        if not isinstance(value, str):
            raise ProviderError("The AI provider returned no explanation text.")
        try:
            payload = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ProviderError("AI output was not valid JSON.") from exc
        return _validate_explanations(payload, result)

    def _require_key(self) -> None:
        if not self.settings.api_key.strip():
            raise ProviderError("An API key is required for the selected AI provider.")


class GeminiProvider(_HTTPProvider):
    """Google Gemini REST strategy using the API key header and JSON output."""

    provider_name = "gemini"

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        if not result.recommendations:
            return {}
        self._require_key()
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", self.model):
            raise ProviderError("Gemini model name contains unsupported characters.")
        payload = await self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.settings.api_key, "Content-Type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": _facts(result)}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
            },
        )
        try:
            candidate = payload["candidates"][0]
            if candidate.get("finishReason", "STOP") != "STOP":
                raise ProviderError("Gemini did not finish a complete explanation.")
            content = "".join(part["text"] for part in candidate["content"]["parts"] if "text" in part)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini returned no usable explanation.") from exc
        return self._validate_content(content, result)


class GroqProvider(_HTTPProvider):
    """Groq's OpenAI-compatible REST strategy with a fixed official endpoint."""

    provider_name = "groq"

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        if not result.recommendations:
            return {}
        self._require_key()
        payload = await self._post(
            "https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
            {
                "model": self.model,
                "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": _facts(result)}],
                "temperature": 0.2,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        )
        try:
            choice = payload["choices"][0]
            if choice.get("finish_reason", "stop") != "stop":
                raise ProviderError("Groq did not finish a complete explanation.")
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Groq returned no usable explanation.") from exc
        return self._validate_content(content, result)


class OpenAIProvider(_HTTPProvider):
    """GPT narration through the official Responses API, without response storage."""

    provider_name = "openai"

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        if not result.recommendations:
            return {}
        self._require_key()
        payload = await self._post(
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
            {"model": self.model, "instructions": _SYSTEM_PROMPT, "input": _facts(result),
             "store": False, "max_output_tokens": 4096,
             "text": {"format": {"type": "json_object"}}},
        )
        if payload.get("status") != "completed":
            raise ProviderError("GPT did not finish a complete explanation.")
        content = "".join(part.get("text", "") for item in payload.get("output", [])
                          if item.get("type") == "message" for part in item.get("content", [])
                          if part.get("type") == "output_text")
        return self._validate_content(content, result)


class ClaudeProvider(_HTTPProvider):
    """Claude narration through Anthropic's official Messages API."""

    provider_name = "claude"

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        if not result.recommendations:
            return {}
        self._require_key()
        payload = await self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.settings.api_key, "anthropic-version": "2023-06-01",
             "Content-Type": "application/json"},
            {"model": self.model, "system": _SYSTEM_PROMPT, "max_tokens": 4096,
             "messages": [{"role": "user", "content": _facts(result)}]},
        )
        if payload.get("stop_reason") != "end_turn":
            raise ProviderError("Claude did not finish a complete explanation.")
        content = "".join(part.get("text", "") for part in payload.get("content", [])
                          if part.get("type") == "text")
        return self._validate_content(content, result)


class OllamaProvider(_HTTPProvider):
    """Local Ollama strategy restricted to an HTTP loopback server."""

    provider_name = "ollama"

    async def generate(self, result: AssessmentResult) -> dict[str, str]:
        if not result.recommendations:
            return {}
        base_url = validate_ollama_url(self.settings.ollama_url)
        payload = await self._post(
            f"{base_url}/api/chat",
            {"Content-Type": "application/json"},
            {
                "model": self.model,
                "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": _facts(result)}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2, "num_predict": 4096},
            },
        )
        try:
            if payload.get("done") is not True:
                raise ProviderError("Ollama did not finish a complete explanation.")
            content = payload["message"]["content"]
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProviderError("Ollama returned no usable explanation.") from exc
        return self._validate_content(content, result)


def create_provider(settings: ProviderSettings, client: httpx.AsyncClient | None = None) -> LLMProvider:
    """Construct the configured strategy; injected clients remain caller-owned."""
    providers: dict[str, type[_HTTPProvider]] = {
        "openai": OpenAIProvider, "claude": ClaudeProvider,
        "gemini": GeminiProvider, "groq": GroqProvider, "ollama": OllamaProvider,
    }
    if settings.provider == "mock":
        return MockProvider()
    return providers[settings.provider](settings, client)


async def enrich_report(
    result: AssessmentResult,
    settings: ProviderSettings,
    client: httpx.AsyncClient | None = None,
) -> AssessmentResult:
    """Copy the report and add narration, falling back safely on provider failure.

    API keys are held only in the caller's request/settings object and transport
    headers. They are never included in this returned report or error messages.
    """
    enriched = result.model_copy(deep=True)
    enriched.provider_requested = settings.provider
    if not result.recommendations:
        enriched.provider_used = "mock"
        enriched.provider_notice = "No services were selected; no AI request was made."
        enriched.ai_explanations = {}
        return enriched
    try:
        enriched.ai_explanations = await create_provider(settings, client).generate(result)
        enriched.provider_used = settings.provider
        enriched.provider_notice = (
            "Offline explanations. No information sent to an AI service."
            if settings.provider == "mock"
            else f"Supplemental {settings.provider.title()} AI text is unverified. Service choices, risks, actions, mappings, and links remain fixed by the rule engine."
        )
    except (ProviderError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # Do not expose raw exceptions: third-party messages may contain keys,
        # endpoint details, or generated content. Every failure is transparent.
        enriched.ai_explanations = await MockProvider().generate(result)
        enriched.provider_used = "mock"
        enriched.provider_notice = (
            f"{settings.provider.title()} explanations were unavailable or failed validation. "
            "Offline explanations were used; rule-based recommendations are unchanged. "
            "Check the provider key, model, connection, and quota in settings."
        )
    return enriched

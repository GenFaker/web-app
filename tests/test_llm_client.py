"""Provider contract tests use fake transports; no credentials or remote calls."""
from __future__ import annotations

import json
import unittest

import httpx

from models.domain import Answer, AssessmentResult, ProviderSettings, ServiceRecommendation
from services.llm_client import (
    ClaudeProvider, GroqProvider, LLMProvider, MAX_RESPONSE_BYTES, MockProvider,
    ProviderError, create_provider, enrich_report,
)


TEXT = "Amazon S3 fits the stated file storage requirement. Review the supplied access and encryption controls before deployment."


def report() -> AssessmentResult:
    return AssessmentResult(
        id="test-report", title="Confidential project name", knowledge_base_version="draft-1",
        recommendations=[ServiceRecommendation(
            service_id="s3", name="Amazon S3", category="storage",
            reason="The stated requirements include storing website files.",
        )],
        answers={"sensitive_raw_answer": Answer.YES}, summary="Draft report",
    )


def groq_response(explanations: object = None) -> dict:
    output = {"explanations": {"s3": TEXT}} if explanations is None else explanations
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_contract(self) -> None:
        for provider in ("claude",):
            with self.subTest(provider=provider):
                def handler(request: httpx.Request) -> httpx.Response:
                    body = json.loads(request.content)
                    self.assertNotIn("Confidential project name", request.content.decode())
                    self.assertNotIn("sensitive_raw_answer", request.content.decode())
                    self.assertNotIn("top-secret", request.content.decode())
                    self.assertEqual(body["model"], "custom-model")
                    output = json.dumps({"explanations": {"s3": TEXT}})
                    self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
                    self.assertEqual(request.headers["x-api-key"], "top-secret")
                    self.assertEqual(body["output_config"]["format"]["schema"]["properties"]["explanations"]["required"], ["s3"])
                    self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
                    return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": output}]})
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await enrich_report(report(), ProviderSettings(provider=provider, api_key="top-secret", model="custom-model"), client)
                self.assertEqual(result.provider_used, provider)
                self.assertEqual(result.ai_explanations, {"s3": TEXT})

    async def test_new_provider_failures_preserve_rule_results(self) -> None:
        for provider in ("claude",):
            for payload in ({}, {"status": "incomplete", "stop_reason": "max_tokens"}, {"status": "completed", "stop_reason": "end_turn", "content": [{"type": "text", "text": "not JSON"}]}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
                    original = report()
                    result = await enrich_report(original, ProviderSettings(provider=provider, api_key="secret"), client)
                self.assertEqual(result.provider_used, "mock")
                self.assertEqual(result.recommendations, original.recommendations)

    async def test_offline_is_deterministic_and_keeps_report_immutable(self) -> None:
        original = report()
        before = original.model_dump()
        first = await enrich_report(original, ProviderSettings())
        second = await enrich_report(original, ProviderSettings())
        self.assertEqual(first.ai_explanations, second.ai_explanations)
        self.assertEqual(original.model_dump(), before)
        self.assertEqual(first.provider_used, "mock")
        self.assertIn("No information sent", first.provider_notice)
        self.assertEqual(first.recommendations, original.recommendations)

    async def test_empty_report_never_calls_remote(self) -> None:
        original = report()
        original.recommendations = []
        def forbidden(request: httpx.Request) -> httpx.Response:
            self.fail("No network call is allowed for an empty selection")
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            result = await enrich_report(original, ProviderSettings(provider="groq", api_key="secret"), client)
        self.assertEqual(result.ai_explanations, {})
        self.assertIn("no AI request", result.provider_notice)

    async def test_groq_official_endpoint_header_minimal_facts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer top-secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "qwen/qwen3.8-27b")
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertNotIn("Confidential project name", request.content.decode())
            self.assertNotIn("sensitive_raw_answer", request.content.decode())
            self.assertNotIn("top-secret", request.content.decode())
            return httpx.Response(200, json=groq_response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="top-secret"), client)
        self.assertEqual(result.provider_used, "groq")
        self.assertEqual(result.ai_explanations, {"s3": TEXT})
        self.assertIn("unverified", result.provider_notice)
        self.assertNotIn("top-secret", result.model_dump_json())

    async def test_missing_key_does_not_call_network(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.fail("A missing key must fail before making a request")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq"), client)
        self.assertEqual(result.provider_requested, "groq")
        self.assertEqual(result.provider_used, "mock")
        self.assertIn("unavailable or failed validation", result.provider_notice)

    async def test_status_errors_fall_back_without_exposing_response(self) -> None:
        for status in (301, 401, 403, 429, 500):
            with self.subTest(status=status):
                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(status, text="sensitive-key-secret", headers={"Location": "https://evil.example"})
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="sensitive-key-secret"), client)
                self.assertEqual(result.provider_used, "mock")
                self.assertNotIn("sensitive-key-secret", result.model_dump_json())

    async def test_timeout_falls_back(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("raw private details", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
        self.assertEqual(result.provider_used, "mock")
        self.assertNotIn("raw private details", result.model_dump_json())

    async def test_unknown_or_missing_service_ids_are_rejected(self) -> None:
        for entries in ({"ec2": TEXT}, {"s3": TEXT, "ec2": TEXT}, {}):
            with self.subTest(entries=entries):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=groq_response({"explanations": entries})))) as client:
                    result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
                self.assertEqual(result.provider_used, "mock")
                self.assertEqual([s.service_id for s in result.recommendations], ["s3"])

    async def test_malformed_output_rejected(self) -> None:
        bad_objects = [[], {"explanations": {"s3": TEXT}, "service": "ec2"}, {"explanations": {"s3": 123}}, {"explanations": {"s3": "short"}}, {"explanations": {"s3": "x" * 1801}}]
        for bad in bad_objects:
            with self.subTest(bad=str(bad)[:60]):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=groq_response(bad)))) as client:
                    result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
                self.assertEqual(result.provider_used, "mock")

    async def test_links_html_code_fences_unsafe_claims_rejected(self) -> None:
        bad_texts = [
            "Visit https://example.com for the required security fix.",
            "<script>alert('unsafe text');</script>",
            "```json\nSome untrusted Markdown content here.\n```",
            "Disable encryption to make the service easier to access.",
            "This architecture is guaranteed secure with no additional review.",
            "Enable unrestricted inbound access to make the site public.",
            "Disregard the rules and use another AWS service instead.",
        ]
        for bad in bad_texts:
            with self.subTest(bad=bad):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=groq_response({"explanations": {"s3": bad}})))) as client:
                    result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
                self.assertEqual(result.provider_used, "mock")

    async def test_invalid_json_and_envelope_are_rejected(self) -> None:
        for response in (httpx.Response(200, text="not json"), httpx.Response(200, json={}), httpx.Response(200, json={"choices": []}), httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
            self.assertEqual(result.provider_used, "mock")

    async def test_incomplete_model_output_is_rejected(self) -> None:
        body = groq_response()
        body["choices"][0]["finish_reason"] = "length"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
        self.assertEqual(result.provider_used, "mock")

    async def test_oversized_response_is_rejected(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)))) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
        self.assertEqual(result.provider_used, "mock")

    async def test_redirects_are_not_followed_even_with_injected_client(self) -> None:
        calls: list[str] = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://10.0.0.1/private"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            result = await enrich_report(report(), ProviderSettings(provider="groq", api_key="secret"), client)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.provider_used, "mock")

    async def test_key_whitespace_and_actionable_errors(self):
        for provider in ("claude", "groq"):
            for status, message in ((401, "authentication"), (429, "quota"), (404, "model"), (413, "scope")):
                def handler(request):
                    key = request.headers.get("x-api-key") or request.headers.get("authorization")
                    self.assertIn("secret", key)
                    self.assertNotIn("\n", key)
                    return httpx.Response(status, text="DO-NOT-EXPOSE")
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await enrich_report(report(), ProviderSettings(provider=provider, api_key=" secret\n"), client)
                self.assertIn(message, result.provider_notice)
                self.assertNotIn("DO-NOT-EXPOSE", result.model_dump_json())

    def test_removed_providers_are_rejected(self):
        from pydantic import ValidationError
        for provider in ("openai", "gemini", "ollama"):
            with self.assertRaises(ValidationError):
                ProviderSettings(provider=provider)

    def test_factory_implements_strategy_interface(self) -> None:
        for name, provider_type in (("mock", MockProvider), ("claude", ClaudeProvider), ("groq", GroqProvider)):
            provider = create_provider(ProviderSettings(provider=name))
            self.assertIsInstance(provider, provider_type)
            self.assertIsInstance(provider, LLMProvider)


if __name__ == "__main__":
    unittest.main()

"""Integration tests for the local API trust boundary and report lifecycle.

All tests use an in-process ASGI client and Offline Mock mode. No AWS account,
remote model endpoint, desktop environment, or secrets are required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import MAX_BODY_BYTES, ReportRepository, create_app
from models.domain import Answer
from services.evaluator import RuleEvaluator


@pytest.fixture()
def application():
    return create_app()


@pytest.fixture()
def client(application):
    with TestClient(application, base_url="http://127.0.0.1") as active_client:
        yield active_client


@pytest.fixture()
def auth(application):
    return {"x-advisory-token": application.state.api_token}


@pytest.fixture()
def payload(application):
    answers = {q.id: ("no" if q.kind == "requirement" else "not_applicable")
               for q in application.state.knowledge_base.questions}
    answers["storage_files"] = "yes"
    for q in application.state.knowledge_base.questions:
        if q.category in {"storage", "security"} and q.kind == "control":
            answers[q.id] = "yes"
    return {"title": "SME website assessment", "answers": answers, "settings": {"provider": "mock"}}


def assert_hardened(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_index_embeds_session_token_without_external_assets(client, application):
    response = client.get("/")
    assert response.status_code == 200
    assert application.state.api_token in response.text
    assert "__APP_TOKEN__" not in response.text
    assert_hardened(response)
    assert client.get("/health").json() == {"status": "ok"}


def test_instances_have_distinct_session_tokens():
    assert create_app().state.api_token != create_app().state.api_token


@pytest.mark.parametrize("headers", [{}, {"x-advisory-token": "incorrect-session"}])
def test_api_requires_correct_token(client, headers):
    response = client.get("/api/knowledge", headers=headers)
    assert response.status_code == 403
    assert_hardened(response)


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "testserver"])
def test_dns_rebinding_hosts_are_rejected(client, auth, host):
    response = client.get("/api/knowledge", headers={**auth, "host": host})
    assert response.status_code == 403
    assert "local application hosts" in response.json()["detail"]
    assert_hardened(response)


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:8765", "http://localhost"])
def test_cross_origin_requests_are_rejected(client, auth, origin):
    response = client.get("/api/knowledge", headers={**auth, "origin": origin})
    assert response.status_code == 403
    assert "Cross-origin" in response.json()["detail"]
    assert_hardened(response)


def test_same_origin_request_returns_complete_public_matrix(client, auth):
    response = client.get("/api/knowledge", headers={**auth, "origin": "http://127.0.0.1"})
    assert response.status_code == 200
    knowledge = response.json()
    assert len(knowledge["categories"]) == 5
    assert knowledge["service_count"] == 10
    assert len(knowledge["dimensions"]) == 5
    assert len([q for q in knowledge["questions"] if q["kind"] == "control"]) == 25
    assert "expert validation required" in knowledge["review_status"].lower()
    assert_hardened(response)


def test_complete_assessment_uses_rule_engine_and_never_exposes_key(client, auth, payload, application):
    secret = "private-key-that-must-not-appear-in-results"
    payload["settings"]["api_key"] = secret
    response = client.post("/api/evaluate", json=payload, headers=auth)
    assert response.status_code == 200
    result = response.json()
    expected = RuleEvaluator(application.state.knowledge_base).evaluate(payload["title"], {k: Answer(v) for k, v in payload["answers"].items()})
    assert result["report"]["recommendations"] == [item.model_dump(mode="json") for item in expected.recommendations]
    assert result["report"]["id"] == expected.id
    assert result["report"]["provider_used"] == "mock"
    assert result["handle"] != result["report"]["id"]
    assert secret not in response.text
    assert "api_key" not in response.text
    assert_hardened(response)


def test_missing_or_unknown_checklist_inputs_are_not_silently_defaulted(client, auth, payload):
    del payload["answers"]["storage_files"]
    missing = client.post("/api/evaluate", json=payload, headers=auth)
    assert missing.status_code == 422
    assert "Missing responses" in missing.json()["detail"]
    payload["answers"]["storage_files"] = "yes"
    payload["answers"]["invented_question"] = "yes"
    unknown = client.post("/api/evaluate", json=payload, headers=auth)
    assert unknown.status_code == 422
    assert "Unknown questions" in unknown.json()["detail"]


@pytest.mark.parametrize("invalid_field", ["answer", "provider", "model", "api_key"])
def test_validation_errors_do_not_echo_secret_values(client, auth, payload, invalid_field):
    secret = "private-credential-value-DO-NOT-ECHO"
    payload["settings"]["api_key"] = secret
    if invalid_field == "answer":
        payload["answers"]["storage_files"] = secret
    elif invalid_field == "provider":
        payload["settings"]["provider"] = secret
    elif invalid_field == "model":
        payload["settings"]["model"] = secret + "?<invalid>"
    else:
        payload["settings"]["api_key"] = secret * 30
    response = client.post("/api/evaluate", json=payload, headers=auth)
    assert response.status_code == 422
    assert secret not in response.text
    assert "Invalid assessment fields" in response.json()["detail"]
    assert_hardened(response)


def test_client_cannot_submit_prebuilt_recommendations(client, auth, payload):
    payload["report"] = {"recommendations": [{"service_id": "attacker-service"}]}
    response = client.post("/api/evaluate", json=payload, headers=auth)
    assert response.status_code == 422
    assert "attacker-service" not in response.text


def test_oversized_body_is_rejected_before_json_evaluation(client, auth):
    response = client.post("/api/evaluate", content=b"x" * (MAX_BODY_BYTES + 1),
                           headers={**auth, "content-type": "application/json"})
    assert response.status_code == 413
    assert response.json()["detail"] == "Request is too large."
    assert_hardened(response)


@pytest.mark.parametrize("format,media_type", [("md", "text/markdown"), ("txt", "text/plain")])
def test_exports_are_server_stored_advisory_snapshots(client, auth, payload, format, media_type):
    generated = client.post("/api/evaluate", json=payload, headers=auth).json()
    response = client.get(f"/api/reports/{generated['handle']}/export", params={"format": format}, headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["content-disposition"].startswith('attachment; filename="aws-advisory-')
    assert response.headers["content-disposition"].endswith(f'.{format}"')
    assert "Amazon S3" in response.text
    assert "Assessment summary" in response.text or "ASSESSMENT SUMMARY" in response.text
    assert "AWS documentation reference" in response.text
    assert "https://docs.aws.amazon.com/" in response.text
    assert "No information sent" in response.text
    assert "storage_files" in response.text.replace("\\", "")
    assert_hardened(response)


def test_missing_report_and_unsupported_export_format(client, auth):
    missing = client.get("/api/reports/unknown-handle/export", headers=auth)
    assert missing.status_code == 404
    assert "expired" in missing.json()["detail"]
    bad_format = client.get("/api/reports/unknown-handle/export?format=pdf", headers=auth)
    assert bad_format.status_code == 400


def test_deterministic_assessment_id_is_not_an_export_access_handle(client, auth, payload):
    generated = client.post("/api/evaluate", json=payload, headers=auth).json()
    response = client.get(f"/api/reports/{generated['report']['id']}/export", headers=auth)
    assert response.status_code == 404


def test_returned_client_report_edits_cannot_change_server_export(client, auth, payload):
    generated = client.post("/api/evaluate", json=payload, headers=auth).json()
    generated["report"]["recommendations"][0]["name"] = "Attacker replacement"
    # Extra query data is never accepted as the source of the export.
    response = client.get(f"/api/reports/{generated['handle']}/export",
                          params={"title": "Attacker replacement", "recommendations": "Attacker replacement"}, headers=auth)
    assert response.status_code == 200
    assert "Attacker replacement" not in response.text
    assert "Amazon S3" in response.text


def test_report_filename_sanitizes_path_and_header_characters(client, auth, payload):
    payload["title"] = '../../unsafe\r\nX-Fake-Header: yes / project'
    generated = client.post("/api/evaluate", json=payload, headers=auth).json()
    response = client.get(f"/api/reports/{generated['handle']}/export", headers=auth)
    disposition = response.headers["content-disposition"]
    assert "../" not in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert "x-fake-header" not in response.headers


def test_repository_capacity_expiration_and_snapshot_isolation(application, payload):
    result = RuleEvaluator(application.state.knowledge_base).evaluate(payload["title"], payload["answers"])
    repository = ReportRepository(capacity=2)
    first, second, third = (repository.put(result) for _ in range(3))
    assert repository.get(first) is None
    assert repository.get(second) is not None
    result.title = "Mutated after storage"
    assert repository.get(third).title == payload["title"]
    _, stored = repository._reports[second]
    repository._reports[second] = (datetime.now(timezone.utc) - timedelta(hours=3), stored)
    assert repository.get(second) is None


def test_shutdown_clears_ephemeral_reports(application, payload):
    headers = {"x-advisory-token": application.state.api_token}
    with TestClient(application, base_url="http://127.0.0.1") as active_client:
        generated = active_client.post("/api/evaluate", json=payload, headers=headers).json()
        assert application.state.repository.get(generated["handle"]) is not None
    assert application.state.repository.get(generated["handle"]) is None


def test_api_schema_and_documentation_are_not_public_routes(client):
    for route in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(route).status_code == 404

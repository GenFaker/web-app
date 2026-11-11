"""Tests of exported evidence, reproducibility, and unsafe-text boundaries."""
from datetime import datetime, timezone

import pytest

from models.domain import (
    Answer,
    AssessmentResult,
    FrameworkReference,
    RiskEntry,
    ServiceRecommendation,
)
from services.report import AdvisoryReport


def sample_result() -> AssessmentResult:
    """Return one conditional service carrying a planned, self-reported control."""
    risk = RiskEntry(
        id="s3-iam",
        service_id="s3",
        dimension="Identity and Access Management",
        title="Excessive bucket privileges",
        description="Broad access permissions could expose stored objects.",
        action="Use least-privilege bucket policies and IAM roles.",
        severity="high",
        status="planned",
        frameworks=[
            FrameworkReference(
                framework="AWS Well-Architected Framework Security Pillar",
                reference="SEC03: Permissions management",
                url="https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/permissions-management.html",
            ),
            FrameworkReference(
                framework="CIS AWS Foundations Benchmark",
                reference="Identity and Access Management (thematic mapping)",
                url="https://www.cisecurity.org/benchmark/amazon_web_services",
            ),
            FrameworkReference(
                framework="NIST Cybersecurity Framework 2.0",
                reference="PR.AA: Identity Management, Authentication, and Access Control",
                url="https://www.nist.gov/cyberframework",
            ),
        ],
        aws_doc_url="https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html",
        source_question_id="storage_iam",
    )
    return AssessmentResult(
        id="abc12345",
        title="SME storefront",
        created_at=datetime(2026, 11, 11, 3, 4, 5, tzinfo=timezone.utc),
        knowledge_base_version="draft-1.0",
        recommendations=[ServiceRecommendation(
            service_id="s3", name="Amazon S3", category="storage",
            reason="The website may need object storage.", conditional=True,
            risks=[risk],
        )],
        unresolved=["Object storage requirement is not sure."],
        contradictions=["Controls apply although storage was marked absent."],
        limitations=["Only the covered AWS services are considered."],
        answers={"storage_iam": Answer.YES, "storage_needed": Answer.UNSURE},
        summary="A conditional storage recommendation requires clarification.",
        ai_explanations={"s3": "Confirm the object-storage requirement before proceeding."},
    )


def test_markdown_contains_all_appendix_b_components() -> None:
    """The export should remain useful independently of the application's UI."""
    markdown = AdvisoryReport(sample_result()).to_markdown()
    for label in (
        "Assessment summary", "Recommended AWS service", "Reason for recommendation",
        "Identified technical misconfiguration risk", "Risk dimension",
        "Recommended action", "Framework reference", "AWS documentation reference",
        "Assessment limitations and uncertainty",
    ):
        assert label in markdown
    assert "https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html" in markdown
    assert "CIS AWS Foundations Benchmark" in markdown
    assert "NIST Cybersecurity Framework 2\\.0" in markdown
    assert "Conditional" in markdown
    assert "Contradictory answers" in markdown
    assert "2026\\-11\\-11 03:04:05 UTC" in markdown
    assert "draft\\-1\\.0" in markdown
    assert "| storage\\_needed | Not sure |" in markdown


def test_self_reported_control_never_becomes_verified_security_finding() -> None:
    result = sample_result()
    result.unresolved = []
    result.contradictions = []
    markdown = AdvisoryReport(result).to_markdown()
    assert "Planned control \\(self\\-reported; not verified\\)" in markdown
    assert "not a measured risk score" in markdown
    assert "AWS expert validation required" in markdown
    assert "does not scan" in markdown
    assert "not verified compliance findings" in markdown
    assert "absence of a warning" in markdown.lower()


def test_user_and_provider_text_cannot_create_markdown_or_html_links() -> None:
    result = sample_result()
    result.title = '![click](javascript:alert(1))\n# Injected <script>alert(2)</script>'
    result.ai_explanations["s3"] = '<img src=x onerror=alert(1)> [download](https://evil.example)'
    markdown = AdvisoryReport(result).to_markdown()
    assert "<script>" not in markdown
    assert "<img" not in markdown
    assert "![click]" not in markdown
    assert "[download](https://evil.example)" not in markdown
    assert "\n# Injected" not in markdown
    assert "&lt;script&gt;" in markdown
    assert r"\[download\]\(https://evil\.example\)" in markdown


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "https://docs.aws.amazon.com.evil.example/guide",
    "https://evil.example/x",
    "https://username:password@docs.aws.amazon.com/x",
    "http://docs.aws.amazon.com/guide",
    "https://docs.aws.amazon.com:444/guide",
    "https://docs.aws.amazon.com/guide\nmalformed",
])
def test_nonofficial_or_malformed_references_are_not_exported_as_links(bad_url: str) -> None:
    result = sample_result()
    result.recommendations[0].risks[0].aws_doc_url = bad_url
    markdown = AdvisoryReport(result).to_markdown()
    assert "**AWS documentation reference:** Documentation URL unavailable" in markdown
    assert f"]({bad_url})" not in markdown


def test_markdown_link_parentheses_cannot_terminate_reference() -> None:
    result = sample_result()
    result.recommendations[0].risks[0].aws_doc_url = "https://docs.aws.amazon.com/guide(topic).html"
    markdown = AdvisoryReport(result).to_markdown()
    assert "https://docs.aws.amazon.com/guide%28topic%29.html" in markdown


@pytest.mark.parametrize("title", ["../../CON\r\nInjected", "NUL", "店铺网络", "Café online", "a" * 200])
def test_export_filename_is_portable_and_cannot_escape_download_directory(title: str) -> None:
    result = sample_result()
    result.title = title
    filename = AdvisoryReport(result).filename()
    assert filename.startswith("aws-advisory-")
    assert filename.endswith("-abc12345.md")
    assert filename.isascii()
    assert len(filename) < 110
    assert not any(char in filename for char in ('/', '\\', ':', '\r', '\n', ' '))
    assert ".." not in filename


def test_export_rejects_unknown_extension() -> None:
    with pytest.raises(ValueError, match="extension"):
        AdvisoryReport(sample_result()).filename("exe")


def test_plain_text_preserves_appendix_b_and_utf8_content_without_markdown_links() -> None:
    result = sample_result()
    result.title = "Café storefront"
    text = AdvisoryReport(result).to_text()
    assert "Café storefront" in text
    assert "ASSESSMENT SUMMARY" in text
    assert "Recommended AWS service: Amazon S3" in text
    assert "Reason for recommendation:" in text
    assert "Risk dimension:" in text
    assert "Recommended action:" in text
    assert "Framework reference:" in text
    assert "AWS documentation reference: https://docs.aws.amazon.com/" in text
    assert "storage_needed: Not sure" in text
    assert "2026-11-11 03:04:05 UTC" in text
    assert "**" not in text
    assert "](" not in text
    assert text.encode("utf-8").decode("utf-8") == text


def test_export_is_a_snapshot_and_does_not_mutate_engine_result() -> None:
    result = sample_result()
    report = AdvisoryReport(result)
    before = report.to_markdown()
    result.title = "Changed later"
    result.recommendations[0].reason = "Changed rule reason"
    assert report.to_markdown() == before
    assert result.title == "Changed later"


def test_empty_assessment_does_not_imply_secure_configuration() -> None:
    result = sample_result()
    result.recommendations = []
    result.ai_explanations = {}
    markdown = AdvisoryReport(result).to_markdown()
    assert "No covered AWS service was selected" in markdown
    assert "Review unresolved requirements before deployment" in markdown
    assert "does not establish that a setting is secure" in markdown


def test_partial_report_records_scope_and_never_promotes_excluded_categories() -> None:
    from models.domain import AssessmentScope
    result = sample_result()
    result.scope = AssessmentScope(mode="partial", categories=["storage"])
    result.assessed_categories = ["storage"]
    result.excluded_categories = ["database", "compute", "network", "security"]
    report = AdvisoryReport(result)
    text = report.to_text()
    assert "Assessment scope: Partial category assessment" in text
    assert "Assessed categories: Storage" in text
    assert "Excluded categories: Database, Compute, Network, Security" in text
    assert "Skipped categories and services have not been assessed" in text
    assert "not treated as secure" in text
    assert "Partial category assessment" in report.to_markdown()


def test_service_control_percentages_use_entries_not_distinct_questions() -> None:
    result = sample_result()
    planned = result.recommendations[0].risks[0]
    gap = planned.model_copy(update={"id": "s3-network", "status": "gap"})
    unresolved = planned.model_copy(update={"id": "s3-data", "status": "unresolved"})
    result.recommendations[0].risks = [planned, gap, unresolved]
    text = AdvisoryReport(result).to_text()
    assert "Denominator: 3 service-control entries" in text
    assert "Planned control (self-reported; not verified): 1 of 3 (33.3%)." in text
    assert "Control gap (self-reported): 1 of 3 (33.3%)." in text
    assert "Unresolved requirement: 1 of 3 (33.3%)." in text
    assert "not counts of distinct questions" in text
    assert "Conditional service candidates are included" in text
    assert "not safety, compliance" in text
    assert "after rounding" in text


def test_empty_report_discloses_unavailable_percentages() -> None:
    result = sample_result()
    result.recommendations = []
    text = AdvisoryReport(result).to_text()
    assert "Denominator: 0 service-control entries" in text
    assert "percentages are unavailable" in text
    assert "(100.0%)" not in text


def test_single_service_scope_target_and_snapshot_metadata_are_preserved() -> None:
    from models.domain import AssessmentScope
    result = sample_result()
    result.scope = AssessmentScope(mode="single", service_id="s3")
    result.assessed_categories = ["storage"]
    result.excluded_categories = ["database", "compute", "network", "security"]
    report = AdvisoryReport(result)
    original = report.to_text()
    result.scope.service_id = "ec2"
    result.excluded_categories.clear()
    assert report.to_text() == original
    assert "Assessment scope: Single-service assessment" in original
    assert "Target service: Amazon S3" in original
    assert "target service was chosen for review by the user" in original
    assert "applicability has not been established" in original

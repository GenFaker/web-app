"""Appendix B advisory exports from fixed, typed assessment results.

The report preserves the rule engine's decisions. Provider text is supplemental,
never a replacement for the knowledge base's risks, actions, or references.
"""
from __future__ import annotations

from datetime import timezone
import html
import re
import unicodedata
from urllib.parse import quote, urlsplit

from models.domain import AssessmentResult, RiskEntry


_STATUS_LABELS: dict[str, str] = {
    "gap": "Control gap (self-reported)",
    "unresolved": "Unresolved requirement",
    "planned": "Planned control (self-reported; not verified)",
    "not_applicable": "Marked not applicable (not a security assurance)",
}
_ANSWER_LABELS: dict[str, str] = {
    "yes": "Yes",
    "no": "No",
    "not_sure": "Not sure",
    "not_applicable": "Not applicable",
}
_DEFAULT_LIMITATION = (
    "This pre-deployment advisory uses stated requirements only. It does not scan, "
    "audit, monitor, provision, configure, or deploy AWS resources. It is not a "
    "security certification or organizational-policy compliance assessment. "
    "Absence of a warning does not establish that a setting is secure."
)
_MAPPING_LIMITATION = (
    "Framework references are thematic knowledge-base mappings, not verified "
    "compliance findings or a claim that any benchmark requirement has been met. "
    "The checklist, rule matrix, and mappings require qualified AWS expert validation."
)


def _inline(value: object) -> str:
    """Escape untrusted values as one Markdown text line, disabling HTML and links."""
    normalized = " ".join(str(value).split())
    escaped = html.escape(normalized, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", escaped)


def _plain(value: object) -> str:
    """Keep plain-text labels on one line without terminal control characters."""
    return " ".join("".join(c for c in str(value) if c.isprintable() or c.isspace()).split())


def _documentation_url(value: str) -> str | None:
    """Allow HTTPS references on the three frameworks' official publishing domains."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        official = any(
            host == domain or host.endswith("." + domain)
            for domain in ("aws.amazon.com", "nist.gov", "cisecurity.org")
        )
        if (
            parsed.scheme != "https"
            or not official
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or any(ord(char) < 32 or char.isspace() for char in value)
        ):
            return None
        # Parentheses and angle brackets must not terminate a Markdown link.
        return quote(value, safe=":/#?=&%+@;.,_-~")
    except ValueError:
        return None


class AdvisoryReport:
    """Create stable Markdown and plain-text snapshots of an assessment.

    All Appendix B fields come from the deterministic result. The snapshot is
    copied so later UI state changes cannot alter an export already prepared.
    Filesystem writes are left to the desktop/API boundary.
    """

    def __init__(self, result: AssessmentResult) -> None:
        """Capture the result without retaining provider credentials or settings."""
        self.result = result.model_copy(deep=True)

    def filename(self, extension: str = "md") -> str:
        """Return a portable filename without paths, shell syntax, or reserved names."""
        if extension not in ("md", "txt"):
            raise ValueError("Report extension must be 'md' or 'txt'.")
        title = unicodedata.normalize("NFKD", self.result.title)
        ascii_title = title.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_title).strip("-").lower()[:60]
        slug = slug or "assessment"
        identifier = re.sub(r"[^a-zA-Z0-9]", "", self.result.id)[:12] or "report"
        return f"aws-advisory-{slug}-{identifier}.{extension}"

    def _created_at(self) -> str:
        """Format timestamps explicitly in UTC, including a stable timezone suffix."""
        created_at = self.result.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _limitations(self) -> list[str]:
        """Keep universal boundaries visible even when an engine supplies no limits."""
        return list(dict.fromkeys([_DEFAULT_LIMITATION, _MAPPING_LIMITATION, *self.result.limitations]))

    def _scope_lines(self) -> list[str]:
        """Describe exactly which categories or target service the snapshot covers."""
        scope = self.result.scope
        label = {"full": "Full assessment", "partial": "Partial category assessment", "single": "Single-service assessment"}[scope.mode]
        lines = [f"Assessment scope: {label}"]
        if scope.mode == "single":
            target = next((s.name for s in self.result.recommendations if s.service_id == scope.service_id), scope.service_id)
            lines.append(f"Target service: {target}")
        assessed = self.result.assessed_categories
        excluded = self.result.excluded_categories
        lines.append("Assessed categories: " + (", ".join(c.title() for c in assessed) or "Not recorded"))
        lines.append("Excluded categories: " + (", ".join(c.title() for c in excluded) or "None"))
        if scope.mode != "full":
            lines.append("Skipped categories and services have not been assessed. They are not treated as secure, absent, or not applicable.")
        if scope.mode == "single":
            lines.append("The target service was chosen for review by the user. A conditional decision means that its applicability has not been established by the supplied requirements.")
        return lines

    def _control_summary_lines(self) -> list[str]:
        """Count service-control entries, keeping the denominator explicit and unweighted."""
        entries = [risk for service in self.result.recommendations for risk in service.risks]
        total = len(entries)
        lines = [f"Denominator: {total} service-control entries across the services in this report."]
        if total:
            for status in ("planned", "gap", "unresolved", "not_applicable"):
                count = sum(risk.status == status for risk in entries)
                lines.append(f"{_STATUS_LABELS[status]}: {count} of {total} ({100 * count / total:.1f}%).")
        else:
            lines.append("No service-control entries were returned; percentages are unavailable.")
        lines.append("Each service-control entry is counted once. Services may share a checklist control, so these counts are not counts of distinct questions. Conditional service candidates are included when present.")
        lines.append("These percentages describe self-reported planning statuses. They are not safety, compliance, implementation-verification, or measured risk scores. Percentages may differ slightly from 100% after rounding.")
        return lines

    @staticmethod
    def _markdown_risk(risk: RiskEntry) -> list[str]:
        """Render a risk without allowing provider or user text to replace its fields."""
        lines = [
            f"#### {_inline(risk.title)}",
            "",
            f"- **Identified technical misconfiguration risk:** {_inline(risk.description)}",
            f"- **Risk dimension:** {_inline(risk.dimension)}",
            f"- **Status:** {_inline(_STATUS_LABELS[risk.status])}",
            f"- **Advisory priority:** {_inline(risk.severity.title())} (rule-assigned; not a measured risk score)",
            f"- **Recommended action:** {_inline(risk.action)}",
            f"- **Source checklist item:** {_inline(risk.source_question_id)}",
            "- **Framework reference:**",
        ]
        for reference in risk.frameworks:
            label = _inline(f"{reference.framework}: {reference.reference}")
            url = _documentation_url(reference.url)
            lines.append(f"  - [{label}]({url})" if url else f"  - {label} (documentation URL unavailable)")
        if not risk.frameworks:
            lines.append("  - Mapping unavailable; requires expert review.")
        url = _documentation_url(risk.aws_doc_url)
        lines.append(
            f"- **AWS documentation reference:** [Official AWS documentation]({url})"
            if url else "- **AWS documentation reference:** Documentation URL unavailable; verify with an AWS expert."
        )
        lines.append("")
        return lines

    def to_markdown(self) -> str:
        """Export every Appendix B advisory component, plus reproducibility metadata."""
        result = self.result
        lines = [
            f"# AWS pre-deployment cybersecurity advisory: {_inline(result.title)}",
            "",
            f"> {_inline(result.review_status)}",
            "",
            f"- **Assessment ID:** {_inline(result.id)}",
            f"- **Created:** {_inline(self._created_at())}",
            f"- **Knowledge-base version:** {_inline(result.knowledge_base_version)}",
            f"- **Explanation provider requested:** {_inline(result.provider_requested)}",
            f"- **Explanation provider used:** {_inline(result.provider_used)}",
            f"- **Provider notice:** {_inline(result.provider_notice)}",
            "",
            "## Assessment scope",
            "",
            *[f"- {_inline(line)}" for line in self._scope_lines()],
            "",
            "## Assessment summary",
            "",
            _inline(result.summary),
            "",
            "## Service-control status distribution",
            "",
            *[f"- {_inline(line)}" for line in self._control_summary_lines()],
            "",
            "## Unresolved requirements and contradictory answers",
            "",
        ]
        if result.unresolved:
            lines.extend(f"- **Unresolved:** {_inline(item)}" for item in result.unresolved)
        if result.contradictions:
            lines.extend(f"- **Contradictory answers:** {_inline(item)}" for item in result.contradictions)
        if not result.unresolved and not result.contradictions:
            lines.append("No unresolved requirements or contradictory answers were identified by the current rules.")
        lines.extend(["", "## Recommended AWS services", ""])
        if not result.recommendations:
            lines.append("No covered AWS service was selected from the stated requirements. Review unresolved requirements before deployment.")
            lines.append("")
        for service in result.recommendations:
            lines.extend([
                f"### {_inline(service.name)}",
                "",
                f"- **Recommended AWS service:** {_inline(service.name)} ({_inline(service.service_id)})",
                f"- **Category:** {_inline(service.category)}",
                f"- **Decision:** {'Conditional — clarify the unresolved requirement before adopting this service.' if service.conditional else 'Selected by deterministic requirements rules.'}",
                f"- **Reason for recommendation:** {_inline(service.reason)}",
                "",
            ])
            explanation = result.ai_explanations.get(service.service_id)
            if explanation:
                label = "Offline supplemental explanation" if result.provider_used == "mock" else "AI supplemental explanation (unverified)"
                lines.extend([f"**{label}:** {_inline(explanation)}", ""])
            if service.risks:
                for risk in service.risks:
                    lines.extend(self._markdown_risk(risk))
            else:
                lines.extend(["No technical risk entries were returned. This is not a finding that the service is secure.", ""])
        lines.extend(["## Assessment limitations and uncertainty", ""])
        lines.extend(f"- {_inline(item)}" for item in self._limitations())
        lines.extend([
            "",
            "## Appendix: recorded checklist responses",
            "",
            "These are self-reported planning answers, not observations of deployed AWS resources.",
            "",
            "| Checklist item ID | Response |",
            "| --- | --- |",
        ])
        for question_id, answer in sorted(result.answers.items()):
            lines.append(f"| {_inline(question_id)} | {_ANSWER_LABELS[answer.value]} |")
        return "\n".join(lines).rstrip() + "\n"

    def to_text(self) -> str:
        """Export a readable UTF-8 plain-text report with no Markdown formatting."""
        result = self.result
        lines = [
            f"AWS PRE-DEPLOYMENT CYBERSECURITY ADVISORY: {_plain(result.title)}",
            _plain(result.review_status),
            "",
            f"Assessment ID: {_plain(result.id)}",
            f"Created: {self._created_at()}",
            f"Knowledge-base version: {_plain(result.knowledge_base_version)}",
            f"Explanation provider requested: {_plain(result.provider_requested)}",
            f"Explanation provider used: {_plain(result.provider_used)}",
            f"Provider notice: {_plain(result.provider_notice)}",
            "",
            "ASSESSMENT SCOPE",
            *self._scope_lines(),
            "",
            "ASSESSMENT SUMMARY",
            _plain(result.summary),
            "",
            "SERVICE-CONTROL STATUS DISTRIBUTION",
            *self._control_summary_lines(),
            "",
            "UNRESOLVED REQUIREMENTS AND CONTRADICTORY ANSWERS",
        ]
        lines.extend(f"Unresolved: {_plain(item)}" for item in result.unresolved)
        lines.extend(f"Contradictory answers: {_plain(item)}" for item in result.contradictions)
        if not result.unresolved and not result.contradictions:
            lines.append("None identified by the current rules.")
        lines.extend(["", "RECOMMENDED AWS SERVICES"])
        if not result.recommendations:
            lines.append("No covered service selected. Review unresolved requirements before deployment.")
        for service in result.recommendations:
            lines.extend([
                "",
                f"Recommended AWS service: {_plain(service.name)} ({_plain(service.service_id)})",
                f"Category: {_plain(service.category)}",
                "Decision: " + ("Conditional; clarify before adopting." if service.conditional else "Selected by deterministic requirements rules."),
                f"Reason for recommendation: {_plain(service.reason)}",
            ])
            explanation = result.ai_explanations.get(service.service_id)
            if explanation:
                label = "Offline supplemental explanation" if result.provider_used == "mock" else "AI supplemental explanation (unverified)"
                lines.append(f"{label}: {_plain(explanation)}")
            for risk in service.risks:
                lines.extend([
                    "",
                    f"Identified technical misconfiguration risk: {_plain(risk.title)} — {_plain(risk.description)}",
                    f"Risk dimension: {_plain(risk.dimension)}",
                    f"Status: {_STATUS_LABELS[risk.status]}",
                    f"Advisory priority: {risk.severity.title()} (rule-assigned; not a measured risk score)",
                    f"Recommended action: {_plain(risk.action)}",
                    f"Source checklist item: {_plain(risk.source_question_id)}",
                    "Framework reference:",
                ])
                for reference in risk.frameworks:
                    url = _documentation_url(reference.url) or "Documentation URL unavailable"
                    lines.append(f"  {_plain(reference.framework)}: {_plain(reference.reference)} — {url}")
                if not risk.frameworks:
                    lines.append("  Mapping unavailable; requires expert review.")
                url = _documentation_url(risk.aws_doc_url) or "Documentation URL unavailable; verify with an AWS expert"
                lines.append(f"AWS documentation reference: {url}")
            if not service.risks:
                lines.append("No risk entries returned; this is not a finding that the service is secure.")
        lines.extend(["", "ASSESSMENT LIMITATIONS AND UNCERTAINTY"])
        lines.extend(f"- {_plain(item)}" for item in self._limitations())
        lines.extend(["", "APPENDIX: RECORDED CHECKLIST RESPONSES", "Self-reported planning answers; not observations of deployed resources."])
        lines.extend(f"{_plain(question_id)}: {_ANSWER_LABELS[answer.value]}" for question_id, answer in sorted(result.answers.items()))
        return "\n".join(lines).rstrip() + "\n"

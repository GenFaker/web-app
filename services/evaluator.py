"""Deterministic, versioned requirement evaluation independent of any AI provider.

The embedded knowledge base is a draft research instrument. Its transparent
selection rules and thematic framework mappings require qualified AWS expert
review before use as an expert-validated benchmark.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from models.domain import Answer, AssessmentResult, AssessmentScope, Question, RiskEntry, ServiceRecommendation


class KnowledgeBase:
    """Load and validate the structured, source-linked advisory rule matrix."""

    DIMENSIONS = (
        "Identity and Access Management", "Network Security", "Data Security",
        "Monitoring", "Configuration",
    )

    def __init__(self, path: str | Path | None = None) -> None:
        """Read the bundled rules, or an explicitly provided review copy."""
        source = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "knowledge_base.json"
        try:
            self._data: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
            self.version: str = self._data["version"]
            self.categories: list[dict[str, str]] = self._data["categories"]
            self.questions: list[Question] = [Question.model_validate(q) for q in self._data["questions"]]
            self.services: dict[str, dict[str, Any]] = self._data["services"]
            self.review_status: str = self._data["review_status"]
            self._validate_structure()
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("The advisory knowledge base could not be loaded or is invalid.") from exc

    def _validate_structure(self) -> None:
        """Fail closed on malformed rules or missing dimensions and references."""
        question_ids = {q.id for q in self.questions}
        if len(question_ids) != len(self.questions):
            raise ValueError("Knowledge base contains duplicate question IDs.")
        expected_categories = {"storage", "database", "compute", "network", "security"}
        if {c["id"] for c in self.categories} != expected_categories:
            raise ValueError("Knowledge base must cover all five service categories.")
        for category in expected_categories:
            controls = [q for q in self.questions if q.category == category and q.kind == "control"]
            if len(controls) != 5 or {q.dimension for q in controls} != set(self.DIMENSIONS):
                raise ValueError(f"Incomplete control matrix for {category}.")
        for service_id, service in self.services.items():
            if service["category"] not in expected_categories:
                raise ValueError(f"Unknown category for {service_id}.")
            for trigger in service.get("any_yes", []):
                if trigger not in question_ids:
                    raise ValueError(f"Unknown selection trigger {trigger}.")
            risks = service["risks"]
            if len(risks) != 5 or {r["dimension"] for r in risks} != set(self.DIMENSIONS):
                raise ValueError(f"Incomplete risk matrix for {service_id}.")
            for rule in risks:
                RiskEntry.model_validate({**rule, "service_id": service_id, "status": "unresolved"})
                if rule["source_question_id"] not in question_ids:
                    raise ValueError(f"Unknown risk control for {service_id}.")
                if urlsplit(rule["aws_doc_url"]).hostname != "docs.aws.amazon.com":
                    raise ValueError("Every risk must reference official AWS documentation.")
                if len(rule["frameworks"]) != 3:
                    raise ValueError("Every risk must include all three framework mappings.")

    def public_data(self) -> dict[str, Any]:
        """Return frontend-safe question metadata and transparent selection rules."""
        return {
            "version": self.version,
            "review_status": self.review_status,
            "categories": self.categories,
            "questions": [q.model_dump() for q in self.questions],
            "dimensions": list(self.DIMENSIONS),
            "service_count": len(self.services),
            "services": [
                {"id": service_id, "name": service["name"], "category": service["category"],
                   "requirement_ids": list(service.get("any_yes", [])),
                   "description": service["reason"],
                   "guidance": [{"title": risk["title"], "action": risk["action"],
                                 "url": risk["aws_doc_url"]} for risk in service["risks"]]}
                for service_id, service in self.services.items()
            ],
        }

    def normalize_scope(self, scope: AssessmentScope | None = None) -> AssessmentScope:
        """Validate coverage against this knowledge base and normalize category order."""
        scope = (scope or AssessmentScope()).model_copy(deep=True)
        covered = [category["id"] for category in self.categories]
        if scope.mode == "full":
            if scope.categories or scope.service_id is not None:
                raise ValueError("Full assessment cannot specify categories or a single service.")
        elif scope.mode == "partial":
            if not scope.categories or scope.service_id is not None:
                raise ValueError("Partial assessment requires categories and no single service.")
            if len(set(scope.categories)) != len(scope.categories):
                raise ValueError("Assessment categories must be unique.")
            if set(scope.categories) - set(covered):
                raise ValueError("Assessment contains unknown categories.")
            scope.categories = [category for category in covered if category in scope.categories]
        elif scope.mode == "single":
            if scope.categories or scope.service_id not in self.services:
                raise ValueError("Single-service assessment requires a covered service and no categories.")
        else:
            raise ValueError("Unknown assessment mode.")
        return scope

    def scope_categories(self, scope: AssessmentScope | None = None) -> list[str]:
        """Return the ordered categories included in an explicitly validated scope."""
        scope = self.normalize_scope(scope)
        if scope.mode == "full":
            return [category["id"] for category in self.categories]
        if scope.mode == "partial":
            return list(scope.categories)
        return [self.services[scope.service_id]["category"]]

    def scope_questions(self, scope: AssessmentScope | None = None) -> list[Question]:
        """Return only questions the user must answer for this assessment scope."""
        scope = self.normalize_scope(scope)
        categories = self.scope_categories(scope)
        if scope.mode == "single":
            requirement_ids = set(self.services[scope.service_id].get("any_yes", []))
            return [question for question in self.questions
                    if question.id in requirement_ids or
                    (question.category in categories and question.kind == "control")]
        return [question for question in self.questions if question.category in categories]

    def validate_answers(self, answers: dict[str, Answer], scope: AssessmentScope | None = None) -> None:
        """Require every scoped answer and reject unasked questions without filling gaps."""
        expected = {question.id for question in self.scope_questions(scope)}
        supplied = set(answers)
        missing, unknown = expected - supplied, supplied - expected
        if missing or unknown:
            details = []
            if missing:
                details.append("Missing responses: " + ", ".join(sorted(missing)))
            if unknown:
                details.append("Unknown questions: " + ", ".join(sorted(unknown)))
            raise ValueError("; ".join(details))
        for question_id, value in answers.items():
            try:
                Answer(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid response for {question_id}.") from exc


class RuleEvaluator:
    """Select covered AWS services and curated risks using only checklist rules.

    Requirements select services; controls never select services. ``Not sure``
    yields a conditional candidate rather than a confirmed recommendation. A
    selected service's ``Not applicable`` control remains unresolved because this
    instrument does not collect the evidence required to waive that control.
    """

    def __init__(self, knowledge_base: KnowledgeBase) -> None:
        self.knowledge_base = knowledge_base

    def evaluate(self, title: str, answers: dict[str, Answer], scope: AssessmentScope | None = None) -> AssessmentResult:
        """Create an Appendix B advisory with deterministic decisions and trace IDs."""
        scope = self.knowledge_base.normalize_scope(scope)
        self.knowledge_base.validate_answers(answers, scope)
        scoped_questions = self.knowledge_base.scope_questions(scope)
        assessed_categories = self.knowledge_base.scope_categories(scope)
        excluded_categories = [category["id"] for category in self.knowledge_base.categories
                               if category["id"] not in assessed_categories]
        allowed_services = {
            service_id for service_id, service in self.knowledge_base.services.items()
            if (scope.mode == "single" and service_id == scope.service_id)
            or (scope.mode != "single" and service["category"] in assessed_categories)
        }
        clean_title = title.strip()
        if not clean_title or len(clean_title) > 100:
            raise ValueError("Project title must contain 1–100 characters.")
        normalized = {key: Answer(value) for key, value in answers.items()}
        selections: dict[str, tuple[bool, str]] = {}
        unresolved = [f"{q.text} — requirement unresolved."
                      for q in scoped_questions
                      if q.kind == "requirement" and normalized[q.id] == Answer.UNSURE]
        contradictions: list[str] = []

        # Direct service triggers are held in the reviewable JSON rule matrix.
        for service_id, service in self.knowledge_base.services.items():
            if service_id not in allowed_services:
                continue
            triggers = service.get("any_yes", [])
            positive = [t for t in triggers if normalized[t] == Answer.YES]
            uncertain = [t for t in triggers if normalized[t] == Answer.UNSURE]
            if positive:
                selections[service_id] = (False, service["reason"])
            elif uncertain:
                selections[service_id] = (True, "Conditional candidate: " + service["reason"] + " Confirm the unresolved requirement before choosing this service.")

        # A single-service review is explicitly requested by the user. Retain the
        # target even when applicability is unresolved, without treating it as a
        # recommendation that the deployment requires it.
        if scope.mode == "single":
            service_id = scope.service_id
            service = self.knowledge_base.services[service_id]
            if service_id not in selections:
                selections[service_id] = (True, "User-requested single-service review: the stated requirements do not establish that " + service["name"] + " is required. Confirm applicability before adopting it; the controls below are reviewed only for this chosen target.")
                unresolved.append(service["name"] + ": applicability has not been established by a positive requirement. The target was selected for review, not automatically recommended.")
            else:
                conditional, reason = selections[service_id]
                selections[service_id] = (conditional, "User-requested single-service review. " + reason)

        # Security groups apply to VPC interfaces, EC2, RDS, or an explicitly
        # requested VPC ingress boundary; Lambda alone does not imply a VPC.
        dependencies = [s for s in ("ec2", "rds") if s in selections]
        if dependencies and "security_groups" in allowed_services:
            conditional = all(selections[s][0] for s in dependencies)
            if "security_groups" not in selections or not conditional:
                selections["security_groups"] = (conditional, "Security Groups provide the stateful network access boundary for the selected EC2 or RDS resources.")

        if scope.mode != "single" and normalized.get("security_private_ingress") == Answer.YES and not any(s in selections for s in ("ec2", "rds", "lambda")):
            if not {"compute", "database"}.issubset(assessed_categories):
                unresolved.append("Security Groups: the VPC-attached workload is outside the selected coverage. Review compute/database applicability in a full assessment; skipped requirements have not been answered.")
            else:
                contradictions.append("A private network ingress boundary is requested, but no compute or database workload was identified. Confirm the VPC-attached resource before configuring Security Groups.")
            selections["security_groups"] = (True, "Conditional candidate: Security Groups may be needed for VPC-attached resources; the workload requiring this boundary is unresolved.")

        if normalized.get("security_tls") in (Answer.NO, Answer.NA) and any(normalized.get(q) == Answer.YES for q in ("network_domain", "network_delivery", "network_global_delivery")):
            contradictions.append("Public website delivery or a custom domain is requested while HTTPS certificate management is declined or marked not applicable. Confirm the TLS plan; a CloudFront default certificate may cover its default hostname, while custom HTTPS domains require a suitable certificate.")

        # Any confirmed AWS service still needs IAM even when the direct
        # identity requirement is uncertain or declined. Dependencies cannot be
        # downgraded by an unresolved optional requirement answer.
        other_services = [state for key, state in selections.items() if key != "iam"]
        if other_services and "iam" in allowed_services and ("iam" not in selections or any(not state[0] for state in other_services)):
            conditional = all(state[0] for state in other_services)
            selections["iam"] = (conditional, "AWS IAM governs identities, roles, and least-privilege access for the AWS services identified by this assessment.")
        if scope.mode != "single" and other_services and normalized.get("security_access") in (Answer.NO, Answer.NA):
            contradictions.append("AWS identity and permission management is declined or marked not applicable while covered AWS services are identified. IAM remains a service dependency; confirm the intended human and workload access model.")

        selected_categories = {self.knowledge_base.services[s]["category"] for s in selections}
        for category in self.knowledge_base.categories:
            category_id = category["id"]
            if category_id not in assessed_categories:
                continue
            controls = [q for q in scoped_questions if q.category == category_id and q.kind == "control"]
            if category_id not in selected_categories and any(normalized[q.id] == Answer.YES for q in controls):
                contradictions.append(f"{category['name']} controls are marked as planned, but no applicable service requirement was identified for that category. Confirm applicability.")
            if category_id in selected_categories:
                for question in controls:
                    answer = normalized[question.id]
                    if answer == Answer.UNSURE:
                        unresolved.append(f"{category['name']} / {question.dimension}: the planned control is not yet confirmed.")
                    elif answer == Answer.NA:
                        unresolved.append(f"{category['name']} / {question.dimension}: marked not applicable for a selected service. Supply an applicability justification during expert review; the control is not waived.")

        recommendations: list[ServiceRecommendation] = []
        for service_id, service in self.knowledge_base.services.items():
            if service_id not in selections:
                continue
            conditional, reason = selections[service_id]
            risks: list[RiskEntry] = []
            for rule in service["risks"]:
                answer = normalized[rule["source_question_id"]]
                status = "planned" if answer == Answer.YES else "gap" if answer == Answer.NO else "unresolved"
                risks.append(RiskEntry.model_validate({**rule, "service_id": service_id, "status": status}))
            recommendations.append(ServiceRecommendation(service_id=service_id, name=service["name"], category=service["category"], reason=reason, conditional=conditional, risks=risks))

        canonical = json.dumps({"title": clean_title, "answers": {k: v.value for k, v in sorted(normalized.items())}, "kb": self.knowledge_base.version, "scope": scope.model_dump()}, sort_keys=True)
        identifier = "ADV-" + hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()
        confirmed = sum(not r.conditional for r in recommendations)
        conditional_count = len(recommendations) - confirmed
        gaps = sum(r.status == "gap" for service in recommendations for r in service.risks)
        summary = f"{confirmed} confirmed service recommendation(s), {conditional_count} conditional candidate(s), and {gaps} service-specific stated control gap(s). {len(unresolved)} requirement or control clarification(s) remain unresolved."
        if not recommendations:
            summary += " No covered AWS service requirement was confirmed; clarify the deployment requirements before selecting services."
        limitations = [
            "Draft research instrument: the checklist, service-selection rules, risk matrix, and thematic framework mappings require validation by a qualified AWS security expert. No expert approval is claimed.",
            "Pre-deployment advisory based only on stated requirements. No AWS account is accessed; resources are not scanned, audited, provisioned, configured, or deployed.",
            "A planned control is self-reported and has not been verified. A stated gap is an advisory concern, not a detected live vulnerability. Conditional candidates require requirement confirmation.",
            "Only the ten covered AWS services and five technical risk dimensions are assessed. An absent warning does not establish security. Organizational policy, legal, contractual, and regulatory compliance are outside scope.",
            "Framework mappings are thematic guidance, not a compliance score or certification. CIS control numbering varies by benchmark version and must be verified by an expert against the edition adopted for the study.",
            "Selection rules identify applicability within the covered services, not cost, performance, region availability, or full architectural suitability. Multiple positive workload requirements may legitimately select both alternatives.",
        ]
        if scope.mode != "full":
            excluded_names = [category["name"] for category in self.knowledge_base.categories
                              if category["id"] in excluded_categories]
            target = self.knowledge_base.services[scope.service_id]["name"] if scope.mode == "single" else ", ".join(assessed_categories)
            limitations.insert(0, "Assessment coverage: " + scope.mode + " review of " + target + ". Excluded categories: " + ", ".join(excluded_names) + ". Skipped requirements and controls were not assessed, were not answered No or Not applicable, and do not establish security.")
            limitations.insert(1, "Dependencies and interactions outside the selected scope, including identity/permissions, network access, certificates, and other workloads, require a separate or full assessment. Their absence from this report is not a waiver.")
            if scope.mode == "single":
                summary = "Single-service review of " + self.knowledge_base.services[scope.service_id]["name"] + ": " + summary
            else:
                summary = "Partial category assessment: " + summary
        return AssessmentResult(id=identifier, title=clean_title, knowledge_base_version=self.knowledge_base.version, recommendations=recommendations, unresolved=unresolved, contradictions=contradictions, limitations=limitations, answers=normalized, summary=summary, review_status=self.knowledge_base.review_status, scope=scope, assessed_categories=assessed_categories, excluded_categories=excluded_categories)

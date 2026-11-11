"""Typed domain objects shared by the rule engine, providers, and presentation layer."""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Answer(str, Enum):
    YES = "yes"
    NO = "no"
    UNSURE = "not_sure"
    NA = "not_applicable"

class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Question(DomainModel):
    id: str
    category: str
    dimension: str
    kind: Literal["requirement", "control"]
    text: str
    help: str

class FrameworkReference(DomainModel):
    framework: str
    reference: str
    url: str

class RiskEntry(DomainModel):
    id: str
    service_id: str
    dimension: str
    title: str
    description: str
    action: str
    severity: Literal["high", "medium", "low"]
    status: Literal["gap", "unresolved", "planned", "not_applicable"]
    frameworks: list[FrameworkReference]
    aws_doc_url: str
    source_question_id: str

class ServiceRecommendation(DomainModel):
    service_id: str
    name: str
    category: str
    reason: str
    conditional: bool = False
    risks: list[RiskEntry] = Field(default_factory=list)

class AssessmentScope(DomainModel):
    """Explicit assessment coverage; skipped requirements are never answers."""
    mode: Literal["full", "partial", "single"] = "full"
    categories: list[str] = Field(default_factory=list, max_length=5)
    service_id: str | None = Field(default=None, max_length=64)

class AssessmentResult(DomainModel):
    id: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    knowledge_base_version: str
    recommendations: list[ServiceRecommendation]
    unresolved: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    answers: dict[str, Answer]
    summary: str
    provider_requested: str = "mock"
    provider_used: str = "mock"
    provider_notice: str = "Offline explanations. No information sent to an AI service."
    ai_explanations: dict[str, str] = Field(default_factory=dict)
    review_status: str = "Draft instrument — AWS expert validation required"
    scope: AssessmentScope = Field(default_factory=AssessmentScope)
    assessed_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)

class ProviderSettings(DomainModel):
    provider: Literal["mock", "gemini", "groq", "ollama", "openai", "claude"] = "mock"
    api_key: str = Field(default="", max_length=512, repr=False)
    model: str = Field(default="", max_length=120, pattern=r"^[a-zA-Z0-9_.:/-]*$")
    ollama_url: str = Field(default="http://127.0.0.1:11434", max_length=200)

class AssessmentRequest(DomainModel):
    title: str = Field(default="Untitled website", min_length=1, max_length=100)
    answers: dict[str, Answer] = Field(max_length=100)
    settings: ProviderSettings = Field(default_factory=ProviderSettings)
    scope: AssessmentScope = Field(default_factory=AssessmentScope)
    assessment_id: str | None = Field(default=None, max_length=64)
    revision: int | None = Field(default=None, ge=1)

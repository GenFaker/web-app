"""Validated inputs for persistent assessment folders, drafts, and planning."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, field_validator

from models.domain import Answer, AssessmentScope, DomainModel


class FolderCreate(DomainModel):
    """Create one logical workspace folder; its name is never a filesystem path."""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        """Reject blank or control-character names while preserving Unicode."""
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Folder name must contain visible text.")
        return value


class AssessmentCreate(DomainModel):
    """Establish a saved assessment and its scope before answering questions."""

    title: str = Field(min_length=1, max_length=100)
    scope: AssessmentScope = Field(default_factory=AssessmentScope)
    folder_id: str = Field(min_length=1, max_length=64)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Assessment title must contain visible text.")
        return value


class AssessmentUpdate(DomainModel):
    """Save draft inputs using optimistic revision checking to prevent lost edits."""

    title: str = Field(min_length=1, max_length=100)
    answers: dict[str, Answer] = Field(default_factory=dict, max_length=100)
    notes: str = Field(default="", max_length=20000)
    plan_status: Literal["planning", "in_progress", "ready"] = "planning"
    revision: int = Field(ge=1)
    selected_category: str | None = Field(default=None, max_length=40)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return AssessmentCreate.clean_title(value)


class FolderRename(FolderCreate):
    revision: int = Field(ge=1)


class FolderRemoval(DomainModel):
    revision: int = Field(ge=1)
    destination_id: str | None = Field(default=None, min_length=1, max_length=64)


class AssessmentMove(DomainModel):
    revision: int = Field(ge=1)
    folder_id: str = Field(min_length=1, max_length=64)

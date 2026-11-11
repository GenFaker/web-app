"""Persistence, history immutability, and lost-update protection for workspaces."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from models.domain import Answer, AssessmentScope
from models.workspace import AssessmentCreate, AssessmentUpdate, FolderCreate
from services.evaluator import KnowledgeBase, RuleEvaluator
from services.storage import PersistentWorkspace, StorageConflict, StorageError, StorageNotFound, default_data_directory


@pytest.fixture()
def repository(tmp_path):
    return PersistentWorkspace(tmp_path / "workspace")


@pytest.fixture()
def complete_inputs():
    kb = KnowledgeBase()
    return {question.id: (Answer.NO if question.kind == "requirement" else Answer.NA) for question in kb.questions}


def save(repository, assessment, answers=None, **changes):
    payload = {"title": assessment["title"], "answers": answers if answers is not None else assessment["answers"], "notes": assessment["notes"], "plan_status": assessment["plan_status"], "expected_revision": assessment["revision"], "selected_category": assessment["selected_category"]}
    payload.update(changes)
    return repository.save_assessment(assessment["id"], **payload)


def test_database_and_empty_folder_are_created_outside_source(tmp_path):
    target = tmp_path / "local-data"
    assert not target.exists()
    workspace = PersistentWorkspace(target)
    assert workspace.database_path == target / "assessments" / "aegis.sqlite3"
    assert workspace.database_path.is_file()
    assert workspace.list_folders() == []
    assert workspace.list_assessments() == []


def test_folder_and_assessment_persist_after_repository_restart(repository):
    folder = repository.create_folder("Customer portal")
    first = repository.create_assessment("Initial architecture", {"mode": "full"}, folder["id"])
    saved = save(repository, first, {"storage_files": Answer.YES}, notes="Plan: scope the upload path.", plan_status="in_progress", selected_category="storage")
    reopened = PersistentWorkspace(repository.directory)
    actual = reopened.get_assessment(first["id"])
    assert actual == saved
    assert actual["answers"] == {"storage_files": "yes"}
    assert actual["notes"] == "Plan: scope the upload path."
    assert actual["plan_status"] == "in_progress"
    assert actual["selected_category"] == "storage"
    assert reopened.list_folders()[0]["assessment_count"] == 1
    assert reopened.list_assessments(folder["id"])[0]["answered_count"] == 1


def test_multiple_independent_assessments_and_folder_filters(repository):
    a = repository.create_folder("Planning")
    b = repository.create_folder("Review")
    one = repository.create_assessment("Full", {"mode": "full"}, a["id"])
    two = repository.create_assessment("S3 review", {"mode": "single", "service_id": "s3"}, b["id"])
    three = repository.create_assessment("Database review", {"mode": "partial", "categories": ["database"]}, folder_id=repository.create_folder("Test folder")["id"])
    assert len(repository.list_assessments()) == 3
    assert {item["id"] for item in repository.list_assessments(a["id"])} == {one["id"]}
    assert {item["id"] for item in repository.list_assessments(b["id"])} == {two["id"]}
    assert repository.get_assessment(three["id"])["scope"]["categories"] == ["database"]
    summaries = repository.list_assessments()
    assert all("answers" in summary and "notes" not in summary for summary in summaries)
    assert all(summary["answered_count"] == len(summary["answers"]) for summary in summaries)
    assert all(summary["report_count"] == 0 for summary in summaries)


def test_new_assessment_has_no_report_and_known_revision(repository):
    result = repository.create_assessment("New plan", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    assert result["revision"] == 1
    assert result["latest_report"] is None
    assert result["latest_report_id"] is None
    assert result["reports"] == []
    assert result["answered_count"] == 0


@pytest.mark.parametrize('old_folder', [None, 'missing-legacy-folder'])
def test_legacy_unfiled_assessments_are_recovered_without_data_loss(repository, complete_inputs, old_folder):
    from services.storage import RECOVERY_FOLDER_ID
    folder = repository.create_folder('Existing folder')
    draft = repository.create_assessment('Legacy assessment', {'mode': 'full'}, folder['id'])
    saved = save(repository, draft, complete_inputs, notes='Keep these notes')
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved['title'], complete_inputs)
    repository.save_report(saved['id'], result, saved['revision'])
    before = repository.get_assessment(saved['id'])
    # Reproduce a database created by an earlier app (or a missing folder reference).
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute('UPDATE assessments SET folder_id=? WHERE id=?', (old_folder, saved['id']))
    recovered = PersistentWorkspace(repository.directory)
    after = recovered.get_assessment(saved['id'])
    assert after['folder_id'] == RECOVERY_FOLDER_ID
    assert after['revision'] == before['revision'] + 1
    for key in ('answers', 'notes', 'title', 'scope', 'reports', 'latest_report', 'created_at', 'updated_at'):
        assert after[key] == before[key]
    assert recovered.list_assessments(RECOVERY_FOLDER_ID)[0]['id'] == saved['id']
    restarted = PersistentWorkspace(repository.directory)
    assert restarted.get_assessment(saved['id']) == after
    assert len(restarted.list_folders()) == 2


def test_stale_revision_does_not_overwrite_new_saved_data(repository):
    draft = repository.create_assessment("Planning", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    latest = save(repository, draft, notes="Latest saved notes")
    with pytest.raises(StorageConflict):
        save(repository, draft, notes="Stale edits")
    assert repository.get_assessment(draft["id"])["notes"] == "Latest saved notes"
    assert latest["revision"] == 2


def test_conflicting_updates_across_repository_instances_are_atomic(repository):
    draft = repository.create_assessment("Shared plan", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    other = PersistentWorkspace(repository.directory)
    def attempt(workspace, notes):
        try:
            save(workspace, draft, notes=notes)
            return "saved"
        except StorageConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: attempt(*pair), [(repository, "First"), (other, "Second")]))
    assert sorted(results) == ["conflict", "saved"]
    assert repository.get_assessment(draft["id"])["revision"] == 2


def test_reports_are_immutable_versions_and_survive_restart(repository, complete_inputs):
    draft = repository.create_assessment("Architecture", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    first = repository.save_report(saved["id"], result, saved["revision"])
    result.summary = "A separately generated explanation revision."
    second = repository.save_report(saved["id"], result, first["revision"])
    result.summary = "An unsaved local mutation."
    reopened = PersistentWorkspace(repository.directory)
    assert reopened.get_report(first["id"]).summary != result.summary
    assert reopened.get_report(first["id"]).summary != reopened.get_report(second["id"]).summary
    current = reopened.get_assessment(saved["id"])
    assert current["report_count"] == 2
    assert current["latest_report_id"] == second["id"]
    assert current["latest_report"]["summary"] == "A separately generated explanation revision."
    assert current["reports"][0]["id"] == second["id"]
    assert current["reports"][0]["assessment_revision"] == first["revision"]
    assert reopened.list_assessments()[0]["report_count"] == 2


def test_report_generation_advances_revision_and_retains_input_snapshot(repository, complete_inputs):
    draft = repository.create_assessment("Architecture", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    repository.save_report(saved["id"], result, saved["revision"])
    assert repository.get_assessment(saved["id"])["revision"] == saved["revision"] + 1


def test_provider_request_settings_are_not_a_storage_input(repository, complete_inputs):
    draft = repository.create_assessment("Architecture", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    result.provider_requested = "groq"
    result.provider_used = "mock"
    snapshot = repository.save_report(saved["id"], result, saved["revision"])
    serialized = repository.get_report(snapshot["id"]).model_dump_json()
    assert '"api_key"' not in serialized
    assert '"settings"' not in serialized
    assert repository.get_report(snapshot["id"]).provider_requested == "groq"


@pytest.mark.parametrize("mismatch", ["revision", "answers", "title", "scope"])
def test_report_cannot_attach_to_mismatched_or_newer_draft(repository, complete_inputs, mismatch):
    draft = repository.create_assessment("Architecture", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    if mismatch == "revision":
        save(repository, saved, notes="Newer plan")
    elif mismatch == "answers":
        result.answers["storage_files"] = Answer.YES
    elif mismatch == "title":
        result.title = "Another project"
    else:
        result.scope = AssessmentScope(mode="partial", categories=["storage"])
    with pytest.raises(StorageConflict):
        repository.save_report(saved["id"], result, saved["revision"])
    assert repository.get_assessment(saved["id"])["report_count"] == 0


def test_new_draft_edits_do_not_modify_historical_report(repository, complete_inputs):
    draft = repository.create_assessment("Initial architecture", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    snapshot = repository.save_report(saved["id"], result, saved["revision"])
    save(repository, repository.get_assessment(saved["id"]), {"storage_files": "yes"}, title="Revised architecture", notes="Prepare a second pass")
    historical = repository.get_report(snapshot["id"])
    assert historical.title == "Initial architecture"
    assert historical.answers["storage_files"] == Answer.NO


def test_names_and_titles_are_text_not_filesystem_or_sql(repository):
    folder = repository.create_folder("../../Outside'; DROP TABLE folders; --")
    assessment = repository.create_assessment("../Review '; DROP TABLE assessments; --", {"mode": "full"}, folder["id"])
    assert repository.list_folders()[0]["name"] == folder["name"]
    assert repository.get_assessment(assessment["id"])["title"] == assessment["title"]
    assert len(repository.list_assessments()) == 1
    assert not (repository.directory.parent / "Outside").exists()


def test_missing_and_invalid_ids_are_safe(repository):
    unknown = "99999999-1111-2222-3333-444444444444"
    assert repository.get_assessment(unknown) is None
    assert repository.get_report(unknown) is None
    assert repository.get_report("unknown-handle") is None
    with pytest.raises(StorageNotFound):
        repository.get_assessment("../../secrets")
    with pytest.raises(StorageNotFound):
        repository.create_assessment("Plan", {"mode": "full"}, unknown)
    with pytest.raises(StorageNotFound):
        repository.save_assessment(unknown, title="Plan", answers={}, notes="", plan_status="planning", expected_revision=1)


def test_delete_checks_revision_and_cascades_reports(repository, complete_inputs):
    folder = repository.create_folder("Folder")
    draft = repository.create_assessment("Plan", {"mode": "full"}, folder["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    snapshot = repository.save_report(saved["id"], result, saved["revision"])
    with pytest.raises(StorageConflict):
        repository.delete_assessment(saved["id"], draft["revision"])
    assert repository.delete_assessment(saved["id"], snapshot["revision"])
    assert repository.get_assessment(saved["id"]) is None
    assert repository.get_report(snapshot["id"]) is None
    assert repository.list_folders()[0]["assessment_count"] == 0
    assert repository.delete_assessment(saved["id"], snapshot["revision"]) is False


@pytest.mark.parametrize("kind", ["folder", "title", "notes", "status", "revision", "unknown_setting"])
def test_workspace_schemas_reject_invalid_inputs(kind):
    with pytest.raises(ValidationError):
        if kind == "folder":
            FolderCreate(name="  ")
        elif kind == "title":
            AssessmentCreate(title="\n", scope={"mode": "full"})
        else:
            payload = {"title": "Plan", "answers": {}, "revision": 1}
            payload.update({"notes": "x" * 20001} if kind == "notes" else {"plan_status": "secure"} if kind == "status" else {"revision": 0} if kind == "revision" else {"api_key": "must-not-be-accepted"})
            AssessmentUpdate(**payload)


def test_corrupt_database_reports_safe_error_and_preserves_file(tmp_path):
    path = tmp_path / "data" / "assessments" / "aegis.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"This is not a SQLite database.")
    with pytest.raises(StorageError, match="workspace could not be opened"):
        PersistentWorkspace(tmp_path / "data")
    assert path.read_bytes() == b"This is not a SQLite database."


def test_future_schema_is_not_silently_downgraded(tmp_path):
    path = tmp_path / "data" / "assessments" / "aegis.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageError, match="newer application version"):
        PersistentWorkspace(tmp_path / "data")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_default_override_has_no_directory_side_effect(tmp_path, monkeypatch):
    target = tmp_path / "chosen-portable-data"
    monkeypatch.setenv("AEGIS_DATA_DIR", str(target))
    assert default_data_directory() == target
    assert not target.exists()


def test_linux_data_directory_uses_absolute_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_DATA_DIR", raising=False)
    monkeypatch.setattr("services.storage.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_data_directory() == tmp_path / "xdg" / "Aegis"


def test_windows_and_mac_data_directories(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_DATA_DIR", raising=False)
    monkeypatch.setattr("services.storage.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_data_directory() == tmp_path / "Local" / "Aegis"
    monkeypatch.setattr("services.storage.sys.platform", "darwin")
    assert default_data_directory().parts[-3:] == ("Library", "Application Support", "Aegis")


@pytest.mark.parametrize("target", ["draft_json", "report_json", "report_schema"])
def test_corrupted_saved_json_is_reported_as_safe_storage_error(repository, complete_inputs, target):
    draft = repository.create_assessment("Plan", {"mode": "full"}, folder_id=repository.create_folder("Test folder")["id"])
    saved = save(repository, draft, complete_inputs)
    result = RuleEvaluator(KnowledgeBase()).evaluate(saved["title"], complete_inputs)
    snapshot = repository.save_report(saved["id"], result, saved["revision"])
    with sqlite3.connect(repository.database_path) as connection:
        if target == "draft_json":
            connection.execute("UPDATE assessments SET answers_json=? WHERE id=?", ("invalid-json", saved["id"]))
        else:
            value = "invalid-json" if target == "report_json" else '{"not_a_report":true}'
            connection.execute("UPDATE reports SET result_json=? WHERE id=?", (value, snapshot["id"]))
    with pytest.raises(StorageError, match="previously saved work is retained"):
        repository.get_assessment(saved["id"])
    if target != "draft_json":
        with pytest.raises(StorageError, match="previously saved work is retained"):
            repository.get_report(snapshot["id"])

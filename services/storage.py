"""Durable, local-only workspace storage with immutable advisory snapshots.

The database is stored outside the source tree and PyInstaller extraction folder.
Folder and project names are display text, never paths. Provider credentials are
not accepted by these interfaces or written into SQLite.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from typing import Any, Iterator
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from pydantic import ValidationError

from models.domain import Answer, AssessmentResult, AssessmentScope
from models.workspace import AssessmentCreate, AssessmentUpdate, FolderCreate

SCHEMA_VERSION = 1
RECOVERY_FOLDER_ID = str(uuid5(NAMESPACE_URL, "aegis:recovered-assessments"))


class StorageError(Exception):
    """A safe local storage failure, without database internals or credentials."""


class StorageConflict(StorageError):
    """An update used a stale revision and must not overwrite newer saved work."""


class StorageNotFound(StorageError):
    """A requested assessment or folder no longer exists."""


def default_data_directory() -> Path:
    """Choose a persistent OS user-data directory without creating anything.

    ``AEGIS_DATA_DIR`` explicitly overrides the platform default for portable
    installations, tests, backups, or managed devices.
    """
    override = os.environ.get("AEGIS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "Aegis"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Aegis"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share") / "Aegis"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: str) -> str:
    """Accept only generated UUID handles; user values never become SQL or paths."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise StorageNotFound("The requested workspace item was not found.") from error


class PersistentWorkspace:
    """SQLite-backed folders, assessment drafts, planning notes, and report history.

    Every operation owns its connection and transaction. Revision checks happen
    under a write transaction, so two application windows cannot silently replace
    one another's edits. Existing report rows are never updated.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.database_path = self.directory / "assessments" / "aegis.sqlite3"
        self._lock = threading.RLock()
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._initialize()
        except (OSError, sqlite3.Error) as error:
            raise StorageError("The local assessment workspace could not be opened. Choose a writable data directory.") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, SCHEMA_VERSION}:
                    raise StorageError("This workspace was created by a newer application version. Update Aegis before opening it.")
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS folders (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS assessments (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        scope_json TEXT NOT NULL,
                        folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
                        answers_json TEXT NOT NULL DEFAULT '{}',
                        notes TEXT NOT NULL DEFAULT '',
                        plan_status TEXT NOT NULL DEFAULT 'planning',
                        selected_category TEXT,
                        revision INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS reports (
                        id TEXT PRIMARY KEY,
                        assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        assessment_revision INTEGER NOT NULL,
                        result_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_assessment_folder ON assessments(folder_id);
                    CREATE INDEX IF NOT EXISTS idx_report_assessment ON reports(assessment_id, created_at DESC);
                    PRAGMA user_version = 1;
                    COMMIT;
                """)
                # Earlier versions allowed unfiled assessments. Repair their folder
                # membership atomically without changing answers or report history.
                connection.execute("BEGIN IMMEDIATE")
                if "revision" not in {row[1] for row in connection.execute("PRAGMA table_info(folders)")}:
                    connection.execute("ALTER TABLE folders ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
                orphaned = connection.execute("""SELECT COUNT(*) FROM assessments a
                    WHERE a.folder_id IS NULL OR NOT EXISTS
                    (SELECT 1 FROM folders f WHERE f.id=a.folder_id)""").fetchone()[0]
                if orphaned:
                    connection.execute("INSERT OR IGNORE INTO folders(id,name,created_at) VALUES(?,?,?)",
                                       (RECOVERY_FOLDER_ID, "Recovered assessments", _now()))
                    connection.execute("""UPDATE assessments SET folder_id=?,revision=revision+1
                        WHERE folder_id IS NULL OR NOT EXISTS
                        (SELECT 1 FROM folders f WHERE f.id=assessments.folder_id)""", (RECOVERY_FOLDER_ID,))
                connection.commit()
            finally:
                connection.close()

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                connection.commit()
            except (sqlite3.Error, json.JSONDecodeError, ValidationError) as error:
                if connection:
                    connection.rollback()
                raise StorageError("The local workspace could not save or read this operation. Your previously saved work is retained.") from error
            except BaseException:
                if connection:
                    connection.rollback()
                raise
            finally:
                if connection:
                    connection.close()

    @staticmethod
    def _assessment_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"],
            "scope": json.loads(row["scope_json"]), "folder_id": row["folder_id"],
            "answers": json.loads(row["answers_json"]), "notes": row["notes"],
            "plan_status": row["plan_status"], "status": row["plan_status"],
            "selected_category": row["selected_category"], "revision": row["revision"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def create_folder(self, name: str) -> dict[str, Any]:
        """Create a logical display folder with an opaque generated ID."""
        name = FolderCreate(name=name).name
        item = {"id": str(uuid4()), "name": name, "created_at": _now(), "assessment_count": 0, "revision": 1}
        with self._transaction(write=True) as connection:
            connection.execute("INSERT INTO folders(id,name,created_at) VALUES(?,?,?)", (item["id"], name, item["created_at"]))
        return item

    def list_folders(self) -> list[dict[str, Any]]:
        """List logical folders and current assessment counts."""
        with self._transaction() as connection:
            rows = connection.execute("""SELECT f.id,f.name,f.created_at,f.revision,COUNT(a.id) AS assessment_count
                FROM folders f LEFT JOIN assessments a ON a.folder_id=f.id
                GROUP BY f.id ORDER BY f.created_at,f.id""").fetchall()
            return [{**dict(row), "recovered": row["id"] == RECOVERY_FOLDER_ID} for row in rows]

    def rename_folder(self, folder_id: str, name: str, revision: int) -> dict[str, Any]:
        identifier = _identifier(folder_id)
        name = FolderCreate(name=name).name
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT revision FROM folders WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise StorageNotFound("Folder not found.")
            if row["revision"] != revision:
                raise StorageConflict("The folder changed. Refresh the library before renaming it.")
            connection.execute("UPDATE folders SET name=?,revision=revision+1 WHERE id=?", (name, identifier))
        return next(folder for folder in self.list_folders() if folder["id"] == identifier)

    def delete_folder(self, folder_id: str, revision: int, destination_id: str | None = None) -> dict[str, Any]:
        """Delete a folder, atomically relocating every assessment when nonempty."""
        identifier = _identifier(folder_id)
        destination = _identifier(destination_id) if destination_id else None
        with self._transaction(write=True) as connection:
            folder = connection.execute("SELECT revision FROM folders WHERE id=?", (identifier,)).fetchone()
            if folder is None:
                raise StorageNotFound("Folder not found.")
            if folder["revision"] != revision:
                raise StorageConflict("The folder changed. Refresh the library before deleting it.")
            if destination == identifier:
                raise StorageConflict("Choose a different destination folder.")
            if destination and not connection.execute("SELECT 1 FROM folders WHERE id=?", (destination,)).fetchone():
                raise StorageNotFound("Destination folder not found.")
            count = connection.execute("SELECT COUNT(*) FROM assessments WHERE folder_id=?", (identifier,)).fetchone()[0]
            if count and not destination:
                raise StorageConflict("This folder contains assessments. Choose a destination to preserve them.")
            if count:
                connection.execute("UPDATE assessments SET folder_id=?,revision=revision+1 WHERE folder_id=?", (destination, identifier))
            connection.execute("DELETE FROM folders WHERE id=?", (identifier,))
        return {"deleted": True, "moved": count, "destination_id": destination}

    def move_assessment(self, assessment_id: str, folder_id: str, revision: int) -> dict[str, Any]:
        identifier, destination = _identifier(assessment_id), _identifier(folder_id)
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT revision FROM assessments WHERE id=?", (identifier,)).fetchone()
            if row is None or not connection.execute("SELECT 1 FROM folders WHERE id=?", (destination,)).fetchone():
                raise StorageNotFound("Assessment or destination folder not found.")
            if row["revision"] != revision:
                raise StorageConflict("The assessment changed. Reopen it before moving it.")
            connection.execute("UPDATE assessments SET folder_id=?,revision=revision+1 WHERE id=?", (destination, identifier))
        return self.get_assessment(identifier)

    def create_assessment(self, title: str, scope: dict[str, Any], folder_id: str | None = None) -> dict[str, Any]:
        """Create an empty draft after the UI chooses its immutable assessment scope."""
        data = AssessmentCreate(title=title, scope=scope, folder_id=folder_id)
        identifier, timestamp = str(uuid4()), _now()
        scope_json = data.scope.model_dump_json()
        folder_id = _identifier(folder_id) if folder_id else None
        with self._transaction(write=True) as connection:
            if folder_id and not connection.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
                raise StorageNotFound("The selected assessment folder was not found.")
            connection.execute("""INSERT INTO assessments(id,title,scope_json,folder_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""", (identifier, data.title, scope_json, folder_id, timestamp, timestamp))
        result = self.get_assessment(identifier)
        assert result is not None
        return result

    def list_assessments(self, folder_id: str | None = None) -> list[dict[str, Any]]:
        """List all drafts/history summaries, optionally filtered to one folder."""
        parameters: tuple[str, ...] = ()
        predicate = ""
        if folder_id:
            parameters = (_identifier(folder_id),)
            predicate = " WHERE a.folder_id=?"
        with self._transaction() as connection:
            rows = connection.execute("SELECT a.*,(SELECT COUNT(*) FROM reports r WHERE r.assessment_id=a.id) AS report_count FROM assessments a" + predicate + " ORDER BY a.updated_at DESC,a.id", parameters).fetchall()
            summaries = []
            for row in rows:
                item = self._assessment_row(row)
                item["answered_count"] = len(item["answers"])
                item["report_count"] = row["report_count"]
                item.pop("notes")
                summaries.append(item)
            return summaries

    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        """Read a saved draft plus ordered immutable report-version summaries."""
        identifier = _identifier(assessment_id)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM assessments WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return None
            result = self._assessment_row(row)
            reports = connection.execute("SELECT * FROM reports WHERE assessment_id=? ORDER BY created_at DESC,id DESC", (identifier,)).fetchall()
            result["reports"] = []
            for report in reports:
                snapshot = AssessmentResult.model_validate_json(report["result_json"])
                result["reports"].append({"id": report["id"], "created_at": report["created_at"], "summary": snapshot.summary, "provider_used": snapshot.provider_used, "assessment_revision": report["assessment_revision"]})
            result["answered_count"] = len(result["answers"])
            result["report_count"] = len(reports)
            result["latest_report_id"] = reports[0]["id"] if reports else None
            result["latest_report"] = json.loads(reports[0]["result_json"]) if reports else None
            return result

    def save_assessment(self, assessment_id: str, *, title: str, answers: dict[str, Answer | str], notes: str, plan_status: str, expected_revision: int, selected_category: str | None = None) -> dict[str, Any]:
        """Atomically save a draft if its revision still matches the caller's copy."""
        identifier = _identifier(assessment_id)
        data = AssessmentUpdate(title=title, answers=answers, notes=notes, plan_status=plan_status, revision=expected_revision, selected_category=selected_category)
        encoded_answers = json.dumps({key: answer.value for key, answer in data.answers.items()}, separators=(",", ":"), ensure_ascii=False)
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT revision FROM assessments WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise StorageNotFound("This assessment was not found.")
            if row["revision"] != expected_revision:
                raise StorageConflict("This assessment changed in another window. Reopen it before saving to avoid overwriting newer work.")
            connection.execute("""UPDATE assessments SET title=?,answers_json=?,notes=?,plan_status=?,selected_category=?,revision=revision+1,updated_at=? WHERE id=?""", (data.title, encoded_answers, data.notes, data.plan_status, data.selected_category, _now(), identifier))
        result = self.get_assessment(identifier)
        assert result is not None
        return result

    def save_report(self, assessment_id: str, result: AssessmentResult, expected_revision: int) -> dict[str, Any]:
        """Persist a report snapshot without modifying its originating checklist.

        AI generation may take time: the revision check prevents attaching an
        advisory to a draft which changed while the provider was responding.
        """
        identifier = _identifier(assessment_id)
        snapshot = result.model_copy(deep=True)
        report_id, timestamp = str(uuid4()), _now()
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT * FROM assessments WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise StorageNotFound("This assessment was not found.")
            if row["revision"] != expected_revision:
                raise StorageConflict("The assessment changed while its advisory was generated. Generate again from the saved version.")
            stored_answers = json.loads(row["answers_json"])
            snapshot_answers = {key: answer.value for key, answer in snapshot.answers.items()}
            if snapshot_answers != stored_answers or snapshot.title != row["title"] or snapshot.scope.model_dump(mode="json") != json.loads(row["scope_json"]):
                raise StorageConflict("The advisory does not match the saved assessment inputs. Save the checklist before generating.")
            connection.execute("INSERT INTO reports(id,assessment_id,created_at,assessment_revision,result_json) VALUES(?,?,?,?,?)", (report_id, identifier, timestamp, expected_revision, snapshot.model_dump_json()))
            connection.execute("UPDATE assessments SET updated_at=?,revision=revision+1 WHERE id=?", (timestamp, identifier))
        return {"id": report_id, "assessment_id": identifier, "created_at": timestamp, "revision": expected_revision + 1}

    def get_report(self, report_id: str) -> AssessmentResult | None:
        """Read a typed historical snapshot independent of later draft edits."""
        try:
            identifier = _identifier(report_id)
        except StorageNotFound:
            return None
        with self._transaction() as connection:
            row = connection.execute("SELECT result_json FROM reports WHERE id=?", (identifier,)).fetchone()
            return AssessmentResult.model_validate_json(row["result_json"]) if row else None

    def delete_assessment(self, assessment_id: str, expected_revision: int) -> bool:
        """Delete a selected draft and its report history after the UI confirms it."""
        identifier = _identifier(assessment_id)
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT revision FROM assessments WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return False
            if row["revision"] != expected_revision:
                raise StorageConflict("The assessment changed. Reopen it before deleting.")
            connection.execute("DELETE FROM assessments WHERE id=?", (identifier,))
            return True

    def close(self) -> None:
        """Compatibility lifecycle hook; all connections close after each operation."""

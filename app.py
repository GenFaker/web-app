"""Independent browser-based AWS security workspace with an isolated data store.

Run ``python app.py`` and open the printed URL in any modern browser.
"""
from __future__ import annotations

import os
import argparse
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import socket
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from models.domain import AssessmentRequest, AssessmentResult, AssessmentScope
from models.workspace import AssessmentCreate, AssessmentUpdate, FolderCreate, FolderRename, FolderRemoval, AssessmentMove
from services.evaluator import KnowledgeBase, RuleEvaluator
from services.llm_client import enrich_report
from services.report import AdvisoryReport
from services.storage import (
    PersistentWorkspace, StorageConflict, StorageError, StorageNotFound,
    default_data_directory,
)

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# Accommodate 20,000-character planning notes, including escaped Unicode JSON.
MAX_BODY_BYTES = 256 * 1024


def harden(response: Response) -> Response:
    """Apply privacy and browser defenses to successes and rejected requests alike."""
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    return response


def reject(detail: str, status_code: int) -> Response:
    """Return a sanitized, hardened local API rejection."""
    return harden(JSONResponse({"detail": detail}, status_code=status_code))


class ReportRepository:
    """Bounded in-memory report storage; expires data after two hours or process exit."""

    def __init__(self, capacity: int = 32) -> None:
        self.capacity = capacity
        self._reports: OrderedDict[str, tuple[datetime, AssessmentResult]] = OrderedDict()

    def put(self, result: AssessmentResult) -> str:
        """Store a snapshot with a random handle distinct from its deterministic input ID."""
        handle = secrets.token_urlsafe(18)
        self._reports[handle] = (datetime.now(timezone.utc), result.model_copy(deep=True))
        self._expire()
        while len(self._reports) > self.capacity:
            self._reports.popitem(last=False)
        return handle

    def get(self, handle: str) -> AssessmentResult | None:
        """Read a live report, or return None after expiration."""
        self._expire()
        entry = self._reports.get(handle)
        return entry[1] if entry else None

    def _expire(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        for handle in list(self._reports):
            if self._reports[handle][0] < cutoff:
                del self._reports[handle]


def create_app(data_directory: Path | None = None) -> FastAPI:
    """Construct isolated application state for native use, browser use, and testing."""
    kb = KnowledgeBase(RESOURCE_ROOT / "data" / "knowledge_base.json")
    repository = ReportRepository()
    workspace = PersistentWorkspace(data_directory or Path(os.environ.get("AEGIS_WEB_DATA_DIR", RESOURCE_ROOT / ".runtime")))
    generation_lock = asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        repository._reports.clear()

    app = FastAPI(title="AWS BALLAN ADVISORY", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.api_token = secrets.token_urlsafe(32)
    app.state.repository = repository
    app.state.knowledge_base = kb
    app.state.workspace = workspace

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        """Reject DNS rebinding, cross-origin calls, oversized bodies, and untrusted API clients."""
        if request.url.hostname not in {"127.0.0.1", "localhost"}:
            return reject("Only local application hosts are allowed.", 403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return reject("Cross-origin requests are not allowed.", 403)
        if request.url.path.startswith("/api/"):
            token = request.headers.get("x-advisory-token", "")
            if not secrets.compare_digest(token, app.state.api_token):
                return reject("Reopen the application to restore your session.", 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    return reject("Request is too large.", 413)
            request._body = bytes(body)
        response = await call_next(request)
        return harden(response)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError):
        # Pydantic's default response echoes input, which might include an API key.
        fields = [".".join(str(p) for p in e["loc"]) for e in error.errors()]
        if "body.folder_id" in fields:
            return JSONResponse({"detail": "Choose an existing folder before creating an assessment."}, status_code=422)
        return JSONResponse({"detail": "Invalid assessment fields: " + ", ".join(fields)}, status_code=422)

    @app.exception_handler(StorageConflict)
    async def storage_conflict(_: Request, error: StorageConflict):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(StorageNotFound)
    async def storage_not_found(_: Request, error: StorageNotFound):
        return JSONResponse({"detail": "The assessment or folder could not be found."}, status_code=404)

    @app.exception_handler(StorageError)
    async def storage_error(_: Request, error: StorageError):
        return JSONResponse({"detail": "The local assessment database is unavailable. Check the application data folder and available disk space."}, status_code=500)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (RESOURCE_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__APP_TOKEN__", app.state.api_token))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/knowledge")
    async def knowledge() -> dict:
        return kb.public_data()

    @app.get("/api/workspace")
    async def workspace_overview() -> dict:
        return {"folders": workspace.list_folders(), "assessments": workspace.list_assessments()}

    @app.post("/api/folders", status_code=201)
    async def create_folder(payload: FolderCreate) -> dict:
        return workspace.create_folder(payload.name)

    @app.patch("/api/folders/{folder_id}")
    async def rename_folder(folder_id: str, payload: FolderRename) -> dict:
        return workspace.rename_folder(folder_id, payload.name, payload.revision)

    @app.delete("/api/folders/{folder_id}")
    async def delete_folder(folder_id: str, payload: FolderRemoval) -> dict:
        return workspace.delete_folder(folder_id, payload.revision, payload.destination_id)

    @app.patch("/api/assessments/{assessment_id}/folder")
    async def move_assessment(assessment_id: str, payload: AssessmentMove) -> dict:
        return workspace.move_assessment(assessment_id, payload.folder_id, payload.revision)

    @app.post("/api/assessments", status_code=201)
    async def create_assessment(payload: AssessmentCreate) -> dict:
        try:
            scope = kb.normalize_scope(payload.scope)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return workspace.create_assessment(payload.title.strip(), scope.model_dump(mode="json"), payload.folder_id)

    @app.get("/api/assessments/{assessment_id}")
    async def get_assessment(assessment_id: str) -> dict:
        assessment = workspace.get_assessment(assessment_id)
        if assessment is None:
            raise HTTPException(status_code=404, detail="Assessment not found.")
        return assessment

    @app.put("/api/assessments/{assessment_id}")
    async def save_assessment(assessment_id: str, payload: AssessmentUpdate) -> dict:
        assessment = workspace.get_assessment(assessment_id)
        if assessment is None:
            raise HTTPException(status_code=404, detail="Assessment not found.")
        scope = AssessmentScope.model_validate(assessment["scope"])
        allowed_questions = {q.id for q in kb.scope_questions(scope)}
        if set(payload.answers) - allowed_questions:
            raise HTTPException(status_code=422, detail="Draft contains answers outside its assessment scope.")
        if payload.selected_category is not None and payload.selected_category not in kb.scope_categories(scope):
            raise HTTPException(status_code=422, detail="Selected category is outside this assessment scope.")
        return workspace.save_assessment(
            assessment_id, title=payload.title.strip(), answers=payload.answers,
            notes=payload.notes, plan_status=payload.plan_status,
            expected_revision=payload.revision, selected_category=payload.selected_category,
        )

    @app.delete("/api/assessments/{assessment_id}")
    async def delete_assessment(assessment_id: str, revision: int) -> dict:
        if revision < 1:
            raise HTTPException(status_code=422, detail="A current assessment revision is required.")
        if not workspace.delete_assessment(assessment_id, revision):
            raise HTTPException(status_code=404, detail="Assessment not found.")
        return {"deleted": True}

    @app.post("/api/evaluate")
    async def evaluate(payload: AssessmentRequest) -> dict:
        try:
            scope = kb.normalize_scope(payload.scope)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        if payload.assessment_id:
            assessment = workspace.get_assessment(payload.assessment_id)
            if assessment is None:
                raise HTTPException(status_code=404, detail="Assessment not found.")
            persisted_answers = {key: str(value.value) for key, value in payload.answers.items()}
            if (payload.revision != assessment["revision"]
                    or scope.model_dump(mode="json") != assessment["scope"]
                    or persisted_answers != assessment["answers"]
                    or payload.title.strip() != assessment["title"]):
                raise HTTPException(status_code=409, detail="Save the current draft before generating its advisory.")
        try:
            result = RuleEvaluator(kb).evaluate(payload.title.strip() or "Untitled website", payload.answers, scope)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        async with generation_lock:
            result = await enrich_report(result, payload.settings)
        if payload.assessment_id:
            snapshot = workspace.save_report(payload.assessment_id, result, payload.revision)
            return {"report": result.model_dump(mode="json"), "handle": snapshot["id"],
                    "assessment": workspace.get_assessment(payload.assessment_id)}
        handle = repository.put(result)
        return {"report": result.model_dump(mode="json"), "handle": handle}

    @app.get("/api/reports/{handle}")
    async def get_report(handle: str) -> dict:
        result = workspace.get_report(handle) or repository.get(handle)
        if result is None:
            raise HTTPException(status_code=404, detail="Report not found or expired.")
        return {"report": result.model_dump(mode="json"), "handle": handle}

    @app.get("/api/reports/{handle}/export")
    async def export(handle: str, format: str = "md") -> Response:
        if format not in {"md", "txt"}:
            raise HTTPException(status_code=400, detail="Choose md or txt export.")
        result = workspace.get_report(handle) or repository.get(handle)
        if result is None:
            raise HTTPException(status_code=404, detail="Report expired. Generate the assessment again.")
        report = AdvisoryReport(result)
        content = report.to_markdown() if format == "md" else report.to_text()
        media_type = "text/markdown" if format == "md" else "text/plain"
        return Response(content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{report.filename(format)}"'})

    app.mount("/static", StaticFiles(directory=RESOURCE_ROOT / "static"), name="static")
    return app


def main() -> None:
    """Run the independent browser application; no desktop renderer is required."""
    parser = argparse.ArgumentParser(description="AWS BALLAN ADVISORY · Web workspace")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.bind(("127.0.0.1", args.port))
    except OSError:
        server_socket.close()
        parser.exit(1, "Could not open the local port. Close the other server or choose another --port.\n")
    url = f"http://127.0.0.1:{server_socket.getsockname()[1]}"
    print(f"AWS BALLAN ADVISORY is running at {url} — open this URL in your browser.", flush=True)
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning", access_log=False))
    server.run(sockets=[server_socket])


if __name__ == "__main__":
    main()

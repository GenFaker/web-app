# AWS BALLAN ADVISORY — Cloud Security Console

Independent browser-based edition of the cybersecurity capstone. The original
desktop application in the parent directory is retained; all new web work lives here.

## Run locally

```powershell
cd web-app
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:8767** in your browser. From this repository, the existing
parent environment also works: `..\.venv\Scripts\python.exe app.py`.
Use `--port 8768` to choose another port.

The browser app has no PyWebView dependency. Its SQLite data lives in
`web-app/.runtime/`; override this with `AEGIS_WEB_DATA_DIR`. It never reads or
modifies the desktop database by default. Existing compatible data can be copied
into that directory with both applications stopped; startup recovers unfiled records.

## Web experience

- Blue command-center design, horizontal navigation, responsive layouts, dark/light themes.
- Interactive architecture graph: service search, filters, pan, zoom, expand, pause motion,
  and official AWS remediation guides. Reduced-motion preferences are respected.
- Drag individual service atoms with a mouse or touch; their connection lines follow.
  Focus a service and use arrow keys (Shift for larger steps) to move it with a keyboard.
  Rearrangements survive filtering within the session; Reset view restores the layout.
- Assessment requirements and risk dimensions use separate cards, prominent dimension
  headings, and per-card answer counts. Advisory findings also have separate risk cards.
- Assessment-derived charts; no simulated telemetry or claims of a live AWS scan.
- Folder search and sorting, renaming, empty-folder deletion, and deletion with relocation
  of contained records. Assessments and advisory history are preserved during folder moves.
- Assessment grid/list modes, sorting, read-only records, explicit editing, individual
  record moves, and confirmed record deletion.
- Optional Claude and Groq narration, with a connection test in AI settings. Provider credentials
  remain in session memory, not persisted. The deterministic rule engine owns findings.

This deliverable runs as a **single-user local web application**. Public hosting and
multi-user authentication are not configured. Loopback binding, same-origin checks,
session request tokens, and response security headers remain enabled.

## Validation

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Browser checks require Chrome and an isolated test server. Set `AEGIS_WEB_DATA_DIR`
to a temporary folder and start `python app.py --port 8767`; then run
`python tests/web_browser.py`. Do not run write-oriented tests against personal data.

## Claude and Groq diagnostics

Claude and Groq are the only hosted integrations. Offline mode remains available
for deterministic guidance and clearly labeled fallback; it is not an API provider.
Defaults: `claude-sonnet-5-5` and Groq `qwen/qwen3.8-27b` (preview).
You can override the model with an identifier available to your provider account.

Open **AI settings**, select the provider, enter the API key, and click **Test
connection**. This generates a small synthetic explanation to check authentication,
model access, and output validation; usage charges may apply. Save the settings,
then generate a new advisory. Existing saved snapshots remain unchanged.
Authentication, quota, unavailable-model, timeout, and invalid-output failures are
shown explicitly. A fallback result is labeled offline, never as successful AI output.
Keys remain in session memory and survive in-app home navigation; refreshing or
closing the page clears them. Do not put keys in source files or GitHub.

Provider contract and persistence tests use mocked HTTP, not live credentials.
Model lifecycle references:
- https://console.groq.com/docs/deprecations
- https://platform.claude.com/docs/en/about-claude/model-deprecations

"""
FastAPI server for the Telegram Mini App.

Provides REST endpoints for the contacts dashboard and JSON import.
Serves the miniapp/ static files at the root URL.
Runs alongside the bot inside the same asyncio event loop.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database.repository import Repository
from .importer.telegram_export import TelegramExportImporter

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).parent.parent / "miniapp"

app = FastAPI(title="TG Agent Mini App API", docs_url=None, redoc_url=None)

# Will be injected at startup by runner.py
_repo: Optional[Repository] = None
_owner_name: str = ""
_active_connection_id: str = ""


def configure(repo: Repository, owner_name: str, connection_id: str = "") -> None:
    """Inject runtime dependencies (called from runner.py before serving)."""
    global _repo, _owner_name, _active_connection_id
    _repo = repo
    _owner_name = owner_name
    _active_connection_id = connection_id


# ── Static files ──────────────────────────────────────────────────────────────

if MINIAPP_DIR.exists():
    # Serve CSS/JS from /static/
    app.mount("/static", StaticFiles(directory=MINIAPP_DIR), name="static")


@app.get("/", include_in_schema=False)
async def serve_index():
    index = MINIAPP_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Mini App not found")
    return FileResponse(index)


# ── Contacts endpoint ─────────────────────────────────────────────────────────

@app.get("/api/contacts")
async def get_contacts(connection_id: Optional[str] = None):
    """Return all unique contacts for the Mini App dashboard."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")

    # Pass connection_id if explicitly given; otherwise get_all_contacts returns ALL contacts
    conn_id = connection_id or _active_connection_id
    contacts = await _repo.get_all_contacts(conn_id)
    return JSONResponse(content={"contacts": contacts, "connection_id": conn_id})


# ── History endpoint ──────────────────────────────────────────────────────────

@app.get("/api/history/{chat_id}")
async def get_history(chat_id: int):
    """Return the recent message history for a specific chat."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")

    messages = await _repo.get_chat_history(chat_id)
    return JSONResponse(content={"messages": messages})


# ── Import endpoint ───────────────────────────────────────────────────────────

@app.post("/api/import")
async def import_chat(
    chat_id: int = Form(...),
    connection_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
):
    """Accept a Telegram JSON export and import it for a specific chat_id."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")

    conn_id = connection_id or _active_connection_id or "default"

    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files are accepted")

    # Save to a temporary location so the importer can open it
    import tempfile, os

    suffix = ".json"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)

    try:
        contents = await file.read()
        tmp_path.write_bytes(contents)

        importer = TelegramExportImporter(repo=_repo, owner_name=_owner_name)
        count = await importer.import_file(
            path=tmp_path,
            chat_id=chat_id,
            connection_id=conn_id,
        )
    except Exception as e:
        logger.error("Import failed: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return JSONResponse(
        content={
            "status": "ok",
            "imported": count,
            "chat_id": chat_id,
            "connection_id": conn_id,
        }
    )


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}

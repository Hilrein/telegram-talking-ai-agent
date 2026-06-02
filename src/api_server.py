import logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database.repository import Repository
from .importer.telegram_export import TelegramExportImporter

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).parent.parent / "miniapp"

app = FastAPI(title="TG Agent Mini App API", docs_url=None, redoc_url=None)

_repo: Optional[Repository] = None
_owner_name: str = ""
_active_connection_id: str = ""
_api_token: str = ""
_handler = None


def configure(
    repo: Repository,
    owner_name: str,
    connection_id: str = "",
    api_token: str = "",
    handler=None,
) -> None:
    global _repo, _owner_name, _active_connection_id, _api_token, _handler
    _repo = repo
    _owner_name = owner_name
    _active_connection_id = connection_id
    _api_token = api_token
    _handler = handler


def verify_token(authorization: Optional[str] = Header(None)) -> None:
    if not _api_token:
        raise HTTPException(status_code=503, detail="API token not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[len("Bearer "):].strip()
    if token != _api_token:
        raise HTTPException(status_code=403, detail="Invalid token")


# ── Static files ──────────────────────────────────────────────────────────────

if MINIAPP_DIR.exists():
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
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")

    conn_id = connection_id or _active_connection_id
    contacts = await _repo.get_all_contacts(conn_id)
    return JSONResponse(content={"contacts": contacts, "connection_id": conn_id})


# ── History endpoint ──────────────────────────────────────────────────────────

@app.get("/api/history/{chat_id}")
async def get_history(chat_id: int):
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
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")

    conn_id = connection_id or _active_connection_id or "default"

    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files are accepted")

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


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(payload: dict):
    if not _api_token:
        raise HTTPException(status_code=503, detail="API token not configured")
    submitted = (payload or {}).get("token", "").strip()
    if not submitted or submitted != _api_token:
        raise HTTPException(status_code=403, detail="Invalid token")
    return {"access_token": _api_token, "token_type": "bearer"}


# ── Business connection management ───────────────────────────────────────────

@app.get("/api/connection/status", dependencies=[Depends(verify_token)])
async def connection_status():
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    if not _active_connection_id:
        return {"connected": False, "connection_id": "", "is_enabled": False}
    conn = await _repo.get_business_connection(_active_connection_id)
    if not conn:
        return {"connected": False, "connection_id": _active_connection_id, "is_enabled": False}
    return {
        "connected": True,
        "connection_id": conn["connection_id"],
        "user_id": conn["user_id"],
        "user_name": conn["user_name"],
        "is_enabled": conn["is_enabled"],
        "can_reply": conn["can_reply"],
    }


@app.post("/api/connection/disable", dependencies=[Depends(verify_token)])
async def connection_disable():
    if _repo is None or not _active_connection_id:
        raise HTTPException(status_code=503, detail="No active connection")
    updated = await _repo.set_connection_enabled(_active_connection_id, False)
    if not updated:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disabled", "connection_id": _active_connection_id}


@app.post("/api/connection/enable", dependencies=[Depends(verify_token)])
async def connection_enable():
    if _repo is None or not _active_connection_id:
        raise HTTPException(status_code=503, detail="No active connection")
    updated = await _repo.set_connection_enabled(_active_connection_id, True)
    if not updated:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "enabled", "connection_id": _active_connection_id}


# ── Settings (runtime AI style prompt) ───────────────────────────────────────

@app.get("/api/settings/style", dependencies=[Depends(verify_token)])
async def settings_get_style():
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    value = await _repo.get_setting("style_prompt")
    return {"value": value or "", "source": "db" if value else "default"}


@app.post("/api/settings/style", dependencies=[Depends(verify_token)])
async def settings_set_style(payload: dict):
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    value = (payload or {}).get("value", "")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="`value` must be a string")
    await _repo.set_setting("style_prompt", value)
    if _handler is not None:
        _handler.set_style_prompt(value)
    return {"status": "ok", "length": len(value)}

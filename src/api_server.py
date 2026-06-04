import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database.repository import Repository
from .database.agent_repository import AgentRepository
from .ai.nvidia_client import NvidiaClient
from .importer.telegram_export import TelegramExportImporter

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).parent.parent / "miniapp"

app = FastAPI(title="TG Agent Mini App API", docs_url=None, redoc_url=None)

_repo: Optional[Repository] = None
_owner_name: str = ""
_active_connection_id: str = ""
_api_token: str = ""
_handler = None
_ai_client: Optional[NvidiaClient] = None
_agent_db_path: Optional[Path] = None


def configure(
    repo: Repository,
    owner_name: str,
    connection_id: str = "",
    api_token: str = "",
    handler=None,
    ai_client: Optional[NvidiaClient] = None,
    agent_db_path: Optional[Path] = None,
) -> None:
    global _repo, _owner_name, _active_connection_id, _api_token, _handler
    global _ai_client, _agent_db_path
    _repo = repo
    _owner_name = owner_name
    _active_connection_id = connection_id
    _api_token = api_token
    _handler = handler
    _ai_client = ai_client
    _agent_db_path = agent_db_path


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

    conn_id = connection_id
    if not conn_id:
        conn_id = await _get_active_conn_id()
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

    conn_id = connection_id
    if not conn_id:
        conn_id = await _get_active_conn_id()
    if not conn_id:
        conn_id = "default"

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


# ── Agent: Tasks CRUD ─────────────────────────────────────────────────────────


async def _get_agent_repo() -> AgentRepository:
    if _agent_db_path is None:
        raise HTTPException(status_code=503, detail="Agent subsystem not configured")
    repo = AgentRepository(_agent_db_path)
    await repo.connect()
    return repo


@app.get("/api/agent/tasks", dependencies=[Depends(verify_token)])
async def get_agent_tasks():
    repo = await _get_agent_repo()
    try:
        tasks = await repo.get_active_tasks()
        return JSONResponse(content={"tasks": tasks})
    finally:
        await repo.close()


@app.post("/api/agent/tasks", dependencies=[Depends(verify_token)])
async def create_agent_task(payload: dict):
    task_type = (payload or {}).get("task_type", "")
    execution_time = (payload or {}).get("execution_time")
    prompt = (payload or {}).get("prompt", "")

    if task_type not in ("every_hour", "every_day", "every_week"):
        raise HTTPException(status_code=400, detail="task_type must be every_hour, every_day, or every_week")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    repo = await _get_agent_repo()
    try:
        task_id = await repo.add_task(
            task_type=task_type,
            prompt=prompt,
            execution_time=execution_time,
        )
        task = await repo.get_task_by_id(task_id)
    finally:
        await repo.close()

    if task:
        from .business.scheduler import add_scheduled_job
        add_scheduled_job(task)

    return JSONResponse(content={"task": task}, status_code=201)


@app.delete("/api/agent/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def delete_agent_task(task_id: int):
    repo = await _get_agent_repo()
    try:
        deleted = await repo.delete_task(task_id)
    finally:
        await repo.close()

    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")

    from .business.scheduler import remove_scheduled_job
    remove_scheduled_job(task_id)
    return JSONResponse(content={"status": "deleted", "task_id": task_id})


# ── Agent: Interactive chat & sessions ───────────────────────────────────────

@app.get("/api/agent/chats", dependencies=[Depends(verify_token)])
async def get_agent_chats():
    repo = await _get_agent_repo()
    try:
        sessions = await repo.get_all_sessions()
        return JSONResponse(content={"sessions": sessions})
    finally:
        await repo.close()

@app.post("/api/agent/chats", dependencies=[Depends(verify_token)])
async def create_agent_chat():
    import uuid
    session_id = uuid.uuid4().hex
    # Default title
    title = "Новый чат"
    repo = await _get_agent_repo()
    try:
        await repo.create_session(session_id, title)
        return JSONResponse(content={"session_id": session_id, "title": title}, status_code=201)
    finally:
        await repo.close()

@app.delete("/api/agent/chats/{session_id}", dependencies=[Depends(verify_token)])
async def delete_agent_chat(session_id: str):
    repo = await _get_agent_repo()
    try:
        deleted = await repo.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return JSONResponse(content={"status": "deleted", "session_id": session_id})
    finally:
        await repo.close()

_AGENT_SYSTEM_PROMPT = (
    "You are an autonomous AI agent running on the user's PC. "
    "You have access to tools: file search, web search, Git management, "
    "and PostgreSQL backup. When the user gives you a task, choose the "
    "most appropriate tool, execute it, and report the result concisely. "
    "If no tool is needed, just answer the question directly. "
    "Reply in the same language the user uses."
)


@app.post("/api/agent/chat", dependencies=[Depends(verify_token)])
async def agent_chat_endpoint(payload: dict):
    message = (payload or {}).get("message", "").strip()
    session_id = (payload or {}).get("session_id", "web_default")

    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    if _ai_client is None:
        raise HTTPException(status_code=503, detail="AI client not configured")

    repo = await _get_agent_repo()
    try:
        await repo.save_history_message(
            session_id=session_id,
            role="user",
            content=message,
        )

        # Rename chat if it's the first user message
        history = await repo.get_session_history(session_id, limit=30)
        
        # If there's only 1 message (the one we just inserted) or we haven't set a title yet
        if len(history) == 1:
            title_words = message.split()[:5]
            title = " ".join(title_words)
            if len(message.split()) > 5:
                title += "..."
            await repo.update_session_title(session_id, title)

        messages: list[dict[str, str]] = [{"role": "system", "content": _AGENT_SYSTEM_PROMPT}]
        for h in history:
            if h["role"] in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})

        response_text = await _ai_client.agent_chat(
            messages=messages,
            session_id=session_id,
            agent_repo=repo,
        )
    finally:
        await repo.close()

    return JSONResponse(content={"response": response_text, "session_id": session_id})


# ── Agent: History ────────────────────────────────────────────────────────────

@app.get("/api/agent/history", dependencies=[Depends(verify_token)])
async def get_agent_history(session_id: str = "web_default", limit: int = 50):
    repo = await _get_agent_repo()
    try:
        messages = await repo.get_session_history(session_id, limit=limit)
    finally:
        await repo.close()
    return JSONResponse(content={"messages": messages, "session_id": session_id})


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

async def _get_active_conn_id() -> str:
    global _active_connection_id
    if _active_connection_id:
        return _active_connection_id
    # Fallback: get the latest connection from DB
    if _repo and hasattr(_repo, "db"):
        # Just fetch the first connection we can find in the DB
        async with _repo.db.execute("SELECT connection_id FROM business_connections ORDER BY updated_at DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                _active_connection_id = row["connection_id"]
                return _active_connection_id
    return ""

@app.get("/api/connection/status", dependencies=[Depends(verify_token)])
async def connection_status():
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    conn_id = await _get_active_conn_id()
    if not conn_id:
        return {"connected": False, "connection_id": "", "is_enabled": False}
    conn = await _repo.get_business_connection(conn_id)
    if not conn:
        return {"connected": False, "connection_id": conn_id, "is_enabled": False}
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
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    conn_id = await _get_active_conn_id()
    if not conn_id:
        raise HTTPException(status_code=503, detail="No active connection")
    updated = await _repo.set_connection_enabled(conn_id, False)
    if not updated:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "disabled", "connection_id": conn_id}

@app.post("/api/connection/enable", dependencies=[Depends(verify_token)])
async def connection_enable():
    if _repo is None:
        raise HTTPException(status_code=503, detail="Repository not ready")
    conn_id = await _get_active_conn_id()
    if not conn_id:
        raise HTTPException(status_code=503, detail="No active connection")
    updated = await _repo.set_connection_enabled(conn_id, True)
    if not updated:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"status": "enabled", "connection_id": conn_id}





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

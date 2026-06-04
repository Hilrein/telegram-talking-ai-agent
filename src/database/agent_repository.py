import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Schema ──────────────────────────────────────────────────────────

AGENT_SCHEMA = """\
CREATE TABLE IF NOT EXISTS agent_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT    NOT NULL CHECK(task_type IN ('every_day', 'every_hour', 'every_week')),
    execution_time  TEXT,               -- e.g. "09:00", NULL for every_hour
    prompt          TEXT    NOT NULL,    -- prompt text sent to the LLM
    is_active       BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_history_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
    content         TEXT    NOT NULL,
    timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_active
    ON agent_tasks(is_active);
CREATE INDEX IF NOT EXISTS idx_agent_history_session
    ON agent_history_new(session_id, timestamp);
"""


# ── Repository ──────────────────────────────────────────────────────

class AgentRepository:

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.executescript(AGENT_SCHEMA)
        
        # Migration from old agent_history to agent_history_new
        cursor = await self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_history'")
        has_old_history = await cursor.fetchone()
        if has_old_history:
            # Create dummy sessions for existing history so foreign key doesn't fail
            await self._conn.executescript("""
                INSERT OR IGNORE INTO agent_sessions (session_id, title)
                SELECT DISTINCT session_id, 'Legacy Session' FROM agent_history;
                
                INSERT INTO agent_history_new (id, session_id, role, content, timestamp)
                SELECT id, session_id, role, content, timestamp FROM agent_history;
                
                DROP TABLE agent_history;
            """)
        
        # Ensure the table is named correctly for queries
        await self._conn.executescript("""
            CREATE VIEW IF NOT EXISTS agent_history AS SELECT * FROM agent_history_new;
        """)
        
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "AgentRepository":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ── agent_tasks CRUD ─────────────────────────────────────────

    async def add_task(
        self,
        task_type: str,
        prompt: str,
        execution_time: Optional[str] = None,
        *,
        is_active: bool = True,
    ) -> int:
        cursor = await self._conn.execute(
            """
            INSERT INTO agent_tasks (task_type, execution_time, prompt, is_active)
            VALUES (?, ?, ?, ?)
            """,
            (task_type, execution_time, prompt, is_active),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_active_tasks(self) -> list[dict]:
        cursor = await self._conn.execute(
            """
            SELECT id, task_type, execution_time, prompt, is_active
            FROM agent_tasks
            WHERE is_active = 1
            ORDER BY id
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "task_type": row["task_type"],
                "execution_time": row["execution_time"],
                "prompt": row["prompt"],
                "is_active": bool(row["is_active"]),
            }
            for row in rows
        ]

    async def get_task_by_id(self, task_id: int) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT id, task_type, execution_time, prompt, is_active FROM agent_tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "task_type": row["task_type"],
            "execution_time": row["execution_time"],
            "prompt": row["prompt"],
            "is_active": bool(row["is_active"]),
        }

    async def set_task_active(self, task_id: int, is_active: bool) -> bool:
        cursor = await self._conn.execute(
            "UPDATE agent_tasks SET is_active = ? WHERE id = ?",
            (is_active, task_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_task(self, task_id: int) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM agent_tasks WHERE id = ?",
            (task_id,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ── agent_sessions CRUD ──────────────────────────────────────

    async def create_session(self, session_id: str, title: str) -> None:
        await self._conn.execute(
            "INSERT INTO agent_sessions (session_id, title) VALUES (?, ?)",
            (session_id, title)
        )
        await self._conn.commit()

    async def get_all_sessions(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT session_id, title, created_at FROM agent_sessions ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "session_id": row["session_id"],
                "title": row["title"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def delete_session(self, session_id: str) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM agent_sessions WHERE session_id = ?",
            (session_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0
    
    async def update_session_title(self, session_id: str, title: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE agent_sessions SET title = ? WHERE session_id = ?",
            (title, session_id)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ── agent_history CRUD ───────────────────────────────────────

    async def save_history_message(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or datetime.utcnow().isoformat()
        cursor = await self._conn.execute(
            """
            INSERT INTO agent_history_new (session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, ts),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_session_history(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict]:
        cursor = await self._conn.execute(
            """
            SELECT id, session_id, role, content, timestamp FROM (
                SELECT id, session_id, role, content, timestamp
                FROM agent_history
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
            ORDER BY timestamp ASC
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

    async def clear_session(self, session_id: str) -> int:
        cursor = await self._conn.execute(
            "DELETE FROM agent_history_new WHERE session_id = ?",
            (session_id,),
        )
        await self._conn.commit()
        return cursor.rowcount

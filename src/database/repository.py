import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import SCHEMA


class Repository:

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Repository":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ── Business connections ─────────────────────────────────────

    async def upsert_business_connection(
        self,
        connection_id: str,
        user_id: int,
        user_name: str,
        is_enabled: bool,
        can_reply: bool,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO business_connections (connection_id, user_id, user_name, is_enabled, can_reply, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(connection_id) DO UPDATE SET
                user_name = excluded.user_name,
                is_enabled = excluded.is_enabled,
                can_reply = excluded.can_reply,
                updated_at = CURRENT_TIMESTAMP
            """,
            (connection_id, user_id, user_name, is_enabled, can_reply),
        )
        await self._conn.commit()

    async def get_business_connection(self, connection_id: str) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT connection_id, user_id, user_name, is_enabled, can_reply FROM business_connections WHERE connection_id = ?",
            (connection_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "connection_id": row["connection_id"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "is_enabled": bool(row["is_enabled"]),
                "can_reply": bool(row["can_reply"]),
            }
        return None

    async def get_business_connections_by_user(self, user_id: int) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT connection_id, user_id, user_name, is_enabled, can_reply FROM business_connections WHERE user_id = ? AND is_enabled = TRUE",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "connection_id": row["connection_id"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "is_enabled": bool(row["is_enabled"]),
                "can_reply": bool(row["can_reply"]),
            }
            for row in rows
        ]

    # ── Business logs ────────────────────────────────────────────

    async def log_business_action(
        self,
        connection_id: str,
        action: str,
        sender_name: str = "",
        message_text: str = "",
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO business_logs (connection_id, action, sender_name, message_text)
            VALUES (?, ?, ?, ?)
            """,
            (connection_id, action, sender_name, message_text),
        )
        await self._conn.commit()

    # ── Chat messages (per-user isolated context) ─────────────────

    async def save_message(
        self,
        chat_id: int,
        connection_id: str,
        role: str,
        content: str,
        sender_name: str = "",
        source: str = "live",
    ) -> None:
        """Persist a single message to the per-user conversation history."""
        await self._conn.execute(
            """
            INSERT INTO chat_messages (chat_id, connection_id, sender_name, role, content, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, connection_id, sender_name, role, content, source),
        )
        await self._conn.commit()

    async def get_context(
        self,
        chat_id: int,
        limit: int = 30,
        months: int = 3,
    ) -> list[dict]:
        """Return the N most recent messages for a given chat within the last X months.

        Messages are returned in chronological order (oldest first) so they form
        a natural conversation thread when passed to an LLM.
        """
        since = datetime.utcnow() - timedelta(days=months * 30)
        cursor = await self._conn.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM chat_messages
                WHERE chat_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (chat_id, since.isoformat(), limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def get_all_contacts(self, connection_id: str = "") -> list[dict]:
        """Return a list of unique contacts for the Mini App dashboard.

        If *connection_id* is provided, results are filtered to that connection.
        If omitted, all contacts across all connections are returned.
        """
        if connection_id:
            cursor = await self._conn.execute(
                """
                SELECT
                    chat_id,
                    MAX(sender_name) AS sender_name,
                    COUNT(*) AS total_messages,
                    MAX(created_at) AS last_message_at,
                    SUM(CASE WHEN source = 'import' THEN 1 ELSE 0 END) AS imported_count
                FROM chat_messages
                WHERE connection_id = ?
                GROUP BY chat_id
                ORDER BY last_message_at DESC
                """,
                (connection_id,),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT
                    chat_id,
                    MAX(sender_name) AS sender_name,
                    COUNT(*) AS total_messages,
                    MAX(created_at) AS last_message_at,
                    SUM(CASE WHEN source = 'import' THEN 1 ELSE 0 END) AS imported_count
                FROM chat_messages
                GROUP BY chat_id
                ORDER BY last_message_at DESC
                """
            )
        rows = await cursor.fetchall()
        return [
            {
                "chat_id": row["chat_id"],
                "sender_name": row["sender_name"],
                "total_messages": row["total_messages"],
                "last_message_at": row["last_message_at"],
                "has_import": row["imported_count"] > 0,
            }
            for row in rows
        ]

    async def get_chat_history(
        self,
        chat_id: int,
        limit: int = 100,
    ) -> list[dict]:
        """Return the recent message history for a specific chat."""
        cursor = await self._conn.execute(
            """
            SELECT role, content, sender_name, created_at, source
            FROM chat_messages
            WHERE chat_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "sender_name": row["sender_name"],
                "created_at": row["created_at"],
                "source": row["source"],
            }
            for row in rows
        ]

    async def has_imported_history(self, chat_id: int) -> bool:
        """Return True if this chat_id has any messages from a JSON import."""
        cursor = await self._conn.execute(
            "SELECT 1 FROM chat_messages WHERE chat_id = ? AND source = 'import' LIMIT 1",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def import_messages(
        self,
        chat_id: int,
        connection_id: str,
        messages: list[dict],
    ) -> int:
        """Bulk-insert pre-parsed messages from a Telegram JSON export.

        Each message dict must have: role, content, sender_name, created_at (ISO str).
        Returns the number of rows inserted.
        """
        rows = [
            (
                chat_id,
                connection_id,
                msg.get("sender_name", ""),
                msg["role"],
                msg["content"],
                "import",
                msg.get("created_at", datetime.utcnow().isoformat()),
            )
            for msg in messages
        ]
        await self._conn.executemany(
            """
            INSERT INTO chat_messages (chat_id, connection_id, sender_name, role, content, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self._conn.commit()
        return len(rows)

import json
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .models import SCHEMA


@dataclass
class Contact:
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    is_user: bool = True
    
    @property
    def display_name(self) -> str:
        if self.first_name:
            name = self.first_name
            if self.last_name:
                name += f" {self.last_name}"
            return name
        if self.username:
            return f"@{self.username}"
        return f"User {self.telegram_id}"


@dataclass
class Message:
    telegram_msg_id: int
    contact_id: int
    text: str
    is_outgoing: bool
    timestamp: datetime


@dataclass
class StyleProfile:
    contact_id: int
    style_json: dict
    analyzed_at: datetime
    message_count: int


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_at: datetime


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
    
    async def upsert_contact(self, contact: Contact) -> None:
        await self._conn.execute(
            """
            INSERT INTO contacts (telegram_id, username, first_name, last_name, is_user, last_synced)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                is_user = excluded.is_user,
                last_synced = CURRENT_TIMESTAMP
            """,
            (contact.telegram_id, contact.username, contact.first_name, contact.last_name, contact.is_user)
        )
        await self._conn.commit()
    
    async def get_contact(self, telegram_id: int) -> Optional[Contact]:
        cursor = await self._conn.execute(
            "SELECT telegram_id, username, first_name, last_name, is_user FROM contacts WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()
        if row:
            return Contact(
                telegram_id=row["telegram_id"],
                username=row["username"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                is_user=bool(row["is_user"])
            )
        return None
    
    async def save_messages(self, messages: list[Message]) -> int:
        if not messages:
            return 0
        
        cursor = await self._conn.executemany(
            """
            INSERT OR IGNORE INTO messages (telegram_msg_id, contact_id, text, is_outgoing, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(m.telegram_msg_id, m.contact_id, m.text, m.is_outgoing, m.timestamp.isoformat()) for m in messages]
        )
        await self._conn.commit()
        return cursor.rowcount
    
    async def get_messages(
        self,
        contact_id: int,
        since: Optional[datetime] = None,
        outgoing_only: bool = False
    ) -> list[Message]:
        query = "SELECT telegram_msg_id, contact_id, text, is_outgoing, timestamp FROM messages WHERE contact_id = ?"
        params: list = [contact_id]
        
        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())
        
        if outgoing_only:
            query += " AND is_outgoing = TRUE"
        
        query += " ORDER BY timestamp ASC"
        
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        
        return [
            Message(
                telegram_msg_id=row["telegram_msg_id"],
                contact_id=row["contact_id"],
                text=row["text"] or "",
                is_outgoing=bool(row["is_outgoing"]),
                timestamp=datetime.fromisoformat(row["timestamp"])
            )
            for row in rows
        ]
    
    async def get_message_count(self, contact_id: int) -> int:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE contact_id = ?",
            (contact_id,)
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0
    
    async def save_style_profile(self, profile: StyleProfile) -> None:
        await self._conn.execute(
            """
            INSERT INTO style_profiles (contact_id, style_json, analyzed_at, message_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET
                style_json = excluded.style_json,
                analyzed_at = excluded.analyzed_at,
                message_count = excluded.message_count
            """,
            (profile.contact_id, json.dumps(profile.style_json), profile.analyzed_at.isoformat(), profile.message_count)
        )
        await self._conn.commit()
    
    async def get_style_profile(self, contact_id: int) -> Optional[StyleProfile]:
        cursor = await self._conn.execute(
            "SELECT contact_id, style_json, analyzed_at, message_count FROM style_profiles WHERE contact_id = ?",
            (contact_id,)
        )
        row = await cursor.fetchone()
        if row:
            return StyleProfile(
                contact_id=row["contact_id"],
                style_json=json.loads(row["style_json"]),
                analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
                message_count=row["message_count"]
            )
        return None
    
    async def save_token(self, token: OAuthToken) -> None:
        await self._conn.execute(
            """
            INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            (token.access_token, token.refresh_token, token.expires_at.isoformat())
        )
        await self._conn.commit()
    
    async def get_token(self) -> Optional[OAuthToken]:
        cursor = await self._conn.execute(
            "SELECT access_token, refresh_token, expires_at FROM oauth_tokens WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row:
            return OAuthToken(
                access_token=row["access_token"],
                refresh_token=row["refresh_token"],
                expires_at=datetime.fromisoformat(row["expires_at"])
            )
        return None
    
    async def delete_token(self) -> None:
        await self._conn.execute("DELETE FROM oauth_tokens WHERE id = 1")
        await self._conn.commit()

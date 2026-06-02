"""
In-memory store for pending response approvals.

Each pending entry tracks the AI-generated reply waiting for owner confirmation,
along with the context needed to send or regenerate it.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PendingResponse:
    pending_id: str
    business_connection_id: str
    sender_chat_id: int
    sender_name: str
    sender_username: Optional[str]
    incoming_text: str
    proposed_reply: str
    owner_chat_id: int
    owner_message_id: int
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | rejected | expired | rewriting


class PendingStore:
    def __init__(self, timeout_minutes: int = 10):
        self._store: dict[str, PendingResponse] = {}
        self._lock = asyncio.Lock()
        self._timeout_seconds = timeout_minutes * 60

    async def add(self, entry: PendingResponse) -> None:
        async with self._lock:
            self._store[entry.pending_id] = entry
            logger.info(
                "Pending response added: id=%s sender=%s",
                entry.pending_id,
                entry.sender_name,
            )

    async def get(self, pending_id: str) -> Optional[PendingResponse]:
        async with self._lock:
            return self._store.get(pending_id)

    async def update_status(self, pending_id: str, status: str) -> bool:
        async with self._lock:
            entry = self._store.get(pending_id)
            if not entry:
                return False
            entry.status = status
            return True

    async def update_reply(self, pending_id: str, new_reply: str) -> bool:
        async with self._lock:
            entry = self._store.get(pending_id)
            if not entry:
                return False
            entry.proposed_reply = new_reply
            entry.status = "pending"
            return True

    async def remove(self, pending_id: str) -> Optional[PendingResponse]:
        async with self._lock:
            return self._store.pop(pending_id, None)

    async def expire_old(self) -> list[PendingResponse]:
        now = time.time()
        expired: list[PendingResponse] = []
        async with self._lock:
            for pid, entry in list(self._store.items()):
                if entry.status == "pending" and (now - entry.created_at) > self._timeout_seconds:
                    entry.status = "expired"
                    expired.append(entry)
                    del self._store[pid]
        return expired

    async def get_by_owner_message(self, chat_id: int, message_id: int) -> Optional[PendingResponse]:
        async with self._lock:
            for entry in self._store.values():
                if entry.owner_chat_id == chat_id and entry.owner_message_id == message_id:
                    return entry
        return None

    @property
    def count(self) -> int:
        return len(self._store)

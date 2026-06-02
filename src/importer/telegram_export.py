import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Union

from ..database.repository import Repository

logger = logging.getLogger(__name__)


class TelegramExportImporter:
    def __init__(self, repo: Repository, owner_name: str):
        self.repo = repo
        self._owner_name = owner_name.strip().lower()

    async def import_file(
        self,
        path: Union[Path, str],
        chat_id: int,
        connection_id: str,
        months: int = 3,
    ) -> int:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Export file not found: {path}")

        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)

        raw_messages: list[dict] = data.get("messages", [])
        cutoff = datetime.utcnow() - timedelta(days=months * 30)

        parsed: list[dict] = []

        for msg in raw_messages:
            if msg.get("type") != "message":
                continue

            text = self._extract_text(msg)
            if not text:
                continue

            created_at = self._parse_date(msg.get("date", ""))
            if created_at is None or created_at < cutoff:
                continue

            sender_raw: str = msg.get("from", "") or ""
            role = "assistant" if sender_raw.strip().lower() == self._owner_name else "user"

            parsed.append(
                {
                    "role": role,
                    "content": text,
                    "sender_name": sender_raw,
                    "created_at": created_at.isoformat(),
                }
            )

        if not parsed:
            logger.info("No eligible messages found in export file: %s", path)
            return 0

        count = await self.repo.import_messages(
            chat_id=chat_id,
            connection_id=connection_id,
            messages=parsed,
        )

        logger.info(
            "Imported %d messages for chat_id=%s from %s",
            count, chat_id, path.name,
        )
        return count

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(msg: dict) -> str:
        text = msg.get("text", "")
        if isinstance(text, str):
            return text.strip()
        if isinstance(text, list):
            parts = []
            for part in text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(part.get("text", ""))
            return "".join(parts).strip()
        return ""

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str)
        except ValueError:
            logger.warning("Could not parse date: %s", date_str)
            return None

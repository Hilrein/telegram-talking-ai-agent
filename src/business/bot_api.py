import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BOT_API_BASE = "https://api.telegram.org"


class BotApiClient:

    def __init__(self, token: str, timeout: float = 60.0):
        self.token = token
        self._base_url = f"{BOT_API_BASE}/bot{token}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))
        self._offset: int = 0

    async def close(self) -> None:
        await self._http.aclose()

    # ── core request ────────────────────────────────────────────

    async def _request(self, method: str, **kwargs) -> dict:
        url = f"{self._base_url}/{method}"
        payload = {k: v for k, v in kwargs.items() if v is not None}

        response = await self._http.post(url, json=payload)
        data = response.json()

        if not data.get("ok"):
            error_code = data.get("error_code", "?")
            description = data.get("description", "Unknown error")
            logger.error("Bot API error %s: %s (method=%s)", error_code, description, method)
            raise RuntimeError(f"Bot API [{error_code}]: {description}")

        return data.get("result", {})

    # ── getUpdates ──────────────────────────────────────────────

    async def get_updates(
        self,
        allowed_updates: Optional[list[str]] = None,
        poll_timeout: int = 30,
    ) -> list[dict]:
        try:
            result = await self._request(
                "getUpdates",
                offset=self._offset,
                timeout=poll_timeout,
                allowed_updates=allowed_updates,
            )
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            return []
        except RuntimeError:
            return []

        if result:
            self._offset = max(u["update_id"] for u in result) + 1

        return result

    # ── getMe ───────────────────────────────────────────────────

    async def get_me(self) -> dict:
        return await self._request("getMe")

    # ── sendMessage ─────────────────────────────────────────────

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[dict] = None,
        business_connection_id: Optional[str] = None,
    ) -> dict:
        return await self._request(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            business_connection_id=business_connection_id,
        )

    # ── answerCallbackQuery ─────────────────────────────────────

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        return await self._request(
            "answerCallbackQuery",
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )

    # ── editMessageText ─────────────────────────────────────────

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[dict] = None,
    ) -> dict:
        return await self._request(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    # ── editMessageReplyMarkup ──────────────────────────────────

    async def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        reply_markup: Optional[dict] = None,
    ) -> dict:
        return await self._request(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )

    # ── deleteMessage ───────────────────────────────────────────

    async def delete_message(
        self,
        chat_id: int | str,
        message_id: int,
    ) -> bool:
        return await self._request(
            "deleteMessage",
            chat_id=chat_id,
            message_id=message_id,
        )

    # ── sendChatAction ──────────────────────────────────────────

    async def send_chat_action(
        self,
        chat_id: int | str,
        action: str,
        business_connection_id: Optional[str] = None,
    ) -> bool:
        return await self._request(
            "sendChatAction",
            chat_id=chat_id,
            action=action,
            business_connection_id=business_connection_id,
        )

    # ── Files & Voice ───────────────────────────────────────────

    async def get_file(self, file_id: str) -> dict:
        return await self._request("getFile", file_id=file_id)

    async def download_file(self, file_path: str) -> bytes:
        url = f"{BOT_API_BASE}/file/bot{self.token}/{file_path}"
        response = await self._http.get(url)
        response.raise_for_status()
        return response.content

    async def send_voice(
        self,
        chat_id: int | str,
        voice_bytes: bytes,
        business_connection_id: Optional[str] = None,
    ) -> dict:
        url = f"{self._base_url}/sendVoice"
        data = {"chat_id": chat_id}
        if business_connection_id:
            data["business_connection_id"] = business_connection_id
            
        files = {"voice": ("voice.mp3", voice_bytes, "audio/mpeg")}
        
        response = await self._http.post(url, data=data, files=files)
        response_data = response.json()
        
        if not response_data.get("ok"):
            raise RuntimeError(f"Bot API sendVoice error: {response_data}")
            
        return response_data.get("result", {})

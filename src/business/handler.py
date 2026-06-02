import asyncio
import json
import logging
import uuid
from html import escape
from typing import Optional

from .bot_api import BotApiClient
from .pending_store import PendingStore, PendingResponse
from ..database.repository import Repository

logger = logging.getLogger(__name__)

# ── Callback data prefixes ──────────────────────────────────────
CB_APPROVE = "biz_approve:"
CB_REJECT = "biz_reject:"
CB_REWRITE = "biz_rewrite:"
CB_APPROVE_VOICE = "biz_appr_voice:"


def _user_display_name(user: dict) -> str:
    first = user.get("first_name", "")
    last = user.get("last_name", "")
    name = f"{first} {last}".strip()
    return name or f"User {user.get('id', '?')}"


def _user_tag(user: dict) -> str:
    username = user.get("username")
    return f"@{username}" if username else ""


def _build_approval_keyboard(pending_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Принять", "callback_data": f"{CB_APPROVE}{pending_id}"},
                {"text": "❌ Отклонить", "callback_data": f"{CB_REJECT}{pending_id}"},
                {"text": "✏️ Переписать", "callback_data": f"{CB_REWRITE}{pending_id}"},
            ],
            [
                {"text": "🎤 Ответить голосом", "callback_data": f"{CB_APPROVE_VOICE}{pending_id}"},
            ]
        ]
    }


class BusinessHandler:
    def __init__(
        self,
        bot: BotApiClient,
        repo: Repository,
        ai_client,
        owner_chat_id: int,
        pending_store: PendingStore,
        audio_service,
        style_prompt: str = "",
        context_limit: int = 30,
        context_months: int = 3,
    ):
        self.bot = bot
        self.repo = repo
        self.ai = ai_client
        self.owner_chat_id = owner_chat_id
        self.pending = pending_store
        self.audio_service = audio_service
        self.style_prompt = style_prompt
        self.context_limit = context_limit
        self.context_months = context_months
        self._rewrite_awaiting: dict[int, str] = {}

    def set_style_prompt(self, new_prompt: str) -> None:
        self.style_prompt = new_prompt or ""
        logger.info("Style prompt updated (%d chars)", len(self.style_prompt))

    def set_active_connection_id(self, conn_id: str) -> None:
        try:
            from ..api_server import _active_connection_id as _glb  # type: ignore
            import api_server as _api_mod
            _api_mod._active_connection_id = conn_id
        except Exception as e:
            logger.warning("Failed to sync active_connection_id: %s", e)

    # ── Public dispatch ─────────────────────────────────────────

    async def handle_update(self, update: dict) -> None:
        if "business_connection" in update:
            await self._on_business_connection(update["business_connection"])

        elif "business_message" in update:
            await self._on_business_message(update["business_message"])

        elif "edited_business_message" in update:
            logger.info("Edited business message (ignored): %s", update["edited_business_message"].get("message_id"))

        elif "deleted_business_messages" in update:
            logger.info("Deleted business messages (ignored)")

        elif "callback_query" in update:
            await self._on_callback_query(update["callback_query"])

        elif "message" in update:
            await self._on_direct_message(update["message"])

    # ── business_connection ─────────────────────────────────────

    async def _on_business_connection(self, conn: dict) -> None:
        conn_id = conn["id"]
        user = conn["user"]
        is_enabled = conn.get("is_enabled", False)
        can_reply = conn.get("can_reply", False)

        user_name = _user_display_name(user)
        user_id = user["id"]

        await self.repo.upsert_business_connection(
            connection_id=conn_id,
            user_id=user_id,
            user_name=user_name,
            is_enabled=is_enabled,
            can_reply=can_reply,
        )

        status_emoji = "🟢" if is_enabled else "🔴"
        status_text = "подключён" if is_enabled else "отключён"
        reply_text = "да" if can_reply else "нет"

        notification = (
            f"{status_emoji} <b>Business Connection</b>\n\n"
            f"Пользователь: <b>{escape(user_name)}</b>\n"
            f"Статус: {status_text}\n"
            f"Может отвечать: {reply_text}\n"
            f"Connection ID: <code>{conn_id}</code>"
        )

        await self.bot.send_message(self.owner_chat_id, notification)
        self.set_active_connection_id(conn_id)
        logger.info(
            "Business connection: user=%s enabled=%s can_reply=%s",
            user_name, is_enabled, can_reply,
        )

    # ── business_message ────────────────────────────────────────

    async def _on_business_message(self, message: dict) -> None:
        from_user = message.get("from", {})
        if from_user.get("is_bot", False):
            return

        business_connection_id = message.get("business_connection_id")
        if not business_connection_id:
            return

        conn = await self.repo.get_business_connection(business_connection_id)
        if not conn or not conn.get("is_enabled"):
            logger.info("Ignoring message for disabled/unknown connection: %s", business_connection_id)
            return

        sender_name = _user_display_name(from_user)
        sender_username = _user_tag(from_user)
        sender_chat_id = message["chat"]["id"]
        owner_name = conn.get("user_name", "Владелец")
        text = message.get("text", "")
        voice = message.get("voice")

        if voice:
            try:
                temp_msg = await self.bot.send_message(self.owner_chat_id, f"⏳ <i>Расшифровываю голосовое от {escape(sender_name)}...</i>")
                temp_id = temp_msg.get("message_id")
                
                file_info = await self.bot.get_file(voice["file_id"])
                audio_bytes = await self.bot.download_file(file_info["file_path"])
                transcription = await self.audio_service.transcribe_voice(audio_bytes)
                text = f"[🎤 Голосовое сообщение] {transcription}"
                
                if temp_id:
                    await self.bot.delete_message(self.owner_chat_id, temp_id)
            except Exception as e:
                logger.error("Failed to process voice message: %s", e)
                return

        if not text.strip():
            logger.info("Non-text/voice business message from %s (skipped)", sender_name)
            return

        # ── Save incoming message to per-user history ───────────
        await self.repo.save_message(
            chat_id=sender_chat_id,
            connection_id=business_connection_id,
            role="user",
            content=text,
            sender_name=sender_name,
            source="live",
        )

        # ── Log incoming message ────────────────────────────────
        await self.repo.log_business_action(
            connection_id=business_connection_id,
            action="incoming_message",
            sender_name=sender_name,
            message_text=text,
        )

        # ── Check if bot can reply ──────────────────────────────
        if not conn.get("can_reply"):
            await self.bot.send_message(
                self.owner_chat_id,
                (
                    f"💬 <b>{escape(sender_name)}</b>\n"
                    f"<i>\"{escape(text)}\"</i>\n\n"
                    f"⚠️ <i>Бот не имеет права отвечать (can_reply=false).</i>"
                ),
            )
            return

        # ── Send a compact "thinking" placeholder ───────────────
        base_thinking_msg = (
            f"💬 <b>{escape(sender_name)}</b>\n"
            f"<i>\"{escape(text)}\"</i>\n\n"
            f"⏳ Генерирую ответ"
        )
        placeholder = await self.bot.send_message(self.owner_chat_id, base_thinking_msg + "…")
        placeholder_id = placeholder.get("message_id", 0)

        # ── Show typing indicator in user's chat ────────────────
        try:
            await self.bot.send_chat_action(
                chat_id=sender_chat_id,
                action="typing",
                business_connection_id=business_connection_id,
            )
        except Exception as e:
            logger.warning("Failed to send typing action: %s", e)

        # ── Generate AI response with animation ─────────────────
        gen_task = asyncio.create_task(
            self._generate_reply(
                text,
                sender_name,
                sender_chat_id=sender_chat_id,
                business_connection_id=business_connection_id,
            )
        )

        dots = 1
        while not gen_task.done():
            try:
                await self.bot.edit_message_text(
                    chat_id=self.owner_chat_id,
                    message_id=placeholder_id,
                    text=f"{base_thinking_msg}{'.' * dots}"
                )
            except Exception:
                pass
            dots = (dots % 3) + 1
            
            try:
                await asyncio.wait_for(asyncio.shield(gen_task), timeout=0.8)
            except asyncio.TimeoutError:
                pass

        ai_reply = gen_task.result()

        # ── Log proposed reply ──────────────────────────────────
        await self.repo.log_business_action(
            connection_id=business_connection_id,
            action="proposed_reply",
            sender_name=sender_name,
            message_text=ai_reply,
        )

        # ── Smooth text output effect ───────────────────────────
        base_approval_msg = (
            f"💬 <b>{escape(sender_name)}</b>\n"
            f"<i>\"{escape(text)}\"</i>\n\n"
            f"💡 <b>Ответ:</b>\n"
        )
        
        chunk_len = max(1, len(ai_reply) // 3)
        chunks = [ai_reply[:chunk_len], ai_reply[:chunk_len*2], ai_reply]
        
        for i, chunk in enumerate(chunks):
            current_text = f"{base_approval_msg}{escape(chunk)}"
            if i < len(chunks) - 1:
                current_text += " ▒"
            
            try:
                await self.bot.edit_message_text(
                    chat_id=self.owner_chat_id,
                    message_id=placeholder_id,
                    text=current_text,
                    reply_markup=None
                )
            except Exception:
                pass
            
            if i < len(chunks) - 1:
                await asyncio.sleep(0.7)

        # ── Finalize with action buttons ────────────────────────
        pending_id = uuid.uuid4().hex[:12]
        keyboard = _build_approval_keyboard(pending_id)
        final_msg = f"{base_approval_msg}{escape(ai_reply)}"

        try:
            await self.bot.edit_message_text(
                chat_id=self.owner_chat_id,
                message_id=placeholder_id,
                text=final_msg,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("Failed to edit placeholder, sending new message: %s", e)
            result = await self.bot.send_message(
                self.owner_chat_id,
                final_msg,
                reply_markup=keyboard,
            )
            placeholder_id = result.get("message_id", 0)

        # ── Store pending response ──────────────────────────────
        entry = PendingResponse(
            pending_id=pending_id,
            business_connection_id=business_connection_id,
            sender_chat_id=sender_chat_id,
            sender_name=sender_name,
            sender_username=from_user.get("username"),
            incoming_text=text,
            proposed_reply=ai_reply,
            owner_chat_id=self.owner_chat_id,
            owner_message_id=placeholder_id,
        )
        await self.pending.add(entry)

        logger.info(
            "Pending approval created: id=%s from=%s reply_preview=%s",
            pending_id, sender_name, ai_reply[:60],
        )

    # ── callback_query (button press) ───────────────────────────

    async def _on_callback_query(self, cq: dict) -> None:
        cq_id = cq["id"]
        data = cq.get("data", "")
        cq_from = cq.get("from", {})

        # ── APPROVE ─────────────────────────────────────────────
        if data.startswith(CB_APPROVE):
            pending_id = data[len(CB_APPROVE):]
            entry = await self.pending.get(pending_id)

            if not entry or entry.status != "pending":
                await self.bot.answer_callback_query(cq_id, text="⏰ Ответ истёк или уже обработан.", show_alert=True)
                return

            try:
                await self.bot.send_message(
                    chat_id=entry.sender_chat_id,
                    text=entry.proposed_reply,
                    parse_mode=None,
                    business_connection_id=entry.business_connection_id,
                )
            except Exception as e:
                logger.error("Failed to send business reply: %s", e)
                await self.bot.answer_callback_query(cq_id, text=f"❌ Ошибка отправки: {e}", show_alert=True)
                return

            # ── Save approved reply to per-user history ─────────
            await self.repo.save_message(
                chat_id=entry.sender_chat_id,
                connection_id=entry.business_connection_id,
                role="assistant",
                content=entry.proposed_reply,
                sender_name="assistant",
                source="live",
            )

            await self.pending.update_status(pending_id, "approved")
            await self.bot.answer_callback_query(cq_id, text="✅ Ответ отправлен!")

            await self.bot.edit_message_text(
                chat_id=entry.owner_chat_id,
                message_id=entry.owner_message_id,
                text=(
                    f"✅ <b>{escape(entry.sender_name)}</b>\n"
                    f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
                    f"Отправлено:\n"
                    f"{escape(entry.proposed_reply)}"
                ),
            )

            await self.repo.log_business_action(
                connection_id=entry.business_connection_id,
                action="approved",
                sender_name=entry.sender_name,
                message_text=entry.proposed_reply,
            )

            await self.pending.remove(pending_id)
            logger.info("Reply approved and sent: id=%s", pending_id)

        # ── APPROVE AS VOICE ────────────────────────────────────
        elif data.startswith(CB_APPROVE_VOICE):
            pending_id = data[len(CB_APPROVE_VOICE):]
            entry = await self.pending.get(pending_id)

            if not entry or entry.status != "pending":
                await self.bot.answer_callback_query(cq_id, text="⏰ Ответ истёк или уже обработан.", show_alert=True)
                return

            await self.bot.answer_callback_query(cq_id, text="Генерирую голосовое...")
            await self.pending.update_status(pending_id, "approved")

            try:
                await self.bot.send_chat_action(
                    entry.sender_chat_id, "record_voice", business_connection_id=entry.business_connection_id
                )
                
                voice_bytes = await self.audio_service.generate_voice(entry.proposed_reply)
                
                await self.bot.send_voice(
                    chat_id=entry.sender_chat_id,
                    voice_bytes=voice_bytes,
                    business_connection_id=entry.business_connection_id,
                )

                sent_msg = (
                    f"✅ <b>{escape(entry.sender_name)}</b>\n"
                    f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
                    f"Отправлено (Голосом):\n"
                    f"{escape(entry.proposed_reply)}"
                )
                await self.bot.edit_message_text(
                    chat_id=entry.owner_chat_id,
                    message_id=entry.owner_message_id,
                    text=sent_msg,
                )

                await self.repo.log_business_action(
                    connection_id=entry.business_connection_id,
                    action="approved_voice",
                    sender_name=entry.sender_name,
                    message_text=entry.proposed_reply,
                )

                await self.repo.save_message(
                    chat_id=entry.sender_chat_id,
                    connection_id=entry.business_connection_id,
                    role="assistant",
                    content=f"[🎤 Голосовой ответ] {entry.proposed_reply}",
                    sender_name="assistant",
                    source="live",
                )
                
                await self.pending.remove(pending_id)
                logger.info("Reply approved as voice and sent: id=%s", pending_id)

            except Exception as e:
                logger.error("Failed to send approved voice reply: %s", e)
                await self.bot.send_message(self.owner_chat_id, f"❌ Ошибка отправки голосового ответа: {e}")
                await self.pending.update_status(pending_id, "pending") # revert

        # ── REJECT ──────────────────────────────────────────────
        elif data.startswith(CB_REJECT):
            pending_id = data[len(CB_REJECT):]
            entry = await self.pending.get(pending_id)

            if not entry or entry.status != "pending":
                await self.bot.answer_callback_query(cq_id, text="⏰ Уже обработано.", show_alert=True)
                return

            await self.pending.update_status(pending_id, "rejected")
            await self.bot.answer_callback_query(cq_id, text="❌ Ответ отклонён.")
            await self.bot.edit_message_text(
                chat_id=entry.owner_chat_id,
                message_id=entry.owner_message_id,
                text=(
                    f"❌ <b>{escape(entry.sender_name)}</b>\n"
                    f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
                    f"<s>{escape(entry.proposed_reply)}</s>\n\n"
                    f"<i>Ответ отклонён</i>"
                ),
            )

            await self.repo.log_business_action(
                connection_id=entry.business_connection_id,
                action="rejected",
                sender_name=entry.sender_name,
                message_text=entry.proposed_reply,
            )

            await self.pending.remove(pending_id)
            logger.info("Reply rejected: id=%s", pending_id)

        # ── REWRITE ─────────────────────────────────────────────
        elif data.startswith(CB_REWRITE):
            pending_id = data[len(CB_REWRITE):]
            entry = await self.pending.get(pending_id)

            if not entry or entry.status != "pending":
                await self.bot.answer_callback_query(cq_id, text="⏰ Уже обработано.", show_alert=True)
                return

            await self.pending.update_status(pending_id, "rewriting")
            await self.bot.answer_callback_query(cq_id, text="✏️ Напишите уточнение.")

            owner_user_id = cq_from.get("id", 0)
            self._rewrite_awaiting[owner_user_id] = pending_id

            await self.bot.edit_message_text(
                chat_id=entry.owner_chat_id,
                message_id=entry.owner_message_id,
                text=(
                    f"✏️ <b>{escape(entry.sender_name)}</b>\n"
                    f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
                    f"Текущий вариант:\n"
                    f"{escape(entry.proposed_reply)}\n\n"
                    f"<b>Отправьте уточнение ↓</b>"
                ),
            )

            logger.info("Rewrite requested: id=%s", pending_id)

        else:
            await self.bot.answer_callback_query(cq_id)

    # ── direct message from owner (rewrite flow) ────────────────

    async def _on_direct_message(self, message: dict) -> None:
        from_user = message.get("from", {})
        user_id = from_user.get("id", 0)
        chat_id = message["chat"]["id"]

        if chat_id != self.owner_chat_id:
            return

        pending_id = self._rewrite_awaiting.pop(user_id, None)
        if not pending_id:
            return

        entry = await self.pending.get(pending_id)
        if not entry:
            await self.bot.send_message(chat_id, "⚠️ Запрос на переписывание истёк.")
            return

        rewrite_instruction = message.get("text", "")
        if not rewrite_instruction.strip():
            await self.bot.send_message(chat_id, "⚠️ Отправьте текстовое уточнение.")
            self._rewrite_awaiting[user_id] = pending_id
            return

        old_msg_id = entry.owner_message_id
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception:
            pass
        base_thinking = (
            f"💬 <b>{escape(entry.sender_name)}</b>\n"
            f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
            f"⏳ Переписываю ответ"
        )
        thinking = await self.bot.send_message(chat_id, base_thinking + "…")
        thinking_id = thinking.get("message_id", 0)

        # ── Generate AI response with animation ─────────────────
        gen_task = asyncio.create_task(
            self._generate_reply(
                entry.incoming_text,
                entry.sender_name,
                rewrite_hint=rewrite_instruction,
                previous_reply=entry.proposed_reply,
            )
        )

        dots = 1
        while not gen_task.done():
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=thinking_id,
                    text=f"{base_thinking}{'.' * dots}"
                )
            except Exception:
                pass
            dots = (dots % 3) + 1
            
            try:
                await asyncio.wait_for(asyncio.shield(gen_task), timeout=0.8)
            except asyncio.TimeoutError:
                pass

        new_reply = gen_task.result()

        await self.pending.update_reply(pending_id, new_reply)

        await self.repo.log_business_action(
            connection_id=entry.business_connection_id,
            action="rewrite",
            sender_name=entry.sender_name,
            message_text=new_reply,
        )

        # ── Smooth text output effect ───────────────────────────
        base_approval_msg = (
            f"💬 <b>{escape(entry.sender_name)}</b>\n"
            f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
            f"💡 <b>Ответ (v2):</b>\n"
        )
        
        chunk_len = max(1, len(new_reply) // 3)
        chunks = [new_reply[:chunk_len], new_reply[:chunk_len*2], new_reply]
        
        for i, chunk in enumerate(chunks):
            current_text = f"{base_approval_msg}{escape(chunk)}"
            if i < len(chunks) - 1:
                current_text += " ▒"
                
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=thinking_id,
                    text=current_text,
                    reply_markup=None
                )
            except Exception:
                pass
                
            if i < len(chunks) - 1:
                await asyncio.sleep(0.7)

        keyboard = _build_approval_keyboard(pending_id)
        new_card_msg = f"{base_approval_msg}{escape(new_reply)}"

        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=thinking_id,
                text=new_card_msg,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("Failed to edit rewrite placeholder: %s", e)
            result = await self.bot.send_message(chat_id, new_card_msg, reply_markup=keyboard)
            thinking_id = result.get("message_id", 0)

        entry.owner_message_id = thinking_id

        logger.info("Rewrite generated: id=%s new_reply_preview=%s", pending_id, new_reply[:60])

    # ── AI response generation ──────────────────────────────────

    async def _generate_reply(
        self,
        incoming_text: str,
        sender_name: str,
        rewrite_hint: Optional[str] = None,
        previous_reply: Optional[str] = None,
        sender_chat_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> str:
        system_parts = []

        if self.style_prompt:
            system_parts.append(self.style_prompt)

        system_parts.append(
            f"Ты отвечаешь на сообщение от {sender_name} от имени владельца бизнес-аккаунта. "
            "Генерируй естественный, дружелюбный ответ. "
            "Отвечай ТОЛЬКО текстом сообщения — без пояснений, без мета-комментариев."
        )

        if rewrite_hint:
            system_parts.append(
                f"\nВладелец попросил переписать предыдущий ответ. "
                f"Предыдущий ответ был: \"{previous_reply}\"\n"
                f"Инструкция владельца: \"{rewrite_hint}\"\n"
                f"Сгенерируй улучшенный ответ с учётом инструкции."
            )

        system_prompt = "\n\n".join(system_parts)

        # ── Load per-user conversation history ──────────────────
        history: list[dict] = []
        if sender_chat_id is not None:
            try:
                history = await self.repo.get_context(
                    chat_id=sender_chat_id,
                    limit=self.context_limit,
                    months=self.context_months,
                )
                if history and history[-1]["role"] == "user" and history[-1]["content"] == incoming_text:
                    history = history[:-1]
            except Exception as e:
                logger.warning("Failed to load context for chat_id=%s: %s", sender_chat_id, e)

        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": incoming_text},
        ]

        logger.info(
            "LLM request: chat_id=%s history_len=%d",
            sender_chat_id, len(history),
        )

        try:
            response = await self.ai.chat(messages, temperature=0.7)
            response = response.strip()
            if response.startswith('"') and response.endswith('"'):
                response = response[1:-1]
            if response.startswith("'") and response.endswith("'"):
                response = response[1:-1]
            return response
        except Exception as e:
            logger.error("AI generation failed: %s", e)
            return f"[Ошибка генерации ответа: {e}]"

    # ── Expiry handling ─────────────────────────────────────────

    async def handle_expired(self) -> None:
        expired = await self.pending.expire_old()
        for entry in expired:
            try:
                await self.bot.edit_message_text(
                    chat_id=entry.owner_chat_id,
                    message_id=entry.owner_message_id,
                    text=(
                        f"⏰ <b>{escape(entry.sender_name)}</b>\n"
                        f"<i>\"{escape(entry.incoming_text)}\"</i>\n\n"
                        f"<i>Не подтверждено вовремя</i>"
                    ),
                )
            except Exception as e:
                logger.warning("Failed to update expired message: %s", e)

            await self.repo.log_business_action(
                connection_id=entry.business_connection_id,
                action="expired",
                sender_name=entry.sender_name,
                message_text=entry.proposed_reply,
            )

            logger.info("Pending response expired: id=%s", entry.pending_id)

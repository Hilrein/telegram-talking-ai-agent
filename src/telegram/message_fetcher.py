

from datetime import datetime, timedelta
from typing import Optional

from telethon.tl.types import Message as TelethonMessage
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .client import TelegramClient
from ..database.repository import Message, Repository


class MessageFetcher:    
    def __init__(self, tg_client: TelegramClient, repository: Repository):
        self.tg_client = tg_client
        self.repo = repository
    
    async def fetch_history(
        self,
        contact_id: int,
        months: int = 6,
        force_refresh: bool = False
    ) -> int:
        since_date = datetime.now() - timedelta(days=months * 30)
        
        if not force_refresh:
            existing_count = await self.repo.get_message_count(contact_id)
            if existing_count > 0:
                messages = await self.repo.get_messages(contact_id)
                if messages:
                    latest = max(m.timestamp for m in messages)
                    since_date = latest
        
        entity = await self.tg_client.get_entity(contact_id)
        my_id = self.tg_client.me.id
        
        messages_to_save: list[Message] = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
        ) as progress:
            task = progress.add_task("[cyan]Fetching messages...", total=None)
            
            async for msg in self.tg_client.client.iter_messages(
                entity,
                offset_date=None,
                reverse=False,
            ):
                if msg.date.replace(tzinfo=None) < since_date:
                    break
                
                if not isinstance(msg, TelethonMessage):
                    continue
                
                text = msg.message or ""
                if msg.media and hasattr(msg, 'caption'):
                    text = msg.caption or text
                
                if not text.strip():
                    continue
                
                is_outgoing = msg.out or msg.sender_id == my_id
                
                messages_to_save.append(Message(
                    telegram_msg_id=msg.id,
                    contact_id=contact_id,
                    text=text,
                    is_outgoing=is_outgoing,
                    timestamp=msg.date.replace(tzinfo=None)
                ))
                
                progress.update(task, description=f"[cyan]Fetched {len(messages_to_save)} messages...")
            
            progress.update(task, completed=True)
        
        if messages_to_save:
            new_count = await self.repo.save_messages(messages_to_save)
            print(f"  → Saved {new_count} new messages")
        
        return await self.repo.get_message_count(contact_id)
    
    async def get_my_messages(
        self,
        contact_id: int,
        limit: Optional[int] = None
    ) -> list[Message]:
        messages = await self.repo.get_messages(contact_id, outgoing_only=True)
        if limit:
            messages = messages[-limit:]
        return messages
    
    async def get_recent_context(
        self,
        contact_id: int,
        limit: int = 20
    ) -> list[Message]:
        messages = await self.repo.get_messages(contact_id)
        return messages[-limit:] if len(messages) > limit else messages

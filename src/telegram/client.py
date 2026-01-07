

import asyncio
import re
import base64
import logging
import qrcode
from pathlib import Path
from typing import Optional, Callable, Any

from telethon import TelegramClient as TelethonClient
from telethon.tl.types import User, Chat, Channel
from telethon.tl.functions.auth import ExportLoginTokenRequest, ImportLoginTokenRequest
from telethon.tl.types.auth import LoginToken, LoginTokenMigrateTo, LoginTokenSuccess
from telethon.events import NewMessage
from telethon.errors import SessionPasswordNeededError
from rich.console import Console

from ..database.repository import Contact


console = Console()


def generate_qr_code(data: str) -> str:
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        matrix = qr.get_matrix()
        lines = []
        for row in matrix:
            line = ""
            for cell in row:
                line += "██" if cell else "  "
            lines.append(line)
        return "\n".join(lines)
    except Exception:
        return f"[QR Error] URL: {data}"


class TelegramClient:
    
    def __init__(self, api_id: int, api_hash: str, session_path: Path):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self._client: Optional[TelethonClient] = None
        self._me: Optional[User] = None
    
    @property
    def client(self) -> TelethonClient:
        if not self._client:
            raise RuntimeError("Client not connected.")
        return self._client
    
    @property
    def me(self) -> User:
        if not self._me:
            raise RuntimeError("Client not connected.")
        return self._me
    
    async def connect(self) -> None:
        self._client = TelethonClient(
            str(self.session_path),
            self.api_id,
            self.api_hash,
            system_version="Windows 10"
        )
        
        await self._client.connect()
        
        if not await self._client.is_user_authorized():
            console.print("\n[bold blue]📱 Telegram Authentication Required[/bold blue]")
            console.print("\n[bold]Choose authentication method:[/bold]")
            console.print("  [green]1[/green] - 📷 QR Code (recommended - scan with phone)")
            console.print("  [yellow]2[/yellow] - 📱 Phone number + code")
            console.print("")
            
            choice = console.input("[cyan]Enter choice (1 or 2): [/cyan]").strip()
            
            if choice == "1":
                await self._auth_with_qr_code()
            else:
                await self._auth_with_phone()
        
        self._me = await self._client.get_me()
        console.print(f"\n[green]✓ Logged in as {self._me.first_name} (@{self._me.username})[/green]")
    
    async def _auth_with_qr_code(self) -> None:
        console.print("\n[bold blue]📷 QR Code Authentication[/bold blue]")
        console.print("[dim]Open Telegram on your phone → Settings → Devices → Link Desktop Device[/dim]\n")
        
        while True:
            try:
                result = await self._client(ExportLoginTokenRequest(
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    except_ids=[]
                ))
                
                if isinstance(result, LoginTokenSuccess):
                    console.print("[green]✓ Authentication successful![/green]")
                    return
                
                if isinstance(result, LoginTokenMigrateTo):
                    await self._client._switch_dc(result.dc_id)
                    result = await self._client(ImportLoginTokenRequest(result.token))
                    if isinstance(result, LoginTokenSuccess):
                        console.print("[green]✓ Authentication successful![/green]")
                        return
                
                if isinstance(result, LoginToken):
                    token_base64 = base64.urlsafe_b64encode(result.token).decode('utf-8').rstrip('=')
                    qr_url = f"tg://login?token={token_base64}"
                    
                    console.print("\n" + generate_qr_code(qr_url))
                    console.print(f"\n[dim]Token expires in 30 seconds. Waiting for scan...[/dim]")
                    
                    try:
                        await asyncio.wait_for(
                            self._wait_for_qr_login(),
                            timeout=30.0
                        )
                        if await self._client.is_user_authorized():
                            return
                    except asyncio.TimeoutError:
                        console.print("[yellow]↻ Token expired, generating new QR code...[/yellow]")
                        continue
                        
            except SessionPasswordNeededError:
                console.print("\n[yellow]⚠️ Two-factor authentication (2FA) is enabled[/yellow]")
                password = console.input("[cyan]Enter your 2FA password: [/cyan]")
                await self._client.sign_in(password=password)
                return
            except Exception as e:
                console.print("[yellow]Falling back to phone authentication...[/yellow]")
                await self._auth_with_phone()
                return
    
    async def _wait_for_qr_login(self) -> None:
        while not await self._client.is_user_authorized():
            await asyncio.sleep(1)
    
    async def _auth_with_phone(self) -> None:
        console.print("\n[bold blue]📱 Phone Authentication[/bold blue]")
        
        phone = console.input("[cyan]Enter your phone number (with country code): [/cyan]")
        
        phone = re.sub(r'[^0-9+]', '', phone)
        if not phone.startswith("+"):
            phone = "+" + phone
        
        console.print(f"\n[dim]Sending code request to {phone}...[/dim]")
        
        try:
            sent_code = await self._client.send_code_request(phone)
            console.print(f"[green]✓ Code request sent[/green]")
        except Exception as e:
            console.print(f"[bold red]Error sending code: {e}[/bold red]")
            raise
        
        console.print("\n[bold yellow]⚠️ Check for verification code:[/bold yellow]")
        
        code = console.input("[bold cyan]Enter the verification code: [/bold cyan]")
        code = re.sub(r'[^0-9]', '', code)
        
        if not code:
            raise ValueError("Verification code is required")
        
        try:
            await self._client.sign_in(phone, code, phone_code_hash=sent_code.phone_code_hash)
        except SessionPasswordNeededError:
            console.print("\n[yellow]⚠️ Two-factor authentication (2FA) is enabled[/yellow]")
            password = console.input("[cyan]Enter your 2FA password: [/cyan]")
            await self._client.sign_in(password=password)
        except Exception as e:
            console.print(f"[bold red]Login Error: {e}[/bold red]")
            raise
    
    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None
            self._me = None
    
    async def __aenter__(self) -> "TelegramClient":
        await self.connect()
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.disconnect()
    
    async def get_recent_dialogs(self, limit: int = 30) -> list[Contact]:
        dialogs = await self._client.get_dialogs(limit=limit)
        contacts = []
        
        for dialog in dialogs:
            entity = dialog.entity
            
            if isinstance(entity, User):
                if entity.bot:
                    continue
                contacts.append(Contact(
                    telegram_id=entity.id,
                    username=entity.username,
                    first_name=entity.first_name,
                    last_name=entity.last_name,
                    is_user=True
                ))
            elif isinstance(entity, (Chat, Channel)):
                if getattr(entity, 'broadcast', False):
                    continue
                contacts.append(Contact(
                    telegram_id=entity.id,
                    username=getattr(entity, 'username', None),
                    first_name=entity.title,
                    last_name=None,
                    is_user=False
                ))
        
        return contacts
    
    async def get_entity(self, contact_id: int) -> Any:
        return await self._client.get_entity(contact_id)
    
    async def send_message(self, contact_id: int, text: str) -> None:
        await self._client.send_message(contact_id, text)
    
    def on_new_message(self, contact_id: int, callback: Callable) -> None:
        @self._client.on(NewMessage(chats=contact_id))
        async def handler(event):
            await callback(event)
    
    async def run_until_disconnected(self) -> None:
        await self._client.run_until_disconnected()

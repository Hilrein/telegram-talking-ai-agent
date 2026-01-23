import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Optional, List

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.generativeai as genai
from rich.console import Console
from rich.panel import Panel

from ..database.repository import Repository, OAuthToken

console = Console()

SCOPES = ['https://www.googleapis.com/auth/generative-language.retriever', 'https://www.googleapis.com/auth/cloud-platform']

class GoogleClient:
    
    def __init__(self, repository: Repository, model: str = "gemini-pro", client_secret_path: str = "client_secret.json"):
        self.repo = repository
        self.model_name = model
        self.client_secret_path = client_secret_path
        self._creds: Optional[Credentials] = None
        self._token: Optional[OAuthToken] = None
        
    async def __aenter__(self) -> "GoogleClient":
        await self._load_or_refresh_token()
        return self
    
    async def __aexit__(self, *args) -> None:
        pass
        
    async def _load_or_refresh_token(self) -> None:
        self._token = await self.repo.get_token(provider="google")
        
        if self._token:
            self._creds = Credentials(
                token=self._token.access_token,
                refresh_token=self._token.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._get_client_id(),
                client_secret=self._get_client_secret(), 
                scopes=SCOPES
            )
            
            if self._creds.expired:
                console.print("[yellow]Google token expired, refreshing...[/yellow]")
                try:
                    self._creds.refresh(Request())
                    await self._save_creds()
                except Exception as e:
                    console.print(f"[red]Failed to refresh Google token: {e}[/red]")
                    await self._auth_flow()
        else:
            await self._auth_flow()
            
    def _get_client_info(self) -> dict:
        if not os.path.exists(self.client_secret_path):
            raise FileNotFoundError(f"Client secret file not found at {self.client_secret_path}")
        
        with open(self.client_secret_path, 'r') as f:
            data = json.load(f)
            return data.get('installed', data.get('web', {}))

    def _get_client_id(self) -> str:
        return self._get_client_info().get('client_id', '')

    def _get_client_secret(self) -> str:
        return self._get_client_info().get('client_secret', '')

    async def _auth_flow(self) -> None:
        if not os.path.exists(self.client_secret_path):
            console.print(f"[bold red]Error: {self.client_secret_path} not found![/bold red]")
            console.print(Panel(
                "1. Go to [link=https://console.cloud.google.com/apis/credentials]Google Cloud Console Credentials[/link]\n"
                "2. Create an OAuth 2.0 Client ID (Application type: Desktop app)\n"
                "3. Download the JSON file\n"
                f"4. Rename it to [bold]{os.path.basename(self.client_secret_path)}[/bold] and place it in the project root.",
                title="Google OAuth Setup",
                border_style="red"
            ))
            raise FileNotFoundError(f"Client secret not found at {self.client_secret_path}")

        loop = asyncio.get_event_loop()
        try:
            flow = InstalledAppFlow.from_client_secrets_file(self.client_secret_path, SCOPES)
            self._creds = await loop.run_in_executor(None, lambda: flow.run_local_server(port=0))
            await self._save_creds()
            console.print("[green]✓ Google Authorization successful![/green]")
        except Exception as e:
            console.print(f"[bold red]Google Auth failed: {e}[/bold red]")
            raise

    async def _save_creds(self) -> None:
        if not self._creds:
            return

        expires_at = datetime.now() + timedelta(seconds=3600)
        if self._creds.expiry:
            expires_at = self._creds.expiry

        token = OAuthToken(
            access_token=self._creds.token,
            refresh_token=self._creds.refresh_token or "",
            expires_at=expires_at,
            provider="google"
        )
        await self.repo.save_token(token)

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        if not self._creds or not self._creds.valid:
             await self._load_or_refresh_token()
             
        genai.configure(credentials=self._creds)
        
        system_instruction = None
        gemini_messages = []
        
        for m in messages:
            if m['role'] == 'system':
                system_instruction = m['content']
                continue
            
            role = 'user' if m['role'] == 'user' else 'model'
            gemini_messages.append({'role': role, 'parts': [m['content']]})
            
        if gemini_messages and gemini_messages[0]['role'] == 'model':
            gemini_messages.insert(0, {'role': 'user', 'parts': ['[History Start]']})
            
        model = genai.GenerativeModel(self.model_name, system_instruction=system_instruction)
        
        try:
            response = await model.generate_content_async(
                gemini_messages,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens
                )
            )
            
            return response.text
        except Exception as e:
            console.print(f"[red]Gemini API Error: {e}[/red]")
            raise

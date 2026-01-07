import asyncio
import hashlib
import base64
import secrets
import uuid
import webbrowser
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from rich.console import Console

from ..database.repository import Repository, OAuthToken


console = Console()


OAUTH_BASE_URL = "https://chat.qwen.ai"
OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
OAUTH_DEVICE_CODE_URL = f"{OAUTH_BASE_URL}/api/v1/oauth2/device/code"
OAUTH_TOKEN_URL = f"{OAUTH_BASE_URL}/api/v1/oauth2/token"
OAUTH_SCOPE = "openid profile email model.completion"
OAUTH_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_API_URL = "https://chat.qwen.ai/api/v1/chat/completions"


@dataclass
class PKCEChallenge:
    verifier: str
    challenge: str
    
    @staticmethod
    def generate() -> "PKCEChallenge":
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
        return PKCEChallenge(verifier=verifier, challenge=challenge)


class QwenClient:
    
    def __init__(self, repository: Repository, model: str = "coder-model"):
        self.repo = repository
        self.model = model
        self._token: Optional[OAuthToken] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._resource_url: Optional[str] = None
    
    async def __aenter__(self) -> "QwenClient":
        self._http = httpx.AsyncClient(timeout=60.0)
        await self._load_or_refresh_token()
        return self
    
    async def __aexit__(self, *args) -> None:
        if self._http:
            await self._http.aclose()
    
    async def _load_or_refresh_token(self) -> None:
        self._token = await self.repo.get_token()
        
        if self._token:
            if self._token.expires_at - timedelta(minutes=5) <= datetime.now():
                console.print("[yellow]Token expiring soon, refreshing...[/yellow]")
                await self._refresh_token()
        else:
            await self._device_flow_auth()
    
    async def _device_flow_auth(self) -> None:
        pkce = PKCEChallenge.generate()
        
        body_data = {
            "client_id": OAUTH_CLIENT_ID,
            "scope": OAUTH_SCOPE,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
        }
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "x-request-id": str(uuid.uuid4()),
        }
        
        response = await self._http.post(
            OAUTH_DEVICE_CODE_URL,
            headers=headers,
            content=urlencode(body_data),
        )
        
        if not response.is_success:
            error_text = response.text
            console.print(f"[bold red]Device auth error: {response.status_code}[/bold red]")
            console.print(f"[red]{error_text}[/red]")
            raise RuntimeError(f"Device authorization failed: {response.status_code} - {error_text}")
        
        data = response.json()
        
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data.get("verification_uri_complete") or data.get("verification_uri", "https://chat.qwen.ai/device")
        expires_in = data.get("expires_in", 600)
        interval = data.get("interval", 5)
        
        console.print("\n[bold blue]🔐 Qwen OAuth2 Authentication Required[/bold blue]")
        console.print(f"\nOpen this URL and enter the code: [bold yellow]{user_code}[/bold yellow]")
        console.print(f"[link={verification_uri}]{verification_uri}[/link]\n")
        
        try:
            webbrowser.open(verification_uri)
        except Exception:
            pass
        
        console.print("[dim]Waiting for authorization...[/dim]")
        deadline = datetime.now() + timedelta(seconds=expires_in)
        
        while datetime.now() < deadline:
            await asyncio.sleep(interval)
            
            try:
                token_body = {
                    "grant_type": OAUTH_GRANT_TYPE,
                    "client_id": OAUTH_CLIENT_ID,
                    "device_code": device_code,
                    "code_verifier": pkce.verifier,
                }
                
                token_headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                }
                
                token_response = await self._http.post(
                    OAUTH_TOKEN_URL,
                    headers=token_headers,
                    content=urlencode(token_body),
                )
                
                if token_response.status_code == 200:
                    token_data = token_response.json()
                    
                    if token_data.get("access_token"):
                        await self._save_token(token_data)
                        console.print("[green]✓ Authorization successful![/green]\n")
                        return
                
                if token_response.status_code == 400:
                    error_data = token_response.json()
                    error = error_data.get("error", "")
                    
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        interval += 5
                        continue
                    elif error in ("expired_token", "access_denied"):
                        raise RuntimeError(f"Authorization failed: {error}")
                
                if token_response.status_code == 429:
                    interval += 5
                    continue
                    
            except httpx.HTTPError as e:
                console.print(f"[dim]Network error, retrying: {e}[/dim]")
                continue
        
        raise RuntimeError("Authorization timed out")
    
    async def _refresh_token(self) -> None:
        if not self._token or not self._token.refresh_token:
            console.print("[yellow]No refresh token, re-authenticating...[/yellow]")
            await self.repo.delete_token()
            await self._device_flow_auth()
            return
        
        try:
            body_data = {
                "grant_type": "refresh_token",
                "refresh_token": self._token.refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            }
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            
            response = await self._http.post(
                OAUTH_TOKEN_URL,
                headers=headers,
                content=urlencode(body_data),
            )
            
            if response.status_code == 200:
                token_data = response.json()
                await self._save_token(token_data)
                console.print("[green]✓ Token refreshed[/green]")
            elif response.status_code == 400:
                console.print("[yellow]Refresh token expired, re-authenticating...[/yellow]")
                await self.repo.delete_token()
                await self._device_flow_auth()
            else:
                console.print(f"[yellow]Token refresh failed ({response.status_code}), re-authenticating...[/yellow]")
                await self.repo.delete_token()
                await self._device_flow_auth()
                
        except httpx.HTTPError:
            await self.repo.delete_token()
            await self._device_flow_auth()
    
    async def _save_token(self, token_data: dict) -> None:
        console.print(f"[dim]Debug: Token Data: {token_data.keys()}[/dim]")
        if "resource_url" in token_data:
            self._resource_url = token_data["resource_url"]
            console.print(f"[dim]Debug: Using resource_url: {self._resource_url}[/dim]")
            
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        self._token = OAuthToken(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token", ""),
            expires_at=expires_at
        )
        
        await self.repo.save_token(self._token)
    
    async def _ensure_valid_token(self) -> str:
        if not self._token:
            await self._load_or_refresh_token()
        
        if self._token.expires_at - timedelta(minutes=1) <= datetime.now():
            await self._refresh_token()
        
        return self._token.access_token

    def _get_api_url(self) -> str:
        base_url = self._resource_url or DEFAULT_API_URL
        
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"

        if base_url.endswith("/"):
            base_url = base_url[:-1]
            
        if "/chat/completions" not in base_url:
            if not base_url.endswith("/v1"):
                base_url += "/v1"
            base_url += "/chat/completions"
            
        return base_url
    
    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        access_token = await self._ensure_valid_token()
        api_url = self._get_api_url()
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-DashScope-AuthType": "qwen-oauth",
            "X-DashScope-WorkSpace": "default",
        }

        response = await self._http.post(
            api_url,
            headers=headers,
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        
        if response.status_code == 401:
            console.print("[yellow]401 Unauthorized, refreshing token...[/yellow]")
            await self._refresh_token()
            return await self.chat(messages, temperature, max_tokens)
        
        if not response.is_success:
            console.print(f"[bold red]API Error {response.status_code}:[/bold red] {response.text}")
            response.raise_for_status()
            
        data = response.json()
        
        return data["choices"][0]["message"]["content"]

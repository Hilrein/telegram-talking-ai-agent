import logging
from typing import Optional

from openai import AsyncOpenAI
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"


class NvidiaClient:
    def __init__(
        self,
        api_key: str,
        model: str = NVIDIA_DEFAULT_MODEL,
        base_url: str = NVIDIA_DEFAULT_BASE_URL,
    ):
        self.model = model
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )

    async def __aenter__(self) -> "NvidiaClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self._client.close()

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content
            return content or ""

        except Exception as e:
            logger.error("NVIDIA NIM API error: %s", e)
            console.print(f"[red]NVIDIA NIM API Error: {e}[/red]")
            raise

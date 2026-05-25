import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

class AudioService:
    def __init__(
        self,
        nvidia_api_key: str,
        cartesia_api_key: Optional[str] = None,
        cartesia_voice_id: Optional[str] = None
    ):
        self.nvidia_api_key = nvidia_api_key
        self.cartesia_api_key = cartesia_api_key
        self.cartesia_voice_id = cartesia_voice_id

    async def transcribe_voice(self, audio_bytes: bytes) -> str:
        """Transcribe OGG voice message using Nvidia NIM Whisper API."""
        url = "https://ai.api.nvidia.com/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.nvidia_api_key}"
        }
        files = {
            "file": ("voice.ogg", audio_bytes, "audio/ogg")
        }
        data = {
            "model": "openai/whisper-large-v3",
            "language": "ru"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            result = response.json()
            return result.get("text", "")

    async def generate_voice(self, text: str) -> bytes:
        """Generate voice from text using Cartesia API."""
        if not self.cartesia_api_key:
            raise ValueError("CARTESIA_API_KEY is not set")

        url = "https://api.cartesia.ai/tts/bytes"
        headers = {
            "Cartesia-Version": "2024-06-10",
            "X-API-Key": self.cartesia_api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "model_id": "sonic-multilingual",
            "transcript": text,
            "voice": {
                "mode": "id",
                "id": self.cartesia_voice_id,
                "__experimental_controls": {
                    "speed": "slow",
                    "emotion": ["positivity:high"]
                }
            },
            "output_format": {
                "container": "mp3",
                "encoding": "mp3",
                "sample_rate": 44100
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.content

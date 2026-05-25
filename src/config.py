
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"


@dataclass
class Config:
    data_dir: Path
    # NVIDIA NIM settings
    nvidia_api_key: Optional[str] = None
    nvidia_base_url: str = NVIDIA_DEFAULT_BASE_URL
    nvidia_model: str = NVIDIA_DEFAULT_MODEL
    # Business bot settings
    bot_token: Optional[str] = None
    business_owner_chat_id: Optional[int] = None
    pending_timeout_minutes: int = 10
    business_ai_model: str = NVIDIA_DEFAULT_MODEL
    business_style_prompt: str = ""
    # Context memory settings
    context_limit: int = 30
    context_months: int = 3
    owner_name: str = ""
    # Mini App settings
    miniapp_port: int = 8000
    ngrok_authtoken: str = ""
    
    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"
    
    @property
    def session_path(self) -> Path:
        return self.data_dir / "session"

    @classmethod
    def load(cls) -> Optional["Config"]:
        load_dotenv()
            
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        
        owner_chat_id_str = os.getenv("BUSINESS_OWNER_CHAT_ID")
        owner_chat_id = int(owner_chat_id_str) if owner_chat_id_str else None

        timeout_str = os.getenv("PENDING_TIMEOUT_MINUTES", "10")
        try:
            timeout = int(timeout_str)
        except ValueError:
            timeout = 10

        return cls(
            data_dir=data_dir,
            nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
            nvidia_base_url=os.getenv("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL),
            nvidia_model=os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
            bot_token=os.getenv("BOT_TOKEN"),
            business_owner_chat_id=owner_chat_id,
            pending_timeout_minutes=timeout,
            business_ai_model=os.getenv(
                "BUSINESS_AI_MODEL",
                os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
            ),
            business_style_prompt=os.getenv("BUSINESS_STYLE_PROMPT", ""),
        )


def load_config() -> Config:
    current = Path(__file__).parent.parent
    env_path = current / ".env"
    
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()
    

    
    data_dir = Path(os.getenv("DATA_DIR", current / "data"))
    
    data_dir.mkdir(parents=True, exist_ok=True)

    owner_chat_id_str = os.getenv("BUSINESS_OWNER_CHAT_ID")
    owner_chat_id = int(owner_chat_id_str) if owner_chat_id_str else None

    timeout_str = os.getenv("PENDING_TIMEOUT_MINUTES", "10")
    try:
        timeout = int(timeout_str)
    except ValueError:
        timeout = 10
    
    return Config(
        data_dir=data_dir,
        nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
        nvidia_base_url=os.getenv("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL),
        nvidia_model=os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
        bot_token=os.getenv("BOT_TOKEN"),
        business_owner_chat_id=owner_chat_id,
        pending_timeout_minutes=timeout,
        business_ai_model=os.getenv(
            "BUSINESS_AI_MODEL",
            os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        ),
        business_style_prompt=os.getenv("BUSINESS_STYLE_PROMPT", ""),
        context_limit=int(os.getenv("CONTEXT_LIMIT", "30")),
        context_months=int(os.getenv("CONTEXT_MONTHS", "3")),
        owner_name=os.getenv("OWNER_NAME", ""),
        miniapp_port=int(os.getenv("MINIAPP_PORT", "8000")),
        ngrok_authtoken=os.getenv("NGROK_AUTHTOKEN", ""),
    )


NVIDIA_MODELS = [
    ("nvidia/llama-3.1-nemotron-ultra-253b-v1", "Nemotron Ultra 253B"),
    ("nvidia/llama-3.3-nemotron-super-49b-v1", "Nemotron Super 49B"),
    ("nvidia/llama-3.1-nemotron-nano-8b-v1", "Nemotron Nano 8B"),
    ("meta/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct"),
    ("meta/llama-3.1-8b-instruct", "Llama 3.1 8B Instruct"),
    ("deepseek-ai/deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("qwen/qwq-32b", "QwQ 32B"),
    ("mistralai/mistral-nemotron", "Mistral Nemotron"),
]


import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    tg_api_id: int
    tg_api_hash: str
    qwen_default_model: str
    google_client_secret_path: str
    data_dir: Path
    
    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"
    
    @property
    def session_path(self) -> Path:
        return self.data_dir / "session"

    @classmethod
    def load(cls) -> Optional["Config"]:
        load_dotenv()
        
        api_id = os.getenv("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        
        if not api_id or not api_hash:
            return None
            
        try:
            api_id = int(api_id)
        except ValueError:
            return None
            
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        
        return cls(
            tg_api_id=api_id,
            tg_api_hash=api_hash,
            qwen_default_model=os.getenv("QWEN_MODEL", "qwen-max"),
            google_client_secret_path=os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json"),
            data_dir=data_dir
        )


def load_config() -> Config:
    current = Path(__file__).parent.parent
    env_path = current / ".env"
    
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()
    
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    
    if not api_id or not api_hash:
        raise ValueError(
            "TG_API_ID and TG_API_HASH must be set in .env file.\n"
            "Get them from https://my.telegram.org"
        )
    
    api_id = api_id.strip().strip('"').strip("'")
    api_hash = api_hash.strip().strip('"').strip("'")
    
    qwen_model = os.getenv("QWEN_DEFAULT_MODEL", "coder-model")
    data_dir = Path(os.getenv("DATA_DIR", current / "data"))
    
    data_dir.mkdir(parents=True, exist_ok=True)
    
    return Config(
        tg_api_id=int(api_id),
        tg_api_hash=api_hash,
        qwen_default_model=qwen_model,
        google_client_secret_path=os.getenv("GOOGLE_CLIENT_SECRET", "client_secret.json"),
        data_dir=data_dir,
    )


QWEN_MODELS = [
    ("coder-model", "Standard model (Required for OAuth)"),
]

GEMINI_MODELS = [
    ("gemini-3-pro-preview", "Gemini 3 Pro Preview"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
    ("gemini-2.5-pro", "Gemini 2.5 Pro"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
]

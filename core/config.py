import json
import os
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path("config/credentials.json")

def load_config():
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())

def get_access_token() -> str | None:
    cfg = load_config()
    return cfg.get("access_token")

def save_access_token(token: str):
    cfg = load_config()
    cfg["access_token"] = token
    cfg["token_saved_at"] = datetime.now().isoformat()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

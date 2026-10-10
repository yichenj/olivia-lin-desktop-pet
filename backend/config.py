"""Non-executable local configuration. Environment variables take precedence."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def data_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Olivia Lin Fan Pet"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "olivia-desktop-pet"


@dataclass
class Config:
    api_key: str = field(default="", repr=False)
    base_url: str = field(default="", repr=False)
    model: str = field(default="", repr=False)
    database: Path = field(default_factory=lambda: data_directory() / "chat.sqlite3")
    prompt: Path = field(default_factory=lambda: ROOT / "prompts/olivia.md")
    provider: str = "ark"
    thinking: str = "disabled"
    bubble_seconds: int = 30
    workspace: Path = field(default_factory=lambda: ROOT)
    shell_enabled: bool = False


def load_config():
    path = Path(os.environ.get("OLIVIA_CONFIG", ROOT / ".olivia.local.json"))
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(values, dict):
        raise ValueError("Local configuration must be a JSON object")
    def get(env, key, default):
        return os.environ.get(env, values.get(key, default))
    defaults = Config()
    seconds = int(get("OLIVIA_BUBBLE_SECONDS", "bubble_seconds", 30))
    if not 1 <= seconds <= 3600:
        raise ValueError("bubble_seconds must be between 1 and 3600")
    thinking = str(get("ARK_THINKING", "thinking", "disabled"))
    if thinking not in ("disabled", "enabled", "auto"):
        raise ValueError("thinking must be disabled, enabled or auto")
    shell_enabled = values.get('shell_enabled', False)
    if type(shell_enabled) is not bool:
        raise ValueError('shell_enabled must be boolean')
    return Config(
        api_key=str(get("ARK_API_KEY", "api_key", "")),
        base_url=str(get("ARK_BASE_URL", "base_url", defaults.base_url)),
        model=str(get("ARK_MODEL", "model", defaults.model)),
        database=Path(get("OLIVIA_DB_PATH", "database", defaults.database)).expanduser(),
        prompt=Path(get("OLIVIA_PROMPT_PATH", "prompt", defaults.prompt)).expanduser(),
        provider=str(get("OLIVIA_PROVIDER", "provider", "ark")),
        thinking=thinking,
        bubble_seconds=seconds,
        workspace=(path.parent / Path(get('OLIVIA_WORKSPACE', 'workspace', ROOT)).expanduser()).resolve(),
        shell_enabled=shell_enabled,
    )

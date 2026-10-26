import json
import os
import stat
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("PAGEPATROL_HOME")
    return Path(override) if override else Path.home() / ".pagepatrol"


@dataclass
class LLMConfig:
    provider: str
    model: str
    base_url: str | None = None


def _write_toml(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps produces valid TOML basic strings for everything we store.
    lines = [f"{key} = {json.dumps(value)}" for key, value in values.items() if value is not None]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_llm_config() -> LLMConfig | None:
    data = _read_toml(config_dir() / "config.toml").get("llm")
    if not data or not data.get("provider") or not data.get("model"):
        return None
    return LLMConfig(provider=data["provider"], model=data["model"], base_url=data.get("base_url"))


def save_llm_config(config: LLMConfig) -> Path:
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {k: v for k, v in asdict(config).items() if v is not None}
    body = "[llm]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in values.items())
    path.write_text(body, encoding="utf-8")
    return path


def _credentials_path() -> Path:
    return config_dir() / "credentials.toml"


def load_saved_key(provider: str) -> str | None:
    return _read_toml(_credentials_path()).get(provider)


def save_key(provider: str, key: str) -> Path:
    path = _credentials_path()
    keys = _read_toml(path)
    keys[provider] = key
    _write_toml(path, keys)
    # Only matters on POSIX; on Windows the file inherits the user profile ACL.
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path

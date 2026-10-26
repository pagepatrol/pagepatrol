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
    # USD per million tokens, for providers whose prices we don't ship.
    price_input: float | None = None
    price_output: float | None = None


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
    return LLMConfig(
        provider=data["provider"],
        model=data["model"],
        base_url=data.get("base_url"),
        price_input=data.get("price_input"),
        price_output=data.get("price_output"),
    )


def save_llm_config(config: LLMConfig) -> Path:
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {k: v for k, v in asdict(config).items() if v is not None}
    body = "[llm]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in values.items())
    path.write_text(body, encoding="utf-8")
    return path


KEYRING_SERVICE = "pagepatrol"


def _credentials_path() -> Path:
    return config_dir() / "credentials.toml"


def _keyring_get(provider: str) -> str | None:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, provider)
    except KeyringError:
        # No usable backend (headless Linux, some CI images).
        return None


def _keyring_set(provider: str, key: str) -> bool:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, provider, key)
    except KeyringError:
        return False
    return True


def key_location(provider: str) -> str | None:
    if _keyring_get(provider):
        return "system keyring"
    if provider in _read_toml(_credentials_path()):
        return str(_credentials_path())
    return None


def load_saved_key(provider: str) -> str | None:
    return _keyring_get(provider) or _read_toml(_credentials_path()).get(provider)


def save_key(provider: str, key: str) -> str:
    """Store the key, preferring the OS keyring. Returns where it went."""
    if _keyring_set(provider, key):
        _remove_file_key(provider)
        return "system keyring"

    path = _credentials_path()
    keys = _read_toml(path)
    keys[provider] = key
    _write_toml(path, keys)
    # Only matters on POSIX; on Windows the file inherits the user profile ACL.
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return str(path)


def _remove_file_key(provider: str) -> None:
    # Don't leave an old plain-text copy behind once the key is in the keyring.
    path = _credentials_path()
    keys = _read_toml(path)
    if keys.pop(provider, None) is None:
        return
    if keys:
        _write_toml(path, keys)
    else:
        path.unlink()

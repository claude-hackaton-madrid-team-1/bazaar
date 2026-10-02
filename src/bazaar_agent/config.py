"""Settings loaded from the environment and the repo's `.env`, validated at startup.

Values are never printed: `Settings.__repr__` hides every secret.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://bazaar.causaprima.ai"
DEFAULT_DATABASE_URL = "postgresql://bazaar:bazaar@localhost:5433/bazaar"


class ConfigError(RuntimeError):
    """A required setting is missing. The message names the variable, never its value."""


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; comments and blank lines are skipped, surrounding quotes removed."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


class Settings(BaseModel):
    bazaar_url: str = DEFAULT_URL
    bazaar_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_runtime: str | None = None  # BAZAAR_LLM_RUNTIME: pins the runtime LLM (alias or model id)
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    data_dir: Path = Field(default=REPO_ROOT / ".local")

    @property
    def feed_dir(self) -> Path:
        return self.data_dir / "feed"

    def require_team_key(self) -> str:
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            raise ConfigError("BAZAAR_KEY is not set: add it to .env (the key on the team slip).")
        return self.bazaar_key.get_secret_value()


def load_settings(env_file: Path | None = None) -> Settings:
    """Environment variables win over `.env`, so a one-off override needs no file edit."""
    file_values = read_env_file(env_file or REPO_ROOT / ".env")

    def pick(name: str) -> str | None:
        value = os.environ.get(name) or file_values.get(name)
        return value or None

    data = {
        "bazaar_url": pick("BAZAAR_URL") or DEFAULT_URL,
        "bazaar_key": pick("BAZAAR_KEY"),
        "typesafe_api_key": pick("TYPESAFE_API_KEY"),
        "anthropic_api_key": pick("ANTHROPIC_API_KEY"),
        "openai_api_key": pick("OPENAI_API_KEY"),
        "llm_runtime": pick("BAZAAR_LLM_RUNTIME"),
        "database_url": pick("DATABASE_URL") or DEFAULT_DATABASE_URL,
    }
    if data_dir := pick("BAZAAR_DATA_DIR"):
        data["data_dir"] = data_dir
    return Settings.model_validate(data)

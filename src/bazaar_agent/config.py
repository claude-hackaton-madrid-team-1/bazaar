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
BROKER_ENV_FILE = "broker.env"  # <data_dir>/broker.env (0600): the broker key a live `venue open` saved


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
    claude_code_oauth_token: SecretStr | None = None  # `claude setup-token`: Claude models on the subscription
    llm_runtime: str | None = None  # BAZAAR_LLM_RUNTIME: pins the runtime LLM (alias or model id)
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    team_id: str | None = Field(default=None, pattern=r"^t\d{1,3}$")  # BAZAAR_TEAM_ID; else /api/me (identity.py)
    data_dir: Path = Field(default=REPO_ROOT / ".local")
    broker_key: SecretStr | None = None  # BAZAAR_BROKER_KEY: our venue's X-Broker-Key (returned once on open)
    venue_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$")  # BAZAAR_VENUE: our venue id

    @property
    def feed_dir(self) -> Path:
        return self.data_dir / "feed"

    def require_team_key(self) -> str:
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            raise ConfigError("BAZAAR_KEY is not set: add it to .env (the key on the team slip).")
        return self.bazaar_key.get_secret_value()

    def require_broker_key(self) -> str:
        if self.broker_key is None or not self.broker_key.get_secret_value():
            raise ConfigError(
                "BAZAAR_BROKER_KEY is not set: a live `bazaar venue open` saves it to "
                f"{BROKER_ENV_FILE} in the data dir; on Railway set the variable by hand."
            )
        return self.broker_key.get_secret_value()


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
        "claude_code_oauth_token": pick("CLAUDE_CODE_OAUTH_TOKEN"),
        "llm_runtime": pick("BAZAAR_LLM_RUNTIME"),
        "database_url": pick("DATABASE_URL") or DEFAULT_DATABASE_URL,
        "team_id": pick("BAZAAR_TEAM_ID"),
    }
    if data_dir := pick("BAZAAR_DATA_DIR"):
        data["data_dir"] = data_dir
    # The broker key and venue id: the environment, then `.env`, then what a live `venue open` saved.
    saved = read_env_file(Path(data.get("data_dir") or REPO_ROOT / ".local") / BROKER_ENV_FILE)
    data["broker_key"] = pick("BAZAAR_BROKER_KEY") or saved.get("BAZAAR_BROKER_KEY") or None
    data["venue_id"] = pick("BAZAAR_VENUE") or saved.get("BAZAAR_VENUE") or None
    return Settings.model_validate(data)

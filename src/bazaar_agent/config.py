"""Settings loaded from the environment and the repo's `.env`, validated at startup.

Values are never printed: `Settings.__repr__` hides every secret.

Real game or simulator (`bazaar-sim`), never mixed up:
- `BAZAAR_URL` on the official host (bazaar.causaprima.ai) takes only a real team key: a `sim-...`
  key is refused here, before any request.
- Any other `BAZAAR_URL` is a simulator: only a `sim-...` key may be sent there, so the real key
  can never leak to a host that is not the game.
- Against a simulator the agents' memory is `BAZAAR_SIM_DATABASE_URL` (else `DATABASE_URL`), and a
  URL naming the team's real `railway` database is refused: simulated trades never land in it.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, Field, SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://bazaar.causaprima.ai"
DEFAULT_DATABASE_URL = "postgresql://bazaar:bazaar@localhost:5433/bazaar"
OFFICIAL_HOST = "bazaar.causaprima.ai"
SIM_KEY_PREFIX = "sim-"
REAL_DATABASE = "railway"  # the team's shared Railway database: real-game memory only


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

    @property
    def feed_dir(self) -> Path:
        return self.data_dir / "feed"

    @property
    def simulator(self) -> bool:
        """True when BAZAAR_URL is not the official game: a `bazaar-sim` (or any other) host."""
        return not is_official(self.bazaar_url)

    def require_team_key(self) -> str:
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            raise ConfigError("BAZAAR_KEY is not set: add it to .env (the key on the team slip).")
        key = self.bazaar_key.get_secret_value()
        check_key_for_url(self.bazaar_url, key)
        return key

    def team_key(self) -> str | None:
        """The team key after the same guard, or None when it is not set (keyless reads)."""
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            return None
        return self.require_team_key()

    def require_database_url(self) -> str:
        """DATABASE_URL (BAZAAR_SIM_DATABASE_URL against a simulator), never the real db for simulated play."""
        url = self.database_url.get_secret_value()
        if self.simulator and database_name(url) == REAL_DATABASE:
            raise ConfigError(
                f"BAZAAR_URL is a simulator but the database URL names the real `{REAL_DATABASE}` database: "
                "set BAZAAR_SIM_DATABASE_URL to the `bazaar_sim` database (README, Simulator)."
            )
        return url


def is_official(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == OFFICIAL_HOST


def check_key_for_url(url: str, key: str) -> None:
    """A sim key only to a simulator, the real key only to the real game. Messages never carry the key."""
    simulated = key.startswith(SIM_KEY_PREFIX)
    if is_official(url) and simulated:
        raise ConfigError(
            f"BAZAAR_KEY is a simulator key ({SIM_KEY_PREFIX}...) but BAZAAR_URL is the real game ({OFFICIAL_HOST}): "
            "point BAZAAR_URL at the simulator, or use the team key."
        )
    if not is_official(url) and not simulated:
        host = urlsplit(url).hostname or url
        raise ConfigError(
            f"BAZAAR_URL ({host}) is not the official Bazaar: only a simulator key ({SIM_KEY_PREFIX}...) is sent "
            "there, never the real team key. Set BAZAAR_KEY=sim-team1 for the simulator."
        )


def database_name(url: str) -> str:
    """The database a Postgres URL names: its path, or a `dbname=` query override (libpq honours both)."""
    parts = urlsplit(url)
    override = parse_qs(parts.query).get("dbname")
    if override:
        return override[-1]
    return unquote(parts.path.lstrip("/"))


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
        "database_url_sim": pick("BAZAAR_SIM_DATABASE_URL"),
        "team_id": pick("BAZAAR_TEAM_ID"),
    }
    if data_dir := pick("BAZAAR_DATA_DIR"):
        data["data_dir"] = data_dir
    sim_db = data.pop("database_url_sim")
    if sim_db and not is_official(str(data["bazaar_url"])):
        data["database_url"] = sim_db
    return Settings.model_validate(data)

"""Settings loaded from the environment and the repo's `.env`, validated at startup.

Values are never printed: `Settings.__repr__` hides every secret.

The target is a feature flag over two hardcoded URLs, decided here and nowhere else:
- `BAZAAR_SIM` unset or 0 → the real game, https://bazaar.causaprima.ai, with `BAZAAR_KEY`.
- `BAZAAR_SIM=1` → the simulator (`bazaar-sim`, README "Simulator") with `BAZAAR_SIM_KEY`
  (default `sim-team1`). The real `BAZAAR_KEY` is not even read in this mode.
- `BAZAAR_SIM=local` → the same, against `uv run bazaar-sim serve` on this laptop (127.0.0.1:8765, or the
  port in `BAZAAR_SIM_PORT` when several simulators share the laptop).
- `BAZAAR_URL` is gone: set, it fails fast (a free-form URL is how a real key reaches a wrong host).
- Guards on top: a `sim-...` key never reaches the real host, and only a `sim-...` key reaches the
  simulator; the key is refused before any request.
- Against the simulator the agents' memory is `BAZAAR_SIM_DATABASE_URL` (else `DATABASE_URL`), and a
  URL naming the team's real `railway` database is refused: simulated trades never land in it.
  Local files (feed capture, ledger, duels log) default to `.local/sim-client/`, not `.local/`.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, Field, SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://bazaar.causaprima.ai"  # the real game: BAZAAR_SIM unset or 0
SIM_URL = "https://bazaar-sim-production-1d48.up.railway.app"  # the simulator: BAZAAR_SIM=1
LOCAL_SIM_URL = "http://127.0.0.1:8765"  # a simulator on this laptop (`uv run bazaar-sim serve`): BAZAAR_SIM=local
DEFAULT_SIM_KEY = "sim-team1"  # BAZAAR_SIM_KEY: sim-team1 ... sim-team8 (not secrets)
DEFAULT_DATABASE_URL = "postgresql://bazaar:bazaar@localhost:5433/bazaar"
OFFICIAL_HOST = "bazaar.causaprima.ai"
SIM_KEY_PREFIX = "sim-"
REAL_DATABASE = "railway"  # the team's shared Railway database: real-game memory only
SIM_DATA_DIR = REPO_ROOT / ".local" / "sim-client"  # default data dir against a simulator


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
    simulated: bool = False  # BAZAAR_SIM=1: the simulator; else the real game
    bazaar_url: str = DEFAULT_URL  # derived from `simulated`, never read from the environment
    bazaar_key: SecretStr | None = None  # the ACTIVE key: BAZAAR_SIM_KEY (simulator) or BAZAAR_KEY (real)
    typesafe_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    claude_code_oauth_token: SecretStr | None = None  # `claude setup-token`: Claude models on the subscription
    llm_runtime: str | None = None  # BAZAAR_LLM_RUNTIME: pins the runtime LLM (alias or model id)
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    sim_database: bool = False  # BAZAAR_SIM_DATABASE_URL is the database: one of the simulator's own
    team_id: str | None = Field(default=None, pattern=r"^t\d{1,3}$")  # BAZAAR_TEAM_ID; else /api/me (identity.py)
    data_dir: Path = Field(default=REPO_ROOT / ".local")

    @property
    def feed_dir(self) -> Path:
        return self.data_dir / "feed"

    @property
    def simulator(self) -> bool:
        """True against the simulator (BAZAAR_SIM=1, or any host that is not the official game)."""
        return self.simulated or not is_official(self.bazaar_url)

    @property
    def target(self) -> dict[str, str]:
        """Where every request goes, for banners and /health: never a key."""
        mode = "simulator" if self.simulator else "real"
        return {"mode": mode, "url": self.bazaar_url}

    def target_line(self) -> str:
        if self.simulator:
            return f"target: SIMULATOR {self.bazaar_url} (BAZAAR_SIM, key {SIM_KEY_PREFIX}...)"
        return f"target: real game {self.bazaar_url}"

    def require_team_key(self) -> str:
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            if self.simulated:
                raise ConfigError("BAZAAR_SIM_KEY is empty: unset it for sim-team1, or set sim-team1 ... sim-team8.")
            raise ConfigError("BAZAAR_KEY is not set: add it to .env (the key on the team slip).")
        key = self.bazaar_key.get_secret_value()
        check_key_for_url(self.bazaar_url, key)
        return key

    def team_key(self) -> str | None:
        """The team key after the same guard, or None when it is not set (keyless reads)."""
        if self.bazaar_key is None or not self.bazaar_key.get_secret_value():
            return None
        return self.require_team_key()

    def require_database_url(self, url: str | None = None) -> str:
        """DATABASE_URL (BAZAAR_SIM_DATABASE_URL against a simulator), never the real db for simulated play.

        An explicit `url` gets the same check: no caller can put simulated data in the real database."""
        url = self.database_url.get_secret_value() if url is None else url
        if self.simulator and database_name(url) == REAL_DATABASE:
            raise ConfigError(
                f"BAZAAR_SIM=1 (the simulator) but the database URL names the real `{REAL_DATABASE}` database: "
                "set BAZAAR_SIM_DATABASE_URL to the `bazaar_sim` database (README, Simulator)."
            )
        return url


def is_official(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == OFFICIAL_HOST


def check_key_for_url(url: str, key: str) -> None:
    """A sim key only to a simulator, the real key only to the real game. Messages never carry the key."""
    simulated = key.startswith(SIM_KEY_PREFIX)
    if is_official(url) and urlsplit(url).scheme != "https":
        raise ConfigError(f"the real game is https://{OFFICIAL_HOST}: a team key never travels unencrypted.")
    if is_official(url) and simulated:
        raise ConfigError(
            f"BAZAAR_KEY is a simulator key ({SIM_KEY_PREFIX}...) but the target is the real game ({OFFICIAL_HOST}): "
            "set BAZAAR_SIM=1 for the simulator (its key is BAZAAR_SIM_KEY), or put the team key in BAZAAR_KEY."
        )
    if not is_official(url) and not simulated:
        host = urlsplit(url).hostname or url
        raise ConfigError(
            f"the target ({host}) is not the official Bazaar: only a simulator key ({SIM_KEY_PREFIX}...) is sent "
            "there, never the real team key. BAZAAR_SIM_KEY must be sim-team1 ... sim-team8."
        )


def same_database(a: str, b: str) -> bool:
    """Do two URLs name the same database (host, port, name)? Unparsable counts as the same: fail safe."""
    try:
        pa, pb = urlsplit(a), urlsplit(b)
        return ((pa.hostname or "").lower(), pa.port or 5432, database_name(a)) == (
            (pb.hostname or "").lower(),
            pb.port or 5432,
            database_name(b),
        )
    except ValueError:
        return True


def database_name(url: str) -> str:
    """The database a Postgres URL names: its path, or a `dbname=` query override (libpq honours both)."""
    parts = urlsplit(url)
    override = parse_qs(parts.query).get("dbname")
    if override:
        return override[-1]
    return unquote(parts.path.lstrip("/"))


def env_file_path() -> Path:
    """The env file every reader uses: BAZAAR_ENV_FILE when set, else the repo's `.env`.

    The override REPLACES `.env`, so a typo must not silently drop it (a BAZAAR_SIM=1 kept in `.env`
    would vanish and the target would become the real game): a missing or relative path fails fast."""
    override = os.environ.get("BAZAAR_ENV_FILE")
    if not override:
        return REPO_ROOT / ".env"
    path = Path(override)
    if not path.is_absolute() or not path.is_file():
        raise ConfigError("BAZAAR_ENV_FILE must name an existing file by absolute path (it replaces .env).")
    return path


def load_settings(env_file: Path | None = None) -> Settings:
    """Environment variables win over `.env`, so a one-off override needs no file edit.

    BAZAAR_ENV_FILE names another env file instead of the repo's `.env` (the simulator smoke points it
    at an empty file, so a laptop's real secrets never load into a smoke run)."""
    file_values = read_env_file(env_file or env_file_path())

    def pick(name: str) -> str | None:
        value = os.environ.get(name) or file_values.get(name)
        return value or None

    if pick("BAZAAR_URL"):
        raise ConfigError(
            "BAZAAR_URL is no longer read: remove it from .env and the environment. The target is a flag, "
            "BAZAAR_SIM=1 for the simulator, unset (or 0) for the real game (README, Simulator)."
        )
    flag = sim_flag(pick("BAZAAR_SIM"))
    simulated = flag != "real"
    data = {
        "simulated": simulated,
        "bazaar_url": (
            local_sim_url(pick("BAZAAR_SIM_PORT")) if flag == "local" else {"real": DEFAULT_URL, "sim": SIM_URL}[flag]
        ),
        # Against the simulator the real key is never loaded at all, so nothing can send it there.
        "bazaar_key": (pick("BAZAAR_SIM_KEY") or DEFAULT_SIM_KEY) if simulated else pick("BAZAAR_KEY"),
        "typesafe_api_key": pick("TYPESAFE_API_KEY"),
        "anthropic_api_key": pick("ANTHROPIC_API_KEY"),
        "openai_api_key": pick("OPENAI_API_KEY"),
        "claude_code_oauth_token": pick("CLAUDE_CODE_OAUTH_TOKEN"),
        "llm_runtime": pick("BAZAAR_LLM_RUNTIME"),
        "database_url": pick("DATABASE_URL") or DEFAULT_DATABASE_URL,
        "database_url_sim": pick("BAZAAR_SIM_DATABASE_URL"),
        "database_url_real": pick("DATABASE_URL") or DEFAULT_DATABASE_URL,
        "team_id": pick("BAZAAR_TEAM_ID"),
    }
    if data_dir := pick("BAZAAR_DATA_DIR"):
        data["data_dir"] = data_dir
    elif simulated:
        data["data_dir"] = str(SIM_DATA_DIR)  # the real feed capture and ledger never see simulated play
    sim_db, real_db = data.pop("database_url_sim"), data.pop("database_url_real")
    if sim_db and simulated:
        data["database_url"] = sim_db
        data["sim_database"] = not same_database(str(sim_db), str(real_db))  # not the real one, respelled
    return Settings.model_validate(data)


def local_sim_url(port: str | None) -> str:
    """BAZAAR_SIM=local's address: 127.0.0.1, port 8765 unless BAZAAR_SIM_PORT names another (one laptop, several
    workers' simulators). Always loopback: the port is the only part that moves, and only for BAZAAR_SIM=local."""
    if port is None:
        return LOCAL_SIM_URL
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise ConfigError("BAZAAR_SIM_PORT must be a port number from 1024 to 65535 (the laptop simulator's port).")
    return f"http://127.0.0.1:{int(port)}"


def sim_flag(raw: str | None) -> str:
    """BAZAAR_SIM: unset, 0/false/no/off → "real"; 1/true/yes/on → "sim"; local → "local"; anything else fails."""
    value = (raw or "").strip().lower()
    if value in ("", "0", "false", "no", "off"):
        return "real"
    if value in ("1", "true", "yes", "on"):
        return "sim"
    if value == "local":
        return "local"
    raise ConfigError("BAZAAR_SIM must be 1 (the simulator), local (one on this laptop) or 0 / unset (the real game).")

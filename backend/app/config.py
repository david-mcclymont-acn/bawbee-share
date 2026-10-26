from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from BAWBEE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BAWBEE_")

    database_url: str = "sqlite:///./data/bawbee.db"
    # Built frontend (Vite dist). Served at / when present; absent in API-only dev.
    static_dir: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    log_level: str = "INFO"
    worker_heartbeat_seconds: int = 300
    # Touched on every heartbeat; the container healthcheck fails if it goes stale.
    worker_heartbeat_file: Path = Path("/tmp/bawbee-worker.heartbeat")  # noqa: S108

    # ---- login (PLAN.md decision 27) ----
    # The public HTTPS URL, used for the OIDC redirect.
    public_url: str = "http://127.0.0.1:8000"
    # Session cookies are HTTPS-only. Turn off only for plain-HTTP local development.
    cookie_secure: bool = True
    session_days: int = 30
    # Password login. Turn off only once OIDC works for both of you.
    auth_local: bool = True
    # Optional OIDC (e.g. authentik): on when all three are set.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_label: str = "authentik"

    # ---- banks (PLAN.md §8, §13) ----
    # Encrypts bank session ids and tokens at rest. A file (a Docker secret) or the key itself.
    fernet_key_file: Path | None = None
    fernet_key: str | None = None
    # Enable Banking: the application id, and its private key as a mounted secret.
    enable_banking_app_id: str | None = None
    enable_banking_key_file: Path | None = None
    enable_banking_api: str = "https://api.enablebanking.com"
    starling_api: str = "https://api.starlingbank.com"
    # Monzo Developer API (decision 29): a confidential client made at developers.monzo.com.
    # The secret is best kept in a file (a Docker secret).
    monzo_client_id: str | None = None
    monzo_client_secret: str | None = None
    monzo_client_secret_file: Path | None = None
    monzo_api: str = "https://api.monzo.com"
    # A fake bank with synthetic data, for CI and Codespaces. Never in production.
    banks_demo: bool = False
    # Scheduled syncs, UK time (banks allow only a few unattended syncs a day).
    sync_hours: str = "6,18"
    # A "Sync now" at most this often per connection.
    manual_sync_minutes: int = 30
    # How long a password confirmation lets someone link or disconnect a bank.
    reauth_hours: int = 12

    # ---- Home Assistant (PLAN.md Phase 5) ----
    # The webhook URL notifications are POSTed to, e.g. http://ha.lan:8123/api/webhook/<id>.
    # Its id is the secret, so this lives in .env, never in git.
    ha_webhook_url: str | None = None
    # Warn when Joint Billing is projected to dip below this before the next payday.
    ha_low_balance_pence: int = 5000

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def enable_banking_problem(self) -> str | None:
        """Why Enable Banking can't be used, or None when it's set up."""
        if not self.enable_banking_app_id:
            return "BAWBEE_ENABLE_BANKING_APP_ID isn't set."
        if not self.enable_banking_key_file:
            return "BAWBEE_ENABLE_BANKING_KEY_FILE isn't set."
        return _unreadable(self.enable_banking_key_file, "private key")

    @property
    def enable_banking_enabled(self) -> bool:
        return self.enable_banking_problem is None

    @property
    def monzo_secret(self) -> str | None:
        if self.monzo_client_secret_file is not None:
            try:
                return self.monzo_client_secret_file.read_text(encoding="utf-8").strip()
            except OSError:
                return None
        return self.monzo_client_secret

    @property
    def monzo_problem(self) -> str | None:
        """Why Monzo can't be linked, or None when it's set up."""
        if not self.monzo_client_id:
            return "BAWBEE_MONZO_CLIENT_ID isn't set."
        if self.monzo_client_secret_file is not None:
            return _unreadable(self.monzo_client_secret_file, "client secret")
        if not self.monzo_client_secret:
            return "There's no client secret: set BAWBEE_MONZO_CLIENT_SECRET_FILE."
        return None

    @property
    def monzo_enabled(self) -> bool:
        return self.monzo_problem is None

    @property
    def bank_redirect_url(self) -> str:
        return f"{self.public_url.rstrip('/')}/api/banks/callback"


def _unreadable(path: Path, what: str) -> str | None:
    """Why a secret file can't be used (missing, not readable by the app, empty), or None."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return f"The {what} file {path} isn't there (is ./secrets mounted?)."
    except PermissionError:
        return f"The app can't read the {what} file {path}: it runs as uid 1000."
    except OSError as exc:
        return f"The {what} file {path} can't be read: {exc.strerror or exc}."
    return None if text else f"The {what} file {path} is empty."


@lru_cache
def get_settings() -> Settings:
    return Settings()

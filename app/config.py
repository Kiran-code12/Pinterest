"""Settings come only from environment variables (optionally loaded from a local .env file)."""
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("engine.config")

DEFAULT_PASSWORD = "change-me"


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=value lines. Supports comments (# at line start or after whitespace) and quoted values."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        value = raw.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:  # an unquoted '#' that starts the value or follows whitespace begins a comment
            value = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip()
        out[key.strip()] = value
    return out


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Real environment variables always win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)


def _persisted_dev_secret(data_dir: Path) -> str:
    """Dev convenience: keep a generated SECRET_KEY on disk (0600) so sessions and stored Pinterest tokens
    survive restarts. Production must set SECRET_KEY explicitly (enforced above)."""
    path = data_dir / ".secret_key"
    try:
        if path.exists():
            value = path.read_text().strip()
            if len(value) >= 32:
                return value
        data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        path.write_text(value)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError:
        return secrets.token_urlsafe(48)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _list(value: str | None, default: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in (value or default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    database_url: str = ""
    data_dir: Path = ROOT / "data" / "engine"
    secret_key: str = ""
    admin_username: str = "admin"
    admin_password: str = DEFAULT_PASSWORD
    # AI
    ai_provider: str = "local"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = field(default="", repr=False)
    anthropic_model: str = "claude-haiku-4-5-20251001"
    # Amazon Associates India: Creators API (OAuth2 client credentials)
    amazon_credential_id: str = field(default="", repr=False)
    amazon_credential_secret: str = field(default="", repr=False)
    amazon_partner_tag: str = ""
    amazon_marketplace: str = "www.amazon.in"
    amazon_credential_version: str = "3.2"
    amazon_api_base: str = "https://creatorsapi.amazon"
    amazon_token_url: str = ""
    # EarnKaro (no public API found: import/manual links only)
    earnkaro_link_domains: tuple[str, ...] = ("ekaro.in", "earnkaro.com")
    # Direct publishing to Pinterest is OFF for the MVP: the app prepares drafts that you post by hand.
    # The whole integration below stays in the codebase (isolated) and is enabled with DIRECT_PUBLISHING_ENABLED=true.
    direct_publishing_enabled: bool = False
    # Pinterest (official API v5, OAuth 2.0). Tokens are NOT configured here: they are obtained by "Connect".
    pinterest_provider: str = "real"  # real | mock (mock = simulation, nothing is sent to Pinterest)
    pinterest_client_id: str = ""
    pinterest_client_secret: str = field(default="", repr=False)
    pinterest_redirect_uri: str = "http://localhost:8000/pinterest/callback"
    pinterest_scopes: tuple[str, ...] = ("boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read")
    pinterest_api_base: str = "https://api.pinterest.com/v5"
    pinterest_oauth_url: str = "https://www.pinterest.com/oauth/"
    scheduler_enabled: bool = False  # scheduled pins are only auto-published by the worker when true
    max_pins_per_day: int = 3
    schedule_slots: tuple[str, ...] = ("09:00", "14:00", "20:00")
    schedule_tz: str = "Asia/Kolkata"
    # Misc
    enable_demo_provider: bool = True
    allow_private_urls: bool = False  # only for tests; blocks SSRF otherwise
    login_rate_limit: int = 5  # attempts per minute per IP
    action_rate_limit: int = 60  # expensive actions per minute per IP

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    def secret_values(self) -> list[str]:
        """Values that must never appear in logs."""
        vals = [self.secret_key, self.admin_password, self.openai_api_key, self.anthropic_api_key,
                self.amazon_credential_id, self.amazon_credential_secret, self.pinterest_client_secret]
        return [v for v in vals if v and len(v) >= 6]


def build_settings(environ: dict[str, str] | None = None) -> Settings:
    e = environ if environ is not None else os.environ
    env = e.get("APP_ENV", "dev").lower()
    data_dir = Path(e.get("DATA_DIR") or ROOT / "data" / "engine")
    password = e.get("ADMIN_PASSWORD", "") or DEFAULT_PASSWORD
    secret = e.get("SECRET_KEY", "")
    if env == "production":
        if password == DEFAULT_PASSWORD:
            raise RuntimeError("Set ADMIN_PASSWORD to a strong value when APP_ENV=production")
        if len(secret) < 32:
            raise RuntimeError("Set SECRET_KEY (32+ random characters) when APP_ENV=production")
    elif password == DEFAULT_PASSWORD:
        log.warning("Using the default admin password. Set ADMIN_PASSWORD in .env before exposing this app.")
    if not secret:
        secret = _persisted_dev_secret(data_dir)
    return Settings(
        env=env,
        database_url=e.get("DATABASE_URL") or f"sqlite:///{(data_dir / 'engine.db').as_posix()}",
        data_dir=data_dir,
        secret_key=secret,
        admin_username=e.get("ADMIN_USERNAME", "admin"),
        admin_password=password,
        ai_provider=e.get("AI_PROVIDER", "local").lower(),
        openai_api_key=e.get("OPENAI_API_KEY", ""),
        openai_model=e.get("OPENAI_MODEL", "gpt-4o-mini"),
        anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=e.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        amazon_credential_id=e.get("AMAZON_CREDENTIAL_ID", ""),
        amazon_credential_secret=e.get("AMAZON_CREDENTIAL_SECRET", ""),
        amazon_partner_tag=e.get("AMAZON_PARTNER_TAG", ""),
        amazon_marketplace=e.get("AMAZON_MARKETPLACE", "www.amazon.in"),
        amazon_credential_version=e.get("AMAZON_CREDENTIAL_VERSION", "3.2"),
        amazon_api_base=e.get("AMAZON_API_BASE", "https://creatorsapi.amazon").rstrip("/"),
        amazon_token_url=e.get("AMAZON_TOKEN_URL", ""),
        earnkaro_link_domains=_list(e.get("EARNKARO_LINK_DOMAINS"), "ekaro.in,earnkaro.com"),
        direct_publishing_enabled=_bool(e.get("DIRECT_PUBLISHING_ENABLED"), False),
        pinterest_provider="mock" if e.get("PINTEREST_PROVIDER", "real").lower() == "mock" else "real",
        pinterest_client_id=e.get("PINTEREST_CLIENT_ID", ""),
        pinterest_client_secret=e.get("PINTEREST_CLIENT_SECRET", ""),
        pinterest_redirect_uri=e.get("PINTEREST_REDIRECT_URI", "http://localhost:8000/pinterest/callback"),
        pinterest_scopes=_list(e.get("PINTEREST_SCOPES"), "boards:read,boards:write,pins:read,pins:write,user_accounts:read"),
        pinterest_api_base=e.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5").rstrip("/"),
        pinterest_oauth_url=e.get("PINTEREST_OAUTH_URL", "https://www.pinterest.com/oauth/"),
        scheduler_enabled=_bool(e.get("SCHEDULER_ENABLED"), False),
        max_pins_per_day=max(1, _int(e.get("MAX_PINS_PER_DAY"), 3)),
        schedule_slots=_list(e.get("SCHEDULE_SLOTS"), "09:00,14:00,20:00"),
        schedule_tz=e.get("SCHEDULE_TZ", "Asia/Kolkata"),
        enable_demo_provider=_bool(e.get("ENABLE_DEMO_PROVIDER"), True),
        allow_private_urls=_bool(e.get("ALLOW_PRIVATE_URLS"), False),
        login_rate_limit=_int(e.get("LOGIN_RATE_LIMIT"), 5),
        action_rate_limit=_int(e.get("ACTION_RATE_LIMIT"), 60),
    )


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return build_settings()

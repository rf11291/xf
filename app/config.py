import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _get(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v

@dataclass(frozen=True)
class Config:
    admin_user: str
    admin_password: str
    secret_key: str
    tz: str
    database_path: str
    company_name: str
    contact_name: str
    contact_url: str

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_from: str
    smtp_timeout: int

    scan_interval_minutes: int

def load_config() -> Config:
    return Config(
        admin_user=_get("ADMIN_USER", "admin"),
        admin_password=_get("ADMIN_PASSWORD", "admin"),
        secret_key=_get("SECRET_KEY", "change-me"),
        tz=_get("TZ", "Europe/Berlin"),
        database_path=_get("DATABASE_PATH", "./data/bot.db"),
        company_name=_get("COMPANY_NAME", "YourCompany"),
        contact_name=_get("CONTACT_NAME", "客服"),
        contact_url=_get("CONTACT_URL", "mailto:support@example.com"),

        smtp_host=_get("SMTP_HOST"),
        smtp_port=int(_get("SMTP_PORT", "587")),
        smtp_user=_get("SMTP_USER"),
        smtp_pass=_get("SMTP_PASS"),
        smtp_from=_get("SMTP_FROM", "noreply@example.com"),
        smtp_timeout=int(_get("SMTP_TIMEOUT", "30")),

        scan_interval_minutes=int(_get("SCAN_INTERVAL_MINUTES", "15")),
    )

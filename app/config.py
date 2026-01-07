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
    bot_token: str
    admin_ids: set[int]
    tz: str
    database_path: str
    company_name: str

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_from: str

    scan_interval_minutes: int

def load_config() -> Config:
    admin_raw = _get("ADMIN_IDS", "")
    admin_ids: set[int] = set()
    for x in admin_raw.split(","):
        x = x.strip()
        if x:
            admin_ids.add(int(x))

    return Config(
        bot_token=_get("BOT_TOKEN"),
        admin_ids=admin_ids,
        tz=_get("TZ", "Europe/Berlin"),
        database_path=_get("DATABASE_PATH", "./data/bot.db"),
        company_name=_get("COMPANY_NAME", "YourCompany"),

        smtp_host=_get("SMTP_HOST"),
        smtp_port=int(_get("SMTP_PORT", "587")),
        smtp_user=_get("SMTP_USER"),
        smtp_pass=_get("SMTP_PASS"),
        smtp_from=_get("SMTP_FROM", "noreply@example.com"),

        scan_interval_minutes=int(_get("SCAN_INTERVAL_MINUTES", "15")),
    )

"""Central configuration, loaded from environment / .env.

Nothing here is secret on its own — secrets live in .env (git-ignored) and are
read into these module-level constants at import time.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# --- Identity ---------------------------------------------------------------
# Two-user app per the spec.
USERS: list[str] = ["Davin", "Marilyn"]

# --- Secrets / keys ---------------------------------------------------------
FERNET_KEY: str = os.getenv("FERNET_KEY", "").strip()
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5").strip()
# Web-search fallback uses a search-capable model + the server web_search tool.
ANTHROPIC_SEARCH_MODEL: str = os.getenv("ANTHROPIC_SEARCH_MODEL", "claude-sonnet-4-6").strip()
WEB_SEARCH_ENABLED: bool = os.getenv("WEB_SEARCH_ENABLED", "false").lower() == "true"
WEB_SEARCH_MAX_CARDS: int = int(os.getenv("WEB_SEARCH_MAX_CARDS", "8"))
WEB_SEARCH_BATCH_SIZE: int = int(os.getenv("WEB_SEARCH_BATCH_SIZE", "8"))
WEB_SEARCH_MAX_USES_PER_BATCH: int = int(os.getenv("WEB_SEARCH_MAX_USES_PER_BATCH", "8"))
WEB_SEARCH_COOLDOWN_DAYS: int = int(os.getenv("WEB_SEARCH_COOLDOWN_DAYS", "30"))

# --- Storage ----------------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/churn.db")

# --- Ingestion tuning -------------------------------------------------------
OFFER_DELTA_THRESHOLD: float = float(os.getenv("OFFER_DELTA_THRESHOLD", "0.15"))
AUTO_COMMIT_SMALL_CHANGES: bool = (
    os.getenv("AUTO_COMMIT_SMALL_CHANGES", "false").lower() == "true"
)

# --- Scoring thresholds (configurable per §6) -------------------------------
APPLY_NOW_THRESHOLD: int = int(os.getenv("APPLY_NOW_THRESHOLD", "80"))
WATCH_THRESHOLD: int = int(os.getenv("WATCH_THRESHOLD", "75"))
WAIT_THRESHOLD: int = int(os.getenv("WAIT_THRESHOLD", "50"))
MIN_APPLY_VALUE: float = float(os.getenv("MIN_APPLY_VALUE", "600"))
MIN_APPLY_POINTS: int = int(os.getenv("MIN_APPLY_POINTS", "50000"))
MIN_WATCH_VALUE: float = float(os.getenv("MIN_WATCH_VALUE", "300"))

# --- Data sources (PUBLIC trackers; configurable per §9) --------------------
# Reputable offer / rule trackers used as starting points for the ingestion
# engine. These are hints for the LLM + fetcher; all values are still scraped
# and carry provenance. Editable in the Card Universe tab / here.
DEFAULT_SOURCES: list[str] = [
    "https://www.doctorofcredit.com/best-current-credit-card-sign-bonuses/",
    "https://frequentmiler.com/best-credit-card-sign-up-bonuses/",
    "https://www.uscreditcardguide.com/",
    "https://thepointsguy.com/credit-cards/best/",
]

# Reputability scope for discovery (configurable per §4.2).
DISCOVERY_ISSUERS: list[str] = [
    "Chase", "American Express", "Citi", "Capital One", "Bank of America",
    "Wells Fargo", "U.S. Bank", "Barclays",
    "United", "Delta", "American Airlines", "Alaska Airlines", "Southwest",
    "JetBlue", "Hawaiian Airlines",
    "Marriott", "Hilton", "Hyatt", "IHG", "Wyndham", "Choice",
]


def llm_available() -> bool:
    return bool(ANTHROPIC_API_KEY)


def crypto_available() -> bool:
    return bool(FERNET_KEY)

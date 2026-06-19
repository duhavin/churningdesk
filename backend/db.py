"""SQLite engine + session management."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


_ensure_sqlite_dir(config.DATABASE_URL)

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if config.DATABASE_URL.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Additive columns introduced after the initial schema. SQLite's create_all does
# not ALTER existing tables, so we add any missing ones on startup.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "card_product": {
        "product_family": "VARCHAR(120)",
        "referral_bonus_points": "INTEGER",
        "referral_bonus_override": "INTEGER",
        "referral_bonus_cash": "FLOAT",
        "current_offer_override": "INTEGER",
        "targeted_peak_offer_points": "INTEGER",
        "targeted_peak_offer_cash": "FLOAT",
        "targeted_peak_offer_source": "TEXT",
        "targeted_peak_offer_date": "VARCHAR(40)",
        "best_category_uses": "JSON",
        "card_benefits": "JSON",
        "downgrade_paths": "JSON",
        "updated_at": "DATETIME",
        "last_web_search_at": "DATETIME",
    },
    "source_config": {
        "product_id": "INTEGER",
    },
    "held_card": {
        "min_spend_requirement": "FLOAT",
        "min_spend_deadline": "DATE",
        "min_spend_progress": "FLOAT",
        "min_spend_completed": "BOOLEAN",
        "updated_at": "DATETIME",
    },
    "manual_targeted_offer": {
        "expires_at": "DATE",
    },
}

_ADDED_UNIQUE_INDEXES: dict[str, str] = {
    "uq_card_product_identity_idx": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_card_product_identity_idx "
        "ON card_product (issuer, product_name)"
    ),
    "uq_watchlist_identity_idx": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_identity_idx "
        "ON card_watchlist (issuer, product_name)"
    ),
    "uq_blacklist_identity_idx": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_blacklist_identity_idx "
        "ON card_blacklist (issuer, product_name)"
    ),
    "uq_targeted_offer_idx": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_targeted_offer_idx "
        "ON manual_targeted_offer (user, issuer, product_name)"
    ),
    "uq_source_config_url_idx": (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_source_config_url_idx "
        "ON source_config (url)"
    ),
}


def _run_additive_migrations() -> None:
    """Add columns introduced after a table was first created (idempotent)."""
    from .product_identity import derive_product_family

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all just made it with the full set
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl_type}'))
        for ddl in _ADDED_UNIQUE_INDEXES.values():
            try:
                conn.execute(text(ddl))
            except (IntegrityError, OperationalError):
                # Existing duplicate rows are user data; leave them untouched and
                # keep startup non-destructive.
                continue
        if "card_product" in existing_tables:
            rows = conn.execute(
                text(
                    "SELECT id, issuer, product_name, product_family FROM card_product "
                    "WHERE product_family IS NULL OR trim(product_family) = '' "
                    "OR product_family IN ("
                    "'chase_sapphire', 'chase_sapphire_business', "
                    "'capital_one_venture', 'capital_one_venture_business', "
                    "'capital_one_savor', 'capital_one_savor_business'"
                    ")"
                )
            ).mappings()
            for row in rows:
                family = derive_product_family(row["issuer"], row["product_name"])
                if family:
                    conn.execute(
                        text("UPDATE card_product SET product_family = :family WHERE id = :id"),
                        {"family": family, "id": row["id"]},
                    )


def init_db() -> None:
    """Create all tables. Imported models register themselves on Base."""
    from . import models  # noqa: F401  (ensures models are registered)

    Base.metadata.create_all(bind=engine)
    _run_additive_migrations()

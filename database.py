import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "agenda.sqlite3")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS google_accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            access_token  TEXT    NOT NULL,
            refresh_token TEXT    NOT NULL,
            expires_at    INTEGER NOT NULL,
            auth_error    TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS selected_calendars (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id    INTEGER NOT NULL REFERENCES google_accounts(id) ON DELETE CASCADE,
            calendar_id   TEXT    NOT NULL,
            calendar_name TEXT    NOT NULL,
            enabled       INTEGER NOT NULL DEFAULT 1,
            UNIQUE(account_id, calendar_id)
        );

        CREATE TABLE IF NOT EXISTS webcal_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            fetch_error TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS event_cache (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key  TEXT    NOT NULL UNIQUE,
            payload    TEXT    NOT NULL,
            cached_at  INTEGER NOT NULL
        );
    """)

    # Migration for databases created before the auth_error column existed.
    cols = [r[1] for r in db.execute("PRAGMA table_info(google_accounts)")]
    if "auth_error" not in cols:
        db.execute("ALTER TABLE google_accounts ADD COLUMN auth_error TEXT NOT NULL DEFAULT ''")

    db.commit()
    db.close()

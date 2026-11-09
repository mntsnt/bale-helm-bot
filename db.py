"""
Database module for Bale Helm Telegram Bot using SQLite (aiosqlite).
Contains all database schema definitions, queries, and persistence logic.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import aiosqlite

from config import DB_PATH, logger


def utc_now_iso() -> str:
    """Returns current UTC time in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def init_db() -> None:
    """Initializes SQLite tables and indexes."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")

        # Campaigns table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                theme TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );
            """
        )

        # Unique partial index to enforce only one active campaign at a time
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_campaign
            ON campaigns(is_active) WHERE is_active = 1;
            """
        )

        # Submissions table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                credit_name TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT NOT NULL,
                is_document INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            """
        )

        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submissions_campaign_user
            ON submissions(campaign_id, user_id);
            """
        )

        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submissions_file_unique
            ON submissions(campaign_id, file_unique_id);
            """
        )

        # Users table (stores preferred credit name for users without a username)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                credit_name TEXT NOT NULL
            );
            """
        )

        await db.commit()
    logger.info("Database initialized successfully at %s", DB_PATH)


async def get_active_campaign() -> Optional[Dict[str, Any]]:
    """Returns the currently active campaign or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, theme, is_active, started_at, ended_at FROM campaigns WHERE is_active = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def get_campaign_by_id(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a campaign by its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, theme, is_active, started_at, ended_at FROM campaigns WHERE id = ? LIMIT 1",
            (campaign_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def create_campaign(theme: str) -> Optional[Dict[str, Any]]:
    """
    Creates a new campaign.
    Returns the new campaign dictionary, or None if a campaign is already active.
    """
    active = await get_active_campaign()
    if active:
        return None

    started_at = utc_now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT INTO campaigns (theme, is_active, started_at) VALUES (?, 1, ?)",
            (theme.strip(), started_at),
        )
        campaign_id = cursor.lastrowid
        await db.commit()

        async with db.execute(
            "SELECT id, theme, is_active, started_at, ended_at FROM campaigns WHERE id = ?",
            (campaign_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    return None


async def close_active_campaign() -> Optional[Dict[str, Any]]:
    """
    Closes the currently active campaign.
    Returns the closed campaign dictionary with photo and contributor counts, or None.
    """
    active = await get_active_campaign()
    if not active:
        return None

    ended_at = utc_now_iso()
    campaign_id = active["id"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE campaigns SET is_active = 0, ended_at = ? WHERE id = ?",
            (ended_at, campaign_id),
        )
        await db.commit()

    stats = await get_campaign_stats(campaign_id)
    active["is_active"] = 0
    active["ended_at"] = ended_at
    active["photos_count"] = stats["photos_count"]
    active["contributors_count"] = stats["contributors_count"]
    return active


async def get_campaign_stats(campaign_id: int) -> Dict[str, int]:
    """Returns total photo count and unique contributor count for a campaign."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(id) AS photo_count, COUNT(DISTINCT user_id) AS contributor_count
            FROM submissions
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "photos_count": row[0] or 0,
                    "contributors_count": row[1] or 0,
                }
    return {"photos_count": 0, "contributors_count": 0}


async def get_campaign_credits(campaign_id: int) -> List[str]:
    """
    Returns a list of unique contributor credit strings for a campaign.
    Format: '@username' if username exists, else 'credit_name'.
    One unique entry per contributor.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT user_id, username, credit_name, MIN(id) AS first_sub
            FROM submissions
            WHERE campaign_id = ?
            GROUP BY user_id
            ORDER BY first_sub ASC
            """,
            (campaign_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            credits_list: List[str] = []
            seen: set = set()
            for row in rows:
                username = row["username"]
                credit_name = row["credit_name"]
                if username:
                    tag = f"@{username.lstrip('@')}"
                else:
                    tag = credit_name.strip()

                if tag and tag not in seen:
                    seen.add(tag)
                    credits_list.append(tag)

            return credits_list


async def get_campaign_submissions(campaign_id: int) -> List[Dict[str, Any]]:
    """Returns all submission records for a campaign (used for export)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, campaign_id, user_id, username, credit_name,
                   file_id, file_unique_id, is_document, created_at
            FROM submissions
            WHERE campaign_id = ?
            ORDER BY id ASC
            """,
            (campaign_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_user_submission_count(campaign_id: int, user_id: int) -> int:
    """Returns the number of photos submitted by a user for a campaign."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM submissions WHERE campaign_id = ? AND user_id = ?",
            (campaign_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def is_file_unique_id_submitted(campaign_id: int, file_unique_id: str) -> bool:
    """Checks whether a file_unique_id was already submitted for a campaign."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM submissions WHERE campaign_id = ? AND file_unique_id = ? LIMIT 1",
            (campaign_id, file_unique_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def add_submission(
    campaign_id: int,
    user_id: int,
    username: Optional[str],
    credit_name: str,
    file_id: str,
    file_unique_id: str,
    is_document: bool,
) -> int:
    """
    Records a photo submission in the database.
    Returns the submission ID.
    """
    created_at = utc_now_iso()
    clean_username = username.lstrip("@") if username else None

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO submissions (
                campaign_id, user_id, username, credit_name,
                file_id, file_unique_id, is_document, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                user_id,
                clean_username,
                credit_name.strip(),
                file_id,
                file_unique_id,
                1 if is_document else 0,
                created_at,
            ),
        )
        submission_id = cursor.lastrowid
        await db.commit()
        return submission_id


async def get_user_credit_name(user_id: int) -> Optional[str]:
    """Retrieves saved credit name for users without a username."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT credit_name FROM users WHERE user_id = ? LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_user_credit_name(user_id: int, credit_name: str) -> None:
    """Stores or updates a user's credit name."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, credit_name)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET credit_name = excluded.credit_name
            """,
            (user_id, credit_name.strip()),
        )
        await db.commit()

"""Подключение к базе данных SQLite (sync + async)."""

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import bindparam, create_engine, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = "sqlite:///./ai_chat.db"
ASYNC_SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./ai_chat.db"

# Sync engine for migrations/background tasks
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Async engine for request handlers
async_engine = create_async_engine(
    ASYNC_SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_messages_chat_ts ON messages (chat_id, timestamp, id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_character_id ON messages (character_id)",
    "CREATE INDEX IF NOT EXISTS ix_characters_chat_order ON characters (chat_id, order_index)",
    "CREATE INDEX IF NOT EXISTS ix_memories_char_created ON memories (character_id, importance DESC, created_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS ix_summaries_chat_character ON character_summaries (chat_id, character_id)",
    "CREATE INDEX IF NOT EXISTS ix_presence_character_message ON message_presence (character_id, message_id)",
    "CREATE INDEX IF NOT EXISTS ix_memories_char_imp ON memories (character_id, importance DESC, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_scene_states_chat_id ON scene_states (chat_id)",
]


def _normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().lower().split())


def memory_content_hash(content: str) -> str:
    return hashlib.sha256(_normalize_memory_content(content).encode()).hexdigest()


# Включаем поддержку внешних ключей в SQLite (нужно для ON DELETE CASCADE)
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


@event.listens_for(async_engine.sync_engine, "connect")
def _set_sqlite_pragma_async(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""

    pass


def _backfill_memory_hashes(conn) -> None:
    """Fill content_hash for existing rows and remove duplicates."""
    rows = conn.execute(
        text("SELECT id, character_id, content FROM memories WHERE content_hash = '' OR content_hash IS NULL")
    ).fetchall()

    seen: set[tuple[int, str]] = set()
    duplicate_ids: list[int] = []

    for row_id, character_id, content in rows:
        content_hash = memory_content_hash(content)
        key = (character_id, content_hash)
        if key in seen:
            duplicate_ids.append(row_id)
        else:
            seen.add(key)
            conn.execute(
                text("UPDATE memories SET content_hash = :hash WHERE id = :id"),
                {"hash": content_hash, "id": row_id},
            )

    if duplicate_ids:
        delete_stmt = text("DELETE FROM memories WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        conn.execute(delete_stmt, {"ids": duplicate_ids})
        logger.info("Removed %d duplicate memories during backfill", len(duplicate_ids))


def ensure_schema(db_engine) -> None:
    """Apply indexes and migrate existing databases (create_all skips alterations)."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS character_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    content TEXT NOT NULL DEFAULT '',
                    through_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_summary_character UNIQUE (character_id)
                )
                """
            )
        )

        inspector = inspect(db_engine)

        # Add missing columns FIRST (memories before any index using them)
        memories_columns = {col["name"] for col in inspector.get_columns("memories")}
        if "content_hash" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            )
            logger.info("Added content_hash column to memories")

        if "importance" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN importance REAL DEFAULT 0.5")
            )
            logger.info("Added importance column to memories for BM25 relevance (P1)")

        if "category" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN category TEXT")
            )
            logger.info("Added category column to memories for BM25 relevance (P1)")

        if "last_accessed_at" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN last_accessed_at DATETIME")
            )
            logger.info("Added last_accessed_at column to memories for consolidation (P3)")

        if "source_message_ids" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN source_message_ids TEXT NOT NULL DEFAULT '[]'")
            )
            logger.info("Added source_message_ids column to memories for consolidation (P3)")

        if "embedding" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN embedding BLOB")
            )
            logger.info("Added embedding column to memories for vector search (P3)")

        _backfill_memory_hashes(conn)

        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_char_hash "
                "ON memories (character_id, content_hash)"
            )
        )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memories_char_imp_created "
                "ON memories (character_id, importance DESC, created_at DESC)"
            )
        )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memories_char_last_accessed "
                "ON memories (character_id, last_accessed_at)"
            )
        )

        character_columns = {
            col["name"] for col in inspector.get_columns("characters")
        }
        new_character_columns = [
            ("speech_style", "TEXT NOT NULL DEFAULT ''"),
            ("example_messages", "TEXT NOT NULL DEFAULT ''"),
            ("boundaries", "TEXT NOT NULL DEFAULT ''"),
            ("background", "TEXT NOT NULL DEFAULT ''"),
            ("relationships", "TEXT NOT NULL DEFAULT ''"),
            ("appearance", "TEXT NOT NULL DEFAULT ''"),
            ("avatar_url", "TEXT NOT NULL DEFAULT ''"),
            ("avatar_crop", "TEXT NOT NULL DEFAULT ''"),
            ("temperature", "REAL"),
            ("location", "TEXT NOT NULL DEFAULT ''"),
        ]
        for column_name, column_type in new_character_columns:
            if column_name not in character_columns:
                conn.execute(
                    text(
                        f"ALTER TABLE characters ADD COLUMN {column_name} {column_type}"
                    )
                )
                logger.info("Added %s column to characters", column_name)

        if "is_player" not in character_columns:
            conn.execute(
                text("ALTER TABLE characters ADD COLUMN is_player INTEGER NOT NULL DEFAULT 0")
            )
            logger.info("Added is_player column to characters")

        # Per-chat thinking / instant mode + player location
        chat_columns = {col["name"] for col in inspector.get_columns("chats")}
        if "thinking_mode" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN thinking_mode BOOLEAN NOT NULL DEFAULT 1"
                )
            )
            logger.info("Added thinking_mode column to chats")
        if "player_location" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN player_location TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("Added player_location column to chats")
        if "locations" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN locations TEXT NOT NULL DEFAULT '[]'"
                )
            )
            logger.info("Added locations column to chats")

        # Message event metadata (visibility / location / targets)
        message_columns = {col["name"] for col in inspector.get_columns("messages")}
        if "visibility" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN visibility TEXT NOT NULL DEFAULT 'local'"
                )
            )
            logger.info("Added visibility column to messages")
        if "location" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN location TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("Added location column to messages")
        if "target_character_ids" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN target_character_ids TEXT NOT NULL DEFAULT '[]'"
                )
            )
            logger.info("Added target_character_ids column to messages")

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS message_presence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    presence TEXT NOT NULL,
                    CONSTRAINT uq_presence_message_character UNIQUE (message_id, character_id)
                )
                """
            )
        )

        # Create scene_states table if not exists (P3 Scene Tracking) - BEFORE indexes
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS scene_states (
                    chat_id INTEGER PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
                    location TEXT DEFAULT '',
                    time_of_day TEXT DEFAULT '',
                    present_character_ids TEXT DEFAULT '[]',
                    character_locations TEXT DEFAULT '{}',
                    custom_state TEXT DEFAULT '{}',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        # Migrate scene_states: add character_locations column if missing
        scene_columns = {col["name"] for col in inspector.get_columns("scene_states")}
        if "character_locations" not in scene_columns:
            conn.execute(
                text(
                    "ALTER TABLE scene_states ADD COLUMN character_locations TEXT NOT NULL DEFAULT '{}'"
                )
            )
            logger.info("Added character_locations column to scene_states")

        # Migrate messages: add channel column if missing
        message_columns = {col["name"] for col in inspector.get_columns("messages")}
        if "channel" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL DEFAULT 'direct'"
                )
            )
            logger.info("Added channel column to messages")

        # ----- Locations table (Локации 2.0) -----
        # Источник истины для CRUD и описаний локаций; `chats.locations`
        # остаётся кэшем названий для движка.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_location_chat_name UNIQUE (chat_id, name)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_locations_chat_id "
                "ON locations (chat_id)"
            )
        )

        # Backfill: из существующих chats.locations (JSON-массив названий)
        # создаём строки locations (description = ""). Идемпотентно —
        # дубликаты игнорируются уникальным ограничением (chat_id, name).
        chat_rows = conn.execute(
            text("SELECT id, locations FROM chats")
        ).fetchall()
        backfilled_locations = 0
        for chat_id, locations_json in chat_rows:
            try:
                loc_list = (
                    json.loads(locations_json)
                    if locations_json and locations_json != "[]"
                    else []
                )
            except (json.JSONDecodeError, TypeError):
                loc_list = []
            if not isinstance(loc_list, list):
                continue
            for loc_name in loc_list:
                if not isinstance(loc_name, str) or not loc_name.strip():
                    continue
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO locations "
                        "(chat_id, name, description, created_at, updated_at) "
                        "VALUES (:cid, :name, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"cid": chat_id, "name": loc_name.strip()},
                )
                backfilled_locations += 1
        if backfilled_locations:
            logger.info(
                "Backfilled %d locations from chats.locations", backfilled_locations
            )

        # ----- Relationship tables -----
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS character_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    source_character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    target_character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    relationship_type TEXT NOT NULL DEFAULT 'нейтральное',
                    affection INTEGER NOT NULL DEFAULT 50,
                    trust INTEGER NOT NULL DEFAULT 50,
                    attraction INTEGER NOT NULL DEFAULT 0,
                    resentment INTEGER NOT NULL DEFAULT 0,
                    jealousy INTEGER NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT '',
                    initial_description TEXT NOT NULL DEFAULT '',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_relationship_pair UNIQUE (source_character_id, target_character_id)
                )
                """
            )
        )

        rel_columns = {col["name"] for col in inspector.get_columns("character_relationships")}

        # Backfill: copy existing character.relationships text into records
        char_rows = conn.execute(
            text("SELECT id, chat_id, relationships FROM characters WHERE relationships != ''")
        ).fetchall()
        existing_pairs = {
            row for row in
            conn.execute(
                text("SELECT source_character_id, target_character_id FROM character_relationships")
            ).fetchall()
        }
        for char_id, chat_id, rel_text in char_rows:
            # Find all other characters in this chat as targets
            other_ids = conn.execute(
                text("SELECT id FROM characters WHERE chat_id = :cid AND id != :cid2"),
                {"cid": chat_id, "cid2": char_id},
            ).fetchall()
            for (target_id,) in other_ids:
                if (char_id, target_id) in existing_pairs:
                    continue
                conn.execute(
                    text(
                        """INSERT INTO character_relationships
                           (chat_id, source_character_id, target_character_id,
                            relationship_type, description, initial_description)
                           VALUES (:cid, :src, :tgt, 'нейтральное', '', :desc)"""
                    ),
                    {"cid": chat_id, "src": char_id, "tgt": target_id, "desc": rel_text},
                )
                existing_pairs.add((char_id, target_id))
        logger.info("Backfilled %d existing character relationships", len(char_rows))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS relationship_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relationship_id INTEGER NOT NULL REFERENCES character_relationships(id) ON DELETE CASCADE,
                    description TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    delta_affection INTEGER NOT NULL DEFAULT 0,
                    delta_trust INTEGER NOT NULL DEFAULT 0,
                    delta_attraction INTEGER NOT NULL DEFAULT 0,
                    delta_resentment INTEGER NOT NULL DEFAULT 0,
                    delta_jealousy INTEGER NOT NULL DEFAULT 0,
                    importance INTEGER NOT NULL DEFAULT 5,
                    source_round_id TEXT,
                    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        # Migration: add new columns to relationship_events for Trajectory (docs/relations.md §11, §17)
        if inspector.has_table("relationship_events"):
            rel_event_columns = {col["name"] for col in inspector.get_columns("relationship_events")}
            new_rel_event_columns = [
                ("kind", "TEXT NOT NULL DEFAULT 'llm'"),
                ("affection_after", "INTEGER NOT NULL DEFAULT 0"),
                ("trust_after", "INTEGER NOT NULL DEFAULT 0"),
                ("attraction_after", "INTEGER NOT NULL DEFAULT 0"),
                ("resentment_after", "INTEGER NOT NULL DEFAULT 0"),
                ("jealousy_after", "INTEGER NOT NULL DEFAULT 0"),
                ("source_message_ids", "TEXT NOT NULL DEFAULT '[]'"),
                ("round_id", "TEXT"),
            ]
            for column_name, column_type in new_rel_event_columns:
                if column_name not in rel_event_columns:
                    conn.execute(
                        text(f"ALTER TABLE relationship_events ADD COLUMN {column_name} {column_type}")
                    )
                    logger.info("Added %s column to relationship_events", column_name)

            # Add indexes for new columns
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rel_events_kind "
                    "ON relationship_events (kind)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rel_events_round "
                    "ON relationship_events (round_id)"
                )
            )

        # Backfill: create player characters for existing chats
        existing_chat_ids = conn.execute(text("SELECT id FROM chats")).fetchall()
        for (chat_id,) in existing_chat_ids:
            player_exists = conn.execute(
                text("SELECT 1 FROM characters WHERE chat_id = :cid AND is_player = 1"),
                {"cid": chat_id},
            ).fetchone()
            if not player_exists:
                conn.execute(
                    text(
                        "INSERT INTO characters (chat_id, name, personality, traits, is_player, order_index, created_at) "
                        "VALUES (:cid, 'Игрок', '', '', 1, 9999, CURRENT_TIMESTAMP)"
                    ),
                    {"cid": chat_id},
                )
                player_id = conn.execute(
                    text("SELECT id FROM characters WHERE chat_id = :cid AND is_player = 1"),
                    {"cid": chat_id},
                ).scalar()
                # Create NPC->Player and Player->NPC relationships for all NPCs
                npc_ids = conn.execute(
                    text("SELECT id FROM characters WHERE chat_id = :cid AND is_player = 0"),
                    {"cid": chat_id},
                ).fetchall()
                for (npc_id,) in npc_ids:
                    # NPC -> Player (Player -> NPC is intentionally not tracked)
                    pair = (npc_id, player_id)
                    if pair not in existing_pairs:
                        conn.execute(
                            text(
                                "INSERT OR IGNORE INTO character_relationships "
                                "(chat_id, source_character_id, target_character_id, "
                                "relationship_type, description, initial_description) "
                                "VALUES (:cid, :src, :tgt, 'нейтральное', '', '')"
                            ),
                            {"cid": chat_id, "src": npc_id, "tgt": player_id},
                        )
                logger.info("Created player character + relationships for chat_id=%d", chat_id)

        # Data migration: drop legacy Player -> NPC relationship rows.
        # Only NPC -> NPC and NPC -> Player relationships are tracked.
        if inspector.has_table("character_relationships"):
            deleted_plr = conn.execute(
                text(
                    "DELETE FROM character_relationships "
                    "WHERE source_character_id IN "
                    "(SELECT id FROM characters WHERE is_player = 1)"
                )
            ).rowcount
            if deleted_plr:
                logger.info("Removed %d Player -> NPC relationship rows", deleted_plr)

        # Data migration: translate legacy English relationship types to Russian
        _RELATIONSHIP_TYPE_TRANSLATION = {
            "neutral": "нейтральное",
            "friend": "друг",
            "close_friend": "близкий_друг",
            "best_friend": "лучший_друг",
            "ally": "союзник",
            "trusted_ally": "верный_союзник",
            "rival": "соперник",
            "enemy": "враг",
            "bitter_enemy": "заклятый_враг",
            "crush": "симпатия",
            "romantic": "романтика",
            "lover": "возлюбленные",
            "mentor": "наставник",
            "student": "ученик",
            "family": "семья",
            "parent": "родитель",
            "sibling": "брат_сестра",
            "stranger": "незнакомец",
            "acquaintance": "знакомый",
        }
        if inspector.has_table("character_relationships"):
            for _old_type, _new_type in _RELATIONSHIP_TYPE_TRANSLATION.items():
                conn.execute(
                    text(
                        "UPDATE character_relationships SET relationship_type = :new "
                        "WHERE relationship_type = :old"
                    ),
                    {"new": _new_type, "old": _old_type},
                )

        # Data migration: translate legacy English memory categories to Russian
        _MEMORY_CATEGORY_TRANSLATION = {
            "relationship": "отношения",
            "event": "событие",
            "location": "локация",
            "item": "предмет",
            "other": "другое",
        }
        if inspector.has_table("memories"):
            for _old_cat, _new_cat in _MEMORY_CATEGORY_TRANSLATION.items():
                conn.execute(
                    text(
                        "UPDATE memories SET category = :new "
                        "WHERE category = :old"
                    ),
                    {"new": _new_cat, "old": _old_cat},
                )

        # ----- Open Issues table (docs/relations.md §7.1) -----
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS relationship_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relationship_id INTEGER NOT NULL REFERENCES character_relationships(id) ON DELETE CASCADE,
                    issue_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 5,
                    state TEXT NOT NULL DEFAULT 'open',
                    created_round_id TEXT,
                    resolved_round_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME,
                    last_mention_round_id TEXT,
                    rounds_since_last_mention INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )

        # Migration: salience counter column (docs/relations.md §7.4, Sprint 1 item 7)
        if inspector.has_table("relationship_issues"):
            issue_columns = {col["name"] for col in inspector.get_columns("relationship_issues")}
            if "rounds_since_last_mention" not in issue_columns:
                conn.execute(
                    text(
                        "ALTER TABLE relationship_issues ADD COLUMN "
                        "rounds_since_last_mention INTEGER NOT NULL DEFAULT 0"
                    )
                )
                logger.info(
                    "Added rounds_since_last_mention column to relationship_issues"
                )
            # Migration: source attribution for issues (Sprint 3 item 18)
            if "source_message_ids" not in issue_columns:
                conn.execute(
                    text(
                        "ALTER TABLE relationship_issues ADD COLUMN "
                        "source_message_ids TEXT NOT NULL DEFAULT '[]'"
                    )
                )
                logger.info(
                    "Added source_message_ids column to relationship_issues"
                )

        # Indexes AFTER all column migrations
        for ddl in INDEXES:
            conn.execute(text(ddl))

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rel_issues_rel_state "
                "ON relationship_issues (relationship_id, state)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_rel_issues_state "
                "ON relationship_issues (state)"
            )
        )


async def init_db() -> None:
    """Initialize database: create tables and run migrations."""
    # Run sync migrations first (uses sync engine directly)
    ensure_schema(engine)
    # Then create tables via async engine
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields async DB session and closes after request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_db():
    """Sync DB dependency (for background tasks if needed)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """Return the sync session factory for testing patching."""
    return SessionLocal


def get_async_session_factory():
    """Return the async session factory for testing patching."""
    return AsyncSessionLocal

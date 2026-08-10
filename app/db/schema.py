"""DDL-схема и скрытая «миграция» SQLite (Sprint 3, decomposition-sprints.md §4).

Вся DDL из ``ensure_schema`` перенесена из ``app/database.py`` сюда **без
изменений SQL** — идемпотентность сохраняется. Engine, pragma, ``init_db`` и
сессии живут в ``db/engine.py``; ``app/database.py`` остаётся тонким
реэкспорт-фасадом.
"""

import hashlib
import json
import logging

from sqlalchemy import bindparam, inspect, text

logger = logging.getLogger(__name__)

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

        # Inspector на ТОМ ЖЕ соединении (conn): иначе отдельное соединение не
        # видит таблицы/колонки, созданные в текущей незакоммиченной транзакции
        # (например, CREATE scene_states в ensure_schema) — миграция падает на
        # прод-БД с данными (см. tests/test_memory_types.py).
        inspector = inspect(conn)

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

        # Sprint 2 (Plans/update20.md §7): типы памяти + эмоциональная окраска +
        # проекция на каноническое `world_events`. Идемпотентные ALTER: для
        # существующих строк memory_type получает default 'semantic' (риск из
        # плана — миграция без дата-потерь); event_id/valence/intensity nullable.
        if "memory_type" not in memories_columns:
            conn.execute(
                text(
                    "ALTER TABLE memories ADD COLUMN memory_type "
                    "TEXT NOT NULL DEFAULT 'semantic'"
                )
            )
            logger.info(
                "Added memory_type column to memories (default 'semantic', Sprint 2)"
            )
        if "event_id" not in memories_columns:
            conn.execute(
                text(
                    "ALTER TABLE memories ADD COLUMN event_id "
                    "INTEGER REFERENCES world_events(id) ON DELETE SET NULL"
                )
            )
            logger.info("Added event_id column to memories (Sprint 2)")
        if "valence" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN valence REAL")
            )
            logger.info("Added valence column to memories (Sprint 2)")
        if "intensity" not in memories_columns:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN intensity REAL")
            )
            logger.info("Added intensity column to memories (Sprint 2)")

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

        # Sprint 2: индексы по типу и по проекции на world_events (§E, п.3).
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memories_char_type "
                "ON memories (character_id, memory_type)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memories_event "
                "ON memories (event_id)"
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
            # WPE 3.0 (Фаза 0): каноническая локация, nullable до backfill (Фаза 1)
            ("location_id", "INTEGER REFERENCES locations(id) ON DELETE SET NULL"),
            # Ручное включение/выключение NPC в автоматической генерации:
            # default 1 (true) — существующие и новые персонажи активны.
            ("is_active", "INTEGER NOT NULL DEFAULT 1"),
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

        # Story separation (Plans/update20.md §16.1, Sprint 0): вынос story из
        # general_prompt. Начальные значения = general_prompt (copy, не move);
        # story_enabled=false. Backfill — `scripts/backfill_plot_fields.py`.
        if "original_plot" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN original_plot TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("Added original_plot column to chats")
        if "story_prompt" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN story_prompt TEXT NOT NULL DEFAULT ''"
                )
            )
            logger.info("Added story_prompt column to chats")
        if "story_enabled" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN story_enabled BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            logger.info("Added story_enabled column to chats")

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

        # Sprint 4 (Plans/update20.md §11): attention score пары (персонаж, событие).
        # Идемпотентный ALTER: существующие строки получают NULL (attention не
        # считался) — read-path при выключенном флаге не читает колонку.
        presence_columns = {col["name"] for col in inspector.get_columns("message_presence")}
        if "attention" not in presence_columns:
            conn.execute(
                text(
                    "ALTER TABLE message_presence ADD COLUMN attention REAL NULL"
                )
            )
            logger.info("Added attention column to message_presence")

        # ----- Addressable intervention (docs/intervention.md) -----
        # Получатели фиксируются в `intervention_recipients` при создании (PUT)
        # и не пересчитываются на генерации. `character_id` — legacy-маркер
        # области действия (NULL = chat-wide); источник истины — таблица
        # получателей. Один chat-wide на чат обеспечивает partial unique index.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS interventions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER REFERENCES characters(id) ON DELETE CASCADE,
                    instruction TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_intervention_chat_character UNIQUE (chat_id, character_id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_interventions_chat_id "
                "ON interventions (chat_id)"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_intervention_chat_wide "
                "ON interventions (chat_id) WHERE character_id IS NULL"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS intervention_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intervention_id INTEGER NOT NULL REFERENCES interventions(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    CONSTRAINT uq_intervention_recipient UNIQUE (intervention_id, character_id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_intervention_recipients_character_id "
                "ON intervention_recipients (character_id)"
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

        # Migrate messages: add stimuli column if missing (Sprint 2)
        if "stimuli" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN stimuli TEXT NOT NULL DEFAULT '[]'"
                )
            )
            logger.info("Added stimuli column to messages")

        # WPE 3.0: canonical location FK for messages (nullable — legacy rows
        # keep NULL and use the string fallback in perception). `location`
        # remains the legacy string snapshot; `location_id` is the identity.
        if "location_id" not in message_columns:
            conn.execute(
                text(
                    "ALTER TABLE messages ADD COLUMN location_id "
                    "INTEGER REFERENCES locations(id) ON DELETE SET NULL"
                )
            )
            logger.info("Added location_id column to messages")
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_messages_location_id "
                    "ON messages (location_id)"
                )
            )

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

        # Migrate locations: add adjacent_to column if missing (Sprint 2)
        location_columns = {col["name"] for col in inspector.get_columns("locations")}
        if "adjacent_to" not in location_columns:
            conn.execute(
                text(
                    "ALTER TABLE locations ADD COLUMN adjacent_to TEXT NOT NULL DEFAULT '[]'"
                )
            )
            logger.info("Added adjacent_to column to locations")

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

        # ----- WPE 3.0 (Фаза 0): WorldEvent / Thread / ThreadParticipantState -----
        # Заведены, НЕ пишутся до Фаз 3/6 (Plans/WPE.md §10). Откат тривиален:
        # новые таблицы не читаются ни одним read-path.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS world_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER REFERENCES characters(id) ON DELETE SET NULL,
                    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    location_from TEXT NOT NULL DEFAULT '',
                    location_to TEXT NOT NULL DEFAULT '',
                    round_id TEXT,
                    target_character_ids TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_world_events_chat_ts "
                "ON world_events (chat_id, created_at, id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_world_events_character_id "
                "ON world_events (character_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_world_events_round_id "
                "ON world_events (round_id)"
            )
        )

        # Sprint 0 (Plans/update20.md): каноническая локация события.
        # Backfill из строковой `world_events.location` — отдельным скриптом
        # (`scripts/backfill_event_location_ids.py`, аналог `characters.location_id`).
        world_event_columns = {
            col["name"] for col in inspector.get_columns("world_events")
        }
        if "location_id" not in world_event_columns:
            conn.execute(
                text(
                    "ALTER TABLE world_events ADD COLUMN location_id "
                    "INTEGER REFERENCES locations(id) ON DELETE SET NULL"
                )
            )
            logger.info("Added location_id column to world_events")
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_world_events_location_id "
                "ON world_events (location_id)"
            )
        )

        # Sprint 1 (Plans/update20.md §15): structured event metadata.
        # Идемпотентные ALTER — существующие БД (после Sprint 0) доезжают без дата-
        # потерь: новые колонки nullable / с default, read-path их не читает.
        if "action" not in world_event_columns:
            conn.execute(
                text(
                    "ALTER TABLE world_events ADD COLUMN action "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
            )
        if "importance" not in world_event_columns:
            conn.execute(
                text("ALTER TABLE world_events ADD COLUMN importance REAL")
            )
        if "story_salience" not in world_event_columns:
            conn.execute(
                text("ALTER TABLE world_events ADD COLUMN story_salience REAL")
            )
        if "emotional_salience" not in world_event_columns:
            conn.execute(
                text(
                    "ALTER TABLE world_events ADD COLUMN emotional_salience REAL"
                )
            )
        if (
            "action" not in world_event_columns
            or "importance" not in world_event_columns
            or "story_salience" not in world_event_columns
            or "emotional_salience" not in world_event_columns
        ):
            logger.info("Added Sprint 1 columns to world_events (action/importance/salience)")

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT 'messenger',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_threads_chat_id "
                "ON threads (chat_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS thread_participant_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    last_delivered_message_id INTEGER,
                    last_read_message_id INTEGER,
                    joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_thread_participant UNIQUE (thread_id, character_id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_thread_participant_character "
                "ON thread_participant_states (character_id)"
            )
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
                # Sprint 1 (Plans/update20.md §15.2): проекция на world_events.
                ("event_id", "INTEGER REFERENCES world_events(id) ON DELETE SET NULL"),
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
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rel_events_event_id "
                    "ON relationship_events (event_id)"
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

        # ----- State-driven tables (Plans/update20.md, Sprint 0) -----
        # Пустые таблицы как фундамент. read-path их НЕ читает, write-path НЕ
        # пишет до соответствующих спринтов (см. комментарии у моделей).
        # Откат тривиален: новые таблицы изолированы от существующего поведения.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS character_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    emotional_state TEXT NOT NULL DEFAULT '{}',
                    mood TEXT NOT NULL DEFAULT '',
                    stress REAL,
                    physical_state TEXT NOT NULL DEFAULT '{}',
                    attention TEXT,
                    current_focus_id INTEGER REFERENCES characters(id) ON DELETE SET NULL,
                    active_goal TEXT NOT NULL DEFAULT '',
                    personal_goals TEXT NOT NULL DEFAULT '[]',
                    updated_round_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_character_state_character UNIQUE (character_id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_character_states_chat_id "
                "ON character_states (chat_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_character_states_character "
                "ON character_states (character_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS beliefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'memory',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    type TEXT NOT NULL DEFAULT 'belief',
                    world_truth_ref INTEGER REFERENCES world_events(id) ON DELETE SET NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_beliefs_character "
                "ON beliefs (character_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_beliefs_chat_character "
                "ON beliefs (chat_id, character_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_beliefs_subject "
                "ON beliefs (subject)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_beliefs_world_truth_ref "
                "ON beliefs (world_truth_ref)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS story_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    original_plot TEXT NOT NULL DEFAULT '',
                    current_story TEXT NOT NULL DEFAULT '{}',
                    story_phase TEXT NOT NULL DEFAULT '',
                    updated_round_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    last_consolidation_rounds INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_story_states_chat_id "
                "ON story_states (chat_id)"
            )
        )

        # Sprint 9 (Story Consolidation §17): last_consolidation_rounds — число
        # раундов на момент последней консолидации (trigger §17.1).
        try:
            story_state_columns = {
                col["name"] for col in inspector.get_columns("story_states")
            }
        except Exception:  # noqa: BLE001 — таблица может отсутствовать на старых БД
            story_state_columns = set()
        if story_state_columns and "last_consolidation_rounds" not in story_state_columns:
            conn.execute(
                text(
                    "ALTER TABLE story_states "
                    "ADD COLUMN last_consolidation_rounds INTEGER"
                )
            )
            logger.info("Added last_consolidation_rounds column to story_states")

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS story_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    actors TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 5,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_round_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_story_threads_chat_status "
                "ON story_threads (chat_id, status)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS story_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER REFERENCES world_events(id) ON DELETE CASCADE,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    round_id TEXT,
                    event TEXT NOT NULL DEFAULT '',
                    actors TEXT NOT NULL DEFAULT '[]',
                    location TEXT NOT NULL DEFAULT '',
                    cause TEXT NOT NULL DEFAULT '',
                    consequences TEXT NOT NULL DEFAULT '',
                    importance INTEGER NOT NULL DEFAULT 5,
                    story_thread_id INTEGER REFERENCES story_threads(id) ON DELETE SET NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_story_events_chat_id "
                "ON story_events (chat_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_story_events_event_id "
                "ON story_events (event_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_story_events_thread "
                "ON story_events (story_thread_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS event_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    event_id INTEGER NOT NULL REFERENCES world_events(id) ON DELETE CASCADE,
                    caused_by_event_id INTEGER REFERENCES world_events(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'causes'
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_event_links_chat_id "
                "ON event_links (chat_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_event_links_event_id "
                "ON event_links (event_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_event_links_caused_by "
                "ON event_links (caused_by_event_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS memory_anchors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relationship_id INTEGER REFERENCES character_relationships(id) ON DELETE CASCADE,
                    event_id INTEGER REFERENCES world_events(id) ON DELETE CASCADE,
                    emotion TEXT NOT NULL DEFAULT '',
                    valence REAL NOT NULL DEFAULT 0.0,
                    intensity REAL NOT NULL DEFAULT 0.0,
                    importance REAL NOT NULL DEFAULT 0.5,
                    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_anchors_rel "
                "ON memory_anchors (relationship_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_anchors_event "
                "ON memory_anchors (event_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    goal TEXT NOT NULL DEFAULT '',
                    target INTEGER REFERENCES characters(id) ON DELETE SET NULL,
                    approach TEXT NOT NULL DEFAULT 'direct',
                    urgency REAL NOT NULL DEFAULT 0.0,
                    emotion TEXT NOT NULL DEFAULT '',
                    risk REAL NOT NULL DEFAULT 0.0,
                    created_round_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_intents_chat_character "
                "ON intents (chat_id, character_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_intents_round "
                "ON intents (created_round_id)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS npc_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                    goal TEXT NOT NULL,
                    next_step TEXT NOT NULL DEFAULT '',
                    blocked_by TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 5,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_round_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_npc_plans_chat_character "
                "ON npc_plans (chat_id, character_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_npc_plans_status "
                "ON npc_plans (status)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS consolidation_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    last_soft_at DATETIME,
                    last_hard_at DATETIME,
                    counters TEXT NOT NULL DEFAULT '{}',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_consolidation_state_chat "
                "ON consolidation_state (chat_id)"
            )
        )

        # ----- LoRA adapters (Plans/LoRA.md, Sprint 1) -----
        # Регистр адаптеров + связка «чат → адаптер» (§2.6). MVP (§2.5): ровно
        # один адаптер на чат — UNIQUE(chat_id); полей weight/order_index нет.
        # read-path (chat_engine) новые таблицы пока не читает — Sprint 2/3.
        # chats.lora_enabled: флаг включения LoRA (§2.4). Идемпотентный ALTER;
        # backfill для существующих чатов не нужен — DEFAULT 0 (false) на
        # колонке уже присваивает lora_enabled=false при ALTER.
        chat_columns = {col["name"] for col in inspector.get_columns("chats")}
        if "lora_enabled" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN lora_enabled "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            logger.info("Added lora_enabled column to chats (LoRA, Sprint 1)")
        # chats.base_model_identity: identity базовой модели для compatibility
        # check (§2.3), nullable. Идемпотентный ALTER; backfill не нужен (NULL =
        # низкодоверенная fallback на model_name).
        if "base_model_identity" not in chat_columns:
            conn.execute(
                text(
                    "ALTER TABLE chats ADD COLUMN base_model_identity "
                    "VARCHAR(512) NULL"
                )
            )
            logger.info(
                "Added base_model_identity column to chats (LoRA, Sprint 2)"
            )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS lora_adapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    format TEXT NOT NULL DEFAULT 'auto',
                    base_model TEXT NOT NULL DEFAULT '',
                    base_model_identity TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    sha256 TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_lora_adapters_enabled "
                "ON lora_adapters (enabled)"
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_lora_adapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    adapter_id INTEGER NOT NULL REFERENCES lora_adapters(id) ON DELETE CASCADE,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_chat_lora_chat UNIQUE (chat_id)
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_lora_adapter_id "
                "ON chat_lora_adapters (adapter_id)"
            )
        )

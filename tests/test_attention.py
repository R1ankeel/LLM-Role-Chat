"""Sprint 4 — Attention (Plans/update20.md §11).

Покрывает:
- `attention.compute_attention_score` — детерминированные сценарии из постановки:
  падение стакана в соседней комнате (audible, без стимулов/адресации) = LOW;
  крик персонажа по имени (shout + addressed + имя в тексте) = HIGH;
  своя речь = 1.0; якорь активирует компоненту w_emotional; novelty;
- `attention_bucket` — пороги и откат (None → HIGH, legacy-поведение);
- `attention_weights` — нормализация на 1.0;
- `apply_sensors_significance` — Sensors-подсказка только в рамках caps (§5.1.3);
- запись attention в `message_presence` через presence round pass (флаг on);
  откат (флаг off → NULL, get_attention_map пуст);
- memory filter: attention < LOW исключает событие из memory-контекста;
- recency tail: attention < LOW исключает событие из хвоста реакций.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from app import attention
from app import crud
from app import memory_service
from app import models
from app import schemas
from app import witness_model


def _event(content: str, *, role: str = "character", author: int = 2,
           targets: tuple = (), stimuli: list | None = None) -> dict:
    return {
        "content": content,
        "role": role,
        "character_id": author,
        "target_character_ids": list(targets),
        "stimuli": stimuli or [],
        "location": "room",
    }


def _observer(cid: int = 1, name: str = "Олег") -> dict:
    return {"character_id": cid, "name": name}


NAMES = {1: "Олег", 2: "Максим"}


# ---------------------------------------------------------------------------
# attention_bucket: пороги и откат
# ---------------------------------------------------------------------------

class TestAttentionBucket:
    def test_none_is_high_legacy_rollback(self):
        # Флаг off / нет данных → всё воспринятое ведёт себя как раньше.
        assert attention.attention_bucket(None) == attention.HIGH

    def test_low_medium_high_thresholds(self):
        assert attention.attention_bucket(0.2) == attention.LOW
        assert attention.attention_bucket(0.35) == attention.MEDIUM
        assert attention.attention_bucket(0.5) == attention.MEDIUM
        assert attention.attention_bucket(0.7) == attention.HIGH
        assert attention.attention_bucket(0.9) == attention.HIGH

    def test_weights_normalize_to_one(self):
        weights = attention.attention_weights()
        assert set(weights) == {
            "volume", "distance", "relevance", "personal",
            "emotional", "novelty", "relationship", "address",
        }
        assert sum(weights.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_attention_score: сценарии из постановки
# ---------------------------------------------------------------------------

class TestAttentionScore:
    def test_glass_fall_in_adjacent_room_is_low(self):
        """Падение стакана в соседней комнате — perception=yes, attention=low."""
        score = attention.compute_attention_score(
            presence="audible",
            event=_event("*в соседней комнате со звоном падает стакан*", author=2),
            observer=_observer(),
            character_names=NAMES,
        )
        assert attention.attention_bucket(score) == attention.LOW

    def test_shout_by_name_is_high(self):
        """Крик персонажа по имени — attention=very high."""
        score = attention.compute_attention_score(
            presence="present",
            event=_event(
                "Олег! Быстро сюда!",
                author=2,
                targets=(1,),
                stimuli=[{"type": "shout", "target_character": "Олег"}],
            ),
            observer=_observer(),
            character_names=NAMES,
        )
        assert score >= 0.7
        assert attention.attention_bucket(score) == attention.HIGH

    def test_own_speech_is_always_full_attention(self):
        score = attention.compute_attention_score(
            presence="present",
            event=_event("Моя собственная реплика.", author=1),
            observer=_observer(),
            character_names=NAMES,
        )
        assert score == 1.0

    def test_anchor_activates_emotional_component(self):
        base = attention.compute_attention_score(
            presence="present",
            event=_event("Олег! Быстро сюда!", author=2, targets=(1,)),
            observer=_observer(),
            character_names=NAMES,
            anchor_active=False,
        )
        with_anchor = attention.compute_attention_score(
            presence="present",
            event=_event("Олег! Быстро сюда!", author=2, targets=(1,)),
            observer=_observer(),
            character_names=NAMES,
            anchor_active=True,
        )
        # якорь добавляет ровно w_emotional к итоговому score
        assert with_anchor - base == pytest.approx(
            attention.attention_weights()["emotional"]
        )

    def test_novelty_affects_score(self):
        repeated = attention.compute_attention_score(
            presence="present",
            event=_event("Повторяющаяся новость.", author=2, targets=(1,)),
            observer=_observer(),
            character_names=NAMES,
            novelty=0.0,
        )
        fresh = attention.compute_attention_score(
            presence="present",
            event=_event("Повторяющаяся новость.", author=2, targets=(1,)),
            observer=_observer(),
            character_names=NAMES,
            novelty=1.0,
        )
        assert fresh - repeated == pytest.approx(
            attention.attention_weights()["novelty"]
        )

    def test_personal_salience_raises_score(self):
        """Упоминание имени наблюдателя в тексте → компонента w_personal."""
        base = attention.compute_attention_score(
            presence="present",
            event=_event("Что-то происходит.", author=2),
            observer=_observer(),
            character_names=NAMES,
        )
        mentioned = attention.compute_attention_score(
            presence="present",
            event=_event("Олег, что-то происходит.", author=2),
            observer=_observer(),
            character_names=NAMES,
        )
        assert mentioned - base == pytest.approx(
            attention.attention_weights()["personal"]
        )


# ---------------------------------------------------------------------------
# apply_sensors_significance (§5.1.3): подсказка только в рамках caps
# ---------------------------------------------------------------------------

class TestSensorsSignificance:
    def test_none_noop(self):
        assert attention.apply_sensors_significance(0.5, None) == pytest.approx(0.5)

    def test_raises_within_cap(self):
        out = attention.apply_sensors_significance(0.6, 1.0, cap=0.15)
        assert out == pytest.approx(0.75)

    def test_clamped_to_one(self):
        out = attention.apply_sensors_significance(0.95, 1.0, cap=0.15)
        assert out == pytest.approx(1.0)

    def test_zero_significance_noop(self):
        assert attention.apply_sensors_significance(0.5, 0.0, cap=0.15) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Запись attention в message_presence (presence round pass) + откат
# ---------------------------------------------------------------------------

@pytest.fixture
def enable_attention(monkeypatch):
    # settings — общий синглтон; патчим атрибуты для всех читающих модулей.
    monkeypatch.setattr("app.crud.settings.attention_enabled", True)
    monkeypatch.setattr("app.witness_model.settings.attention_enabled", True)
    monkeypatch.setattr("app.context_builder.settings.attention_enabled", True)
    monkeypatch.setattr("app.memory_service.settings.attention_enabled", True)
    monkeypatch.setattr("app.chat_engine.settings.attention_enabled", True)


@pytest.mark.asyncio
async def test_round_pass_writes_attention(
    enable_attention, db_session, chat, three_characters
):
    a, b, c = three_characters
    names = {x.id: x.name for x in three_characters}
    msgs = [
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id, role="user", content="Привет всем!", visibility="global"
            ),
        ),
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="character",
                character_id=a.id,
                content="Привет!",
                visibility="global",
            ),
        ),
    ]
    await crud.compute_and_save_presence_for_round(
        db_session,
        msgs,
        [x.id for x in three_characters],
        names,
        characters=list(three_characters),
    )

    attention_map = await crud.get_attention_map(
        db_session, [m.id for m in msgs], b.id
    )
    assert len(attention_map) == len(msgs)
    for value in attention_map.values():
        assert 0.0 <= value <= 1.0


@pytest.mark.asyncio
async def test_round_pass_attention_null_when_flag_off(
    db_session, chat, three_characters
):
    """Откат: флаг off по умолчанию → attention не считается (NULL, legacy)."""
    a, b, c = three_characters
    names = {x.id: x.name for x in three_characters}
    msgs = [
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id, role="user", content="Привет!", visibility="global"
            ),
        )
    ]
    await crud.compute_and_save_presence_for_round(
        db_session,
        msgs,
        [x.id for x in three_characters],
        names,
        characters=list(three_characters),
    )

    assert await crud.get_attention_map(db_session, [m.id for m in msgs], a.id) == {}

    rows = (
        await db_session.execute(
            __import__("sqlalchemy").select(models.MessagePresence).where(
                models.MessagePresence.character_id == a.id
            )
        )
    ).scalars().all()
    assert rows
    assert all(row.attention is None for row in rows)


@pytest.mark.asyncio
async def test_upsert_preserves_attention_when_record_none(
    db_session, chat, three_characters
):
    """Обновление строки без attention не затирает существующее значение."""
    a, b, c = three_characters
    msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id, role="user", content="Привет!", visibility="global"
        ),
    )
    await crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=msg.id, character_id=a.id, presence="present", attention=0.8
            )
        ],
    )
    await crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=msg.id, character_id=a.id, presence="present", attention=None
            )
        ],
    )
    row = (
        await db_session.execute(
            __import__("sqlalchemy").select(models.MessagePresence).where(
                models.MessagePresence.message_id == msg.id,
                models.MessagePresence.character_id == a.id,
            )
        )
    ).scalar_one()
    assert row.attention == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# memory filter: attention < LOW → не в память
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_filter_excludes_low_attention(
    enable_attention, db_session, chat, three_characters
):
    a, b, c = three_characters
    names = {x.id: x.name for x in three_characters}
    msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=a.id,
            content="Олег, подойди!",
            visibility="global",
        ),
    )
    await crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=msg.id, character_id=b.id, presence="present", attention=0.2
            )
        ],
    )
    presence_map = await crud.get_presence_map(db_session, [msg.id], b.id)
    attention_map = await crud.get_attention_map(db_session, [msg.id], b.id)

    ctx = memory_service.get_observable_context_for_character(
        [msg], b.id, names, presence_map, attention_map=attention_map
    )
    assert ctx.text == ""
    assert ctx.skipped and ctx.skipped[0]["reason"] == "low_attention_background"


@pytest.mark.asyncio
async def test_memory_filter_includes_high_attention(
    enable_attention, db_session, chat, three_characters
):
    a, b, c = three_characters
    names = {x.id: x.name for x in three_characters}
    msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=a.id,
            content="Олег, подойди!",
            visibility="global",
        ),
    )
    await crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=msg.id, character_id=b.id, presence="present", attention=0.9
            )
        ],
    )
    presence_map = await crud.get_presence_map(db_session, [msg.id], b.id)
    attention_map = await crud.get_attention_map(db_session, [msg.id], b.id)

    ctx = memory_service.get_observable_context_for_character(
        [msg], b.id, names, presence_map, attention_map=attention_map
    )
    assert ctx.text != ""
    assert "Олег" in ctx.text


# ---------------------------------------------------------------------------
# recency tail hook: attention < LOW → не в реакцию
# ---------------------------------------------------------------------------

def test_recency_tail_excludes_low_attention():
    addressed = SimpleNamespace(
        id=7,
        role="character",
        character_id=2,
        target_character_ids="[1]",
    )
    tail = witness_model.build_character_recency_tail(
        [addressed],
        1,
        {1: "Олег", 2: "Максим"},
        attention_map={7: 0.2},
    )
    assert tail == ""

    tail = witness_model.build_character_recency_tail(
        [addressed],
        1,
        {1: "Олег", 2: "Максим"},
        attention_map={7: 0.9},
    )
    assert "Отреагируй" in tail


def test_recency_tail_legacy_without_attention_map():
    """Без attention_map (флаг off) — legacy-поведение, события остаются."""
    addressed = SimpleNamespace(
        id=7,
        role="character",
        character_id=2,
        target_character_ids="[1]",
    )
    tail = witness_model.build_character_recency_tail(
        [addressed], 1, {1: "Олег", 2: "Максим"}, attention_map=None
    )
    assert "Отреагируй" in tail

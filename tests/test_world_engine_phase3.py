"""WPE 3.0 (Plans/WPE.md) Фаза 3 — WorldEvent dual-write + shadow Perception (2 канала).

Покрывает:
- атомарный dual-write: `WorldEvent` рядом с `Message` в одной транзакции
  (флаг `WORLD_ENGINE_EVENTS_ENABLED`), round_id для user выводится как в
  chat_engine; при выключенном флаге событие не пишется (регрессия);
- golden-классификация расхождений со старым `can_character_perceive_event`
  по четырём категориям v2 (§7 Фаза 2) + И13-подкатегории (стекло / крик
  из-за стены / невидимость / стена / адресация);
- shadow-прогон `perceive()`: статистика (`WPE_SHADOW_STATS`) и логи
  `[WPE-P3] shadow …`, контекст и legacy-решения не меняются;
- критерий выхода: необъяснимых расхождений нет (`unexplained == 0`).
"""

from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import select

import app.ollama_client as ollama_client
from app import crud
from app import models
from app import perception
from app import schemas
from app import wpe_shadow
from app.config import settings

# Защита от известной утечки мока (test_stream_disconnect) — как в Фазе 2.
_REAL_GENERATE = ollama_client.generate

_CATEGORIES = (
    wpe_shadow.REGRESSION,
    wpe_shadow.FIX,
    wpe_shadow.EXPECTED_EXPANSION,
    wpe_shadow.EXPECTED_MODEL_CHANGE,
)


def _reset_stats() -> None:
    wpe_shadow.WPE_SHADOW_STATS.update(
        {
            "events": 0,
            "observers": 0,
            "matched": 0,
            "diverged": 0,
            "by_category": {c: 0 for c in _CATEGORIES},
            "by_sublabel": {},
            "unexplained": 0,
        }
    )


@pytest.fixture(autouse=True)
def _reset_wpe_state(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    _reset_stats()
    ollama_client.generate = _REAL_GENERATE
    yield


async def _create_message(
    db,
    chat_id: int,
    *,
    character_id=None,
    role="character",
    content="hi",
    location="",
    targets=(),
    round_id=None,
    visibility="local",
    channel="direct",
):
    # Shadow-триггер перенесён из crud в сервисный слой (Sprint 1, §7.1):
    # тест повторяет контракт `chat_engine._create_message_with_shadow`.
    message = await crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            character_id=character_id,
            role=role,
            content=content,
            visibility=visibility,
            location=location,
            target_character_ids=list(targets),
            channel=channel,
            stimuli=[],
        ),
        round_id=round_id,
    )
    await wpe_shadow.maybe_run_shadow_perception(db, message)
    return message


async def _world_events(db, message_id: int):
    stmt = select(models.WorldEvent).where(models.WorldEvent.message_id == message_id)
    return list((await db.execute(stmt)).scalars().all())


def _res(visual="none", audio="none", addressed=False, remote="none"):
    return schemas.PerceptionResult(
        visual_level=visual,
        audio_level=audio,
        addressed=addressed,
        remote_status=remote,
    )


# ---------------------------------------------------------------------------
# Dual-write (атомарный WorldEvent рядом с Message)
# ---------------------------------------------------------------------------

async def test_flag_off_no_world_event(db_session, chat, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    msg = await _create_message(
        db_session, chat.id, role="user", content="привет", location="Кухня"
    )
    assert await _world_events(db_session, msg.id) == []


async def test_dual_write_user_event_round_derived(db_session, chat, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", True)
    msg = await _create_message(
        db_session, chat.id, role="user", content="привет", location="Кухня"
    )
    events = await _world_events(db_session, msg.id)
    assert len(events) == 1
    ev = events[0]
    assert ev.chat_id == chat.id
    assert ev.message_id == msg.id
    assert ev.character_id is None
    assert ev.event_type == "speech"
    assert ev.location == "Кухня"
    assert ev.round_id == f"r{chat.id}-m{msg.id}"


async def test_dual_write_character_event_fields(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_events_enabled", True)
    author, target, _ = three_characters
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=author.id,
        role="character",
        content="Привет!",
        location="Кухня",
        targets=[target.id],
        round_id="r1-m9",
        channel="phone",
    )
    events = await _world_events(db_session, msg.id)
    assert len(events) == 1
    ev = events[0]
    assert ev.character_id == author.id
    assert ev.event_type == "speech"
    assert ev.location == "Кухня"
    assert ev.round_id == "r1-m9"
    assert ev.target_character_ids == json.dumps([target.id], ensure_ascii=False)


async def test_dual_write_system_event_type(db_session, chat, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", True)
    msg = await _create_message(
        db_session,
        chat.id,
        role="system",
        content="*A переместился*",
        visibility="global",
    )
    events = await _world_events(db_session, msg.id)
    assert len(events) == 1
    assert events[0].event_type == "system"
    assert events[0].character_id is None


# ---------------------------------------------------------------------------
# Golden-классификация расхождений (4 категории v2 + И13-подкатегории)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "old_present,visual,audio,same_location,expected_cat,expected_sub",
    [
        # matched
        ("present", "full", "full", False, None, None),
        ("absent", "none", "none", False, None, None),
        # regression (old=present, new=absent)
        ("present", "none", "none", False, wpe_shadow.REGRESSION, "OLD_PRESENT_NEW_ABSENT"),
        ("audible", "none", "none", False, wpe_shadow.REGRESSION, "OLD_PRESENT_NEW_ABSENT"),
        # fix (old=absent, new=present)
        ("absent", "full", "full", False, wpe_shadow.FIX, "OLD_ABSENT_NEW_PRESENT"),
        # expected_expansion (old=absent, new=partial)
        ("absent", "full", "none", False, wpe_shadow.EXPECTED_EXPANSION, "GLASS"),
        ("absent", "none", "full", False, wpe_shadow.EXPECTED_EXPANSION, "SHOUT_THROUGH_WALL"),
        ("absent", "none", "muffled", False, wpe_shadow.EXPECTED_EXPANSION, "WALL"),
        ("absent", "partial", "muffled", False, wpe_shadow.EXPECTED_EXPANSION, "PARTIAL"),
        # expected_model_change (old=present, new=partial / remote)
        ("present", "none", "full", True, wpe_shadow.EXPECTED_MODEL_CHANGE, "INVISIBLE"),
        ("present", "full", "none", True, wpe_shadow.EXPECTED_MODEL_CHANGE, "GLASS"),
        ("present", "none", "muffled", False, wpe_shadow.EXPECTED_MODEL_CHANGE, "WALL"),
        ("absent", "full", "full", False, wpe_shadow.EXPECTED_MODEL_CHANGE, "REMOTE_DELIVERED"),
    ],
)
def test_classify_golden_grid(
    old_present, visual, audio, same_location, expected_cat, expected_sub
):
    remote = "delivered" if expected_sub == "REMOTE_DELIVERED" else "none"
    result = _res(visual, audio, remote=remote)
    out = wpe_shadow.classify_shadow_discrepancy(
        old_presence=old_present, result=result, same_location=same_location
    )
    if expected_cat is None:
        assert out is None
        return
    assert out == (expected_cat, expected_sub)


def test_classify_addressed_partial_sublabel():
    result = _res("partial", "muffled", addressed=True)
    out = wpe_shadow.classify_shadow_discrepancy(old_presence="absent", result=result)
    assert out == (wpe_shadow.EXPECTED_EXPANSION, "ADDRESSED_PARTIAL")


def test_exit_criteria_no_unexplained_divergences():
    """Все расхождения попадают в известные категории; unexplained == 0."""
    cases = [
        ("present", "none", "none", False),
        ("present", "none", "full", True),
        ("present", "full", "none", True),
        ("present", "none", "muffled", False),
        ("absent", "full", "full", False),
        ("absent", "full", "none", False),
        ("absent", "none", "full", False),
        ("absent", "none", "muffled", False),
    ]
    diverged = 0
    for old_present, visual, audio, same_location in cases:
        out = wpe_shadow.classify_shadow_discrepancy(
            old_presence=old_present,
            result=_res(visual, audio),
            same_location=same_location,
        )
        if out is not None:
            diverged += 1
            wpe_shadow._record(*out)
    stats = wpe_shadow.wpe_shadow_stats_snapshot()
    assert stats["diverged"] == diverged
    assert stats["unexplained"] == 0
    assert stats["by_category"][wpe_shadow.REGRESSION] > 0
    assert stats["by_category"][wpe_shadow.FIX] > 0
    assert stats["by_category"][wpe_shadow.EXPECTED_EXPANSION] > 0
    assert stats["by_category"][wpe_shadow.EXPECTED_MODEL_CHANGE] > 0
    assert {"GLASS", "SHOUT_THROUGH_WALL", "INVISIBLE", "WALL"} <= set(
        stats["by_sublabel"]
    )


# ---------------------------------------------------------------------------
# Shadow-прогон (интеграция через create_message)
# ---------------------------------------------------------------------------

async def _add_adjacent_locations(db, chat_id: int) -> None:
    """«Кухня» ↔ «Гостиная» (стена: visual=none, audio=full), «Спальня» изолирована."""
    db.add(
        models.Location(
            chat_id=chat_id,
            name="Кухня",
            adjacent_to=json.dumps(
                [
                    {
                        "name": "Гостиная",
                        "visual_permeability": "none",
                        "audio_permeability": "full",
                    }
                ],
                ensure_ascii=False,
            ),
        )
    )
    db.add(
        models.Location(
            chat_id=chat_id,
            name="Гостиная",
            adjacent_to=json.dumps(
                [
                    {
                        "name": "Кухня",
                        "visual_permeability": "none",
                        "audio_permeability": "full",
                    }
                ],
                ensure_ascii=False,
            ),
        )
    )
    db.add(models.Location(chat_id=chat_id, name="Спальня", adjacent_to="[]"))
    await db.commit()


async def test_shadow_runner_logs_divergence_and_stats(
    db_session, chat, three_characters, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "world_engine_events_enabled", True)
    author, observer, far = three_characters
    author.location = "Кухня"
    observer.location = "Гостиная"
    far.location = "Спальня"
    await _add_adjacent_locations(db_session, chat.id)
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="app.wpe_shadow"):
        msg = await _create_message(
            db_session,
            chat.id,
            character_id=author.id,
            role="character",
            content="Эй, вы!",
            location="Кухня",
            round_id="r1-m5",
        )

    stats = wpe_shadow.wpe_shadow_stats_snapshot()
    assert stats["events"] == 1
    assert stats["observers"] == 3
    # автор (old=present, new=full/full) и дальний (old=absent, new=none/none) — совпали
    assert stats["matched"] == 2
    assert stats["diverged"] == 1
    assert stats["unexplained"] == 0
    # сосед: old=absent (ADJACENT_QUIET) → new=audio full/visual none → крик из-за стены
    assert stats["by_sublabel"].get("SHOUT_THROUGH_WALL") == 1
    assert stats["by_category"][wpe_shadow.EXPECTED_EXPANSION] >= 1

    assert "SHOUT_THROUGH_WALL" in caplog.text
    assert "[WPE-P3] shadow divergence" in caplog.text


async def test_shadow_runner_flag_off_noop(db_session, chat, three_characters, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    author = three_characters[0]
    author.location = "Кухня"
    await db_session.commit()
    await _create_message(
        db_session,
        chat.id,
        character_id=author.id,
        content="тихо",
        location="Кухня",
    )
    assert wpe_shadow.wpe_shadow_stats_snapshot()["events"] == 0


async def test_shadow_does_not_write_presence_or_alter_legacy(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_events_enabled", True)
    author, viewer, _ = three_characters
    author.location = "Кухня"
    await db_session.commit()

    event = {
        "location": "Кухня",
        "character_id": author.id,
        "role": "character",
        "content": "привет",
        "visibility": "local",
        "channel": "direct",
        "target_character_ids": [],
        "stimuli": [],
    }
    before = perception.can_character_perceive_event(
        viewer_character_id=viewer.id,
        viewer_location="",
        event=event,
        viewer_name=viewer.name,
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=author.id,
        role="character",
        content="привет",
        location="Кухня",
        round_id="r1-m1",
    )
    after = perception.can_character_perceive_event(
        viewer_character_id=viewer.id,
        viewer_location="",
        event=event,
        viewer_name=viewer.name,
    )
    assert after == before
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.message_id == msg.id
    )
    assert list((await db_session.execute(stmt)).scalars().all()) == []

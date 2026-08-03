"""Sprint 2 tests (Plans/isolation-fix.md §18, items 4-10): perception levels.

Covers the new VISIBLE / AUDIBLE / MENTIONED / ABSENT levels, adjacency-driven
audio perception, address-driven mentioning, and the "no visual leak in AUDIBLE"
guarantee (ТЗ §6-§8, §14).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import crud
from app import perception
from app import schemas
from app import witness_model
from app.stimuli import Stimulus, build_audible_line

_ADJ = {"living_room": {"kitchen"}}


def _event(
    content: str,
    *,
    location: str = "",
    stimuli: list | None = None,
    character_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        role="character",
        character_id=character_id,
        content=content,
        location=location,
        visibility="local",
        target_character_ids="[]",
        stimuli=stimuli or [],
    )


# ------------------------------- Test 4: VISIBLE -------------------------------
def test_same_location_visible():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="living_room",
    )
    assert level == "visible"
    assert reason == "SAME_LOCATION"


def test_same_location_visible_even_with_loud_stimuli():
    """Событие из той же локации → visible вне зависимости от стимулов (§1.4)."""
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="living_room",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="knock", audibility="high")],
    )
    assert level == "visible"


# ------------------------------- Test 5/6: AUDIBLE ------------------------------
def test_adjacent_knock_audible():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="knock", audibility="high")],
    )
    assert level == "audible"
    assert reason == "ADJACENT_KNOCK"


def test_adjacent_shout_audible():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="shout", audibility="high")],
    )
    assert level == "audible"
    assert reason == "ADJACENT_SHOUT"


def test_adjacent_quiet_without_stimulus_not_audible():
    """Тихие события из соседней локации без громкого стимула НЕ слышны (§7)."""
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
    )
    assert level == "absent"
    assert reason == "ADJACENT_QUIET"


def test_can_perceive_adjacent_knock_audible_presence():
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=2,
        viewer_location="living_room",
        event=_event("Стук в стену", location="kitchen", stimuli=[Stimulus(type="knock", audibility="high")]),
        adjacency_index=_ADJ,
    )
    assert presence == "audible"
    assert reason == "ADJACENT_KNOCK"


# ------------------------------ Test 7: MENTIONED -------------------------------
def test_address_by_name_mentioned():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="address", target_character="Борис")],
        viewer_name="Борис",
    )
    assert level == "mentioned"
    assert reason == "MENTIONED_ADDRESS"


def test_call_by_name_mentioned():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="call", target_character="Борис", audibility="high")],
        viewer_name="Борис",
    )
    assert level == "mentioned"


def test_address_other_character_from_adjacent_not_mentioned():
    """Address на другого из соседней локации → НЕ mentioned (нет аудио-стимула)."""
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="kitchen",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="address", target_character="Анна")],
        viewer_name="Борис",
    )
    assert level == "absent"


# ------------------------- Test 8: NOT MENTIONED --------------------------------
def test_simple_mention_not_mentioned():
    """Простое упоминание имени в повествовании → НЕ mentioned (ТЗ §6)."""
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="far_street",
        event_text="Вчера Антон ходил в магазин",
        viewer_name="Антон",
        adjacency_index=_ADJ,
    )
    assert level != "mentioned"


def test_local_branch_name_mention_no_longer_mentioned():
    """LOCAL-ветка больше не даёт mentioned за простое упоминание имени."""
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=1,
        viewer_location="street",
        event={
            "role": "user",
            "character_id": None,
            "location": "living_room",
            "visibility": "local",
            "target_character_ids": [],
            "content": "Вчера Антон ходил в магазин",
        },
        viewer_name="Антон",
    )
    assert presence == "absent"
    assert reason == "DIFFERENT_LOCATION"


# ------------------------- Test 9: ABSENT ---------------------------------------
def test_far_unrelated_location_absent_even_when_addressed():
    """Далёкая несвязанная локация → ABSENT даже при обращении по имени (§14)."""
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="garden",
        adjacency_index=_ADJ,
        stimuli=[Stimulus(type="address", target_character="Борис")],
        viewer_name="Борис",
    )
    assert level == "absent"
    assert reason == "UNREACHABLE_ADDRESS"


def test_far_location_absent():
    level, reason = perception.get_perception_level(
        viewer_location="living_room",
        event_location="garden",
        adjacency_index=_ADJ,
    )
    assert level == "absent"
    assert reason == "DIFFERENT_LOCATION"


# ------------------------- Test 10: AUDIBLE no visual leak ----------------------
def test_audible_line_does_not_reveal_visual_details():
    event = _event(
        "Борис вошёл в кухню в красном плаще и схватил нож.",
        location="kitchen",
        stimuli=[Stimulus(type="knock", audibility="high")],
    )
    line = build_audible_line(event)
    assert "нож" not in line
    assert "красн" not in line
    assert "плащ" not in line
    assert "стук" in line.lower()


def test_audible_line_no_stimuli_generic_no_content():
    event = _event(
        "Секрет: Ольга спрятала ключ под ковром.",
        location="kitchen",
    )
    line = build_audible_line(event)
    assert "ключ" not in line
    assert "ковр" not in line
    assert line == "Из соседней локации доносится звук."


def test_format_audible_line_uses_template():
    event = _event(
        "Борис в красном плаще схватил нож.",
        location="kitchen",
        stimuli=[Stimulus(type="shout", audibility="high")],
    )
    line = witness_model.format_line_for_presence(event, "audible", {1: "Борис"})
    assert line is not None
    assert line.startswith("[Ты слышишь:")
    assert "нож" not in line
    assert "красн" not in line


# ----------------------------- adjacency index -----------------------------
def _loc(name: str, adjacent_to: list[str]) -> SimpleNamespace:
    return SimpleNamespace(name=name, adjacent_to=adjacent_to)


def test_build_adjacency_index_symmetric():
    index = perception.build_adjacency_index(
        [_loc("Кухня", ["Гостиная"]), _loc("Гостиная", [])]
    )
    assert "кухня" in index
    assert "гостиная" in index["кухня"]
    assert "кухня" in index["гостиная"]


def test_are_locations_adjacent():
    index = {"кухня": {"гостиная"}, "гостиная": {"кухня"}}
    assert perception.are_locations_adjacent("Кухня", "Гостиная", index)
    assert perception.are_locations_adjacent("гостиная", "кухня", index)
    assert not perception.are_locations_adjacent("кухня", "улица", index)
    assert not perception.are_locations_adjacent("кухня", "кухня", index)
    assert not perception.are_locations_adjacent("кухня", "гостиная", None)


# -------------------------- CRUD + perception integration --------------------------
@pytest.mark.asyncio
async def test_adjacency_crud_and_perception_integration(db_session, chat):
    """adjacent_to persists through CRUD and drives AUDIBLE presence end-to-end."""
    await crud.create_location(
        db_session,
        chat.id,
        schemas.LocationCreate(name="Кухня", adjacent_to=["Гостиная"]),
    )
    await crud.create_location(
        db_session,
        chat.id,
        schemas.LocationCreate(name="Гостиная"),
    )

    index = await crud.get_adjacency_index(db_session, chat.id)
    assert "кухня" in index
    assert "гостиная" in index["кухня"]
    assert "кухня" in index["гостиная"]

    names = {1: "Борис", 2: "Ольга"}
    event = SimpleNamespace(
        id=1,
        role="character",
        character_id=2,
        content="Секрет: ключ под ковром.",
        location="Гостиная",
        visibility="local",
        target_character_ids="[]",
        stimuli=[Stimulus(type="knock", audibility="high")],
    )
    presence = witness_model.compute_mvp_presence(
        event,
        1,
        names,
        viewer_location="Кухня",
        adjacency_index=index,
    )
    assert presence == "audible"

    # AUDIBLE line must not leak the visual detail.
    line = witness_model.format_line_for_presence(event, presence, names)
    assert line is not None
    assert "ключ" not in line
    assert "ковр" not in line


@pytest.mark.asyncio
async def test_rename_updates_adjacency_references(db_session, chat):
    """Rename of a location updates `adjacent_to` on sibling locations."""
    kitchen = await crud.create_location(
        db_session,
        chat.id,
        schemas.LocationCreate(name="Кухня", adjacent_to=["Гостиная"]),
    )
    await crud.create_location(
        db_session,
        chat.id,
        schemas.LocationCreate(name="Гостиная"),
    )
    await crud.update_location(
        db_session,
        kitchen.id,
        schemas.LocationUpdate(name="Кухня-2"),
    )
    index = await crud.get_adjacency_index(db_session, chat.id)
    assert "кухня-2" in index
    assert "гостиная" in index["кухня-2"]

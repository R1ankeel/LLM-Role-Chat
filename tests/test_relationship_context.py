"""Tests for the per-pair relationship context builder and delta constraints."""

from types import SimpleNamespace

from app.chat_engine import (
    _build_pair_relationship_context,
    _constrain_pair_delta,
)
from app.relationship_analyzer import _build_analyzer_prompt
from app.schemas import RelationshipDelta

PLAYER_ID = 100

NAMES = {1: "A", 2: "B", 3: "C", PLAYER_ID: "Игрок"}


def _char(cid: int, name: str, location: str):
    return SimpleNamespace(id=cid, name=name, location=location)


def _snap(
    role: str,
    character_id,
    content: str,
    *,
    location: str = "hall",
    visibility: str = "local",
    targets: list[int] | None = None,
) -> dict:
    return {
        "id": 1,
        "role": role,
        "character_id": character_id,
        "content": content,
        "location": location,
        "visibility": visibility,
        "channel": "direct",
        "target_character_ids": targets or [],
    }


class TestPairContextMisattribution:
    """Confession to B must not be attributed to pairs that did not interact."""

    def test_third_party_pair_has_no_evidence(self):
        # Player confesses to B; everyone in the same hall.
        snap = _snap("user", None, "Я люблю тебя, B", targets=[2])
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(2, "B", "hall"), _char(3, "C", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is False
        assert ctx["direct_interaction"] is False
        assert ctx["observed_target"] is False

    def test_confessed_pair_is_direct(self):
        snap = _snap("user", None, "Я люблю тебя, B", targets=[2])
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(2, "B", "hall"), _char(PLAYER_ID, "Игрок", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is True
        assert ctx["direct_interaction"] is True
        assert "-> B" in ctx["excerpt"]

    def test_witness_pair_is_observed_not_direct(self):
        snap = _snap("user", None, "Я люблю тебя, B", targets=[2])
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(3, "C", "hall"), _char(PLAYER_ID, "Игрок", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is True
        assert ctx["direct_interaction"] is False
        assert ctx["observed_target"] is True


class TestPairContextIsolation:
    """Isolated monologue must not inflate unrelated relationships."""

    def test_reflection_about_target_is_weak(self):
        snap = _snap(
            "character", 1, "Ох, если бы B был здесь рядом...",
            location="room",
        )
        locations = {1: "room", 2: "garden", 3: "garden"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "room"), _char(2, "B", "garden"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is True
        assert ctx["direct_interaction"] is False
        assert ctx["observed_target"] is True

    def test_unrelated_target_has_no_evidence(self):
        snap = _snap(
            "character", 1, "Ох, если бы B был здесь рядом...",
            location="room",
        )
        locations = {1: "room", 2: "garden", 3: "garden"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "room"), _char(3, "C", "garden"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is False


class TestPairContextFaceToFace:
    def test_two_people_room_speech_is_direct(self):
        snap = _snap("character", 1, "Я тебя люблю", location="hall")
        locations = {1: "hall", 2: "hall", 3: "garden"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(2, "B", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["direct_interaction"] is True
        assert ctx["observed_target"] is False

    def test_third_person_absent_pair_skipped(self):
        snap = _snap("character", 1, "Я тебя люблю", location="hall")
        locations = {1: "hall", 2: "hall", 3: "garden"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(3, "C", "garden"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["any_evidence"] is False


class TestConstrainPairDelta:
    def _delta(self, **kwargs) -> RelationshipDelta:
        return RelationshipDelta(
            source_character_id=1,
            target_character_id=2,
            delta_affection=20,
            delta_attraction=15,
            relationship_type="возлюбленные",
            importance=5,
            **kwargs,
        )

    def test_observed_caps_and_keeps_type(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(), rel, {"direct_interaction": False}
        )
        assert out.delta_affection == 5
        assert out.delta_attraction == 5
        assert out.relationship_type == "нейтральное"

    def test_direct_is_not_capped(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(), rel, {"direct_interaction": True}
        )
        assert out.delta_affection == 20
        assert out.delta_attraction == 15
        assert out.relationship_type == "возлюбленные"


class TestAnalyzerPrompt:
    def test_prompt_mentions_no_interaction_rule(self):
        prompt = _build_analyzer_prompt(
            source_name="A",
            target_name="B",
            source_character_id=1,
            target_character_id=2,
            current_type="нейтральное",
            affection=50,
            trust=50,
            attraction=0,
            resentment=0,
            jealousy=0,
            recent_events_text="",
            round_text="",
            interaction_summary="взаимодействия не было",
            direct_interaction=False,
            observed_target=False,
        )
        assert "ВСЕ дельты должны быть 0" in prompt
        assert "ТОЛЬКО отношения A к B" in prompt

    def test_prompt_observed_small_deltas(self):
        prompt = _build_analyzer_prompt(
            source_name="A",
            target_name="B",
            source_character_id=1,
            target_character_id=2,
            current_type="нейтральное",
            affection=50,
            trust=50,
            attraction=0,
            resentment=0,
            jealousy=0,
            recent_events_text="",
            round_text="",
            interaction_summary="A наблюдал события с B",
            direct_interaction=False,
            observed_target=True,
        )
        assert "малые дельты" in prompt
        assert "relationship_type НЕ менять" in prompt

    def test_prompt_direct_interaction(self):
        prompt = _build_analyzer_prompt(
            source_name="A",
            target_name="B",
            source_character_id=1,
            target_character_id=2,
            current_type="нейтральное",
            affection=50,
            trust=50,
            attraction=0,
            resentment=0,
            jealousy=0,
            recent_events_text="",
            round_text="",
            interaction_summary="A и B говорили",
            direct_interaction=True,
            observed_target=True,
        )
        assert "взаимодействовали напрямую" in prompt
        assert "-20..20" in prompt

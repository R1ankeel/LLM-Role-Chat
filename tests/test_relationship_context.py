"""Tests for the per-pair relationship context builder and delta constraints."""

from types import SimpleNamespace

from app.chat_engine import (
    _build_pair_relationship_context,
    _constrain_pair_delta,
    _evidence_mode,
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


class TestPairContextHearsay:
    """Third party telling the source about the target is hearsay (§12)."""

    def _ctx_for(self, snap, source_id=1, target_id=2):
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        return _build_pair_relationship_context(
            [snap], _char(source_id, NAMES[source_id], "hall"),
            _char(target_id, NAMES[target_id], "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )

    def test_direct_address_about_target_is_hearsay(self):
        # C addresses A and talks about B -> second-hand report for A->B.
        snap = _snap("character", 3, "Слушай, B вчера обманул всех", targets=[1])
        ctx = self._ctx_for(snap)
        assert ctx["hearsay"] is True
        assert ctx["hearsay_source"] == 3
        assert ctx["any_evidence"] is True
        assert ctx["direct_interaction"] is False
        assert ctx["observed_target"] is False
        assert "[слух от C]" in ctx["excerpt"]

    def test_non_addressed_report_is_observed_not_hearsay(self):
        # C talks about B in A's presence but does not address A.
        snap = _snap("character", 3, "B вчера обманул всех", location="hall")
        ctx = self._ctx_for(snap)
        assert ctx["hearsay"] is False
        assert ctx["hearsay_source"] is None
        assert ctx["observed_target"] is True
        assert "[слух от C]" not in ctx["excerpt"]

    def test_unrelated_third_party_is_not_hearsay(self):
        # C addresses A but does not mention B -> no evidence for A->B.
        snap = _snap("character", 3, "Слушай, погода сегодня отличная", targets=[1])
        ctx = self._ctx_for(snap)
        assert ctx["hearsay"] is False
        assert ctx["any_evidence"] is False


class TestEvidenceMode:
    def test_direct(self):
        assert _evidence_mode({"direct_interaction": True, "observed_target": True}) == "direct"

    def test_observed(self):
        assert _evidence_mode({"direct_interaction": False, "observed_target": True}) == "observed"

    def test_hearsay(self):
        assert _evidence_mode({"hearsay": True, "direct_interaction": False, "observed_target": False}) == "hearsay"

    def test_direct_overrides_hearsay(self):
        assert _evidence_mode({"hearsay": True, "direct_interaction": True}) == "direct"

    def test_none(self):
        assert _evidence_mode({"direct_interaction": False, "observed_target": False}) == "none"
        assert _evidence_mode({"direct_interaction": False}) == "none"


class TestConstrainPairDelta:
    def _delta(self, **kwargs) -> RelationshipDelta:
        kwargs.setdefault("importance", 5)
        return RelationshipDelta(
            source_character_id=1,
            target_character_id=2,
            delta_affection=20,
            delta_attraction=15,
            relationship_type="возлюбленные",
            **kwargs,
        )

    def test_observed_caps_and_keeps_type(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"direct_interaction": False, "observed_target": True},
        )
        assert out is not None
        assert out.delta_affection == 5
        assert out.delta_attraction == 5
        assert out.relationship_type == "нейтральное"

    def test_none_mode_rejects_delta(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"direct_interaction": False, "observed_target": False},
        )
        assert out is None

    def test_hearsay_caps_and_keeps_type(self):
        from app.config import settings

        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"hearsay": True, "direct_interaction": False, "observed_target": False},
        )
        assert out is not None
        assert out.delta_affection == settings.relationship_hearsay_cap
        assert out.delta_attraction == settings.relationship_hearsay_cap
        assert out.relationship_type == "нейтральное"

    def test_hearsay_uses_effective_cap(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"hearsay": True, "hearsay_effective_cap": 2},
        )
        assert out is not None
        assert out.delta_affection == 2
        assert out.relationship_type == "нейтральное"

    def test_hearsay_cap_floor_is_one(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"hearsay": True, "hearsay_effective_cap": 0},
        )
        assert out is not None
        assert out.delta_affection == 1

    def test_direct_capped_by_importance(self):
        # importance=5 -> cap_by_importance[5]=10 narrows the ±20 direct cap.
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(), rel, {"direct_interaction": True}
        )
        assert out is not None
        assert out.delta_affection == 10
        assert out.delta_attraction == 10
        assert out.relationship_type == "возлюбленные"

    def test_direct_importance_one_tight_cap(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(importance=1), rel, {"direct_interaction": True}
        )
        assert out is not None
        assert out.delta_affection == 2
        assert out.delta_attraction == 2

    def test_direct_importance_ten_keeps_max(self):
        # importance=10 -> cap 30 > MAX_DELTA 20, so ±20 stays intact.
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(importance=10), rel, {"direct_interaction": True}
        )
        assert out is not None
        assert out.delta_affection == 20
        assert out.delta_attraction == 15

    def test_observed_importance_narrows_mode_cap(self):
        # observed cap 5 vs importance=2 cap 3 -> min(5, 3) = 3.
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(importance=2),
            rel,
            {"direct_interaction": False, "observed_target": True},
        )
        assert out is not None
        assert out.delta_affection == 3
        assert out.delta_attraction == 3


class TestConstrainPairDeltaSaturation:
    """Anti-inflation §27.3: repeated growth is dampened by recent gains."""

    def _delta(self, **kwargs) -> RelationshipDelta:
        kwargs.setdefault("delta_affection", 10)
        kwargs.setdefault("delta_attraction", 15)
        return RelationshipDelta(
            source_character_id=1,
            target_character_id=2,
            relationship_type="нейтральное",
            importance=5,
            **kwargs,
        )

    def test_positive_deltas_dampened_above_threshold(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "direct_interaction": True,
                "recent_gains": {"affection": 30, "attraction": 30},
            },
        )
        assert out is not None
        # 10 * 0.3 = 3; 15 * 0.3 = 4.5 -> 4 (round-half-to-even -> 4)
        assert out.delta_affection == 3
        assert out.delta_attraction == 4

    def test_below_threshold_unchanged(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "direct_interaction": True,
                "recent_gains": {"affection": 20, "attraction": 20},
            },
        )
        assert out is not None
        assert out.delta_affection == 10
        # Saturation is a no-op below threshold, but the importance cap (10)
        # still clamps attraction 15 → 10.
        assert out.delta_attraction == 10

    def test_negative_and_missing_metrics_untouched(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        delta = self._delta(delta_affection=-10, delta_attraction=0)
        out = _constrain_pair_delta(
            delta,
            rel,
            {
                "direct_interaction": True,
                "recent_gains": {"affection": 100},
            },
        )
        assert out is not None
        assert out.delta_affection == -10  # negative never dampened
        assert out.delta_attraction == 0

    def test_no_recent_gains_is_legacy(self):
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(), rel, {"direct_interaction": True}
        )
        assert out is not None
        assert out.delta_affection == 10  # importance=5 cap, no saturation
        assert out.delta_attraction == 10


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

    def test_prompt_hearsay_small_deltas(self):
        from app.config import settings

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
            round_text="[слух от C] C -> A: B обманул всех",
            interaction_summary="C -> A",
            direct_interaction=False,
            observed_target=False,
            hearsay=True,
            hearsay_cap=3,
        )
        assert "со слов третьего лица" in prompt
        assert "это слух" in prompt
        assert f"|дельты| <= {settings.relationship_hearsay_cap}" in prompt
        assert "relationship_type НЕ менять" in prompt

    def test_prompt_contains_importance_calibration(self):
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
        assert "ШКАЛА ВАЖНОСТИ" in prompt
        assert "Комплимент — это бытовое" in prompt


class TestThirdPartyNotes:
    """Triadic MVP (§13): third-party notes from current round."""

    def test_third_party_ids_collected_when_target_mentioned(self):
        # C addresses A about B -> C is third party for A->B pair
        snap = _snap("character", 3, "B вчера ушел", targets=[1])
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(2, "B", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert 3 in ctx["third_party_ids"]

    def test_third_party_ids_when_target_speaks_about_others(self):
        # B speaks about C -> C is third party for A->B pair
        snap = _snap("character", 2, "C любит котиков", location="hall")
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(2, "B", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert 3 in ctx["third_party_ids"]

    def test_third_party_ids_excludes_source_and_target(self):
        # A speaks about B -> no third party for A->B (source and target)
        snap = _snap("character", 1, "B, ты хороший", targets=[2])
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(2, "B", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert 1 not in ctx["third_party_ids"]
        assert 2 not in ctx["third_party_ids"]

    def test_third_party_ids_empty_when_unrelated(self):
        # C talks about weather, unrelated to A->B
        snap = _snap("character", 3, "Погода сегодня отличная", location="hall")
        locations = {1: "hall", 2: "hall", 3: "hall", PLAYER_ID: "hall"}
        ctx = _build_pair_relationship_context(
            [snap], _char(1, "A", "hall"), _char(2, "B", "hall"),
            NAMES, locations, player_id=PLAYER_ID,
        )
        assert ctx["third_party_ids"] == []

    def test_analyzer_prompt_includes_third_party_notes(self):
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
            interaction_summary="",
            direct_interaction=False,
            observed_target=True,
            third_party_notes=["[третье лицо] B ↔ C: соперники, ревность=60"],
        )
        assert "Заметки третьих лиц" in prompt
        assert "B ↔ C: соперники, ревность=60" in prompt

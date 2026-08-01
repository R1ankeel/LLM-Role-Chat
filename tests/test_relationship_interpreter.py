"""Tests for the deterministic relationship interpreter (State -> Interpretation)."""

from types import SimpleNamespace

from app.relationship_interpreter import (
    OPEN_ISSUE_DERIVED,
    build_behavior_drivers,
    decline_name,
    format_interpretation,
    interpret,
    RelationshipInterpretation,
    weighted_behavior_drivers,
)


def _rel(**overrides):
    defaults = {
        "affection": 50,
        "trust": 50,
        "attraction": 0,
        "resentment": 0,
        "jealousy": 0,
        "relationship_type": "нейтральное",
        "description": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _issue(issue_type="lie", text="Борис солгал Ане"):
    return SimpleNamespace(issue_type=issue_type, text=text)


class TestTrustBands:
    def test_low_trust(self):
        assert interpret(_rel(trust=20)).trust == "low"

    def test_medium_trust(self):
        assert interpret(_rel(trust=50)).trust == "medium"

    def test_high_trust(self):
        assert interpret(_rel(trust=90)).trust == "high"

    def test_boundary_low(self):
        assert interpret(_rel(trust=30)).trust == "medium"  # <30 is low, 30 is medium

    def test_boundary_high(self):
        assert interpret(_rel(trust=70)).trust == "high"  # >=70 is high


class TestAttachmentBands:
    def test_low_attachment(self):
        assert interpret(_rel(affection=10)).attachment == "low"

    def test_medium_attachment(self):
        assert interpret(_rel(affection=50)).attachment == "medium"

    def test_high_attachment(self):
        assert interpret(_rel(affection=90)).attachment == "high"


class TestHostility:
    def test_low_hostility(self):
        assert interpret(_rel(resentment=10, jealousy=10)).hostility == "low"

    def test_high_hostility_from_resentment(self):
        assert interpret(_rel(resentment=60)).hostility == "high"

    def test_high_hostility_from_jealousy(self):
        assert interpret(_rel(jealousy=70)).hostility == "high"


class TestAttractionBands:
    def test_none(self):
        assert interpret(_rel(attraction=10)).attraction == "none"

    def test_visible(self):
        assert interpret(_rel(attraction=50, trust=80, resentment=10)).attraction == "visible"

    def test_hidden_when_resentful(self):
        assert interpret(_rel(attraction=80, resentment=60)).attraction == "hidden"

    def test_hidden_when_mistrustful(self):
        assert interpret(_rel(attraction=80, trust=20, resentment=10)).attraction == "hidden"

    def test_high_attraction_trusted_is_visible(self):
        # attraction high but no resentment/low-trust -> visible, not hidden
        assert interpret(_rel(attraction=80, trust=80, resentment=10)).attraction == "visible"


class TestJealousyBands:
    def test_none(self):
        assert interpret(_rel(jealousy=0)).jealousy == "none"

    def test_moderate(self):
        assert interpret(_rel(jealousy=40)).jealousy == "moderate"

    def test_high(self):
        assert interpret(_rel(jealousy=70)).jealousy == "high"


class TestDerivedCombinations:
    def test_painful_attachment(self):
        interp = interpret(_rel(affection=80, trust=20))
        assert "болезненная привязанность" in interp.derived

    def test_painful_attachment_needs_both(self):
        interp = interpret(_rel(affection=80, trust=80))
        assert "болезненная привязанность" not in interp.derived
        interp2 = interpret(_rel(affection=40, trust=20))
        assert "болезненная привязанность" not in interp2.derived

    def test_hidden_attraction(self):
        interp = interpret(_rel(attraction=80, resentment=60))
        assert "скрытое влечение" in interp.derived

    def test_distrust_plus_resentment(self):
        interp = interpret(_rel(trust=20, resentment=60))
        assert "недоверие + обида" in interp.derived

    def test_distrust_plus_resentment_needs_both(self):
        interp = interpret(_rel(trust=20, resentment=20))
        assert "недоверие + обида" not in interp.derived
        interp2 = interpret(_rel(trust=80, resentment=60))
        assert "недоверие + обида" not in interp2.derived


class TestOpenIssues:
    def test_open_issue_adds_derived_label(self):
        interp = interpret(_rel(), open_issues=[_issue()])
        assert OPEN_ISSUE_DERIVED in interp.derived

    def test_empty_open_issues_no_label(self):
        assert OPEN_ISSUE_DERIVED not in interpret(_rel(), open_issues=[]).derived
        assert OPEN_ISSUE_DERIVED not in interpret(_rel()).derived

    def test_open_issue_driver_is_tendency(self):
        drivers = build_behavior_drivers(interpret(_rel(), open_issues=[_issue()]), "Борис")
        assert any("нерешённом вопросе" in d for d in drivers)
        assert all("должен" not in d and "обязан" not in d for d in drivers)

    def test_open_issue_driver_ranks_above_neutral(self):
        # Neutral state + open issue -> the open-issue driver is the only one.
        drivers = build_behavior_drivers(interpret(_rel(), open_issues=[_issue()]), "Борис")
        assert drivers and "нерешённом вопросе" in drivers[0]

    def test_format_interpretation_mentions_open_issue(self):
        text = format_interpretation(interpret(_rel(), open_issues=[_issue()]), "Борис")
        assert "нерешённый вопрос" in text

    def test_no_numbers_when_open_issue(self):
        text = format_interpretation(interpret(_rel(), open_issues=[_issue()]), "Борис")
        assert "=" not in text


class TestDeterminism:
    def test_same_input_same_output(self):
        a = interpret(_rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70))
        b = interpret(_rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70))
        assert a == b

    def test_returns_typed_interpretation(self):
        assert isinstance(interpret(_rel()), RelationshipInterpretation)


class TestDeclineName:
    def test_masculine_consonant_dative(self):
        assert decline_name("Борис", "dative") == "Борису"

    def test_masculine_consonant_accusative(self):
        assert decline_name("Борис", "accusative") == "Бориса"

    def test_feminine_a_dative(self):
        assert decline_name("Аня", "dative") == "Ане"
        assert decline_name("Катя", "dative") == "Кате"

    def test_feminine_a_accusative(self):
        assert decline_name("Аня", "accusative") == "Аню"
        assert decline_name("Катя", "accusative") == "Катю"

    def test_masculine_y_dative(self):
        assert decline_name("Андрей", "dative") == "Андрею"

    def test_masculine_y_accusative(self):
        assert decline_name("Андрей", "accusative") == "Андрея"

    def test_soft_sign_falls_back(self):
        assert decline_name("Игорь", "dative") == "Игорь"

    def test_multiworld_falls_back(self):
        assert decline_name("Борис Сидоров", "dative") == "Борис Сидоров"


class TestFormatInterpretation:
    def test_neutral_relationship_is_laconic(self):
        # Default neutral state -> no fabricated strong statements
        assert format_interpretation(interpret(_rel()), "Борис") == ""

    def test_low_trust_phrase(self):
        text = format_interpretation(interpret(_rel(trust=20)), "Борис")
        assert "не доверяешь Борису" in text

    def test_high_attachment_phrase(self):
        text = format_interpretation(interpret(_rel(affection=80)), "Борис")
        assert "привязан к Борису" in text

    def test_hidden_attraction_phrase(self):
        text = format_interpretation(interpret(_rel(attraction=80, resentment=60)), "Борис")
        assert "стараешься этого не показывать" in text

    def test_no_numbers_in_text(self):
        rel = _rel(affection=85, trust=20, attraction=70, resentment=60, jealousy=70)
        text = format_interpretation(interpret(rel), "Борис")
        assert "=" not in text
        assert "85" not in text
        assert "affection" not in text
        assert "доверие=" not in text
        assert "привязанность=" not in text


class TestBehaviorDrivers:
    def test_high_attachment_driver(self):
        drivers = build_behavior_drivers(interpret(_rel(affection=80, trust=80)), "Борис")
        assert any("эмоционально привязан" in d for d in drivers)

    def test_low_trust_driver(self):
        drivers = build_behavior_drivers(interpret(_rel(trust=20)), "Борис")
        assert any("не доверяешь Борису" in d for d in drivers)

    def test_high_resentment_grievance_driver(self):
        drivers = build_behavior_drivers(interpret(_rel(resentment=60)), "Борис")
        assert any("помнишь обиду на Бориса" in d for d in drivers)

    def test_hidden_attraction_driver(self):
        drivers = build_behavior_drivers(interpret(_rel(attraction=80, resentment=60)), "Борис")
        assert any("тянет к Борису" in d and "скрываешь" in d for d in drivers)

    def test_derived_driver_ranks_first(self):
        drivers = build_behavior_drivers(interpret(_rel(affection=80, trust=20)), "Борис")
        assert drivers[0] == "Ты держишься за Бориса, но твоя привязанность к Борису болезненна."

    def test_neutral_relationship_has_no_drivers(self):
        assert build_behavior_drivers(interpret(_rel()), "Борис") == []

    def test_drivers_are_tendencies_not_commands(self):
        drivers = build_behavior_drivers(
            interpret(_rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70)),
            "Борис",
        )
        joined = " ".join(drivers)
        assert "должен" not in joined
        assert "обязан" not in joined

    def test_deterministic(self):
        rel = _rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70)
        a = build_behavior_drivers(interpret(rel), "Борис")
        b = build_behavior_drivers(interpret(rel), "Борис")
        assert a == b

    def test_max_drivers_caps(self):
        drivers = build_behavior_drivers(
            interpret(_rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70)),
            "Борис",
            max_drivers=2,
        )
        assert len(drivers) == 2

    def test_no_numbers_in_drivers(self):
        drivers = build_behavior_drivers(
            interpret(_rel(affection=85, trust=20, resentment=60)), "Борис",
        )
        assert all("=" not in d and "85" not in d for d in drivers)


class TestWeightedBehaviorDrivers:
    def test_sorted_by_weight_desc(self):
        pairs = weighted_behavior_drivers(
            interpret(_rel(affection=80, trust=20, attraction=80, resentment=60, jealousy=70)),
            "Борис",
        )
        weights = [w for w, _ in pairs]
        assert weights == sorted(weights, reverse=True)

    def test_derived_outranks_base(self):
        pairs = weighted_behavior_drivers(interpret(_rel(affection=80, trust=20)), "Борис")
        assert pairs[0][1].startswith("Ты держишься за Бориса")
        assert pairs[0][0] > pairs[1][0]

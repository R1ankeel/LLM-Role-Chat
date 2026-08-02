"""Tests for relationship type validation (Sprint 4 item 1)."""

from app.relationship_service import validate_relationship_type_update


class TestValidation:
    def test_valid_type_and_transition(self):
        ok, err = validate_relationship_type_update("нейтральное", "друг")
        assert ok is True
        assert err == ""

    def test_same_type_is_allowed(self):
        ok, err = validate_relationship_type_update("враг", "враг")
        assert ok is True
        assert err == ""

    def test_invalid_type_rejected(self):
        ok, err = validate_relationship_type_update("нейтральное", "bogus_type")
        assert ok is False
        assert "bogus_type" in err
        assert "Must be one of" in err

    def test_invalid_transition_rejected(self):
        ok, err = validate_relationship_type_update("нейтральное", "заклятый_враг")
        assert ok is False
        assert "заклятый_враг" in err
        assert "Allowed transitions" in err

    def test_no_transitions_listed(self):
        ok, err = validate_relationship_type_update("заклятый_враг", "незнакомец")
        assert ok is False
        assert "Allowed transitions" in err

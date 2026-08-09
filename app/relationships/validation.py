"""Валидация типов отношений и константы (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения. Хранит
белые списки типов/переходов и функции проверки; применение дельт — в
``deltas.py``.
"""

from ..config import settings

VALID_TYPES = set(settings.relationship_valid_types)
TRANSITIONS: dict[str, set[str]] = {
    k: set(v) for k, v in settings.relationship_transition_rules.items()
}
# Family relations are UI-managed only: the engine may neither create nor
# remove them, even when a transition exists in the graph. Only the manual
# update endpoint (strict=False) can set/clear these types.
FAMILY_TYPES = set(settings.relationship_family_types)


def is_family_type(type_name: str) -> bool:
    """True for UI-only family types (``семья``/``родитель``/``брат_сестра``)."""
    return type_name in FAMILY_TYPES


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_transition(
    current_type: str,
    new_type: str,
) -> bool:
    if new_type == current_type:
        return True
    allowed = TRANSITIONS.get(current_type, set())
    return new_type in allowed


def validate_relationship_type_update(
    current_type: str,
    new_type: str,
    strict: bool = True,
) -> tuple[bool, str]:
    """Validate a relationship type update.
    
    Returns (is_valid, error_message). If valid, error_message is empty.
    Checks:
    1. new_type is in valid types whitelist
    2. transition from current_type to new_type is allowed

    ``strict`` controls the transition gate:
    - ``strict=True`` (default) — only realistic progressions are allowed
      (used by the automatic LLM analysis path: ``apply_delta``);
    - ``strict=False`` — any valid type is allowed, including family types
      (``семья``/``родитель``/``брат_сестра``), used by the manual editing
      endpoint. The whitelist check always applies.
    """
    if new_type not in VALID_TYPES:
        return False, (
            f"Invalid relationship_type: '{new_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_TYPES))}"
        )
    if strict and not validate_transition(current_type, new_type):
        allowed = TRANSITIONS.get(current_type, set())
        return False, (
            f"Invalid transition from '{current_type}' to '{new_type}'. "
            f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none'}"
        )
    return True, ""


def clamp_metric(value: int) -> int:
    return max(0, min(100, value))

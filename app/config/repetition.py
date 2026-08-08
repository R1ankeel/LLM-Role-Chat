"""Настройки детектора повторений (Sprint 2, §4.9)."""

from pydantic import Field




class RepetitionSettings():
    """Repetition detection: скоринг, cooldown, loop/stagnation."""

    # Repetition detection
    repetition_detection_enabled: bool = Field(default=True, alias="REPETITION_DETECTION_ENABLED")
    repetition_window_size: int = Field(default=6, alias="REPETITION_WINDOW_SIZE")
    repetition_threshold: float = Field(default=0.72, alias="REPETITION_THRESHOLD")
    stagnation_threshold: float = Field(default=0.65, alias="STAGNATION_THRESHOLD")
    max_repetition_retries: int = Field(default=2, alias="MAX_REPETITION_RETRIES")
    action_cooldown_turns: int = Field(default=2, alias="ACTION_COOLDOWN_TURNS")
    repetition_text_jaccard: float = Field(default=0.82, alias="REPETITION_TEXT_JACCARD")
    repetition_min_bundle_size: int = Field(default=2, alias="REPETITION_MIN_BUNDLE_SIZE")
    # Scene loop detection: a loop requires at least this many turns in the window.
    repetition_loop_min_turns: int = Field(default=6, alias="REPETITION_LOOP_MIN_TURNS")
    # If scene progression is >= this, loop/stagnation flags are suppressed
    # (the scene IS developing, e.g. touch/kiss escalation in intimacy scenes).
    repetition_scene_gate: float = Field(default=0.45, alias="REPETITION_SCENE_GATE")
    # Cooldown hard floor requires at least this many distinct hit actions.
    repetition_cooldown_hits_required: int = Field(default=2, alias="REPETITION_COOLDOWN_HITS_REQUIRED")
    # Softened floors for loop components (were hardcoded 0.85/0.75/0.8/0.55).
    repetition_shared_floor: float = Field(default=0.6, alias="REPETITION_SHARED_FLOOR")
    repetition_sticky_floor: float = Field(default=0.6, alias="REPETITION_STICKY_FLOOR")
    repetition_global_sticky_floor: float = Field(default=0.6, alias="REPETITION_GLOBAL_STICKY_FLOOR")
    repetition_global_sticky_low_floor: float = Field(default=0.4, alias="REPETITION_GLOBAL_STICKY_LOW_FLOOR")

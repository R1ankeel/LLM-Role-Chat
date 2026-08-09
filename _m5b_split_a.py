# -*- coding: utf-8 -*-
"""Temporary splitter for Milestone 5B (decomposition.md §4.2).

Reads `app/chat_engine.py` and regenerates, verbatim from source:
- app/pipeline/lora.py
- app/pipeline/story.py
- app/pipeline/session.py
- app/pipeline/streaming.py
- app/pipeline/regeneration.py
- app/chat_engine.py (facade: imports + re-exports + relations block stays)

NOT part of the app. Delete after use.
"""

import ast
import pathlib

ROOT = pathlib.Path("C:/dev/Role-LLM/ai-roleplay-chat")
SRC = ROOT / "app" / "chat_engine.py"

source = SRC.read_text(encoding="utf-8")
if source.startswith("\ufeff"):
    source = source[1:]
lines = source.split("\n")
tree = ast.parse(source)

REL_FUNC_NAMES = {
    "_analyze_and_update_relationships",
    "_run_sensors_relationship_proposal",
    "_run_per_pair_analysis",
    "_text_mentions_name",
    "_build_pair_relationship_context",
    "_evidence_mode",
    "evidence_mode_from_perception",
    "_build_batch_scene_summary",
    "_constrain_pair_delta",
    "_belief_multiplier",
    "_hearsay_effective_cap",
    "_compute_hearsay_effective_cap",
}


def func_lines(name: str):
    """0-based inclusive (start, end) line indices for a top/nested def."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno - 1, node.end_lineno - 1
    raise KeyError(name)


def slice_from(start_idx: int, end_idx: int) -> str:
    """0-based inclusive slice of `lines`."""
    return "\n".join(lines[start_idx : end_idx + 1])


def find_line(pred, start: int = 0, what: str = "line") -> int:
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    raise LookupError(f"line not found: {what}")


def rewrite(body_text: str) -> str:
    """Rewrite `from .` lazy imports (app-relative) to `from ..` (pipeline-relative)."""
    return body_text.replace("from .", "from ..")


def assert_once(text: str, needle: str, ctx: str):
    cnt = text.count(needle)
    assert cnt == 1, f"needle count {cnt} (expected 1) for {ctx}: {needle[:80]!r}"


# ---------------------------------------------------------------------------
# lora.py
# ---------------------------------------------------------------------------
_lora_start = find_line(
    lambda l: l.strip().startswith("_LORA_MANAGER_DEFAULT:"), what="_LORA_MANAGER_DEFAULT"
)
_lora_end = func_lines("lora_first_apply_warning")[1]
_lora_body = slice_from(_lora_start, _lora_end)

lora_py = '''"""LoRA: выбор модели для ОСНОВНОЙ генерации (Plans/LoRA.md, Sprint 3).

Вынесено из ``app/chat_engine.py`` (Milestone 5B, decomposition.md §4.2).

LoRA применяется ТОЛЬКО к основному ответу персонажа. Служебные LLM-вызовы
(scene state, post_round_pipeline, память, отношения, сенсоры, consolidation,
crisis) вызываются без этого хелпера и получают ``chat.model_name`` как раньше.
"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..lora_manager import CompatibilityStatus, LoRAManager, ResolveResult

logger = logging.getLogger("app.chat_engine.pipeline.lora")

'''
assert "from ." not in _lora_body, "lora body has relative imports?"
lora_py += _lora_body.rstrip() + "\n"

# ---------------------------------------------------------------------------
# story.py
# ---------------------------------------------------------------------------
_s1, _e1 = func_lines("_chat_plot_text")
_s2, _e2 = func_lines("_chat_story_block")
_ep_s, _ep_e = func_lines("_compute_epistemic_evidence")
_bel_s, _bel_e = func_lines("_belief_evidenced_ids")

story_py = '''"""Story-блок и belief evidence (decomposition.md §4.2, Sprint 8 §16).

Вынесено из ``app/chat_engine.py`` (Milestone 5B):
- ``_chat_plot_text`` / ``_chat_story_block`` — сюжет чата (Sprint 8 §16);
- ``_compute_epistemic_evidence`` / ``_belief_evidenced_ids`` — эпистемический
  evidence/belief для mask (docs/relations.md §10, Sprint 5 §9).

``_compute_epistemic_evidence`` использует ``_build_pair_relationship_context``
и ``_evidence_mode`` из фасада ``chat_engine`` (они переедут в
``pipeline/relations.py`` в Milestone 6A) — импорт отложенный, внутри функции.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings

logger = logging.getLogger("app.chat_engine.pipeline.story")

'''

_story_chunk = rewrite(slice_from(_s1, _e2))
_epistemic_chunk = rewrite(slice_from(_ep_s, _bel_e))

# Lazy facade imports inside _compute_epistemic_evidence (6A stopgap).
needle = '"""\n    evidenced: set[int] = set()'
assert_once(_epistemic_chunk, needle, "story epistemic")
_epistemic_chunk = _epistemic_chunk.replace(
    needle,
    '"""\n    from ..chat_engine import _build_pair_relationship_context, _evidence_mode\n\n    evidenced: set[int] = set()',
)

story_py += _story_chunk.rstrip() + "\n\n\n" + _epistemic_chunk.rstrip() + "\n"

# ---------------------------------------------------------------------------
# session.py
# ---------------------------------------------------------------------------
_cs_s, _cs_e = func_lines("_create_message_with_shadow")
_mtd_s, _mtd_e = func_lines("_message_to_dict")
_ms_s, _ms_e = func_lines("_message_snapshot")
_ld_s, _ld_e = func_lines("_load_location_descriptions")
_ct_s, _ct_e = func_lines("_character_to_snapshot")

session_py = '''"""Общие хелперы раунда + non-streaming точка входа (decomposition.md §4.2).

Вынесено из ``app/chat_engine.py`` (Milestone 5B). Хелперы используются и
``streaming.py``, и ``regeneration.py``; ``process_user_message`` — удобная
non-streaming обёртка над ``process_user_message_streaming``.

Logger хранится в поддереве ``app.chat_engine`` (``app.chat_engine.pipeline.*`),
чтобы log-фильтры/тесты, ориентирующиеся на ``app.chat_engine``, продолжали
видеть записи (пропагация в pytest caplog).
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import models
from .. import perception
from .. import schemas
from .. import wpe_shadow
from ..config import settings
from ..movement import detect_character_movement
from ..witness_model import resolve_presence

logger = logging.getLogger("app.chat_engine.pipeline.session")

'''

_piece_create = rewrite(slice_from(_cs_s, _cs_e))
_piece_msg = rewrite(slice_from(_mtd_s, _ms_e))
# Drop epistemic/belief blocks BEFORE rewriting (the block contains lazy
# `from . import crud`; `_ep_block` is matched against the raw slice).
_piece_helpers = slice_from(_ld_s, _ct_e)
_ep_block = slice_from(_ep_s, _bel_e)
assert_once(_piece_helpers, _ep_block, "session epistemic removal")
_piece_helpers = _piece_helpers.replace(_ep_block, "")
_piece_helpers = rewrite(_piece_helpers)

_piece_pum = rewrite(slice_from(*func_lines("process_user_message")))
# Lazy import of streaming to avoid session <-> streaming cycle.
needle_pum = "    messages = []"
assert_once(_piece_pum, needle_pum, "session process_user_message")
_piece_pum = _piece_pum.replace(
    needle_pum,
    "    from .streaming import process_user_message_streaming\n\n    messages = []",
)

session_py += _piece_create.rstrip() + "\n\n\n"
session_py += _piece_msg.rstrip() + "\n\n\n"
session_py += _piece_helpers.rstrip() + "\n\n\n"
session_py += _piece_pum.rstrip() + "\n"

# ---------------------------------------------------------------------------
# streaming.py
# ---------------------------------------------------------------------------
_st_s, _st_e = func_lines("process_user_message_streaming")
_streaming_body = rewrite(slice_from(_st_s, _st_e))

needle_prp = (
    "    try:\n"
    "        from ..post_round_pipeline import run_post_round_pipeline\n"
    "\n"
    "        _pipeline_report = await run_post_round_pipeline("
)
assert_once(_streaming_body, needle_prp, "streaming post-round")
_streaming_body = _streaming_body.replace(
    needle_prp,
    "    try:\n"
    "        from ..chat_engine import _analyze_and_update_relationships\n"
    "        from ..post_round_pipeline import run_post_round_pipeline\n"
    "\n"
    "        _pipeline_report = await run_post_round_pipeline(",
)

streaming_py = '''"""Основной streaming-пайплайн раунда (decomposition.md §4.2, Milestone 5B).

``process_user_message_streaming`` вынесено из ``app/chat_engine.py`` без
изменения поведения: SSE-события, присутствие, память, отношения, повторы,
ретраи, LoRA, story, WPE-фазы — всё на месте.

Внешний контракт: ``process_user_message_streaming`` использует единую
DB-транзакцию на раунд (batch commit) и отдаёт те же SSE-события, что и раньше.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import action_resolution
from .. import crud
from .. import memory_service
from .. import ollama_client
from .. import pending_intervention
from .. import perception
from .. import round_engine
from .. import relationship_service
from .. import schemas
from .. import witness_model
from ..config import settings
from ..context_builder import ContextBuilder
from ..lora_manager import LoRAManager
from ..movement import detect_character_movement
from ..post_round_pipeline import compute_and_save_presence_for_message
from ..repetition_detector import analyze_response
from ..role_isolation import get_other_character_names
from ..stimuli import extract_stimuli

from .lora import lora_first_apply_warning, resolve_generation_model
from .session import (
    _character_is_isolated,
    _character_to_snapshot,
    _create_message_with_shadow,
    _detect_communication_channel,
    _directly_addressed_ids,
    _effective_prior_replies,
    _is_location_allowed,
    _load_location_descriptions,
    _log_generation_diagnostics,
    _message_snapshot,
    _message_to_dict,
    _parse_allowed_locations,
    _parse_known_locations,
    _scene_gate_confirms,
)
from .story import _chat_plot_text, _chat_story_block, _compute_epistemic_evidence

logger = logging.getLogger("app.chat_engine.pipeline.streaming")

'''
streaming_py += _streaming_body.rstrip() + "\n"

# __PART2__

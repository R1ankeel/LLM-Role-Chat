# ---------------------------------------------------------------------------
# regeneration.py
# ---------------------------------------------------------------------------
_rg_s, _rg_e = func_lines("regenerate_message_streaming")
_regeneration_body = rewrite(slice_from(_rg_s, _rg_e))

regeneration_py = '''"""Регенерация ответа персонажа (decomposition.md §4.2, Milestone 5B).

``regenerate_message_streaming`` вынесено из ``app/chat_engine.py`` без
изменения поведения: WPE-фазы, присутствие, память, LoRA, story — всё на месте.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import action_resolution
from .. import crud
from .. import models
from .. import ollama_client
from .. import pending_intervention
from .. import perception
from .. import relationship_service
from .. import schemas
from .. import witness_model
from ..config import settings
from ..context_builder import ContextBuilder
from ..lora_manager import LoRAManager
from ..movement import detect_character_movement
from ..post_round_pipeline import compute_and_save_presence_for_message
from ..role_isolation import get_other_character_names
from ..stimuli import extract_stimuli

from .lora import lora_first_apply_warning, resolve_generation_model
from .session import (
    _character_is_isolated,
    _create_message_with_shadow,
    _detect_communication_channel,
    _load_location_descriptions,
    _log_generation_diagnostics,
    _message_snapshot,
    _message_to_dict,
    _parse_known_locations,
)
from .story import _chat_plot_text, _chat_story_block, _compute_epistemic_evidence

logger = logging.getLogger("app.chat_engine.pipeline.regeneration")

'''
regeneration_py += _regeneration_body.rstrip() + "\n"

# ---------------------------------------------------------------------------
# facade: app/chat_engine.py
# ---------------------------------------------------------------------------
_logger_idx = find_line(
    lambda l: l.startswith("logger = logging.getLogger(__name__)"),
    what="facade logger",
)
_header_lines = list(lines[0 : _logger_idx + 1])
# Replace the one-line module docstring (first line) with a facade docstring,
# keeping the original import block and logger intact.
assert _header_lines[0].startswith(
    '"""Chat engine: process user messages, generate character replies, extract memories."""'
), "unexpected leading docstring"
_facade_doc = (
    '"""Chat engine facade: public API + relations analysis (Milestone 5B).'
    + "\n"
    + "\n"
    + "После спринта 5B (decomposition.md §4.2) streaming-ядро живёт в ``app/pipeline/``:"
    + "\n"
    + "``process_user_message_streaming`` (streaming.py), ``process_user_message`` и"
    + "\n"
    + "общие хелперы раунда (session.py), ``regenerate_message_streaming``"
    + "\n"
    + "(regeneration.py), LoRA-резолв (lora.py), story-блок и belief evidence (story.py)."
    + "\n"
    + "\n"
    + "Модуль остаётся фасадом: реэкспортирует публичный API и сохраняет контракт"
    + "\n"
    + "патчей тестов (``app.chat_engine.{asyncio, ollama_client, settings,"
    + "\n"
    + "AsyncSessionLocal, ...}``). Анализ отношений (``_analyze_and_update_relationships``"
    + "\n"
    + "и окружение) остаётся здесь до Milestone 6A (``pipeline/relations.py``)."
    + "\n"
    + '"""'
)
_header = "\n".join([_facade_doc] + _header_lines[1:]) + "\n\n"

_reexports = (
    "from .pipeline.lora import (\n"
    "    _LORA_MANAGER_DEFAULT,\n"
    "    _default_lora_manager,\n"
    "    _lora_unknown_warned_chats,\n"
    "    lora_first_apply_warning,\n"
    "    resolve_generation_model,\n"
    ")\n"
    "from .pipeline.regeneration import regenerate_message_streaming\n"
    "from .pipeline.session import (\n"
    "    _build_character_round_text,\n"
    "    _character_is_isolated,\n"
    "    _character_to_snapshot,\n"
    "    _create_message_with_shadow,\n"
    "    _detect_communication_channel,\n"
    "    _directly_addressed_ids,\n"
    "    _effective_prior_replies,\n"
    "    _is_location_allowed,\n"
    "    _load_location_descriptions,\n"
    "    _log_generation_diagnostics,\n"
    "    _message_snapshot,\n"
    "    _message_to_dict,\n"
    "    _parse_allowed_locations,\n"
    "    _parse_known_locations,\n"
    "    _scene_gate_confirms,\n"
    "    process_user_message,\n"
    ")\n"
    "from .pipeline.story import (\n"
    "    _belief_evidenced_ids,\n"
    "    _chat_plot_text,\n"
    "    _chat_story_block,\n"
    "    _compute_epistemic_evidence,\n"
    ")\n"
    "from .pipeline.streaming import process_user_message_streaming\n"
)

# Relations region: from the def of _analyze_and_update_relationships (plus any
# contiguous blank/comment lines directly above it) up to the end of
# _compute_hearsay_effective_cap. No dependency on a `# ---` banner.
_rel_start = func_lines("_analyze_and_update_relationships")[0]
while _rel_start > 0 and (
    lines[_rel_start - 1].strip() == "" or lines[_rel_start - 1].startswith("#")
):
    _rel_start -= 1
_rel_end = func_lines("_compute_hearsay_effective_cap")[1]
_relations = "\n".join(lines[_rel_start : _rel_end + 1]).rstrip() + "\n"

facade_py = _header + _reexports + "\n\n\n" + _relations

# Sanity checks on the generated facade.
_facade_defs = {
    n.name
    for n in ast.walk(ast.parse(facade_py))
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
}
assert _facade_defs == REL_FUNC_NAMES, f"facade defs mismatch: {_facade_defs}"
assert len(facade_py.splitlines()) < 1500, "facade too large"
assert "import httpx" in facade_py, "facade lost its import block"
assert "logger = logging.getLogger(__name__)" in facade_py, "facade lost logger"

# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
OUT = ROOT / "app" / "pipeline"
writes = [
    (OUT / "lora.py", lora_py),
    (OUT / "story.py", story_py),
    (OUT / "session.py", session_py),
    (OUT / "streaming.py", streaming_py),
    (OUT / "regeneration.py", regeneration_py),
    (SRC, facade_py),
]
for path, content in writes:
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({len(content.splitlines())} lines)")

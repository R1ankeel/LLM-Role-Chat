"""Story-блок и belief evidence (decomposition.md §4.2, Sprint 8 §16).

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

def _chat_plot_text(chat: Any) -> str:
    """Сюжетный текст для scene-блока (Plans/update20.md §16.1, Sprint 8).

    При включённом story (``story_enabled`` + ``chats.story_enabled``) сцена
    использует ``story_prompt`` (эволюционирующее story prompt); иначе —
    legacy ``general_prompt``. Сам ``general_prompt`` НЕ меняется.
    """
    if (
        settings.story_enabled
        and getattr(chat, "story_enabled", False)
        and (getattr(chat, "story_prompt", "") or "").strip()
    ):
        return chat.story_prompt
    return chat.general_prompt


async def _chat_story_block(db: AsyncSession, chat: Any) -> str:
    """STORY block для чата (общий для всех персонажей; Sprint 8).

    Пусто при выключенном ``story_enabled`` или ``chats.story_enabled=false``.
    Падение не роняет раунд — блок пуст.
    """
    if not settings.story_enabled or not getattr(chat, "story_enabled", False):
        return ""
    try:
        from ..plot import story_state as plot_story

        return await plot_story.build_story_block(db, chat.id)
    except Exception as exc:  # noqa: BLE001 — блок не должен ронять раунд
        logger.warning(
            "[chat_id=%d] Failed to build story block: %s", chat.id, exc
        )
        return ""


async def _compute_epistemic_evidence(
    round_snapshots: list[dict],
    viewer,
    all_characters: list,
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None,
    db: AsyncSession,
    chat_id: int,
) -> set[int]:
    """Ids of characters whose behavior ``viewer`` perceived this round.

    MVP epistemic mask (docs/relations.md §10, Sprint 2 item 10): a character
    may only learn how another treats it when there was direct or observed
    evidence of that other's behavior in the current round (mode != "none").

    Sprint 5 (§9, belief-aware): при ``beliefs_enabled`` evidence расширяется
    beliefs — id персонажей, о которых у ``viewer`` уже есть убеждение из
    прошлых раундов (mask может читать beliefs вместо «неизвестно»).
    """
    from ..chat_engine import _build_pair_relationship_context, _evidence_mode

    evidenced: set[int] = set()
    for other in all_characters:
        if other.id == viewer.id:
            continue
        ctx = _build_pair_relationship_context(
            round_snapshots,
            viewer,
            other,
            character_names,
            character_locations,
            player_id=player_id,
        )
        if _evidence_mode(ctx) in ("direct", "observed"):
            evidenced.add(other.id)
    if settings.beliefs_enabled:
        evidenced.update(
            await _belief_evidenced_ids(db, chat_id, viewer.id, character_names)
        )
    return evidenced


async def _belief_evidenced_ids(
    db: AsyncSession, chat_id: int, viewer_id: int, character_names: dict[int, str]
) -> set[int]:
    """Ids персонажей, о которых у ``viewer`` есть belief (Sprint 5, §9).

    Убеждение из прошлых раундов заменяет «неизвестно» в mask; сами beliefs
    рендерятся в блоке WHAT YOU KNOW. Пусто при отсутствии beliefs/флага.
    """
    if not settings.beliefs_enabled:
        return set()
    try:
        from .. import crud

        beliefs = await crud.get_beliefs_for_character(
            db,
            viewer_id,
            top_k=settings.beliefs_top_k,
            min_confidence=settings.beliefs_render_confidence,
        )
        name_to_id = {
            (name or "").strip().lower(): cid
            for cid, name in character_names.items()
            if name
        }
        ids = set()
        for b in beliefs:
            subject = (b.subject or "").strip().lower()
            cid = name_to_id.get(subject)
            if cid is not None and cid != viewer_id:
                ids.add(cid)
        return ids
    except Exception as exc:
        logger.warning(
            "Failed to compute belief-evidenced ids (viewer=%s): %s",
            viewer_id, exc,
        )
        return set()

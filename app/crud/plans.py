"""NPC plans (Sprint 4)."""



from __future__ import annotations



from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

async def get_active_npc_plan(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
) -> models.NpcPlan | None:
    """Активный план персонажа (active|blocked, newest first, §22).

    Один активный план на персонажа (обычно) — ``get_or_create_active_plan``
    не создаёт второй, пока предыдущий жив.
    """
    stmt = (
        select(models.NpcPlan)
        .where(
            models.NpcPlan.chat_id == chat_id,
            models.NpcPlan.character_id == character_id,
            models.NpcPlan.status.in_(["active", "blocked"]),
        )
        .order_by(models.NpcPlan.id.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_npc_plans_for_character(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    statuses: list[str] | None = None,
    limit: int = 20,
) -> list[models.NpcPlan]:
    """Планы персонажа (новые сначала, опциональный фильтр по status)."""
    stmt = (
        select(models.NpcPlan)
        .where(
            models.NpcPlan.chat_id == chat_id,
            models.NpcPlan.character_id == character_id,
        )
        .order_by(models.NpcPlan.id.desc())
    )
    if statuses:
        stmt = stmt.where(models.NpcPlan.status.in_(list(statuses)))
    stmt = stmt.limit(max(0, int(limit)))
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def create_npc_plan(
    db: AsyncSession,
    *,
    chat_id: int,
    character_id: int,
    goal: str,
    next_step: str = "",
    blocked_by: str = "",
    priority: int = 5,
    created_round_id: str | None = None,
) -> models.NpcPlan:
    """Новый долгоживущий план NPC (Plans/update20.md §22, Sprint 10)."""
    row = models.NpcPlan(
        chat_id=chat_id,
        character_id=character_id,
        goal=(goal or "")[:500],
        next_step=(next_step or "")[:500],
        blocked_by=(blocked_by or "")[:500],
        priority=max(0, min(10, int(priority))),
        status="active",
        created_round_id=created_round_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

async def update_npc_plan(
    db: AsyncSession,
    plan_id: int,
    *,
    next_step: str | None = None,
    blocked_by: str | None = None,
    priority: int | None = None,
    status: str | None = None,
) -> models.NpcPlan | None:
    """Обновить план NPC (частичное; None-поля НЕ сбрасываются)."""
    plan = await db.get(models.NpcPlan, plan_id)
    if plan is None:
        return None
    if next_step is not None:
        plan.next_step = next_step[:500]
    if blocked_by is not None:
        plan.blocked_by = blocked_by[:500]
    if priority is not None:
        plan.priority = max(0, min(10, int(priority)))
    if status is not None:
        plan.status = status
    await db.commit()
    await db.refresh(plan)
    return plan

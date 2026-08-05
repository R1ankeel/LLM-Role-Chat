"""Dynamic Story State (Plans/update20.md §16, Sprint 8/9).

Сюжет как отдельная ось: Original Plot + Current Story State + Story History
+ Phase. Write-path — пост-раунд (story_events + story_state), read-path —
блок STORY в контексте и API story state GET/PATCH (только пользователь
правит ``original_plot``).

Sprint 8: ``story_events.py`` (проекция world_events раунда) и
``story_state.py`` (story_states, phase, активные story_threads, summary).
Sprint 9: ``story_consolidation.py`` — LLM-обновление Current Story State с
валидацией (original plot diff, grounding, rollback) под benchmark gate §27.
Поздние спринты добавляют ``story_threads.py`` (Sprint 10), crisis (Sprint 11).
"""

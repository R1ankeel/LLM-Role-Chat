# AI Roleplay Chat v2 — Recommendations for Improvement

> Based on codebase analysis: 2026-07-30  
> Current state: P0/P1 largely complete; P2/P3 pending

---

## Executive Summary

The project has a **strong foundation**: witness-model perception filtering, multi-level memory (dialogue + episodic facts + per-character summaries), BM25 relevance selection, structured extraction with validation, Ollama Chat API migration, negative prompting, and robust role isolation with retry/fallback. The architecture is modular and well-tested.

**Main gaps** are in: context budget management, memory CRUD/UI, anti-mimicry for multi-character rounds, token streaming, and operational tooling (config, task queue, eval harness).

---

## Priority Matrix

| Priority | Area | Impact | Effort | Status |
|----------|------|--------|--------|--------|
| **P2** | Context Budget Manager | Prevents prompt overflow, optimizes token use | Medium | blocked |
| **P2** | Full Memory CRUD (API + UI) | User control over facts | Medium | ✅
| **P2** | Anti-mimicry for sequential generation | Character distinctiveness | Low | ✅ |
| **P2** | Token Streaming (SSE chunks) | UX: "live" feeling | Medium | ✅ |
| **P2** | Semantic Regex Hard/Soft Split | Reduce false-positive retries | Low | ✅ |
| **P2** | Per-character `min_length` | Allow short valid replies | Low | blocked |
| **P2** | Clear History Options (messages/memories/summaries) | Data hygiene | Low | ✅ |
| **P3** | pydantic-settings Config | Type-safe env config | Low | ✅ |
| **P3** | Task Queue for Memory Jobs | Reliability, observability | Medium | ✅ |
| **P3** | Batch Commit per Round | Transactional integrity | Low | ✅ |
| **P3** | Memory Consolidation Job | Dedupe/merge similar facts | Medium | ✅ |
| **P3** | Eval Harness + Golden Tests | Regression prevention | High | ✅ |
| **P3** | Vector Search / Embeddings | Long-campaign scaling | High | ✅ |
| **P3** | SceneState / World Tracking | Implicit world consistency | Medium | ✅ |


---

## Detailed Recommendations

### 1. Context Budget Manager (P2 — High Impact)

**Problem**: Prompt grows unbounded (system + character card + summary + memories + dialogue + examples + rules + isolation + negative + cue). No token accounting.

**Solution**: New module `context_budget.py`

```python
# context_budget.py
from dataclasses import dataclass
from typing import List

@dataclass
class BudgetSection:
    name: str
    content: str
    priority: int  # lower = more important
    max_chars: int | None = None

class ContextBudget:
    def __init__(self, max_total_chars: int = 120000):  # ~40k tokens RU
        self.max_total_chars = max_total_chars
        self.sections: List[BudgetSection] = []

    def add(self, name: str, content: str, priority: int, max_chars: int | None = None):
        self.sections.append(BudgetSection(name, content, priority, max_chars))

    def build(self) -> str:
        # Sort by priority, truncate lowest-priority sections first
        self.sections.sort(key=lambda s: s.priority)
        total = sum(len(s.content) for s in self.sections)
        if total <= self.max_total_chars:
            return "\n\n".join(s.content for s in self.sections if s.content)

        # Truncate from lowest priority
        for section in reversed(self.sections):
            if total <= self.max_total_chars:
                break
            if section.max_chars and len(section.content) > section.max_chars:
                excess = total - self.max_total_chars
                cut = min(excess, len(section.content) - section.max_chars)
                section.content = section.content[:-cut]
                total -= cut
        return "\n\n".join(s.content for s in self.sections if s.content)
```

**Integration**: In `ollama_client._generate_once()`, replace manual concatenation with `ContextBudget`.

**Section Priorities** (suggested):
| Priority | Section | Max Chars |
|----------|---------|-----------|
| 0 | System prompt (card + isolation) | 8000 |
| 1 | Character summary | 3000 |
| 2 | Recent dialogue (witness-filtered) | Dynamic (remaining) |
| 3 | Relevant memories (BM25 top-K) | 5000 |
| 4 | Examples (few-shot) | 2000 |
| 5 | Rules + Negative + Reinforcement | 2000 |
| 6 | Generation cue | 500 |

---

### 2. Full Memory CRUD — API + UI (P2)

**Current**: `GET /characters/{id}/memories`, `DELETE /memories/{id}`  
**Missing**: `POST /characters/{id}/memories`, `PUT /memories/{id}`

**API** (`routers/characters.py`):
```python
@router.post("/characters/{character_id}/memories", response_model=schemas.MemoryRead)
def create_memory(character_id: int, memory: schemas.MemoryCreate, db: Session = Depends(get_db)):
    # Validate character exists
    # Create memory with importance/category
    return crud.create_memory(db, memory)

@router.put("/memories/{memory_id}", response_model=schemas.MemoryRead)
def update_memory(memory_id: int, memory_update: schemas.MemoryUpdate, db: Session = Depends(get_db)):
    # Update content/importance/category
    return crud.update_memory(db, memory_id, memory_update)
```

**UI** (`static/app.js` — `renderMemoriesTab`):
- Add "Add Memory" button per character
- Inline edit for content/importance/category
- Show importance as colored badge (green ≥0.7, yellow 0.4-0.7, red <0.4)
- Category filter dropdown

**Schema additions** (already in `schemas.py`):
```python
class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    category: Optional[str] = None
```

---

### 3. Anti-Mimicry for Sequential Generation (P2)

**Problem**: Later characters in a round see earlier replies and may copy style/actions.

**Solution**: Inject anti-mimicry block in prompt for characters with `order_index > 0`.

In `prompt_builder.py`:
```python
def build_anti_mimicry_block(
    current_name: str,
    prior_replies: List[tuple[str, str]],  # (name, content)
) -> str:
    if not prior_replies:
        return ""
    lines = [
        f"В этом ходе уже ответили: {', '.join(name for name, _ in prior_replies)}.",
        "Их реплики выше — для контекста. НЕ повторяй их действия, интонацию или формулировки.",
        f"Отвечай ТОЛЬКО со своей уникальной перспективы как {current_name}.",
    ]
    return "\n---\n" + "\n".join(lines) + "\n---\n"
```

In `ollama_client._generate_once()`:
```python
# Before building messages, collect prior replies this round
prior_replies = [
    (c.name, m.content) for c, m in zip(characters, round_messages[1:])  # skip user
    if c.order_index < current_character.order_index
]
anti_mimicry = build_anti_mimicry_block(character.name, prior_replies)
# Insert into user context message after dialogue_block
```

**Alternative**: For characters not in same location (`presence == "absent"`), don't show prior replies at all — only summary.

---

### 4. Token Streaming in UI (P2)

**Backend** (`ollama_client.py`): Already yields chunks in `_stream_ollama_chat/generate`.  
**Router** (`routers/chat_engine.py`): Modify SSE to emit token events.

```python
# In _run_generation:
async for event in chat_engine.process_user_message_streaming(...):
    if event["type"] == "token":  # NEW
        await queue.put({"type": "token", "text": event["text"]})
    elif event["type"] == "response":
        await queue.put({"type": "message", "message": ...})
```

**Frontend** (`static/app.js`): Handle `type: "token"` — append to streaming message element.

```javascript
// In readSSEStream onEvent:
if (event.type === "token") {
    appendToStreamingMessage(event.text);
} else if (event.type === "message") {
    finalizeStreamingMessage(event.message);
}
```

**Note**: Keep thinking hidden — only stream `response` content.

---

### 5. Semantic Regex Hard/Soft Split (P2)

**Current**: `contains_perspective_violation()` treats all patterns equally → false positives (e.g., "он улыбнулся" flagged as thought).

**Fix** (`role_isolation.py`):
```python
_HARD_PATTERNS = [
    # Internal states of others — definitive violations
    r"\b(он|она|они)\s+(подумал|чувствовал|решил|знал|хотел)\b",
    r"\b(я\s+знаю|ты\s+думал)\s+(что|как)\b",
    # Speaking for others
    r"\b(он|она|они)\s+(скажет|ответит|сделает|пойдёт)\b",
]

_SOFT_PATTERNS = [
    # Observable actions — log only, don't retry
    r"\b(он|она|они)\s+(улыбнулся|кивнул|посмотрел|отвернулся|встал|сел)\b",
    r"\b(смотрел|глядел)\s+(на\s+него|на\s+нее|в\s+сторону)\b",
]

def contains_perspective_violation(text: str, other_names: list[str]) -> tuple[bool, bool]:
    """Returns (hard_violation, soft_violation)."""
    lowered = text.lower()
    hard = any(re.search(p, lowered) for p in _HARD_PATTERNS)
    soft = any(re.search(p, lowered) for p in _SOFT_PATTERNS)
    # Also check name + internal verb combo for hard
    for name in other_names:
        if re.search(rf"{re.escape(name.lower())}.*\b(думал|чувствовал|хотел|решил)\b", lowered):
            hard = True
    return hard, soft
```

**In `sanitize_and_validate_response`**: Only retry on hard; log soft.

---

### 6. Per-Character `min_length` (P2)

**Current**: Global `MIN_CHARACTER_RESPONSE_LENGTH = 10` rejects valid short replies ("— Нет.").

**Fix**:
1. Add `min_response_length` to `Character` model (nullable, default None)
2. In `ollama_client._generate_once()`:
```python
min_len = getattr(character, "min_response_length", None) or MIN_CHARACTER_RESPONSE_LENGTH
sanitized, is_valid = sanitize_and_validate_response(..., min_length=min_len)
```
3. Fallback in `generate()` already uses `min_length=3` — unify.

**UI**: Add field in character editor (optional, placeholder "default").

---

### 7. Clear History Options (P2)

**Current**: `DELETE /chats/{id}/messages` only clears messages + summaries.

**Extend** (`routers/chats.py`):
```python
class ClearHistoryRequest(BaseModel):
    scope: Literal["messages", "messages_memories", "full"] = "messages"

@router.delete("/{chat_id}/messages")
def clear_messages(chat_id: int, scope: str = "messages", db: Session = Depends(get_db)):
    if scope == "messages":
        crud.clear_chat_messages(db, chat_id)
    elif scope == "messages_memories":
        crud.clear_chat_messages(db, chat_id)
        crud.clear_chat_memories(db, chat_id)  # NEW
    elif scope == "full":
        crud.clear_chat_messages(db, chat_id)
        crud.clear_chat_memories(db, chat_id)
        crud.reset_character_summaries_for_chat(db, chat_id)
```

**UI**: Modal with radio buttons when clicking "Clear History".

---

### 8. pydantic-settings Config (P3)

**Current**: `config.py` with hardcoded constants + `OLLAMA_BASE_URL` in `ollama_client.py`.

**New** (`config.py`):
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout: float = 180.0

    # Generation
    default_model: str = "qwen3-coder:30b-a3b-q4_K_M"
    default_temperature: float = 0.8
    enable_thinking: bool = True
    use_chat_api: bool = True

    # Memory
    max_memories_per_character: int = 20
    recent_memories_for_prompt: int = 10
    memory_relevance_top_k: int = 5
    enable_relevant_memory_selection: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    # Summary
    summary_interval_messages: int = 20
    summary_max_paragraphs: int = 3

    # Witness
    enable_witness_filter: bool = True
    witness_mentioned_snippet_len: int = 120

    # Repetition
    repetition_detection_enabled: bool = True
    repetition_window_size: int = 6
    repetition_threshold: float = 0.72

    # Rate limit
    rate_limit_seconds: int = 5

settings = Settings()
```

**Migration**: Replace all `from config import X` with `from config import settings` → `settings.X`.

---

### 9. Task Queue for Memory Jobs (P3)

**Current**: `asyncio.create_task(memory_service.process_post_round(...))` — no retry, no status, no observability.

**Option A** (lightweight): Add `tenacity` retry + structured logging
**Option B** (robust): Integrate `arq` or `dramatiq` with Redis

**Minimal improvement** (`memory_service.py`):
```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _extract_and_save_memories_with_retry(...):
    await _extract_and_save_memories(...)

async def process_post_round(...):
    try:
        await _extract_and_save_memories_with_retry(...)
        await _maybe_update_summaries(...)
    except Exception:
        logger.exception("Post-round processing failed after retries")
        # Could persist failed job to DB for manual retry
```

---

### 10. Batch Commit per Round (P3)

**Current**: Each message commits separately in `chat_engine.py` → partial round on failure.

**Fix**: Wrap round in single transaction.

```python
# In process_user_message_streaming:
async with db.begin():  # SQLAlchemy 2.0 async session
    # Create user message
    # For each character: generate, create message, compute presence
    # All flushed at end
    await db.commit()
```

**Note**: Requires `AsyncSession` — migrate from sync `SessionLocal` to async engine.

---

### 11. Memory Consolidation Job (P3)

**Current**: Inline Jaccard dedup at extraction time only.

**Scheduled job** (daily/weekly via APScheduler or cron):
```python
async def consolidate_memories(db: Session):
    # For each character:
    #   Load all memories
    #   Cluster by embedding similarity (or BM25 + Jaccard)
    #   Merge clusters: keep highest importance, concatenate facts
    #   Update category/importance
    #   Delete merged
```

**Prerequisite**: Add `last_accessed_at` and `source_message_ids` to `Memory` model for smarter merging.

---

### 12. Eval Harness + Golden Tests (P3)

**Structure**:
```
tests/eval/
├── scenarios/
│   ├── isolation_basic.yaml
│   ├── memory_recall.yaml
│   ├── style_consistency.yaml
│   └── witness_filter.yaml
├── metrics.py
└── run_eval.py
```

**Scenario format** (YAML):
```yaml
name: "isolation_basic"
chat:
  prompt: "Two knights argue in a tavern."
  characters:
    - name: "Sir Gallant"
      personality: "Honorable, verbose"
    - name: "Sir Cynic"
      personality: "Sarcastic, brief"
turns:
  - user: "What do you think of the king?"
  - expect:
      character: "Sir Gallant"
      must_contain: ["honor", "duty"]
      must_not_contain: ["Sir Cynic thinks", "he believes"]
```

**Metrics**:
- Isolation violation rate (foreign markers + perspective regex)
- Fact recall@5 (query character memory after N turns)
- Style similarity (embedding vs example_messages)
- Silence rate (% rounds with placeholder)

**CI**: Nightly job against real Ollama (optional).

---

### 13. Vector Search / Embeddings (P3 — Long Campaigns)

**When**: BM25 + 20 facts/character insufficient (campaigns > 500 messages).

**Approach**:
- Add `embedding` column to `Memory` (BLOB/ARRAY or separate table)
- Use local embedding model (e.g., `bge-m3`, `e5-small`) via Ollama or `sentence-transformers`
- Hybrid retrieval: BM25 (lexical) + cosine (semantic) → RRF fusion
- Background job: embed new memories on creation

---

### 14. SceneState / World Tracking (P3)

**Problem**: "Where are we? What time? Who's present?" lost in history.

**New model** (`models.py`):
```python
class SceneState(Base):
    __tablename__ = "scene_states"
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), primary_key=True)
    location: Mapped[str] = mapped_column(default="")
    time_of_day: Mapped[str] = mapped_column(default="")
    present_character_ids: Mapped[str] = mapped_column(default="[]")  # JSON
    custom_state: Mapped[str] = mapped_column(default="{}")  # JSON for extensibility
    updated_at: Mapped[datetime] = mapped_column(onupdate=datetime.utcnow)
```

**Update** after each round via LLM call (structured output) or explicit system message.

**Use**: Inject into `<scene>` block, inform witness filter defaults.

---

## Code Quality Improvements

| Area | Current | Target |
|------|---------|--------|
| Type hints | Partial | 100% (mypy strict) |
| Docstrings | Minimal | Google-style on all public fns |
| Logging | `logger.info/warning` | Structured (structlog) + correlation IDs |
| Error handling | RuntimeError | Custom exceptions + HTTP mapping |
| Tests | Unit + integration | + golden prompt snapshots + eval harness |

---

## Migration Checklist (v1 → v2)

- [ ] Add `context_budget.py` and integrate in `ollama_client`
- [ ] Implement memory POST/PUT endpoints + UI
- [ ] Add anti-mimicry block in prompt builder
- [ ] Enable token streaming in SSE + frontend
- [ ] Split semantic regex hard/soft
- [ ] Add per-character `min_response_length`
- [ ] Extend clear history with scope options
- [ ] Migrate to `pydantic-settings` config
- [ ] Add tenacity retry to memory jobs
- [ ] Migrate to async SQLAlchemy + batch commit
- [ ] Add `last_accessed_at`, `source_message_ids` to Memory
- [ ] Write consolidation job
- [ ] Build eval harness with 5+ scenarios
- [ ] Add golden-file tests for prompt builder
- [ ] (Optional) Vector search prototype

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Context budget breaks existing prompts | Medium | High | Feature flag; gradual rollout; golden tests |
| Memory CRUD introduces duplicate facts | Low | Medium | Validation on create/update; unique constraint on (char_id, content_hash) |
| Token streaming breaks SSE contract | Low | Medium | Versioned API (`/api/v2/...`); fallback to non-stream |
| Async migration breaks DB | High | High | Staged: new engine parallel, migrate routers one by one |
| Eval harness flaky on local Ollama | High | Medium | Mock mode for CI; real model only nightly |

---

## File Map for Implementation

```
ai-roleplay-chat/
├── config.py                    → pydantic-settings
├── context_budget.py            → NEW
├── memory_service.py            → + retry, consolidation job
├── ollama_client.py             → + context budget, token streaming
├── prompt_builder.py            → + anti_mimicry_block
├── role_isolation.py            → hard/soft regex split
├── models.py                    → + min_response_length, SceneState, Memory fields
├── schemas.py                   → + MemoryCreate/Update, ClearHistoryRequest
├── database.py                  → async engine + session
├── crud.py                      → + clear_chat_memories, update_memory
├── routers/
│   ├── chats.py                 → + clear scope param
│   ├── characters.py            → + memory CRUD
│   └── chat_engine.py           → + token streaming SSE
├── static/app.js                → + token streaming UI, memory CRUD UI
├── tests/
│   ├── test_context_budget.py   → NEW
│   ├── test_memory_crud.py      → NEW
│   ├── eval/                    → NEW
│   └── golden/                  → NEW (prompt snapshots)
└── .env.example                 → NEW
```

---

## Next Steps (Suggested Order)

1. **Week 1**: Context Budget Manager + golden prompt tests (foundation for all)
2. **Week 2**: Memory CRUD (API + UI) + Clear History options
3. **Week 3**: Anti-mimicry + Semantic regex hard/soft + Per-character min_length
4. **Week 4**: Token Streaming (backend + frontend)
5. **Week 5**: pydantic-settings config + async DB migration + batch commit
6. **Week 6**: Task queue retry + consolidation job + eval harness
7. **Ongoing**: Vector search prototype (spike), SceneState design
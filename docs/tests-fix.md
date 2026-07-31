# Test Failures Analysis and Fixes

## Summary
- **Total tests run**: 135
- **Passed**: 116
- **Failed**: 15
- **Errors**: 2
- **Warnings**: ~484 (mostly deprecation and unawaited coroutine warnings)

---

## Category 1: Missing `await` on Async Functions (11 failures)

**Root Cause**: Test code calls async CRUD functions (`create_characters`, `create_character`, `get_characters_by_chat`) without `await`, returning coroutine objects instead of actual results.

### test_memory_service.py (5 failures)

| Test | Error | Fix |
|------|-------|-----|
| `test_summary_not_triggered_below_threshold` | `TypeError: 'coroutine' object is not iterable` on `crud.get_characters_by_chat()` | Add `await` before `crud.get_characters_by_chat()` |
| `test_summary_triggered_at_threshold` | `TypeError: 'coroutine' object is not subscriptable` on `create_characters()` | Add `await` before `create_characters()` |
| `test_summary_watermark_advances` | `TypeError: 'coroutine' object is not subscriptable` on `create_characters()` | Add `await` before `create_characters()` |
| `test_extract_and_save_stores_importance_category` | `TypeError: 'coroutine' object is not subscriptable` on `create_characters()` | Add `await` before `create_characters()` |
| `test_eviction_prefers_low_importance` | `TypeError: 'coroutine' object is not subscriptable` on `create_characters()` | Add `await` before `create_characters()` |

### test_memory_perception.py (6 failures)

| Test | Error | Fix |
|------|-------|-----|
| `test_cross_location_memory_extraction_skip` | `AttributeError: 'coroutine' object has no attribute 'id'` | Add `await` before `create_characters()` |
| `test_first_person_pronoun_no_leak_to_remote` | `AttributeError: 'coroutine' object has no attribute 'id'` | Add `await` before `create_characters()` |
| `test_information_transfer_after_telling` | `AttributeError: 'coroutine' object has no attribute 'id'` on `maxim` | Add `await` before `create_character()` for `maxim` |
| `test_memory_isolation_between_characters` | `TypeError: cannot unpack non-iterable coroutine object` | Add `await` before `create_characters()` |
| `test_memory_retrieval_in_prompt_block` | `TypeError: cannot unpack non-iterable coroutine object` | Add `await` before `create_characters()` |
| `test_bad_llm_fact_for_non_witness_grounding` | `AttributeError: 'coroutine' object has no attribute 'id'` on `alina` | Add `await` before `create_character()` for `alina` |

---

## Category 2: Database Lock Errors (3 failures/errors in test_memory_crud.py)

**Root Cause**: SQLite database locking due to concurrent test execution or improper connection cleanup. Tests share the same database file and connections aren't properly closed between tests.

### Affected Tests

| Test | Error |
|------|-------|
| `test_clear_chat_memories_scope` | `sqlite3.OperationalError: database is locked` |
| `test_clear_chat_full_scope` | `sqlite3.OperationalError: database is locked` (ERROR at setup) |
| `test_clear_chat_invalid_scope` | `sqlite3.OperationalError: database is locked` (ERROR at setup) |

### Recommended Fixes
1. **Use in-memory SQLite per test**: Configure test fixtures to use `:memory:` database or unique file per test
2. **Proper async session cleanup**: Ensure `db_session.close()` is awaited in fixtures
3. **Isolate test database**: Use `pytest-asyncio` with function-scoped fixtures that create/drop tables per test
4. **Check `conftest.py`**: Verify `db_session` fixture properly yields and closes connections

---

## Category 3: External Dependency - Ollama Not Running (2 failures in test_repetition_detector.py)

**Root Cause**: Tests require a running Ollama instance at `http://localhost:11434` but it's not available.

### Affected Tests

| Test | Error |
|------|-------|
| `test_generate_repetition_retry_with_feedback` | `RuntimeError: Ollama недоступен. Проверьте, запущен ли сервер...` |
| `test_repetition_retry_limit` | `RuntimeError: Ollama недоступен. Проверьте, запущен ли сервер...` |

### Recommended Fixes
1. **Mock Ollama client**: Use `unittest.mock` to mock `ollama_client.generate()` for unit tests
2. **Integration test marker**: Mark these as `@pytest.mark.integration` and skip by default
3. **Testcontainers**: Use testcontainers to spin up Ollama for CI
4. **Conditional skip**: Add `@pytest.mark.skipif(not ollama_available(), reason="Ollama not running")`

---

## Category 4: Unawaited Coroutines in Production Code (Warnings)

These are **runtime warnings** from SQLAlchemy detecting coroutines that were created but never awaited. They indicate potential bugs in the application code, not just tests.

### Locations
- `app/crud.py:859` - `datetime.utcnow()` deprecation
- `app/crud.py:1125` - `datetime.utcnow()` deprecation
- `app/crud.py:556` - `datetime.utcnow()` deprecation
- `app/crud.py:793` - `datetime.utcnow()` deprecation
- `app/crud.py:585` - `datetime.utcnow()` deprecation
- `app/crud.py:675` - `datetime.utcnow()` deprecation
- `app/task_queue.py:138` - `datetime.utcnow()` deprecation
- `app/chat_engine.py` - `_analyze_and_update_relationships` and `process_post_round` coroutines not awaited
- `app/generation_tracker.py` - Session transaction state issues

### Fix for datetime.utcnow()
Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` (requires `from datetime import timezone`)

### Fix for unawaited coroutines in chat_engine
Ensure `_analyze_and_update_relationships` and `process_post_round` are properly awaited where called.

---

## Priority Order for Fixes

1. **HIGH** - Category 1 (Missing awaits in tests): Quick fixes, unblocks 11 tests
2. **HIGH** - Category 2 (DB locking): Fix test infrastructure, unblocks 3 tests
3. **MEDIUM** - Category 3 (Ollama dependency): Mock or mark as integration tests
4. **MEDIUM** - Category 4 (Production code warnings): Fix datetime deprecations and unawaited coroutines

---

## Files to Modify (Test Code Only)

Based on the analysis, the following test files need fixes:
- `tests/test_memory_service.py` - 5 tests need `await` added
- `tests/test_memory_perception.py` - 6 tests need `await` added
- `tests/test_memory_crud.py` - Fixture/database configuration
- `tests/test_repetition_detector.py` - Mock Ollama or add skip markers
- `tests/conftest.py` - Fix database session fixture for proper isolation
"""Tests for global LLM request serialization (one Ollama request at a time).

Covers ``app.ollama_client.llm_request`` and the lock-holding behaviour of the
streaming / non-streaming call sites: a concurrent caller must not start until
the previous request has fully completed (including the whole stream).
"""

import asyncio
import json
from unittest.mock import MagicMock

import httpx
import pytest

from app import ollama_client


async def _wait_until(predicate, timeout=2.0, step=0.005) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


# ---------------------------------------------------------------------------
# llm_request helper semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_request_serializes_concurrent_callers():
    """Concurrent llm_request blocks never overlap: max_active == 1."""
    active = 0
    max_active = 0
    completed = []

    async def worker(name):
        nonlocal active, max_active
        async with ollama_client.llm_request(name, "/api/chat"):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            completed.append(name)
            active -= 1

    await asyncio.gather(*(worker(f"m{i}") for i in range(5)))

    assert max_active == 1
    assert sorted(completed) == [f"m{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_llm_request_fifo_order():
    """Waiters are served in FIFO order (asyncio.Lock is fair)."""
    order = []

    async def worker(name, hold):
        async with ollama_client.llm_request(name, "/api/chat"):
            order.append(name)
            if hold:
                await asyncio.sleep(hold)

    t1 = asyncio.create_task(worker("first", 0.1))
    await asyncio.sleep(0.02)
    t2 = asyncio.create_task(worker("second", 0.0))
    await asyncio.sleep(0)
    t3 = asyncio.create_task(worker("third", 0.0))
    await asyncio.gather(t1, t2, t3)

    assert order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_llm_request_releases_on_exception():
    """The lock must be released when the wrapped block raises."""
    with pytest.raises(RuntimeError):
        async with ollama_client.llm_request("m", "/api/chat"):
            raise RuntimeError("boom")

    entered = []
    async with ollama_client.llm_request("m2", "/api/chat"):
        entered.append(True)
    assert entered == [True]


def test_llm_lock_is_per_event_loop():
    """One lock per event loop: same loop -> same lock, fresh loop -> new lock."""
    async def probe(store):
        store.append(ollama_client._llm_lock_for())
        store.append(ollama_client._llm_lock_for())

    loop_a = asyncio.new_event_loop()
    try:
        locks_a = []
        loop_a.run_until_complete(probe(locks_a))
        assert locks_a[0] is locks_a[1]
    finally:
        loop_a.close()

    loop_b = asyncio.new_event_loop()
    try:
        locks_b = []
        loop_b.run_until_complete(probe(locks_b))
        assert locks_b[0] is locks_b[1]
        assert locks_b[0] is not locks_a[0]
    finally:
        loop_b.close()


# ---------------------------------------------------------------------------
# Streaming call sites hold the lock for the whole exchange
# ---------------------------------------------------------------------------


class _SlowStreamResponse:
    status_code = 200

    def __init__(self, lines, delay=0.05):
        self._lines = lines
        self._delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            await asyncio.sleep(self._delay)
            yield line


@pytest.mark.asyncio
async def test_stream_ollama_chat_holds_lock_for_whole_stream():
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "A"}}),
        json.dumps({"message": {"role": "assistant", "content": "B"}}),
        json.dumps({"done": True}),
    ]
    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(return_value=_SlowStreamResponse(lines, delay=0.05))

    gen = ollama_client._stream_ollama_chat(
        client, "test-model", [{"role": "user", "content": "hi"}], temperature=0.8
    )
    first = await anext(gen)
    assert first["type"] == "token"

    contender_acquired = []

    async def contender():
        async with ollama_client.llm_request("other-model", "/api/chat"):
            contender_acquired.append(True)

    task = asyncio.create_task(contender())
    await asyncio.sleep(0.02)
    assert not contender_acquired, "lock must be held while the stream is active"

    remaining = [ev async for ev in gen]
    assert remaining[-1]["type"] == "complete"
    assert remaining[-1]["text"] == "AB"

    await asyncio.wait_for(task, timeout=5.0)
    assert contender_acquired, "lock must be released after the stream finishes"


@pytest.mark.asyncio
async def test_stream_ollama_generate_holds_lock_for_whole_stream():
    lines = [
        json.dumps({"response": "A"}),
        json.dumps({"response": "B"}),
        json.dumps({"done": True}),
    ]
    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(return_value=_SlowStreamResponse(lines, delay=0.05))

    gen = ollama_client._stream_ollama_generate(
        client, "test-model", "hello", temperature=0.8
    )
    first = await anext(gen)
    assert first["type"] == "token"

    contender_acquired = []

    async def contender():
        async with ollama_client.llm_request("other-model", "/api/generate"):
            contender_acquired.append(True)

    task = asyncio.create_task(contender())
    await asyncio.sleep(0.02)
    assert not contender_acquired

    remaining = [ev async for ev in gen]
    assert remaining[-1]["type"] == "complete"
    assert remaining[-1]["text"] == "AB"

    await asyncio.wait_for(task, timeout=5.0)
    assert contender_acquired


@pytest.mark.asyncio
async def test_stream_lock_released_when_generator_closed_early():
    """Closing the generator early (SSE disconnect) releases the lock too."""
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "A"}}),
        json.dumps({"message": {"role": "assistant", "content": "B"}}),
        json.dumps({"done": True}),
    ]
    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(return_value=_SlowStreamResponse(lines, delay=0.05))

    gen = ollama_client._stream_ollama_chat(
        client, "test-model", [{"role": "user", "content": "hi"}], temperature=0.8
    )
    first = await anext(gen)
    assert first["type"] == "token"
    await gen.aclose()  # simulate client disconnect

    entered = []

    async def contender():
        async with ollama_client.llm_request("other-model", "/api/chat"):
            entered.append(True)

    await asyncio.wait_for(contender(), timeout=5.0)
    assert entered == [True]


# ---------------------------------------------------------------------------
# Non-streaming call site holds the lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_ollama_chat_holds_lock_while_request_in_flight():
    entered = []

    async def fake_post(url, json=None, **kwargs):
        entered.append(url)
        await asyncio.sleep(0.05)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={"message": {"role": "assistant", "content": "hi"}}
        )
        return resp

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post

    t1 = asyncio.create_task(
        ollama_client._call_ollama_chat(
            client, "model", [{"role": "user", "content": "x"}]
        )
    )
    assert await _wait_until(lambda: bool(entered))

    contender_acquired = []

    async def contender():
        async with ollama_client.llm_request("other-model", "/api/chat"):
            contender_acquired.append(True)

    t2 = asyncio.create_task(contender())
    await asyncio.sleep(0.02)
    assert not contender_acquired, "lock must be held while the request is in flight"

    result = await t1
    assert result == "hi"

    await asyncio.wait_for(t2, timeout=5.0)
    assert contender_acquired

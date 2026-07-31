"""Unit tests for the token counter (estimated vs exact, caching)."""

import logging

import pytest

from app.token_counter import (
    EstimatedTokenCounter,
    ExactTokenCounter,
    MESSAGE_OVERHEAD_TOKENS,
    get_token_counter,
)


def test_estimated_counter_empty_text_is_zero():
    counter = EstimatedTokenCounter()
    assert counter.count("") == 0
    assert counter.count(None) == 0


def test_estimated_counter_nonempty_is_at_least_one():
    counter = EstimatedTokenCounter()
    assert counter.count("hello world") >= 1
    assert counter.count("привет мир") >= 1


def test_estimated_counter_cjk_is_denser_per_char():
    counter = EstimatedTokenCounter()
    cjk = counter.count("龙和凤")
    latin = counter.count("abc")
    assert cjk >= latin


def test_estimated_counter_count_messages_adds_overhead():
    counter = EstimatedTokenCounter()
    messages = [{"content": "Привет"}, {"content": "Как дела?"}]
    content = counter.count("Привет") + counter.count("Как дела?")
    assert counter.count_messages(messages) == content + 2 * MESSAGE_OVERHEAD_TOKENS
    assert counter.count_messages([]) == 0


def test_exact_counter_falls_back_to_estimated_when_no_tiktoken():
    counter = ExactTokenCounter("o200k_base")
    assert counter.mode == "estimated"
    assert counter.count("Привет мир") == EstimatedTokenCounter().count("Привет мир")


def test_get_token_counter_is_cached():
    assert get_token_counter() is get_token_counter()


def test_token_counter_mode_reported(caplog, monkeypatch):
    from app import token_counter as tc

    monkeypatch.setattr(tc, "_counter", None)
    with caplog.at_level(logging.INFO, logger="token_counter"):
        counter = tc.get_token_counter()
        assert counter.mode in ("estimated", "exact")
    monkeypatch.setattr(tc, "_counter", None)
    assert any("mode=" in record.message for record in caplog.records)


def test_estimated_count_is_deterministic():
    counter = EstimatedTokenCounter()
    assert counter.count("Токен-осознанный контекст") == counter.count(
        "Токен-осознанный контекст"
    )

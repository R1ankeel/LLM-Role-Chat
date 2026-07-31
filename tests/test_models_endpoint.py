"""API tests for GET /api/models (list of models loaded in Ollama)."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeOllamaClient:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None, error: bool = False):
        self._status_code = status_code
        self._payload = payload
        self._error = error

    async def get(self, url: str, **kwargs) -> _FakeResp:
        if self._error:
            raise httpx.ConnectError("connection refused")
        return _FakeResp(self._status_code, self._payload)


@pytest.fixture
def client():
    app.state.ollama_client = _FakeOllamaClient()
    with TestClient(app) as c:
        yield c
    app.state.ollama_client = None


def test_list_models_returns_sorted_names(client):
    app.state.ollama_client = _FakeOllamaClient(
        payload={"models": [
            {"name": "qwen3-coder:30b-a3b-q4_K_M", "size": 1},
            {"name": "bge-m3", "size": 1},
            {"name": "llama3.1:8b", "size": 1},
        ]}
    )
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": ["bge-m3", "llama3.1:8b", "qwen3-coder:30b-a3b-q4_K_M"]}


def test_list_models_deduplicates(client):
    app.state.ollama_client = _FakeOllamaClient(
        payload={"models": [{"name": "qwen3"}, {"name": "qwen3"}]}
    )
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": ["qwen3"]}


def test_list_models_empty(client):
    app.state.ollama_client = _FakeOllamaClient(payload={"models": []})
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": []}


def test_list_models_ollama_down(client):
    app.state.ollama_client = _FakeOllamaClient(error=True)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["error"]


def test_list_models_non_200(client):
    app.state.ollama_client = _FakeOllamaClient(status_code=503)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["error"]

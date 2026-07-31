"""Точка входа для запуска `uvicorn main:app` (приложение в пакете app/)."""

from app.main import app

__all__ = ["app"]

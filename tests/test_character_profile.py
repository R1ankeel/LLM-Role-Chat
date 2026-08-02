"""API tests for character profile fields (Этап A, docs/Profile.docx).

Covers the model/schema/migration stage: `appearance` and `avatar_url` fields,
`temperature` range validation (0–2), and that `avatar_url` cannot be set at
creation (files are loaded only through the upload endpoint — Этап B).
"""

from __future__ import annotations

import io
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import avatar_service, crud, schemas
from app.config import settings
from app.database import get_async_db
from app.routers.characters import router as characters_router


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(characters_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _chat_and_character(db_engine) -> tuple[int, int]:
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as db:
        chat = await crud.create_chat(
            db, schemas.ChatCreate(name="Test", general_prompt="scene")
        )
        char = await crud.create_character(
            db, chat.id, schemas.CharacterCreate(name="Alice", order_index=1)
        )
        return chat.id, char.id


class TestCharacterProfileFields:
    async def test_create_character_has_appearance_and_empty_avatar(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            chat = await crud.create_chat(
                db, schemas.ChatCreate(name="Test", general_prompt="scene")
            )

        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/chats/{chat.id}/characters",
                json={
                    "name": "Alice",
                    "appearance": "Tall, red hair",
                    "avatar_url": "/static/avatars/evil.png",
                    "avatar_crop": '{"scale": 2, "positionX": 0.5, "positionY": -0.5}',
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["appearance"] == "Tall, red hair"
            # avatar_url при создании не задаётся (только через upload endpoint)
            assert data["avatar_url"] == ""
            # avatar_crop тоже задаётся только вместе с файлом аватара
            assert data["avatar_crop"] == ""

    async def test_update_appearance_persists(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}",
                json={"appearance": "Silver hair, green eyes"},
            )
            assert resp.status_code == 200
            assert resp.json()["appearance"] == "Silver hair, green eyes"

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.appearance == "Silver hair, green eyes"

    async def test_update_avatar_url_persists(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}",
                json={"avatar_url": "/static/avatars/1-123.png"},
            )
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] == "/static/avatars/1-123.png"

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.avatar_url == "/static/avatars/1-123.png"

    async def test_temperature_out_of_range_rejected(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            for bad_temp in (2.5, -0.1):
                resp = await client.put(
                    f"/characters/{char_id}", json={"temperature": bad_temp}
                )
                assert resp.status_code == 422, f"temperature={bad_temp}"

    async def test_temperature_within_range_accepted(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}", json={"temperature": 1.35}
            )
            assert resp.status_code == 200
            assert resp.json()["temperature"] == 1.35

    async def test_update_avatar_crop_persists(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)
        crop_json = '{"scale": 1.5, "positionX": 0.25, "positionY": -0.75}'

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}", json={"avatar_crop": crop_json}
            )
            assert resp.status_code == 200
            assert resp.json()["avatar_crop"] == crop_json

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.avatar_crop == crop_json

    async def test_update_avatar_crop_invalid_rejected(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        bad_crops = [
            "not json",
            '{"scale": 2}',
            '{"scale": 0.5, "positionX": 0, "positionY": 0}',
            '{"scale": 9, "positionX": 0, "positionY": 0}',
            '{"scale": 1, "positionX": 1.5, "positionY": 0}',
            '[1, 2, 3]',
        ]
        async with await _make_client(session_factory) as client:
            for crop in bad_crops:
                resp = await client.put(
                    f"/characters/{char_id}", json={"avatar_crop": crop}
                )
                assert resp.status_code == 422, f"avatar_crop={crop}"

    async def test_update_avatar_crop_clear(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)
        crop_json = '{"scale": 1.5, "positionX": 0, "positionY": 0}'

        async with await _make_client(session_factory) as client:
            set_resp = await client.put(
                f"/characters/{char_id}", json={"avatar_crop": crop_json}
            )
            assert set_resp.status_code == 200
            clear_resp = await client.put(
                f"/characters/{char_id}", json={"avatar_crop": ""}
            )
            assert clear_resp.status_code == 200
            assert clear_resp.json()["avatar_crop"] == ""


def _make_image_bytes(size=(120, 80), color=(200, 30, 30), fmt="PNG") -> bytes:
    """Render a tiny real image into memory (used as upload payload)."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _avatar_files(tmp_path: Path, character_id: int) -> list[Path]:
    return sorted(tmp_path.glob(f"{character_id}-*"))


class TestCharacterAvatar:
    """Avatar upload/delete endpoints (Этап B, docs/Profile.docx §27)."""

    async def test_upload_avatar_success(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("avatar.png", _make_image_bytes(), "image/png")},
            )
            assert resp.status_code == 200, resp.text
            avatar_url = resp.json()["avatar_url"]
            assert avatar_url.startswith("/static/avatars/")

            files = _avatar_files(tmp_path, char_id)
            assert len(files) == 1
            # Saved file is a valid WebP (converted + EXIF stripped).
            with Image.open(files[0]) as img:
                assert img.format == "WEBP"
            assert avatar_url == f"/static/avatars/{files[0].name}"

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.avatar_url == avatar_url

    async def test_upload_avatar_unknown_character_404(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                "/characters/999999/avatar",
                files={"file": ("a.png", _make_image_bytes(), "image/png")},
            )
            assert resp.status_code == 404

    async def test_upload_avatar_invalid_type_400(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("a.txt", b"definitely not an image", "text/plain")},
            )
            assert resp.status_code == 400
            assert _avatar_files(tmp_path, char_id) == []

    async def test_upload_avatar_too_large_400(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        # Valid PNG magic, but payload exceeds the 5 MB limit.
        oversized = _make_image_bytes() + b"\x00" * (settings.avatar_max_size_mb * 1024 * 1024)
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("big.png", oversized, "image/png")},
            )
            assert resp.status_code == 400
            assert _avatar_files(tmp_path, char_id) == []

    async def test_replace_avatar_deletes_old_file(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            first = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("one.png", _make_image_bytes(), "image/png")},
            )
            assert first.status_code == 200
            first_file = _avatar_files(tmp_path, char_id)[0]

            second = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("two.png", _make_image_bytes(), "image/png")},
            )
            assert second.status_code == 200
            assert second.json()["avatar_url"] != first.json()["avatar_url"]

            files = _avatar_files(tmp_path, char_id)
            assert len(files) == 1
            assert files[0].name != first_file.name

    async def test_upload_avatar_resets_crop(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)
        crop_json = '{"scale": 1.5, "positionX": 0, "positionY": 0}'

        async with await _make_client(session_factory) as client:
            set_resp = await client.put(
                f"/characters/{char_id}", json={"avatar_crop": crop_json}
            )
            assert set_resp.status_code == 200

            resp = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("avatar.png", _make_image_bytes(), "image/png")},
            )
            assert resp.status_code == 200, resp.text
            # Новый файл получает собственные параметры кадрирования: старые сброшены
            assert resp.json()["avatar_crop"] == ""

    async def test_delete_avatar_resets_crop(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)
        crop_json = '{"scale": 1.5, "positionX": 0, "positionY": 0}'

        async with await _make_client(session_factory) as client:
            await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("avatar.png", _make_image_bytes(), "image/png")},
            )
            set_resp = await client.put(
                f"/characters/{char_id}", json={"avatar_crop": crop_json}
            )
            assert set_resp.status_code == 200

            resp = await client.delete(f"/characters/{char_id}/avatar")
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] == ""
            assert resp.json()["avatar_crop"] == ""
            assert _avatar_files(tmp_path, char_id) == []

    async def test_delete_avatar_removes_file_and_url(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            uploaded = await client.post(
                f"/characters/{char_id}/avatar",
                files={"file": ("a.png", _make_image_bytes(), "image/png")},
            )
            assert uploaded.status_code == 200
            assert _avatar_files(tmp_path, char_id)

            resp = await client.delete(f"/characters/{char_id}/avatar")
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] == ""
            assert _avatar_files(tmp_path, char_id) == []

    async def test_delete_avatar_unknown_character_404(self, db_engine, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "avatar_dir", str(tmp_path))
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.delete("/characters/999999/avatar")
            assert resp.status_code == 404

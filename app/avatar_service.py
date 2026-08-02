"""Avatar storage, validation and image processing (Этап B, docs/Profile.docx).

Avatars live on disk under ``settings.avatar_dir`` (default
``app/static/avatars``, already served by FastAPI on ``/static``). Each file is
named ``{character_id}-{stamp}.webp`` — the stamp is a random hex prefix so a
fresh upload busts browser/client caches. Image bytes are never stored in the
database; ``Character.avatar_url`` only holds the relative URL.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from .config import settings

AVATAR_URL_PREFIX = "/static/avatars"
# Output format: WebP keeps transparency (unlike JPEG) and compresses well.
_OUTPUT_FORMAT = "WEBP"
_OUTPUT_EXT = "webp"

# Magic-byte signatures — we never trust the browser's MIME type.
_MAGIC_SIGNATURES: dict[str, bytes] = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpeg": b"\xff\xd8\xff",
    "webp": b"RIFF",
}


def avatar_dir_path() -> Path:
    """Resolve the configured avatar directory to an absolute path.

    Relative ``AVATAR_DIR`` values are resolved against the project root (the
    parent of the ``app`` package) so the default ``app/static/avatars`` works
    regardless of the current working directory.
    """
    path = Path(settings.avatar_dir)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def ensure_avatar_dir() -> Path:
    """Create the avatar directory if it does not exist. Returns its path."""
    directory = avatar_dir_path()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def detect_image_format(data: bytes) -> str | None:
    """Detect the image format from the file's leading bytes.

    Returns ``"png"``, ``"jpeg"`` or ``"webp"``, or ``None`` when the data does
    not match any allowed signature.
    """
    if data.startswith(_MAGIC_SIGNATURES["png"]):
        return "png"
    if data.startswith(_MAGIC_SIGNATURES["jpeg"]):
        return "jpeg"
    # WebP container: "RIFF" + size (4 bytes) + "WEBP".
    if (
        data[:4] == _MAGIC_SIGNATURES["webp"]
        and len(data) >= 12
        and data[8:12] == b"WEBP"
    ):
        return "webp"
    return None


def _character_files(character_id: int) -> list[Path]:
    """All avatar files on disk belonging to a character."""
    directory = avatar_dir_path()
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{character_id}-*"))


def remove_avatar(character_id: int) -> None:
    """Delete every avatar file belonging to a character (best-effort)."""
    for path in _character_files(character_id):
        try:
            path.unlink()
        except OSError:
            pass


def _process_image(content: bytes) -> bytes:
    """Decode, normalize and re-encode the image into WebP bytes.

    Raises ``ValueError`` when the bytes are not a decodable image. EXIF is
    dropped by the fresh re-encode.
    """
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Файл повреждён или не является изображением") from exc

    # Normalize the color mode so re-encoding never fails on unsupported modes.
    if image.mode not in ("RGB", "RGBA"):
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        else:
            image = image.convert("RGB")

    max_dimension = settings.avatar_max_dimension
    width, height = image.size
    if max(width, height) > max_dimension:
        scale = max_dimension / max(width, height)
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.LANCZOS,
        )

    out = io.BytesIO()
    image.save(out, format=_OUTPUT_FORMAT, quality=90)
    return out.getvalue()


async def validate_and_save(file: UploadFile, character_id: int) -> str:
    """Validate an uploaded avatar, persist it and return its relative URL.

    Checks (in order): file size against ``avatar_max_size_mb``, magic bytes
    against ``avatar_allowed_types``, decodability by Pillow. On success the
    image is resized/re-encoded and written as ``{character_id}-{stamp}.webp``,
    replacing any previous avatar file of the character. Raises ``ValueError``
    for any validation/processing failure — the router maps it to HTTP 400.
    """
    content = await file.read()

    max_bytes = settings.avatar_max_size_mb * 1024 * 1024
    if not content:
        raise ValueError("Файл пуст")
    if len(content) > max_bytes:
        raise ValueError(
            f"Файл больше {settings.avatar_max_size_mb} МБ"
        )

    detected = detect_image_format(content)
    if detected is None or detected not in settings.avatar_allowed_types:
        raise ValueError(
            "Недопустимый тип файла (допустимы: "
            + ", ".join(settings.avatar_allowed_types)
            + ")"
        )

    processed = _process_image(content)

    directory = ensure_avatar_dir()
    stamp = uuid.uuid4().hex[:8]
    filename = f"{character_id}-{stamp}.{_OUTPUT_EXT}"
    path = directory / filename
    path.write_bytes(processed)

    # Replace: drop any older avatar files for this character (keep the new one).
    for old in _character_files(character_id):
        if old.name != filename:
            try:
                old.unlink()
            except OSError:
                pass

    return f"{AVATAR_URL_PREFIX}/{filename}"

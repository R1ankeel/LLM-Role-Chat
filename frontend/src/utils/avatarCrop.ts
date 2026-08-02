/**
 * Параметры кадрирования аватара (docs/avatar_ui_crop_spec.md §4).
 *
 * Модель: изображение подгоняется под квадрат через `object-fit: cover`
 * (нижний слой), затем масштабируется `scale` (>= 1) вокруг центра и
 * сдвигается `positionX`/`positionY` — нормализованный сдвиг в долях от
 * максимального смещения ([-1..1]). Хранится как JSON-строка в
 * `Character.avatar_crop`.
 */
export interface AvatarCrop {
  scale: number
  positionX: number
  positionY: number
}

export const DEFAULT_CROP: Readonly<AvatarCrop> = {
  scale: 1,
  positionX: 0,
  positionY: 0,
}

export const MAX_CROP_SCALE = 4

function clampNum(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function clampCrop(crop: AvatarCrop): AvatarCrop {
  return {
    scale: clampNum(crop.scale, 1, MAX_CROP_SCALE),
    positionX: clampNum(crop.positionX, -1, 1),
    positionY: clampNum(crop.positionY, -1, 1),
  }
}

/** Разобрать JSON-строку кадрирования (или вернуть null). */
export function parseCrop(value: string | null | undefined): AvatarCrop | null {
  if (!value) return null
  try {
    const data = JSON.parse(value)
    if (!data || typeof data !== 'object') return null
    const scale = Number(data.scale)
    const positionX = Number(data.positionX)
    const positionY = Number(data.positionY)
    if (
      !Number.isFinite(scale) ||
      !Number.isFinite(positionX) ||
      !Number.isFinite(positionY)
    ) {
      return null
    }
    return clampCrop({ scale, positionX, positionY })
  } catch {
    return null
  }
}

export function serializeCrop(crop: AvatarCrop): string {
  return JSON.stringify(clampCrop(crop))
}

/**
 * CSS-трансформация изображения (translate в % относительно квадрата).
 *
 * `aspectRatio = naturalWidth / naturalHeight` исходного файла.
 * База — `object-fit: cover` в квадратном контейнере; масштаб и сдвиг
 * применяются поверх неё, сдвиг ограничен так, чтобы не открывались края.
 */
export function cropTransform(
  crop: AvatarCrop,
  aspectRatio: number,
): { tx: number; ty: number } {
  const scale = clampNum(crop.scale, 1, MAX_CROP_SCALE)
  // Коэффициенты относительно меньшей стороны (cover в квадрат)
  const widthFactor = Math.max(aspectRatio, 1)
  const heightFactor = Math.max(1 / aspectRatio, 1)
  const maxX = ((widthFactor * scale - 1) / 2) * 100
  const maxY = ((heightFactor * scale - 1) / 2) * 100
  return {
    tx: clampNum(crop.positionX * maxX, -maxX, maxX),
    ty: clampNum(crop.positionY * maxY, -maxY, maxY),
  }
}

/** Максимальный сдвиг в пикселях для заданного размера контейнера (px). */
export function cropMaxPanPx(
  crop: AvatarCrop,
  aspectRatio: number,
  containerSize: number,
): { x: number; y: number } {
  const scale = clampNum(crop.scale, 1, MAX_CROP_SCALE)
  const widthFactor = Math.max(aspectRatio, 1)
  const heightFactor = Math.max(1 / aspectRatio, 1)
  return {
    x: ((widthFactor * scale - 1) / 2) * containerSize,
    y: ((heightFactor * scale - 1) / 2) * containerSize,
  }
}

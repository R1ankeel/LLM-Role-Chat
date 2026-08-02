# План внедрения: Character Profile, аватарки и внешность (docs/Profile.docx)

> **Статус:** ✅ **Этап A (Backend: поля модели, схема, миграция) — ВЫПОЛНЕН** (см. §3). ✅ **Этап B (Backend: avatar storage/upload/валидация) — ВЫПОЛНЕН** (см. §3). Этап C и все frontend-этапы 1–6 — ещё не начаты.
> **Источник ТЗ:** `docs/Profile.docx` (34 пункта, 6 этапов, критерии готовности в §34).
> **Ограничения ТЗ:** не переписывать frontend целиком, расширять существующую архитектуру, старый frontend (`app/static/`) не трогать, один `CharacterProfileModal`, единый источник истины (`CharacterStore`).

---

## 1. Резюме текущего состояния (что уже есть, чего нет)

### 1.1 Уже реализовано в новом frontend (`frontend/src/`)

| Что | Где | Комментарий |
|-----|-----|-------------|
| Единый `CharacterProfileModal` | `components/settings/CharacterProfileModal.vue` | Уже существует, но смонтирован ВНУТРИ `SettingsModal` (`SettingsModal.vue:30`) → открыть из правой панели или сообщения невозможно |
| Точка входа «Settings → Персонажи» | `components/settings/CharacterSettings.vue:20` → `ui.openCharacterProfile(id)` | Работает, открывает тот же модал |
| Поле `appearance` | `types/character.ts:46` (`CharacterForm.appearance`) и `CharacterFormFields.vue:128` | **Frontend-only UI-задел**: не сохраняется в backend (помечено TODO) |
| Аватар-компонент | `components/common/Avatar.vue` | Проп `imageUrl` уже есть, но размеры только `sm/md/lg`, форма только скруглённая (`--radius`), круглой миниатюры нет; в сообщениях/списках не передаётся `avatar_url` |
| Механика синхронизации | `stores/characters.ts` `update()` | После `PUT` объект заменяется в массиве → все компоненты (профиль, списки, правая панель) обновляются автоматически — единый источник истины уже соблюдён |
| Отображение локации в сообщении | `MessageItem.vue:57` | «Alice · Classroom» уже есть (вторичный текст) |

### 1.2 Чего нет (требуется по ТЗ)

| Требование ТЗ | Статус |
|---------------|--------|
| Поля `appearance` и `avatar_url` в `Character` | ✅ Этап A — колонки, схема, миграция добавлены (`app/models.py:77-78`, `app/schemas.py`, `app/database.py`) |
| Хранение и загрузка аватара (upload/validate/обработка) | ✅ Этап B — `app/avatar_service.py` (magic-bytes, лимит размера, ресайз/конвертация в WebP, безопасные имена), `POST/DELETE /characters/{id}/avatar`; каталог `app/static/avatars/` создаётся при старте |
| Единый профиль из трёх точек входа (сообщение, правая панель, Settings) | Частично — из Settings да; из сообщения и правой панели нет |
| Кликабельные avatar+имя в сообщениях | Нет — `MessageItem.vue:51/54` не кликабельны; у сообщений игрока `<Avatar name="Я">` (не аватар player-персонажа) |
| Кликабельные avatar+имя в правой панели | Нет — `CharacterList.vue:21` клик открывает inline `CharacterDetails`, а не единый профиль |
| `appearance` в Character Context (self + для присутствующих в той же локации) | Нет — `prompt_builder.py` `_CHARACTER_SECTIONS` (стр. 17), `build_scene_block` (стр. 190), `ru.json` `section_tags`, `format_character_descriptor` (стр. 347) |
| Диапазон `temperature` в backend-схеме | ✅ Этап A — `ge=0.0, le=2.0` в `CharacterBase`/`CharacterUpdate` (`schemas.py`); фронт использует 0–2 |
| Player: редактирование имени + аватар + внешность | Частично — `PlayerSettings.vue` редактирует только имя |
| Задел под будущие характеристики (отношения, память, состояние…) | Учтено архитектурой единого профиля (§31) |

### 1.3 Ключевые факты архитектуры (для решений)

- Backend: FastAPI, SQLite + SQLAlchemy 2.0 async. Миграции — идемпотентный `ensure_schema()` в `app/database.py` (ALTER TABLE по отсутствующим колонкам).
- `crud.update_character` (`app/crud.py:213`) — generic `setattr` по `model_dump(exclude_unset=True)` → новые поля подхватятся автоматически.
- FastAPI монтирует `app/static/` на `/static` (`app/main.py:238`). Vite dev-proxy сейчас проксирует только `/api` (`vite.config.ts:17`).
- Фронт: Vue 3 + TS + Pinia, `api/index.ts` — фасад с флагом `useMocks`; интерфейс `Api` в `api/types.ts` обязан быть 1:1 с `mocks/service.ts`.
- Контекст персонажа собирается в `ContextBuilder.build` (`app/context_builder.py:82`); вызывается из `app/chat_engine.py:558` и `:1924`. В точке вызова доступны `characters`, `character_locations` (id→loc), `character_names` (id→name).
- Изоляция знаний реализована через witness/presence (`witness_model.py`) + локации. Appearance должен попадать в контекст ТОЛЬКО для себя и для персонажей той же локации (§20, §21).
- Примеры реплик уже разделяются по `---` (`prompt_builder.py:77` `build_examples_block`) — семантику сохраняем.

---

## 2. Архитектурные решения

### 2.1 Единый `CharacterProfileModal`

- Модалка остаётся один (никаких вариантов для Settings/панели/сообщения).
- **Перенос монтирования**: из `SettingsModal.vue` — на уровень `ChatView.vue` (рядом с `RelationshipModal`, `SettingsModal`). `ui.characterProfileId` уже глобальный → модалка откроется поверх чата из любой точки входа.
- При открытии из Settings модалка рисуется поверх модалки настроек (проверить z-index `--z-modal` — при необходимости поднять вложенные модалки на `--z-modal + 1`).
- При открытии профиля ставить `characters.selectedId` (для синхронизации правой панели) — опционально, решается на Этапе 5.

### 2.2 Хранение и раздача аватаров

- **На диске**, не base64 в SQLite (§13, §32). Каталог: `app/static/avatars/` (уже отдаётся FastAPI на `/static`).
- Поле `Character.avatar_url` — относительный URL `/static/avatars/{character_id}-{stamp}.{ext}` (stamp = unix-time или uuid-префикс → сброс кэша при перезагрузке).
- **Dev-frontend**: в `vite.config.ts` добавить proxy-правило `/static → localhost:8000`, чтобы `<img src="/static/...">` работал на `:3000`. В production `avatar_url` относительный → резолвится на origin backend.
- Альтернатива (запасная, не основная): `GET /api/characters/{id}/avatar` через `FileResponse`. Отклоняем как основную — `/static` уже существует, кэшируется браузером, не требует отдельного endpoint на отдачу.
- Обработка изображения (Pillow, добавить в `requirements.txt`): ресайз до `avatar_max_dimension` (512 px), конвертация в JPEG/WebP, удаление EXIF. Файл без EXIF-пути.

### 2.3 Backend API

- Обычные поля (включая `appearance`) — через существующий `PUT /api/characters/{id}` (расширить схему `CharacterUpdate`), НЕ отдельные endpoint'ы (§26).
- Аватар — отдельный upload endpoint (§27):
  - `POST /api/characters/{id}/avatar` (multipart `UploadFile`) — валидация: персонаж существует; тип файла по magic-bytes (PNG/JPEG/WebP), не доверяем MIME браузера; размер ≤ `avatar_max_size_mb`; обработка; сохранение; обновление `avatar_url`; возврат актуального `CharacterRead`.
  - `DELETE /api/characters/{id}/avatar` — удаление файла, `avatar_url = ""` → placeholder.
- Файловую логику выносим в отдельный модуль `app/avatar_service.py` (не в CRUD).

### 2.4 Appearance в контексте (изоляция сохранена)

- **Self**: добавить `appearance` в `_CHARACTER_SECTIONS` (`prompt_builder.py:17`) и в `section_tags` (`ru.json:4`) → попадает в `<character>`-карту (каждый персонаж знает свою внешность, §19). Добавить в `format_character_descriptor` (для extraction/summary).
- **Присутствующие в той же локации** (§20): `build_scene_block` уже вычисляет «Рядом с тобой: …» по `character_locations`. Расширить сигнатуру параметром `character_appearances: dict[str, str] | None` и для имён из same-location добавлять строку вида `Внешность рядом стоящих: Alice — <описание>`.
- `ContextBuilder.build` получает новый параметр `character_appearances` (map name→appearance); `chat_engine.py:558` передаёт map, собранную из `characters` (доступны объекты) — фильтрация по локации уже внутри `build_scene_block`.
- Никаких глобальных списков внешностей в общий system prompt (§20, §32). Не менять существующую witness/isolation логику — только расширить входные данные scene-блока.
- Обратить внимание: `ollama_client.py:1634` (`scene state extraction`) шлёт `format_character_descriptor` для всех персонажей — это engine-вызов (не генерация за персонажа); если в descriptor добавить appearance, он попадёт в трекер локаций, но не в роли. Проверить и при необходимости appearance туда НЕ включать (оставить только в character card) — решение на этапе C.

### 2.5 Temperature

- Задать диапазон в backend-схеме: `temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)` в `CharacterBase` и `CharacterUpdate` (фронтовый slider 0–2, step 0.05 — совпадает). Правка `app/schemas.py`.
- ✅ Сделано на Этапе A. Примечание: `ge/le` применяется и в `CharacterRead` — если в БД уже есть значение вне 0–2 (задано до валидации), чтение такого персонажа упадёт (риск низкий: старый frontend шлёт только 0–2, `app/static/index.html`).

---

## 3. Backend: этапы и файлы

### Этап A — Поля модели, схема, миграция

> **Статус: ✅ ВЫПОЛНЕН.**

- `app/models.py` `Character`: `appearance: Mapped[str] = mapped_column(Text, default="")`, `avatar_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)`.
- `app/schemas.py`: `CharacterBase` += `appearance: str = ""`, `avatar_url: str = ""`; `CharacterUpdate` += `appearance: Optional[str]`, `avatar_url: Optional[str]`; `temperature` — `ge/le` (п.2.5). `CharacterRead` наследует автоматически.
- `app/database.py` `ensure_schema`: в блок миграций `characters` (стр. 192) добавить `("appearance", "TEXT NOT NULL DEFAULT ''")`, `("avatar_url", "TEXT NOT NULL DEFAULT ''")`.
- `app/crud.py`: `create_character` — вырезать `avatar_url` из `model_dump` при создании (файл грузится только через upload endpoint), `is_player` уже вырезается. `update_character` — без изменений (generic).
- `app/config.py` + `.env.example`: настройки аватара (`AVATAR_DIR`, `AVATAR_MAX_SIZE_MB`, `AVATAR_MAX_DIMENSION`; `avatar_allowed_types` — константа в коде). Это вынесено на Этап A, чтобы записи `.env.example` были осмысленными; фактическое использование — на Этапе B.
- ✅ Тесты: `tests/test_character_profile.py` — PUT `appearance`/`avatar_url` сохраняются; `temperature` вне 0–2 → 422; `avatar_url` в `CharacterRead` (default `""`); `avatar_url` не задаётся при создании.

### Этап B — Avatar: хранилище, upload, валидация

> **Статус: ✅ ВЫПОЛНЕН.**

- `requirements.txt`: `Pillow>=10.0.0`, `python-multipart>=0.0.9` (multipart-парсинг FastAPI для `UploadFile`).
- `app/config.py`: `avatar_dir` (default `app/static/avatars`), `avatar_max_size_mb` (5), `avatar_max_dimension` (512), `avatar_allowed_types` (png/jpeg/webp) — задано на Этапе A, используется на Этапе B.
- `app/avatar_service.py`: `validate_and_save(file, character_id) -> avatar_url`, `remove_avatar(character_id)`, `detect_image_format` (magic-byte PNG/JPEG/WebP), лимит размера, ресайз до `avatar_max_dimension` + конвертация в WebP (EXIF отбрасывается), безопасное имя файла только `{id}-{stamp}.webp`, удаление старого файла при замене; `ensure_avatar_dir()`/`avatar_dir_path()` (относительный `AVATAR_DIR` резолвится от корня проекта).
- `app/routers/characters.py`: `POST /characters/{id}/avatar` (проверки §27: 404/тип/размер/обработка), `DELETE /characters/{id}/avatar`; оба возвращают актуальный `CharacterRead`; при `DELETE /characters/{id}` файлы аватара также удаляются.
- `app/main.py`: в `lifespan` вызывается `avatar_service.ensure_avatar_dir()`; `/static` уже смонтирован.
- ✅ Тесты: `tests/test_character_profile.py` — класс `TestCharacterAvatar` (см. §5).

### Этап C — Appearance в контекст

- `app/prompts/ru.json`: `character.section_tags.appearance = "appearance"`.
- `app/prompt_builder.py`:
  - `_CHARACTER_SECTIONS` += `"appearance"` → попадает в `build_character_card` (self);
  - `build_scene_block(..., character_appearances=None)`: для same-location имён добавлять строки внешности (только для co-present);
  - `format_character_descriptor`: решить включать ли appearance (п.2.4 — скорее НЕ включать).
- `app/context_builder.py` `ContextBuilder.build` += параметр `character_appearances`, пробросить в `build_scene_block`.
- `app/chat_engine.py` (строки 558 и 1924): собрать `{character.name: character.appearance}` и передать.
- Тесты: `tests/test_prompt_builder.py` / `test_context_builder.py` (см. §5).

---

## 4. Frontend: этапы и файлы

### Этап 1 — Типы, API, store, mock-синхронизация (база)

- `types/character.ts`: `Character` += `appearance: string`, `avatar_url: string`; `CharacterForm` += `avatar_url` (appearance уже есть); `characterToForm`/`formToCharacterUpdate` — прокинуть `appearance` (убрать хардкод `''`), `avatar_url` в update.
- `api/types.ts`: `CharacterUpdateInput` += `appearance?`, `avatar_url?`; `Api` += `uploadCharacterAvatar(characterId, file): Promise<Character>`, `deleteCharacterAvatar(characterId): Promise<Character>`.
- `api/characters.ts`: реализовать `uploadCharacterAvatar` (multipart FormData, нельзя JSON — body `FormData`), `deleteCharacterAvatar`.
- `api/index.ts`: зарегистрировать в фасаде.
- `mocks/service.ts` + `mocks/data.ts`: реализовать те же методы 1:1 (интерфейс `Api` строго совпадает), mock-avatar_url (например, на `mockCharacters` добавить `avatar_url` у части персонажей).
- `stores/characters.ts`: `uploadAvatar(characterId, file)`, `removeAvatar(characterId)` — после успеха заменять объект в массиве (как `update()`), флаг `mutating`.

### Этап 2 — Avatar-компонент

- `components/common/Avatar.vue` — это и есть единый `CharacterAvatar` (§12). Расширить:
  - размер `xl` (160–220 px, для профиля), добавить `size` в пропсы;
  - проп `shape: 'rounded' | 'circle'` (круглая миниатюра для сообщений);
  - оставить placeholder (инициалы + accent) как полноценный UI-элемент (§10.1);
  - `imageUrl` уже есть — везде передавать `character.avatar_url`.
- Заменить использование во всех списках/сообщениях: `CharacterList`, `CharacterSettings`, `CharacterDetails`, `MessageItem`, `PlayerSettings`, профиль.

### Этап 3 — Единый `CharacterProfileModal` (реализация ТЗ §7–§8, §28–§30)

- `components/settings/CharacterProfileModal.vue` — редизайн:
  - верхняя зона: слева большой `Avatar` (xl) + кнопка смены/удаления аватара (§14); справа — редактируемое имя (крупно), компактно локация + badge «Игрок» (§9), поле «Внешность» (многострочное) рядом с аватаром;
  - ниже: Личность → Черты → Предыстория → Стиль речи → Примеры реплик (сохранить семантику `---`) → Границы роли (§8 порядок);
  - технические параметры внизу, визуально не конкурируют (§29): temperature (slider 0–2 + показать значение), order_index (числовой input);
  - кнопки «Отмена» / «Сохранить»; модалка закрывается только после успешного сохранения (§23); сохранение через `characters.update`, аватар — отдельно если изменён;
  - responsive: на узких экранах верхняя часть в одну колонку (§30).
- `CharacterFormFields.vue`: убрать UI-задел-подпись про внешность; `appearance` станет реальным полем (оставить в create-форме). Разметку полей можно вынести/переиспользовать между profile и create, НЕ делая giant-компонент (§32).
- Перенести монтирование `CharacterProfileModal` из `SettingsModal.vue` в `ChatView.vue` (п.2.1).

### Этап 4 — Три точки входа

- **Сообщение** (`components/chat/MessageItem.vue`):
  - для NPC: avatar и имя кликабельны → `ui.openCharacterProfile(character.id)`; передать `avatar_url`; для user-сообщений использовать player-персонажа из `characters.player` (avatar_url + имя), клик тоже открывает его профиль (§11);
  - «Alice · Classroom» остаётся, локация — вторичный стиль, при отсутствии не показывается (§22).
- **Правая панель** (`components/characters/CharacterList.vue`, `components/layout/RightPanel.vue`):
  - клик по строке (или явно по avatar/имени) → `ui.openCharacterProfile(character.id)` — единый профиль;
  - существующий inline `CharacterDetails` (память, локация, отношения) сохранить как вторичный слой: внутри модалки кнопка «Отношения» (уже есть RelationshipModal) и/или сохранение `RelationshipView` для выбранного персонажа. Решение по финальному UX на Этапе 5 (не плодить отдельную версию «профиля»).
- **Settings → Персонажи**: уже открывает единый модал — после переноса в `ChatView` проверить, что модалка корректно рендерится поверх Settings.

### Этап 5 — Player: имя + аватар + внешность (§17)

- `components/settings/PlayerSettings.vue`: большая карточка с аватаром (смена/удаление), поле «Внешность» (многострочное), имя — всё сохраняется через `characters.update` / `uploadAvatar` (player — обычный `Character` с `is_player=true`, отдельной системы не создаём).

### Этап 6 — UI polish (§6, §30, §33 Этап 6)

- Состояния: loading загрузки аватара, ошибки upload (тип/размер) и save, unsaved-changes, hover/focus/click, placeholder аватара, обрезание (`object-fit`).
- Обновление всех мест после сохранения — через единый store (уже работает).
- Мелкие экраны: скролл модалки, верхняя часть в одну колонку.
- `npm run build` (vue-tsc strict) без ошибок; ручная сверка с `docs/Frontend.png` (по возможности) и макетами ТЗ §7/§28.

---

## 5. Тесты

Backend (pytest, существующая структура `tests/`):
- `tests/test_character_profile.py` (✅ Этап A-часть и Этап B-часть реализованы):
  - `PUT /characters/{id}` с `appearance` и `temperature` вне диапазона (422);
  - `avatar_url` попадает в `CharacterRead`;
  - upload: несуществующий персонаж → 404; недопустимый тип (магик-байты) → 400; слишком большой файл → 400; успешный upload → `avatar_url` начинается с `/static/avatars/`, файл существует и является валидным WebP;
  - delete avatar → `avatar_url == ""`, файл удалён;
  - замена аватара удаляет старый файл.
- `tests/test_prompt_builder.py`: `<appearance>` присутствует в character card (self); scene-блок содержит внешность только для персонажей той же локации, НЕ содержит для других локаций (изоляция).
- `tests/test_context_builder.py`: `character_appearances` прокидывается; при `viewer_location != other_location` внешность другого не включается.

Frontend: тестовой инфраструктуры нет — проверка `npm run build` (vue-tsc strict) + ручные сценарии (три точки входа, upload/delete аватара, синхронизация без reload).

---

## 6. Критерии готовности (из ТЗ §34 — карта на задачи)

| Критерий | Покрытие |
|----------|----------|
| avatar у персонажа, показ в профиле/сообщениях/списках | ✅ Этап B (backend хранилище/upload) + frontend 2–4 |
| avatar можно изменить, отсутствующий → placeholder | ✅ Этап B (upload/delete + замена файла) + frontend 3 |
| отдельное поле `appearance`, редактируется, сохраняется в backend | ✅ Этап A (backend); UI-часть — frontend 1/3 |
| `appearance` игрока поддерживается | frontend 5 |
| `appearance` попадает в Character Context | Этап C |
| `appearance` не нарушает изоляцию | Этап C (тест) |
| клик по avatar/имени в сообщении → профиль | frontend 4 |
| клик в Right Panel → профиль | frontend 4 |
| клик в Settings → тот же профиль | frontend 3–4 |
| один `CharacterProfileModal`, синхронизация без reload | frontend 3–4 + store |
| старый frontend работает | нет правок в `app/static/` |
| TS build проходит, backend tests проходят, нет giant-компонентов | frontend 6 + §5 |

---

## 7. Риски и решения

| Риск | Оценка | Решение |
|------|--------|---------|
| Раздача `/static` в dev (порт 3000) | низкий | proxy `/static → :8000` в `vite.config.ts` |
| Nested модалки (профиль поверх Settings) | низкий | z-index вложенных модалок, перенос в `ChatView` |
| Изоляция знаний при добавлении appearance | средний | Appearance только в character card (self) и в scene-блок для same-location; тест изоляции; не менять witness-логику |
| Внешность в scene-state extraction для всех персонажей | низкий | Осознанное решение: в `format_character_descriptor` appearance НЕ включать (п.2.4) |
| Большие/невалидные файлы | средний | magic-byte валидация, лимит размера, ресайз через Pillow |
| Расхождение с макетом | средний | Ручная сверка на Этапах 3 и 6 (PNG из docs/) |
| Инерция кликов в правой панели (была детализация) | средний | UX-решение на Этапе 5: клик → единый профиль, отношения/память доступны изнутри модалки и/или вторичного inline-слоя |

---

## 8. Порядок работ (сводно)

1. ✅ **Backend A** (модель/схема/миграция) → тесты `test_character_profile.py` (A-часть).
2. ✅ **Backend B** (avatar storage + endpoints + Pillow) → тесты.
3. **Backend C** (appearance в контекст, изоляция) → тесты.
4. **Frontend 1** (типы/api/store/mocks) → build.
5. **Frontend 2** (Avatar component: xl/circle/avatar_url).
6. **Frontend 3** (CharacterProfileModal редизайн + перенос монтирования).
7. **Frontend 4** (три точки входа).
8. **Frontend 5** (Player).
9. **Frontend 6** (polish + визуальная проверка) → финальный `npm run build` + `pytest`.

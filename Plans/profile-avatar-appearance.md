# План внедрения: Character Profile, аватарки и внешность (docs/Profile.docx)

> **Статус:** ✅ **Этап A (Backend: поля модели, схема, миграция) — ВЫПОЛНЕН** (см. §3). ✅ **Этап B (Backend: avatar storage/upload/валидация) — ВЫПОЛНЕН** (см. §3). ✅ **Этап C (Backend: appearance в контекст) — ВЫПОЛНЕН** (см. §3). ✅ **Этап 1 (Frontend: типы/API/store/mock-синхронизация) — ВЫПОЛНЕН** (см. §4). ✅ **Этап 2 (Frontend: Avatar-компонент) — ВЫПОЛНЕН** (см. §4). ✅ **Этап 3 (Frontend: единый CharacterProfileModal) — ВЫПОЛНЕН** (см. §4). ✅ **Этап 4 (Frontend: три точки входа) — ВЫПОЛНЕН** (см. §4). ✅ **Этап 5 (Frontend: Player) — ВЫПОЛНЕН** (см. §4). ✅ **Этап 6 (Frontend: UI polish) — ВЫПОЛНЕН** (см. §4). Все фронтенд-этапы завершены.
> **Источник ТЗ:** `docs/Profile.docx` (34 пункта, 6 этапов, критерии готовности в §34).
> **Ограничения ТЗ:** не переписывать frontend целиком, расширять существующую архитектуру, старый frontend (`app/static/`) не трогать, один `CharacterProfileModal`, единый источник истины (`CharacterStore`).

---

## 1. Резюме текущего состояния (что уже есть, чего нет)

### 1.1 Уже реализовано в новом frontend (`frontend/src/`)

| Что | Где | Комментарий |
|-----|-----|-------------|
| Единый `CharacterProfileModal` | `components/settings/CharacterProfileModal.vue` | ✅ Этап 3 — редизайн (верхняя зона с Avatar xl + смена/удаление аватара, редактируемое имя/локация/badge «Игрок»/внешность; §8-блок полей; технические параметры; «Отмена/Сохранить» с закрытием только после успешного сохранения; responsive); монтирование перенесено из `SettingsModal.vue` в `ChatView.vue` (после `<SettingsModal />` — профиль рендерится поверх) |
| Точка входа «Settings → Персонажи» | `components/settings/CharacterSettings.vue:20` → `ui.openCharacterProfile(id)` | Работает, открывает тот же модал |
| Поле `appearance` | `types/character.ts:46` (`CharacterForm.appearance`) и `CharacterFormFields.vue:128` | ✅ frontend 1 — `characterToForm`/`formToCharacterUpdate` прокидывают `appearance` в `PUT /characters/{id}` (хардкод `''` убран); UI-ввод уже есть в `CharacterFormFields` |
| Аватар-компонент | `components/common/Avatar.vue` | ✅ Этап 2 — проп `size` расширен до `sm/md/lg/xl` (xl = 168 px, для профиля), добавлен проп `shape: 'rounded' | 'circle'` (круглая миниатюра для сообщений); placeholder (инициалы + accent) сохранён; `imageUrl` пробрасывается во все места использования (списки, сообщения, профиль, Player, delete-confirm) |
| Механика синхронизации | `stores/characters.ts` `update()` — после `PUT` объект заменяется в массиве → все компоненты (профиль, списки, правая панель) обновляются автоматически — единый источник истины уже соблюдён; ✅ frontend 1 — то же для аватара: `uploadAvatar()`/`removeAvatar()` |
| Отображение локации в сообщении | `MessageItem.vue:57` | «Alice · Classroom» уже есть (вторичный текст) |

### 1.2 Чего нет (требуется по ТЗ)

| Требование ТЗ | Статус |
|---------------|--------|
| Поля `appearance` и `avatar_url` в `Character` | ✅ Этап A — колонки, схема, миграция добавлены (`app/models.py:77-78`, `app/schemas.py`, `app/database.py`); ✅ frontend 1 — поля в типах `Character`/`CharacterForm` (`types/character.ts`) |
| Хранение и загрузка аватара (upload/validate/обработка) | ✅ Этап B — `app/avatar_service.py` (magic-bytes, лимит размера, ресайз/конвертация в WebP, безопасные имена), `POST/DELETE /characters/{id}/avatar`; каталог `app/static/avatars/` создаётся при старте |
| Единый профиль из трёх точек входа (сообщение, правая панель, Settings) | ✅ Этап 4 — все три точки ведут в единый модал (см. §4) |
| Кликабельные avatar+имя в сообщениях | ✅ Этап 4 — `MessageItem.vue`: у NPC avatar и имя кликабельны → профиль; у user-сообщений аватар игрока (`characters.player`, avatar_url+имя, имя не выводится по решению) → профиль игрока; локация — вторичный стиль, при отсутствии не показывается (§22) |
| Кликабельные avatar+имя в правой панели | ✅ Этап 4 — `CharacterList.vue`: клик по строке → единый профиль; inline `CharacterDetails` сохранён как вторичный слой через кнопку-шеврон «Подробности» |
| `appearance` в Character Context (self + для присутствующих в той же локации) | ✅ Этап C — `prompt_builder.py` `_CHARACTER_SECTIONS` += `appearance`, `build_scene_block` += `character_appearances` (только co-present), `ru.json` `section_tags.appearance`, `ContextBuilder.build` += `character_appearances`, `chat_engine.py:558/1924` передают map |
| Диапазон `temperature` в backend-схеме | ✅ Этап A — `ge=0.0, le=2.0` в `CharacterBase`/`CharacterUpdate` (`schemas.py`); фронт использует 0–2 |
| Player: редактирование имени + аватар + внешность | ✅ Этап 5 — `PlayerSettings.vue` (карточка с аватаром xl + смена/удаление, имя + внешность, сохранение через `characters.update`/`uploadAvatar`); player — обычный `Character` |
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
- ✅ **Перенос монтирования** (Этап 3): из `SettingsModal.vue` — на уровень `ChatView.vue` (рядом с `RelationshipModal`, `SettingsModal`, ПОСЛЕ `SettingsModal`). `ui.characterProfileId` уже глобальный → модалка откроется поверх чата из любой точки входа.
- ✅ При открытии из Settings модалка рисуется поверх модалки настроек — без правки z-index: обе на `--z-modal: 100`, порядок в DOM решает (профиль объявлен после настроек в `ChatView`). Escape-стек `Modal.vue` закрывает только верхнюю модалку.
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
- **Резолюция (Этап C):** `format_character_descriptor` appearance НЕ включает. Внешность попадает в контекст только через `<appearance>` в character card (self) и через строку «Внешность рядом стоящих» в scene-блоке для co-present. Трекер локаций / эстракция памяти внешность не получают — риск §7 снят. Текст строки: `Внешность рядом стоящих: Имя — <описание>, ...` (только непустые внешности, только персонажи той же локации).

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

> **Статус: ✅ ВЫПОЛНЕН.**

- `app/prompts/ru.json`: `character.section_tags.appearance = "appearance"` ✅.
- `app/prompt_builder.py`:
  - `_CHARACTER_SECTIONS` += `"appearance"` → попадает в `build_character_card` (self) как `<appearance>`-тег ✅;
  - `build_scene_block(..., character_appearances=None)`: для same-location имён добавляет строку `Внешность рядом стоящих: Имя — <описание>, ...` (только co-present, только непустые внешности; ключи — имена персонажей) ✅;
  - `format_character_descriptor`: **НЕ включает appearance** (резолюция п.2.4) ✅.
- `app/context_builder.py` `ContextBuilder.build` += параметр `character_appearances`, проброшен в `build_scene_block` ✅.
- `app/chat_engine.py` (строки 558 и 1924): собирается `{character.name: character.appearance or ""}` из `all_characters` (включая player) и передаётся в `build` ✅.
- Legacy-fallback `ollama_client.py:868` (`build_scene_block` при `context_enabled=False`) — без изменений: старый контекст не получает внешность co-present (осознанно, обратная совместимость).
- ✅ Тесты: `tests/test_prompt_builder.py` (`TestBuildSceneBlock` — same-location включена / другой локации исключена / пустые внешности / без аргумента) и `tests/test_context_builder.py` (прокидывание `character_appearances`, изоляция при `viewer_location != other_location`).

> **Замечание:** заодно исправлен давно падавший тест `test_full_character_card` — assertion `<relationships>` в character card был ошибочным (relationships добавляется динамически через `build_relationships_block`, в статической карточке его нет).

---

## 4. Frontend: этапы и файлы

### Этап 1 — Типы, API, store, mock-синхронизация (база)

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит).

- ✅ `types/character.ts`: `Character` += `appearance: string`, `avatar_url: string`; `CharacterForm` += `avatar_url`; `characterToForm` — прокинул `appearance: character.appearance` (убран хардкод `''`) и добавил `avatar_url: character.avatar_url`; `formToCharacterUpdate` — в возврат добавлены `appearance` и `avatar_url` (и в тип возврата).
- ✅ `api/types.ts`: `CharacterUpdateInput` += `appearance?`, `avatar_url?`; `Api` += `uploadCharacterAvatar(characterId, file): Promise<Character>`, `deleteCharacterAvatar(characterId): Promise<Character>`.
- ✅ `api/characters.ts`: `uploadCharacterAvatar` (multipart `FormData`, поле формы `file`, соответствует `UploadFile = File(...)` в `app/routers/characters.py:95`), `deleteCharacterAvatar`.
- ✅ `api/index.ts`: оба метода зарегистрированы в фасаде (real-ветка).
- ✅ `mocks/service.ts` + `mocks/data.ts`: методы 1:1 с `Api` (`uploadCharacterAvatar` ставит `/static/avatars/{id}-mock.webp`, `deleteCharacterAvatar` → `avatar_url = ""`); `mockCharacters` — добавлены `appearance` и `avatar_url` (обязательные поля типа), `avatar_url` задан у части персонажей (Alice id 102), `appearance` — у Alice и Bob; в `createCharacter`/`createChat` (player) — поля со значением `''`; `updateCharacter` mock применяет `patch.appearance`/`patch.avatar_url`.
- ✅ `stores/characters.ts`: `uploadAvatar(characterId, file)`, `removeAvatar(characterId)` — после успеха объект заменяется в массиве (как `update()`), флаг `mutating`.
- ✅ Компоненты (`CharacterCreateModal.vue`, `CharacterProfileModal.vue`): начальные `reactive<CharacterForm>` += `avatar_url: ''` (новое обязательное поле формы).

> **Замечание (технический задел):** `uploadCharacterAvatar` требует multipart-тело без JSON. Существующий `request()` всегда сериализовал тело в JSON и ставил `Content-Type: application/json`, поэтому в `api/client.ts` аддитивно расширен `RequestOptions` полем `rawBody?: BodyInit`: при его передаче тело уходит как есть, `Content-Type` не выставляется (boundary генерирует браузер). JSON-ветка не изменена.

### Этап 2 — Avatar-компонент

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит).

- ✅ `components/common/Avatar.vue` — единый `CharacterAvatar` (§12) расширен:
  - размер `xl` (168 px, диапазон 160–220, для профиля) — добавлен в union пропа `size`;
  - проп `shape: 'rounded' | 'circle'` — `rounded` по умолчанию (обратная совместимость), `circle` → `border-radius: 50%` (круглая миниатюра для сообщений);
  - placeholder (инициалы + accent) сохранён как полноценный UI-элемент (§10.1);
  - `imageUrl` был и остался — теперь передаётся везде.
- ✅ Проброс `character.avatar_url` во всех списках/сообщениях:
  - `CharacterList.vue` (`character-row__avatar`, `size="sm"`);
  - `CharacterSettings.vue` (`character-settings__avatar`, `size="sm"`);
  - `CharacterDetails.vue` (`size="lg"`);
  - `MessageItem.vue` — обе роли (character и user) передают `props.character?.avatar_url`, обе — `shape="circle"` (для user пока остаётся имя-заглушка «Я»; смена имени player-персонажа — Этап 4);
  - `PlayerSettings.vue` (`size="lg"`);
  - профиль `CharacterProfileModal.vue` — `size="xl"`;
  - заодно `CharacterDeleteConfirm.vue` (`size="lg"`) — вне списка ТЗ, но консистентно.
- ✅ `vite.config.ts`: добавлен dev-прокси `/static → localhost:8000`, чтобы `<img src="/static/avatars/...">` работал на `:3000` (§2.2, риск §7). `Sidebar.vue` не трогался (чат, у чатов нет `avatar_url`).
- Кликабельность avatar+имя (сообщение / правая панель) — намеренно не входит в этот этап, это Этап 4.

### Этап 3 — Единый `CharacterProfileModal` (реализация ТЗ §7–§8, §28–§30)

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит).

- ✅ `components/settings/CharacterProfileModal.vue` — редизайн:
  - верхняя зона (§7, §14): слева большой `Avatar` (`size="xl"`, placeholder живёт по имени из формы) + кнопки «Сменить»/«Удалить» аватара (скрытый `<input type="file" accept="image/png,image/jpeg,image/webp">`; upload/delete сразу через `characters.uploadAvatar/removeAvatar`, затем `form.avatar_url = updated.avatar_url` — не перезатирая несохранённый текст формы); справа — крупный редактируемый input имени, badge «Игрок» (§9), компактный input «Локация», textarea «Внешность» (многострочная);
  - ниже: `<CharacterFormFields mode="profile">` — Личность → Черты → Предыстория → Стиль речи → Примеры реплик (семантика `---` сохранена, в поле добавлен hint про разделитель) → Границы роли (§8), затем подзаголовок «Отношения» (вторичный стиль) с полем «Описание отношений» — оставлено осознанно, чтобы не потерять редактирование (отдельная система `CharacterRelationship` — в будущем отдельной задачей);
  - технические параметры внизу, визуально не конкурируют (§29): блок «Технические параметры» (панель), temperature — slider 0–2, step 0.05 + показано значение, `order_index` — числовой input (только profile);
  - кнопки «Отмена» (discard) / «Сохранить»; модалка закрывается только после успешного сохранения (§23) — при ошибке остаётся открытой + error-toast; сохранение через `characters.update`, аватар — отдельно (если изменён);
  - responsive (§30): `@media (max-width: 640px)` — верхняя зона в одну колонку.
- ✅ `CharacterFormFields.vue` — рефакторинг без giant-компонента (§32): добавлен проп `mode: 'create' | 'profile'`; убран проп `showAppearance` и подпись-задел «UI-задел: пока не сохраняется…» (`appearance` реально сохраняется с Этапа 1); `appearance` стала многострочным полем в create-форме; средний §8-блок и технический блок общие для обоих режимов; в create temperature тоже slider (единообразие UI). `order_index` — только в profile.
- ✅ Монтирование перенесено из `SettingsModal.vue` в `ChatView.vue` (п.2.1); в `SettingsModal.vue` остались `CharacterCreateModal` и `CharacterDeleteConfirm`.
- ✅ Синхронизация «API → store → form/UI»: `characters.uploadAvatar/removeAvatar` уже заменяют объект в массиве store (списки/панель обновляются), модалка синхронизирует только `form.avatar_url` из возвращённого объекта.
- Точка входа Settings → Персонажи открывает тот же единый модал (теперь поверх настроек). Точки входа «сообщение» и «правая панель» — Этап 4.

### Этап 4 — Три точки входа

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит).

- ✅ **Сообщение** (`components/chat/MessageItem.vue`):
  - NPC (role=`character`): `<Avatar>` обёрнут в `<button class="message-item__avatar-btn">`, имя — в `<button class="message-item__author">`; оба кликабельны → `ui.openCharacterProfile(character.id)` (при `character == null` — неактивные `span`/простой `Avatar`). Кнопки: reset браузерного стиля, `:hover` underline у имени, `:focus-visible` outline.
  - user-сообщения: аватар использует player-персонажа из `characters.player` (avatar_url + имя для инициалов-заглушки), клик → профиль игрока; **имя не выводится** (решение в ходе реализации); при отсутствии player — фоллбэк `<Avatar name="Я">` без клика.
  - «Alice · Classroom» остаётся: локация — вторичный стиль (`message-item__location`), `v-if="location"` — при отсутствии не показывается (§22).
- ✅ **Правая панель** (`components/characters/CharacterList.vue`; `RightPanel.vue` — без правок, композиция уже корректна):
  - клик по строке (и `@keydown.enter`) → `ui.openCharacterProfile(character.id)` — единый профиль;
  - существующий inline `CharacterDetails` (память, локация, отношения) сохранён как вторичный слой: шеврон стал кнопкой «Подробности» (`@click.stop` → `characters.selectCharacter(id)`, появляется на hover/active/focus); кнопка «Отношения» внутри `CharacterDetails` (→ `RelationshipModal`) и секция `RelationshipView` от `selectedId` работают как раньше;
  - примечание: `selectedId` при клике по строке больше не ставится → active-подсветка строки обновляется только через «Подробности»; финальный UX — Этап 5.
- ✅ **Settings → Персонажи**: уже открывает единый модал — после переноса в `ChatView` рендерится корректно поверх Settings.

### Этап 5 — Player: имя + аватар + внешность (§17)

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит).

- ✅ `components/settings/PlayerSettings.vue` — редизайн (§17); player — обычный `Character` с `is_player=true`, отдельной системы не создаётся:
  - большая карточка: `<Avatar size="xl">` (placeholder живёт по вводимому имени) + кнопки «Сменить»/«Удалить» аватара (скрытый file-input, `accept="image/png,image/jpeg,image/webp"`; upload/delete сразу через `characters.uploadAvatar/removeAvatar` — store синхронизирует массив → карточка/сообщения/правая панель обновляются; «Удалить» — если аватар есть);
  - поля «Имя игрока» + «Внешность» (многострочный textarea); синк с `characters.player` через watch на отдельные поля (не перезатирает несохранённый ввод при upload аватара);
  - сохранение одной кнопкой через `characters.update(player.id, { name, appearance })` (PUT `/characters/{id}`); кнопка disabled при пустом имени / без изменений / saving / avatarBusy; тосты успех/ошибка;
  - responsive: `@media (max-width: 640px)` — карточка в одну колонку;
  - TODO на следующий этап: паттерн upload/delete аватара дублируется с `CharacterProfileModal` — при ≥2–3 местах вынести общий `AvatarUploader`-компонент (preview, hidden file input, «Сменить», «Удалить», loading, error, validation).
- ✅ **Cleanup:** удалён ставший мёртвым `updatePlayerName` из фронтенда (`api/types.ts` — интерфейс `Api`, `api/index.ts` — фасад, `api/characters.ts` — реализация, `mocks/service.ts` — мок, `stores/characters.ts` — метод + export). Backend-эндпоинт `PUT /chats/{id}/player` **не тронут** (старый frontend `app/static` может его использовать).

### Этап 6 — UI polish (§6, §30, §33 Этап 6)

> **Статус: ✅ ВЫПОЛНЕН** (`npm run build` — vue-tsc strict + vite — проходит; релевантные backend-тесты `test_character_profile.py` / `test_prompt_builder.py` / `test_context_builder.py` — 48 passed).

- ✅ **Состояния**:
  - loading загрузки аватара: `avatarBusy` + кнопка «Загрузка…» (уже с Этапа 3/5) + **новый** dim (`opacity: .55`) аватара в `CharacterProfileModal` и `PlayerSettings` при upload/delete;
  - ошибки upload (тип/размер) и save: через toast, `ApiError.detail` от backend (`api/client.ts`) — уже работало, проверено;
  - **unsaved-changes**: в `CharacterProfileModal` добавлен `dirty` computed с **нормализованным сравнением** (`undefined/null→''`, temperature через `Number.isFinite`, `order_index` через `?? 0`; база — `characterToForm(target)`, т.е. то же нормализующее представление, что заполняет форму → «открыл → ничего не менял → dirty=false»); подпись «Несохранённые изменения» в футере при `dirty && !saving`; «Сохранить» disabled при `!dirty || !name.trim() || saving || avatarBusy` (аватар применяется сразу, «Сохранить» — только текстовые/числовые поля); аналогичная подпись добавлена в `PlayerSettings` (кнопка уже была disabled при `!changed`);
  - hover/focus/click: глобальный `:focus-visible` уже есть в `base.css`; локальные стили кликабельных avatar/имени/строк — с Этапов 2–4;
  - placeholder аватара: инициалы+accent (с Этапа 1/2) + **новый** фоллбэк на placeholder при ошибке загрузки `<img>` (`@error` + сброс по `watch(imageUrl)`) в `Avatar.vue` — реальный edge case (404/удалённый файл);
  - обрезание: `object-fit: cover` на `.avatar__img` (было).
- ✅ Обновление всех мест после сохранения — через единый store (уже работает с Этапа 1): после `characters.update`/`uploadAvatar`/`removeAvatar` объект заменяется в массиве → списки/сообщения/панель/карточка обновляются без reload.
- ✅ Мелкие экраны: скролл модалки (`overflow-y: auto` + **новый** `overscroll-behavior: contain` на `.modal__body` в `Modal.vue` — скролл не чейнится в страницу); верхняя часть профиля и карточка игрока в одну колонку (`@media (max-width: 640px)`, с Этапа 3/5).
- ✅ `npm run build` (vue-tsc strict) без ошибок.
- **Сверка с макетами:** `docs/Frontend.png` **отсутствует в репозитории** — ручная сверка по нему невозможна (зафиксировано осознанно, дизайн по памяти не восстанавливаем); визуальный QA переносится на ручную проверку по макетам ТЗ §7/§28.
- **Открытый TODO (из Этапа 5):** паттерн upload/delete аватара дублируется в 2 местах (`CharacterProfileModal`, `PlayerSettings`) — при появлении ≥3-го места вынести общий `AvatarUploader`-компонент (preview, hidden file input, «Сменить», «Удалить», loading, error, validation). Сейчас осознанно не выносится.

---

## 5. Тесты

Backend (pytest, существующая структура `tests/`):
- `tests/test_character_profile.py` (✅ Этап A-часть и Этап B-часть реализованы):
  - `PUT /characters/{id}` с `appearance` и `temperature` вне диапазона (422);
  - `avatar_url` попадает в `CharacterRead`;
  - upload: несуществующий персонаж → 404; недопустимый тип (магик-байты) → 400; слишком большой файл → 400; успешный upload → `avatar_url` начинается с `/static/avatars/`, файл существует и является валидным WebP;
  - delete avatar → `avatar_url == ""`, файл удалён;
  - замена аватара удаляет старый файл.
- `tests/test_prompt_builder.py` (✅ Этап C-часть реализована): `<appearance>` присутствует в character card (self); scene-блок содержит внешность только для персонажей той же локации, НЕ содержит для других локаций (изоляция).
- `tests/test_context_builder.py` (✅ Этап C-часть реализована): `character_appearances` прокидывается; при `viewer_location != other_location` внешность другого не включается.

Frontend: тестовой инфраструктуры нет — проверка `npm run build` (vue-tsc strict) + ручные сценарии (три точки входа, upload/delete аватара, синхронизация без reload).

---

## 6. Критерии готовности (из ТЗ §34 — карта на задачи)

| Критерий | Покрытие |
|----------|----------|
| avatar у персонажа, показ в профиле/сообщениях/списках | ✅ Этап B (backend хранилище/upload) + ✅ Этап 2 (показ аватара/placeholder во всех местах); клики — frontend 4 |
| avatar можно изменить, отсутствующий → placeholder | ✅ Этап B (upload/delete + замена файла) + ✅ Этап 3 (UI смены/удаления в профиле, кнопки «Сменить»/«Удалить», placeholder при пустом) |
| отдельное поле `appearance`, редактируется, сохраняется в backend | ✅ Этап A (backend); ✅ frontend 1 (типы/API/store/mocks); ✅ Этап 3 (UI-редактирование: профиль — textarea во внешности, create — многострочное поле) |
| `appearance` игрока поддерживается | ✅ Этап 5 (редактируется и сохраняется; в Character Context попадает как у любого персонажа) |
| `appearance` попадает в Character Context | ✅ Этап C (самостоятельно + co-present в scene-блоке) |
| `appearance` не нарушает изоляцию | ✅ Этап C (тест) |
| клик по avatar/имени в сообщении → профиль | ✅ Этап 4 (NPC — avatar+имя, user — аватар игрока) |
| клик в Right Panel → профиль | ✅ Этап 4 (клик по строке; детализация — через «Подробности»/шеврон) |
| клик в Settings → тот же профиль | ✅ Этап 3 (единый модал поверх настроек) |
| один `CharacterProfileModal`, синхронизация без reload | ✅ Этап 3 (единый модал, монтирование в `ChatView`, обновление через store без reload); точки входа сообщение/панель — frontend 4 |
| старый frontend работает | нет правок в `app/static/` |
| TS build проходит, backend tests проходят, нет giant-компонентов | ✅ `npm run build` (vue-tsc strict) — проходит; релевантные backend-тесты (`test_character_profile.py`, `test_prompt_builder.py`, `test_context_builder.py`) — 48 passed; giant-компонентов нет (общий `CharacterFormFields` с `mode`) |

---

## 7. Риски и решения

| Риск | Оценка | Решение |
|------|--------|---------|
| Раздача `/static` в dev (порт 3000) | низкий | proxy `/static → :8000` в `vite.config.ts` |
| Nested модалки (профиль поверх Settings) | низкий | ✅ Этап 3 — обе модалки на `--z-modal: 100`, порядок в DOM (`ChatView`): профиль после настроек; Escape-стек закрывает только верхнюю |
| Изоляция знаний при добавлении appearance | средний | ✅ Appearance только в character card (self) и в scene-блок для same-location; тест изоляции; witness-логика не менялась |
| Внешность в scene-state extraction для всех персонажей | низкий | ✅ Осознанное решение: в `format_character_descriptor` appearance НЕ включать (п.2.4) |
| Большие/невалидные файлы | средний | magic-byte валидация, лимит размера, ресайз через Pillow |
| Расхождение с макетом | средний | Ручная сверка на Этапах 3 и 6 (PNG из docs/) |
| Инерция кликов в правой панели (была детализация) | средний | ✅ Этап 4 — клик по строке → единый профиль; inline-детализация сохранена через кнопку «Подробности» (шеврон); финальный UX (selectedId/активная подсветка, отношения изнутри модалки) — Этап 5 |

---

## 8. Порядок работ (сводно)

1. ✅ **Backend A** (модель/схема/миграция) → тесты `test_character_profile.py` (A-часть).
2. ✅ **Backend B** (avatar storage + endpoints + Pillow) → тесты.
3. ✅ **Backend C** (appearance в контекст, изоляция) → тесты `test_prompt_builder.py` / `test_context_builder.py`.
4. ✅ **Frontend 1** (типы/api/store/mocks) → `npm run build` (vue-tsc strict) проходит.
5. ✅ **Frontend 2** (Avatar component: xl/circle/avatar_url + vite-прокси `/static`) → `npm run build` (vue-tsc strict) проходит.
6. ✅ **Frontend 3** (CharacterProfileModal редизайн + перенос монтирования) → `npm run build` (vue-tsc strict) проходит.
7. ✅ **Frontend 4** (три точки входа) → `npm run build` (vue-tsc strict) проходит.
8. ✅ **Frontend 5** (Player: имя + аватар + внешность; cleanup `updatePlayerName`) → `npm run build` (vue-tsc strict) проходит.
9. ✅ **Frontend 6** (polish + визуальная проверка) → `npm run build` (vue-tsc strict) проходит; релевантные backend-тесты — 48 passed (полный `pytest` в этой среде не завершается: integration-тесты ждут живой backend/ollama — не связано с изменениями, backend не менялся на этапах 2–6).

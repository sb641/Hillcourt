# AGENTS.md — закон репозитория Hillcourt

Документ обязателен к прочтению любым агентом до первой правки файла.
Он отвечает на три вопроса: **кто где пишет**, **как вызывается роль**, **что считается готовым**.
Приоритет документов: `docs/00_constitution.md` > этот файл > профильные доки (`docs/01`–`docs/06`).

## 0. Три правила, которые нельзя нарушать никогда

1. **Инварианты `docs/00_constitution.md` выше любой задачи.** Если задача требует нарушить инвариант — остановись и напиши ADR, а не ломай закон.
2. **Чужая папка неприкосновенна.** Агент пишет только в свою зону (раздел 2). Правка чужой зоны = откат, а не «улучшение».
3. **Нет проверки — нет результата.** Документ без критерия проверки и код без зелёного теста — брак, даже если красиво.

## 1. Язык и имена

- Документы, комментарии к решениям, ADR — **русский**.
- Имена файлов, папок, сущностей, полей, id в YAML — **английский `snake_case`**.
- Числовые поля носят единицу в имени: `grain_kg`, `labor_days`, `rent_share`, `travel_days`.
- Заголовки документов — русские. Идентификаторы внутри — английские.

## 2. Карта репозитория и владельцы

Правило: **у пути есть владелец.** Нет строки — агент не имеет права писать, даже если
задача его явно касается. Это блокирующий дефект (ADR 0166), а не формальность: за день
три агента работали в бесхозных файлах, пока таблица молчала.

| Путь | Владелец | Кто ещё может писать |
|---|---|---|
| `docs/00_constitution.md` | Scribe | никто без ADR |
| `docs/0*.md` | Scribe | профильная роль через предложку |
| `docs/decisions/*.md` | любой агент | только добавление новых ADR, старые не редактируются |
| `docs/fiction/` | Scribe | Orchestrator (продуктовое видение) |
| `sim/README.md`, `sim/SEAT_PLACE.md` | Scribe | профильная роль через предложку |
| `design/catalogs/goods.yml`, `design/catalogs/recipes.yml`, `design/catalogs/spawn_rules.yml`, `design/catalogs/hazards.yml`, `design/catalogs/seasons.yml` | Economist | никто |
| `design/catalogs/manor.yml`, `design/catalogs/needs.yml`, `design/catalogs/actions_household.yml` | Economist | никто |
| `design/catalogs/obligations.yml`, `design/catalogs/rights.yml` | Legal | никто |
| `design/catalogs/calendar_v0.yml`, `design/catalogs/legal_statuses.yml`, `design/catalogs/offices.yml`, `design/catalogs/land_regimes.yml` | Legal | никто |
| `design/catalogs/README.md` | Scribe | Economist, Legal (только свои разделы) |
| `design/scenarios/*.yml`, `design/scenarios/*.md` | Implementer | Economist (только секции `stocks`/`recipes`) |
| `design/review/` | системный дизайнер | Critic (только `critic_canon_*.md`), Scribe (только `manor_accounts_v0.md`, `backlog_v0.md`) |
| `design/art/` | Illustrator | World (только вид), системный дизайнер (только спека и манифесты) |
| `sim/src/hillcourt/engine/`, `sim/src/hillcourt/world.py` | Implementer | никто |
| `sim/src/hillcourt/ontology.py`, `sim/src/hillcourt/catalogs.py`, `sim/src/hillcourt/ledger.py`, `sim/src/hillcourt/scenario.py`, `sim/src/hillcourt/runner.py`, `sim/src/hillcourt/__init__.py` | Implementer | никто |
| `sim/src/hillcourt/economy/` | Economist | никто |
| `sim/src/hillcourt/legal/` | Legal | Economist (только чтение) |
| `sim/src/hillcourt/news/`, `sim/src/hillcourt/info/` | Info | никто |
| `sim/src/hillcourt/hazards/` | Implementer | Info (только тексты вестей через предложку) |
| `sim/src/hillcourt/engine/tile_view.py` (только вид/данные, без механики) | World | никто без приказа хозяина |
| `sim/tests/` | Implementer (каркас и общие обвинители) | **профильная роль пишет тест своей зоны**; Scribe — `sim/tests/test_docs_claims.py` (проверка `docs/`, `AGENTS.md`, реестра) |
| `tools/prompts/` | Orchestrator | никто |
| `tools/generate_illustration.py` | Illustrator | никто |
| `client/` | — | **заморожено в фазе 0** |
| `README.md`, `STATUS.md`, `HANDOFF.md`, `CHANGELOG.md`, `.gitignore` | Orchestrator | никто |

Проверку «у пути есть владелец» держит `sim/tests/test_docs_claims.py`
(`TestEveryTrackedPathHasAnOwner`): путь из репозитория, которого нет в этой таблице,
роняет тест. Список исключений — `KNOWN_OWNERLESS`.

Правило одной правки: агент меняет только те файлы, что нужны для его задачи,
и не «причёсывает» соседние.

## 3. Роли и как они вызываются

Роль — это не персона, а контракт. Роль вызывается передачей ей:
`роль → цель → входные файлы → критерий приёмки → запрещённая зона`.
Промпты-карточки лежат в `tools/prompts/<role>.md`.

| Роль | Отвечает за | Пишет в | Не пишет в |
|---|---|---|---|
| **Economist** | товары, рецепты, хранение, порча, рента, правила появления материи | `design/catalogs/{goods,recipes,spawn_rules,hazards}.yml`, `sim/src/hillcourt/economy/` | `docs/00`, `sim/src/hillcourt/news/`, `client/` |
| **Legal** | права, повинности, недоимка, переход держания | `design/catalogs/{obligations,rights}.yml` | экономика-числа, `docs/00` |
| **Info** | рождение/задержка/искажение/смерть известия, всеведение игрока | `sim/src/hillcourt/news/`, предложки в `docs/05` | каталоги, экономику |
| **Critic** | адверсариальная проверка: вечный двигатель, дюп, всеведение, скрытый tech-tree | отчёт в `docs/decisions/` (review) | любой код и законные доки |
| **Implementer** | каркас Python, тесты, runner, сценарии | `sim/`, `sim/tests/`, `tools/` | каталоги Economist/Legal, `client/` |
| **Scribe** | документы `docs/` | `docs/` | `sim/`, `design/catalogs/` |
| **Orchestrator** | решения, приёмка, ADR, конфликты | весь репо | — |

**Critic всегда независим**: он не автор проверяемого кода и не «дорабатывает» его.
**Scribe вызывается только после Critic** и правит формулировки, а не факты симуляции.

## 4. Протокол работы агента

1. Прочитай `AGENTS.md`, `docs/00_constitution.md`, `docs/03_ontology.md`.
2. Определи, в какую папку писать (раздел 2). Если папки нет в таблице — спроси оркестратора, не создавай самовольно.
3. Сделай минимальную правку, которая решает задачу.
4. Проверь по Definition of Done (раздел 5) для своего типа артефакта.
5. Обнови/добавь тест, если менял код.
6. Если решение спорное — добавь ADR в `docs/decisions/`.
7. Handoff: назови изменённые файлы, команду проверки и её вывод.

## 5. Definition of Done

### 5.1 Документ

- [ ] Есть три обязательных блока: **Назначение**, **Правила**, **Проверка**.
- [ ] «Проверка» — конкретна: команда, тест или однозначный ручной критерий. Не «смотреть глазами».
- [ ] Нет обещаний, которым нет сущности или поля в `docs/03_ontology.md`.
- [ ] Нет художественного лора без сущности в онтологии (имена, ордена, империи, реликвии).
- [ ] Объём — операционный: до ~2 экранов, без романа.
- [ ] Изменение инварианта сопровождается ADR.

### 5.2 Код

- [ ] `bash sim/run_tests.sh` — зелёный.
- [ ] Новый объект материи имеет в каталоге **место хранения**, **рецепт** или **правило появления**.
- [ ] Тик не создаёт материю из ничего: `test_matter_conservation` зелёный.
- [ ] Симуляция не импортирует графику/Godot; рендер не считает ренту.
- [ ] Детерминизм: одинаковый `seed` → одинаковый результат.
- [ ] Нет вызовов LLM в тике двора и нет pathfinding всех людей каждый день.
- [ ] Публичный интерфейс описан docstring-ом.

### 5.3 Каталог / YAML

- [ ] У каждой записи есть `id` (english snake_case) и `name` (русский).
- [ ] У записи есть `storage`, `recipe` или `spawn_rule` (что применимо).
- [ ] Все ссылки на другие `id` существуют (проверяется тестом загрузчика).
- [ ] Проверка: `sim/tests/test_catalog_rules.py`.

## 6. Жёсткие запреты (нарушение = откат)

- Не делать RTS-туман и не симулировать pathfinding всех людей на всей карте каждый день.
- Не делать классы `Knight`/`Explorer`; не звать LLM в тик каждого двора.
- Не писать Godot-сцены первыми; сначала симуляция, данные, тесты. `client/` в фазе 0 заморожен.
- Не раздувать каталог реликвий и лор империй.
- Симуляция не знает спрайтов; рендер не считает ренту.
- Новый объект без места хранения, рецепта или правила появления — брак.
- Документ без критерия проверки — брак.
- Не ломать чужие папки: Economist не переписывает каталоги Legal и наоборот.
- Не притаскивать чужие движки/ассеты стратегий как основу.
- **Ключом словаря нельзя ставить датакласс.** `@dataclass` обнуляет `__hash__`
  (`type(world).__hash__ is None`), поэтому `World` и остальные 24 датакласса
  `ontology.py` непригодны как ключ `dict`/`set`/`WeakKeyDictionary`/`WeakSet` —
  `WeakKeyDictionary()[world]` падает с `TypeError`. Кэш уровня мира — только
  `id(obj)` рядом с `weakref.ref`, как в `economy/soil.py:249`.
- **Объект ушёл в `weakref.ref` ⇒ в дереве обязан быть обход, который чистит запись**
  (сверка `ref() is obj` либо явный сброс по событию). Иначе запись висит на висящем
  `id()`: новый объект получит тот же адрес, прочитает чужой кэш, и тик создаст материю
  из ничего. Нарушение = откат, как и вечный двигатель.
- Не коммитить секреты. Репо должен открываться без секретов и без обязательного Godot.

## 7. Проверка

```bash
bash sim/run_tests.sh
```

Эквивалент без скрипта:

```bash
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_*.py' -v
```

`pytest` не обязателен, но тесты написаны на `unittest` и собираются им тоже.

## 8. ADR

- Нумеруются `docs/decisions/NNNN_short_title.md`.
- Формат: Контекст → Решение → Альтернативы → Последствия → Что запрещено следующим агентам.
- Старые ADR не редактируются. Решение пересматривается новым ADR, который ссылается на старый.

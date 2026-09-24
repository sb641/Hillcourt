# 0001. Bootstrap фазы 0

## Контекст

Репозиторий был пуст (только `README.md` и `.gitignore`). Задача сессии — создать workspace,
в котором другие агенты смогут работать, не уничтожая архитектуру: монорепо для headless-симуляции
на Python, каталогов YAML, документов-законов и будущего Godot-клиента. Без чужих движков и ассетов.

Работа выполнена ролями: Orchestrator (решения и приёмка), Implementer (каркас `sim/`),
Critic (адверсариальный обзор, см. `docs/decisions/0002_review_phase0.md`), Scribe (правка документов
под критику). Состав ролей и их зоны — в `AGENTS.md`.

## Решение

1. **Дерево монорепо.** `docs/` (закон и ADR), `design/catalogs/` + `design/scenarios/` (данные),
   `sim/` (headless-симуляция), `tools/prompts/` (карточки ролей), `client/` (Godot, заморожено).
2. **Один Python-компонент `sim/` со src-layout** (`sim/src/hillcourt/`, `sim/tests/`). Не
   uv/poetry-workspace: в окружении нет `pip`, `uv` и `pytest`, поэтому тесты — на стандартном
   `unittest`, запуск `bash sim/run_tests.sh`; единственная внешняя зависимость — `PyYAML`.
3. **Закон — `AGENTS.md` + `docs/00_constitution.md`.** Владелец каждой папки зафиксирован;
   чужую зону правит только владелец.
4. **Материя.** Введён `Ledger` с проводками `transfer` / `process` / `external_in` /
   `external_out`. `external_in` возможен только по правилу появления (`spawn_rules` → рост извне).
   Инвариант И-1 проверяется `sim/tests/test_matter_conservation.py`.
5. **Известие ≠ мир.** Введены `Report`, `ReportView`, `PlayerView`; знание игрока строится только
   из доставленных `Report`, сосед/шериф/обоз искажаются `rng_news`, молчание достижимо,
   устаревание — `STALE_AFTER_MONTHS = 2`. Проверка — `sim/tests/test_news_no_omniscience.py`.
6. **Seed-экономика v0.** Товары `grain/firewood/peat/salt/brine`, рецепты `harvest_grain`,
   `cut_firewood`, `cut_peat`, `boil_salt` (помечен `transform: true`), держание через
   `Settlement.works_tiles`. Двор работает, пока хватает труда/стоячей материи/входов; еда — в приоритете.
7. **Доказательство фазы.** Прогон 36 месяцев: 119 датированных известий, 1 двор уходит и не
   возвращается, рента платится и копится в недоимку, дельта материи `0.000000`, хеш состояния
   стабилен между процессами. Тестов — 21.

## Альтернативы

- **uv/poetry-workspace из нескольких пакетов** — отклонено: нет инструментов, для одной
  runtime-единицы это лишняя сложность. При появлении нескольких Python-компонентов вернуться к ADR.
- **pytest как обязательный раннер** — отклонено: не установлен. Тесты написаны на `unittest`
  и совместимы с pytest, если он появится.
- **Пул обработки без проверки состава (`emit` без `allowed_goods`)** — отклонено после находки
  Critic F-1: позволяло молча подменить вещество. Теперь `emit` требует объявленный набор рецепта,
  а нетрансформационные рецепты не могут выдать товар, которого нет на входе (тест `test_catalog_rules`).
- **Игрок читает истину через `Report.facts`** — отклонено после находки Critic F-2. Теперь числа
  соседей искажаются, вводится `PlayerView`.
- **Гексагональная сетка** — отклонено в пользу квадратной как более простой для v0.

## Последствия

- Зоны Implementer/Economist/Legal/Info/Critic/Scribe разведены; новые агенты пишут по `AGENTS.md`.
- **Отложено и зафиксировано как deferred:** исполнение `spawn_rules` для `hazard`/`pack`/`ruin`
  (обоз и шериф существуют только как `Report`, не как `Pack`); объект руины не порождается
  (`Tile.ruin_id` пуст); соляная деревня не агрегируется по LOD и соль не участвует в обмене.
- **Хеш детерминизма** покрывает дату, стоки, `tile.hazard_ids`, поля дворов (`mood`, `labor_days`,
  недоимка, уход), здоровье людей, повинности и известия. Не покрывает `Person.curiosity/fear`
  (в v0 не меняются) — расширять при первом изменении.
- **Bootstrap-исключение по владению.** При создании каркаса Orchestrator правил `sim/src/hillcourt/economy/`
  (зона Economist) и `sim/src/hillcourt/engine/` (зона Implementer), чтобы довести экономику до
  рабочего состояния. Дальше эти зоны принадлежат Economist и Implementer соответственно.
- Seed-числа экономики подобраны Orchestrator (`grow_grain.amount = 10.0`); это seed, а не баланс.

## Что запрещено следующим агентам

1. Менять `docs/00_constitution.md` инварианты без нового ADR.
2. Читать игроку истину `Tile`/`Household`/`Stock` в обход `Report`/`PlayerView`; возвращать точные
   числа в `neighbor`/`sheriff`/`caravan`.
3. Ослаблять `Ledger.emit`: убирать `allowed_goods` или разрешать рецепту выдавать необъявленный товар.
4. Добавлять рецепт с `transform: false`, чей выход/потери не объявлены на входе; ломать массовый
   баланс `sum(inputs)+sum(draws_standing) == sum(outputs)+sum(loss)`.
5. Молча включать отложенные `spawn_rules` (hazard/pack/ruin) без теста и без обновления `docs/02`.
6. Писать в чужую зону (`design/catalogs/` Economist ↔ Legal; `sim/src/hillcourt/news/`; `client/`).
7. Делать `client/`, Godot или `pip`/`pytest` обязательными для тестов и прогона.
8. Раздувать каталог реликвий/лор империй, вводить классы `Knight`/`Explorer`, звать LLM в тике,
   симулировать pathfinding всех людей ежедневно.

## Проверка

```bash
bash sim/run_tests.sh   # 21 тест, OK
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --print-log
```

Критерий: тесты зелёные; прогон 36 месяцев печатает датированные `Report` (с пометкой искажения `?`),
дельта материи `0.000000`, ушёл ровно один двор; повторный прогон даёт тот же `state_hash`.

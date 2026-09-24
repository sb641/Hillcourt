# GD Intake: карта фактов v0

## Назначение

Стол плейтеста. Что РЕАЛЬНО есть в репозитории и что даёт прогон — до всяких оценок.
Источник: код `sim/src/hillcourt/**`, `sim/tests/**`, `design/scenarios/v0_hill_and_salt.yml`,
прогоны runner 36 и 60 месяцев. Роли-исполнители: Reader + SimRunner. Оценок здесь нет.

## Правила

### 1. Тесты

`bash sim/run_tests.sh` → `Ran 108 tests ... OK`, 0 fail.

### 2. Сущности в коде против онтологии

| Сущность | Есть в коде | Место |
|---|---|---|
| Person | да | `ontology.py`, создаётся только `scenario.py` |
| Household | да | `ontology.py`, `world.households` |
| Tile | да | `ontology.py`, `world.tiles` |
| Stock | да | `ontology.py`, виды `household/settlement/tile/pack/sink/manor` |
| Obligation | да | `ontology.py` |
| Report | да | `ontology.py`, `news/propagation.py` |
| Pack | да | `ontology.py`; создаётся только `legal/actions.send_party` (тесты) |
| Hazard | да | `ontology.py`, статичный набор из `initial_hazards` |
| Settlement | да | `ontology.py` |
| Right (Tenure) | да | `ontology.py`, `world.rights` |
| Manor (сущность) | да | `ontology.py`, `world.manors` + `world.player_manor_id` |
| grant_thegn (вложенный манор) | да | `engine/manor.py:230`, `depth ≤ 1` |
| Muster как тело (вооружённый человек) | **нет** | `Manor.mustered` — bool, `engine/manor.py:379` |

### 3. PlayerAction: что реально в коде

| Действие | Файл:строка | Пишет в `player_actions` |
|---|---|---|
| `grant_tenure` | `legal/actions.py:94` | **нет** |
| `send_party` | `legal/actions.py:24` | **нет** (cargo пустой) |
| `add_obligation` | `legal/actions.py:119` | **нет** |
| `set_tile_regime` | `engine/manor.py:111` | да |
| `grant_tenement` | `engine/manor.py:130` | да |
| `call_boon` | `engine/manor.py:182` | да |
| `ease_week_work` | `engine/manor.py:193` | да |
| `grant_thegn` | `engine/manor.py:230` | да (или `grant_thegn_rejected`) |
| `revoke_thegn` | `engine/manor.py:323` | да |
| `revoke_tenure`, `set_rent_share`, `send_pack`, `send_messenger`, `patrol` | — | **не реализованы** |

Runner никогда не вызывает действий игрока: `runner.run` = `load_scenario` + цикл `run_month`
(`runner.py:70-78`). `world.player_actions` в лог прогона не печатается.

### 4. Прогон: сырые числа

| seed | мес | живых дворов | ушло (людей) | ренты | голодных | ходок | известий | замок зерно | замок соль | материя | дельта |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1729 | 36 | 12 | 2 (8) | 28.8 | 33 | 3 | 119 | 155.26 | 4.0 | 4280.085 | 0 |
| 1 | 36 | 12 | 2 (8) | 28.8 | 33 | 1 | 120 | 155.26 | 4.0 | 4280.085 | 0 |
| 2 | 36 | 12 | 2 (8) | 28.8 | 33 | 2 | 120 | 155.26 | 4.0 | 4280.085 | 0 |
| 7 | 36 | 12 | 2 (8) | 28.8 | 33 | 3 | 119 | 155.26 | 4.0 | 4280.085 | 0 |
| 1729 | 60 | 12 | 2 (8) | 43.2 | 81 | 3 | 199 | 119.74 | 4.0 | 5750.375 | 0 |

Уходят ровно два двора: `hh_06` (free_landless, Y1-M10) и `hh_07` (sokeman, Y2-M03).

### 5. Голод: 33 дворовых-месяца

| Пресет | Дворовых-месяцев | Дворы |
|---|---|---|
| villein | 27 | `hh_08` 25 (mood 0 на конец), `hh_02` 2 |
| sokeman | 3 | `hh_07` (потом уходит) |
| free_landless | 3 | `hh_06` (потом уходит) |
| holder / thegn / cotter / geneat | 0 | — |

Ключевые статы 36 мес: corvee 1556, board_grain 252, hired 212, gafol 34, boon 9,
demesne_grain 384.9, relief_given 75, cold_months 134, slave_labor 1440, sow 16, animals_starved 2.

### 6. Материя и путь соли

Дельта материи 0. `external_in` — природные `grow_*`; `transfer` — harvest/eat/board/rent/…;
`process` — harvest/mill_flour/boil_salt/…

Соль в конце 36 мес (seed 1729): всего 21.6; `household:hh_salt_01` 5.867,
`household:hh_salt_02` 5.733, `household:hh_court` 6.0 (стартовая), `settlement:hill_court` 4.0
(стартовая, **не изменилась**). `boil_salt` дал 52.2 за прогон; **transfer соли = 0**;
потребление соли = 0 (`goods.yml`: `category: material`, `edible: false`, нет `nutrition`).
Обоз существует только как `Report`.

### 7. Разрыв seed

Сценарий объявляет `seed: 1729`. Прогон без флага даёт `state_hash c197cfe42bba`, прогон
`--seed 1729` даёт `677cfbde3a26` — **разные миры**. Причина: `load_scenario` потребляет поток
`rng.economy` (проверено: `economy`-состояние после загрузки ≠ свежему `from_seed(1729)`;
`world/news/hazard` совпадают). `runner.run` при заданном `seed` пересоздаёт потоки и обнуляет
уже потраченное. Оба режима детерминированы внутри себя.

### 8. Отложенное — проверено, не слух чата

- `spawn_rules` с `target ∈ {pack, hazard, ruin}` не исполняются: `phase_growth` берёт только
  `kind == calendric AND target == good` (`engine/tick.py:75`). `caravan_visit` (cargo salt,
  destination hill_court) мёртв.
- Соляная деревня вне книги манора: `household.manor_id = null` (`scenario.py:316-326`),
  `_on_manor` её отсекает (`economy/manor.py:148-153`).
- Дневного контура нет: только `Clock.advance_month`; фаз, итерирующих всех `Person`, нет.
- `offices.yml` (`produces_report`, sheriff) парсится, но нигде не читается; `messenger`-Report
  безусловен и не привязан к шерифу.
- `Tile.terrain = ruin` есть, объекта/события руины нет; `Tile.ruin_id` пуст.
- `bog` размещён в сценарии, но месячного эффекта не даёт (только `travel_risk` при пути).
- ADR: 0001,0002,0003,0004,0006,0007,0008,0010. Номера 0005 и 0009 свободны.

### 9. Документация против кода

`docs/08_manor.md:67-71` утверждает, что перевод гэфоля/посева/урожая тэна на `manor.stock_id`
«в работе», тогда как код `economy/manor.py` уже делает всё по-манорски. Документ отстал от кода.
`docs/03_ontology.md:80-82` обещает, что каждое `PlayerAction` порождает `Report`; три действия
из `legal/actions.py` не пишут ни в лог, ни в известия.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --print-log
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729 --print-log
```

Критерий: тесты 108/108; в 36-мес логе живых 12, ушло 2, соль замка 4.0, голодных 33; два
прогона из последних команд дают разные `state_hash` (фиксирует разрыв seed).

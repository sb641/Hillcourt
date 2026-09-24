# 0043. Фаза топ-апа волков (Implementer, исполнитель `wolves_den`)

Дата: 2026-09-23. Роль: Implementer. Исполнитель контракта ADR 0042 в
`phase_hazard` (зона своя: `engine/tick.py` + тесты). Весть — параллельно за
Info по `report_required`; его зону (`news/`, `info/`) не трогал.

## Контекст

Правило `wolves_den` вшито (цикл 12), исполнителя `target==hazard` в тике не
было. Живые волки сценариев: `intensity`/`population` 0.6 на лесной клетке.
Кража: `intensity × 0.5`; давление пути: `population × голод` (`hazards/model.py`).

## Решение

`_apply_hazard_topups(world)` в начале `phase_hazard` (до кражи: подросшая стая
крадёт уже в этом месяце):

- Перебор правил `target==hazard, mode==topup` (сейчас только `wolves_den`);
  всё числовое — из `params` правила, хардкода чисел нет.
- Отбор: угроза активна, `kind` совпадает, клетка — террейна правила.
- Бросок `base_prob` — именованным потоком из `rng_stream` (`hazard`, И-6);
  бросок всегда потребляет позицию потока у отобранных (каденция не зависит
  от состояния).
- Приросты `intensity_gain`/`population_gain`, рез потолками
  `intensity_cap`/`population_cap`. Новых сущностей `Hazard` нет.
- Топ-ап материю не двигает (числа угрозы, не зерно): дельта 0, И-1 цел.
- Каждый рост метится для Info (сам `Report` — не здесь): счётчик
  `wolves_den_topup` (`bump`) и штамп месяца `wolves_den_topup_<hazard_id>`
  (`year × 12 + month`) в `world.stats`. Роста не было — метки нет;
  рост, упёршийся в потолок, метки не ставит.

Метка для Info (читает в `phase_inform`, весть — его код):

| Ключ `world.stats` | Смысл |
|---|---|
| `wolves_den_topup` | число срабатываний за прогон |
| `wolves_den_topup_<hazard_id>` | месяц последнего роста угрозы (дата события вести) |

## Альтернативы

- Режим spawn новых логов — отклонено (контракт 0042, И-3: весть ниоткуда).
- Топ-ап после кражи — отклонено: рост-then-использование повторяет порядок
  `phase_growth` (сначала природа, потом руки).
- `Report` из фазы движком — отклонено: весть ordered за Info, чужая зона;
  движок оставляет только метку.
- Новое поле `Hazard` под метку — отклонено: сущность/поле только через ADR,
  а бокового канала `world.stats` (прецедент: `eased_days_*`,
  `boon_called_month`) достаточно.

## Последствия

- Сюит **436 OK** (429 + 5 новых фазы + 2 параллельных Info `test_threat_report`,
  дерево общее, зелень общая).
- Канон (числа цикла 8 бит-в-бит, дельта 0 везде; хеши ниже — честный сдвиг):

| Сценарий | Живые/соль/голод | Хеш было → стало | Топ-апы |
|---|---|---|---|
| hill 36/1729 | 12/15.6/31 | `a5ffefe13285` → `2797748c9bc5` | 3 (0.6 → 3.6/1.0) |
| two 60/42 | 14/15.6/341 | `453bc32f6843` — без изменений | 0 (бросок не выпал) |
| shire 60/1729 | 26/15.6/547 | `74dfb26b2624` → `913fe579a070` | 6 (капы 6.0/1.0) |

- Гейты: two 14 ≥ 14 (кромка, предрегистрация цикла 7 в силе: 13 =
  перебазировка); shire 26 ∈ 24–28. Сдвиг хеша — только метки `stats` и
  `population` (она в хеше; `intensity` — нет): поведение окон не двигает.
- Замечание честности: two при seed 42 топ-апов не видел — хеш не сдвинулся
  случайно, а не структурно; при других seed рост будет (потолки держат).
- `test_rule_is_silent_without_phase` переписан в
  `test_phase_topup_only_no_spawn_caps_hold` (молчание ложно при живой фазе;
  контракт каталога `TestWolvesDenRule.test_dens_topup_existing_wolves_only`
  не тронут).

## Что запрещено следующим агентам

1. Рожать новые `Hazard` топ-апом; поднимать потолки без ADR с окнами.
2. Рождать `Report` о росте из движка (зона Info) и читать мир в весть напрямую (И-3).
3. Менять порядок «топ-ап до кражи» без замера окон.
4. Считать метку `stats` истиной для игрока — это задел вести, не знание.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_wolves_den_phase sim.tests.test_catalog_rules -v
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_two_settlements.yml --months 60 --seed 42
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_shire.yml --months 60 --seed 1729
```

Критерий: сюит зелёный; рост/потолок/без-новых/детерминизм/дельта 0;
канон — числа цикла 8, two ≥ 14, shire 24–28, дельта 0.

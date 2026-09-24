# Место стола усадьбы холма (сущность)

## Назначение

Усадьба холма — МЕСТО стола, а не запись книги: клетки зала, двора замка
и ближних полей под холмом. Держатель видит соседние клетки сам без почты,
быстрее высылает людей на кромку и держит мир рядом (hazard слабее).
Дальний выселок живёт как раньше: только Report. Урожая место не даёт.
Модуль — `sim/src/hillcourt/engine/seat.py`; книга — `Manor.seat_*`
(онтология `Manor`, владелец Implementer). Решение — ADR 0023.

## Правила

- Корень `manor_hill`: `seat_tile_id` — клетка зала; `seat_tiles` — зал +
  ортогональные соседи; `eye_range_tiles = 1`, `peace_range_tiles = 1`.
  У тэна место пусто.
- Глаз: `eye_tiles` — манхэттен 1 от зала. О каждой кроме зала — Report
  `eye_from_hill`, задержка 0, `confidence` 1.0, `noise` 0.0,
  «видно с холма», `facts={grain_approx, hazard.kind?}`. Дальняя соль —
  только `caravan`/`silence` («узнали письмом» как канал). RNG и материя
  не тронуты.
- Задержка: `order_delay_months` — 0 рядом, 1 вдали. `send_sally` — только
  на соседнюю клетку, Pack `party`, `eta=date+delay`. Рядом — глазом,
  вдали — гонцом. Лог `player_actions` с `delay_months`.
- Мир: одно правило — кража `grain→waste` ×0.5 на клетке мира, иначе ×1.0.
  Всхода, иммунитета, урожая, ренты, порчи место не меняет.
- Запреты: служба тэна, grant/revoke, service_gap, комплекты, календарь
  барщины, животные, реки, pathfinder суши, yield, поселения, третий тэн,
  Godot — не тронуты.

## Проверка

```bash
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_seat_place.py' -v
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
```

Критерий: 10 зондов `test_seat_place.py` зелёные — seat у корня, сосед
виден глазом 0 мес, соль только письмом, задержка 0 vs 1 и eta Pack,
мир 0.5 vs 1.0, дельта материи 0, возы `load 7/unload 5/11.6/ext 0`;
`run_tests.sh` зелёный; приёмка держится кроме хеша (сдвиг этим ADR).
Не закрыто при «бонусе замка к зерну».

# 0081. Исполнение отчёта по событиям месяца (Implementer)

Дата: 2026-09-24. Роль: Implementer. Исполнены ADR 0078 и ADR 0079 в зоне
`engine/`, `world/`, `sim/tests/`; старые ADR не редактировались.

## Назначение

Закрыть соляную дыру: отчёт обоза должен описывать движение груза за месяц, а не
остаток склада после фаз. Игрок получает только доставленный `Report`; источник
истины для отчёта — плоский список событий месяца.

## Правила

1. `engine/events.py::month_events(world)` читает проводки `Ledger` текущего месяца и
   возвращает `caravan_departure`, `caravan_arrival`, `pack_departure`, `pack_arrival`,
   `harvest`, `hay_mowed`, `graze`.
2. Записи содержат `kind`, `good`, `amount`, `date`, `month`, `reason`, `src_id`,
   `dst_id`; для потоков поселения выводятся `settlement_id` и `household_id`, для клетки —
   `tile_id`.
3. `household:*` атрибутируется к поселению через владельца двора: соль действительно уходит
   со стока соляного двора, а не с несуществующего поселенческого амбара.
4. `phase_inform` заполняет `World.month_events` до `make_month_reports`; `Report` получает
   поток, а `delay_days` остаётся задержкой канала, не временем пути.
5. Порядок событий детерминирован: дата, вид, товар, `src_id`, `dst_id`. Отток не скрывается;
   отсутствие событий даёт честный ноль. Новых мировых сущностей и полей нет.

## Проверка

```bash
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_news_month_events sim.tests.test_caravan_report sim.tests.test_caravan_report_h -v
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_month_events -v
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_two_settlements.yml --months 60 --seed 42
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_shire.yml --months 60 --seed 1729
```

Целевые детекторы: 9/9; событийные тесты: 9/9. Полный сюит выявил только чужие
падения: `test_caravan_module.TestHonestDaysFodder.test_paved_route_eats_less`,
`test_caravan_module.TestHonestDaysFodder.test_river_pack_stays_finite`,
`test_caravan_module.TestHonestDaysFodder.test_unpaved_pays_full_days`,
`test_caravan_trade.TestCaravanTrade.test_road_on_route_shortens_days_and_keeps_delta`.

| Сценарий | Живые/соль/голод | Дельта | Хеш первого/повторного прогона |
|---|---:|---:|---|
| hill 36/1729 | 13 / 15.6 / 28 | 0 | `79796030ead0` / `79796030ead0` |
| two 60/42 | 17 / 15.6 / 383 | 0 | `7fddcf96722c` / `7fddcf96722c` |
| shire 60/1729 | 25 / 15.6 / 535 | 0 | `73f56b9133d6` / `73f56b9133d6` |

## Альтернативы

- Снимок склада после фаз отклонён: приход и отток в одном месяце взаимно гасились бы.
- Скрытие оттока отклонено: игрок должен различать отсутствие груза и его уход.
- Перенос расчёта времени пути или фуража в этот срез отклонён: это отдельная зона Economist.

## Последствия

Соляный канал больше не пуст из-за семантики остатка; появились плоские события как
контракт отчёта. Числа канонов не вышли из окон §1. Хеши two/shire сдвинулись относительно
ADR 0078 из-за добавленных Legal полей в состояние мира, не из-за событий; детерминизм
сохранён.

## Что запрещено следующим агентам

1. Строить соляный отчёт от остатка стока после фаз.
2. Исключать `caravan_departure` или скрывать отток.
3. Подменять события месяца чтением складов игроком или сдвигать `delay_days` в путь.
4. Добавлять мировые сущности/поля ради этого отчёта.
5. Менять фураж, дорожные числа или `economy/caravan.py` в рамках ADR 0079; это зона Economist.

# 0003. Формальная модель права и известия (сессия Legal + Info)

## Контекст

Сессия «Юрист + разведка» должна превратить «игрока на холме» в формальную модель информации
и права: режимы земли, правовые статусы, повинности, известие по месту и опасность как
популяцию. До неё в репозитории были `Right`/`Obligation` без режимов земли и без правила
«кто может уйти/быть послан», `Report` без шума и со старым словарём источников
(`hill/neighbor/sheriff`), а `Hazard` был одной интенсивностью без популяции и сытости.

В том же workspace параллельно работала экономическая сессия (агент Economist): агентные
решения двора, `economy/`, каталоги `needs.yml`/`actions_household.yml`, поля
`Household.main_action/minor_action/adventurism/rumor_fear/tool_wear/traveling`,
`World.needs/stats`. Правки разведены по зонам; общие файлы (`ontology.py`, `catalogs.py`,
`scenario.py`, `tick.py`, `world.py`) интегрированы точечно.

## Решение

1. **Режимы земли.** Каталог `design/catalogs/land_regimes.yml`: `demesne`, `tenement`,
   `waste`, `reserved_wood`, `foreign`. Единый словарь действий `plough`,
   `gather_brushwood`, `take_game`, `build_hut`, `leave`. Режим хранится в `Tile.regime_id`;
   `foreign` не хранится, а выводится (`legal/regimes.py::effective_regime_id`), когда действие
   совершает двор, не держащий землю. `allowed_actions` урезает набор по статусу.
2. **Статусы людей.** Каталог `design/catalogs/legal_statuses.yml`: `holder`,
   `free_household`, `bound_labor`, `retainer` с флагами `can_leave`, `owes_rent`,
   `can_be_sent`, `may_hold_land`. `Household.legal_status_id` задаётся в сценарии.
   Вассал-вассала нет: соседний держатель — равный (`vassalage_allowed() == False`),
   соляная деревня имеет статус `holder` и не платит ренту игроку.
3. **Повинности.** `Obligation.basis ∈ {share, fixed, duty}`, `corvee_days`, `duty_days`.
   Рента-доля и фиксированный мешок — `legal/obligations.py::rent_amount_for`; повинность на
   частокол копит дни в `corvee_days`, а `due_amount` остаётся 0 (дни не путаются с зерном).
4. **Должности.** Каталог `design/catalogs/offices.yml`: `sheriff`, `huntsman` с
   `produces_report` и `travel`. Должность — дорогой рот, что ездит и рождает `Report`.
5. **Словарь источников.** `Report.source` переименован в
   `{eye_from_hill, adjacent_daily, messenger, caravan, silence}` (`info/sources.py`).
   Добавлено `Report.noise` (0..1) как мера разброса канала; `distorted` остаётся булевым
   следом подмены. `phase_inform` делегирует в `info/briefing.py`.
6. **Знание по месту.** Новый слой `info/knowledge.py`: `KnowledgeEntry`
   (`about`, `source`, `observed_month`, `arrived_month`, `noise`, `confidence`, `distorted`,
   `content`, `facts`, `stale`) и `PlayerKnowledge` (`latest(about)`, `silences()`).
   `player.knowledge[about]` — последний доехавший `Report` о месте, не `Tile.state`.
   `build_player_knowledge` читает только доставленные `Report`. `STALE_AFTER_MONTHS = 2`.
   Молчание — отдельный сигнал, а не «всё хорошо».
7. **Слух авантюриста.** `info/rumor.py`: риск похода оценивается по собственному слуху
   (`facts['hazard']`), а не по истинному `Hazard`.
8. **Опасность как популяция.** `Hazard.population`, `Hazard.satiety`;
   `hazards/model.py::per_person_loss_risk` даёт квадратичную защиту строя (одиночка гибнет
   много чаще отряда), `hazards/encounter.py` разыгрывает потери по людям,
   `hazards/travel.py` разрешает `Pack` и при полной гибели шлёт `silence`, а не пересказ волка.
9. **Действие `send_party`.** `legal/actions.py`: посылка людей только двором со статусом
   `can_be_sent`, только на соседнюю клетку (без pathfinding).
10. **`RightTemplate.land_regime_id`** добавлен, чтобы шаблон права мог задавать режим земли.

## Альтернативы

- **Хранить `foreign` на клетке.** Отклонено: режим относителен двора; один и тот же участок
  для держателя — `tenement`, для чужого — `foreign`.
- **Лесенка статусов в духе CK3.** Отклонено: v0 требует узкой модели; четыре флага покрывают
  «уйти / платить / послать / держать».
- **Оставить старые имена источников и добавить новые алиасы.** Отклонено: два словаря
  расходятся и ломают проверку; миграция сделана целиком, тест обновлён.
- **`corvee_days` через `due_amount`.** Отклонено после красного rent-теста Economist:
  дни повинности нельзя суммировать с зерном. Введено `duty_days`.
- **Ивент-таблица опасностей (шанс из таблицы).** Отклонено: популяция+сытость дают
  зависимость шанса от размера группы и от того, сыта ли опасность.

## Последствия

- `phase_inform` больше не собирает истину клеток напрямую: это делает `info/briefing.py`,
  а игрок читает `KnowledgeEntry`.
- В `tick.py` добавлены `phase_travel` (после `phase_migrate`) и ветка трудовой повинности;
  `phase_migrate` пропускает двор, у которого `can_leave == False`.
- `world.state_hash()` покрывает `legal_status_id`, `regime_id`, права, посылки, опасности
  (`population`/`satiety`) и `corvee_days`/`duty_days`.
- Тесты: `test_legal_regimes.py`, `test_info_knowledge.py`, `test_hazards_encounter.py`,
  `test_travel_silence.py`; `test_news_no_omniscience.py` обновлён под новый словарь.
- Приёмка: `test_info_knowledge.py::test_player_does_not_read_distant_stock` (деревня голодает,
  а знание игрока до вестника старое) и
  `test_travel_silence.py::test_lost_party_yields_silence_not_wolf_replay` (трое не вернулись →
  `silence`, не пересказ волка).

## Отложено в v0 (честно)

1. **Производство не сверяется с режимом земли.** `allowed_actions` — источник правды и покрыт
   тестом, но `economy/labor.py` выбирает рецепты без вызова режима: `bound_labor`/`reserved_wood`
   всё ещё могут жать. Подключение — задача экономической зоны.
2. **Должности как рты.** Каталог `offices` загружается, но вычет труда и найм не смоделированы.
3. **Труд повинности.** `corvee_days` копится по периоду, но `labor_days` не списываются.
4. **Груз посылки.** `Pack.cargo` создаётся пустым и не загружается/не выгружается; перенос
   материи обозом — отдельная задача (И-1 при этом не нарушен).
5. **Ежедневный контур.** Разбор `Pack` идёт месячной фазой `phase_travel`; дневного контура нет.

## Что запрещено следующим агентам

1. Возвращать старые имена источников (`hill`/`neighbor`/`sheriff`) или добавлять в `Report`
   ссылки на истину `Tile`/`Household`/`Stock`.
2. Трактовать `silence` как «всё хорошо» или убирать отдельный сигнал молчания.
3. Давать игроку читать запас дальней деревни в обход `build_player_knowledge`.
4. Складывать `corvee_days` и ренту в одно поле; снова писать дни повинности в `due_amount`.
5. Вводить вассал-вассала, лесенку статусов CK3 или путь длиннее одного шага без pathfinding.
6. Превращать опасность в случайный ивент из таблицы, оторванный от популяции и сытости.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m hillcourt.runner \
  --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --print-log
```

Критерий: тесты зелёные (52+), прогон 36 месяцев печатает датированные `Report` с источниками
`eye_from_hill`/`adjacent_daily`/`messenger`/`caravan`/`silence`, дельта материи `0.000000`;
`test_legal_regimes.py`, `test_info_knowledge.py`, `test_hazards_encounter.py`,
`test_travel_silence.py` проходят.

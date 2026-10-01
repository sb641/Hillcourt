# 05. Известие

## Назначение

Как рождается, тухнет и врёт известие. Инвариант И-3: игрок знает не мир, а `Report`.
Модуль-владелец — `sim/src/hillcourt/info/` (роль Info).

## Правила

### Рождение
`phase_inform` раз в месяц вызывает `info.briefing.make_month_reports`: холм — всегда,
сосед — с вероятностью 75 % (иначе молчание о соседях), гонец (шериф) — всегда. Отчёты обоза
о соли и дальней деревне строятся по событиям месяца, а не по календарному «снимку склада»;
при отсутствии события допустим честный ноль. Плюс отдельно `Report` о прибытии воза и молчание
о погибшем возе. Каждый `Report`
собирается по схеме: `event_date` (когда случилось) → канал → `delivery_date` (когда дошло) →
`content`/`facts` (как рассказано) → `confidence`/`noise`. Отчёты месяца строятся из событий;
календарные события и отток груза — через `World.month_events` (ADR 0079/0081). Гибель отряда
даёт молчание, а прибытие ушедшего двора
(`Pack kind=household_move`, `status=arrived`) — `messenger`-отчёт о клетке назначения
с `facts={household_id, tile}` (`hazards/travel.py::resolve_migrations`).
Семантика `facts['tile']`: это клетка рассказа (`about = subject_id = destination`),
а не координата двора — при тесноте (`rejected`) двор физически остаётся на `origin`
(`Pack.origin_tile_id`), но весть рассказывает о тесноте клетки назначения; при частичной
потере добавляется `facts['lost']` (число погибших в пути).

### Весть об угрозе
Подросшая стая видна только вестью. Топ-ап существующих волков (`wolves_den`,
`mode: topup`, потолки `intensity_cap` 1.0 / `population_cap` 6.0 — ADR 0042;
фаза `_apply_hazard_topups` в начале `phase_hazard` — ADR 0043) каждый рост метит
для Info (счётчик `wolves_den_topup` и штамп месяца `wolves_den_topup_<hazard_id>`
в `world.stats`; роста не было или рост упёрся в потолок — метки нет), а весть
рождает `news/threats.py::report_pending_topups` в `phase_inform` по меткам
текущего месяца (проводка — ADR 0044). Форма (`make_threat_report`): канал
`messenger`, `subject_kind="tile"`, `subject_id=tile_id`,
`facts={hazard: {kind, population_approx}, tile}`; `tile` — about по правилу
tile-about, а не координата кого-либо. Численность — слуховая
(`population_approx` ±15 % потоком `rng.news`, `distorted`, когда не сошлось),
вид (`kind`) — точный, чтобы `rumor.perceived_risk` считал по слуху, а не по
истинному `Hazard` (И-3). Задержка 0 (событийная весть, как отчёты об
уходе/прибытии — новых задержек нет), `confidence` 0.5, `noise` 0.15. Дедуп
`source+subject_id+event_date` (повторный `phase_inform` молчит); прошлые штампы
игнорируются. Роста не было — вести нет: тишина, а не «всё хорошо».

Рождение ватаги видно той же вестью. `band_camp` (`mode: spawn` — ADR 0045;
фаза `_apply_hazard_spawns` между топ-апом и кражей — ADR 0046) после метки
`<rule_id>_birth_<hazard_id>` синхронно зовёт `news.threats.report_pending_births`
и требует весть о клетке рождения в ответе — иначе откатывает рождение (после
фазы либо связка, либо ничего). Форма (`make_birth_report`) — та же, что у
топапа (канал `messenger`, задержка 0, `confidence` 0.5, `noise` 0.15,
`tile` = about, численность слуховая, вид точный); отличается только текст:
«засела новая шайка» — игрок читает рождение, а не дрейф чисел. Дедуп общий.

### Схема известия
Проводной `Report` (`ontology.py`) несёт `source`, `subject_kind`, `subject_id`, `content`,
`facts`, `event_date`, `delivery_date`, `confidence`, `distorted`, `noise`. Запись знания
`KnowledgeEntry` (`info/knowledge.py`) переводит это в поля задачи:

| `KnowledgeEntry` | Откуда |
|---|---|
| `about` | `Report.subject_id` — о каком месте рассказ |
| `source` | `Report.source` |
| `observed_month` | абсолютный месяц `event_date` |
| `arrived_month` | абсолютный месяц `delivery_date` |
| `noise` | `Report.noise` — типичный разброс канала |

### Каналы и задержка
| `source` | Кто свидетель | Задержка | `confidence` | `noise` | Искажение |
|---|---|---|---|---|---|
| `eye_from_hill` | глаз зала корня (радиус 1) | 0 мес | 1.0 | 0.0 | нет: точное «зерно около» + вид `Hazard` |
| `adjacent_daily` | соседний двор | 1 мес | 0.7 | 0.3 | зерно ±30 %, ключ `grain_approx`, «сказывают» |
| `messenger` | гонец/шериф баронии | 1 мес | 0.5 | 0.15 | счёт дворов с недоимкой дрейфует на ±1 |
| `scout` | партия-разведчик (`Pack.purpose=scout`) | 0 мес | 1.0 факт / 0.5 слух | `rng_news` | факт/слух о наблюдённом гексе; `observer_id=Pack.id` |
| `caravan` | проходящий обоз | 2 мес | 0.4 | 0.4 | события месяца соли: `caravan_departure`/`caravan_arrival`, `pack_departure`/`pack_arrival`, `harvest`, `hay_mowed`, `graze`; зерно дальней деревни ±40 % (`grain_approx`); прибытие воза несёт `{carried, delivered, ratio, lost}` |
| `silence` | никто | 0 мес | 0.3 | 0.0 | `facts` пуст: «известий нет» |

Числа соседа, гонца и обоза сдвигаются потоком `rng_news`; `Report.distorted = True`, когда
доложенное число не совпало с истинным. Названия источников — словарь v0 (`info/sources.py`).
Задержка, `confidence` и `noise` — свойства канала (`sources.CHANNELS`), но конкретный отчёт
может их задавать: прибытие воза идёт с задержкой 0, `confidence` 0.6, `noise` 0.1;
весть об угрозе — с задержкой 0, `confidence` 0.5, `noise` 0.15.

### Разведка: факт, слух и границы
`Pack.purpose ∈ {party,scout}`; `profile_id ∈ {hunters,forester,party}`. `tower` в v1 нет.
Только активный `scout`-Pack с живыми участниками в одной клетке наблюдает. Гекс партии и
клетки маршрута — факт без броска. Кольцо 1 — вероятность профиля и покрова через
физический `rng_world`: `hunters=0.85` (следы жизни, силён в чистом поле),
`forester=0.75` (лесные знаки, силён в лесу), `party=0.55` (без подготовки); покров
лес/топя 1.0, луг 0.75, поле 0.5, `Hazard` ухудшает. Кольцо 2 — только слух
«дым/след/костёр» с `confidence=0.5` и `fact=False`. Дальше двух гексов — никогда;
партия видит только маршрут и кольцо 1–2, не «вглубь карты».

`observer_id` — `Pack.id`, не `"player"`; `source="scout"` отличает разведку от `messenger`.
Гекс открывается только из доставленного `Report`; слух не становится фактом. Текст и шум
разведки выбирает `rng_news`, физический бросок — `rng_world`: перестановка формулировок
не меняет физические события или хеш. Профили и их числа — закрытый закон ADR 0088;
пересмотр только новым ADR с замером. Запрещены наблюдение дальше 2 гексов, наблюдение
без Pack, башня-дозор без отдельного ADR, открывание гекса без доставленной вести, выдача
слуха за факт и формулировка из физического потока.
### Глаз холма — место стола, не всеведение
`eye_from_hill` — не «игрок смотрит куда хочет», а радиус от зала: `make_seat_eye_reports`
(после `info.briefing`) рождает `Report` о клетках радиуса `eye_range_tiles` = 1 (манхэттен)
от `Manor.seat_tile_id` корня, кроме самой клетки зала. Задержка 0, `confidence` 1.0,
`noise` 0.0, `facts` — `grain_approx` (**зерно живых дворов** клетки, `round` 0.1, без поля
`grain`) и вид `Hazard` без численности. Стоячий сток `Tile` не читается (И-3).
`peace_range_tiles` = 1 — не канал известия, а ослабление волчьей кражи (`peace_theft_factor`
0.5, см. `04`); «бонуса к зерну» место не даёт.
### Пустое поселение и знание
Двор без живых людей (`member_ids` пуст) не считается живым для вида: пустая клетка
`hill`/`field`/`pasture` получает форму `open_field`, а не форму поселения или `tribal_village`.
Пустота сама не порождает `Report` и не добавляет клетку в `known_tiles`; игрок узнаёт о месте
только из доставленного `Report`. Племенной guard в `phase_migrate` не отменяет это правило:
живые дворы племени не уходят сами, а полностью опустевшее поселение не показывается как
действующее племя (ADR 0068).

### Обоз: соль, рейс, дальняя деревня
Каналом `caravan` идут несколько разных отчётов, и источник каждого отчёта — события месяца,
а не снимок склада:

- **Соль** — `World.month_events` содержит `caravan_departure`/`caravan_arrival`,
  `pack_departure`/`pack_arrival`, `harvest`, `hay_mowed`, `graze`; выход и приход соли
  атрибутируются владельцу стока — двору, а не поселению. Отток — такое же событие, как
  приток. `make_month_reports` строит `Report` из этих событий, поэтому ненулевой отчёт
  означает событие, а нулевой — отсутствие события за месяц.
- **Прибытие воза** — `_report_caravan_arrivals`: Report о рейсе, `subject_kind="route"`,
  `subject_id="<origin>-><destination>"`, `facts={carried, delivered, ratio, lost}`, задержка
  0, `confidence` 0.6, `noise` 0.1. Числа берутся из памяти сделки `world.barter_memory`, а не
  из стока/груза; `ratio == delivered/carried` — доля доехавшего груза, не цена; `lost` —
  погибшие люди воза (у обоза `member_ids` пуст, поэтому 0). Нет записи памяти — отчёт не
  выдумывается.
- **Дальняя деревня** — `_report_far_villages`: поселение `kind == "village"` (`ash_village`)
  видно только через `caravan`; отчёт о зерне `facts={'grain_approx'}` из стоков живых дворов
  (`_grain_in_households`), задержка 2 мес, `confidence` 0.4, `noise` 0.4; 20 % — молчание о её
  клетке.
- **Гибель воза** — `_report_lost_packs` и `hazards/travel.py:resolve_pack_loss`:
  `Pack.status=lost` рождает `silence` о клетке назначения на дату гибели; повторно не
  дублируется (проверка `source+subject_id+event_date`).

Событийный канал подтверждён ненулевыми живыми прогонами. Table и Critic считают разные
подмножества отчётов (4/4, 11/11, 10/10 против 2/9, 3/15, 1/16), поэтому это не спор о едином
каноне: обе методики подтверждают, что канал жив. Игрок узнаёт соль только из доставленного
`Report`; `Tile.state` и склад поселения не являются источником вести (И-3).

### Молчание — отдельный сигнал
Молчание не значит «всё хорошо». Если ожидаемый канал не пришёл (сосед не выпал, обоз не
дошёл до дальней деревни), ставится `Report` с `source=silence` и пустыми `facts`: о соседях
(ключ `neighbors`), о дальней деревне (её клетка) или о клетке, откуда отряд не вернулся.
Потерянный отряд или обоз (`Pack.status=lost`) тоже рождает молчание о клетке (о клетке
назначения у обоза), а не пересказ волка: известие не приходит из пустоты
(`_report_lost_packs`, `hazards/travel.py:resolve_pack_loss`).

### Тухнет
`KnowledgeEntry.stale = (age > STALE_AFTER_MONTHS)`, где `STALE_AFTER_MONTHS = 2`
(`info/knowledge.py`): известие старше двух месяцев от `event_date` помечается устаревшим.
Просроченный факт не исчезает из истории, но строить на нём решение рискованно.

### Знание по месту
`PlayerKnowledge.latest(about)` — это **последний доехавший** `Report` о месте, а не `Tile.state`.
`build_player_knowledge(world, as_of)` собирает записи только из доставленных `Report`
(`delivery_date ≤ as_of`) через `news.propagation.delivered_reports`; другого доступа к истине
у игрока нет. `KnowledgeEntry` не имеет полей `Tile`/`Household`/`Stock`/`amounts`.

### Слух авантюриста
`info.rumor.perceived_risk(knowledge, about, group_size, hazard_rules)` считает риск по СВОЕМУ
слуху (`facts['hazard']` последнего известия), а не по истинному `Hazard`. Нет слуха — риск
считается низким (`UNKNOWN_HAZARD_RISK`), и потому двор может недооценить чащу.

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_info_knowledge.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_travel_silence.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_caravan_report_h.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_far_village_report.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_messenger_departure_ids.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_threat_report.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_band_spawn_phase.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_month_events.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_news_month_events.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_scouting.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_scouting_reports.py' -v
```

Критерий: `test_news_no_omniscience.py` зелёный — словарь источников v0, искажение и молчание
существуют, соседские `facts` содержат `grain_approx` (а не точное `grain`), `PlayerView` равен
множеству доставленных `Report`. `test_info_knowledge.py` зелёный — знание собирается только из
`Report`, у старых известий `stale=True` при `STALE_AFTER_MONTHS = 2`, а прогон-приёмка показывает:
дальняя деревня уже голодает в мире, но в знании игрока до приезда вестника всё ещё старое «норм».
`test_travel_silence.py` зелёный — посланные трое не вернулись, у игрока появляется `silence`,
а не пересказ волка. `test_caravan_report_h.py` зелёный — прибытие воза несёт
`{carried, delivered, ratio, lost}` в месяц прибытия (задержка 0), `ratio == delivered/carried`,
а `ReportView` не имеет полей истины мира. `test_far_village_report.py` зелёный — игрок слеп до
2-го месяца, `grain_approx` дальней деревни сходится с зерном дворов в пределах шума канала,
молчание не течёт истиной. `test_caravan_report.py`/`test_caravan_report_h.py` зелёные —
событийный соляный канал даёт ненулевые отчёты в живых прогонах, а отсутствие событий даёт
честный ноль; `test_month_events.py` проверяет виды событий и атрибуцию владельцу стока.
`test_messenger_departure_ids.py` зелёный — прибытие ушедшего двора даёт `messenger`-отчёт
с `household_id` и `tile` (о клетке назначения), при тесноте `tile` — тоже about
(`destination`), а двор физически на `origin`; при частичной потере есть `facts['lost']`.
`test_threat_report.py` зелёный — рост стаи даёт `messenger`-отчёт
о её клетке (`tile` = about, `population_approx` в пределах шума канала, `confidence`
0.5 / `noise` 0.15 / задержка 0); без роста — тишина, повторный `phase_inform` — без
дублей; знание собирается только из `Report`. `test_band_spawn_phase.py` зелёный —
гейт/потолки/одна-на-клетку/связка-или-откат/дельта 0; весть о рождении — та же
форма («засела новая шайка»), в `PlayerView`.

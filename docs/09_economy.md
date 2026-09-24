# 09. Экономика (обзор)

## Назначение

Единая точка входа в экономику v0: инварианты, единицы, поток месяца, кто чем
кормится, и таблица «правило → где задано → чем проверяется». Это **обзор со
ссылками**, а не второй источник истины: числа живут в каталогах, сущности — в
`docs/03`, порядок фаз — в `docs/04`, право — в `docs/07`/`docs/08`.

Владелец: Economist. Кто МОЖЕТ и ЧТО ДОЛЖЕН — Legal; здесь — чем это кормится.

## Правила

### 1. Инварианты (не нарушаются)

- **И-1, материя не из ничего.** Любое количество — `Ledger.transfer` между
  стоками или `external_in` по правилу появления с `rule_id`. `Ledger.emit`
  разрешён только для товаров рецепта. Проверка: `test_matter_conservation`.
- **И-7, объект обязан иметь место.** У товара есть `storage`; у результата —
  `recipe` или `spawn_rule`. Проверка: `test_catalog_rules`.
- **И-6, детерминизм.** Случайность — через `rng_economy/world/news/hazard`;
  порядок фаз и сущностей фиксирован. Проверка: `test_tick_runs`.

### 2. Единицы (полностью — `design/catalogs/README.md`)

| Величина | Обозначение | Смысл |
|---|---|---|
| масса | `unit` | 1 мешок зерна; взрослый съедает ~1 unit/мес |
| питательность | `nutrition` | зерно 1.0, мука 1.2, мясо 1.5 |
| труд | `labor_days` | 1 трудодень = день взрослого; взрослый = 20/мес |
| надел | `tile_plot` | 1 полевая клетка = виргата (30 акров); гайда = 4; коттер = 0.5 |
| воз | `Pack` | обоз — реальный `Pack` с грузом, фуражом, износом телеги, риском пути и гибелью (A1/H) |
| доля/риск | `0..1` | безразмерно |

### 3. Поток месяца (19 фаз, `docs/04_tick.md`)

`season → growth → manor → hay → roadworks → labor → spoil → consume → demography → obligations → exchange → hazard → migrate → caravan → travel → day → decide → inform → record`

| фаза | что делает с материей |
|---|---|
| `phase_growth` | природа: стоячая материя по `spawn_rules` (`external_in`, потолок `cap_per_tile`) |
| `phase_manor` | **труд**: вернуть двору месяц труда → барщина/рабы в пул СВОЕГО манора → паёк из амбара своей книги (`manor:<id>`, корень — замок) по `monthly_food_need` двора → найм → гэфоль и посев → урожай домена в амбар своего манора (не больше спроса) |
| `phase_hay` | косьба сена дворами с тяглом из рук после барщины, до стройки и поля (сено первое; единственная, ADR 0050) |
| `phase_roadworks` | остаток рук после барщины и косьбы (≤40 трудодней/мес на проект, ADR 0067) → проект дороги/брода/моста; дни клетки и материалы ADR 0039 не меняются; материал `log` списан при `start_work` в `sink:waste`; без рук нет прогресса |
| `phase_labor` | двор работает на **своём наделе** (`feeds_household`); домен сюда не входит |
| `phase_spoil` | порча в `sink:waste` |
| `phase_consume` | еда → `sink:eaten`; зимнее топливо и корм скота; нехватка → `hunger_days` |
| `phase_demography` | сытый двор растит семью: старение, недоросли в тягло, роды от излишка (люди — не материя) |
| `phase_obligations` | натуральная рента (`transfer` двор → замок) и запись барщины |
| `phase_exchange` | подмога, тайники, соседский торг (свои пары, затем чужие пары кластера с ценой носителя) — только `transfer` |
| `phase_hazard` | волчья кража зерна; риск пути |
| `phase_migrate` | уход двора на соседнюю клетку, если `can_leave`; держатель ЛЮБОГО манора (корень и тэн) не уходит — фьеф держит (J1/I3) |
| `phase_caravan` | обозы `Pack` по правилам `caravan_visit`/`caravan_grain_to_ash`: груз, фураж, износ телеги, риск клетки и гибель (`resolve_pack_loss`); излишек еды — выше `need_buffer` 2 мес |
| `phase_day` | только `Pack` в пути: сутки месяца и штатное прибытие по `eta_date`; дворы и экономика не обходят по дням |
| `phase_decide` | выбор действий на следующий месяц |

Домен и надел — **разные стоки времени**: рука, ушедшая на домен, свой надел в
этот месяц не молотит (барщина списывается до `phase_labor`).

### 3a. Время и логистика
`HOURS_PER_DAY = 24.0`; якорь поля: нога 0.65 ч, конь 0.40 ч, обоз 0.85 ч, `arms` 0.8125 ч
(нога ×1.25). `entry_hours = entry_cost × hours_scale`; террейновые пропорции сохраняются,
тропа/грунт/road получают прежние скидки. `find_path` даёт `travel_hours`, `travel_days`
округляет вверх до суток, `travel_months` — производная для лога. `Pack.eta_date` хранится
по суткам, месяц равен 30 суткам; в логе приказа видны `travel_hours`, `days` и `months`.
Месяц остаётся основным тиком; `phase_day` обслуживает только `Pack` в пути (ADR 0078).

### 3b. Каноны текущего среза
Окна §1 не менялись: hill 8–15, two 14–18, shire 24–28; соль 15.6. Измеренные числа
текущего среза: hill 13/28, two 17/383, shire 25/535 (живые/голод; соль в каждом 15.6).
Сдвиг записывается честно и не подменяется старыми значениями.

### 3c. Соляная дыра
ADR 0079: проект закона о соляной дыре **принят, в работе, ещё не реализован**. В коде нет
механики, поля сущности или отчёта «снимок склада»; документ не обещает ни одного такого
отчёта. В рабочем проекте отчёт должен описывать события месяца «пришло/увезено», когда
механика будет реализована.

### 4. Кто чем кормится (пресеты — `docs/07`, `docs/08`)

| Пресет | Надел | Работа | Еда |
|---|---|---|---|
| `holder` / `thegn` | свой | сам не пашет | паёк из амбара своей книги |
| `sokeman` | свой | редкий вызов | надел + фикс. рента 0.6/мес |
| `geneat` | свой | вызов на домен | надел |
| `villein` | виргата (1.0) | барщина 8–16 дней/мес | надел − барщина + гэфоль |
| `cotter` | клочок (0.5) | барщина 4–16 | клочок + найм в страду |
| `free_landless` | нет | найм за еду/пенс | найм; рано уходит, если не нанят |
| `slave` | нет | 20 дней/мес на домене | паёк из амбара своей книги |

**Вложенный манор (тэн).** `grant_thegn` переносит право: клетки и дворы уходят
из книги корня в книгу тэна (`Household.manor_id`), режимы не меняются. У каждого
манора свой амбар (`Manor.stock_id`: корень — замок, тэн — `manor:<id>`). Труд,
спрос, найм, гэфоль, паёк, посев и урожай — **по манору двора**: тэн пашет свой
домен в свой амбар, его виллан платит тэну, его раб ест из амбара тэна. Корень
после пожалования от этого двора не получает и его не кормит. Домен/помочи —
только корень. Контракт — ADR 0010.

### 5. Надел, община, инструмент

- **Ёмкость клетки (`plot_cap`).** `manor.yml::plot_batch_cap_per_tile` = 4 партии/мес;
  `_plot_cap` = 4 × `holding_scale`, но это лимит на КЛЕТКУ, а не на двор. Держимые
  клетки (`_held_tile_ids`: усадьба и `Right`) делят общий счёт клетки: два двора на
  одной клетке дают 4 партии (не 8), один двор на двух своих клетках — до 8 (по 4 с
  каждой). Проверка: `test_plot_cap_per_tile`.
- **Общинный сбор.** `waste`/`reserved_wood` — не держание, а право ДОСТУПА:
  `has_access_to_communal_tile` принимает только `Settlement.works_tiles` или клетку с
  `Right.kind = common` для двора либо его поселения. Соседство даёт встречу и путь,
  но не communal-доступ; `own_tiles` и `_held_tile_ids` не меняются. Ёмкость клетки общая
  (4), каждый двор не берёт полную виргату (`_communal_collection_shared`, J3).
  `common` не становится индивидуальным наделом; `grazing` остаётся личным держанием.
  Чужой двор без `works_tiles`/`common` доступа не имеет. Проверка:
  `test_common_right_access`, `test_communal_access` (ADR 0077).
- **Племя v1.** `Tribe.settlement_id` связывает `native_village` с `Settlement`; состав
  берётся из `Settlement.household_ids`, поэтому поля состава или отдельной книги в `Tribe`
  нет. Двор племени — `free_landless`, но `phase_migrate` не даёт ему уйти самому. При
  `stance = allied|vassal` и `tribute_grain = 0.6` создаётся `levy` фиксированного
  натурального оброка 0.6 зерна/мес на каждый живой двор; неоплата идёт в `arrears`, а
  удаление/смена стойки снимает повинность и долг. `muster_kits` ограничивает потенциал
  вызова и не создаёт `Pack` или действие. Проверка: `test_tribe_v1`, `test_tribe_economy`.
- **Инструмент.** Выход жатвы: нет инструмента 1.0 < `wooden_plough` 1.05 < `iron_share`
  1.15 (`tool_yield_factor`, `TOOL_YIELD_FACTORS`). Инструмент — материя: железный лемех
  выдаётся из стока лорда переводом (`engine/manor.py::grant_tool`), а железо идёт
  рецептами `smelt_iron_bloom`/`smith_iron_share` в очереди `idle_repair` (гейт сытости,
  ADR 0037); деревянный плуг и телега — рецептами `craft_wooden_plough`/`craft_cart`. Проверка:
  `test_grant_tool`.
- **Тягло (ADR 0025).** Вол/конь удешевляют ТРУД партии `harvest_grain` (вол ×0.7,
  конь ×0.9 — хуже вола, осёл/молодняк ×1.0; `economy/livestock.py`). Без тягла плуг
  живёт за полный труд. Воз: осёл ×1.2 ёмкости, конь ×1.3 и дни ×0.75 поверх телеги
  (`origin_draft`); вол воза не знает. Проверка: `test_draft_livestock`.

### 6. Обмен между поселениями (обоз)

Обоз — реальный `Pack`, а не только `Report` (A1/H). Правила — `SpawnRule` с
`target=pack` и `params.kind=caravan` (`economy/caravan.py::caravan_rules`):

- `caravan_visit`: соль `salt_village → hill_court`, каждые **3** мес, `cargo_amount` 4.0.
- `caravan_grain_to_ash`: зерно `hill_court → ash_village`, каждые **4** мес, `cargo_amount` 6.0.

Груз уходит со стоков origin в сток воза (`caravan_load`), фураж — в `sink:eaten`,
остаток — в склад назначения (`caravan_unload`); материя не создаётся (`external_in 0`,
телепорт household→castle 0). Съедобный груз (зерно) не вывозится ниже
`need_buffer_months` 2.0 месячной нужды живых дворов origin (`caravan_below_buffer`) —
посев и зима не продаются; соль — не еда, буфера нет. Цен нет вовсе: `barter_memory`
(ключ `origin->destination:good`) хранит `carried`/`delivered`/`ratio`/`lost`, где
`ratio` — доля доехавшего груза, а не меновое число. На рейсе телега изнашивается
(`cart_wear` 0.05, ломается ниже 0.5, `cart_broken`), риск опасной клетки рассеивает
долю груза (`loss_share` 0.25), катастрофа 0.25 губит воз целиком через
`hazards/travel.py::resolve_pack_loss` (груз — в стоячий сток клетки, `status=lost`,
`silence`). **Маршрут строит `find_path` профилем `caravan`** (ADR 0029): возницы землю
знают (`known=None`), дорога/брод/мост дешевят и живой обоз, река без переправы
непроходима; нет пути — воз не идёт. Часы — сумма часов входа из `find_path`; сутки
округляются вверх, месяц — производная для лога. Ёмкость с
телегой ×1.5, без неё ×0.6 и время ×1.5; тягло поверх (осёл ×1.2, конь ×1.3 и время ×0.75).
Фураж и риск — по клеткам после origin. Игрок
видит обоз только через `Report`. Проверка: `test_caravan_h`, `test_caravan_trade`,
`test_barter_memory`, `test_caravan_module`.

### 7. Платежи и материя (только `transfer`)

- **Рента/гэфоль.** Сокмен — фиксированный `fixed_rent_grain_or_pence` 0.6/мес
  (Obligation). Виллан — `in_kind_martinmas` 4 зерна в мес 11. Это **не доля
  всего добытого**.
- **Паёк.** Амбар СВОЕЙ книги → двор (`board_grain_per_adult` × `monthly_food_need`
  двора × буфер против порчи) рабу/лорду; чужой амбар не кормит (G2).
- **Посев гэфоль-акров** (мес 10–11). Зерно из амбара виллана → стоячая материя
  поля домена его манора.
- **Найм.** Только если домен не закрыт барщиной и у амбара своего манора есть
  зерно/серебро.

### 8. Решения двора (один выборщик)

`economy/decisions.py` — один выборщик на все пресеты; набор действий — из
пресета (`allowed_action_ids`). Порядок: подмога при голоде → еда → зимнее
топливо → починка → скот → риск. Авантюризм = интерес − страх − цена рук на
поле. Держатель любого манора (корень и тэн) не уходит — фьеф держит (J1/I3);
`tied` (виллан/коттер) не уходит легально; свободный вне книги (`free_landless`,
часть сокменов) уходит.

### 9. Износ и природа

- Топор тупится только на «топорных» рецептах (`needs.tool.wear_recipes`), масса
  уходит в `sink:waste`; ниже порога ломается.
- Свинья плодится только при паре (`pig ≥ 2`) и расходует производителя.
- Тягло (ADR 0025, оживлено ADR 0030): три вида с полом в стоке (`ox_m/ox_f`, `donkey_m/donkey_f`,
  `horse_m/horse_f`; молодняк `ox_calf/donkey_foal/horse_foal`; общего `horse`
   нет). Корм сезонный (правка хозяина, цикл 19; ADR 0053/0055): зимой — сено полной
   нормой из своего стока, летом (мес. 5–9) — 30 % сеном + 70 % выпасом
   (`graze_for_herd`); выпас — только из стоячего сена пастбищ и не больше кормовой
   ёмкости клетки (пастбище 2.0/мес, остальное 0 — `pasture_forage_capacity`, только
   чтение), перевод в `sink:eaten` (причина `graze`); недобор выпаса — снова сеном из
   стока, затем падёж прежней пропорцией; бесплатной травы нет. Пастбища стада:
   амбар — клетки книги, склад — клетка + `works_tiles`. Косьба — единственная, ранней фазой `phase_hay`
  до поля (поздний добор в `livestock_month` удалён, ADR 0050), только при нехватке
  (`gather_draft_hay`, рецептом `gather_hay`; труд — из остатка, нового не создаётся; партии идут в общий кап клетки
  (тот же кап 4, счётчик свой на фазу стада; набор клеток `hay_pastures` — свой надел +
  соседнее общинное пастбище — исключение); износ топора косьбой не идёт — только партии
  `work_month`, исключение ADR 0030); приплод редок (0.08/мес на пару, родители — условие, кобыла цела),
  взросление за сено (пол — жребий), падёж возможен (0.005/0.01). Конь — только у
  свободных и только свободным даёт тягло/воз (`draft_goods_for`, проводка `can_hold_horse`
  в `decisions`; `tied`-двор с конём бонуса не получает); рабочий конь нормой
  тэна не считается (только `war_kit`). Выгода тягла — там, где вяжет труд, а не кап
  партии: у двора с `holding_scale` 0.5 кап (2 партии) наступает раньше рук, и вол поля
  не прибавляет (честно, ADR 0030). Шир стартует с тремя парами (волы `hh_11`-виллан,
  ослы `hh_14`, кони `hh_09` — конь у свободного) и запасом сена; критерий пар —
  анти-голод (`starved` = 0 за 12 мес, естественный падёж допускается).
- Железо (ADR 0030, проводка засвидетельствована ADR 0037): рецепт `smelt_iron_bloom` (`iron 1.2 +
  firewood 0.6 → iron_bloom 1.0`, труд 10, баланс 1.8 = 1.8) закрывает И-7; рецепт
  `smith_iron_share` официален (`iron 1.2 + firewood 0.6 → iron_share 1.0`, труд 8) и
  подключён в очередь `idle_repair` (после топора и крицы); дорогое — только сытому при
  входах (гейт), бонус выхода держится `tool_yield_factor` (1.0/1.05/1.15 — числа не
  тронуты).
- Резерв косьбы (E1) удалён (ADR 0050): `_mow_reserve` жала поле под добор, который
  не косит, — полю теперь все руки; ранняя косьба `phase_hay` цела. Голодный кормит
  семью, не скот.
- Рубка рядом (E3): `cut_firewood`/`cut_wood`/`mine_iron`/`cut_peat` разрешены на соседнем
  лесу/топи без спавна; стартовый `log`/`works_tiles` — в сценарии.
- Покупка (E4/E7, соседский торг — ADR 0057): сначала свои пары клетки, затем чужие пары
  кластера (кластер — клетка + 4 ортогональных; каждая клетка ровно в одном, двойных
  сделок нет; носитель — двор, порядок по id): те же фикс-цены — бревно 0.5 зерном
  (кап 4), скот за зерно (вол 8, осёл 5, конь 12; молодняк дешевле), свинья 3.0 серебром,
  зерно 0.5 серебром; цена носителя — сбор 0.5 (`PORTER_FEE`) там, где покупатель сыт
  (свиньи/брёвна/скот), на голодном зерне — укус 0.25 (`PORTER_BITE`) с богатой стороны
  в отход; зерно через клетку ≤ 2.0 (`NEIGHBOR_GRAIN_MAX`); дар — без цены. Только
  `Ledger.transfer` + потери; продаётся излишек сверх пары (пара неделима), конь —
  только свободному; серебро из ничего не создаётся. Дальше соседнего кластера —
  только обозом-`Pack`. Спавн — отложен.
- Пул барщины (E6): спрос манора капирует пул (`_render_pool`); дни без доменных клеток —
  в `wasted_labor_days`, рабы вне капа.
- Смертность и роды (E8 + ADR 0031): `_die_by_age` + `needs.yml:death` (ставки низкие,
  баланс держат роды); расцвет в иммунитете для сценарных грантов (скрипты не падают);
  характер новорождённого — из хеша id (не ест `rng.economy`, жребий трио цел); люди —
  не материя.
- У каждого `grow_*` есть `cap_per_tile`: природа не бездонный кран.

### 10. Правило → где задано → чем проверяется

| Правило | Где задано | Проверка |
|---|---|---|
| Товары, хранение, порча | `design/catalogs/goods.yml` | `test_catalog_rules` |
| Рецепты, массовый баланс | `design/catalogs/recipes.yml` | `test_catalog_rules` |
| Скот и тягло: пол, корм, приплод, пахота, воз | `goods/needs/recipes.yml`, `economy/livestock.py`, ADR 0025 | `test_draft_livestock` |
| Выплавка крицы действием (`idle_repair`, гейт сытости, очередь E2) | `recipes.yml:smelt_iron_bloom`, `economy/labor.py`, ADR 0037 | `test_bloom_and_hay` |
| Лемех официален и в очереди `idle_repair` (гейт: сыт + входы) | `recipes.yml:smith_iron_share`, ADR 0033/0035 | `test_cycle_iron_share_prototype` |
| Косьба одна — ранняя `phase_hay` (добор и резерв удалены) | `economy/livestock.py::gather_draft_hay`, `phase_hay`, ADR 0050 | `test_bloom_and_hay`, `test_hay_before_field` |
| Покупка и соседский торг: фикс-цены, цена носителя (сбор 0.5/укус 0.25/кап 2.0/дар) | `economy/exchange.py`, ADR 0033/0035/0057 | `test_household_economy` (`TestNeighborPricing`) |
| Пул барщины под спрос; рабы вне капа | `economy/manor.py::_render_pool`, ADR 0033 | `test_manor_economy` |
| Смертность по возрасту; иммунитет расцвета для грантов | `economy/demography.py::_die_by_age`, `needs.yml:death`, ADR 0033 | `test_demography` |
| Конь только свободным; сено — единственной косьбой из остатка труда | `economy/livestock.py::draft_goods_for`, `gather_draft_hay`, `economy/decisions.py`, ADR 0030 | `test_bloom_and_hay`, `test_draft_livestock` |
| Стартовые пары шира (волы/ослы/кони + сено) | `design/scenarios/v0_shire.yml`, ADR 0030 | `test_shire_draft_start` |
| Потолок природы `cap_per_tile` | `design/catalogs/spawn_rules.yml` + `phase_growth` | `test_household_economy` |
| Рот/корм/дрова/износ | `design/catalogs/needs.yml` | `test_household_economy` |
| Сезонный корм и выпас с краем (зима 1.0, лето 5–9: 30 % сено + 70 % выпас, кап 2.0, недобор → сено) | `needs.yml`, `economy/needs.py::hay_need_rate`, `economy/livestock.py::graze_for_herd`, ADR 0053/0055/0056 | `test_draft_livestock` |
| Действия двора | `design/catalogs/actions_household.yml` | `test_household_economy` |
| Наделы, спрос домена, паёк, найм | `design/catalogs/manor.yml` | `test_manor_economy` |
| Сезонный выход на трудодень | `design/catalogs/seasons.yml` | `test_manor_economy` |
| Барщина по сезонам | `design/catalogs/calendar_v0.yml` (Legal) | `test_legal_regimes`, `test_manor_economy` |
| Голод без еды/труда | `phase_consume` | `test_household_economy` |
| Рента двор → замок | `phase_obligations` | `test_household_economy` |
| Гэфоль/паёк/посев — `transfer` | `economy/manor.py` | `test_manor_economy` |
| Роды от излишка, недоросли в тягло | `economy/demography.py`, `needs.yml:birth` | `test_demography` |
| Домен не пашется сам | `economy/manor.py::_work_demesne` | `test_manor_economy` |
| Вложенный манор: право и амбар | `engine/manor.py` (`grant_thegn`, `Manor.stock_id`), ADR 0010 | `test_manor_entity` |
| Урожай/гэфоль/паёк/посев по манору двора | `economy/manor.py` (`_work_demesne`, `_gafol`, `_board`, `_sow_demesne`) | `test_manor_economy` |
| Паёк/подмога по своей книге | `economy/manor.py::_board`, `economy/decisions.py::_own_book_grain` | `test_relief_by_book`, `test_root_barn_probe` |
| Ёмкость клетки на держание (`plot_cap`) | `economy/labor.py::_plot_cap`, `_held_tile_ids` | `test_plot_cap_per_tile` |
| Часы пути, ETA по суткам, `phase_day` для Pack | `engine/terrain.py`, `engine/path.py`, `ontology.py::SimDate`, ADR 0078 | `test_travel_hours`, `test_march_path`, `test_caravan_module` |
| Каноны §1 и честный сдвиг чисел | приёмочные прогоны Table, ADR 0077/0078 | Table 591 OK |
| Кап стройки 40 трудодней/мес; дни 20/14/28 и материалы 3/2/4 | `engine/roadworks.py`, ADR 0039/0067 | `test_roadworks` |
| Общинный `common`: доступ, не надел; `grazing` личный | `legal/regimes.py::has_access_to_communal_tile`, `economy/labor.py::own_tiles`, `_held_tile_ids` | `test_common_right_access` |
| Племя v1: guard ухода, оброк 0.6 при allied/vassal, `muster_kits` как данные | `economy/tribe.py`, `engine/tick.py`, ADR 0062/0063/0064/0066/0068 | `test_tribe_v1`, `test_tribe_economy` |
| Инструмент: выход, `grant_tool` | `economy/labor.py::tool_yield_factor`, `engine/manor.py::grant_tool`, `recipes.yml` | `test_grant_tool` |
| Тягло в пахоте (труд, не зерно с неба) | `economy/livestock.py::draft_plough_factor`, `economy/labor.py::_apply_set` | `test_draft_livestock` |
| Тягло в возу (ёмкость/дни поверх телеги) | `economy/livestock.py::origin_draft`, `economy/caravan.py` | `test_draft_livestock` |
| Обоз-`Pack`: фураж, износ, риск, гибель | `economy/caravan.py`, `hazards/travel.py::resolve_pack_loss` | `test_caravan_module`, `test_caravan_h`, `test_pack_loss` |
| Маршрут живого обоза `find_path` (дорога/брод/мост) | `economy/caravan.py::_dispatch_rule`, `engine/path.py::find_path`, ADR 0029 | `test_caravan_module` |
| Обмен между поселениями, `barter_memory` | `design/catalogs/spawn_rules.yml` (`caravan_visit`, `caravan_grain_to_ash`), `economy/caravan.py` | `test_caravan_trade`, `test_barter_memory` |
| Держатель манора не уходит (`can_leave`) | `legal/regimes.py::can_leave` | `test_fief_holder_cannot_leave` |
| Два амбара, зерно не двоится | `Manor.stock_id`, `Ledger.transfer` | `test_manor_economy`, `test_matter_conservation` |
| Свинья только за куплю/приплод | `economy/labor.py`, `exchange.py` | `test_household_economy` |
| `travel_adjacent` снимает руки | `economy/labor.py` | `test_household_economy` |
| Страх держит вне чащи | `economy/decisions.py` | `test_household_economy` |
| Материя за прогон | весь тик | `test_matter_conservation` |

### 11. Карта документов

| Уровень | Документ |
|---|---|
| Инварианты, объём, LOD | `docs/00`, `docs/02`, `docs/06` |
| Онтология, тик | `docs/03`, `docs/04` |
| Право, манор-книга | `docs/07`, `docs/08` |
| Решения экономики | `docs/decisions/0004`, `0007` |
| Столовый счёт (арифметика) | `design/review/manor_accounts_v0.md` |
| Единицы, DoD каталогов | `design/catalogs/README.md` |
| Данные | `design/catalogs/*.yml` |
| Код | `sim/src/hillcourt/economy/*.py` |

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_travel_hours -v
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_common_right_access sim.tests.test_communal_access -v
PYTHONPATH=sim/src python3 -m hillcourt.runner --scenario design/scenarios/v0_hill_and_salt.yml --months 36 --seed 1729
```

Критерий: все тесты зелёные; в логе видны `travel_hours`, `days` и `months`; прогон
36 месяцев завершается с `дельта материи 0.000000`; приёмочные каноны дают hill 13/28,
two 17/383, shire 25/535 при соли 15.6 и окнах §1.
Ревизия Scribe: каждое правило из §10 имеет файл-источник и тест.

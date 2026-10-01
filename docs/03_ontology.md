# 03. Онтология

## Назначение

Единственный источник сущностей и полей. Всё, что упомянуто в документах или коде, обязано
трассироваться сюда. Новая сущность или поле — только через ADR.

## Правила

Идентификаторы — english `snake_case`. Числовые поля носят единицу в имени (`grain_kg`, `labor_days`).
Владелец — роль, которая пишет модуль/каталог этой сущности.

### Ядро (8 + 3)

| Сущность | Поля (ключевые) | Владелец |
|---|---|---|
| `Person` | `id`, `name`, `household_id`, `age_class ∈ {child,adult,elder}`, `curiosity 0..1`, `fear 0..1`, `health 0..1`, `location_tile_id`, `personal_status ∈ {free,tied,slave}`, `land_relation ∈ {secure_holding,tenement,landless}`, `obligation_bundle?`, `age_months` | Implementer |
| `Household` | `id`, `name`, `settlement_id`, `member_ids`, `stock_id`, `labor_days`, `obligation_ids`, `hunger_days`, `arrears_days`, `mood 0..1`, `intent ∈ {stay,leave}`, `current_tile_id`, `left_at: SimDate?`, `legal_status_id` (пресет), `personal_status ∈ {free,tied,slave}`, `land_relation ∈ {secure_holding,tenement,landless}`, `obligation_bundle?`, `manor_id?`, `holding_scale`, `main_action`, `minor_action`, `adventurism 0..1`, `rumor_fear 0..1`, `tool_wear`, `traveling`, `food_streak`, `birth_count` | Economist |
| `Tile` | `id`, `coord (x,y)`, `terrain ∈ {hill,field,pasture,forest,marsh,heath,salt_flat,ruin,water}`, `standing_stock_id` (сток стоячей материи), `hazard_ids`, `settlement_id?`, `ruin_id?`, `regime_id`, `trail_wear`, `resource_productivity`, `road`, `ford`, `bridge` (теги пути: в игре ставятся только стройкой `start_work`; начальная карта сценария задаёт их секциями `fords`/`bridges`/`roads` — стартовое условие, не игровая краска) | Implementer |
| `Stock` | `id`, `owner_kind ∈ {household,settlement,manor,tile,pack,sink}`, `owner_id`, `amounts: dict[good_id,float]` | Economist |
| `Obligation` | `id`, `household_id`, `kind ∈ {rent,labor_duty,levy,muster}`, `basis ∈ {share,fixed,duty,measured}`, `due_amount`, `due_good`, `period_months`, `paid_total`, `arrears`, `corvee_days`, `duty_days`, `right_id`, `call_status ∈ {pending,met,refused,unable,in_service,returned,overdue}` (для `muster` —
`in_service|returned|overdue|unable|refused`; `pending`/`met` — базовые значения) | Legal |
| `Report` | `id`, `source ∈ {eye_from_hill,adjacent_daily,messenger,scout,caravan,silence}`, `subject_kind`, `subject_id`, `content`, `facts: dict`, `event_date: SimDate`, `delivery_date: SimDate`, `confidence 0..1`, `noise 0..1`, `distorted: bool`, `observer_id` | Info |
| `Pack` | `id`, `kind ∈ {pack,party,caravan,household_move}`, `purpose ∈ {party,scout}`, `profile_id ∈ {hunters,forester,party}` (`tower` не в v1; обычные Pack: `party`/`party`), `origin_tile_id`, `destination_tile_id`, `route: list[tile_id]` (маршрут `find_path`; сухопутный воз и приказы хранят с origin, речной воз `send_river` — без origin), `member_ids`, `cargo: Stock`, `departed_date`, `eta_date`, `status ∈ {in_transit,arrived,lost}`, `owner_household_id?`, `obligation_id?`, `lost_date?` | Implementer |
| `Hazard` | `id`, `kind ∈ {wolves,bog,band}`, `tile_id`, `intensity`, `population`, `satiety 0..1`, `active`, `spawn_rule_id` | Implementer |
| `Settlement` | `id`, `name`, `kind ∈ {hill_court,farmstead,salt_village,village,native_village}`, `coord`, `household_ids`, `stores_stock_id`, `works_tiles: list[tile_id]` | Implementer |
| `Right` (`Tenure`) | `id`, `holder_household_id`, `tile_id`, `kind ∈ {tenure,common,grazing}`, `granted_date`, `rent_share 0..1` (**переключатель «понесёт ли оброк», а не размер; размер — `ObligationTemplate.default_share`, ADR 0183**) | Legal |
| `Manor` | `id`, `holder_person_id`, `stock_id`, `tile_ids`, `tile_regimes`, `household_ids`, `parent_manor_id?`, `upward_bundle`, `demesne_labor_demand_this_month`, `demesne_labor_filled`, `grant_ids`, `mustered`, `prior_preset`, `service_kits_required`, `service_men_required`, `service_kits_held`, `service_men_held`, `service_met`, `service_gap`, `bad_service_months`, `iron_requested`, `seat_tile_id` (клетка зала, только корень), `seat_tiles`, `eye_range_tiles`, `peace_range_tiles` | Implementer |
| `Tribe` | ровно 6 полей: `id`, `name`, `stance ∈ {independent,allied,vassal}`, `settlement_id`, `tribute_grain`, `muster_kits` | Implementer |

Корень — `settlement:hill_court`, тэн — `manor:<id>`. `guard_ring` (кольцо тэна:
клетки книги + непосредственные соседи) — **вычисляется**, в сущности не хранится.

`Tribe ↔ Settlement.kind = native_village` — биекция: каждая нативная деревня связана
ровно с одним `Tribe.settlement_id`, а каждое `Tribe` — с одной такой деревней. В коде у
`Tribe` ровно 6 полей; ADR 0064 перечисляет тот же набор, хотя в его тексте встречается
слово «7 полей». Состав племени не хранится в `Tribe`: он вычисляется из
`Settlement.household_ids`; общая земля — клетки `Right.kind = common` и
`Settlement.works_tiles`. `Right.kind = common` даёт доступ, но не индивидуальный надел;
`grazing` остаётся личным держанием. `tribute_grain` — поле данных; оброк создаётся **только
после принятия в солидарности и только при `vassal`**, до этого повинностей **0**
(ADR 0140 п. 1, ADR 0145); `muster_kits` — только данные потенциала вызова
(ADR 0060, 0062, 0064, 0068).

### Каталожные сущности (данные, не код)

| Сущность | Поля | Владелец |
|---|---|---|
| `Good` | `id`, `name`, `category ∈ {food,fuel,material,lux,livestock}`, `storage ∈ {granary,cellar,barn,open}`, `spoil_per_month`, `edible`, `nutrition`, `price_silver`, `price_labor_silver`, `price_materials_silver`, `price_losses_silver` | Economist |
| `Recipe` | `id`, `name`, `place ∈ {tile,settlement}`, `requires_terrain: list[terrain]`, `inputs: dict[good_id,float]`, `draws_standing: dict[good_id,float]`, `outputs: dict[good_id,float]`, `loss: dict[good_id,float]`, `labor_days`, `transform: bool` | Economist |
| `HouseholdAction` | читаются расчётом: `id`, `recipes: list[recipe_id]`; `name` — подпись. **Не читаются расчётом** (загружаются, но ни на что не влияют): `kind ∈ {main,minor,both}`, `labor_share 0..1`, `purpose`, `requires_terrain`, `requires_tool`, `risk 0..1` | Economist |
| `NeedConfig` | `adult_food_per_month`, `child_food_per_month`, `elder_food_per_month`, `edible_order`, `winter_months`, `firewood_per_adult_winter_month`, `feed_good`, `feed_per_month`, `land_regime_id` (режим земли, к которому привязаны потребности), `axe_wear_per_batch`, `axe_break_below`, `wear_recipes`, `relief_min_court_grain`, `relief_amount`, `birth_food_months`, `birth_streak_months`, `birth_max_household`, `birth_adults_required`, `birth_maturity_months`, `death_child_per_month`, `death_adult_per_month`, `death_elder_per_month`, `death_old_age_months`, `death_old_age_extra` | Economist |
| `SpawnRule` | `id`, `name`, `target ∈ {hazard,pack,good,ruin}`, `kind ∈ {probabilistic,calendric,conditional}`, `params: dict` (в т.ч. `cap_per_tile`) | Economist |
| `HazardRule` | `id`, `name`, `kind`, `params: dict` | Economist |
| `LandRegime` | `id`, `name`, `allowed_actions ⊆ {plough,take_game,leave}`, `requires_labor_days`, `feeds_household` | Legal |
| `LegalStatus` (пресет) | `id`, `name`, `personal_status`, `land_relation`, `obligation_bundle`, `can_leave`, `marriage_needs_permission`, `court ∈ {manor,public}`, `wergeld`, `inheritance`, `can_sell_land`, `can_be_taken_on_expedition`, `ploughs`, `land_kind` | Legal |
| `ObligationBundle` | читаются расчётом: `id`, `terms: dict[term,{value,unit,note}]` (гэфоль `in_kind_martinmas`/`geld_michaelmas`, `sow_demesne_acres`), `calendar_id?`; `name` — подпись. **Не читается расчётом** (только проверяется при загрузке): `currency_mix ⊆ {labor-day,in-kind,penny}` — `unit: acre` у члена `sow_demesne_acres` валютой не является | Legal |
| `CalendarMonth` | `month 1..12`, `season ∈ {winter,plough,hay,harvest,sow_winter}`, `demesne_work`, `labor_mod: dict[preset,dict]`, `boon_allowed`, `sow_demesne_acres` | Legal |
| `ManorConfig` | `holding_tiles_by_land_kind`, `plot_batch_cap_per_tile`, `demand_days_per_tile: dict[month,float]`, `harvest_recipe`, `board_grain_per_adult`, `hire_wage_grain_per_day`, `hire_wage_silver_per_day`, `hire_min_castle_grain`, `sow_grain_per_acre` | Economist |
| `SeasonYield` | `month 1..12`, `season`, `plot_yield`, `demesne_yield` | Economist |
| `Office` | `id`, `name`, `action`, `travel` | Legal |

### Мёртвые поля формулы (загружаются, но расчётом не читаются)

В коде и каталогах есть поля формулы, которые **загружаются, но не участвуют ни в одном
расчёте**: `Person.talent`, `Person.labor_productivity`, `Recipe.tool_multiplier`. Они **не
являются полями онтологии** и не должны попадать в формулу, пока их не начнёт читать расчёт
(ADR 0110 п. 5). Проверяется `sim/tests/test_hex_yield_law.py` — тест доказывает только
загрузку, не влияние на выход. Поле `Good.material_quality` **удалено** как поле с нулём чтений
(ADR 0157 п. 4): в `sim/src/` и `design/` — 0 совпадений.

Читаемые расчётом множители: труд `labor_days`, инструмент из стока двора
(`TOOL_YIELD_FACTORS`: нет 1.0 < `wooden_plough` 1.05 < `iron_share` 1.15), сезон `plot_yield`,
пригодность гекса по режиму (`field_yield_factor`), `holding_scale` в ёмкости клетки и
`Tile.resource_productivity` — последний **только** в `phase_growth` для стоячей материи.
Материал входит не множителем качества, а входом по лестнице `log → board → plank`.

Класс основания `basis` в каталоге шире, чем в коде: `share` (оброк — доля от зерна двора,
ADR 0183), `fixed` (назначенная сумма), `duty` (отработка) и **`measured` — величина не
назначается, а меряется** (подача по недобору, ADR 0158). По `basis` выбирается, кто вообще
платит: оброк требует `basis: share`, иначе расчёт бросает исключение, а не подставляет число.

### Еда, корзина и готовое блюдо

`Good` уже является единственным носителем съедобности (`edible`, `nutrition`,
`storage`, `spoil_per_month`). Корзина — не новая мировая сущность и не отдельный товар:
`phase_consume` суммирует доступные съедобные `Stock.amounts` и списывает их в
`sink:eaten` по детерминированному порядку `NeedConfig.edible_order`; при нехватке растёт
`Household.hunger_days`. `cook_meal` — служебное действие `HouseholdAction` и приём пищи,
а не новый `Good`: оно переводит зерно или муку и доступные добавки в `sink:eaten` и `sink:waste`, уменьшает `Household.labor_days` на 2.0 и увеличивает счётчик
`World.stats.meals_cooked`; отдельного блюда в каталоге нет. Добавки `roots`, `greens`,
`mushrooms`, `berries` — обычные съедобные `Good` с рецептами сбора и местами хранения.

### Служебные типы

- `World.month_events` и `scout_observe` — события месяца для Info; `scout_observe` имеет
  `tile_id`, `fact`, `rumor`, `confidence`, `date`, `observer_id=Pack.id`, `source="scout"`.
  `fact=False` — слух, не факт; `known_tiles` из события не растёт.
  Записи `World.month_events` имеют `kind`, `good`, `amount`, `date`, `month`, `reason`,
  `src_id`, `dst_id`, а при необходимости `settlement_id`, `household_id` или `tile_id`
  (ADR 0079/0081).
- Наблюдение: маршрут и свой гекс — факт без броска; кольцо 1 — `rng_world` с профилем и
  покровом; кольцо 2 — слух `confidence=0.5`; дальше 2 гексов нет. Без `scout`-Pack в гексе
  наблюдений нет. Профили: `hunters=0.85` (следы жизни, силён в чистом поле),
  `forester=0.75` (лесные знаки, силён в лесу), `party=0.55` (без подготовки). Покров:
  лес/топя 1.0, луг 0.75, поле 0.5; `Hazard` ухудшает. Это разные оси и закрытые числа
  закона; пересмотр — только новым ADR с замером (ADR 0074/0087/0088).
- Физический бросок наблюдения использует `rng_world`; выбор текста/шума использует
  `rng_news` и не двигает физический мир. `tower`/башня, `Scout` и отдельный наблюдатель
  в v1 не существуют.

- `SimDate = {year:int, month:int, day:int}`; `months_per_year=12`.
- `World.roadworks` — реестр строек пути, ключ `tile_id`:
  `{kind ∈ {road,ford,bridge}, required_days, done_days, material_good, material_amount,
  status ∈ {active,done}, started, manor_id}`. Не `dataclass`, а словарь мира; материал —
  только перевод `log` → `sink:waste` (`engine/roadworks.py`).
- `TILE_MAX_HOUSEHOLDS = 5` — кап жилых дворов на клетке (`engine/tile_view.py`), эквивалент
  будущего `Tile.max_households`. Не путать с `plot_batch_cap_per_tile` — это кап **стойла
  скота** на клетку в месяц, а не кап пашни (ADR 0143 п. 2, ADR 0147); на пашне и общинном
  сборе капа партий нет вовсе (ADR 0173 п. 2).
  Вид клетки (`engine/tile_view.py:FORMS`) — чистый рендер, не сущность мира (И-5).
- `Tile.trail_wear` — износ сухопутного пути. `trail_level_for()` возвращает уровень 0/1/2
  по порогам `TRAIL_WEAR_TRAIL = 3.0` и `TRAIL_WEAR_DIRT = 12.0`; входные множители для
  целины, тропы и грунтовки — 1.0/0.9/0.8. Затухание `TRAIL_DECAY_PER_MONTH = 0.5`;
  вода и `Tile.road` не принимают тропообразование. Ходьба никогда не ставит `Tile.road`
  (ADR 0061).
- Веса топтания задаёт вид движения: `PACK_TREAD_WEIGHTS` — `caravan` 4.0, `party` 2.0,
  `household_move` 2.0; ходок `Household.traveling` весит 1.0; речной маршрут и неизвестный
  вид движения — 0.
- Профили движения — строки `MOVEMENT_PROFILES`, не классы. Среди восьми профилей:
  `arms` умножает все сухопутные цены на 1.25; `rider` задаёт для `field` 0.6,
  `forest` 4.0, `marsh` 6.0, `ford` 4.0, `hill` 2.0, `heath` 1.0, `salt_flat` 1.2,
  `ruin` 1.5, `road` 0.5 (ADR 0061).
- Сценарные маркеры `marks` хранятся как динамические атрибуты `Tile`, а не его поля.
  Для одного живого двора `dwelling ∈ {tent_earth,house,multi_storey}` задаёт только форму
  `tent_earth_homestead`/`timber_house`/`multi_storey_house`; `tavern` задаёт форму
  `tavern_site` (участок). Тик эти маркеры не читает, бонусов и переходов жилья нет;
  `housing_level` в v0 не существует (ADR 0065). Клетка без живых дворов получает вид
  пустой местности: для `hill`/`field`/`pasture` это `open_field`, не `tribal_village`;
  такая пустота не создаёт знание (ADR 0068).
- Сценарий большой карты хранит 100×100 `Tile` как данные, 6-связную реку, поселения,
  дворы, `works_tiles`, леса/луга/wilds и редкие маркеры; новые мировые сущности для карты не
  введены. Подробный контур ограничен поселениями и `works_tiles`; подключение карты к тику —
  «в работе» (ADR 0073/0080/0083).
- `Obligation(kind="muster")` хранит срок 2/3 месяца и `call_status`; связанные outbound/return
  `Pack` используют `Pack.obligation_id`. Потерянный Pack или тишина не означают `refused`;
  `unable` означает невозможность, а подавление тэна — существующий `revoke_thegn`. Регент,
  `annexed` и `chief_person_id` в v1 отсутствуют (ADR 0082).
- `state_hash` пока **не покрывает** `Obligation.call_status` и `Pack.obligation_id`; это
  обязательный долг, а не заявленная синхронизация.

  `transfer` (сток → сток), `process` (выдача из пула обработки рецепта), `external_in`
  (материя извне по `rule_id`), `external_out` (наружу; в v0 не используется). `process`
   разрешён только для товаров, объявленных на выходе рецепта (`allowed_goods`), иначе
   `Ledger.emit` падает. Счёт `sink:waste` и `sink:eaten` — обычные `Stock` с `owner_kind=sink`.
- **И-1 держит вид проводки и `LedgerEntry.rule_id`, а не ярлык правила.** Происхождение
  потока помечается **видом проводки** `external_in` (`ENTRY_KINDS`) и **непустым `rule_id`** в
  записи; доказательство И-1 — именно непустой `rule_id`, и его проверяет
  `test_matter_conservation`. Отдельного поля «поток извне» у `SpawnRule` **нет и не было
  нужно**: ярлык `external` был объявлен, грузился и не читался ни одним потребителем — снят
  как поле с нулём чтений (ADR 0164 п. 2, тем же основанием, что `material_quality` по
  ADR 0157). Враг фазы роста отбирает правила по `kind == "calendric" and target == "good"`
  (`engine/growth.py:44`) и зовёт `external_in`, передавая `reason`/`rule_id` правила.
  Называть поле функцией, которой у него нет, запрещено (ADR 0164 п. 1).
- Служебная память рядом с `Ledger` (не сущности мира, ADR 0058):
  `World.barter_memory: dict[str, dict]` — память сделок вместо биржи (ключ
  `origin->destination:good`, поля `carried`/`delivered`/`ratio`/`lost`; цен нет);
  `remembered_road_tiles(world)` — вычисляемые клетки дорог из маршрутов дошедших
  `Pack` (не хранятся). Игрок их напрямую не читает (И-3).

### Ловушка: датакласс нельзя ставить ключом словаря

`@dataclass` в `sim/src/hillcourt/ontology.py` генерирует `__eq__`, а Python обнуляет
`__hash__`. Проверено: `type(world).__hash__ is None`, и
`weakref.WeakKeyDictionary()[world] = 1` падает с `TypeError: unhashable type: 'World'`.
Уязвимы **все 24 датакласса** `ontology.py` (`World`, `Household`, `Settlement`,
`Obligation`, `Stock`, `Tile`, `Person`, `Pack`, `Recipe`, `Manor`, `Good` и другие).

**Правило.** Ключом словаря, `WeakKeyDictionary`, `WeakSet` или `set` запрещено ставить
датакласс. Кэш уровня мира ведётся на `id(obj)` плюс рядом `weakref.ref`, как уже сделано
в `economy/soil.py:249` (`_HERD_STATE: dict[int, tuple[dict, "weakref.ref[World]"]]`).

**Правило симметричное.** Если объект ушёл в `weakref.ref`, в дереве обязан быть обход,
который чистит запись: либо сверка `entry[1]() is obj` перед чтением, либо явный сброс по
событию. Иначе запись висит на **висящем `id()`** — новый объект получит тот же адрес,
прочитает чужой кэш, и тик создаст материю из ничего.

**Проверка.** Узкая форма — `grep -rn "WeakKeyDictionary\|WeakSet" sim/src/` обязан вернуть
только строки с комментарием-обоснованием либо обёрткой `id(...) + weakref.ref`; читающая
форма — у каждого найденного `WeakKeyDictionary` рядом обязан встречаться `weakref.ref` **и**
сверка `() is ` **или** явная очистка. Автоматический тест ожидается в
`sim/tests/test_docs_claims.py` от Implementer; до его появления критерий проверяется
этим grep вручную, и это не считается выполненным, пока тест не написан.

### Знание игрока (`news/`)

Игрок не читает мир напрямую. `build_player_view(world, as_of)` собирает `PlayerView` только из
доставленных `Report` (`delivery_date ≤ as_of`); другого доступа к истине у игрока нет.

| Сущность | Поля | Владелец |
|---|---|---|
| `ReportView` | `id`, `source`, `subject_kind`, `subject_id`, `content`, `facts: dict`, `event_date`, `delivery_date`, `confidence`, `distorted`, `stale` | Info |
| `PlayerView` | `as_of: SimDate`, `entries: list[ReportView]`, `sources()` | Info |
| `KnowledgeEntry` | `about`, `source`, `observed_month`, `arrived_month`, `noise`, `confidence`, `distorted`, `content`, `facts: dict`, `stale` | Info |
| `PlayerKnowledge` | `as_of: SimDate`, `entries: list[KnowledgeEntry]`, `by_about: dict`, `latest(about)`, `silences()` | Info |

`stale = (age > STALE_AFTER_MONTHS)`, где `STALE_AFTER_MONTHS = 2` (месяца от `event_date`).
У `ReportView` и `KnowledgeEntry` нет полей `Tile`/`Household`/`Stock`: только рассказ о них.
Знание игрока по месту — это `PlayerKnowledge.latest(about)` (последний доехавший `Report` о
месте, а не `Tile.state`); собирается `build_player_knowledge(world, as_of)` только из
доставленных `Report` (`info/knowledge.py`). Не-клеточные сводки адресуются ключами мест:
`neighbors` (молчание о соседях), `barony` (гонец/шериф).

### Действия игрока (`PlayerAction`)

В v0 реализованы, по домам:

- **Право/посылка** (`legal/actions.py`): `grant_tenure`, `add_obligation`, `send_party`.
- **Книга манора** (`engine/manor.py`): `set_tile_regime`, `grant_tenement`, `grant_tool`,
  `call_boon`, `ease_week_work`, `grant_thegn`, `revoke_thegn`.
- **Путь** (`engine/march.py`, `engine/seat.py`, `engine/river.py`): `send_march` (отряд по
  `find_path`, в логе `route`/`months`/`profile`), `send_sally` (высылка на соседнюю клетку,
  `order_delay_months` = 0 на клетке глаза/мира корня, иначе 1), `send_river` (речной воз:
  нужен `raft`/`boat` ≥ 1.0 для груза > 1.0, в маршруте обязана быть вода).
- **Стройка пути** (`engine/roadworks.py`): `work_road`, `work_ford`, `work_bridge`.
- **Место** — не приказ, а правило места (`engine/tile_view.py:settle_household`): посадка
  двора сверх капа 5 отклоняется; вызывается миграцией и тестами, в `ACTION_HANDLERS`
  runner не заведена (отдельного приказа `settle_household` нет).

Каждое действие меняет `Right`/`Obligation`/`Pack`/`Manor`/`Tile` и пишется в
`World.player_actions`; `Report` порождают не все (например `work_*`, `grant_tool`,
`call_boon`, `ease_week_work` — только лог). Прямого приказа `Person` нет (И-2).
`revoke_tenure`, `set_rent_share`, `send_pack`, `send_messenger`, `patrol` заявлены, но ещё
не реализованы (отложено). `World.rights` хранит выданные `Right`,
`World.manors` + `World.player_manor_id` — книгу земли.

## Проверка

Критерий: каждая сущность ядра существует как `dataclass` в `sim/src/hillcourt/`; любое имя
собственное в `docs/` и `design/` трассируется в эту таблицу или в каталог; поле с числом имеет
единицу в имени; `ReportView` не содержит полей истины мира.
Проверка: ревизия Critic, `sim/tests/test_catalog_rules.py` (каталог),
`sim/tests/test_scouting.py` и `sim/tests/test_scouting_reports.py` (Pack/наблюдение),
`sim/tests/test_news_no_omniscience.py` и `sim/tests/test_info_knowledge.py` (знание игрока),
`sim/tests/test_legal_regimes.py` (режимы/статусы), `sim/tests/test_hazards_encounter.py`
(опасность как популяция), `sim/tests/test_matter_conservation.py` (виды проводок `Ledger`).

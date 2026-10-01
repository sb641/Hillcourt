"""Месячный тик: фиксированный порядок фаз (docs/04_tick.md).

Порядок v0 (манор + агентная экономика):
  1 season   — сезонный множитель (материю не трогает);
  2 growth   — внешний приход по календарным spawn_rules (external_in);
   3 manor    — труд/барщина/паёк/гэфоль/посев и урожай домена (economy/manor.py);
   3a hay      — косьба сена тягловыми дворами из остатка труда (economy/livestock.gather_draft_hay), до поля;
   3b roadworks — стройка пути из остатка рук после барщины, до поля;
  4 labor    — двор работает на своём наделе (economy/labor.py);
  5 spoil    — порча;
  6 exchange  — подмога и локальный обмен до расхода;
  7 consume  — еда, зимнее топливо, корм скота;
  8 demography — сытый двор растит семью: старение, недоросли в тягло, роды;
  9 obligations — натуральная рента и запись барщины;
 10 hazard   — волчья кража и риск пути;
 11 migrate  — уход двора на соседнюю клетку (если can_leave);
  11a ruin    — пересчёт двух проигрышей и защёлка (engine/ruin.py, ADR 0209);
  12 caravan  — отправка и разгрузка обозов соли (economy/caravan.py);
 13 travel   — разрешение дошедших посылок Pack;
 13a day     — дневной контур Pack: сутки `eta_date` (ADR 0071, И-4);
 14 decide   — выбор действий на следующий месяц;
 15 inform   — факты месяца в Report с задержкой и искажением;
  16 record   — снимок и сдвиг календаря.
"""


from __future__ import annotations

from ..economy import caravan, decisions, exchange, labor
from ..economy.cooking import meal_credit, phase_cook_meal
from ..economy.demography import demography_month
from ..economy.labor import work_month
from ..economy.livestock import livestock_month
from ..economy.manor import manor_month
from ..economy.thegn import thegn_month
from .events import month_events
from .growth import index_growth_tiles, run_detailed_growth
from .hexgrid import neighbor_ids
from .hunting import run_hunt_actions
from .scouting import observe_scout_packs, settle_reported_discoveries
from .terrain import DAYS_PER_MONTH
from .manor import update_musters
from .ruin import phase_ruin
from .tile_view import can_settle
from . import trails
from ..economy.needs import member_counts, monthly_food_need
from ..hazards.travel import resolve_migrations, resolve_packs
from ..info.briefing import make_month_reports, report_travel_loss
from ..info.sources import CARAVAN, CHANNELS
from ..news import threats as threats_module
from ..news.hunting import make_hunt_report
from ..news.propagation import make_report
from ..news.ruin import report_ruin
from ..news.scouting import report_scout_observations
from ..news.threats import report_pending_topups
from ..news.trails import report_new_trails
from ..legal.calendar import seasonal_labor_days
from ..legal.obligations import accrue_corvee
from ..legal.regimes import can_leave, hunt_destination_stock_id
from ..ontology import Hazard, Household, Pack, Report, SimDate, Stock
from ..world import World

EPSILON = 1e-9
WASTE_STOCK_ID = "sink:waste"
HUNTER_PROFILE = "hunters"
CARAVAN_CHANNEL = CHANNELS[CARAVAN]
EATEN_STOCK_ID = "sink:eaten"
FUEL_ACTION_IDS = frozenset(("cut_wood_if_allowed", "cut_peat"))
FUEL_TERRAINS = frozenset(("forest", "marsh"))

SEASON_FACTORS: dict[int, float] = {
    1: 0.2,
    2: 0.2,
    3: 0.4,
    4: 0.7,
    5: 1.0,
    6: 1.2,
    7: 1.3,
    8: 1.2,
    9: 1.0,
    10: 0.6,
    11: 0.3,
    12: 0.2,
}


def season_factor(month: int) -> float:
    """Сезонный множитель урожая по номеру месяца (зима ~0.2, лето ~1.3)."""
    return SEASON_FACTORS.get(month, 1.0)


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def phase_season(world: World) -> None:
    """Зафиксировать сезонный множитель месяца; материю не трогает."""
    season_factor(world.clock.month)


def phase_growth(world: World) -> None:
    """Внести материю извне только на индексированных (обрабатываемых) гексах.

    Индекс **пересчитывается каждый месяц** из данных мира
    (`engine/growth.py::index_growth_tiles`): обрабатываемая земля — это места и
    угодья поселений, наделы по праву, домен и дикая земля под рукой у них, а
    право игрок жмёт между тиками. Состояния индекса в мире нет, есть вывод, и
    он берётся здесь — поэтому выдача надела, отзыв пожалования и смена режима
    попадают в рост без чьего-либо invalidation (забыть нельзя: нечего).

    Старый индекс строилось один раз при загрузке из координат поселений, и на
    `start_stand` в него попадала одна пашня из семи: надел `t_02_00` съедал
    8.0 стартового зерна за зиму и с четвёртого месяца был пуст навеки, а домен
    копил 788.9 зерна, которое никто не убирал. Обход при этом остаётся по
    индексу, а не по всей coarse-карте: 253 клетки из 10 000 на `v0_barony_100`.
    """
    world.growth_tile_ids = index_growth_tiles(world)
    run_detailed_growth(world, season_factor(world.clock.month))


def fuel_source_available(world: World, household: Household) -> bool:
    """Проверить forest/marsh и стоячее топливо среди доступных клеток."""
    for recipe_id in ("cut_firewood", "cut_wood", "cut_peat"):
        recipe = world.catalogs.recipes.get(recipe_id)
        if recipe is None:
            continue
        for tile in labor._tiles_for_recipe(world, household, recipe):
            if tile.terrain not in FUEL_TERRAINS:
                continue
            stock = world.get_stock(tile.standing_stock_id)
            if any(
                stock.amounts.get(good, 0.0) > EPSILON
                for good in recipe.draws_standing
            ):
                return True
    return False


def _fuel_fallback(
    world: World, household: Household, *, main: bool
) -> str:
    """Выбрать разрешённое дело вместо топлива без источника."""
    allowed = decisions.allowed_action_ids(world, household)
    candidates = (
        ("work_plot", "hire_out", "oversee", "demesne_labor", "idle_repair")
        if main
        else ("idle_repair", "tend_animals", "work_plot")
    )
    for action in candidates:
        if action in allowed:
            return action
    return "idle_repair"


def _guard_fuel_actions(world: World) -> None:
    """Снять топливный выбор, если у двора нет доступного источника."""
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        has_source = None
        if (
            household.main_action in FUEL_ACTION_IDS
            or household.minor_action in FUEL_ACTION_IDS
        ):
            has_source = fuel_source_available(world, household)
        if has_source:
            continue
        if household.main_action in FUEL_ACTION_IDS:
            household.main_action = _fuel_fallback(world, household, main=True)
        if household.minor_action in FUEL_ACTION_IDS:
            household.minor_action = _fuel_fallback(world, household, main=False)


def _hunt_action_slots(world: World) -> list[tuple[Household, str, str]]:
    slots: list[tuple[Household, str, str]] = []
    for household_id in sorted(world.households):
        household = world.households[household_id]
        if household.left_at is not None:
            continue
        if "take_game" not in (household.main_action, household.minor_action):
            continue
        slots.append((household, household.main_action, household.minor_action))
    return slots


def phase_labor(world: World) -> None:
    """Отработать решения дворов после проверки топливных кандидатов."""
    _guard_fuel_actions(world)
    run_hunt_actions(world, world.clock.date)
    slots = _hunt_action_slots(world)
    for household, main_action, minor_action in slots:
        if main_action == "take_game":
            household.main_action = "cook_meal"
        if minor_action == "take_game":
            household.minor_action = "cook_meal"
    try:
        work_month(world, world.clock.date)
    finally:
        for household, main_action, minor_action in slots:
            household.main_action = main_action
            household.minor_action = minor_action


def phase_manor(world: World) -> None:
    """Манор: барщина/домен/паёк/гэфоль/найм до работы двора на своём наделе."""
    manor_month(world, world.clock.date)
    thegn_month(world, world.clock.date)


def phase_hay(world: World) -> None:
    """Косьба сена до поля: тягловые дворы докашивают из остатка труда.

    Косьба единственная (ADR 0050: поздний добор в `livestock_month` и резерв
    `_mow_reserve` удалены — поле забирает все руки). Вызывает существующую
    `economy/livestock.gather_draft_hay` без её правок; второго прохода
    по сену в месяце нет, расход труда и материи не двоится.
    """
    from ..economy.livestock import gather_draft_hay

    gather_draft_hay(world, world.clock.date)


def phase_roadworks(world: World) -> None:
    """Стройка пути: добрать труд из остатка рук после барщины, до поля.

    No-op без активных работ: канонные числа (соль, дельта, живые) не
    сдвигаются, пока игрок не поставил `work_road`/`work_ford`/`work_bridge`.
    """
    from .roadworks import phase_roadworks as build_phase

    build_phase(world)


def _household_stock_ids(world: World) -> list[str]:
    return sorted(
        sid for sid in world.stocks if world.stocks[sid].owner_kind == "household"
    )


def phase_spoil(world: World) -> None:
    """Перевести порчу товаров дворов (включая тайники) в sink:waste."""
    date = world.clock.date
    waste = world.get_stock(WASTE_STOCK_ID)
    for sid in _household_stock_ids(world):
        stock = world.stocks[sid]
        for good in sorted(list(stock.amounts)):
            rule = world.catalogs.goods.get(good)
            if rule is None or rule.spoil_per_month <= 0:
                continue
            amount = stock.amounts.get(good, 0.0) * rule.spoil_per_month
            if amount > EPSILON:
                world.ledger.transfer(stock, waste, good, amount, "spoil", date)


def _eat_from(stock, good: str, need: float, world: World, date: SimDate) -> float:
    """Съесть good, закрыв до `need` «рот-единиц»; вернуть остаток нужды."""
    if need <= EPSILON:
        return need
    rule = world.catalogs.goods.get(good)
    nutrition = rule.nutrition if rule is not None and rule.nutrition > 0 else 1.0
    available = stock.amounts.get(good, 0.0)
    if available <= EPSILON:
        return need
    take = min(available, need / nutrition)
    if take <= EPSILON:
        return need
    world.ledger.transfer(stock, world.get_stock(EATEN_STOCK_ID), good, take, "eat", date)
    return need - take * nutrition


def _feed_livestock(world: World, household: Household, date: SimDate) -> None:
    """Скормить сено скоту; без корма скот гибнет и уходит в отходы.

    Правка по именному разрешению хозяина (ADR 0053, шаг 2; прецедентом не
    является): норма сезонная — зимой полное сено из стока, летом 30 % сеном
    + 70 % выпасом (`economy/livestock.graze_for_herd`, кап ёмкости пастбищ);
    недобор выпаса — снова сеном из стока, затем падёж прежней пропорцией.
    """
    from ..economy.livestock import graze_for_herd
    from ..economy.needs import hay_need_rate

    needs = world.needs
    if needs is None:
        return
    stock = world.get_stock(household.stock_id)
    feed = needs.feed_good
    for animal in sorted(needs.feed_per_month):
        count = stock.amounts.get(animal, 0.0)
        if count <= EPSILON:
            continue
        base = float(needs.feed_per_month[animal])
        hay_part = count * hay_need_rate(world, animal, date.month)
        given = min(stock.amounts.get(feed, 0.0), hay_part)
        if given > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(EATEN_STOCK_ID), feed, given, "feed", date
            )
        grazed = graze_for_herd(world, stock, count * base - hay_part, date)
        extra = min(
            max(0.0, stock.amounts.get(feed, 0.0)), count * base - given - grazed
        )
        if extra > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(EATEN_STOCK_ID), feed, extra, "feed", date
            )
        shortfall = count * base - given - grazed - extra
        if shortfall > EPSILON and count > EPSILON:
            # Голод косит стадо пропорционально нехватке, а не ровно одну голову.
            fraction = min(1.0, shortfall / (count * base))
            kill = min(count, max(1.0, count * fraction))
            world.ledger.transfer(
                stock, world.get_stock(WASTE_STOCK_ID), animal, kill, "starved", date
            )
            world.bump("animals_starved", kill)


def phase_consume(world: World) -> None:
    """Съесть доступную еду, протопить зиму, накормить скот.

    Дворы кормится здесь же; скот амбаров манора, приплод, взросление и падёж —
    в `economy/livestock.py::livestock_month` (проводка зоны скота, ADR 0025).
    """
    date = world.clock.date
    needs = world.needs
    month = world.clock.month
    winter = needs is not None and month in needs.winter_months
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        stock = world.get_stock(household.stock_id)
        need = monthly_food_need(world, household)
        # Приём пищи, который двор уже получил готовкой (ADR 0098, 0100, 0159),
        # вычитается ДО поедания: еда, сваренная фазой 5, не должна считаться
        # дважды — ни по материи, ни по нужде. Зачёт месячный и протухает сам.
        credit = meal_credit(world, household)
        if credit > EPSILON:
            need = max(0.0, need - credit)
        order = needs.edible_order if needs is not None else ["grain"]
        for good in order:
            need = _eat_from(stock, good, need, world, date)
        if need > 0.01:
            household.hunger_days += 1
            household.mood = max(0.0, household.mood - 0.1)
            world.bump("hunger_months")
        else:
            household.hunger_days = 0
            household.mood = min(1.0, household.mood + 0.03)

        if winter:
            adults, _, _ = member_counts(world, household)
            fuel_need = needs.firewood_per_adult_winter_month * max(adults, 1)
            for fuel in ("firewood", "peat"):
                if fuel_need <= EPSILON:
                    break
                available = stock.amounts.get(fuel, 0.0)
                take = min(available, fuel_need)
                if take > EPSILON:
                    world.ledger.transfer(
                        stock, world.get_stock(WASTE_STOCK_ID), fuel, take, "burn", date
                    )
                    fuel_need -= take
            if fuel_need > 0.01:
                household.mood = max(0.0, household.mood - 0.05)
                world.bump("cold_months")
        _feed_livestock(world, household, date)
    livestock_month(world, date)


def phase_demography(world: World) -> None:
    """Сытый двор растит семью: старение, недоросли в тягло, роды (economy/demography.py)."""
    demography_month(world, world.clock.date)


def phase_obligations(world: World) -> None:
    """Внести ренту или накопить недоимку по каждой повинности."""
    date = world.clock.date
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if not household.obligation_ids:
            continue
        stock = world.get_stock(household.stock_id)
        for oid in sorted(household.obligation_ids):
            obligation = world.obligations.get(oid)
            if obligation is None:
                continue
            duty = obligation.basis == "duty" or obligation.kind == "labor_duty"
            if not duty and obligation.kind == "rent" and obligation.basis == "share":
                # Оброк — доля от урожая двора, поэтому сумма живёт: пересчитываем
                # её от зерна этого месяца (ADR 0185), иначе она застыла бы в
                # момент выдачи наделения. Локальный импорт — правка блока фазы.
                from ..legal.obligations import refresh_rent_due

                refresh_rent_due(world, household, obligation)
            if duty:
                # Сезонный долг из календаря (legal.calendar): не «2 дня всегда».
                # Сами руки с надела снимает фаза манора (economy/manor.py), иначе
                # был бы двойной счёт; здесь — только запись долга по периоду.
                obligation.duty_days = seasonal_labor_days(
                    world, household, world.clock.month
                )
                period = max(1, obligation.period_months)
                if world.clock.month % period != 0:
                    continue
            due = obligation.due_amount
            if due <= 0:
                if duty:
                    accrue_corvee(obligation, obligation.duty_days)
                continue
            available = (
                stock.amounts.get(obligation.due_good, 0.0)
                if obligation.due_good
                else 0.0
            )
            if obligation.due_good and available + EPSILON >= due:
                court_stock = world.get_stock(
                    "settlement:" + world.player.court_settlement_id
                )
                world.ledger.transfer(
                    stock,
                    court_stock,
                    obligation.due_good,
                    due,
                    "corvee_commuted" if duty else "rent",
                    date,
                )
                obligation.paid_total += due
                obligation.arrears = max(0.0, obligation.arrears - due * 0.5)
                world.bump("corvee_commuted" if duty else "rent_collected", due)
                if duty:
                    accrue_corvee(obligation, obligation.duty_days, paid_in_lieu=True)
            else:
                obligation.arrears += due
                household.arrears_days += 1
                if duty:
                    accrue_corvee(obligation, obligation.duty_days)


def phase_exchange(world: World) -> None:
    """Подмога, тайники, локальный обмен на клетке и прилавок барона.

    Порядок: сначала подача голодному (ADR 0114) — она нужнее рынка и от денег
    не зависит, потом обмен между дворами, и только потом покупка зерна, которое
    барон выставил в деревне (ADR 0139): продажа не отменяет подачу, но и не
    заменяет её.
    """
    date = world.clock.date
    exchange.apply_relief(world, date)
    exchange.apply_hide_stores(world, date)
    exchange.local_exchange(world, date)
    exchange.sell_listed_grain(world, date)


def _travel_risk(world: World, household: Household) -> float:
    tile = world.tiles.get(household.current_tile_id)
    if tile is None:
        return 0.0
    risk = 0.0
    for neighbour in decisions.adjacent_tiles(world, household):
        risk = max(risk, decisions.tile_risk(world, neighbour))
    return risk


def _apply_hazard_topups(world: World) -> None:
    """Дотянуть СУЩЕСТВУЮЩИЕ угрозы по правилам `target==hazard, mode==topup`.

    Контракт правила (ADR 0042, `wolves_den`): новых сущностей `Hazard` нет
    (иначе весть ниоткуда, И-3); бросок `base_prob` — именованным потоком
    из `rng_stream` (`hazard`, детерминизм И-6); приросты `intensity_gain` /
    `population_gain` режутся потолками `intensity_cap` / `population_cap`.
    Рост — только на клетке террейна правила и только активной угрозы.
    Топ-ап материю не двигает (числа угрозы, не зерно): дельта 0.
    Каждое срабатывание метится для Info (весть — не здесь, её рождает Info
    в `phase_inform` по `report_required`): счётчик `wolves_den_topup`
    и штамп месяца `wolves_den_topup_<hazard_id>` в `world.stats`
    (`year * 12 + month`). Без роста метки нет.
    """

    def _month_stamp() -> float:
        return float(world.clock.year * 12 + world.clock.month)

    if world.rng is None:
        return
    for rule_id in sorted(world.catalogs.spawn_rules):
        rule = world.catalogs.spawn_rules[rule_id]
        if rule.target != "hazard" or rule.params.get("mode") != "topup":
            continue
        kind = str(rule.params.get("kind", ""))
        terrain = rule.params.get("terrain")
        base_prob = float(rule.params.get("base_prob", 0.0))
        if not kind or base_prob <= 0.0:
            continue
        intensity_gain = float(rule.params.get("intensity_gain", 0.0))
        population_gain = float(rule.params.get("population_gain", 0.0))
        intensity_cap = float(rule.params.get("intensity_cap", float("inf")))
        population_cap = float(rule.params.get("population_cap", float("inf")))
        rng = getattr(world.rng, str(rule.params.get("rng_stream", "hazard")), None)
        if rng is None:
            rng = world.rng.hazard
        for hazard_id in sorted(world.hazards):
            hazard = world.hazards[hazard_id]
            if not hazard.active or hazard.kind != kind:
                continue
            tile = world.tiles.get(hazard.tile_id)
            if tile is None or (terrain is not None and tile.terrain != terrain):
                continue
            if rng.random() >= base_prob:
                continue
            grown = False
            if intensity_gain > 0.0 and hazard.intensity < intensity_cap - EPSILON:
                hazard.intensity = min(intensity_cap, hazard.intensity + intensity_gain)
                grown = True
            if population_gain > 0.0 and hazard.population < population_cap - EPSILON:
                hazard.population = min(
                    population_cap, hazard.population + population_gain
                )
                grown = True
            if grown:
                world.bump("wolves_den_topup")
                world.stats[f"wolves_den_topup_{hazard_id}"] = _month_stamp()


def _next_hazard_id(world: World) -> str:
    """Следующий id угрозы (`haz_NNN` по максимуму занятых, детерминированно)."""
    taken = 0
    for hazard_id in world.hazards:
        if hazard_id.startswith("haz_"):
            try:
                taken = max(taken, int(hazard_id[len("haz_") :]))
            except ValueError:
                continue
    return f"haz_{taken + 1:03d}"


def _revert_birth(world: World, hazard_id: str, tile_id: str, mark_key: str) -> None:
    """Откатить рождение: сущность, привязка к клетке, метка, счётчик."""
    world.hazards.pop(hazard_id, None)
    tile = world.tiles.get(tile_id)
    if tile is not None and hazard_id in tile.hazard_ids:
        tile.hazard_ids.remove(hazard_id)
    world.stats.pop(mark_key, None)


def _apply_hazard_spawns(world: World) -> None:
    """Родить СТОЯЧИЕ угрозы по правилам `target==hazard, mode==spawn`.

    Контракт правила (ADR 0045, `band_camp`): гейт `terrain` + живых дворов
    на клетке не меньше `min_households` считается КАЖДЫЙ тик (миграция может
    посадить двор на топь mid-run — кэша нет); бросок `base_prob` — именованным
    потоком из `rng_stream` (`hazard`, И-6), по одному броску на прошедшую гейт
    клетку; рождение `intensity_init` / `population_init`, рез потолками
    `intensity_cap` / `population_cap`; на клетке с активной угрозой того же
    вида не сеем (одна стоячая угроза на клетку — рост сверх рождения отдан
    будущим решением). `spawn_rule_id` newborn — честный id правила.
    Рождение материю не двигает (скаляры угрозы, не зерно): дельта 0.

    Наказ Critic (структурно, кодом): новая сущность — только вместе с вестью.
    Выигравший бросок регистрирует угрозу и метку `<rule_id>_birth_<hazard_id>`
    (штамп месяца, как у топ-апа), затем зовёт читателя Info
    `news.threats.report_pending_births(world)` (граница — чтением: тексты,
    канал и задержки решает Info, здесь их нет) и проверяет, что весть
    о клетке рождения с датой сегодня в его ответе есть. Нет читателя или нет
    вести — рождение откатывается (`_revert_birth`): после фазы висит либо
    связка «угроза + весть», либо ничего. Без `report_required` (флаг правила)
    рождение идёт без читателя.
    """

    def _month_stamp() -> float:
        return float(world.clock.year * 12 + world.clock.month)

    if world.rng is None:
        return
    date = world.clock.date
    reader = getattr(threats_module, "report_pending_births", None)
    for rule_id in sorted(world.catalogs.spawn_rules):
        rule = world.catalogs.spawn_rules[rule_id]
        if rule.target != "hazard" or rule.params.get("mode") != "spawn":
            continue
        kind = str(rule.params.get("kind", ""))
        terrain = rule.params.get("terrain")
        try:
            min_households = int(rule.params.get("min_households", 0))
        except (TypeError, ValueError):
            continue
        base_prob = float(rule.params.get("base_prob", 0.0))
        if not kind or min_households <= 0 or base_prob <= 0.0:
            continue
        intensity_init = min(
            float(rule.params.get("intensity_init", 0.0)),
            float(rule.params.get("intensity_cap", float("inf"))),
        )
        population_init = min(
            float(rule.params.get("population_init", 0.0)),
            float(rule.params.get("population_cap", float("inf"))),
        )
        need_report = bool(rule.params.get("report_required", True))
        rng = getattr(world.rng, str(rule.params.get("rng_stream", "hazard")), None)
        if rng is None:
            rng = world.rng.hazard
        for tile_id in sorted(world.tiles):
            tile = world.tiles[tile_id]
            if terrain is not None and tile.terrain != terrain:
                continue
            if any(
                hazard.active and hazard.kind == kind
                for hid in tile.hazard_ids
                if (hazard := world.hazards.get(hid)) is not None
            ):
                continue
            living = sum(
                1
                for household in world.households.values()
                if household.left_at is None
                and household.current_tile_id == tile_id
            )
            if living < min_households:
                continue
            if rng.random() >= base_prob:
                continue
            hazard_id = _next_hazard_id(world)
            mark_key = f"{rule.id}_birth_{hazard_id}"
            world.hazards[hazard_id] = Hazard(
                id=hazard_id,
                kind=kind,
                tile_id=tile_id,
                intensity=intensity_init,
                active=True,
                spawn_rule_id=rule.id,
                population=population_init,
                satiety=0.4,
            )
            tile.hazard_ids.append(hazard_id)
            world.bump(f"{rule.id}_births")
            world.stats[mark_key] = _month_stamp()
            if not need_report:
                continue
            produced = reader(world) if reader is not None else []
            if not any(
                report.subject_id == tile_id and report.event_date == date
                for report in produced
            ):
                _revert_birth(world, hazard_id, tile_id, mark_key)
                counter_key = f"{rule.id}_births"
                left = float(world.stats.get(counter_key, 0.0)) - 1.0
                if left <= 0.0:
                    world.stats.pop(counter_key, None)
                else:
                    world.stats[counter_key] = left


def phase_hazard(world: World) -> None:
    """Волки крадут зерно; путь по опасной клетке может унести человека.

    Мир усадьбы (engine/seat.py): кража на клетке в радиусе мира слабее
    в PEACE_THEFT_FACTOR раз (одно правило). Урожая место не даёт.
    Сначала логово дотягивает стаю (`_apply_hazard_topups` по `wolves_den`,
    ADR 0042), затем топь сеет ватагу (`_apply_hazard_spawns` по `band_camp`,
    ADR 0045 — рождение только с вестью): новорождённые крадут уже в этом
    месяце, как подросшие.
    """
    date = world.clock.date
    _apply_hazard_topups(world)
    _apply_hazard_spawns(world)
    waste = world.get_stock(WASTE_STOCK_ID)
    try:
        from .seat import peace_theft_factor
    except ImportError:  # pragma: no cover — место всегда собирается

        def peace_theft_factor(world, tile_id):  # type: ignore[no-redef]
            return 1.0

    rules_by_kind = {
        rule.kind: rule
        for rule in sorted(world.catalogs.hazard_rules.values(), key=lambda r: r.id)
    }
    for hazard_id in sorted(world.hazards):
        hazard = world.hazards[hazard_id]
        if not hazard.active:
            continue
        rule = rules_by_kind.get(hazard.kind)
        theft_rate = (
            float(rule.params.get("grain_theft_per_month", 0.0)) if rule else 0.0
        )
        theft = hazard.intensity * theft_rate * peace_theft_factor(world, hazard.tile_id)
        for hid in sorted(world.households):
            household = world.households[hid]
            if household.left_at is not None:
                continue
            if household.current_tile_id != hazard.tile_id:
                continue
            if theft > EPSILON:
                stock = world.get_stock(household.stock_id)
                available = stock.amounts.get("grain", 0.0)
                take = min(available, theft)
                if take > EPSILON:
                    world.ledger.transfer(
                        stock, waste, "grain", take, hazard.kind, date
                    )
                    world.bump("grain_stolen", take)

    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or not household.traveling:
            continue
        risk = _travel_risk(world, household)
        if risk <= EPSILON:
            continue
        if world.rng.world.random() < risk and household.member_ids:
            lost = household.member_ids.pop()
            person = world.persons.get(lost)
            if person is not None:
                person.health = 0.0
            world.bump("persons_lost")
            target = max(
                decisions.adjacent_tiles(world, household),
                key=lambda t: decisions.tile_risk(world, t),
            )
            report_travel_loss(world, target.id, date)


def _start_departure(
    world: World, household: Household, destination_tile_id: str, date: SimDate
) -> Pack:
    """Начать уход двора: груз в воз, люди в путь; клетка меняется по прибытии.

    Уход — не телепорт и не delete: все товары со стока двора переводятся в
    сток воза (`departure_cargo`), сам двор остаётся на исходной клетке, пока
    воз не дойдёт до `eta_date` (`hazards/travel.resolve_migrations`).
    """
    origin = household.current_tile_id
    pack_id = f"move_{date.year:04d}_{date.month:02d}_{household.id}"
    cargo = Stock(
        id=f"pack:{pack_id}", owner_kind="pack", owner_id=pack_id, amounts={}
    )
    world.stocks[cargo.id] = cargo
    stock = world.get_stock(household.stock_id)
    for good in sorted(list(stock.amounts)):
        amount = stock.amounts.get(good, 0.0)
        if amount > EPSILON:
            world.ledger.transfer(stock, cargo, good, amount, "departure_cargo", date)
    pack = Pack(
        id=pack_id,
        kind="household_move",
        origin_tile_id=origin,
        destination_tile_id=destination_tile_id,
        route=[origin, destination_tile_id],
        member_ids=list(household.member_ids),
        cargo=cargo,
        departed_date=date,
        eta_date=date.advance(world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household.id,
    )
    world.packs[pack_id] = pack
    household.member_ids = []
    household.left_at = date
    household.intent = "leave"
    household.settlement_id = None
    world.bump("departures")
    return pack


def _tribe_household(world: World, household: Household) -> bool:
    """Двор племенной деревни: обратный lookup без `Household.tribe_id`.

    Состав племени вычисляется из `Settlement.household_ids` (ADR 0064: полей-кэшей
    нет): поселение двора — `kind == "native_village"` И двор числится в его
    составе. Оба условия: подделанный `settlement_id` без членства не считается.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    return (
        settlement is not None
        and settlement.kind == "native_village"
        and household.id in settlement.household_ids
    )


def phase_migrate(world: World) -> None:
    """Увести двор на соседнюю клетку при голоде или недоимке (через Pack).

    Дворы племенной деревни не уходят (ADR 0064): ни голод, ни недоимка племя
    не выселяет — guard `_tribe_household` до проверки `can_leave`. Племя само
    уходит лишь отдельным решением владельца (v2), не тиком.

    Кап жилых дворов (`engine/tile_view.py`): если клетка назначения к моменту
    прибытия полна (гонка двух возов), двор возвращается на исходную клетку
    (`origin`), а не уплотняется сверх капа. Груз цел (`arrival` в свой сток),
    в статистике `settle_rejected_arrival`, игроку — `messenger` о тесноте.
    Явный отказ с записью — в `settle_household` (приказ посадить).
    """
    date = world.clock.date
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if _tribe_household(world, household):
            continue
        leaving = household.hunger_days >= 3
        if not leaving:
            for oid in sorted(household.obligation_ids):
                obligation = world.obligations.get(oid)
                if (
                    obligation is not None
                    and obligation.due_amount > EPSILON
                    and obligation.arrears >= 3 * obligation.due_amount
                ):
                    leaving = True
                    break
        if not leaving:
            continue
        if not can_leave(world, household):
            continue
        tile = world.tiles.get(household.current_tile_id)
        if tile is None:
            continue
        # Соседство — только хелпер гекс-сетки (ADR 0071), шесть направлений.
        neighbours = neighbor_ids(world, tile)
        if not neighbours:
            continue
        free = [tid for tid in sorted(neighbours) if can_settle(world, tid)]
        if not free:
            continue
        chosen = world.rng.world.choice(free)
        people = len(household.member_ids)
        _start_departure(world, household, chosen, date)
        world.bump("households_left")
        world.bump("persons_left", people)


def _in_transit_pack_ids(world: World) -> list[str]:
    """id посылок в пути на сейчас — чтобы после разбора топтать именно их."""
    return [
        pack_id
        for pack_id in sorted(world.packs)
        if world.packs[pack_id].status == "in_transit"
    ]


def phase_caravan(world: World) -> None:
    """Отправить обозы по каденции и разгрузить дошедшие (economy/caravan.py).

    Пришедшие возы топчут свой маршрут (`engine/trails.py`): физика ходьбы,
    не приказ (И-2), вес на возу.
    """
    date = world.clock.date
    in_transit = _in_transit_pack_ids(world)
    caravan.dispatch_caravans(world, date)
    caravan.resolve_caravans(world, date)
    trails.tread_arrivals(world, in_transit)


def phase_decide(world: World) -> None:
    """Выбрать действия дворов на следующий месяц и отсеять топливо без источника."""
    decisions.plan_next_month(world)
    _guard_fuel_actions(world)
    trails.tread_travelers(world)


def resolve_hunt_packs(world: World, date: SimDate) -> list[Report]:
    """Передать добычу охотничьего Pack в кладовую барона.

    Материя движется в тике и сама по себе не является вестью (И-3): перенос
    идёт молча, а знание о нём оформляет `news.hunting.make_hunt_report` —
    каналом с задержкой, шумом и искажением. Отряд опознаётся профилем
    `hunters`, а не его именем: имя сценарное и в расчёте не участвует.
    """
    produced: list[Report] = []
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.profile_id != HUNTER_PROFILE or pack.status != "arrived":
            continue
        owner = world.households.get(pack.owner_household_id or "")
        if owner is None or owner.left_at is not None:
            continue
        tile = world.tiles.get(pack.destination_tile_id)
        if tile is None:
            continue
        source = world.get_stock(tile.standing_stock_id)
        amount = source.amounts.get("meat", 0.0)
        destination_id = hunt_destination_stock_id(world, owner, tile, "meat")
        if destination_id is None or amount <= EPSILON:
            continue
        target = world.get_stock(destination_id)
        if any(
            entry.reason == "hunt"
            and entry.src_id == source.id
            and entry.dst_id == target.id
            for entry in world.ledger.entries
        ):
            continue
        world.ledger.transfer(source, target, "meat", amount, "hunt", date)
        produced.append(
            make_hunt_report(
                world,
                owner.id,
                tile.id,
                "meat",
                amount,
                destination_id,
                date,
                observer_id=pack.id,
            )
        )
    return produced


def report_caravan_arrivals(world: World) -> list[Report]:
    """Описать доставку обоза только после его полной разгрузки."""
    produced: list[Report] = []
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != "caravan" or pack.status != "arrived":
            continue
        if pack.cargo.total() > EPSILON:
            continue
        if any(
            report.facts.get("event") == "caravan_arrival"
            and report.facts.get("pack") == pack.id
            for report in world.reports
        ):
            continue
        entries = world.ledger.entries
        carried = sum(
            entry.amount
            for entry in entries
            if entry.kind == "transfer"
            and entry.dst_id == pack.cargo.id
            and entry.reason in {"caravan_load", "caravan_fodder"}
        )
        delivered = sum(
            entry.amount
            for entry in entries
            if entry.kind == "transfer"
            and entry.src_id == pack.cargo.id
            and entry.reason == "caravan_unload"
        )
        lost = sum(
            entry.amount
            for entry in entries
            if entry.kind == "transfer"
            and entry.src_id == pack.cargo.id
            and entry.reason == "scattered"
        )
        consumed = sum(
            entry.amount
            for entry in entries
            if entry.kind == "transfer"
            and entry.src_id == pack.cargo.id
            and entry.reason == "fodder"
        )
        reported = delivered * (
            1.0 + world.rng.news.uniform(-CARAVAN_CHANNEL.noise, CARAVAN_CHANNEL.noise)
        )
        produced.append(
            make_report(
                world,
                CARAVAN,
                "pack",
                pack.id,
                f"Обоз прибыл: вёз {carried:.2f}, сказывают, доставил {reported:.2f}.",
                {
                    "event": "caravan_arrival",
                    "pack": pack.id,
                    "carried_approx": round(carried, 2),
                    "delivered_approx": round(reported, 2),
                    "lost": lost,
                    "consumed": consumed,
                    "route": list(pack.route),
                    "origin": pack.origin_tile_id,
                    "destination": pack.destination_tile_id,
                },
                world.clock.date,
                CARAVAN_CHANNEL.delay_months,
                CARAVAN_CHANNEL.confidence,
                distorted=abs(reported - delivered) > EPSILON,
                noise=CARAVAN_CHANNEL.noise,
                observer_id=pack.id,
            )
        )
    return produced


def phase_travel(world: World) -> None:
    """Разрешить уходы дворов, посылки и месячные наблюдения разведки.

    Разведка наблюдает в потоке `rng_world`; разобранные посылки топчут свой
    маршрут (`engine/trails.py`), а речные возы землю не топчут.
    """
    in_transit = _in_transit_pack_ids(world)
    world.month_events = observe_scout_packs(world, world.clock.date)
    resolve_migrations(world, world.clock.date)
    resolve_packs(world, world.clock.date)
    resolve_hunt_packs(world, world.clock.date)
    trails.tread_arrivals(world, in_transit)


def phase_day(world: World) -> None:
    """Дневной контур походов: `Pack` в пути, `eta_date` с точностью до суток.

    ADR 0071 п.3/0069 п.3: поход/обоз приходит в день срока, а не через
    округлённый месяц. И-4 разрешает дневной контур ровно для этого: месячный
    тик остаётся основным, а здесь проходятся СУТКИ текущего месяца и для
    каждой `in_transit` посылки с `eta_date` внутри месяца вызывается её
    штатный разбор (`hazards/travel.py` для отрядов/уходов,
    `economy/caravan.py` для обозов) с датой прибытия. Сутки, на которые посылка
    не поспела, уходят следующему месяцу — месяц по-прежнему главный тик.
    """
    date = world.clock.date
    # Топот идёт ЗА завершением ходьбы (ADR 0061), а дневной контур завершает
    # посылки точнее месяца (ADR 0069). Поэтому снимок пути берётся ДО суток, а
    # топот — ПОСЛЕ: к этому моменту пакет, дошедший сегодня, уже `arrived` и
    # проходит фильтр `trails.tread_arrivals`. Без этого вызова доход дневного
    # контура терялся: два месячных вызова снимают список ДО `phase_day`.
    in_transit = _in_transit_pack_ids(world)
    for day in range(1, int(DAYS_PER_MONTH) + 1):
        current = SimDate(date.year, date.month, day)
        resolve_migrations(world, current)
        resolve_packs(world, current)
        resolve_hunt_packs(world, current)
        caravan.resolve_caravans(world, current)
    # Топчутся только дошедшие за эти сутки: и `phase_caravan`, и `phase_travel`
    # уже оттоптали своих, и их пакеты в снимок не попали (статус `arrived`),
    # поэтому задвоения быть не может, а неподвижные посылки отсекает фильтр.
    trails.tread_arrivals(world, in_transit)


def phase_inform(world: World) -> None:
    """Собрать Report месяца через модуль знания (см. info/briefing.py).

    Плюс глаз усадьбы (engine/seat.py): соседние клетки — «видно с холма»
    с задержкой 0. Почта дальних не тронута: дальняя соль — только письмом.
    Плюс весть о подросшей угрозе (news/threats.py): метки топ-апа
    `phase_hazard` превращаются в `Report` (гейт `report_required`, ADR 0042),
    и весть о новой тропе (news/trails.py): метки `trail_born_`/`dirt_born_`
    `phase_hazard`/`phase_caravan` (топтание) превращаются в `Report` глаза
    (этап троп, ADR 0061 — как ADR 0044 для угроз). Плюс вести о проигрыше
    (`news/ruin.py`, ADR 0209): «поселение оставлено» и «стол сеньора пуст».
    Они не строчка в логе, а `Report` — иначе проигрыш был бы известен только
    отладчику (И-3).

    Отчётам месяца сначала отдаются СОБЫТИЯ месяца (`engine/events.py`,
    ADR 0079): что пришло/ушло/созрело — плоский список записей, а не снимок
    склада. Снимок после фаз детектором отчёта больше не является.
    """
    world.month_events = month_events(world)
    make_month_reports(world)
    report_caravan_arrivals(world)
    report_scout_observations(world)
    settle_reported_discoveries(world)
    try:
        from .seat import make_seat_eye_reports

        make_seat_eye_reports(world)
    except ImportError:  # pragma: no cover
        pass
    report_pending_topups(world)
    report_new_trails(world)
    report_ruin(world)


def phase_record(world: World) -> None:
    """Записать список фаз месяца, флаг mustered и сдвинуть календарь.

    Затухание троп (`engine/trails.py`): −0.5 износа там, где по клетке не
    ходили; вода и строеная дорога — всегда 0.
    """
    update_musters(world)
    trails.decay_trails(world)
    names = [phase.__name__ for phase in PHASES]
    world.phase_log.append((str(world.clock.date), names))
    world.clock.advance_month()


PHASES = (
    phase_season,
    phase_growth,
    phase_manor,
    phase_hay,
    phase_roadworks,
    phase_cook_meal,
    phase_labor,
    phase_spoil,
    phase_exchange,
    phase_consume,
    phase_demography,
    phase_obligations,
    phase_hazard,
    phase_migrate,
    phase_ruin,
    phase_caravan,
    phase_travel,
    phase_day,
    phase_decide,
    phase_inform,
    phase_record,
)


def run_month(world: World) -> None:
    """Выполнить все фазы месяца в фиксированном порядке."""
    for phase in PHASES:
        phase(world)

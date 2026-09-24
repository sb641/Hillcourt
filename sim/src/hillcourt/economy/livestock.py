"""Скот и тягло: вол, осёл, конь — пол, корм, приплод, пахота, воз.

Три вида, у каждого пол; молодняк появляется только редким приплодом в тике,
а не из меню и не из воздуха (рецепты `breed_*`/`mature_*`, ADR 0025).

Роли (одно правило на вид, без энциклопедии пород и без боевого движка):
  вол   — пахота: труд партии `harvest_grain` ×0.7; воза не тянет, не скачет;
  осёл  — воз: ёмкость ×1.2; не пашет (труд ×1.0);
  конь  — воз быстрее: ёмкость ×1.3, дни ×0.75; пашет хуже вола (труд ×0.9);
          верх (ride) — только пометка: боевое применение коня не считается,
          норму тэна закрывает лишь `war_kit` (флажок для чужого агента).

Без тягла плуг и телега живут, но хуже: инструмент (`wooden_plough`/`iron_share`)
и телега (`cart`) дают своё как раньше, тягло лишь удешевляет труд пахоты и
поднимает ёмкость/скорость воза. Выход — трудовой (больше партий с того же
труда), а не бесплатное зерно: каждая партия по-прежнему ест стоячую материю
и сходится по массе.

Корм — сено из своего стока (двор, амбар манора или склад поселения);
без корма скот гибнет, как свиньи (`starved`). Падёж возможен и сытому: малая месячная вероятность.
Коня держат свободные (`personal_status == free`); tied-двор с коня не начинает
и коня не использует: `draft_plough_factor`/`origin_draft`/`tend_animals` его
не видят (каталоги Legal не тронуты, ADR 0025).
"""

from __future__ import annotations

from ..engine.hexgrid import neighbor_ids
from ..ontology import SimDate, Stock
from ..world import World

EPSILON = 1e-9
PROCESSING_STOCK_ID = "sink:processing"
WASTE_STOCK_ID = "sink:waste"
EATEN_STOCK_ID = "sink:eaten"

SPECIES: tuple[str, ...] = ("ox", "donkey", "horse")
MALE: dict[str, str] = {"ox": "ox_m", "donkey": "donkey_m", "horse": "horse_m"}
FEMALE: dict[str, str] = {"ox": "ox_f", "donkey": "donkey_f", "horse": "horse_f"}
YOUNG: dict[str, str] = {"ox": "ox_calf", "donkey": "donkey_foal", "horse": "horse_foal"}

# Одно правило пахоты на вид: вол лучше всех, конь хуже вола, осёл не пашет.
PLOUGH_FACTOR: dict[str, float] = {"ox": 0.7, "donkey": 1.0, "horse": 0.9}
# Рецепты, чей труд дешевит тягло (в v0 пахота и жатва слиты в жатву поля).
PLOUGH_RECIPES: tuple[str, ...] = ("harvest_grain",)

# Воз: осёл — ёмкость, конь — ёмкость и скорость. Вол воза не знает.
DRAFT_CAPACITY: dict[str, float] = {"horse": 1.3, "donkey": 1.2, "none": 1.0}
DRAFT_DAYS: dict[str, float] = {"horse": 0.75, "donkey": 1.0, "none": 1.0}

# Приплод редок: ~1 молодняк в год на пару; взросление — ~5 месяцев.
BREED_PROB = 0.08
BREED_HAY = 4.0
GROW_PROB = 0.2
GROW_HAY = 2.0
# Падёж сытого стада: взрослые ~6 % в год, молодняк чаще.
DIE_PROB_ADULT = 0.005
DIE_PROB_YOUNG = 0.01

# Заготовка сена двором с тяглом: партий рецепта `gather_hay` в месяц (каждая
# партия 3.0 сена, труд 8 дней из остатка месяца). Без тягла двор не косит.
HAY_GATHER_BATCH_CAP = 2

ADULT_GOODS: frozenset[str] = frozenset(list(MALE.values()) + list(FEMALE.values()))
YOUNG_GOODS: frozenset[str] = frozenset(YOUNG.values())
DRAFT_GOODS: frozenset[str] = frozenset(list(ADULT_GOODS) + list(YOUNG_GOODS))
HORSE_GOODS: frozenset[str] = frozenset(
    (MALE["horse"], FEMALE["horse"], YOUNG["horse"])
)


def _amount(stock: Stock, good: str) -> float:
    """Сколько товара в стоке (0, если ключа нет)."""
    return float(stock.amounts.get(good, 0.0))


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def species_of(good: str) -> str | None:
    """Вид товара (`ox`/`donkey`/`horse`) или None, если не тягло."""
    for species in SPECIES:
        if good in (MALE[species], FEMALE[species], YOUNG[species]):
            return species
    return None


def draft_goods_for(world: World, household) -> frozenset[str]:
    """Товары тягла, которые двор вправе держать.

    Вол и осёл — всем; конь — только `free` (`can_hold_horse`). Для tied/раба
    конь в стоке не считается тяглом: он не пашет, не везёт и не выбирает
    `tend_animals`. Биология пассивна — кормить коня это не запрещает.
    """
    if can_hold_horse(world, household):
        return DRAFT_GOODS
    return DRAFT_GOODS - HORSE_GOODS


def draft_plough_factor(world: World, household) -> float:
    """Во сколько раз тягло удешевляет труд пахоты двора.

    Вол (≥ 1 головы взрослых) — 0.7; иначе конь — 0.9 (пашет хуже вола),
    но только у свободного; иначе 1.0 (осёл и молодняк не пашут). Без тягла
    плуг живёт как раньше, просто за полный труд.
    """
    stock = world.get_stock(household.stock_id)
    if _amount(stock, MALE["ox"]) + _amount(stock, FEMALE["ox"]) >= 1.0 - EPSILON:
        return PLOUGH_FACTOR["ox"]
    if can_hold_horse(world, household) and (
        _amount(stock, MALE["horse"]) + _amount(stock, FEMALE["horse"])
        >= 1.0 - EPSILON
    ):
        return PLOUGH_FACTOR["horse"]
    return 1.0


def origin_draft(world: World, settlement_id: str) -> str:
    """Лучшее тягло поселения-origin для воза: `horse` > `donkey` > `none`.

    Считаются взрослые головы в стоках живых дворов поселения плюс склад
    (как телега). Вол и молодняк воза не знают.
    """
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        return "none"
    horse = donkey = 0.0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.settlement_id != settlement_id or household.left_at is not None:
            continue
        stock = world.stocks.get(household.stock_id)
        if stock is None:
            continue
        if can_hold_horse(world, household):
            horse += _amount(stock, MALE["horse"]) + _amount(stock, FEMALE["horse"])
        donkey += _amount(stock, MALE["donkey"]) + _amount(stock, FEMALE["donkey"])
    stores = world.stocks.get(settlement.stores_stock_id)
    if stores is not None:
        horse += _amount(stores, MALE["horse"]) + _amount(stores, FEMALE["horse"])
        donkey += _amount(stores, MALE["donkey"]) + _amount(stores, FEMALE["donkey"])
    if horse >= 1.0 - EPSILON:
        return "horse"
    if donkey >= 1.0 - EPSILON:
        return "donkey"
    return "none"


def draft_capacity_factor(draft: str) -> float:
    """Множитель ёмкости воза от тягла (поверх тележного)."""
    return DRAFT_CAPACITY.get(draft, 1.0)


def draft_days_factor(draft: str) -> float:
    """Множитель дней пути от тягла (конь везёт быстрее)."""
    return DRAFT_DAYS.get(draft, 1.0)


def can_hold_horse(world: World, household) -> bool:
    """Может ли двор держать и использовать коня: только свободный.

    `holder`/`thegn`/`sokeman`/`geneat`/`free_landless` — `free`; `tied`
    (виллан/коттер) и `slave` коня не держат и не используют. Правило
    подключено к решениям (`draft_goods_for`) и тяглу (пахота/воз); каталоги
    Legal не тронуты (ADR 0025). Биология пассивна: кормление коня не запрещено.
    """
    return household.personal_status == "free"


def _cook(world: World, stock: Stock, recipe_id: str, date: SimDate) -> bool:
    """Провести рецепт скота через пул обработки; False — рецепта нет в каталоге.

    Материя не создаётся: входы уходят в пул, выходы и потери — из пула.
    """
    recipe = world.catalogs.recipes.get(recipe_id)
    if recipe is None:
        return False
    processing = world.get_stock(PROCESSING_STOCK_ID)
    waste = world.get_stock(WASTE_STOCK_ID)
    for good in sorted(recipe.inputs):
        amount = recipe.inputs[good]
        if amount > 0:
            world.ledger.transfer(stock, processing, good, amount, recipe_id, date)
    allowed = set(recipe.outputs) | set(recipe.loss)
    for good in sorted(recipe.outputs):
        amount = recipe.outputs[good]
        if amount > 0:
            world.ledger.emit(processing, stock, good, amount, recipe_id, date, allowed)
    for good in sorted(recipe.loss):
        amount = recipe.loss[good]
        if amount > 0:
            world.ledger.emit(processing, waste, good, amount, recipe_id, date, allowed)
    if abs(processing.total()) >= 1e-6:
        raise AssertionError(f"Рецепт '{recipe_id}' не сбалансирован в стаде")
    return True


def _pasture_tiles_of_stock(world: World, stock: Stock) -> list:
    """Пастбища стойла: куда стадо вправе выйти на подножный корм.

    Двор — его косьбенные пастбища (`hay_pastures`: свой надел + общинная
    клетка по `works_tiles`/`Right.common`; соседство правом не является —
    ADR 0077), амбар манора — пастбища из клеток книги;
    склад поселения — клетка поселения + `works_tiles`. Чужих пастбищ нет.
    """
    from ..legal.regimes import (
        has_access_to_communal_tile,
        is_communal_collection_tile,
    )

    tiles = world.tiles
    if stock.owner_kind == "household":
        household = world.households.get(stock.owner_id)
        if household is None:
            return []
        return [t for t in hay_pastures(world, household) if t.terrain == "pasture"]
    if stock.owner_kind == "manor":
        manor = world.manors.get(stock.owner_id)
        candidates = manor.tile_ids if manor is not None else []
    elif stock.owner_kind == "settlement":
        settlement = world.settlements.get(stock.owner_id)
        if settlement is None:
            return []
        home = tiles.get(_tile_id(*settlement.coord))
        candidates = ([home.id] if home is not None else []) + list(
            settlement.works_tiles
        )
    else:
        return []
    out = []
    for tid in sorted(set(candidates)):
        tile = tiles.get(tid)
        if tile is not None and tile.terrain == "pasture":
            out.append(tile)
    return out


def graze_for_herd(world: World, stock: Stock, amount: float, date: SimDate) -> float:
    """Списать подножный корм со стоячих пастбищ стойла; вернуть съеденное.

    Летние 70 % не из воздуха (отказ хозяина бесплатной траве): каждая клетка
    отдаёт не больше своей кормовой ёмкости в месяц
    (`engine/tile_view.pasture_forage_capacity`, только чтение) и не больше
    стоячего сена — край общий для всех стад месяца. Порядок детерминирован
    (id клетки). Проводка — перевод в `sink:eaten` (причина `graze`).
    """
    from ..engine.tile_view import pasture_forage_capacity

    if amount <= EPSILON:
        return 0.0
    eaten = world.get_stock(EATEN_STOCK_ID)
    stamp = f"{date.year:04d}-{date.month:02d}"
    taken = 0.0
    for tile in _pasture_tiles_of_stock(world, stock):
        if taken + EPSILON >= amount:
            break
        cap = pasture_forage_capacity(world, tile.id)
        used = float(world.stats.get(f"graze_used:{stamp}:{tile.id}", 0.0))
        room = max(0.0, cap - used)
        if room <= EPSILON:
            continue
        tile_stock = world.stocks.get(tile.standing_stock_id)
        if tile_stock is None:
            continue
        give = min(tile_stock.amounts.get("hay", 0.0), room, amount - taken)
        if give <= EPSILON:
            continue
        world.ledger.transfer(tile_stock, eaten, "hay", give, "graze", date)
        world.stats[f"graze_used:{stamp}:{tile.id}"] = used + give
        taken += give
        world.bump("hay_grazed", give)
    return taken


def _feed_stock(world: World, stock: Stock, date: SimDate) -> None:
    """Скормить скот стойла; без корма — падёж от голода.

    Норма сезонная: зимой полное сено из стока; летом — 30 % сеном из стока
    + 70 % выпасом со стоячих пастбищ (`graze_for_herd`, кап ёмкости);
    недобор выпаса — снова сеном из стока (скот у полного стога не голодает),
    затем — падёж той же пропорцией, что у дворов.
    """
    from .needs import hay_fraction, hay_need_rate

    needs = world.needs
    if needs is None:
        return
    feed = needs.feed_good
    for animal in sorted(needs.feed_per_month):
        count = _amount(stock, animal)
        if count <= EPSILON:
            continue
        base = float(needs.feed_per_month[animal])
        hay_part = count * hay_need_rate(world, animal, date.month)
        given = min(_amount(stock, feed), hay_part)
        if given > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(EATEN_STOCK_ID), feed, given, "feed", date
            )
        grazed = graze_for_herd(world, stock, count * base - hay_part, date)
        extra = min(max(0.0, _amount(stock, feed)), count * base - given - grazed)
        if extra > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(EATEN_STOCK_ID), feed, extra, "feed", date
            )
        shortfall = count * base - given - grazed - extra
        if shortfall > EPSILON and count > EPSILON:
            fraction = min(1.0, shortfall / (count * base))
            kill = min(count, max(1.0, count * fraction))
            world.ledger.transfer(
                stock, world.get_stock(WASTE_STOCK_ID), animal, kill, "starved", date
            )
            world.bump("animals_starved", kill)


def _hay_surplus_months(world: World, stock: Stock, month: int) -> float:
    """На сколько месяцев сена хватит стаду стока (для гейта приплода).

    Нужда сезонная: летом сена надо меньше — излишек считается честно.
    Приплод и взросление — только при устойчивом излишке (≥ 3 мес корма).
    """
    from .needs import hay_need_rate

    needs = world.needs
    if needs is None:
        return 0.0
    monthly = sum(
        _amount(stock, animal) * hay_need_rate(world, animal, month)
        for animal in needs.feed_per_month
    )
    if monthly <= EPSILON:
        return 0.0
    return _amount(stock, needs.feed_good) / monthly


def _herd_roll(world: World, stock: Stock, date: SimDate, purpose: str, index: int = 0) -> float:
    """Жребий стойла 0..1: хеш (seed, сток, месяц, цель, номер), не поток.

    Приплод/взросление/падёж не едят общий `rng.economy`: стадо не реролит
    жребий демографии, обмена и разведки (долг ADR 0030/0032). Тот же приём,
    что характер новорождённого (`demography._give_birth`) и черты людей
    (`scenario`: `seed:persons`). Один seed — один жребий; порядка обхода нет
    в формуле — только id стока. Без голов вызывающий не бросает (как раньше).
    """
    import hashlib

    stamp = f"{date.year:04d}-{date.month:02d}"
    digest = hashlib.md5(
        f"{world.seed}:herd:{stock.id}:{stamp}:{purpose}:{index}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _breed_stock(
    world: World, stock: Stock, date: SimDate, breed_prob: float
) -> None:
    """Редкий приплод: пара взрослых + сено в одном стоке → молодняк.

    Родители — условие, не вход: самка не исчезает, телёнок рождается из сена.
    Бросок — только при живой паре, полном сене И запасе на 3+ мес, иначе
    жребий не бросается; общий `rng.economy` не естся никогда.
    """
    if _hay_surplus_months(world, stock, date.month) < 3.0:
        return
    for species in SPECIES:
        if _amount(stock, MALE[species]) < 1.0 - EPSILON:
            continue
        if _amount(stock, FEMALE[species]) < 1.0 - EPSILON:
            continue
        if _amount(stock, "hay") < BREED_HAY - EPSILON:
            continue
        if _herd_roll(world, stock, date, f"breed_{species}") >= breed_prob:
            continue
        if _cook(world, stock, f"breed_{species}", date):
            world.bump(f"born_{species}")


def _mature_stock(
    world: World, stock: Stock, date: SimDate, grow_prob: float
) -> None:
    """Взросление молодняка за сено; пол — жребием (целые головы).

    Только при запасе сена на 3+ мес: растить не на что — не растим.
    """
    if _hay_surplus_months(world, stock, date.month) < 3.0:
        return
    for species in SPECIES:
        young = YOUNG[species]
        heads = int(_amount(stock, young) + 1e-9)
        for head in range(heads):
            if _amount(stock, young) < 1.0 - EPSILON:
                break
            if _amount(stock, "hay") < GROW_HAY - EPSILON:
                break
            if _herd_roll(world, stock, date, f"mature_{young}", head) >= grow_prob:
                continue
            sex = "m" if _herd_roll(world, stock, date, f"sex_{young}", head) < 0.5 else "f"
            if _cook(world, stock, f"mature_{young}_{sex}", date):
                world.bump(f"grown_{species}")


def _die_stock(
    world: World,
    stock: Stock,
    date: SimDate,
    die_adult: float,
    die_young: float,
) -> None:
    """Случайный падёж тягла (целые головы в отход, причина `died`).

    Свиней не трогает: у них по-старому только голод, иначе поплыл бы канон.
    Без голов жребий не бросается.
    """
    for good in sorted(DRAFT_GOODS):
        prob = die_young if good in YOUNG_GOODS else die_adult
        if prob <= 0:
            continue
        heads = int(_amount(stock, good) + 1e-9)
        for head in range(heads):
            if _amount(stock, good) < 1.0 - EPSILON:
                break
            if _herd_roll(world, stock, date, f"die_{good}", head) >= prob:
                continue
            world.ledger.transfer(
                stock, world.get_stock(WASTE_STOCK_ID), good, 1.0, "died", date
            )
            world.bump("animals_died")


def _manor_stocks(world: World) -> list[Stock]:
    """Амбары маноров и склады поселений (по id).

    Их скот тик не кормит, только месяц стада. Корневой сток
    (`settlement:hill_court`) — он же амбар корня: входит один раз.
    """
    out: dict[str, Stock] = {}
    for manor_id in sorted(world.manors):
        stock_id = world.manors[manor_id].stock_id
        stock = world.stocks.get(stock_id) if stock_id else None
        if stock is not None and stock.owner_kind in ("manor", "settlement"):
            out[stock.id] = stock
    for sid in sorted(world.settlements):
        stock = world.stocks.get(world.settlements[sid].stores_stock_id)
        if stock is not None:
            out.setdefault(stock.id, stock)
    return [out[key] for key in sorted(out)]


def _herd_stocks(world: World) -> list[Stock]:
    """Стоки со скотом: живые дворы, затем амбары и склады (по id, без дублей).

    Стойло — двор или амбар; склад поселения тоже кормится, чтобы тягло,
    засчитанное возу, не жило бесплатно.
    """
    out: dict[str, Stock] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            out[stock.id] = stock
    for stock in _manor_stocks(world):
        out.setdefault(stock.id, stock)
    return [out[key] for key in sorted(out)]


def hay_pastures(world: World, household) -> list:
    """Пастбища, куда двор вправе послать косцов.

    Это свой кормящий надел (`feeds_household`) или общинная клетка по праву
    доступа — `works_tiles` поселения либо `Right.kind=common`
    (`legal/regimes.py`; ADR 0077: одно лишь соседство правом не является).
    Чужая дальняя клетка доступа не даёт.
    """
    from ..legal.regimes import (
        has_access_to_communal_tile,
        is_communal_collection_tile,
    )
    from .labor import own_tiles

    out: list = []
    seen: set[str] = set()
    for tile in own_tiles(world, household):
        regime = world.catalogs.land_regimes.get(tile.regime_id)
        if tile.terrain == "pasture" and regime is not None and regime.feeds_household:
            out.append(tile)
            seen.add(tile.id)
    home = world.tiles.get(household.current_tile_id)
    if home is not None:
        for neighbour_id in neighbor_ids(world, home):
            neighbour = world.tiles.get(neighbour_id)
            if neighbour is None or neighbour.id in seen:
                continue
            if neighbour.terrain != "pasture":
                continue
            if not is_communal_collection_tile(neighbour):
                continue
            if has_access_to_communal_tile(world, household, neighbour):
                out.append(neighbour)
                seen.add(neighbour.id)
    return sorted(out, key=lambda tile: tile.id)


def _draft_hay_need(world: World, household, stock: Stock, month: int) -> float:
    """Сколько сена в месяц нужно тяглу двора (0, если тягла нет).

    Норма сезонная (выпас летом). Считается только тягло, которое двор вправе
    держать (`draft_goods_for`): конь tied-двора сена не просит.
    """
    from .needs import hay_need_rate

    needs = world.needs
    if needs is None:
        return 0.0
    allowed = draft_goods_for(world, household)
    total = 0.0
    for good in sorted(allowed):
        count = _amount(stock, good)
        if count > EPSILON:
            total += count * hay_need_rate(world, good, month)
    return total


def gather_draft_hay(world: World, date: SimDate) -> None:
    """Заготовка сена двором, держащим тягло (труд из остатка месяца).

    Пастбище — стоячая материя (`grow_hay`), поэтому сено идёт через рецепт
    `gather_hay` и `sink:processing`: масса сходится, из воздуха ничего не
    берётся. Косцы выходят только при нехватке сена у тягла; конь tied-двора
    в тягло не идёт (`draft_goods_for`). Без тягла — ни проводки, ни броска
    RNG, поэтому канон без скота бит-в-бит.

    Ёмкость пастбища — общая на всех косцов месяца: `tile_batches` считает
    партии на клетку против `labor._tile_batch_cap`, как `work_month` (тот же
    учёт, что у рецепта в поле). Клетки берутся по праву доступа
    (`hay_pastures`: свой надел или общинная клетка по `works_tiles`/
    `Right.common`, ADR 0077) — это законное исключение из «только кормящий
    надел»: сено в v0 косит двор с тяглом.
    """
    recipe = world.catalogs.recipes.get("gather_hay")
    if recipe is None or not recipe.draws_standing:
        return
    from .labor import _tile_batch_cap, apply_recipe, tool_yield_factor

    tile_cap = _tile_batch_cap(world)
    tile_batches: dict[str, int] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        stock = world.stocks.get(household.stock_id)
        if stock is None:
            continue
        need = _draft_hay_need(world, household, stock, date.month)
        if need <= EPSILON or _amount(stock, "hay") + EPSILON >= need:
            continue
        factor = max(tool_yield_factor(world, household), EPSILON)
        draw_per_batch = float(recipe.draws_standing.get("hay", 0.0)) * factor
        if draw_per_batch <= EPSILON:
            continue
        batches = 0
        for tile in hay_pastures(world, household):
            remaining = tile_cap - tile_batches.get(tile.id, 0)
            if remaining <= 0:
                continue
            tile_stock = world.stocks.get(tile.standing_stock_id)
            if tile_stock is None:
                continue
            made_here = 0
            while (
                batches < HAY_GATHER_BATCH_CAP
                and made_here < remaining
                and household.labor_days + EPSILON >= recipe.labor_days
                and _amount(stock, "hay") + EPSILON < need
            ):
                scale = min(1.0, _amount(tile_stock, "hay") / draw_per_batch)
                if scale <= 1e-4:
                    break
                apply_recipe(world, household, tile, recipe, scale, date)
                household.labor_days = max(
                    0.0, household.labor_days - recipe.labor_days * scale
                )
                batches += 1
                made_here += 1
            if made_here:
                tile_batches[tile.id] = tile_batches.get(tile.id, 0) + made_here
            if batches >= HAY_GATHER_BATCH_CAP:
                break


def livestock_month(
    world: World,
    date: SimDate,
    breed_prob: float = BREED_PROB,
    grow_prob: float = GROW_PROB,
    die_adult: float = DIE_PROB_ADULT,
    die_young: float = DIE_PROB_YOUNG,
) -> None:
    """Месяц стада: корм амбаров, приплод, взросление, падёж.

    Вызывается в конце `phase_consume` (дворы уже накормлены тиком): амбары
    кормятся здесь же, затем каждый сток плодится и растёт при полном сене.
    Труд не списывается — биология пассивна, не ферма-кликер. Косьба — только
    ранняя фаза `phase_hay` (`gather_draft_hay`, единственная, ADR 0050):
    поздний добор удалён (замер: за 60 мес шира накосил 0 — пастбища пусты,
    а резерв под него жал поле). Без скота — ни одной проводки и ни одного
    броска RNG.
    """
    for stock in _manor_stocks(world):
        _feed_stock(world, stock, date)
    for stock in _herd_stocks(world):
        _breed_stock(world, stock, date, breed_prob)
        _mature_stock(world, stock, date, grow_prob)
        _die_stock(world, stock, date, die_adult, die_young)

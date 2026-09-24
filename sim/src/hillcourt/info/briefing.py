"""Месячная сводка известий: холм, сосед, гонец, обоз или молчание.

Каждый `Report` собирается по схеме `event_date` → канал → `delivery_date` →
`content`/`facts` → `confidence`/`noise`. Числа соседа, гонца, обоза и дальней
деревни сдвигаются потоком `rng_news`, поэтому это рассказ, а не истина.
Молчание по дальней деревне — отдельный `Report` о том же месте, а не
«всё хорошо».
"""

from __future__ import annotations

from ..engine.events import event_total
from ..news.propagation import make_report
from ..ontology import SimDate
from ..world import World
from .sources import ADJACENT_DAILY, CARAVAN, EYE_FROM_HILL, MESSENGER, SILENCE


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _court_tile(world: World) -> str:
    settlement = world.settlements[world.player.court_settlement_id]
    return _tile_id(*settlement.coord)


def _settlement_tile(settlement) -> str:
    return _tile_id(*settlement.coord)


def make_month_reports(world: World) -> None:
    """Породить известия месяца; истина места игроку не отдаётся."""
    date = world.clock.date
    month = world.clock.month
    rng = world.rng.news
    court_tile = _court_tile(world)

    court_household = world.households.get(world.player.household_id)
    court_grain = 0.0
    if court_household is not None:
        court_grain = world.get_stock(court_household.stock_id).amounts.get("grain", 0.0)
    make_report(
        world,
        EYE_FROM_HILL,
        "tile",
        court_tile,
        f"Холм: зерна во дворе примерно {court_grain:.0f}.",
        {"grain_approx": round(court_grain, 1)},
        date,
        0,
        1.0,
        distorted=False,
        noise=0.0,
    )

    _report_adjacent(world, date, court_tile, rng)
    _report_messenger(world, date, court_tile, rng)
    _report_caravan_or_silence(world, date, month, rng)
    _report_caravan_arrivals(world, date, rng)
    _report_lost_packs(world, date)
    _report_far_villages(world, date, rng)


def report_travel_loss(world: World, tile_id: str, date: SimDate) -> None:
    """Молчание о клетке, откуда двор/отряд не вернулся: сигнал, а не «всё хорошо»."""
    make_report(
        world,
        SILENCE,
        "tile",
        tile_id,
        "Двор ходил на клетку и не вернулся. Известий о нём нет.",
        {},
        date,
        0,
        0.3,
        distorted=False,
    )


def _report_adjacent(world: World, date: SimDate, court_tile: str, rng) -> None:
    farmsteads = sorted(
        (s for s in world.settlements.values() if s.kind == "farmstead"),
        key=lambda s: s.id,
    )
    if not farmsteads or rng.random() <= 0.25:
        make_report(
            world,
            SILENCE,
            "channel",
            "neighbors",
            "Известий о соседях нет.",
            {},
            date,
            0,
            0.3,
            distorted=False,
        )
        return
    farmstead = rng.choice(farmsteads)
    household = (
        world.households.get(farmstead.household_ids[0])
        if farmstead.household_ids
        else None
    )
    grain = 0.0
    if household is not None:
        grain = world.get_stock(household.stock_id).amounts.get("grain", 0.0)
    reported = grain * (1.0 + rng.uniform(-0.3, 0.3))
    make_report(
        world,
        ADJACENT_DAILY,
        "tile",
        _settlement_tile(farmstead),
        f"Сосед {farmstead.name}: зерна, сказывают, около {reported:.0f}.",
        {"grain_approx": round(reported, 1)},
        date,
        1,
        0.7,
        distorted=abs(reported - grain) > 1e-9,
        noise=0.3,
    )


def _report_messenger(world: World, date: SimDate, court_tile: str, rng) -> None:
    arrears_households = sum(
        1
        for hid in sorted(world.households)
        if world.households[hid].left_at is None
        and world.households[hid].arrears_days > 0
    )
    reported = arrears_households
    drift = rng.choice([-1, 0, 0, 0, 1])
    if drift:
        reported = max(0, arrears_households + drift)
    make_report(
        world,
        MESSENGER,
        "barony",
        "barony",
        f"Шериф: дворов с недоимкой, по слухам, {reported}.",
        {"arrears_households": reported},
        date,
        1,
        0.5,
        distorted=reported != arrears_households,
        noise=0.15,
    )


def _salt_in_settlement(world: World, settlement) -> float:
    """Соль там, где она лежит: живые дворы, сток поселения и возы в пути.

    Пустой `settlement.stores_stock_id` не должен превращаться в «соли нет»:
    варят её дворы солеваров, и обоз должен читать их стоки, а не только амбар.
    Соль, уже погруженная в вышедший из деревни воз, тоже лежит в мире; без неё
    в месяц отправки отчёт видел бы пустую деревню и врал «соли 0».
    """
    total = world.get_stock(settlement.stores_stock_id).amounts.get("salt", 0.0)
    for household_id in sorted(settlement.household_ids):
        household = world.households.get(household_id)
        if household is None or household.left_at is not None:
            continue
        total += world.get_stock(household.stock_id).amounts.get("salt", 0.0)
    origin_tile = _settlement_tile(settlement)
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != "caravan" or pack.status != "in_transit":
            continue
        if pack.origin_tile_id != origin_tile:
            continue
        total += pack.cargo.amounts.get("salt", 0.0)
    return total


def _report_caravan_or_silence(world: World, date: SimDate, month: int, rng) -> None:
    salt = world.settlements.get("salt_village")
    if salt is None or month % 3 != 0:
        return
    salt_tile = _settlement_tile(salt)
    if rng.random() < 0.2:
        make_report(
            world,
            SILENCE,
            "tile",
            salt_tile,
            "Из деревни у соли вестей нет; обоз не дошёл.",
            {},
            date,
            0,
            0.3,
            distorted=False,
        )
        return
    # ADR 0079: отчёт — СОБЫТИЯ месяца, не снимок склада. Обоз с коротким
    # путём увозит соль в том же месяце ДО `phase_inform` — снимок после фаз
    # честно пуст, и игрок не видит оттока. Источник истины — `month_events`
    # (события прихода/ухода, `engine/events.py`). Отчёт «о деревне у соли»
    # говорит о ПОТОКЕ: сколько соли обоз увёз (`salt_approx`) и сколько
    # привезено (`salt_received`). Нет событий месяца — честный ноль.
    events = getattr(world, "month_events", [])
    salt_amount = event_total(
        events, "caravan_departure", "salt", settlement_id=salt.id
    )
    salt_received = event_total(
        events, "caravan_arrival", "salt", settlement_id=salt.id
    )
    reported = salt_amount * (1.0 + rng.uniform(-0.4, 0.4))
    make_report(
        world,
        CARAVAN,
        "tile",
        salt_tile,
        f"Обоз: соли из деревни будто бы {reported:.0f}.",
        {
            "salt_approx": round(reported, 1),
            "salt_received": round(salt_received, 1),
        },
        date,
        2,
        0.4,
        distorted=abs(reported - salt_amount) > 1e-9,
        noise=0.4,
    )


def _settlement_id_at(world: World, tile_id: str) -> str | None:
    """id поселения на клетке или None, если клетки/поселения нет."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return None
    return tile.settlement_id


def _barter_memory_for(world: World, origin: str, destination: str) -> dict | None:
    """Последняя память сделки по маршруту с ненулевым грузом и долей.

    Память — не истина стока: ключ описывает конкретный рейс
    (`origin->destination:good`), а `ratio` — доля доехавшего груза. Записи
    без груза или без доли (рейс ещё не разгружался, был пропуск) не годятся.
    """
    prefix = f"{origin}->{destination}:"
    best: dict | None = None
    for key in sorted(world.barter_memory):
        if not key.startswith(prefix):
            continue
        memory = world.barter_memory[key]
        if memory.get("ratio") is None:
            continue
        if float(memory.get("carried", 0.0)) <= 0.0:
            continue
        if best is None or str(memory.get("date", "")) > str(best.get("date", "")):
            best = memory
    return best


def _has_report(
    world: World, source: str, subject_id: str, event_date: SimDate
) -> bool:
    """Уже рождался ли такой Report (источник, место, дата события) — не дублировать."""
    return any(
        report.source == source
        and report.subject_id == subject_id
        and report.event_date == event_date
        for report in world.reports
    )


def _report_caravan_arrivals(world: World, date: SimDate, rng) -> None:
    """Дошедший обоз: Report «вёз / доехало / доля» по памяти сделки.

    Разбирается только `world.barter_memory`, а истину воза (сток, груз) не
    читаем: игроку отдаётся рассказ о рейсе, согласованный с миром. Дата
    доставки равна дате события — отчёт рождается в месяц прибытия
    (`eta_date` совпал с текущим). Записи о маршруте нет — отчёт не
    выдумывается. `lost` — погибшие люди воза; у обоза `member_ids` пуст,
    поэтому 0 (людские потери двора `household_move` пишет `travel.py`, не здесь).

    `subject_id` — маршрут `origin->destination`, а не клетка: это известие о
    рейсе, и оно не подменяет отчёт о стоке поселения на той же клетке.
    """
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != "caravan" or pack.status != "arrived":
            continue
        if (pack.eta_date.year, pack.eta_date.month) != (date.year, date.month):
            continue
        origin = _settlement_id_at(world, pack.origin_tile_id)
        destination = _settlement_id_at(world, pack.destination_tile_id)
        if origin is None or destination is None:
            continue
        route_id = f"{origin}->{destination}"
        if any(
            report.source == CARAVAN
            and report.subject_id == route_id
            and report.event_date == date
            and "carried" in report.facts
            for report in world.reports
        ):
            continue
        memory = _barter_memory_for(world, origin, destination)
        if memory is None:
            continue
        carried = float(memory.get("carried", 0.0))
        delivered = float(memory.get("delivered", 0.0))
        ratio = float(memory["ratio"])
        make_report(
            world,
            CARAVAN,
            "route",
            route_id,
            f"Обоз {origin}→{destination}: вёз {carried:.2f}, "
            f"доехало {delivered:.2f} (доля {ratio:.2f}).",
            {
                "carried": carried,
                "delivered": delivered,
                "ratio": ratio,
                "lost": 0,
            },
            date,
            0,
            0.6,
            distorted=False,
            noise=0.1,
        )


def _report_lost_packs(world: World, date: SimDate) -> None:
    """Погибший воз: молчание о клетке назначения, без дубля уже рождённого.

    H1/H3 (посылки, уходы) уже ставят `silence` на дату гибели; здесь только
    добирается воз, о котором иначе игрок не узнал бы. Проверка по
    `subject_id + source + event_date`.
    """
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != "caravan" or pack.status != "lost":
            continue
        destination = pack.destination_tile_id
        if _has_report(world, SILENCE, destination, date):
            continue
        make_report(
            world,
            SILENCE,
            "tile",
            destination,
            "Обоз в пути не дошёл. Известий о нём нет.",
            {},
            date,
            0,
            0.3,
            distorted=False,
        )


def _grain_in_households(world: World, settlement) -> float:
    """Зерно там, где оно лежит: в стоках живых дворов поселения.

    Клетка (`Tile.state`) не читается: ушедший двор `left_at` зерна не носит,
    поэтому его сток в счёт не идёт. Для дальней деревни этого достаточно —
    обозу не нужно ни поселенческий амбар, ни возы в пути.
    """
    total = 0.0
    for household_id in sorted(settlement.household_ids):
        household = world.households.get(household_id)
        if household is None or household.left_at is not None:
            continue
        total += world.get_stock(household.stock_id).amounts.get("grain", 0.0)
    return total


def _report_far_villages(world: World, date: SimDate, rng) -> None:
    """Обоз о дальней деревне раз в 3 месяца или молчание о ней.

    Дальняя деревня (`kind == "village"`) не видна игроку напрямую: её зерно
    приходит только `Report`-ом с датой и источником. В 20 % месяцев вместо
    отчёта ставится молчание — отдельный сигнал о той же клетке.
    """
    if world.clock.month % 3 != 0:
        return
    far_villages = sorted(
        (s for s in world.settlements.values() if s.kind == "village"),
        key=lambda s: s.id,
    )
    for settlement in far_villages:
        tile = _settlement_tile(settlement)
        if rng.random() < 0.2:
            make_report(
                world,
                SILENCE,
                "tile",
                tile,
                "Из дальней деревни вестей нет.",
                {},
                date,
                0,
                0.3,
                distorted=False,
            )
            continue
        grain = _grain_in_households(world, settlement)
        reported = grain * (1.0 + rng.uniform(-0.4, 0.4))
        make_report(
            world,
            CARAVAN,
            "tile",
            tile,
            f"Дальняя деревня {settlement.name}: зерна, сказывают, около {reported:.0f}.",
            {"grain_approx": round(reported, 1)},
            date,
            2,
            0.4,
            distorted=abs(reported - grain) > 1e-9,
            noise=0.4,
        )

"""Месячная сводка известий: холм, сосед, гонец, обоз или молчание.

Каждый `Report` собирается по схеме `event_date` → канал → `delivery_date` →
`content`/`facts` → `confidence`/`noise`. Числа соседа, гонца, обоза и дальней
деревни сдвигаются потоком `rng_news`, поэтому это рассказ, а не истина.
Молчание по дальней деревне — отдельный `Report` о том же месте, а не
«всё хорошо».

**ЗАКОН СВОЕГО ЗАПАСА (ADR 0205).** «Холм» — достоверная сводка, а не слух:
свидетель — сам держатель, задержка 0, `confidence` 1.0, `noise` 0.0
(`docs/05_information.md:70`). Одно условие: **весть обязана называть то же,
что читает.** Предмет вести глаза — зерно живых дворов **клетки зала**
(`docs/05:100-105`, как у соседних глазных клеток, `engine/seat.py:242-252`),
а НЕ сток `world.player.household_id`: держатель, книга корня и личный ларёк
лорда — три разных числа, и склеивать их нельзя. Книга корня идёт отдельной
вестию `subject_kind="manor"` — это то, что `grant_grain`/`relief` берут, то
есть «что можно раздать».

**ЗАКОН ИМЕНИ (ADR 0212).** На малой карте поселение и клетка зала совпадают,
и подпись «Холм» была честной. На большой карте (`v0_barony_100`) поселение
`hill_court` — 90 дворов на восьми кварталах, а клетка зала `t_45_50` — 12
дворов: то же самое число, но подпись «Холм» стала ложью на 87 % холма (12 дворов из 90).
Поэтому **слово «холм» отдано поселению, а не клетке**: глаз называет клетку
(«У зала: в дворах этой клетки зерна около N»), а всё поселение приходит
отдельной вестью шерифа `subject_kind="settlement"` — слухом, с задержкой
месяц и дрейфом в пределах шума канала. Слово «холм» не может стоять в тексте
вести, `subject_id` которой — клетка: текст обязан называть свою область.
"""

from __future__ import annotations

from ..engine.events import event_total
from ..engine.manor import manor_stock, root_manor
from ..engine.ruin import barony_settlements, left_count
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


def _live_households_on_tile(world: World, tile_id: str) -> list[str]:
    """id дворов, которые ЖИВУТ на клетке (ушёл — не считается).

    Единственный источник «чей это запас» для вести о клетке. Клетку зала
    намеренно пропускает `engine/seat.py::make_seat_eye_reports`
    (`docs/05_information.md:101-105`): глазная клетка ≠ зал, и зал докладывает
    `info.briefing`. Поэтому «во дворе» — это ДВОРЫ клетки, а не сток одного
    двора: тот же закон, что у соседних глазных клеток
    (`engine/seat.py:242-252`, `_grain_on_tile`).
    """
    return [
        hid
        for hid in sorted(world.households)
        if world.households[hid].left_at is None
        and world.households[hid].current_tile_id == tile_id
    ]


def _grain_on_tile(world: World, tile_id: str) -> float:
    """Зерно живых дворов клетки — величина, которую глаз имеет право назвать."""
    total = 0.0
    for hid in _live_households_on_tile(world, tile_id):
        total += world.get_stock(world.households[hid].stock_id).amounts.get("grain", 0.0)
    return total


def _report_court(world: World, date: SimDate, court_tile: str) -> None:
    """Весть ГЛАЗА о КЛЕТКЕ ЗАЛА: то, что весть заявляет, и то, что читает, — одно.

    **Предмет — дворы этой клетки, а не сток лорда.** Раньше число бралось из
    `world.player.household_id` (личный ларёк держателя), а текст обещал «Холм:
    зерна во дворе». Это расхождение, из-за которого игрок читал «0» и делал вывод
    «холм умирает», пока книга корня стояла на 849.2 (замер `start_stand`, 24 мес,
    сид 1729). Закон `docs/05_information.md:100-105`: глазная клетка несёт
    «зерно живых дворов» — как у соседей (`engine/seat.py:242-252`), и
    `engine/seat.py:238-239` пропускает клетку зала только потому, что о ней
    докладывает этот модуль.

    **Клетка зала — не «холм» (ADR 0212).** На `v0_barony_100` поселение
    `hill_court` — 90 дворов на восьми кварталах, клетка зала `t_45_50` — 12:
    глаз по закону видит радиус 1 от `Manor.seat_tile_id`
    (`docs/05:101-103`), а поселение выходит за него на три гекса, поэтому
    точного числа всего поселения у глаза нет и быть не может. Значит глаз
    называет **клетку**, а «холм» получает отдельную весть — см.
    `_report_settlement_grain`. Слово «холм» в этом тексте запрещено: оно
    называет поселение, а `subject_id` здесь — клетка.

    **Достоверность 1.0 здесь законна, потому что предмет — свой запас.**
    `docs/05_information.md:70` для `eye_from_hill` прямо требует `confidence`
    1.0 и `noise` 0.0: свидетель — сам держатель, задержка 0. Одно условие:
    весть обязана называть то же, что читает. Называя не то, она делала
    абсолютную достоверность враньём; называя то же — она законна.

    **Книга корня — отдельная весть, а не этот факт.** Амбар манора
    (`Manor.stock_id`) — то, что `grant_grain` и `relief` берут; личный ларёк
    лорда — то, из чего ест его собственный двор. Все три числа разные, и
    склеивать их в одно нельзя: игрок решал бы «кормить ли», глядя на число,
    которое к подаче отношения не имеет.
    """
    grain = _grain_on_tile(world, court_tile)
    make_report(
        world,
        EYE_FROM_HILL,
        "tile",
        court_tile,
        f"У зала: в дворах этой клетки зерна около {grain:.0f}.",
        {"grain_approx": round(grain, 1)},
        date,
        0,
        1.0,
        distorted=False,
        noise=0.0,
    )


def _report_manor_barn(world: World, date: SimDate) -> None:
    """Весть о книге корня: сколько зерна лорд имеет право раздать.

    Предмет — `Manor.stock_id` амбара корневого манора, тот самый сток, из
    которого `grant_grain` отдаёт зерно двору и из которого `relief` платит
    подачу. Это НЕ «зерно во дворе» и НЕ личный ларёк лорда: третья величина,
    и раньше она была не видна вовсе — потому что весть читала ларёк и
    называла «Холм».

    Канал `eye_from_hill` по той же причине, что у холма (`docs/05:70`):
    книга корня — достояние держателя, а не чужой двор, задержка 0, свидетель
    он сам. Всеведение не открывается: `build_player_view`
    (`news/views.py:70-78`) читает только доставленные `Report` и не имеет
    пути к `Tile`/`Household`; число попадает к игроку тем же `facts`, что и
    любой факт вести, — плоским числом, без ссылки на контейнер.

    `subject_kind="manor"` — тот же словарь, что у `engine/manor.py:1082`
    (просьба тэна о железе) и `economy/thegn.py:254`.
    """
    manor = root_manor(world)
    if manor is None:
        return
    barn = manor_stock(world, manor)
    if barn is None:
        return
    grain = barn.amounts.get("grain", 0.0)
    make_report(
        world,
        EYE_FROM_HILL,
        "manor",
        manor.id,
        f"Книга сеньора: в амбаре зерна около {grain:.0f} — столько есть на раздачу.",
        {"barn_grain": round(grain, 1)},
        date,
        0,
        1.0,
        distorted=False,
        noise=0.0,
    )


def _report_settlement_grain(world: World, date: SimDate, rng) -> None:
    """Весть о ВСЁМ ПОСЕЛЕНИИ столицы: вот где слово «холм» честно (ADR 0212).

    **Почему это не глаз.** `docs/05_information.md:100-105` отдаёт глазу
    клетку: радиус 1 от `Manor.seat_tile_id` и `grain_approx` = зерно живых
    дворов клетки. Поселение больше клетки — на `v0_barony_100` это 90 дворов
    на восьми кварталах против 12 на клетке зала, и крайние кварталы стоят в
    трёх гексах от зала, то есть за пределами глаза. Точное число всего
    поселения у глаза не существует; выдать его — значит либо соврать, либо
    сломать закон о радиусе. Значит «холм» получает другой канал.

    **Канал — `messenger`, потому что шериф уже считает поселения.**
    `_report_messenger` несёт `departed_households` и `left_by_settlement` по
    поселениям баронства: шериф — должностное лицо, а не глаз, и месячная
    задержка ему положена. Числа гонца сдвигаются потоком `rng_news`
    (`docs/05_information.md:72`), дрейф берётся в пределах шума канала
    (`docs/05_information.md:70`, строка `messenger`: `noise` 0.15) — тот же
    приём, что у соседа (±30 % при шуме 0.3) и обоза (±40 % при 0.4).
    `confidence` 0.5, `noise` 0.15, задержка 1 — закон канала, свои числа
    весть не выдумывает.

    **Ключ факта — `settlement_grain_approx`, а не `grain_approx`.**
    `grain_approx` — имя, которое `docs/05:104-105` закрепляет за точной
    глазной величиной; слух о поселении под этим именем сделал бы глазную
    величину неразличимой от шерифской.

    **Амбар поселения в сумму не входит.** `_grain_in_households` читает
    только дворы, а `settlement:hill_court` — это тот же сток, что
    `Manor.stock_id` (`engine/manor.py`), и он уже доложен отдельной вестью
    `_report_manor_barn`. Считать его здесь — значит задвоить книгу корня.

    **Всеведение не открывается (И-3).** В `facts` лежит одно плоское число;
    ни id дворов, ни ссылок на стоки игрок не получает. `subject_kind`
    добавляет значение в словарь вида предмета — не сущность: `Settlement`
    уже есть в `docs/03_ontology.md`, а `id` поселения игрок и так видит в
    `left_by_settlement` того же отчёта шерифа.
    """
    settlement = world.settlements.get(world.player.court_settlement_id)
    if settlement is None:
        return
    grain = _grain_in_households(world, settlement)
    reported = grain * (1.0 + rng.uniform(-0.15, 0.15))
    make_report(
        world,
        MESSENGER,
        "settlement",
        settlement.id,
        f"Шериф: в поселении «{settlement.name}» зерна, по его словам, "
        f"около {reported:.0f}.",
        {"settlement_grain_approx": round(reported, 1)},
        date,
        1,
        0.5,
        distorted=abs(reported - grain) > 1e-9,
        noise=0.15,
    )


def make_month_reports(world: World) -> None:
    """Породить известия месяца; истина места игроку не отдаётся."""
    date = world.clock.date
    month = world.clock.month
    rng = world.rng.news
    court_tile = _court_tile(world)

    _report_court(world, date, court_tile)
    _report_manor_barn(world, date)
    _report_settlement_grain(world, date, rng)

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


def left_by_settlement(world: World) -> dict[str, int]:
    """Сколько дворов ушло из каждого поселения баронства.

    Источник — `Settlement.household_ids` × `Household.left_at`: ушедший двор
    остаётся в списке своего поселения навсегда (`docs/07_legal.md`: ушедший двор
    обратно не возвращается), поэтому число монотонно и не требует ни счётчика, ни
    нового поля. Племенное поселение не считается: племя не уходит со своей земли
    (ADR 0064, ADR 0184 п. 3) и проигрышем держателя не является.
    """
    out: dict[str, int] = {}
    for settlement in barony_settlements(world):
        count = left_count(world, settlement)
        if count > 0:
            out[settlement.id] = count
    return out


def _left_phrase(world: World, left_reported: dict[str, int]) -> str:
    """Разбивка уходов **по названиям поселений**, а не по сумме.

    Замер стола плейтеста: весть читалась как «ушло с земли, по его словам, 3»,
    сумма по всему баронству, а проигрыш считается `engine/ruin.py` **по одному
    поселению**. Игрок физически не мог связать «по баронству ушло 3» с «умерла
    деревня у ясеня»: в тексте не было ни слова о том, ОТКУДА ушли. Разбивка
    `left_by_settlement` лежала в `facts` и в `content` не попадала, а
    `runner.py` печатает только `content` — то есть правильная правка была в
    файле, а видел игрок пустоту (ADR 0215 §Дыра 5).

    Названия берутся у `Settlement.name`, а не у id: `hill_court` игроку ничего не
    говорит, «деревня у ясеня» говорит всё. Порядок — по id поселения, а не по
    размеру счёта, чтобы текст не прыгал от месяца к месяцу.

    **Правдивая часть — про то, что в вести есть.** Дрейф ±1 применён к разбивке
    и к итогу одним броском (ниже), поэтому итог равен сумме названных чисел, и
    весть не противоречит сама себе. Нулевое поселение в список не попадает:
    «деревня X — 0» кажется отчётом о деревне, из которой ничего не ушло, и
    только засоряет строку.

    **Точка ставится после разбивки, а не перед ней** (правка приёмки ADR 0215).
    Фрагмент начинается с `"; по поселениям: "` и точкой не заканчивается: точку
    ставит вызывающий, уже после `{breakdown}`. Стояла она раньше — и игрок читал
    `ушло с земли, по его словам, 4.; по поселениям: Деревня у ясеня — 4`, то
    есть точку с запятой в одном месте текста, который игрок читает каждый месяц.
    """
    if not left_reported:
        return ""
    parts = [
        f"{world.settlements[settlement_id].name} — {count}"
        for settlement_id, count in sorted(left_reported.items())
        if settlement_id in world.settlements
    ]
    if not parts:
        return ""
    return "; по поселениям: " + ", ".join(parts)


def _report_messenger(world: World, date: SimDate, court_tile: str, rng) -> None:
    """Шериф: недоимка и уходы дворов — оба числа в одном отчёте, одним глотком.

    **Зачем в весть уходы (ADR 0209).** Игрок обязан узнавать об уходе дворов **по
    ходу**, а не постфактум. Постфактум он узнавал бы из отчёта о прибытии ушедшего
    двора к соседу (`hazards/travel.py::resolve_migrations`), то есть через год и
    словами о чужой клетке. Счётчик ушедших дворов в ежемесячном отчёте шерифа —
    это и есть «по ходу»: число растёт на глазах, и до порога игрок видит, как
    деревня пустеет.

    Один отчёт, а не отдельная весть на каждый уход: на `v0_shire` в месяц уходит
    единицы дворов из 32, а поселений 17 — семнадцать отдельных строк в месяц были бы
    шумом, который игрок перестал бы читать. Пороговое событие («поселение ОСТАВЛЕНО»)
    рождается отдельной вестью, потому что оно редкое и по определению важное
    (`news/ruin.py`).

    **Искажение — то же, что у недоимки, и одним броском на весь отчёт.** Дрейф ±1
    применяется и к итогу, и к каждому числу в разбивке, иначе отчёт сам себе
    противоречил бы («ушло 3», а в списке 2 + 1 + 0). Истина мира в `facts` не
    попадает: только плоские целые, как у `arrears_households` (И-3).
    """
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
    left_true = left_by_settlement(world)
    left_reported = {
        settlement_id: max(0, count + drift)
        for settlement_id, count in sorted(left_true.items())
    }
    departed = sum(left_reported.values())
    breakdown = _left_phrase(world, left_reported)
    make_report(
        world,
        MESSENGER,
        "barony",
        "barony",
        f"Шериф: дворов с недоимкой, по слухам, {reported}; ушло с земли, "
        f"по его словам, {departed}{breakdown}.",
        {
            "arrears_households": reported,
            "departed_households": departed,
            "left_by_settlement": left_reported,
        },
        date,
        1,
        0.5,
        distorted=reported != arrears_households or departed != sum(left_true.values()),
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

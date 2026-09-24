"""Место стола усадьбы холма: клетки, глаз, задержка приказа, мир.

Назначение: Manor — книга земли, а это — МЕСТО стола: клетки усадьбы
(зал / двор замка / ближайшие поля под холмом). С холма держатель ВИДИТ
соседние клетки сам, без почты. Рядом быстрее реакция (выслать людей
на кромку) и крепче мир (hazard слабее). Дальний выселок живёт как раньше:
только Report.

Правила:
- `seat_tiles` корня = клетка зала + ортогональные соседи, существующие
  в мире. У тэна место пусто.
- `eye_tiles` = клетки в `eye_range_tiles` (манхэттен 1) от зала: о них
  каждый месяц рождается Report `eye_from_hill` с задержкой 0,
  `confidence` 1.0, `noise` 0.0, content «видно с холма». Дальняя соль
  глазом не видна — только `caravan`/`messenger` («узнали письмом»).
  Почта дальних не ломается: глаз не читает Tile/Stock напрямую, только
  зерно живых дворов клетки как рассказ (И-3).
- `order_delay_months` = 0 мес на клетку мира/глаза, иначе 1 мес.
  `send_sally` — высылка на СОСЕДНЮЮ клетку (без pathfinding): с холма
  на кромку eta в том же месяце, дальняя высылка — в следующем.
  Материю не двигает (люди без груза), труд снимает как `send_party`.
- `peace_theft_factor` = 0.5 на клетке мира, иначе 1.0. ОДНО правило:
  волчья кража зерна слабее рядом с усадьбой (шум двора, близкий высых).
  Всход реже, гашение быстрее, иммунитет — НЕ делаем. Урожая место
  не даёт: ни grain, ни yield, ни rent_share.

Проверка: `sim/tests/test_seat_place.py`; `bash sim/run_tests.sh`.
"""

from __future__ import annotations

from .hexgrid import (
    axial_distance,
    axial_is_neighbor,
    neighbor_ids,
    offset_to_axial,
    tile_id_at,
)


EYE_RANGE_TILES = 1
PEACE_RANGE_TILES = 1
PEACE_THEFT_FACTOR = 0.5

EYE_SOURCE = "eye_from_hill"
MESSENGER_SOURCE = "messenger"


def _tile_id(x: int, y: int) -> str:
    """id клетки в формате сценария."""
    return f"t_{x:02d}_{y:02d}"


def _parse_tile_id(tile_id: str) -> tuple[int, int] | None:
    """Аксиальные координаты клетки из id `t_XX_YY`; None — чужой формат.

    Id хранит координаты карты (`col`/`row`), а сравнения идут по аксиальным
    `Tile.coord`, поэтому здесь перевод через `offset_to_axial` (ADR 0071).
    """
    parsed = _parse_offset(tile_id)
    if parsed is None:
        return None
    return offset_to_axial(*parsed)


def _parse_offset(tile_id: str) -> tuple[int, int] | None:
    """Координаты карты (`col`/`row`) из id `t_XX_YY`; None — чужой формат."""
    try:
        _, xs, ys = tile_id.split("_")
        return int(xs), int(ys)
    except (ValueError, AttributeError):
        return None


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Шаги по гексу между аксиальными координатами (ADR 0071).

    Имя оставлено прежним (его знают вести/тесты), смысл — гекс-расстояние:
    на квадрате это был манхэттен, теперь аксиальная метрика соседства.
    """
    return axial_distance(a, b)


def court_tile_id(world) -> str | None:
    """Клетка зала: координата `hill_court`."""
    court = world.settlements.get(world.player.court_settlement_id)
    if court is None:
        return None
    return tile_id_at(int(court.coord[0]), int(court.coord[1]))


def root_manor(world):
    """Корневой манор игрока или None."""
    if world.player_manor_id is None:
        return None
    return world.manors.get(world.player_manor_id)


def compute_seat_tiles(world, court_tile: str) -> list[str]:
    """Клетки усадьбы: зал + соседи по гексу, существующие в мире (ADR 0071)."""
    tile = world.tiles.get(court_tile)
    if tile is None:
        return []
    found = [court_tile]
    for neighbour in neighbor_ids(world, tile):
        if neighbour not in found:
            found.append(neighbour)
    return sorted(found)


def init_seat_for_root(world) -> list[str]:
    """Заполнить место стола корня: зал, клетки, радиусы. Вернуть seat_tiles.

    Вызывается из сборки сценария. У тэнов место пусто. Радиусы — место
    и радиус (дополнение, не магия): глаз 1, мир 1.
    """
    manor = root_manor(world)
    if manor is None:
        return []
    court = court_tile_id(world)
    if court is None:
        return []
    manor.seat_tile_id = court
    manor.seat_tiles = compute_seat_tiles(world, court)
    manor.eye_range_tiles = EYE_RANGE_TILES
    manor.peace_range_tiles = PEACE_RANGE_TILES
    return list(manor.seat_tiles)


def _seat_center(world) -> tuple[int, int] | None:
    """Координата зала корня; None — места нет."""
    manor = root_manor(world)
    if manor is None:
        return None
    center_id = manor.seat_tile_id or court_tile_id(world)
    if not center_id:
        return None
    return _parse_tile_id(center_id)


def _tiles_within(world, center: tuple[int, int], radius: int) -> list[str]:
    """Клетки мира в гекс-радиусе от центра, по id (ADR 0071)."""
    found: list[str] = []
    for tile_id, tile in world.tiles.items():
        if axial_distance(tile.coord, center) <= radius:
            found.append(tile_id)
    return sorted(found)


def eye_tiles(world) -> list[str]:
    """Клетки, видимые с холма без вестника (глаз). Только корень."""
    manor = root_manor(world)
    if manor is None:
        return []
    center = _parse_tile_id(manor.seat_tile_id) if manor.seat_tile_id else _seat_center(world)
    if center is None:
        return []
    radius = int(getattr(manor, "eye_range_tiles", EYE_RANGE_TILES))
    return _tiles_within(world, center, radius)


def peace_tiles(world) -> list[str]:
    """Клетки мира усадьбы (закон и близость): hazard здесь слабее."""
    manor = root_manor(world)
    if manor is None:
        return []
    center = _parse_tile_id(manor.seat_tile_id) if manor.seat_tile_id else _seat_center(world)
    if center is None:
        return []
    radius = int(getattr(manor, "peace_range_tiles", PEACE_RANGE_TILES))
    return _tiles_within(world, center, radius)


def is_eye_tile(world, tile_id: str) -> bool:
    """Видна ли клетка с холма без почты."""
    return tile_id in set(eye_tiles(world))


def is_peace_tile(world, tile_id: str) -> bool:
    """Входит ли клетка в мир усадьбы."""
    return tile_id in set(peace_tiles(world))


def peace_theft_factor(world, tile_id: str) -> float:
    """Множитель волчьей кражи: 0.5 в мире усадьбы, иначе 1.0.

    Единственное правило мира: слабее, не реже и не иммунитет.
    """
    return PEACE_THEFT_FACTOR if is_peace_tile(world, tile_id) else 1.0


def order_delay_months(world, origin_tile_id: str, destination_tile_id: str) -> int:
    """Задержка приказа в месяцах: 0 на клетку глаза/мира, иначе 1.

    С холма на соседнюю клетку короче, чем на дальнюю. Считается по месту
    назначения, не по отправителю: кромка рядом со столом быстрая.
    """
    if is_eye_tile(world, destination_tile_id) or is_peace_tile(world, destination_tile_id):
        return 0
    return 1


def _add_months(date, months: int, months_per_year: int):
    """Сдвинуть дату на N месяцев вперёд."""
    result = date
    for _ in range(max(0, months)):
        result = result.advance(months_per_year)
    return result


def _adjacent(origin: str, destination: str) -> bool:
    """Соседние ли клетки по гекс-сетке (без pathfinding, ADR 0071)."""
    a = _parse_tile_id(origin)
    b = _parse_tile_id(destination)
    if a is None or b is None:
        return False
    return axial_is_neighbor(a, b)


def _grain_on_tile(world, tile_id: str) -> float:
    """Зерно живых дворов клетки (рассказ, не Tile.state)."""
    total = 0.0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if household.current_tile_id != tile_id:
            continue
        total += world.get_stock(household.stock_id).amounts.get("grain", 0.0)
    return total


def _hazard_on_tile(world, tile_id: str):
    """Активная угроза клетки для глаза (вид, не сила истины)."""
    best = None
    for hazard in world.hazards.values():
        if not hazard.active or hazard.tile_id != tile_id:
            continue
        if best is None or (hazard.population * hazard.intensity) > (
            best.population * best.intensity
        ):
            best = hazard
    return best


def make_seat_eye_reports(world) -> list:
    """Родить глазные Report о соседних клетках: «видно с холма».

    Пропускает клетку зала (о ней уже докладывает `info.briefing`).
    RNG не трогает: глаз точен, детерминизм и почта дальних целы.
    Материю не трогает: только Report.
    """
    from ..news.propagation import make_report

    date = world.clock.date
    court = court_tile_id(world)
    produced = []
    for tile_id in eye_tiles(world):
        if tile_id == court:
            continue
        grain = _grain_on_tile(world, tile_id)
        hazard = _hazard_on_tile(world, tile_id)
        facts: dict = {"grain_approx": round(grain, 1)}
        if hazard is not None:
            facts["hazard"] = {"kind": hazard.kind}
        if hazard is not None:
            content = (
                f"Видно с холма: на клетке '{tile_id}' зерна около {grain:.0f}, "
                f"в кромке '{hazard.kind}'."
            )
        else:
            content = f"Видно с холма: на клетке '{tile_id}' зерна около {grain:.0f}."
        produced.append(
            make_report(
                world,
                EYE_SOURCE,
                "tile",
                tile_id,
                content,
                facts,
                date,
                0,
                1.0,
                distorted=False,
                noise=0.0,
            )
        )
    return produced


def send_sally(world, household_id: str, member_ids: list[str], destination_tile_id: str):
    """Выслать людей двора на СОСЕДНЮЮ клетку (кромка без pathfinding).

    Задержка — `order_delay_months`: с холма на соседнюю (мир/глаз) eta
    в том же месяце (0), дальняя высылка — в следующем (1). Разбор похода —
    существующий `hazards.travel.resolve_packs`. Весть: рядом — глаз
    («видно с холма», задержка 0), вдали — гонец («узнали письмом»,
    задержка 1). В лог приказов пишется всегда.
    """
    from ..news.propagation import make_report
    from ..ontology import Pack, Stock

    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    from ..legal.regimes import can_be_sent

    if not can_be_sent(world, household):
        raise PermissionError(
            f"Двор '{household_id}' ({household.legal_status_id}) нельзя послать"
        )
    origin = household.current_tile_id
    if destination_tile_id not in world.tiles:
        raise ValueError(f"Нет клетки '{destination_tile_id}'")
    if destination_tile_id == origin:
        raise ValueError("Цель высылки — своя же клетка: посылать некого")
    if not _adjacent(origin, destination_tile_id):
        raise ValueError(
            f"Клетка '{destination_tile_id}' не соседняя с '{origin}' "
            f"(pathfinding запрещён)"
        )
    adults = {
        pid
        for pid in household.member_ids
        if world.persons.get(pid) is not None and world.persons[pid].age_class == "adult"
    }
    going = [pid for pid in member_ids if pid in adults]
    if not going:
        raise ValueError(f"У двора '{household_id}' не выбрано ни одного взрослого")

    delay = order_delay_months(world, origin, destination_tile_id)
    date = world.clock.date
    number = len(world.packs) + 1
    while f"pack_{number:04d}" in world.packs or f"pack:{number:04d}" in world.stocks:
        number += 1
    cargo = Stock(
        id=f"pack:{number:04d}",
        owner_kind="pack",
        owner_id=f"pack_{number:04d}",
        amounts={},
    )
    world.add_stock(cargo)
    pack = Pack(
        id=f"pack_{number:04d}",
        kind="party",
        origin_tile_id=origin,
        destination_tile_id=destination_tile_id,
        route=[origin, destination_tile_id],
        member_ids=going,
        cargo=cargo,
        departed_date=date,
        eta_date=_add_months(date, delay, world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household_id,
    )
    world.packs[pack.id] = pack
    for pid in going:
        household.member_ids.remove(pid)
        world.persons[pid].location_tile_id = destination_tile_id
    household.labor_days = max(0.0, household.labor_days - 20.0 * len(going))
    record = {
        "date": str(world.clock.date),
        "month": world.clock.month,
        "action": "send_sally",
        "household": household_id,
        "destination": destination_tile_id,
        "members": list(going),
        "pack": pack.id,
        "delay_months": delay,
    }
    world.player_actions.append(record)
    if delay == 0:
        make_report(
            world,
            EYE_SOURCE,
            "pack",
            pack.id,
            f"Видно с холма: выслали {len(going)} на кромку '{destination_tile_id}'",
            {"destination": destination_tile_id, "members": list(going)},
            date,
            0,
            1.0,
            distorted=False,
            noise=0.0,
        )
    else:
        make_report(
            world,
            MESSENGER_SOURCE,
            "pack",
            pack.id,
            f"Узнали письмом: выслали {len(going)} вдаль на '{destination_tile_id}'",
            {"destination": destination_tile_id, "members": list(going)},
            date,
            1,
            0.5,
            distorted=False,
            noise=0.15,
        )
    return pack

"""Headless-прогон сценария и сводка для игрока.

Лог прогона показывает: запасы замка, число живых дворов, сколько ренты собрано,
сколько раз дворы ходили на соседнюю клетку и сколько людей ушло и не вернулось.
Это состояние мира для отладки; игрок по-прежнему видит только `Report`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .engine.manor import (
    call_boon,
    ease_week_work,
    grant_thegn,
    grant_tenement,
    grant_tool,
    revoke_thegn,
    set_tile_regime,
)
from .engine.march import send_march
from .engine.river import send_river_pack
from .engine.tick import run_month
from .engine.tile_view import dump_tile_views, format_tile_view
from .legal.actions import add_obligation, grant_tenure, send_party
from .news.views import build_player_view
from .ontology import SimDate
from .scenario import load_scenario


@dataclass
class MonthSnapshot:
    """Сводка одного месяца: запасы замка, живые дворы, рента, уход людей."""

    date: str
    court_grain: float
    court_salt: float
    living_households: int
    rent_collected: float
    households_left: int
    persons_left: int
    travel_events: int
    hunger_months: int


@dataclass
class RunResult:
    """Итог прогона: дата, известия, ушедшие дворы и баланс материи."""

    months: int
    final_date: SimDate
    reports_delivered: int
    households_left: int
    persons_left: int
    living_households: int
    rent_collected: float
    hunger_months: int
    travel_events: int
    castle_stores: dict[str, float]
    total_matter: float
    matter_delta: float
    state_hash: str
    monthly: list[MonthSnapshot] = field(default_factory=list)
    player_actions: list[dict] = field(default_factory=list)


def _as_id_list(value) -> list[str]:
    """Привести список id из YAML к строкам."""
    return [str(item) for item in value]


def _dispatch_grant_thegn(world, entry: dict) -> None:
    grant_thegn(
        world,
        str(entry["person"]),
        _as_id_list(entry["tiles"]),
        _as_id_list(entry["households"]),
    )


def _dispatch_revoke_thegn(world, entry: dict) -> None:
    revoke_thegn(world, str(entry["manor"]))


def _dispatch_grant_tenement(world, entry: dict) -> None:
    grant_tenement(world, str(entry["household"]), _as_id_list(entry["tiles"]))


def _dispatch_set_tile_regime(world, entry: dict) -> None:
    set_tile_regime(world, str(entry["tile"]), str(entry["regime"]))


def _dispatch_grant_tool(world, entry: dict) -> None:
    grant_tool(
        world,
        str(entry["household"]),
        str(entry["good"]),
        float(entry["amount"]),
    )


def _dispatch_call_boon(world, entry: dict) -> None:
    call_boon(world, int(entry["month"]))


def _dispatch_ease_week_work(world, entry: dict) -> None:
    ease_week_work(world, int(entry["month"]), float(entry["delta"]))


def _dispatch_grant_tenure(world, entry: dict) -> None:
    grant_tenure(
        world,
        str(entry["household"]),
        str(entry["tile"]),
        kind=str(entry.get("kind", "tenure")),
        rent_share=float(entry.get("rent_share", 0.1)),
    )


def _dispatch_add_obligation(world, entry: dict) -> None:
    add_obligation(
        world,
        str(entry["household"]),
        str(entry["kind"]),
        entry.get("due_good"),
        float(entry["due_amount"]),
        period_months=int(entry.get("period_months", 1)),
        basis=str(entry.get("basis", "share")),
        corvee_days=float(entry.get("corvee_days", 0.0)),
        right_id=entry.get("right_id"),
    )


def _dispatch_send_party(world, entry: dict) -> None:
    send_party(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
    )


def _dispatch_send_march(world, entry: dict) -> None:
    send_march(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
        str(entry.get("profile", "foot")),
    )


def _dispatch_send_river(world, entry: dict) -> None:
    send_river_pack(
        world,
        str(entry["household"]),
        _as_id_list(entry.get("members", [])),
        str(entry["destination"]),
        str(entry.get("profile", "water_raft")),
        dict(entry.get("cargo") or {}),
    )


def _dispatch_send_sally(world, entry: dict) -> None:
    from .engine.seat import send_sally

    send_sally(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
    )


def _dispatch_work_road(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "road")


def _dispatch_work_ford(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "ford")


def _dispatch_work_bridge(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "bridge")


ACTION_HANDLERS = {
    "grant_thegn": _dispatch_grant_thegn,
    "revoke_thegn": _dispatch_revoke_thegn,
    "grant_tenement": _dispatch_grant_tenement,
    "set_tile_regime": _dispatch_set_tile_regime,
    "grant_tool": _dispatch_grant_tool,
    "call_boon": _dispatch_call_boon,
    "ease_week_work": _dispatch_ease_week_work,
    "grant_tenure": _dispatch_grant_tenure,
    "add_obligation": _dispatch_add_obligation,
    "send_party": _dispatch_send_party,
    "send_march": _dispatch_send_march,
    "send_river": _dispatch_send_river,
    "send_sally": _dispatch_send_sally,
    "work_road": _dispatch_work_road,
    "work_ford": _dispatch_work_ford,
    "work_bridge": _dispatch_work_bridge,
}


def _apply_script_entry(world, entry: dict) -> list[dict]:
    """Выполнить одну запись `script:` и вернуть её запись в лог действий.

    Действия манора сами пишут в `World.player_actions`; действия права —
    нет, поэтому для них синтезируется запись. Неизвестное действие —
    `ValueError` с датой и именем.
    """
    action = str(entry.get("action") or "")
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"{world.clock.date}: неизвестное действие '{action}'")
    before = len(world.player_actions)
    handler(world, entry)
    produced = world.player_actions[before:]
    if produced:
        return list(produced)
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update({k: v for k, v in entry.items() if k not in ("at_month", "action")})
    world.player_actions.append(record)
    return [record]


def _format_player_action(record: dict) -> str:
    """Строка лога действия игрока: `[Y1-M06] grant_thegn person=...`."""
    fields = " ".join(
        f"{key}={record[key]}"
        for key in sorted(record)
        if key not in ("date", "month", "action")
    )
    return f"[{record.get('date', '?')}] {record.get('action', '?')} {fields}".rstrip()


def _castle_stores(world) -> dict[str, float]:
    court = world.settlements.get(world.player.court_settlement_id)
    if court is None:
        return {}
    return dict(world.get_stock(court.stores_stock_id).amounts)


def load_actions_fixture(path: str | Path) -> list[dict]:
    """Загрузить внешнюю фикстуру действий игрока (B1).

    YAML — список записей `script:` (`at_month`, `action`, поля рычага)
    либо словарь с ключом `actions`. Исполняются существующими
    обработчиками `ACTION_HANDLERS`; новой механики здесь нет.
    """
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("actions", [])
    if not isinstance(data, list):
        raise ValueError(f"Фикстура '{path}': ожидался список записей")
    return [dict(entry) for entry in data]


def run(
    scenario_path: str | Path,
    months: int,
    seed: int | None = None,
    print_log: bool = False,
    actions: list[dict] | None = None,
) -> RunResult:
    """Прогнать сценарий N месяцев и вернуть сводку.

    `seed` уходит в `load_scenario`, поэтому мир (черты людей и потоки RNG)
    целиком собран из одного seed. Перед i-м вызовом `run_month` (1-based)
    выполняются записи `script:` с `at_month == i`, затем — записи внешней
    фикстуры `actions` с тем же месяцем (B1). Каждая исполненная запись
    пишется в `world.player_actions` с датой (`_apply_script_entry`).
    """
    world = load_scenario(scenario_path, seed=seed)
    script = list(world.script) + [dict(entry) for entry in actions or []]

    player_actions: list[dict] = []
    monthly: list[MonthSnapshot] = []
    for month_index in range(1, months + 1):
        for entry in script:
            if int(entry.get("at_month", 0)) != month_index:
                continue
            records = _apply_script_entry(world, entry)
            player_actions.extend(records)
            if print_log:
                for record in records:
                    print(_format_player_action(record))
        start = str(world.clock.date)
        run_month(world)
        stores = _castle_stores(world)
        snapshot = MonthSnapshot(
            date=start,
            court_grain=stores.get("grain", 0.0),
            court_salt=stores.get("salt", 0.0),
            living_households=sum(
                1 for hh in world.households.values() if hh.left_at is None
            ),
            rent_collected=sum(o.paid_total for o in world.obligations.values()),
            households_left=int(world.stats.get("households_left", 0)),
            persons_left=int(world.stats.get("persons_left", 0)),
            travel_events=int(world.stats.get("travel_events", 0)),
            hunger_months=int(world.stats.get("hunger_months", 0)),
        )
        monthly.append(snapshot)
        if print_log:
            print(
                f"[{snapshot.date}] живых дворов {snapshot.living_households}, "
                f"замок зерно {snapshot.court_grain:.1f} соль {snapshot.court_salt:.1f}, "
                f"ренты собрано {snapshot.rent_collected:.1f}, "
                f"ходок на соседнюю клетку {snapshot.travel_events}, "
                f"ушло дворов {snapshot.households_left} "
                f"(людей {snapshot.persons_left}), "
                f"голодных месяцев {snapshot.hunger_months}"
            )

    final_date = world.clock.date
    view = build_player_view(world, final_date)
    if print_log:
        for entry in view.entries:
            marker = "?" if entry.distorted else " "
            stale = " [устарело]" if entry.stale else ""
            print(
                f"  [{entry.delivery_date}] ({entry.source}){marker} "
                f"{entry.content}{stale}"
            )
        print("  Клетки (вид без RimWorld-слоя):")
        for dump in dump_tile_views(world):
            print(f"    {format_tile_view(dump)}")

    total = world.total_matter()
    return RunResult(
        months=months,
        final_date=final_date,
        reports_delivered=len(view.entries),
        households_left=int(world.stats.get("households_left", 0)),
        persons_left=int(world.stats.get("persons_left", 0)),
        living_households=sum(
            1 for hh in world.households.values() if hh.left_at is None
        ),
        rent_collected=sum(o.paid_total for o in world.obligations.values()),
        hunger_months=int(world.stats.get("hunger_months", 0)),
        travel_events=int(world.stats.get("travel_events", 0)),
        castle_stores=_castle_stores(world),
        total_matter=total,
        matter_delta=world.ledger.delta(total),
        state_hash=world.state_hash(),
        monthly=monthly,
        player_actions=player_actions,
    )


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы и напечатать русскую сводку прогона."""
    parser = argparse.ArgumentParser(description="Headless-прогон Hillcourt")
    parser.add_argument("--scenario", required=True, help="путь к YAML сценария")
    parser.add_argument("--months", type=int, default=12, help="число месяцев")
    parser.add_argument("--seed", type=int, default=None, help="переопределить seed")
    parser.add_argument(
        "--print-log", action="store_true", help="печатать месячный лог и известия"
    )
    parser.add_argument(
        "--actions",
        default=None,
        help="YAML-фикстура действий игрока (B1): список записей script:",
    )
    args = parser.parse_args(argv)

    fixture = load_actions_fixture(args.actions) if args.actions else None
    result = run(args.scenario, args.months, args.seed, args.print_log, fixture)
    for record in result.player_actions:
        print(_format_player_action(record))
    grain = result.castle_stores.get("grain", 0.0)
    salt = result.castle_stores.get("salt", 0.0)
    print(
        f"Сценарий пройден: {result.months} мес, дата {result.final_date}; "
        f"живых дворов {result.living_households}, ушло {result.households_left} "
        f"(людей {result.persons_left}); "
        f"замок: зерно {grain:.1f}, соль {salt:.1f}; "
        f"ренты собрано {result.rent_collected:.1f}; "
        f"голодных месяцев {result.hunger_months}; "
        f"ходок на соседнюю клетку {result.travel_events}; "
        f"известий {result.reports_delivered}; "
        f"материи {result.total_matter:.3f}, дельта {result.matter_delta:.6f}, "
        f"хеш {result.state_hash[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

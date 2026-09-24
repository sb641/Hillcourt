"""Мир симуляции: реестры сущностей, сумма материи и хеш состояния."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from .catalogs import Catalogs
from .engine.clock import Clock
from .ledger import Ledger
from .ontology import (
    Hazard,
    Household,
    Obligation,
    Pack,
    Person,
    Report,
    Settlement,
    Stock,
    Tile,
)


@dataclass
class World:
    """Контейнер всего состояния одного прогона сценария."""

    scenario_id: str
    seed: int
    clock: Clock
    tiles: dict[str, Tile]
    settlements: dict[str, Settlement]
    households: dict[str, Household]
    persons: dict[str, Person]
    stocks: dict[str, Stock]
    ledger: Ledger
    hazards: dict[str, Hazard]
    packs: dict[str, Pack]
    reports: list[Report]
    player: Any
    catalogs: Catalogs
    initial_matter: float
    obligations: dict[str, Obligation] = field(default_factory=dict)
    rng: Optional[Any] = None
    phase_log: list[tuple[str, list[str]]] = field(default_factory=list)
    needs: Optional[Any] = None
    household_actions: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=dict)
    manor: Optional[Any] = None
    manor_log: list[dict] = field(default_factory=list)
    seasons: dict[int, Any] = field(default_factory=dict)
    calendar: dict[int, Any] = field(default_factory=dict)
    rights: dict[str, Any] = field(default_factory=dict)
    manors: dict[str, Any] = field(default_factory=dict)
    player_manor_id: Optional[str] = None
    player_actions: list[dict] = field(default_factory=list)
    script: list[dict] = field(default_factory=list)
    barter_memory: dict[str, dict] = field(default_factory=dict)
    roadworks: dict = field(default_factory=dict)
    tribes: dict[str, Any] = field(default_factory=dict)
    month_events: list[dict] = field(default_factory=list)
    growth_tile_ids: tuple[str, ...] = ()

    def bump(self, key: str, amount: float = 1.0) -> None:
        """Увеличить счётчик статистики прогона (для сводки runner)."""
        self.stats[key] = self.stats.get(key, 0.0) + amount

    def total_matter(self) -> float:
        """Суммарная материя по всем зарегистрированным стокам, включая sink."""
        return float(sum(self.stocks[sid].total() for sid in sorted(self.stocks)))

    def state_hash(self) -> str:
        """Стабильный sha256 по дате, стокам и ключевым полям дворов."""
        parts = [str(self.clock.date)]
        for sid in sorted(self.stocks):
            amounts = sorted(self.stocks[sid].amounts.items())
            parts.append(f"{sid}={amounts!r}")
        for hid in sorted(self.households):
            hh = self.households[hid]
            parts.append(
                f"{hid}|{hh.current_tile_id}|{hh.intent}|{hh.mood:.6f}|"
                f"{hh.hunger_days}|{hh.arrears_days}|{hh.left_at}|"
                f"{hh.labor_days:.6f}|{hh.main_action}|{hh.minor_action}|"
                f"{hh.adventurism:.6f}|{hh.rumor_fear:.6f}|{hh.tool_wear:.6f}|"
                f"{hh.legal_status_id}|{hh.personal_status}|{hh.land_relation}|"
                f"{hh.obligation_bundle}|{hh.manor_id}|"
                f"{hh.food_streak}|{hh.birth_count}"
            )
        for tid in sorted(self.tiles):
            tile = self.tiles[tid]
            parts.append(
                f"tile|{tid}|{sorted(tile.hazard_ids)}|{tile.regime_id}|"
                f"{tile.trail_wear:.6f}|{tile.coord!r}"
            )
        for pack_id in sorted(self.packs):
            pack = self.packs[pack_id]
            parts.append(
                f"pack|{pack_id}|{pack.status}|{sorted(pack.member_ids)}|{pack.eta_date}|"
                f"{pack.lost_date}"
            )
        for pid in sorted(self.persons):
            parts.append(
                f"person|{pid}|{self.persons[pid].age_class}|"
                f"{self.persons[pid].age_months}|{self.persons[pid].health:.6f}"
            )
        for oid in sorted(self.obligations):
            ob = self.obligations[oid]
            parts.append(
                f"obl|{oid}|{ob.paid_total:.6f}|{ob.arrears:.6f}|"
                f"{ob.corvee_days:.6f}|{ob.duty_days:.6f}"
            )
        for hid in sorted(self.hazards):
            hz = self.hazards[hid]
            parts.append(
                f"hazard|{hid}|{hz.population:.6f}|{hz.satiety:.6f}|{hz.active}"
            )
        for rid in sorted(self.rights):
            right = self.rights[rid]
            parts.append(
                f"right|{rid}|{right.holder_household_id}|{right.tile_id}|"
                f"{right.kind}|{right.rent_share:.6f}"
            )
        for manor_id in sorted(self.manors):
            manor = self.manors[manor_id]
            parts.append(
                f"manor|{manor_id}|{manor.holder_person_id}|{manor.stock_id}|"
                f"{manor.parent_manor_id}|{manor.upward_bundle}|{sorted(manor.tile_ids)}|"
                f"{sorted(manor.tile_regimes.items())}|"
                f"{sorted(manor.household_ids)}|{sorted(manor.grant_ids)}|"
                f"{manor.demesne_labor_demand_this_month:.6f}|"
                f"{manor.demesne_labor_filled:.6f}|{manor.mustered}|"
                f"{manor.prior_preset}|{manor.service_kits_required:.6f}|"
                f"{manor.service_men_required:.6f}|{manor.service_kits_held:.6f}|"
                f"{manor.service_men_held:.6f}|"
                f"{manor.service_met}|{manor.service_gap:.6f}|"
                f"{manor.bad_service_months}|{manor.iron_requested}|"
                f"{getattr(manor, 'seat_tile_id', '')}|"
                f"{sorted(getattr(manor, 'seat_tiles', []) or [])}|"
                f"{getattr(manor, 'eye_range_tiles', 1)}|"
                f"{getattr(manor, 'peace_range_tiles', 1)}"
            )
        for action in self.player_actions:
            parts.append(
                f"act|{action.get('date')}|{action.get('action')}|"
                f"{sorted((k, str(v)) for k, v in action.items())}"
            )
        for key in sorted(self.barter_memory):
            memory = self.barter_memory[key]
            parts.append(
                f"barter|{key}|{sorted((k, str(v)) for k, v in memory.items())}"
            )
        for key in sorted(self.stats):
            parts.append(f"stat|{key}|{self.stats[key]:.6f}")
        for tribe_id in sorted(getattr(self, "tribes", {})):
            tribe = self.tribes[tribe_id]
            parts.append(
                f"tribe|{tribe_id}|{tribe.name}|{tribe.stance}|{tribe.settlement_id}|"
                f"{tribe.tribute_grain:.6f}|{tribe.muster_kits:.6f}"
            )
        for report in self.reports:
            parts.append(f"rep|{report.id}|{report.delivery_date}")
        for tid in sorted(self.tiles):
            tile = self.tiles[tid]
            if getattr(tile, "road", False) or getattr(tile, "ford", False) or getattr(
                tile, "bridge", False
            ):
                parts.append(
                    f"way|{tid}|{int(bool(tile.road))}|{int(bool(tile.ford))}|"
                    f"{int(bool(tile.bridge))}"
                )
        for tid in sorted(getattr(self, "roadworks", {}) or {}):
            work = self.roadworks[tid]
            parts.append(
                f"roadwork|{tid}|{work.get('kind')}|"
                f"{float(work.get('done_days', 0.0)):.6f}|"
                f"{float(work.get('required_days', 0.0)):.6f}|{work.get('status')}"
            )
        blob = "\n".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def add_stock(self, stock: Stock) -> None:
        """Зарегистрировать сток в мире по его id."""
        self.stocks[stock.id] = stock

    def get_stock(self, stock_id: str) -> Stock:
        """Получить сток по id или упасть с понятной ошибкой."""
        try:
            return self.stocks[stock_id]
        except KeyError as exc:
            raise KeyError(f"Сток '{stock_id}' не зарегистрирован в мире") from exc

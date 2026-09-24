"""Календарь работ v0: сезонная барщина, помоги (bene), гэфоль-пахота.

Год = 12 месячных тиков. Rectitudines свёрнуты в месячные коэффициенты
(`design/catalogs/calendar_v0.yml`); дневного календаря святых в коде нет.
Перевод недель в месяц тика один: `month_days = week_days * 4` (WEEKS_PER_MONTH).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..ontology import CalendarMonth
from ..world import World
from .regimes import preset_of

WEEKS_PER_MONTH = 4.0
CALLOUT_DAYS = 4.0
SLAVE_MONTH_DAYS = 20.0
BOON_USED_KEY = "boon_used_this_year"
BOON_YEAR_KEY = "boon_used_year"
SEED_GRAIN_PER_ACRE = 1.0  # seed; экономист вправе переопределить

VALID_SEASON = {"winter", "plough", "hay", "harvest", "sow_winter"}
VALID_WORK = {"plough", "weed", "hay", "harvest", "thresh", "wood_flock", "idle_court"}
VALID_PRESET_MODS = {"villein", "cotter", "geneat", "sokeman", "slave", "free_landless"}


def load_calendar(path: str | Path) -> dict[int, CalendarMonth]:
    """Прочитать calendar_v0.yml в словарь {номер месяца: CalendarMonth}."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"calendar_v0.yml '{path}': ожидался словарь")
    unknown = sorted(set(data) - {"version", "calendar", "months"})
    if unknown:
        raise ValueError(f"calendar_v0.yml '{path}': неизвестные разделы {unknown}")
    out: dict[int, CalendarMonth] = {}
    for record in data.get("months", []):
        month = int(record["month"])
        if month in out:
            raise ValueError(f"calendar_v0.yml: месяц {month} повторяется")
        season = record["season"]
        work = record["demesne_work"]
        if season not in VALID_SEASON:
            raise ValueError(f"Месяц {month}: лишний season '{season}'")
        if work not in VALID_WORK:
            raise ValueError(f"Месяц {month}: лишний demesne_work '{work}'")
        labor_mod = dict(record.get("labor_mod", {}))
        bad = sorted(set(labor_mod) - VALID_PRESET_MODS)
        if bad:
            raise ValueError(f"Месяц {month}: лишние пресеты в labor_mod {bad}")
        out[month] = CalendarMonth(
            month=month,
            season=season,
            demesne_work=work,
            labor_mod=labor_mod,
            boon_allowed=bool(record.get("boon_allowed", False)),
            sow_demesne_acres=bool(record.get("sow_demesne_acres", False)),
        )
    if set(out) != set(range(1, 13)):
        raise ValueError(f"calendar_v0.yml: нужны месяцы 1..12, есть {sorted(out)}")
    return out


def month_of(world: World, month_number: int) -> CalendarMonth | None:
    """Месяц календаря по номеру."""
    return world.calendar.get(month_number)


def monthly_labor_days(
    world: World, month_number: int, preset_id: str, boon: bool = False
) -> float:
    """Трудодни пресета в данном месяце (по календарю), в единицах тика.

    villein/cotter — base_week_days × 4; `boon` добавляет extra_week_days, если
    месяц помечен `boon_allowed`. geneat/sokeman — только callout (недельных дней
    нет). slave — always, сезонного нуля нет.
    """
    month = month_of(world, month_number)
    if month is None:
        return 0.0
    mod = month.labor_mod.get(preset_id, {})
    if preset_id in ("villein", "cotter"):
        base = float(mod.get("base_week_days", 0.0))
        extra = float(mod.get("extra_week_days", 0.0))
        if not (boon and month.boon_allowed):
            extra = 0.0
        return (base + extra) * WEEKS_PER_MONTH
    if preset_id in ("geneat", "sokeman"):
        return CALLOUT_DAYS if mod.get("callout") else 0.0
    if preset_id == "slave":
        return SLAVE_MONTH_DAYS if mod.get("always") else 0.0
    return 0.0


def seasonal_labor_days(
    world: World, household, month_number: int | None = None, boon: bool = False
) -> float:
    """Трудодни двора в месяце по его пресету (по умолчанию — текущий месяц)."""
    number = month_number if month_number is not None else world.clock.month
    return monthly_labor_days(world, number, household.legal_status_id, boon)


def hire_demand(world: World, month_number: int | None = None) -> str:
    """Спрос на наём свободных без земли: low | high."""
    number = month_number if month_number is not None else world.clock.month
    month = month_of(world, number)
    if month is None:
        return "low"
    return str(month.labor_mod.get("free_landless", {}).get("hire_demand", "low"))


def boon_cap(world: World, household) -> float:
    """Потолок помоги (bene) за год из бандла двора."""
    preset = preset_of(world, household)
    if preset is None:
        return 0.0
    bundle = world.catalogs.bundles.get(preset.obligation_bundle)
    if bundle is None:
        return 0.0
    term = bundle.terms.get("boon_days_cap", {})
    return float(term.get("value", 0.0))


def take_boon(world: World, household, month_number: int | None = None) -> float:
    """Взять помогу в месяц с `boon_allowed`, до годового cap; вернуть дни.

    Это не бесплатно юридически: счётчик `boon_used_this_year` растёт (tension_flag).
    Счастье не моделируется.
    """
    number = month_number if month_number is not None else world.clock.month
    month = month_of(world, number)
    if month is None or not month.boon_allowed:
        return 0.0
    mod = month.labor_mod.get(household.legal_status_id, {})
    extra = float(mod.get("extra_week_days", 0.0)) * WEEKS_PER_MONTH
    cap = boon_cap(world, household)
    if world.stats.get(BOON_YEAR_KEY) != float(world.clock.year):
        world.stats[BOON_YEAR_KEY] = float(world.clock.year)
        world.stats[BOON_USED_KEY] = 0.0
    used = float(world.stats.get(BOON_USED_KEY, 0.0))
    allowed = min(extra, max(0.0, cap - used))
    if allowed > 0:
        world.bump(BOON_USED_KEY, allowed)
    return allowed


def can_sow_demesne(world: World, month_number: int | None = None) -> bool:
    """Разрешена ли гэфоль-пахота своим зерном в этом месяце (осень)."""
    number = month_number if month_number is not None else world.clock.month
    month = month_of(world, number)
    return bool(month and month.sow_demesne_acres)


def sow_demesne(world: World, household, month_number: int | None = None) -> float:
    """Отдать зерно двора в посев домена (сток замка) в разрешённый месяц.

    Материю списывает Ledger (`transfer`), урожайность не считаем: это график
    долга, а не yield. Возвращает, сколько зерна ушло в домен.
    """
    if not can_sow_demesne(world, month_number):
        return 0.0
    preset = preset_of(world, household)
    if preset is None:
        return 0.0
    bundle = world.catalogs.bundles.get(preset.obligation_bundle)
    if bundle is None or "sow_demesne_acres" not in bundle.terms:
        return 0.0
    acres = float(bundle.terms["sow_demesne_acres"].get("value", 0.0))
    need = acres * SEED_GRAIN_PER_ACRE
    stock = world.get_stock(household.stock_id)
    available = stock.amounts.get("grain", 0.0)
    take = min(available, need)
    if take <= 0:
        return 0.0
    court = world.get_stock("settlement:" + world.player.court_settlement_id)
    world.ledger.transfer(stock, court, "grain", take, "sow_demesne", world.clock.date)
    return take

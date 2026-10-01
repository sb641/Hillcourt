"""Расчёт коэффициентов производственной клетки по ADR 0107–0110, 0132.

Формула ветвится только по данным: режим клетки и календарь. Сценарий — данные,
а не особый код (ADR 0132 п. 4). Модуль не содержит собственной формулы выхода:
множители читает `engine/growth.py`, а выход считает рецепт по каталогу.
"""

from __future__ import annotations

from typing import Any

EPSILON = 1e-9


def field_yield_factor(world: Any, tile_id: str) -> float:
    """Множитель пашни по режиму клетки: пашня растёт там, где землю пашут.

    Режим читается из `land_regimes`: пахать можно на домене и на наделах
    (`plough` в `allowed_actions`), а на пустоши, заповедном лесу и чужом
    держании зерно не растёт. Надела двора это не трогает: `holding_scale` —
    виргата 1,0 или клочок 0,5 из пресета, а не доля от числа дворов на гексе.
    """
    tile = getattr(world, "tiles", {}).get(tile_id)
    if tile is None:
        return 1.0
    regimes = getattr(getattr(world, "catalogs", None), "land_regimes", {})
    regime = regimes.get(getattr(tile, "regime_id", ""))
    if regime is None:
        return 1.0
    return 1.0 if "plough" in getattr(regime, "allowed_actions", ()) else 0.0


def demesne_field_factor(world: Any, tile_id: str, params: Any) -> float:
    """Множитель выхода клетки ДОМЕНА — из `grow_grain.params.demesne_base_yield`.

    Домен отличается от надела (`holding_scale` виргаты) собственным числом
    работников, а значит и своей нормой выхода: спрос гекса
    (`manor.yml::demesne.demand_days_per_tile`) снимается целиком и даёт
    300–350 зерна в год (ADR 0137/0138). Надел и пустошь берут 1.0 — их числа
    (4 работника ≈ 137, 8 ≈ 274) задаёт ADR 0137 и они не меняются.

    Домен опознаётся данными, а не строкой: это режим, чья земля работает за
    трудодни (`land_regimes.yml::requires_labor_days`), а не кормит двор
    напрямую (`feeds_household`). Один и тот же множитель читают и рост
    (`engine/growth.py`), и выход (`economy/soil.py::tile_yield_factor`), иначе
    урожайный потолок превратился бы в нехватку посева.
    """
    tile = getattr(world, "tiles", {}).get(tile_id)
    if tile is None or not isinstance(params, dict):
        return 1.0
    regimes = getattr(getattr(world, "catalogs", None), "land_regimes", {})
    regime = regimes.get(getattr(tile, "regime_id", ""))
    if regime is None or not getattr(regime, "requires_labor_days", False):
        return 1.0
    # Нормы выхода домена в коде нет: число живёт только в
    # `spawn_rules.yml::grow_grain.params.demesne_base_yield` (ADR 0163 п. 5).
    # Заглушка `.get(..., 1.0)` молча срезала бы урожай домена на 21 % и
    # выглядела бы как «просто другой каталог», поэтому отсутствие ключа —
    # ошибка загрузки данных, а не тихий ноль.
    if "demesne_base_yield" not in params:
        raise ValueError(
            "grow_grain.params.demesne_base_yield нет в каталоге: норма выхода "
            "домена обязана быть числом в spawn_rules.yml (ADR 0163 п. 5)"
        )
    try:
        return float(params["demesne_base_yield"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "grow_grain.params.demesne_base_yield не число: "
            f"{params['demesne_base_yield']!r}"
        ) from exc


def _month(world: Any, month: int | None) -> int | None:
    if month is not None:
        return int(month)
    clock = getattr(world, "clock", None)
    value = getattr(clock, "month", None)
    return int(value) if value is not None else None


def _season_value(entry: Any) -> float:
    if isinstance(entry, dict):
        return float(entry.get("plot_yield", 1.0))
    return float(getattr(entry, "plot_yield", 1.0))


def growth_season_factor(
    world: Any,
    rule_id: str,
    month: int | None = None,
    fallback: float = 1.0,
) -> float:
    """Вернуть множитель роста, нормализованный относительно пика правила."""
    number = _month(world, month)
    rule = getattr(getattr(world, "catalogs", None), "spawn_rules", {}).get(rule_id)
    params = getattr(rule, "params", {})
    peak = params.get("season_peak_month") if params else None
    if number is None or peak is None:
        return float(fallback)
    peak_month = int(peak)
    if number == peak_month:
        return 1.0
    seasons = getattr(world, "seasons", {})
    current = seasons.get(number)
    peak_entry = seasons.get(peak_month)
    if current is not None and peak_entry is not None:
        peak_value = _season_value(peak_entry)
        if peak_value > EPSILON:
            return _season_value(current) / peak_value
    return 0.5

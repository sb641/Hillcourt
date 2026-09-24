"""Бандлы повинностей: данные каталога → строки `Obligation` для тика.

Сводим к месячному тику только то, что он умеет: трудодни (`duty_days`) и
фиксированную натуральную ренту. Остальные условия бандла (годовые пенсы,
натура на праздник, засев акров, подвода/гоньба) остаются данными и помечены
`note` в каталоге: к тику месяца они ещё не сведены.
"""

from __future__ import annotations

from ..ontology import Obligation
from ..world import World
from .regimes import preset_of

SUPPORTED_TERMS = ("fixed_rent_grain_or_pence",)


def bundle_of(world: World, household):
    """Бандл двора по пресету (`obligation_bundle`), или None."""
    preset = preset_of(world, household)
    if preset is None:
        return None
    return world.catalogs.bundles.get(preset.obligation_bundle)


def _duty(world: World, household) -> Obligation:
    """Контейнер барщины: дни заполняются сезонно из календаря (legal.calendar)."""
    obligation = Obligation(
        id=f"obl_{household.id}_seasonal_labor",
        household_id=household.id,
        kind="labor_duty",
        due_good=None,
        due_amount=0.0,
        period_months=1,
        paid_total=0.0,
        arrears=0.0,
        basis="duty",
        duty_days=0.0,
    )
    world.obligations[obligation.id] = obligation
    household.obligation_ids.append(obligation.id)
    return obligation


def _rent(world: World, household, amount: float, term: str) -> Obligation:
    obligation = Obligation(
        id=f"obl_{household.id}_{term}",
        household_id=household.id,
        kind="rent",
        due_good="grain",
        due_amount=amount,
        period_months=1,
        paid_total=0.0,
        arrears=0.0,
        basis="fixed",
    )
    world.obligations[obligation.id] = obligation
    household.obligation_ids.append(obligation.id)
    return obligation


def materialize_obligations(world: World, household) -> list[Obligation]:
    """Создать `Obligation` двора по его бандлу (только сведённые условия)."""
    bundle = bundle_of(world, household)
    if bundle is None:
        return []
    terms = bundle.terms
    out: list[Obligation] = []
    if bundle.calendar_id is not None:
        out.append(_duty(world, household))
    if "fixed_rent_grain_or_pence" in terms:
        out.append(
            _rent(
                world,
                household,
                float(terms["fixed_rent_grain_or_pence"]["value"]),
                "fixed_rent_grain_or_pence",
            )
        )
    return out

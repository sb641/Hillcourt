"""Бухгалтерия материи (инвариант И-1): переводы и внешние потоки."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .ontology import SimDate, Stock

EPSILON = 1e-9

ENTRY_KINDS = ("transfer", "process", "external_in", "external_out")


@dataclass
class LedgerEntry:
    """Одна проводка: перевод, внешний приход или внешний расход."""

    kind: str
    src_id: Optional[str]
    dst_id: Optional[str]
    good: str
    amount: float
    reason: str
    rule_id: Optional[str]
    date: SimDate


class Ledger:
    """Журнал материи; гарантирует отсутствие отрицательных остатков."""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self.ext_in_total: float = 0.0
        self.ext_out_total: float = 0.0
        self.initial_total: float = 0.0

    def _charge(self, src: Stock, good: str, amount: float) -> None:
        available = src.amounts.get(good, 0.0)
        if available + EPSILON < amount:
            raise ValueError(
                f"Недостаточно '{good}' в стоке '{src.id}': "
                f"есть {available}, нужно {amount}"
            )
        left = available - amount
        src.amounts[good] = 0.0 if abs(left) < EPSILON else left

    def transfer(
        self,
        src: Stock,
        dst: Stock,
        good: str,
        amount: float,
        reason: str,
        date: SimDate,
    ) -> None:
        """Перевести материю из одного стока в другой без внешних потоков."""
        assert amount > 0, "Перевод требует amount > 0"
        self._charge(src, good, amount)
        dst.add(good, amount)
        self.entries.append(
            LedgerEntry("transfer", src.id, dst.id, good, amount, reason, None, date)
        )

    def external_in(
        self,
        dst: Stock,
        good: str,
        amount: float,
        reason: str,
        rule_id: Optional[str],
        date: SimDate,
    ) -> None:
        """Внести материю извне системы по правилу появления."""
        assert amount > 0, "Внешний приход требует amount > 0"
        dst.add(good, amount)
        self.ext_in_total += amount
        self.entries.append(
            LedgerEntry(
                "external_in", None, dst.id, good, amount, reason, rule_id, date
            )
        )

    def external_out(
        self,
        src: Stock,
        good: str,
        amount: float,
        reason: str,
        date: SimDate,
    ) -> None:
        """Вывести материю из системы наружу (в v0 не используется)."""
        assert amount > 0, "Внешний расход требует amount > 0"
        self._charge(src, good, amount)
        self.ext_out_total += amount
        self.entries.append(
            LedgerEntry("external_out", src.id, None, good, amount, reason, None, date)
        )

    def emit(
        self,
        src: Stock,
        dst: Stock,
        good: str,
        amount: float,
        reason: str,
        date: SimDate,
        allowed_goods: set[str] | None = None,
    ) -> None:
        """Выдать товар из пула обработки, где вещество однородно.

        Для рецептов-превращений (например, рапа + торф -> соль) проверяется
        общий остаток пула, а не остаток по каждому товару. Масса сохраняется:
        пул опустошается ровно на выданное количество. `allowed_goods` — состав
        рецепта, который вправе выходить из пула; без него emit запрещён, чтобы
        нельзя было молча подменить одно вещество другим.
        """
        assert amount > 0, "Выдача из пула требует amount > 0"
        if allowed_goods is None or good not in allowed_goods:
            raise ValueError(
                f"Пул '{src.id}': товар '{good}' не объявлен в рецепте "
                f"(разрешено: {sorted(allowed_goods) if allowed_goods else 'ничего'})"
            )
        available = src.total()
        if available + EPSILON < amount:
            raise ValueError(
                f"Недостаточно материи в пуле '{src.id}': есть {available}, нужно {amount}"
            )
        left_to_drain = amount
        for pool_good in sorted(src.amounts):
            if left_to_drain <= EPSILON:
                break
            have = src.amounts[pool_good]
            take = min(have, left_to_drain)
            if take <= 0:
                continue
            rest = have - take
            src.amounts[pool_good] = 0.0 if abs(rest) < EPSILON else rest
            left_to_drain -= take
        dst.add(good, amount)
        self.entries.append(
            LedgerEntry("process", src.id, dst.id, good, amount, reason, reason, date)
        )

    def capture_initial(self, total: float) -> None:
        """Запомнить стартовую сумму материи для проверки дельты."""
        self.initial_total = float(total)

    def delta(self, current_total: float) -> float:
        """Отклонение баланса: должно быть ~0 при соблюдении И-1."""
        return (
            float(current_total)
            - self.initial_total
            - self.ext_in_total
            + self.ext_out_total
        )

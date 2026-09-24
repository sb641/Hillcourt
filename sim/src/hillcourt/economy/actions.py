"""Каталог действий двора (actions_household.yml)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..ontology import HouseholdAction

KNOWN_FIELDS = {
    "id", "name", "kind", "labor_share", "purpose", "recipes",
    "requires_terrain", "requires_tool", "risk",
}
VALID_KIND = {"main", "minor", "both"}


def load_actions(path: str | Path) -> dict[str, HouseholdAction]:
    """Прочитать actions_household.yml в словарь по id с проверкой полей."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"actions_household.yml '{path}': ожидался словарь")
    unknown_top = sorted(set(data) - {"version", "actions"})
    if unknown_top:
        raise ValueError(f"actions_household.yml '{path}': неизвестные разделы {unknown_top}")
    out: dict[str, HouseholdAction] = {}
    for record in data.get("actions", []):
        if not isinstance(record, dict):
            raise ValueError(f"actions_household.yml: запись должна быть словарём: {record!r}")
        rid = record.get("id", "<без id>")
        unknown = sorted(set(record) - KNOWN_FIELDS)
        if unknown:
            raise ValueError(f"Действие '{rid}': неизвестные поля {unknown}")
        for required in ("id", "name", "kind"):
            if required not in record:
                raise ValueError(f"Действие '{rid}': нет обязательного поля '{required}'")
        kind = record["kind"]
        if kind not in VALID_KIND:
            raise ValueError(f"Действие '{rid}': kind '{kind}' не из {sorted(VALID_KIND)}")
        out[rid] = HouseholdAction(
            id=rid,
            name=record["name"],
            kind=kind,
            labor_share=float(record.get("labor_share", 1.0)),
            purpose=str(record.get("purpose", "food")),
            recipes=list(record.get("recipes", [])),
            requires_terrain=list(record.get("requires_terrain", [])),
            requires_tool=bool(record.get("requires_tool", False)),
            risk=float(record.get("risk", 0.0)),
        )
    if not out:
        raise ValueError("actions_household.yml: каталог действий пуст")
    return out

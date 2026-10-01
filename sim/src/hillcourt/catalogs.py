"""Загрузка и валидация каталогов данных design/catalogs/*.yml."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .ontology import (
    Good,
    HazardRule,
    LandRegime,
    LegalStatus,
    ObligationBundle,
    ObligationTemplate,
    Office,
    Recipe,
    RightTemplate,
    SpawnRule,
)

DEFAULT_CATALOG_MAP: dict[str, str] = {
    "goods": "design/catalogs/goods.yml",
    "recipes": "design/catalogs/recipes.yml",
    "spawn_rules": "design/catalogs/spawn_rules.yml",
    "hazards": "design/catalogs/hazards.yml",
    "obligations": "design/catalogs/obligations.yml",
    "rights": "design/catalogs/rights.yml",
    "land_regimes": "design/catalogs/land_regimes.yml",
    "legal_statuses": "design/catalogs/legal_statuses.yml",
    "offices": "design/catalogs/offices.yml",
}

# Словарь действий земли (ADR 0161 п. 2, ADR 0182). Объявлено только то, на что
# механизм отвечает: `plough` — пахота (`engine/yield_law.py::field_yield_factor`),
# `take_game` — охота, `leave` — право уйти, `clear_forest` — расчистка леса
# (право выдаёт только домен, работа — рецепт `uproot_stumps` через
# `LAND_ACTION_RECIPES` ниже). Право и рецепт носят РАЗНЫЕ имена, как `take_game`
# и `take_game_*`: право — разрешение, рецепт — работа. Сбор хвороста снят тем
# же основанием, что и постройка (ADR 0150 п. 1, ADR 0161 п. 2): рецепта у него
# не было ни одного, а лес убирается существующим действием двора
# `cut_wood_if_allowed`. Право, которому нечем исполниться, — не право, а
# объявление, которое вводит юриста в заблуждение.
LAND_ACTIONS: tuple[str, ...] = (
    "plough",
    "take_game",
    "leave",
    "clear_forest",
)

LAND_ACTION_RECIPES: dict[str, tuple[str, ...]] = {
    "take_game": (
        "take_game_squirrel",
        "take_game_rabbit",
        "take_game_deer",
        "take_game_boar",
    ),
    # Расчистка леса: право `clear_forest` исполняется рецептом выкорчёвки (ADR 0182).
    "clear_forest": ("uproot_stumps",),
}


@dataclass
class Catalogs:
    """Разобранные каталоги, словари по id, плюс пути источников."""

    goods: dict[str, Good] = field(default_factory=dict)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    spawn_rules: dict[str, SpawnRule] = field(default_factory=dict)
    hazard_rules: dict[str, HazardRule] = field(default_factory=dict)
    obligation_templates: dict[str, ObligationTemplate] = field(default_factory=dict)
    right_templates: dict[str, RightTemplate] = field(default_factory=dict)
    land_regimes: dict[str, LandRegime] = field(default_factory=dict)
    legal_statuses: dict[str, LegalStatus] = field(default_factory=dict)
    offices: dict[str, Office] = field(default_factory=dict)
    bundles: dict[str, ObligationBundle] = field(default_factory=dict)
    source_paths: dict[str, str] = field(default_factory=dict)


_CATALOG_CACHE: dict[tuple[tuple[str, str], ...], Catalogs] = {}


def _check_record(record: Any, known: set[str], required: set[str], key: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError(f"Каталог '{key}': запись должна быть словарём, а не {type(record)}")
    rid = record.get("id", "<без id>")
    unknown = sorted(set(record) - known)
    if unknown:
        raise ValueError(
            f"Каталог '{key}', запись '{rid}': неизвестные поля {unknown}"
        )
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(
            f"Каталог '{key}', запись '{rid}': нет обязательных полей {missing}"
        )
    return record


def _parse_goods(docs: dict) -> dict[str, Good]:
    known = {
        "id", "name", "category", "storage", "spoil_per_month", "edible", "nutrition",
        "price_silver", "price_labor_silver", "price_materials_silver", "price_losses_silver",
    }
    out: dict[str, Good] = {}
    for record in docs.get("goods", []):
        r = _check_record(record, known, {"id", "name", "category", "storage"}, "goods")
        out[r["id"]] = Good(
            id=r["id"],
            name=r["name"],
            category=r["category"],
            storage=r["storage"],
            spoil_per_month=float(r.get("spoil_per_month", 0.0)),
            edible=bool(r.get("edible", False)),
            nutrition=float(r.get("nutrition", 0.0)),
            price_silver=float(r.get("price_silver", 0.0)),
            price_labor_silver=float(r.get("price_labor_silver", 0.0)),
            price_materials_silver=float(r.get("price_materials_silver", 0.0)),
            price_losses_silver=float(r.get("price_losses_silver", 0.0)),
        )
    return out


def _parse_recipes(docs: dict) -> dict[str, Recipe]:
    known = {
        "id", "name", "place", "requires_terrain", "inputs",
        "draws_standing", "outputs", "loss", "labor_days", "transform",
        "tool_multiplier",
    }
    out: dict[str, Recipe] = {}
    for record in docs.get("recipes", []):
        r = _check_record(record, known, {"id", "name", "place", "labor_days"}, "recipes")
        out[r["id"]] = Recipe(
            id=r["id"],
            name=r["name"],
            place=r["place"],
            requires_terrain=list(r.get("requires_terrain", [])),
            inputs=dict(r.get("inputs", {})),
            draws_standing=dict(r.get("draws_standing", {})),
            outputs=dict(r.get("outputs", {})),
            loss=dict(r.get("loss", {})),
            labor_days=float(r["labor_days"]),
            transform=bool(r.get("transform", False)),
            tool_multiplier=float(r.get("tool_multiplier", 1.0)),
        )
    return out


# Правило `grow_grain` — единственный носитель прибавок урожайности пашни, и
# `economy/soil.py` читает его `params` без права на подстановку (ADR 0157 п. 5).
# Ключ, который читает расчёт, обязан лежать в каталоге: отсутствие — ошибка
# загрузки, а не тихая подмена числа из Python (в `soil.py` таких чисел нет).
GROW_RULE_PARAMS: tuple[str, ...] = (
    "tool_yield",
    "draft_yield_per_head",
    "draft_yield_cap",
    "manure_yield_per_head",
    "manure_yield_cap",
    "manure_uptake",
    "rotation_yield",
    "rotation_month",
    "rotation_days_per_tile",
    "spread_days_per_unit",
    "spread_units_per_month",
)


def _parse_spawn_rules(docs: dict) -> dict[str, SpawnRule]:
    # Ярлык происхождения снят (ADR 0164 п. 2): объявлялся и грузился, но не
    # читался ни одним потребителем. Происхождение потока несёт вид проводки
    # `LedgerEntry.kind` и непустой `LedgerEntry.rule_id`, который и проверяет И-1.
    known = {"id", "name", "target", "kind", "params"}
    out: dict[str, SpawnRule] = {}
    for record in docs.get("spawn_rules", []):
        r = _check_record(
            record, known, {"id", "name", "target", "kind"}, "spawn_rules"
        )
        params = dict(r.get("params", {}))
        if r["id"] == "grow_grain":
            missing = [key for key in GROW_RULE_PARAMS if key not in params]
            if missing:
                raise ValueError(
                    f"Каталог 'spawn_rules', правило 'grow_grain': нет params {missing} — "
                    "урожай пашни не имеет числа, а подставлять его в коде запрещено "
                    "(ADR 0157 п. 5)"
                )
        out[r["id"]] = SpawnRule(
            id=r["id"],
            name=r["name"],
            target=r["target"],
            kind=r["kind"],
            params=params,
        )
    return out


def _parse_hazards(docs: dict) -> dict[str, HazardRule]:
    known = {"id", "name", "kind", "params"}
    out: dict[str, HazardRule] = {}
    for record in docs.get("hazards", []):
        r = _check_record(record, known, {"id", "name", "kind"}, "hazards")
        out[r["id"]] = HazardRule(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            params=dict(r.get("params", {})),
        )
    return out


def _parse_obligations(docs: dict) -> dict[str, ObligationTemplate]:
    """Шаблоны повинностей из секции `obligations:` файла `obligations.yml`.

    Секция обязана существовать: пустой список раздела — это «повинностей нет»,
    а отсутствие ключа — «раздел не написан», и это разные вещи (ADR 0155: тест,
    который не может упасть, хуже отсутствующего теста).
    """
    if "obligations" not in docs:
        raise ValueError("Файл 'obligations.yml': нет раздела 'obligations'")
    known = {
        "id", "name", "kind", "due_good", "period_months",
        "default_share", "labor_days", "basis", "default_amount",
    }
    out: dict[str, ObligationTemplate] = {}
    for record in docs.get("obligations", []):
        r = _check_record(record, known, {"id", "name", "kind"}, "obligations")
        out[r["id"]] = ObligationTemplate(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            due_good=r.get("due_good"),
            period_months=int(r.get("period_months", 1)),
            default_share=r.get("default_share"),
            labor_days=r.get("labor_days"),
            basis=r.get("basis", "share"),
            default_amount=r.get("default_amount"),
        )
    return out


def _parse_rights(docs: dict) -> dict[str, RightTemplate]:
    known = {"id", "name", "kind", "default_rent_share", "land_regime_id"}
    out: dict[str, RightTemplate] = {}
    for record in docs.get("rights", []):
        r = _check_record(record, known, {"id", "name", "kind"}, "rights")
        out[r["id"]] = RightTemplate(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            default_rent_share=r.get("default_rent_share"),
            land_regime_id=r.get("land_regime_id"),
        )
    return out


def _parse_land_regimes(docs: dict) -> dict[str, LandRegime]:
    known = {
        "id", "name", "allowed_actions", "requires_labor_days", "feeds_household",
    }
    out: dict[str, LandRegime] = {}
    for record in docs.get("land_regimes", []):
        r = _check_record(record, known, {"id", "name"}, "land_regimes")
        actions = list(r.get("allowed_actions", []))
        unknown = [a for a in actions if a not in LAND_ACTIONS]
        if unknown:
            raise ValueError(
                f"Режим земли '{r['id']}': неизвестные действия {sorted(unknown)}"
            )
        out[r["id"]] = LandRegime(
            id=r["id"],
            name=r["name"],
            allowed_actions=actions,
            requires_labor_days=bool(r.get("requires_labor_days", False)),
            feeds_household=bool(r.get("feeds_household", False)),
        )
    return out


_PERSONAL_STATUS = {"free", "tied", "slave"}
_LAND_RELATION = {"secure_holding", "tenement", "landless"}
_COURTS = {"manor", "public"}


def _parse_legal_statuses(docs: dict) -> dict[str, LegalStatus]:
    known = {
        "id", "name", "personal_status", "land_relation", "obligation_bundle",
        "can_leave", "marriage_needs_permission", "court", "wergeld",
        "inheritance", "can_sell_land", "can_be_taken_on_expedition",
        "ploughs", "land_kind",
    }
    required = {
        "id", "name", "personal_status", "land_relation", "obligation_bundle",
        "can_leave", "marriage_needs_permission", "court", "wergeld",
        "inheritance", "can_sell_land", "can_be_taken_on_expedition",
    }
    out: dict[str, LegalStatus] = {}
    for record in docs.get("legal_statuses", []):
        r = _check_record(record, known, required, "legal_statuses")
        if r["personal_status"] not in _PERSONAL_STATUS:
            raise ValueError(
                f"Пресет '{r['id']}': лишний personal_status {r['personal_status']}"
            )
        if r["land_relation"] not in _LAND_RELATION:
            raise ValueError(
                f"Пресет '{r['id']}': лишний land_relation {r['land_relation']}"
            )
        if r["court"] not in _COURTS:
            raise ValueError(f"Пресет '{r['id']}': лишний court {r['court']}")
        out[r["id"]] = LegalStatus(
            id=r["id"],
            name=r["name"],
            personal_status=r["personal_status"],
            land_relation=r["land_relation"],
            obligation_bundle=r["obligation_bundle"],
            can_leave=bool(r["can_leave"]),
            marriage_needs_permission=bool(r["marriage_needs_permission"]),
            court=r["court"],
            wergeld=bool(r["wergeld"]),
            inheritance=r["inheritance"],
            can_sell_land=bool(r["can_sell_land"]),
            can_be_taken_on_expedition=bool(r["can_be_taken_on_expedition"]),
            ploughs=bool(r.get("ploughs", True)),
            land_kind=r.get("land_kind", "waste"),
        )
    return out


_BUNDLE_CURRENCIES = {"labor-day", "in-kind", "penny", "acre"}


def _parse_bundles(docs: dict) -> dict[str, ObligationBundle]:
    """Бандлы повинностей из секции `bundles:` того же файла, что и повинности.

    Секция обязана существовать — по той же причине, что и в `_parse_obligations`.
    """
    if "bundles" not in docs:
        raise ValueError("Файл 'obligations.yml': нет раздела 'bundles'")
    known = {"id", "name", "currency_mix", "terms", "calendar"}
    out: dict[str, ObligationBundle] = {}
    for record in docs.get("bundles", []):
        r = _check_record(record, known, {"id", "name"}, "bundles")
        mix = list(r.get("currency_mix", []))
        unknown = [c for c in mix if c not in _BUNDLE_CURRENCIES]
        if unknown:
            raise ValueError(
                f"Бандл '{r['id']}': неизвестные валюты {sorted(unknown)}"
            )
        terms = dict(r.get("terms", {}))
        for term, spec in terms.items():
            if isinstance(spec, dict) and "value" in spec:
                if isinstance(spec["value"], (int, float)) and "unit" not in spec:
                    raise ValueError(
                        f"Бандл '{r['id']}', условие '{term}': у числа нет unit"
                    )
        out[r["id"]] = ObligationBundle(
            id=r["id"],
            name=r["name"],
            currency_mix=mix,
            terms=terms,
            calendar_id=r.get("calendar"),
        )
    return out


def _parse_offices(docs: dict) -> dict[str, Office]:
    known = {"id", "name", "action", "travel"}
    required = {"id", "name", "action", "travel"}
    out: dict[str, Office] = {}
    for record in docs.get("offices", []):
        r = _check_record(record, known, required, "offices")
        out[r["id"]] = Office(
            id=r["id"],
            name=r["name"],
            action=r["action"],
            travel=bool(r["travel"]),
        )
    return out


# Секции файла `obligations.yml`: обе обязательны, обе разбираются своими
# функциями, а в `Catalogs` попадают в разные слоты. Раньше файл вёл только
# `bundles`, а `obligations:` читался в пустоту (ADR 0186).
_OBLIGATION_FILE_SECTIONS = ("bundles", "obligations")


def _parse_obligations_file(docs: dict) -> tuple[dict, dict]:
    """Обе секции `obligations.yml`: `(бандлы, шаблоны повинностей)`."""
    return _parse_bundles(docs), _parse_obligations(docs)


_PARSERS: dict[str, tuple[str, Callable[[dict], dict]]] = {
    "goods": ("goods", _parse_goods),
    "recipes": ("recipes", _parse_recipes),
    "spawn_rules": ("spawn_rules", _parse_spawn_rules),
    "hazards": ("hazards", _parse_hazards),
    "obligations": ("obligations", _parse_obligations_file),
    "rights": ("rights", _parse_rights),
    "land_regimes": ("land_regimes", _parse_land_regimes),
    "legal_statuses": ("legal_statuses", _parse_legal_statuses),
    "offices": ("offices", _parse_offices),
}


def _read_yaml(path: Path, source: bytes | None = None) -> dict:
    try:
        if source is None:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        else:
            data = yaml.safe_load(source.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Файл каталога не найден: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Каталог '{path}' должен содержать словарь верхнего уровня")
    return data


def load_catalogs(
    repo_root: Path, catalog_map: dict[str, str] | None = None
) -> Catalogs:
    """Загрузить каталоги по карте путей относительно корня репозитория."""
    repo_root = Path(repo_root)
    mapping = dict(catalog_map) if catalog_map else dict(DEFAULT_CATALOG_MAP)
    sources: dict[str, bytes] = {}
    cache_key_items: list[tuple[str, str]] = []
    for key in sorted(mapping):
        if key not in _PARSERS:
            raise ValueError(f"Неизвестный каталог в карте путей: '{key}'")
        path = repo_root / mapping[key]
        try:
            source = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f"Файл каталога не найден: {path}") from exc
        sources[key] = source
        cache_key_items.append((key, hashlib.sha256(source).hexdigest()))

    cache_key = tuple(cache_key_items)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        catalogs = copy.deepcopy(cached)
    else:
        catalogs = Catalogs()
        for key in sorted(mapping):
            expected_key, parser = _PARSERS[key]
            path = repo_root / mapping[key]
            docs = _read_yaml(path, sources[key])
            allowed_top = {expected_key, "version"}
            required_sections = (expected_key,)
            if key == "obligations":
                allowed_top.update(_OBLIGATION_FILE_SECTIONS)
                required_sections = _OBLIGATION_FILE_SECTIONS
            unknown_top = sorted(set(docs) - allowed_top)
            if unknown_top:
                raise ValueError(
                    f"Каталог '{path}': неизвестные разделы {unknown_top}"
                )
            for section in required_sections:
                if section not in docs:
                    raise ValueError(
                        f"Каталог '{path}': нет раздела '{section}'"
                    )
            parsed = parser(docs)
            if key == "goods":
                catalogs.goods = parsed
            elif key == "recipes":
                catalogs.recipes = parsed
            elif key == "spawn_rules":
                catalogs.spawn_rules = parsed
            elif key == "hazards":
                catalogs.hazard_rules = parsed
            elif key == "obligations":
                catalogs.bundles, catalogs.obligation_templates = parsed
            elif key == "rights":
                catalogs.right_templates = parsed
            elif key == "land_regimes":
                catalogs.land_regimes = parsed
            elif key == "legal_statuses":
                catalogs.legal_statuses = parsed
            elif key == "offices":
                catalogs.offices = parsed
            catalogs.source_paths[key] = str(path)
        _CATALOG_CACHE[cache_key] = copy.deepcopy(catalogs)
    catalogs.source_paths = {
        key: str(repo_root / mapping[key]) for key in sorted(mapping)
    }
    return catalogs

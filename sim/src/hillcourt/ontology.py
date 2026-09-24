"""Сущности онтологии Hillcourt (docs/03_ontology.md), только dataclass'ы."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(order=True)
class SimDate:
    """Календарная дата симуляции: год, месяц, день.

    День в дате — для `Pack.eta_date` с точностью до суток (ADR 0071): месячный
    тик остаётся основным (И-4), но поход/обоз приходит в дневном контуре, а не
    через округлённый месяц. `advance_days` — единственная арифметика дней;
    месяц = `days_per_month` суток (конвенция мира, как и `DAYS_PER_MONTH`).
    """

    year: int
    month: int
    day: int = 1

    def advance(self, months_per_year: int = 12) -> "SimDate":
        """Вернуть дату, сдвинутую на один месяц вперёд."""
        month = self.month + 1
        year = self.year
        if month > months_per_year:
            month = 1
            year += 1
        return SimDate(year, month, self.day)

    def to_day_index(
        self, days_per_month: int = 30, months_per_year: int = 12
    ) -> int:
        """Дата → сквозной номер суток от (год 1, месяц 1, день 1)."""
        dpm = int(days_per_month)
        mpy = int(months_per_year)
        return (
            (self.year - 1) * mpy * dpm
            + (self.month - 1) * dpm
            + (self.day - 1)
        )

    def advance_days(
        self,
        days: float,
        days_per_month: int = 30,
        months_per_year: int = 12,
    ) -> "SimDate":
        """Вернуть дату, сдвинутую на `days` суток вперёд (ADR 0071).

        Дробные сутки округляются вверх до целых (срок похода — целые сутки).
        """
        dpm = int(days_per_month)
        mpy = int(months_per_year)
        index = self.to_day_index(dpm, mpy) + int(math.ceil(float(days)))
        year, rest = divmod(index, mpy * dpm)
        month, day = divmod(rest, dpm)
        return SimDate(year + 1, month + 1, day + 1)

    def __str__(self) -> str:
        return f"Y{self.year}-M{self.month:02d}"


@dataclass
class Person:
    """Человек домохозяйства (в v0 не управляется поштучно)."""

    id: str
    name: str
    household_id: str
    age_class: str
    curiosity: float
    fear: float
    health: float
    location_tile_id: str
    personal_status: str = "free"
    land_relation: str = "landless"
    obligation_bundle: Optional[str] = None
    age_months: int = 0


@dataclass
class Household:
    """Домохозяйство — автономный агент еды, работы и ренты.

    Поля решения (выбираются раз в месяц, `economy/decisions.py`):
      `main_action`/`minor_action` — id из `actions_household.yml`;
      `adventurism 0..1` — интерес минус страх и цена рук на поле;
      `rumor_fear 0..1` — страх по собственным слухам (соседние опасности, голод);
      `tool_wear` — накопленный износ топора (unit массы);
      `traveling` — двор в этом месяце ходил по соседней клетке (труд снят с поля).
    """

    id: str
    name: str
    settlement_id: Optional[str]
    member_ids: list[str]
    stock_id: str
    labor_days: float
    obligation_ids: list[str]
    hunger_days: int
    arrears_days: int
    mood: float
    intent: str
    current_tile_id: str
    left_at: Optional[SimDate] = None
    main_action: str = "work_plot"
    minor_action: str = "idle_repair"
    adventurism: float = 0.0
    rumor_fear: float = 0.0
    tool_wear: float = 0.0
    traveling: bool = False
    legal_status_id: str = "free_landless"
    personal_status: str = "free"
    land_relation: str = "landless"
    obligation_bundle: Optional[str] = None
    manor_id: Optional[str] = None
    holding_scale: float = 1.0
    food_streak: int = 0
    birth_count: int = 0


@dataclass
class Tile:
    """Клетка карты с террейном и стоячей материей.

    Проходимость для приказов дальнего хода (`engine/path.py`):
      `road` — дорога: дешевит вход любому профилю;
      `ford`/`bridge` — брод/мост: только через них профиль может войти
        в `water` (река без переправы путь режет).
    Ходьба топчет тропы (`engine/trails.py`): `trail_wear` — износ пути
      (3.0 — тропа, 12.0 — грунтовка); строеная дорога — только `road`
      через стройку, автоматом не ставится; вода и `road` вне системы.
    """

    id: str
    coord: tuple[int, int]
    terrain: str
    standing_stock_id: str
    hazard_ids: list[str]
    settlement_id: Optional[str] = None
    ruin_id: Optional[str] = None
    regime_id: str = "waste"
    road: bool = False
    ford: bool = False
    bridge: bool = False
    trail_wear: float = 0.0


@dataclass
class Settlement:
    """Поселение: двор игрока, двор-хутор или соляная деревня."""

    id: str
    name: str
    kind: str
    coord: tuple[int, int]
    household_ids: list[str]
    stores_stock_id: str
    works_tiles: list[str] = field(default_factory=list)


TRIBE_STANCES: tuple[str, ...] = ("independent", "allied", "vassal")


@dataclass
class Tribe:
    """Племя: политическая надстройка над деревней `native_village` (ADR 0064).

    Ровно 7 полей, и больше ничего: состав вычисляется из
    `Settlement.household_ids`, общая земля — `Right.kind=common` + `works_tiles`,
    своей книги (амбара/`Manor`) нет. `tribute_grain`/`muster_kits` — числа
    Economist (здесь дефолты, не выдуманная экономика). `stance` — политическая
    стойка; игроку точно не показывается (только `Report`, И-3).
    """

    id: str
    name: str
    stance: str
    settlement_id: str
    tribute_grain: float = 0.0
    muster_kits: float = 0.0


@dataclass
class Stock:
    """Контейнер материи (двор, склад, тайл, обоз, счёт-сток)."""

    id: str
    owner_kind: str
    owner_id: str
    amounts: dict[str, float] = field(default_factory=dict)

    def total(self) -> float:
        """Сумма всей материи в контейнере."""
        return float(sum(self.amounts.values()))

    def add(self, good: str, amount: float) -> None:
        """Добавить (или убавить) количество товара в контейнер."""
        self.amounts[good] = self.amounts.get(good, 0.0) + amount


OBLIGATION_CALL_STATUSES = (
    "pending",
    "met",
    "refused",
    "unable",
    "in_service",
    "returned",
    "overdue",
)


@dataclass
class Obligation:
    """Повинность домохозяйства: рента, трудовая повинность, побор, вызов."""

    id: str
    household_id: str
    kind: str
    due_good: Optional[str]
    due_amount: float
    period_months: int
    paid_total: float
    arrears: float
    right_id: Optional[str] = None
    basis: str = "share"
    corvee_days: float = 0.0
    duty_days: float = 0.0
    call_status: str = "pending"

    def __post_init__(self) -> None:
        if self.call_status not in OBLIGATION_CALL_STATUSES:
            raise ValueError(f"Неизвестный call_status '{self.call_status}'")


@dataclass
class Right:
    """Право держания (Tenure): кому, на какую клетку, за какую долю."""

    id: str
    holder_household_id: str
    tile_id: str
    kind: str
    granted_date: SimDate
    rent_share: float


@dataclass
class Report:
    """Известие: рассказ о факте с датой события, доставки и уверенностью."""

    id: str
    source: str
    subject_kind: str
    subject_id: str
    content: str
    facts: dict[str, Any]
    event_date: SimDate
    delivery_date: SimDate
    confidence: float
    distorted: bool = False
    observer_id: Optional[str] = None
    noise: float = 0.0


@dataclass
class Pack:
    """Обоз/посылка в пути; в v0 существует, но не задействован."""

    id: str
    kind: str
    origin_tile_id: str
    destination_tile_id: str
    route: list[str]
    member_ids: list[str]
    cargo: Stock
    departed_date: SimDate
    eta_date: SimDate
    status: str = "in_transit"
    owner_household_id: Optional[str] = None
    obligation_id: Optional[str] = None
    lost_date: Optional[SimDate] = None


@dataclass
class Hazard:
    """Опасность клетки (волки, топь, ватага)."""

    id: str
    kind: str
    tile_id: str
    intensity: float
    active: bool = True
    spawn_rule_id: Optional[str] = None
    population: float = 0.0
    satiety: float = 0.5


@dataclass
class Good:
    """Каталожный товар: где хранится, как портится, съедобен ли."""

    id: str
    name: str
    category: str
    storage: str
    spoil_per_month: float = 0.0
    edible: bool = False
    nutrition: float = 0.0


@dataclass
class Recipe:
    """Каталожный рецепт: что списывается, что выходит, потери и труд."""

    id: str
    name: str
    place: str
    requires_terrain: list[str]
    inputs: dict[str, float]
    draws_standing: dict[str, float]
    outputs: dict[str, float]
    loss: dict[str, float]
    labor_days: float
    transform: bool = False


@dataclass
class SpawnRule:
    """Каталожное правило появления материи, угрозы, обоза или руины."""

    id: str
    name: str
    target: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    external: Optional[str] = None


@dataclass
class HazardRule:
    """Каталожное правило опасности и её числовых эффектов."""

    id: str
    name: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObligationTemplate:
    """Шаблон повинности (каталог Legal)."""

    id: str
    name: str
    kind: str
    due_good: Optional[str] = None
    period_months: int = 1
    default_share: Optional[float] = None
    labor_days: Optional[float] = None
    basis: str = "share"
    default_amount: Optional[float] = None


@dataclass
class RightTemplate:
    """Шаблон права держания (каталог Legal)."""

    id: str
    name: str
    kind: str
    default_rent_share: Optional[float] = None
    land_regime_id: Optional[str] = None


@dataclass
class HouseholdAction:
    """Каталожное действие двора: какую долю труда и какие рецепты разрешает."""

    id: str
    name: str
    kind: str
    labor_share: float = 1.0
    purpose: str = "food"
    recipes: list[str] = field(default_factory=list)
    requires_terrain: list[str] = field(default_factory=list)
    requires_tool: bool = False
    risk: float = 0.0


@dataclass
class NeedConfig:
    """Каталожные потребности двора: рот, корм скота, дрова, износ топора."""

    adult_food_per_month: float
    child_food_per_month: float
    elder_food_per_month: float
    edible_order: list[str]
    winter_months: list[int]
    firewood_per_adult_winter_month: float
    feed_good: str
    feed_per_month: dict[str, float]
    axe_wear_per_batch: float
    axe_break_below: float
    wear_recipes: list[str]
    relief_min_court_grain: float
    relief_amount: float
    land_regime_id: Optional[str] = None
    birth_food_months: float = 3.0
    birth_streak_months: int = 3
    birth_max_household: int = 8
    birth_adults_required: int = 2
    birth_maturity_months: int = 24
    death_child_per_month: float = 0.001
    death_adult_per_month: float = 0.002
    death_elder_per_month: float = 0.02
    death_old_age_months: int = 600
    death_old_age_extra: float = 0.01


@dataclass
class LandRegime:
    """Режим земли (каталог Legal): что двор МОЖЕТ на клетке.

    `requires_labor_days` — домен: работа требует трудодней.
    `feeds_household` — надел: кормит двор (не смешивается с доменом).
    """

    id: str
    name: str
    allowed_actions: list[str] = field(default_factory=list)
    requires_labor_days: bool = False
    feeds_household: bool = False


@dataclass
class LegalStatus:
    """Пресет правового положения (каталог Legal): тройка осей + права.

    Оси: `personal_status ∈ {free,tied,slave}`, `land_relation ∈
    {secure_holding,tenement,landless}`, `obligation_bundle` — id бандла.
    Пресет — ярлык (holder/thegn/...), не отдельный класс в коде.
    """

    id: str
    name: str
    personal_status: str
    land_relation: str
    obligation_bundle: str
    can_leave: bool
    marriage_needs_permission: bool
    court: str
    wergeld: bool
    inheritance: str
    can_sell_land: bool
    can_be_taken_on_expedition: bool
    ploughs: bool = True
    land_kind: str = "waste"


@dataclass
class ObligationBundle:
    """Бандл повинностей (каталог Legal): три валюты и условия.

    `terms` — словарь term → {value, unit, note}. `unit` обязателен для числа
    (`labor-day` / `acre` / `penny` / `in-kind`); часть условий ещё не сведена
    к месячному тику и помечена `note`.
    """

    id: str
    name: str
    currency_mix: list[str] = field(default_factory=list)
    terms: dict[str, Any] = field(default_factory=dict)
    calendar_id: Optional[str] = None


@dataclass
class CalendarMonth:
    """Месяц календаря работ (каталог Legal): сезон и месячные коэффициенты.

    `labor_mod` — по пресету: villein/cotter `base_week_days`+`extra_week_days`,
    geneat/sokeman `callout`, slave `always`, free_landless `hire_demand`.
    Барщина сезонная; дневного календаря святых в коде нет.
    """

    month: int
    season: str
    demesne_work: str
    labor_mod: dict[str, Any] = field(default_factory=dict)
    boon_allowed: bool = False
    sow_demesne_acres: bool = False


@dataclass
class Office:
    """Должность (каталог Legal): дорогой рот, что ездит (вестей не рождает)."""

    id: str
    name: str
    action: str
    travel: bool


@dataclass
class Manor:
    """Манор — книга земли держателя (игрок или вложенный тэн).

    Не класс юнита: тэн — вложенный манор с другим долгом вверх
    (`upward_bundle: thegn_service`). Игрок в v0 владеет одним корневым манором
    (`parent_manor_id = None`); вложенность depth ≤ 1, дальше не жалуют.

    МЕСТО СТОЛА (усадьба холма, не фьеф тэна):
      `seat_tile_id` — клетка зала (двор на холме);
      `seat_tiles` — клетки усадьбы: зал / двор замка / ближайшие поля
        под холмом (корт + ортогональные соседи, существующие в мире);
      `eye_range_tiles` — радиус видимости с холма в клетках (манхэттен);
        клетки в радиусе игрок знает без вестника (Report eye_from_hill,
        задержка 0), дальние — только почтой;
      `peace_range_tiles` — радиус мира усадьбы в клетках (манхэттен);
        hazard на клетке мира слабее (одно правило, не бонус к зерну).
    У вложенного тэна место стола пусто: глаз и мир — только у корня.
    Урожая место не даёт: ни grain, ни yield, ни rent_share от близости.

    Норма службы тэна (задаётся в `grant_thegn`, читается книгой):
      `service_kits_required` — сколько комплектов (`war_kit` в амбаре и во
      дворе держателя) фьеф обязан нести;
      `service_men_required` — сколько явок (взрослых двора держателя) фьеф
      обязан нести.
    Состояние службы (обновляется в `phase_record`):
      `service_kits_held` — комплектов сейчас в книге (амбар + двор держателя);
      `service_men_held` — живых взрослых двора держателя (его явки);
      `service_met` — норма закрыта в этом месяце (комплекты + явки + сытость);
      `service_gap` — нехватка комплектов до нормы (≥ 0);
      `bad_service_months` — подряд месяцев с незакрытой нормой (для политики
      отзыва: один плохой сезон — не повод);
      `iron_requested` — тэн просил железа (комплекты нечем собирать).
    """

    id: str
    holder_person_id: str
    stock_id: str = ""
    tile_ids: list[str] = field(default_factory=list)
    tile_regimes: dict[str, str] = field(default_factory=dict)
    household_ids: list[str] = field(default_factory=list)
    parent_manor_id: Optional[str] = None
    upward_bundle: str = "none"
    demesne_labor_demand_this_month: float = 0.0
    demesne_labor_filled: float = 0.0
    grant_ids: list[str] = field(default_factory=list)
    mustered: bool = False
    prior_preset: str = ""
    service_kits_required: float = 0.0
    service_men_required: float = 0.0
    service_kits_held: float = 0.0
    service_men_held: float = 0.0
    service_met: bool = False
    service_gap: float = 0.0
    bad_service_months: int = 0
    iron_requested: bool = False
    seat_tile_id: str = ""
    seat_tiles: list[str] = field(default_factory=list)
    eye_range_tiles: int = 1
    peace_range_tiles: int = 1

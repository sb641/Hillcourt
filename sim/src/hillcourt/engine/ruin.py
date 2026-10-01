"""Проигрыш — состояние мира, а не строка в логе (ADR 0209).

Хозяин постановил: цели у игры нет, проигрышей ровно два (армия отложена), и оба
должны быть **состояниями** — такими, которые дают `True` на разоревшемся мире и
`False` на здоровом, и которые нельзя вывести отсутствующей строкой в логе.

| проигрыш | закон | где меряется |
|---|---|---|
| **крестьяне ушли** | поселение оставлено: ушло `>= ABANDONED_LEFT_MIN` дворов **и** `>= ABANDONED_SHARE` от бывших поначалу | `Settlement.household_ids` × `Household.left_at` |
| **семейство банкроты** | двор лорда (`player.household_id`) не ет `FAMILY_HUNGER_MONTHS` месяцев подряд | `Household.hunger_days` |

**Числа — замер, а не вкус.** Замер 60 месяцев, сид 1729, отпечаток дерева
`sha256:1c96d0999703501dd438ccdb0baddd8fbd20d711bbd3da32e2c396ad0d6c58ca` (73 файла
`sim/src/hillcourt/**/*.py`, рецепт: относительный путь + NUL + содержимое + NUL,
поимённо в ADR 0209 §Замер). Промежуточный замер на дереве **до** добавления
`news/ruin.py` и фазы `phase_ruin` — `sha256:215443352cd1d11af…` (71 файл);
на нём числа те же, потому что закон к тому моменту уже был написан:

* **здоровая сторона — 0.** Во всех шести малых сценариях за 60 месяцев ни одно
  поселение с двумя и более дворами не потеряло ни одного двора. Пустели только
  хутора-однодворки: `fs_07` в `v0_hill_and_salt`, `fs_01` в `v0_native_village`,
  `fs_13` в `v0_shire` — по одному двору, то есть 100 % при знаменателе 1.
  **Отсюда `ABANDONED_LEFT_MIN = 2`:** без него «поселение оставлено» срабатывало бы
  в трёх здоровых мирах.
* **разоряющаяся сторона — 25 %.** `design/scenarios/v0_ruin_empty_village.yml`:
  сеньор на 6-м месяце снимает надельный режим с четырёх пашен деревни у ясеня,
  и к 11-му месяцу из 16 дворов уходят 4 = 25.0 %.
* **ни один проигрыш на здоровом мире, 60 месяцев, сиды 1729/7/99/42:** шесть малых
  сценариев и `v0_barony_100` (24 и 60 месяцев, сиды 1729/7). Единственные пустые
  поселения — хутора из одного двора: `fs_07` (`v0_hill_and_salt`), `fs_01`
  (`v0_native_village`), `fs_13` (`v0_shire`).
* **0.25 — четверть, а не измеренная константа.** Замер разделяет стороны интервалом
  `(0.00, 0.25]`, и любое число внутри разделяет их одинаково; выбрана верхняя
  граница интервала, потому что ниже неё «оставленным» можно было бы назвать поселение,
  потерявшее 2 двора из 8. **Число 0.5 и выше недостижимо в принципе:**
  `can_leave: false` у `villein`/`cotter` (`design/catalogs/legal_statuses.yml`),
  и в `v0_shire` у дерени у ясеня из 16 дворов уйти могут 7 = 43.8 %. Это не дефект
  порога, это закон `legal_statuses.yml`; обойти его можно только отменой `can_leave`,
  а она не моя зона.

**Доля считается от знаменателя первого наблюдения** (`settlement_households_at_start_*`
в `world.stats`), а не от текущего `len(household_ids)`. Причины: `arrive_household`
дописывает нового двора в список поселения (знаменатель рос бы и проигрыш размывался
бы), а снятия с дела у двора, ушедшего прочь, нет — ушедший двор навсегда остаётся
в `Settlement.household_ids` своего поселения, поэтому числитель монотонен.
Новых полей онтологии здесь нет и не требуется: `World.stats` — уже существующий
счётчик меток (`thegn_limit_tiles`, `wolves_den_topup_*`) и он входит в `state_hash`.

**Замкнутость (latch).** Оба проигрыша — необратимые состояния: однажды случившись, они не
отменяются. Ушедшие дворы не возвращаются (`docs/07_legal.md`), а семья, которая не
ела три месяца подряд, уже разорилась. Мгновенная проверка без защёлки позволяла бы
проигрыш «случиться и отмениться» — это не проигрыш, а испуг.

**И-1.** Ни одна функция модуля не двигает материю: здесь только чтение `Household`,
`Settlement` и запись чисел в `World.stats`. `phase_ruin` не зовёт ни одного рецепта,
ни одного `Ledger.transfer` и ни одного `bump` с материей.
"""

from __future__ import annotations

from dataclasses import dataclass

import math

from ..ontology import Household, Settlement
from ..world import World

#: Банкротство наступает, когда двор лорда не ет столько месяцев подряд.
#: Число не выдумано: ровно этим порогом `phase_migrate` открывает дверь ухода
#: крестьянину (ADR 0184 п. 2, `leaving = household.hunger_days >= 3`). Один и тот же
#: счётчик наказывает и уходом, и разорением — мера одна.
FAMILY_HUNGER_MONTHS = 3

#: «Поселение оставлено» не объявляется по одному ушедшему двору. Замер: в здоровом
#: мире за 60 месяцев пустеют только хутора из одного двора (100 % при знаменателе 1).
ABANDONED_LEFT_MIN = 2

#: Доля ушедших дворов, начиная с которой поселение считается оставленным.
#: Обоснование и границы — в докстринге модуля и в ADR 0209 §Замер.
ABANDONED_SHARE = 0.25

#: Знаменатель доли: сколько дворов было в поселении, когда тик увидел его впервые.
BASELINE_PREFIX = "settlement_households_at_start_"

#: Защёлка «крестьяне ушли».
RUIN_PEASANTS_LEFT = "ruin_peasants_left"
#: Защёлка «семейство банкроты».
RUIN_FAMILY_BANKRUPT = "ruin_family_bankrupt"
#: Абсолютный месяц банкротства (`year * 12 + month`) — для вести и для отчёта.
RUIN_FAMILY_BANKRUPT_MONTH = "ruin_family_bankrupt_month"
#: Защёлка по поселению: `ruin_abandoned_<settlement_id>` = 1.0.
RUIN_ABANDONED_PREFIX = "ruin_abandoned_"

#: Поселения, которые **не являются** поселениями баронства. Закон, а не вкус:
#:
#: * `native_village` — племя. Племя не уходит со своей земли (ADR 0064, ADR 0184
#:   п. 3: guard `_tribe_household`), его запустение не проигрыш держателя: у него нет
#:   ни книги, ни оброка, ни пайка от сеньора;
#: * `salt_village` — соляная деревня, **равный держатель, а не подданный**
#:   (`docs/07_legal.md`: «Сосед-держатель — равный, не вассал»; там же «соляная
#:   деревня (`holder`) не платит ренту игроку», `legal/regimes.py::vassalage_allowed`
#:   возвращает `False`). Код выражает то же самое: `scenario.py::non_root_settlements
#:   = {"salt_village", "native_village"}` — эти поселения не входят в книгу корневого
#:   манора ни одним двором.
#:
#: Исключение `salt_village` измерено, а не подобрано. `v0_barony_100`, 60 месяцев,
#: сид 1729, здоровый мир (три приказа самого сценария и ничего больше): соляная
#: деревня теряет **18 дворов из 18** — все её дворы `free_landless`, `can_leave: true`,
#: и при недоимке за соль они уходят все. Включение её в число поселений баронства даёт
#: ложное срабатывание на здоровом мире. Это не ошибка закона о проигрыше, а факт о
#: экономике солеварни (вопрос Economist, не мой), и выход здесь один — не считать
#: чужое поселение своим.
FOREIGN_SETTLEMENT_KINDS = frozenset({"native_village", "salt_village"})

#: Племенное поселение — частный случай (ADR 0064), названо отдельно, потому что на
#: него ссылается и `settlement_is_abandoned`, и весть.
TRIBE_SETTLEMENT_KIND = "native_village"


@dataclass(frozen=True)
class AbandonedSettlement:
    """Поселение, оставленное дворами: числа, по которым это сказано.

    Снимок величин, а не ссылка на `Settlement`: отчёт о проигрыше должен называть
    то же, что читает (ADR 0205 — то же рассуждение, что у вести о холме), и не
    нести контейнер мира в `facts`.
    """

    settlement_id: str
    name: str
    left: int
    at_start: int

    @property
    def share(self) -> float:
        """Доля ушедших дворов от знаменателя первого наблюдения."""
        if self.at_start <= 0:
            return 0.0
        return self.left / float(self.at_start)


def barony_settlements(world: World) -> list[Settlement]:
    """Поселения баронства — те, чьи дворы в книге корневого манора.

    Закон — по `Settlement.kind`, а не по имени (`FOREIGN_SETTLEMENT_KINDS`): имена
    сценариев меняются, а правовой статус поселения нет. Сортировка по id: порядок
    обхода не должен зависеть от порядка вставки, иначе И-6 (один seed — один мир)
    ломается о словарь.
    """
    return [
        settlement
        for settlement in sorted(world.settlements.values(), key=lambda s: s.id)
        if settlement.kind not in FOREIGN_SETTLEMENT_KINDS
    ]


def at_start(world: World, settlement: Settlement) -> int:
    """Знаменатель доли: сколько дворов было в поселении при первом наблюдении.

    Запоминается один раз в `world.stats` и с тех пор не меняется. Число снято
    ПОСЛЕ первых уходов, но это не искажает долю: ушедший двор остаётся в
    `Settlement.household_ids` навсегда, поэтому он уже учтён и в знаменателе, и в
    числителе. А вот новый двор (`arrive_household`) в знаменатель не попадает — и это
    правильная сторона ошибки: пополнение поселения проигрыш отодвигает, а не приближает.
    """
    key = BASELINE_PREFIX + settlement.id
    known = world.stats.get(key)
    if known is None:
        known = float(len(settlement.household_ids))
        world.stats[key] = known
    return int(known)


def left_count(world: World, settlement: Settlement) -> int:
    """Сколько дворов поселения ушло прочь (`Household.left_at` задан).

    Числитель монотонен: ушедший двор не возвращается в список поселения, а новые
    дворы приходят несписанными, поэтому их вклад — ноль.
    """
    left = 0
    for household_id in settlement.household_ids:
        household = world.households.get(household_id)
        if household is not None and household.left_at is not None:
            left += 1
    return left


def live_count(world: World, settlement: Settlement) -> int:
    """Сколько дворов поселения осталось на месте (`left_at` не задан)."""
    live = 0
    for household_id in settlement.household_ids:
        household = world.households.get(household_id)
        if household is not None and household.left_at is None:
            live += 1
    return live


def abandoned_threshold(at_first_seen: int) -> int:
    """Сколько дворов должно уйти, чтобы поселение признали оставленным.

    **Закон и его арифметика живут здесь, а не в вести** (ADR 0216 §Дыра 4).
    Правка ADR 0215 объявила, что «вести и закон не считаются каждый сам по
    себе», и добавила для этого `news/ruin.py::ruin_threshold` — то есть
    **создала ровно две арифметики**, между которыми обещала не разойтись. Пока
    числа совпадали, совпадение держали два человека; изменится одна из двух —
    и весть начнёт называть порог, которого нет.

    Одна формула, один дом. Весть читает её отсюда, и разойтись им больше негде:

        `left >= ABANDONED_LEFT_MIN`  **и**  `left >= ABANDONED_SHARE * base`

    Целочисленный порог для текста — `max(ABANDONED_LEFT_MIN, ceil(ABANDONED_SHARE
    * base))`: при целом `left` условие `left >= ABANDONED_SHARE * base` и условие
    `left >= ceil(...)` равносильны, то есть округление меняет только форму
    предложения игроку, а не его смысл.
    """
    return max(ABANDONED_LEFT_MIN, math.ceil(ABANDONED_SHARE * max(0, int(at_first_seen))))


def settlement_is_abandoned(world: World, settlement_id: str) -> bool:
    """Оставлено ли поселение дворами — ЗАКОН, а не сообщение.

    Два условия, оба нужны. `left >= ABANDONED_LEFT_MIN` отсекает хутор из одного
    двора (замер: именно такие пустеют в здоровом мире, 100 % при знаменателе 1).
    `left / at_start >= ABANDONED_SHARE` требует, чтобы ушла заметная доля, а не
    «двое из восьми». Племенное поселение законом не является (ADR 0064).

    Порог берётся функцией `abandoned_threshold` — той же, что читает весть.
    """
    settlement = world.settlements.get(settlement_id)
    if settlement is None or settlement.kind in FOREIGN_SETTLEMENT_KINDS:
        return False
    left = left_count(world, settlement)
    if left < ABANDONED_LEFT_MIN:
        return False
    base = at_start(world, settlement)
    if base <= 0:
        return False
    return left >= abandoned_threshold(base)


def abandoned_settlements(world: World) -> list[AbandonedSettlement]:
    """Все оставленные поселения баронства — по порядку id."""
    out: list[AbandonedSettlement] = []
    for settlement in barony_settlements(world):
        if not settlement_is_abandoned(world, settlement.id):
            continue
        out.append(
            AbandonedSettlement(
                settlement_id=settlement.id,
                name=settlement.name,
                left=left_count(world, settlement),
                at_start=at_start(world, settlement),
            )
        )
    return out


def family_household(world: World) -> Household | None:
    """Двор сеньора — семья, которая банкротится.

    Это `Player.household_id`, а не «кто первый в книге»: у лорда в книге десятки
    дворов, и только у него одного паёк берётся из амбара корня по ставке `holder`
    (`economy/manor.py::_board`). Двор, ушедший со двора (такого у лорда быть не может:
    держатель держит манор, `legal/regimes.py::can_leave`), проигрышем не считается —
    вернулась бы только вырожденная пустая оболочка.
    """
    household_id = getattr(world.player, "household_id", None)
    if not household_id:
        return None
    household = world.households.get(str(household_id))
    if household is None or household.left_at is not None:
        return None
    return household


def family_hunger_months(world: World) -> int:
    """Сколько месяцев подряд двор сеньора не ест (`Household.hunger_days`)."""
    household = family_household(world)
    return int(household.hunger_days) if household is not None else 0


def family_bankrupt_now(world: World) -> bool:
    """Разорилась ли семья ПРЯМО СЕЙЧАС (мгновенная мера, без защёлки)."""
    return family_hunger_months(world) >= FAMILY_HUNGER_MONTHS


def family_bankrupt(world: World) -> bool:
    """**Проигрыш «семейство банкроты» — состояние.** Раз после — навсегда.

    Защёлка в `world.stats`, а не пересчёт: `hunger_days` обнуляется первым же сытым
    месяцем, и без защёлки проигрыш мог бы случиться и отмениться, то есть не быть
    проигрышем. Счётчик месяца банкротства пишется тем же тиком.
    """
    return world.stats.get(RUIN_FAMILY_BANKRUPT, 0.0) >= 1.0


def peasants_left(world: World) -> bool:
    """**Проигрыш «крестьяне ушли» — состояние.** Раз после — навсегда."""
    return world.stats.get(RUIN_PEASANTS_LEFT, 0.0) >= 1.0


def is_lost(world: World) -> bool:
    """Проигрыш наступил по любому из двух законов (army отложена хозяином)."""
    return peasants_left(world) or family_bankrupt(world)


def phase_ruin(world: World) -> None:
    """Пересчитать оба закона и защёлкнуть то, что случилось (раз в тик).

    Фаза стоит сразу за `phase_migrate`: уход этого месяца уже помечен
    (`Household.left_at`), а `hunger_days` лорда уже посчитан фазой `phase_consume`
    на 6 шагов раньше. Материю фаза не двигает и случайность не тратит, поэтому
    И-1 и И-6 выполняются тривиально.
    """
    for settlement_id in (s.id for s in barony_settlements(world)):
        if world.stats.get(RUIN_ABANDONED_PREFIX + settlement_id, 0.0) >= 1.0:
            continue
        if not settlement_is_abandoned(world, settlement_id):
            continue
        world.stats[RUIN_ABANDONED_PREFIX + settlement_id] = 1.0
        world.stats[RUIN_PEASANTS_LEFT] = 1.0
    if world.stats.get(RUIN_FAMILY_BANKRUPT, 0.0) >= 1.0:
        return
    if not family_bankrupt_now(world):
        return
    world.stats[RUIN_FAMILY_BANKRUPT] = 1.0
    world.stats[RUIN_FAMILY_BANKRUPT_MONTH] = float(
        world.clock.date.year * 12 + world.clock.date.month
    )

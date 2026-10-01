"""Племя: независимая единица, оброк только в солидарности, торговля по умолчанию.

Слова хозяина, ставшие законом (ADR 0140): племя **не подчинилось нам**, это
изолированный соседний населённый пункт, с которым **по умолчанию можно
торговать**, пока мы не в военных отношениях. Пока племя не принято в
солидарности, оно **ничего нам не платит: ни оброка, ни десятины, ни подачи, ни
барщины**.

Ровно семь полей `Tribe` (ADR 0064), состав вычисляется из
`Settlement.household_ids`. Здесь пять рычагов, все на существующих сущностях:

- **Повинности** (`tribute_grain`) — существующий `levy`-механизм: одна
  `Obligation(kind=levy)` на племенный двор, но **только после принятия в
  солидарности** (`TRIBUTE_STANCE`). При `allied` повинностей нет — это и была
  регрессия юриста (ADR 0140 «Что отменяет»: оброк при `allied` отменяется).
  Солидарность в онтологии ещё не заведена, поэтому носитель подчинения —
  существующая стойка `vassal`; новая механика подчинения не выдумывается.
- **Корщина** (`labor_duty`) — в солидарности племя получает настоящую корщину
  «как любой двор» (ADR 0140 п. 4, ADR 0149 п. 1) и ровно одно отличие: её можно
  оплатить вместо отработки, и оплата отменяет начисление за период
  (ADR 0149 п. 2). До солидарности корщины нет вовсе.
- **Вызов** (`MUSTER_STANCE`) — не повинность и не оплата, а служба по союзу:
  союзника (`allied`) позвать можно, как и раньше (ADR 0066/0089 не тронуты).
  Отделена от оброка намеренно: сужение оброка не должно отнимать вызов.
- **Подача** — не наша: племя не в книге барона, `relief_sources` для
  племенного двора пуст, поэтому наша подача в общинный склад племени не идёт
  (ADR 0140 «Что отменяет»). Проверяет тест, кодом не меняется.
- **Торговля** (`tribe_trade_open`) — **открыта по умолчанию** в обе стороны.
  Закрывает её ровно одно: военные отношения, то есть живая угроза на жилой
  клетке племени (`WAR_HAZARD_KINDS`). Сейчас ни один сценарий такой угрозы не
  заводит, поэтому работает ветка «открыто»; носитель военных отношений в
  онтологии пока отсутствует и не выдумывается.

Открытый вопрос ADR 0140 («платить натурально или серебром вместо барщины»)
**хозяин закрыл: платит только племя и только после солидарности** (ADR 0145,
поправка п. 3 — ADR 0149). Барщина своих дворов осталась бесплатной (ADR 0131).
Оплата погашает повинность за период, а не надбавляется к ней: начисление
корщины за оплаченный период не идёт.

Материя — только `Ledger.transfer` существующей фазы (И-1); новая фаза тика
не заводится (вызов `manor_month` в той же фазе, что и повинности).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..legal.actions import grant_tenure, revoke_tenure
from ..legal.obligations import is_corvee
from ..ontology import Household, Obligation, SimDate, Tribe
from ..world import World

EPSILON = 1e-9
# Оброк и прочие повинности — ТОЛЬКО в солидарности (ADR 0140). `allied` — союз,
# а не подчинение: союзник платить нам не должен.
TRIBUTE_STANCE: frozenset[str] = frozenset({"vassal"})
# Вызов — служба, а не повинность: союзника можно позвать и раньше подчинения.
MUSTER_STANCE: frozenset[str] = frozenset({"allied", "vassal"})
# Военные отношения: единственная причина закрыть торговлю с племеней (ADR 0140
# п. 3). Носитель — живая угроза на жилой клетке племени; сегодня таких угроз
# ни один сценарий не заводит, поэтому по умолчанию торговля ОТКРЫТА.
WAR_HAZARD_KINDS: frozenset[str] = frozenset({"war"})
KIT_GOOD = "war_kit"
LEVY_PREFIX = "levy_tribe_"
CORVEE_PREFIX = "obl_tribe_corvee_"
CORVEE_GOOD = "grain"
# Солидарность даёт племени наделы и книгу (ADR 0156 п. 1-2). Пресет `geneat`
# уже есть в каталоге: свободный, `land_relation = tenement`, бандл
# `geneat_service`. Ни пресета, ни бандла, ни поля онтологии не заводим.
SOLIDARITY_STATUS = "geneat"
HOLDING_KIND = "tenure"
# Наделы дают корщину, оброк даёт `vassal` (ADR 0156 п. 3) — две разные
# повинности, как корщина плюс рента у любого двора. Ренты от наделов нет.
SOLIDARITY_RENT_SHARE = 0.0

# --- Вызов племени (ADR 0089 п.2): пороги ответа, измеренные (Economist) ---
# Комплект обслуживает двух взрослых: та же пропорция, что у нормы тэна
# (`service_kits_required = 1 + tenants` при `service_men_required` = взрослые
# держателя, ADR 0021) — комплект снаряжает отряд, а не каждого.
KITS_PER_PERSON = 0.5
ANSWERS: tuple[str, ...] = ("accepted", "unable", "refused")
MUSTER_PERIODS: tuple[int, ...] = (2, 3)
# Политика племени — закрытый список решений (ADR 0089 п.2): неизвестное значение
# НЕ молча согласие (fail-open Critic), а отказ применить вызов.
TRIBE_POLICIES: tuple[str, ...] = ("accept", "refuse")


@dataclass(frozen=True)
class TribeAnswer:
    """Ответ племени на вызов: ровно один исход + числа, по которым он выбран.

    Исходы не пересекаются: `unable` — нехватка (не отказ), `refused` — активное
    политическое решение, пришедшее явным аргументом `policy` (поток решений/
    новостей зоны Info/Legal, не физика и не бросок). Игрок получает весть, а не
    читает это состояние (И-3); вести строит Info из этих полей.
    """

    tribe_id: str
    call_date: SimDate
    status: str
    reason: str
    required_persons_count: int
    persons_available: int
    adults_available: int
    kits_available: float
    food_available: float
    food_needed: float
    period_months: int
    due_date: SimDate

    @property
    def is_refusal(self) -> bool:
        return self.status == "refused"


def tribe_households(world: World, tribe: Tribe) -> list[Household]:
    """Живые дворы племени: состав вычисляется из поселения (ADR 0064)."""
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is None:
        return []
    out: list[Household] = []
    for hid in settlement.household_ids:
        household = world.households.get(hid)
        if household is not None and household.left_at is None:
            out.append(household)
    return sorted(out, key=lambda h: h.id)


def _adults(world: World, household: Household) -> int:
    return sum(
        1
        for pid in household.member_ids
        if (person := world.persons.get(pid)) is not None
        and person.age_class == "adult"
    )


def _living_people(world: World, household: Household) -> int:
    return sum(1 for pid in household.member_ids if pid in world.persons)


def _levy_id(tribe: Tribe, household: Household) -> str:
    return f"{LEVY_PREFIX}{tribe.id}_{household.id}"


def _corvee_id(tribe: Tribe, household: Household) -> str:
    return f"{CORVEE_PREFIX}{tribe.id}_{household.id}"


def _drop_obligation(world: World, household: Household, oid: str) -> None:
    """Снять повинность племени: договор снят — повинности и долга нет."""
    world.obligations.pop(oid, None)
    if oid in household.obligation_ids:
        household.obligation_ids.remove(oid)


def tribe_holding_tiles(world: World, tribe: Tribe) -> list[str]:
    """Земли племени — его же `works_tiles`, без новых сущностей и чисел."""
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is None:
        return []
    return sorted(settlement.works_tiles)


def tribe_holds_tile(world: World, household: Household, tile_id: str) -> bool:
    """Держит ли двор индивидуальный надел на этой клетке."""
    return any(
        right.holder_household_id == household.id and right.tile_id == tile_id
        for right in world.rights.values()
    )


def tribe_right_ids(world: World, household: Household) -> list[str]:
    """Индивидуальные права двора племени (общинное право не трогаем)."""
    return sorted(
        right.id
        for right in world.rights.values()
        if right.holder_household_id == household.id and right.kind != "common"
    )


def grant_solidarity_holdings(world: World, tribe: Tribe) -> list[str]:
    """Солидарность даёт племени наделы и книгу; вернуть выданные клетки.

    ADR 0156 п. 1-2. Слова хозяина (ADR 0140 п. 1): принятое в солидарности племя —
    «деревня в составе нашего оборонца с такими же точно обязанностями и правами».
    Обязанности и права двора определяются наделом (ADR 0154), а у племени наделов
    не было — значит «теми же обязанностями» было пусто. Здесь пробел
    восполняется, а не обходится исключением: двор получает надел, попадает в
    книгу манора и становится `geneat` — свободным с землёй, службой, пахотой и
    правом уйти. Дальше всё следует само: надел кормит двор (ADR 0010), его труд
    идёт в пул домена книги, а сезонная корщина даётся календарём (ADR 0154).

    Наделов ровно столько, сколько у деревни земли и дворов: `works_tiles` поселения
    разбираются по одному на двор, ни числа, ни клетки не выдумываются. Двор без живых
    людей наделов не получает — по ADR 0140 племя без ртов не платит и не барщит.
    """
    granted: list[str] = []
    for household, tile_id in zip(
        tribe_households(world, tribe), tribe_holding_tiles(world, tribe)
    ):
        if _living_people(world, household) <= 0:
            continue
        if tribe_holds_tile(world, household, tile_id):
            granted.append(tile_id)
            continue
        grant_tenure(
            world,
            household.id,
            tile_id,
            kind=HOLDING_KIND,
            rent_share=SOLIDARITY_RENT_SHARE,
            status_id=SOLIDARITY_STATUS,
        )
        world.bump("tribe_holdings_granted", 1.0)
        granted.append(tile_id)
    return granted


def revoke_solidarity_holdings(world: World, tribe: Tribe) -> list[str]:
    """Потеря солидарности отбирает наделы, книгу и пресет (ADR 0156 п. 5).

    Двор снова `free_landless`, вне книги и без земли; повинности и долг снимает
    вызывающая сторона — `revoke_tenure` чистит обязательства сам.
    """
    revoked: list[str] = []
    for household in tribe_households(world, tribe):
        for right_id in tribe_right_ids(world, household):
            revoked.extend(revoke_tenure(world, household.id, right_id))
            world.bump("tribe_holdings_revoked", 1.0)
    return revoked


def _find_duty(world: World, household: Household) -> Obligation | None:
    """Контейнер барщины двора — тот, что завёл бандл (`legal.bundles._duty`)."""
    for oid in household.obligation_ids:
        known = world.obligations.get(oid)
        if known is not None and is_corvee(known):
            return known
    return None


def tribe_corvee_duty(
    world: World, tribe: Tribe, household: Household
) -> Obligation | None:
    """Корщина племени в солидарности; `None` — племя вне солидарности.

    ADR 0140 п. 4 и ADR 0149 п. 1: принятое в солидарности племя становится
    «деревней в составе нашего оборонца с такими же точно обязанностями и
    правами», поэтому корщина у него такая же, как у любого двора, и появляется
    вместе с солидарностью — до неё корщины нет вовсе.

    ADR 0156: корщина племени не особенная — её даёт надел и бандл `geneat`,
    как у любого землевладельца. Этот код только делает её **оплачиваемой**:
    проставляет контейнеру бандла `due_good`/`due_amount`. Своего второго
    контейнера не заводим — иначе один и тот же период был бы записан дважды
    (один отработкой, другой оплатой), а это ровно двойное взыскание, запрещённое
    ADR 0149. Если бандл контейнера не завёл (нет календаря у пресета), падаем
    на собственную повинность — но тогда у пресета нет и сезонной корщины.

    ADR 0149 п. 2: оплата идёт существующей фазой повинностей (`phase_obligations`)
    и заполняет `paid_total`; подавление начисления — guard в
    `legal.obligations.accrue_corvee`. Выбор «отработать или заплатить» сделан
    порядком в фазе: оплата проверяется первой, и если зерна хватает, повинность
    оплачена, а начисление корщины не идёт; если не хватает — племя отрабатывает,
    а недобор в долг. Отработать и заплатить за одно и то же нельзя.

    Цена отработки — `Tribe.tribute_grain`, единственная платёжная ставка племени:
    ADR 0149 новых чисел и полей онтологии не заводит. Ставка нулевая — платить
    нечем, и племя отрабатывает корщину бесплатно, как любой двор (ADR 0131).
    """
    if not tribe_in_solidarity(world, tribe):
        return None
    obligation = _find_duty(world, household)
    if obligation is None:
        oid = _corvee_id(tribe, household)
        obligation = world.obligations.get(oid)
        if obligation is None:
            obligation = Obligation(
                id=oid,
                household_id=household.id,
                kind="labor_duty",
                due_good=CORVEE_GOOD,
                due_amount=float(tribe.tribute_grain),
                period_months=1,
                paid_total=0.0,
                arrears=0.0,
                right_id=None,
                basis="duty",
                duty_days=0.0,
            )
            world.obligations[oid] = obligation
            household.obligation_ids.append(oid)
            world.bump("tribe_corvee_created", 1.0)
    obligation.due_good = CORVEE_GOOD
    obligation.due_amount = float(tribe.tribute_grain)
    obligation.period_months = 1
    return obligation


def _find_levy(world: World, tribe: Tribe, household: Household) -> Obligation | None:
    oid = _levy_id(tribe, household)
    obligation = world.obligations.get(oid)
    if obligation is not None:
        return obligation
    for known_id in household.obligation_ids:
        known = world.obligations.get(known_id)
        if known is not None and known.kind == "levy" and known_id == oid:
            return known
    return None


def tribe_tribute_month(world: World, date: SimDate) -> list[Obligation]:
    """Заводить/держать месячный оброк племени; вернуть повинности этого месяца.

    Повинность заводится **только в солидарности** (`TRIBUTE_STANCE`, носитель —
    стойка `vassal`): племя до солидарности платит нам ничего (ADR 0140 п. 1).
    `allied` — союз, а не подчинение, поэтому при нём повинностей **0**: в этом
    и была регрессия юриста. `independent` и пустой двор (нет живых людей) —
    повинности нет; договор снятой повинности снимает и долг (иначе «независимое»
    племя копит долг как должник). Оплата/`arrears` — существующей фазе
    `phase_obligations`.

    Барщина племени — не оброк: она есть в солидарности даже при нулевой ставке
    (ADR 0149 п. 1) и не оплачивается деньгами ни при какой ставке, если племя
    не в солидарности. Вне солидарности снимается вместе с оброком.

    ADR 0156: солидарность — это наделы и книга. Наделы выдаются до оброка,
    потому что `grant_tenure` чистит обязательства двора, а оброк и корщину
    должен завести этот же месяц. Потеря солидарности отзывает наделы, книгу и
    пресет, а вместе с ними повинности и долг.
    """
    out: list[Obligation] = []
    for tribe_id in sorted(getattr(world, "tribes", {})):
        tribe = world.tribes[tribe_id]
        households = tribe_households(world, tribe)
        solidary = tribe_in_solidarity(world, tribe)
        owes = solidary and float(tribe.tribute_grain) > EPSILON
        if not solidary:
            revoke_solidarity_holdings(world, tribe)
        else:
            grant_solidarity_holdings(world, tribe)
        for household in households:
            obligation = _find_levy(world, tribe, household)
            if not solidary:
                # Не подчинено — повинностей нет: договор снимает и долг (иначе
                # «независимое» племя копит долг как должник).
                if obligation is not None:
                    _drop_obligation(world, household, obligation.id)
                    world.bump("tribe_levy_dropped", 1.0)
                if _corvee_id(tribe, household) in world.obligations:
                    _drop_obligation(world, household, _corvee_id(tribe, household))
                    world.bump("tribe_corvee_dropped", 1.0)
                continue
            if _living_people(world, household) <= 0:
                continue
            tribe_corvee_duty(world, tribe, household)
            if not owes:
                if obligation is not None:
                    _drop_obligation(world, household, obligation.id)
                    world.bump("tribe_levy_dropped", 1.0)
                continue
            if obligation is None:
                obligation = Obligation(
                    id=_levy_id(tribe, household),
                    household_id=household.id,
                    kind="levy",
                    due_good="grain",
                    due_amount=float(tribe.tribute_grain),
                    period_months=1,
                    paid_total=0.0,
                    arrears=0.0,
                    right_id=None,
                    basis="fixed",
                )
                world.obligations[obligation.id] = obligation
                household.obligation_ids.append(obligation.id)
                world.bump("tribe_levy_created", 1.0)
            else:
                obligation.due_amount = float(tribe.tribute_grain)
                obligation.period_months = 1
                if obligation.due_good is None:
                    obligation.due_good = "grain"
            out.append(obligation)
    return out


def tribe_in_solidarity(world: World, tribe: Tribe) -> bool:
    """Принято ли племя в солидарность — то есть обязано ли оно нам (ADR 0140).

    Солидарность в онтологии ещё не заведена (ADR 0140 п. 4: подчинение — будущая
    механика), поэтому носитель подчинения — существующая стойка `vassal`.
    Принято в солидарности — племя становится двором оборонца с теми же
    обязанностями, и только тогда у него появляются повинности.
    """
    return tribe.stance in TRIBUTE_STANCE


def tribe_tribe_tile(world: World, tribe: Tribe) -> str | None:
    """Жилая клетка племени: `Settlement.coord` поселения племени."""
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is None:
        return None
    return f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}"


def tribe_at_war(world: World, tribe: Tribe) -> bool:
    """Мы в военных отношениях с племенем: живая угроза на его жилой клетке.

    Носитель военных отношений в онтологии пока не заведён (ADR 0140 п. 3 — про
    закрытие торговли, а не про новую сущность), поэтому честный источник —
    активная угроза вида `WAR_HAZARD_KINDS` на жилой клетке племени. Ни один
    сценарий v0 такую угрозу не ставит, так что по умолчанию ответ `False`.
    """
    tile_id = tribe_tribe_tile(world, tribe)
    if tile_id is None:
        return False
    return any(
        hazard.kind in WAR_HAZARD_KINDS
        and hazard.active
        and hazard.tile_id == tile_id
        for hazard in world.hazards.values()
    )


def tribe_trade_open(world: World, tribe: Tribe) -> bool:
    """Открыта ли торговля с племенем — по умолчанию да, в обе стороны.

    Закон хозяина (ADR 0140 п. 3): «по умолчанию с ними можно торговать», и
    закрыта торговля только при военных отношениях. Никаких других причин
    закрывать нельзя: ни независимость, ни отсутствие книги барона, ни стоянка
    племени — это не повод.
    """
    return not tribe_at_war(world, tribe)


def tribe_of_household(world: World, household: Household) -> Tribe | None:
    """Племя, которому принадлежит двор, или None (двор не племенной)."""
    for tribe_id in sorted(getattr(world, "tribes", {})):
        tribe = world.tribes[tribe_id]
        if any(h.id == household.id for h in tribe_households(world, tribe)):
            return tribe
    return None


def tribe_muster_kits(world: World, tribe: Tribe) -> float:
    """Сколько комплектов племя может дать: факт `war_kit` в стоках дворов.

    Потолок — `muster_kits` (потенциал из ADR 0064); факт ниже потолка, когда
    комплектов в руках меньше. Новый товар/юнит не заводится: `war_kit`
    уже в каталоге. Племя без стойки союза/вассала не даёт ничего. Стойка здесь
    `MUSTER_STANCE`, а не `TRIBUTE_STANCE`: вызов — служба, а не повинность
    (ADR 0140 сузил только оброк).
    """
    if tribe.stance not in MUSTER_STANCE:
        return 0.0
    held = 0.0
    for household in tribe_households(world, tribe):
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            held += float(stock.amounts.get(KIT_GOOD, 0.0))
    # Склад общинной деревни — тоже снаряжение племени (не книга барона:
    # ADR 0064 п.4 запрещает второй Manor/амбар, `Settlement.stores` — общий).
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is not None:
        stores = world.stocks.get(settlement.stores_stock_id)
        if stores is not None:
            held += float(stores.amounts.get(KIT_GOOD, 0.0))
    return min(held, max(0.0, float(tribe.muster_kits)))


def tribe_can_muster(world: World, tribe: Tribe) -> bool:
    """Может ли племя дать вызов: есть взрослые и ≥1 комплект.

    Только проверка потенциала (ADR 0064/0066): вызова, `Pack` и handler нет.
    Служба по союзу (`MUSTER_STANCE`) — не повинность, оброк с неё не следует.
    """
    if tribe.stance not in MUSTER_STANCE:
        return False
    if tribe_muster_kits(world, tribe) < 1.0 - EPSILON:
        return False
    return any(
        _adults(world, household) >= 1 for household in tribe_households(world, tribe)
    )


def tribe_muster_capacity(world: World, tribe: Tribe, period_months: int) -> dict:
    """Сколько племя реально может выставить на срок службы (ADR 0089 п.2).

    Три потолка, минимум из них — честная нехватка, а не отказ:
      * взрослые в составе племени;
      * комплекты: `KITS_PER_PERSON` (комплект на двух), факт в стоках дворов под
        потолком `muster_kits`;
      * корм на весь срок: `adult_food_per_month` × голов × месяцев, берётся из
        зерна дворов племени и склада поселения (замер v0: 225+40 зерна на 9
        взрослых — 3 человека на 3 месяца съедают 9.0, 4 % запаса).
    """
    households = tribe_households(world, tribe)
    adults = sum(_adults(world, h) for h in households)
    kits = tribe_muster_kits(world, tribe)
    food = 0.0
    for h in households:
        stock = world.stocks.get(h.stock_id)
        if stock is not None:
            food += float(stock.amounts.get("grain", 0.0))
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is not None:
        stores = world.stocks.get(settlement.stores_stock_id)
        if stores is not None:
            food += float(stores.amounts.get("grain", 0.0))
    ration = 1.0
    if world.needs is not None:
        ration = float(world.needs.adult_food_per_month)
    per_person = ration * max(1, int(period_months))
    by_adults = adults
    by_kits = int(kits / KITS_PER_PERSON + EPSILON)
    by_food = int(food / per_person) if per_person > EPSILON else adults
    return {
        "adults": adults,
        "kits": kits,
        "food": food,
        "food_per_person": per_person,
        "by_adults": by_adults,
        "by_kits": by_kits,
        "by_food": by_food,
        "capacity": max(0, min(by_adults, by_kits, by_food)),
    }


def tribe_answer(
    world: World,
    tribe_id: str,
    required_persons_count: int,
    period_months: int,
    call_date: SimDate,
    policy: str = "accept",
) -> TribeAnswer:
    """Ответ племени на вызов: `accepted` / `unable` / `refused` (ADR 0089).

    Чистый вход/выход для Legal (создаёт повинность) и Info (строит весть):
    ни стоков, ни правок, ни RNG внутри. Нехватка людей/комплектов/корма —
    `unable` (не отказ). Отказ возможен только при явном политическом `policy`
    вида `"refuse"` (поток решений/новостей), который приходит аргументом.
    Срок службы — `period_months` (2 или 3), дедлайн — `call_date + period`.
    """
    tribe = world.tribes.get(tribe_id)
    if tribe is None:
        raise ValueError(f"Нет племени '{tribe_id}'")
    if int(period_months) not in MUSTER_PERIODS:
        raise ValueError(f"Срок службы {period_months} не из {MUSTER_PERIODS}")
    if str(policy) not in TRIBE_POLICIES:
        raise ValueError(
            f"Политика '{policy}' вне {TRIBE_POLICIES}: неизвестное решение не значит "
            "согласие (ADR 0089)"
        )
    required = max(0, int(required_persons_count))
    due_date = call_date.advance(world.clock.months_per_year * 12)
    for _ in range(int(period_months) - 1):
        due_date = due_date.advance(world.clock.months_per_year)
    if tribe.stance not in MUSTER_STANCE:
        return TribeAnswer(
            tribe_id=tribe_id, call_date=call_date, status="unable",
            reason="independent", required_persons_count=required,
            persons_available=0, adults_available=0, kits_available=0.0,
            food_available=0.0, food_needed=0.0, period_months=int(period_months),
            due_date=due_date,
        )
    capacity = tribe_muster_capacity(world, tribe, int(period_months))
    if str(policy) == "refuse":
        return TribeAnswer(
            tribe_id=tribe_id, call_date=call_date, status="refused",
            reason="tribe_decision", required_persons_count=required,
            persons_available=0, adults_available=int(capacity["adults"]),
            kits_available=float(capacity["kits"]),
            food_available=float(capacity["food"]),
            food_needed=float(capacity["food_per_person"] * required),
            period_months=int(period_months), due_date=due_date,
        )
    if capacity["adults"] < required:
        status, reason = "unable", "no_adults"
    elif capacity["by_kits"] < required:
        status, reason = "unable", "no_kits"
    elif capacity["by_food"] < required:
        status, reason = "unable", "no_food"
    else:
        status, reason = "accepted", "ok"
    available = required if status == "accepted" else int(capacity["capacity"])
    return TribeAnswer(
        tribe_id=tribe_id, call_date=call_date, status=status, reason=reason,
        required_persons_count=required, persons_available=available,
        adults_available=int(capacity["adults"]),
        kits_available=float(capacity["kits"]),
        food_available=float(capacity["food"]),
        food_needed=float(capacity["food_per_person"] * required),
        period_months=int(period_months), due_date=due_date,
    )

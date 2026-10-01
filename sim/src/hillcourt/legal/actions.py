"""Действия игрока, меняющие право/повинность/посылку (И-2).

Прямого приказа `Person` здесь нет: `send_party` создаёт `Pack`, а кто именно
пойдёт — решает двор. Право и повинность оформляются через `Right`/`Obligation`.
"""

from __future__ import annotations

from ..engine.hexgrid import tile_ids_are_neighbor
from ..info.sources import CHANNELS, EYE_FROM_HILL, MESSENGER
from ..news.propagation import make_report
from ..ontology import Household, Obligation, Pack, Right, SimDate, Stock
from ..world import World
from .bundles import bundle_of, materialize_obligations
from .obligations import (
    RENT_KIND,
    WORKS_BASIS,
    WORKS_KIND,
    WORKS_PREFIX,
    household_rent_due_base,
    rent_amount_for,
    rent_rate_for,
    rent_template,
    register_obligation,
    works_due_for,
    works_obligation_id,
    works_template,
)
from .regimes import can_be_sent, preset_of


def _log(world: World, action: str, **fields) -> dict:
    """Записать действие игрока в лог (`World.player_actions`)."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def _report(
    world: World,
    source: str,
    subject_kind: str,
    subject_id: str,
    content: str,
    facts: dict,
) -> None:
    """Породить Report по каналу источника: задержка и шум — из `info.sources`."""
    channel = CHANNELS[source]
    make_report(
        world,
        source=source,
        subject_kind=subject_kind,
        subject_id=subject_id,
        content=content,
        facts=facts,
        event_date=world.clock.date,
        delay_months=channel.delay_months,
        confidence=channel.confidence,
        noise=channel.noise,
    )


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _adjacent(origin: str, destination: str) -> bool:
    """Соседние ли клетки — вопрос задаёт `hexgrid`, а не этот файл (ADR 0203 §4).

    Локального расчёта здесь нет и не будет: манхэттен по координатам карты —
    это 4 из 6 направлений, то есть смешанная 4/6 топология. Она отвергала 26 %
    настоящих соседств на `start_stand` и 31 % на `v0_shire`, и на одних и тех же
    данных `send_party` отказывал там, где `send_sally` проходил. Долг перед
    `hexgrid` закрыт этой строкой; цикла импортов нет — `hexgrid` не знает ни о
    `legal`, ни о `world`.
    """
    return tile_ids_are_neighbor(origin, destination)


def send_party(
    world: World,
    household_id: str,
    member_ids: list[str],
    destination_tile_id: str,
    kind: str = "party",
    actor: str = "player",
) -> Pack:
    """Послать людей двором на соседнюю клетку; без pathfinding (только шаг).

    Послать можно только двор со статусом `can_be_sent`. Уходят взрослые члены
    этого двора; путь — один шаг до соседней клетки. Разбор похода — в
    `hillcourt.hazards.travel.resolve_packs`. Весть — гонцом с задержкой
    в обоих случаях; но в лог приказов игрока (`player_actions`) пишется только
    его собственный приказ: вылазка тэна (`actor="thegn"`) — не приказ барона.
    """
    household = world.households[household_id]
    if not can_be_sent(world, household):
        raise PermissionError(
            f"Двор '{household_id}' ({household.legal_status_id}) нельзя послать"
        )
    origin = household.current_tile_id
    if not _adjacent(origin, destination_tile_id):
        raise ValueError(
            f"Клетка '{destination_tile_id}' не соседняя с '{origin}' "
            f"(pathfinding в v0 запрещён)"
        )

    adults = {
        pid
        for pid in household.member_ids
        if world.persons.get(pid) is not None and world.persons[pid].age_class == "adult"
    }
    going = [pid for pid in member_ids if pid in adults]
    if not going:
        raise ValueError(f"У двора '{household_id}' не выбрано ни одного взрослого")

    date = world.clock.date
    number = world_pack_count(world) + 1
    cargo = Stock(
        id=f"pack:{number:04d}",
        owner_kind="pack",
        owner_id=f"pack_{number:04d}",
        amounts={},
    )
    world.add_stock(cargo)
    pack = Pack(
        id=f"pack_{number:04d}",
        kind=kind,
        origin_tile_id=origin,
        destination_tile_id=destination_tile_id,
        route=[origin, destination_tile_id],
        member_ids=going,
        cargo=cargo,
        departed_date=date,
        eta_date=date.advance(world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household_id,
    )
    world.packs[pack.id] = pack

    for pid in going:
        household.member_ids.remove(pid)
        world.persons[pid].location_tile_id = destination_tile_id
    household.labor_days = max(0.0, household.labor_days - 20.0 * len(going))
    if actor == "player":
        _log(
            world,
            "send_party",
            household=household_id,
            destination=destination_tile_id,
            members=list(going),
            pack=pack.id,
        )
    _report(
        world,
        MESSENGER,
        "pack",
        pack.id,
        f"Отряд послан из '{origin}' на клетку '{destination_tile_id}'",
        {"destination": destination_tile_id, "members": list(going)},
    )
    return pack


def world_pack_count(world: World) -> int:
    """Число созданных посылок; служит детерминированным счётчиком id."""
    return len(world.packs)


def _clear_obligations(world: World, household) -> None:
    for obligation_id in list(household.obligation_ids):
        world.obligations.pop(obligation_id, None)
    household.obligation_ids.clear()


def _set_status_from_preset(world: World, household, status_id: str) -> None:
    preset = world.catalogs.legal_statuses.get(status_id)
    if preset is None:
        raise ValueError(f"Неизвестный правовой пресет '{status_id}'")
    household.legal_status_id = status_id
    household.personal_status = preset.personal_status
    household.land_relation = preset.land_relation
    household.obligation_bundle = preset.obligation_bundle
    holding_scale = getattr(world.manor, "holding_tiles_by_land_kind", {})
    household.holding_scale = float(holding_scale.get(preset.land_kind, 1.0))


def found_works(
    world: World,
    lord_household_id: str,
    tile_id: str,
    kind: str,
    workers: float,
    works_id: str | None = None,
) -> tuple[Right, Obligation]:
    """Основать промысел: сеньор кладёт работников (ADR 0189).

    **Цена — люди и простой, не зерно**, поэтому промысел не покупается и не
    оплачивается: `due_good` пуст, а повинность называет, сколько работников
    сеньор обязан держать. Направление обратное `rent`/`labor_duty`/`levy`/
    `muster` («двор должен сеньору») — здесь сеньор должен промыслу, поэтому вид
    свой, а не переиспользованный.

    Правовая запись читается без новых полей онтологии:
      * **кто основал** — `household_id` (двор сеньора);
      * **где** — `right_id` → `Right.tile_id` (промысел держит клетку правом,
        как `grant_tenure` держит надел; право — не зерно и не `works_tiles`);
      * **сколько работников** — `due_amount`;
      * **с какого месяца** — идентичность записи в `id` (ADR 0185).

    Повинность **не** попадает в `household.obligation_ids`, ровно как запись о
    подаче (ADR 0169): фаза повинностей платит из стока, а здесь платить нечем —
    основание исполняется трудом промысла, а не зерном. Потому в книге запись
    видна, а фаза её не трогает и не начислит на неё «недоимку».
    """
    template = works_template(world)
    works_due_for(template)
    if float(workers) <= 0.0:
        raise ValueError(
            f"Основание промысла '{kind}': работников {workers} — основать нечем"
        )
    lord = world.households.get(lord_household_id)
    if lord is None:
        raise ValueError(f"Нет двора сеньора '{lord_household_id}'")
    if tile_id not in world.tiles:
        raise ValueError(f"Клетка '{tile_id}' не найдена")
    wid = works_id or f"{kind}_{tile_id}"
    right = Right(
        id=f"right_works_{wid}",
        holder_household_id=lord_household_id,
        tile_id=tile_id,
        kind="works",
        granted_date=world.clock.date,
        rent_share=0.0,
    )
    world.rights[right.id] = right
    obligation = Obligation(
        id=works_obligation_id(wid, world.clock.date),
        household_id=lord_household_id,
        kind=WORKS_KIND,
        due_good=None,
        due_amount=float(workers),
        period_months=template.period_months,
        paid_total=0.0,
        arrears=0.0,
        right_id=right.id,
        basis=WORKS_BASIS,
        corvee_days=0.0,
        duty_days=0.0,
    )
    world.obligations[obligation.id] = obligation
    _log(
        world,
        "found_works",
        works_id=wid,
        kind=kind,
        tile_id=tile_id,
        workers=float(workers),
        lord=lord_household_id,
        right=right.id,
        obligation=obligation.id,
    )
    return right, obligation


def move_works(
    world: World,
    obligation: Obligation,
    tile_id: str,
    kind: str,
    workers: float,
) -> tuple[Right, Obligation]:
    """Перевести промысел на другую клетку — это новое основание (ADR 0189).

    Хозяин прямо: «переезд торфопромысла — это по сути основание нового
    промысла». Значит и цена та же, и путь тот же: прежнее право и повинность
    снимаются, предъявляются заново. Промысел не телепортируется — он проходит
    то же основание, иначе переезд был бы бесплатным обходом закона.

    `kind` передаёт вызывающий, а **не вытаскивается разбором `id`**: строка
    идентификатора не должна быть носителем данных (ADR 0186), и разбор вида из
    неё уже давал бы мусор в id вроде `peat_t_06_t_07_01`.
    """
    if tile_id not in world.tiles:
        raise ValueError(f"Клетка '{tile_id}' не найдена")
    lord_id = obligation.household_id
    revoke_works(world, obligation)
    return found_works(world, lord_id, tile_id, kind, workers)


def revoke_works(world: World, obligation: Obligation) -> None:
    """Снять основание: право на клетку и повинность уходят вместе."""
    if obligation.right_id:
        world.rights.pop(obligation.right_id, None)
    world.obligations.pop(obligation.id, None)
    _log(world, "revoke_works", obligation=obligation.id, right=obligation.right_id)


def _add_to_manor_book(world: World, household) -> None:
    manor = world.manors.get(world.player_manor_id) if world.player_manor_id else None
    if manor is None:
        return
    if household.id not in manor.household_ids:
        manor.household_ids.append(household.id)
    household.manor_id = manor.id


def _manor_of_tile(world: World, tile_id: str):
    """Манор, в книге которого числится клетка (эталон — `engine/manor.manor_of_tile`)."""
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        if tile_id in manor.tile_ids:
            return manor
    return None


def set_tile_regime_pair(world: World, tile_id: str, regime_id: str) -> None:
    """Записать режим клетки в **оба** хранилища: клетку и книгу режимов.

    Два хранилища: `Tile.regime_id` (сама клетка, 10 писателей) и
    `Manor.tile_regimes` (книга режимов манора, 3 писателя). Эталон
    двуххранилищной записи — `set_tile_regime` (`engine/manor.py::set_tile_regime`).
    Номера строк здесь не приводятся: правка сдвигает их, а ссылка на имя
    переживает любой сдвиг.

    `grant_tenure` писал только клетку, а `_add_to_manor_book` — это книга
    **дворов**, не режимов, поэтому путь «выдал двору надел» в книгу режимов не
    попадал вовсе. Расхождение было не временным: книга обновлялась лишь при
    хирургии манора (`set_tile_regime`, пожалование тэна, отзыв), то есть на
    смену пресета по наделу отвечал **никто**.

    **Держателя эта функция не назначает — и не должна.** Назначение живёт в
    `engine/manor.py::appoint_regime_holder` и вызывается из `set_tile_regime`:
    приказ игрока обязан сказать, кому клетка теперь принадлежит (ADR 0198). Все
    вызывающие отсюда (`grant_tenure`, `revoke_tenure` через
    `_restore_revoked_tile`) право либо уже выдали, либо только что сняли, то
    есть держателя у них свой. Добавление назначения сюда выдало бы двору второе
    право поверх `grant_tenure`.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        return
    tile.regime_id = regime_id
    manor = _manor_of_tile(world, tile_id)
    if manor is not None:
        manor.tile_regimes[tile_id] = regime_id


def _restore_revoked_tile(world: World, household, tile_id: str) -> None:
    tile = world.tiles.get(tile_id)
    settlement = world.settlements.get(household.settlement_id or "")
    if tile is None or settlement is None:
        return
    if tile.settlement_id == settlement.id or tile_id in settlement.works_tiles:
        set_tile_regime_pair(
            world, tile_id, "demesne" if settlement.kind == "hill_court" else "tenement"
        )
    else:
        set_tile_regime_pair(world, tile_id, "waste")


def grant_tenure(
    world: World,
    household_id: str,
    tile_id: str,
    kind: str = "tenure",
    rent_share: float = 0.1,
    status_id: str | None = None,
    temporary: bool = False,
) -> Right:
    """Выдать право, применить пресет и материализовать его повинности.

    **`rent_share` — доля урожая, а не переключатель** (ADR 0206). Игрок нарезает
    ставку: `0.9` и `0.1` дают разный оброк, а `0.0` означает «оброк не
    заводится» — повинности оброка не будет вовсе. Доля измеряется в `[0, 1]`,
    потому что за пределами это уже не доля: больше урожая собрать нельзя, и
    счёт перестал бы быть достижимым. Каталожный `default_share` — умолчание
    для наделов без назначенной доли, а не потолок и не замена ставки.

    **Без вергельда надел не выдаётся** (ADR 0193): вергельд — способность отвечать за долг
    телом, а надел — право, за которое платит рента. Пресет с `wergeld: false` — это раб, и
    выдать ему право на чужое тело нельзя.
    """
    household = world.households[household_id]
    tile = world.tiles[tile_id]
    if float(rent_share) < 0.0 or float(rent_share) > 1.0:
        raise ValueError(
            f"Выдача надела: rent_share={rent_share} вне доли. Ставка задаётся в "
            f"[0.0, 1.0], где 0.0 — «оброк не заводится» (ADR 0206)"
        )
    status = preset_of(world, household)
    if not temporary and status is not None and not status.wergeld:
        raise PermissionError(
            f"Двор '{household_id}': пресет '{status.id}' без вергельда, а надел это "
            "право, за которое отвечают телом (ADR 0193)"
        )
    if temporary:
        if household.land_relation != "landless":
            raise PermissionError("Временный клочок выдаётся только безземельному")
        status = world.catalogs.legal_statuses.get("free_landless")
        if status is None:
            raise ValueError("Нет пресета free_landless")
    else:
        status_id = status_id or (
            "villein" if household.land_relation == "landless" else household.legal_status_id
        )
        status = world.catalogs.legal_statuses.get(status_id)
        if status is None:
            raise ValueError(f"Неизвестный правовой пресет '{status_id}'")
        if status.land_relation == "landless":
            raise PermissionError("Постоянное держание не может получить landless-пресет")
    right = Right(
        id=f"right_{household_id}_{tile_id}",
        holder_household_id=household_id,
        tile_id=tile_id,
        kind=kind,
        granted_date=world.clock.date,
        rent_share=rent_share,
    )
    world.rights[right.id] = right
    _clear_obligations(world, household)
    if temporary:
        household.legal_status_id = "free_landless"
        household.personal_status = status.personal_status
        household.land_relation = "landless"
        household.obligation_bundle = status.obligation_bundle
        household.holding_scale = 0.5
        set_tile_regime_pair(world, tile_id, "cotter_plot")
    else:
        _set_status_from_preset(world, household, status.id)
        regime = status.land_kind
        if regime not in world.catalogs.land_regimes:
            regime = "tenement"
        set_tile_regime_pair(world, tile_id, regime)
        _add_to_manor_book(world, household)
        materialize_obligations(world, household)
        bundle = bundle_of(world, household)
        has_fixed_rent = bool(
            bundle and "fixed_rent_grain_or_pence" in bundle.terms
        )
        if rent_share > 0.0 and not has_fixed_rent:
            _open_rent_obligation(world, household, right_id=right.id)
    _log(
        world,
        "grant_tenure",
        household=household_id,
        tile=tile_id,
        kind=kind,
        rent_share=rent_share,
        right=right.id,
        temporary=temporary,
    )
    _report(
        world,
        EYE_FROM_HILL,
        "right",
        right.id,
        f"Двору '{household_id}' выдано держание на клетку '{tile_id}'",
        {
            "household": household_id,
            "tile": tile_id,
            "kind": kind,
            "rent_share": rent_share,
            "temporary": temporary,
        },
    )
    return right


def revoke_tenure(
    world: World,
    household_id: str,
    right_id: str | None = None,
) -> list[str]:
    """Отозвать индивидуальные права и вернуть двор в free_landless."""
    household = world.households[household_id]
    if right_id is None:
        rights = [
            right
            for right in world.rights.values()
            if right.holder_household_id == household_id and right.kind != "common"
        ]
    else:
        right = world.rights.get(right_id)
        if right is None or right.holder_household_id != household_id:
            raise ValueError(f"Нет права '{right_id}' у двора '{household_id}'")
        if right.kind == "common":
            raise PermissionError("Общинное право не отзывается как индивидуальное")
        rights = [right]
    if not rights:
        raise ValueError(f"У двора '{household_id}' нет индивидуального права")
    revoked: list[str] = []
    for right in sorted(rights, key=lambda item: item.id):
        world.rights.pop(right.id, None)
        _restore_revoked_tile(world, household, right.tile_id)
        revoked.append(right.id)
    _clear_obligations(world, household)
    status = world.catalogs.legal_statuses.get("free_landless")
    if status is None:
        raise ValueError("Нет пресета free_landless")
    household.legal_status_id = "free_landless"
    household.personal_status = status.personal_status
    household.land_relation = "landless"
    household.obligation_bundle = status.obligation_bundle
    household.holding_scale = 1.0
    for manor in world.manors.values():
        if household.id in manor.household_ids:
            manor.household_ids.remove(household.id)
    household.manor_id = None
    _log(world, "revoke_tenure", household=household_id, rights=revoked)
    for rid in revoked:
        _report(
            world,
            EYE_FROM_HILL,
            "right",
            rid,
            f"У двора '{household_id}' отозвано право '{rid}'",
            {"household": household_id, "right": rid},
        )
    return revoked


def _register_obligation(
    world: World,
    household_id: str,
    kind: str,
    due_good: str | None,
    due_amount: float,
    period_months: int,
    basis: str,
    corvee_days: float,
    right_id: str | None,
) -> Obligation:
    """Записать повинность в книгу, в лог приказов и в вести — одна запись.

    Общая точка для публичного приказа и внутренних путей. Внутренние пути
    (выдача надела) обязаны пользоваться ею, а не подменять запись: иначе в
    `player_actions` появилась бы повинность, которой игрок не заказывал, и по
    ADR 0202 он не смог бы отличить свой приказ от последствия чужого решения.
    """
    obligation = Obligation(
        id=f"obl_{household_id}_{kind}",
        household_id=household_id,
        kind=kind,
        due_good=due_good,
        due_amount=due_amount,
        period_months=period_months,
        paid_total=0.0,
        arrears=0.0,
        right_id=right_id,
        basis=basis,
        corvee_days=corvee_days,
    )
    obligation = register_obligation(
        world, world.households[household_id], obligation
    )
    _log(
        world,
        "add_obligation",
        household=household_id,
        kind=kind,
        due_good=due_good,
        due_amount=due_amount,
        obligation=obligation.id,
    )
    _report(
        world,
        EYE_FROM_HILL,
        "obligation",
        obligation.id,
        f"Двору '{household_id}' оформлена повинность '{kind}'",
        {
            "household": household_id,
            "kind": kind,
            "due_good": due_good,
            "due_amount": due_amount,
            "obligation": obligation.id,
        },
    )
    return obligation


def _is_rent_share_order(kind: str, basis: str) -> bool:
    """Вид «оброк-доля» ли это: тот самый, чью сумму мир выводит каждый месяц."""
    return kind == RENT_KIND and basis == "share"


def _open_rent_obligation(
    world: World, household, right_id: str | None, corvee_days: float = 0.0
) -> Obligation:
    """Завести оброк по праву: вид, сумма и ставка — всё из закона, не от приказа.

    Сумма выводится здесь, а не принимается числом: оброк пересчитывается
    помесячно (ADR 0185, ADR 0192), и число в приказе продержалось бы не дольше
    месяца. Ставка — доля права (ADR 0206), каталог при отсутствии права.

    Путь внутренний (`grant_tenure` вызывает его сам), поэтому приказ игрока с
    числом оброка и этот путь должны оставаться разными сущностями: иначе
    приказ снова станет числом, которое ни на что не влияет.
    """
    template = rent_template(world)
    if template.kind != RENT_KIND or template.basis != "share":
        raise ValueError(
            f"Шаблон '{template.id}': kind '{template.kind}'/basis '{template.basis}', "
            f"а оброк — {RENT_KIND}/share (ADR 0183)"
        )
    stub = Obligation(
        id="",
        household_id=household.id,
        kind=template.kind,
        due_good=template.due_good,
        due_amount=0.0,
        period_months=template.period_months,
        paid_total=0.0,
        arrears=0.0,
        right_id=right_id,
        basis=template.basis,
        corvee_days=corvee_days,
    )
    rate = rent_rate_for(world, stub)
    obligation = _register_obligation(
        world,
        household.id,
        template.kind,
        template.due_good,
        rent_amount_for(
            template, household_rent_due_base(world, household), rate=rate
        ),
        template.period_months,
        template.basis,
        corvee_days,
        right_id,
    )
    return obligation


def add_obligation(
    world: World,
    household_id: str,
    kind: str,
    due_good: str | None,
    due_amount: float | None = None,
    period_months: int = 1,
    basis: str = "share",
    corvee_days: float = 0.0,
    right_id: str | None = None,
) -> Obligation:
    """Оформить повинность двора (фиксированный мешок, трудовая повинность, оброк).

    **Вид «рента-доля» суммы от приказа не принимает** (ADR 0206). Её размер —
    производная: ставка права × урожай прошлого месяца, пересчитывается ежемесячно
    (ADR 0185, ADR 0192). Приказ, который принимает `due_amount=100` и через месяц
    платит каталожную сумму, врёт игроку ровно тем же, чем врал `rent_share` в
    ADR 0204: показывает число, которым нельзя управлять. Поэтому:

    * `due_amount` вида «рента-доля» — **отказ** (`ValueError`), а не молчаливая
      выброшенная величина: игрок узнаёт причину в том же тике, в котором его
      обманули;
    * без суммы оброк заводится по праву (`_open_rent_obligation`), и ставку
      игрок задаёт там, где она и живёт, — в `rent_share` приказа `grant_tenure`;
    * остальные виды сумму принимают и считают как задано: там число и есть
      долг, а не производная (ADR 0131, ADR 0149).
    """
    if _is_rent_share_order(kind, basis):
        if due_amount is not None:
            raise ValueError(
                f"Приказ повинности '{kind}' для двора '{household_id}': сумма "
                f"due_amount={due_amount} не принимается. Оброк — доля урожая, его "
                "размер выводится каждый месяц из ставки права и урожая прошлого "
                "месяца. Задайте ставку приказом grant_tenure (rent_share) "
                "(ADR 0206)"
            )
        household = world.households[household_id]
        return _open_rent_obligation(world, household, right_id, corvee_days=corvee_days)
    if due_amount is None:
        raise ValueError(
            f"Приказ повинности '{kind}' для двора '{household_id}': не задана сумма "
            "due_amount. Этот вид считает сумму, а не долю, — без неё нечего платить "
            "(ADR 0206)"
        )
    return _register_obligation(
        world,
        household_id,
        kind,
        due_good,
        float(due_amount),
        period_months,
        basis,
        corvee_days,
        right_id,
    )

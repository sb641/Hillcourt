"""Выбор тэна: комплекты раньше зерна, вылазки только на своё кольцо.

Тэн — не богатый виллан: при закрытом голоде и незакрытой норме комплектов
он тратит доступный излишек на комплекты (сборка `smith_kit` из амбарного
железа) или просит железа, а не копит зерно. При открытом голоде фьефа
комплекты не собираются — сначала еда. Угроза на его кольце (опасность или
срыв воза) тянет вылазку на СВОЮ кромку через ту же `send_party`, что у
корня; дальние чужие клетки тэна не касаются — он их не видит слухом.
Боевого движка нет: исход вылазки решают существующие правила hazard.

Всё читается книгой манора, двором, стоком, повинностью, pack и известием:
сборка — проводки `Ledger` (перевод/выдача из пула рецепта), вылазка — `Pack`
вида `party`, весть корню — по старым каналам (`messenger`/`silence`).
"""

from __future__ import annotations

from ..engine.hexgrid import neighbor_ids
from ..engine.manor import (
    KIT_GOOD,
    KIT_STOCK,
    KIT_MIN_HOLDER,
    fief_kits,
    guard_ring,
    holder_household,
    holder_kits,
    manor_stock,
    nested_manors,
)
from ..hazards.model import strongest_hazard
from ..info.sources import CHANNELS, MESSENGER
from ..legal.actions import send_party
from ..news.propagation import make_report
from ..ontology import SimDate
from ..world import World
from .needs import food_months

EPSILON = 1e-9
KIT_RECIPE = "smith_kit"
FOOD_SECURE_MONTHS = 1.0
SORTIE_CHANCE = 0.5
PACK_LOSS_WEIGHT = 0.5


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _perceived_tiles(world: World, dwelling_tile_id: str) -> set[str]:
    """Что двор узнал слухом сам: своя клетка и непосредственные соседи.

    Соседство — общий хелпер гекс-сетки `engine/hexgrid.py` (ADR 0071).
    """
    seen = {dwelling_tile_id}
    tile = world.tiles.get(dwelling_tile_id)
    if tile is None:
        return seen
    seen.update(neighbor_ids(world, tile))
    return seen


def _take_for_kit(world: World, barn, holder_stock, good: str, need: float, date: SimDate) -> bool:
    """Взять `need` товара из амбара, затем из стока двора, в пул обработки."""
    processing = world.get_stock("sink:processing")
    remaining = need
    for source in (barn, holder_stock):
        if remaining <= EPSILON:
            break
        if source is None:
            continue
        available = source.amounts.get(good, 0.0)
        take = min(available, remaining)
        if take > EPSILON:
            world.ledger.transfer(source, processing, good, take, KIT_RECIPE, date)
            remaining -= take
    return remaining <= EPSILON


def _assemble_one(world: World, manor, barn, holder, date: SimDate) -> bool:
    """Собрать один комплект в амбар фьефа по рецепту каталога.

    Железо не появляется магией: берётся из стоков книги (амбар, затем двор
    держателя). Труд — руки держателя (его пул всё равно не пашет). Не больше
    одного комплекта в месяц на фьеф: служба закрывается постепенно, и бедный
    фьеф честно отстанет от живого.
    """
    recipe = world.catalogs.recipes.get(KIT_RECIPE)
    if recipe is None or barn is None or holder is None:
        return False
    if holder.labor_days + EPSILON < recipe.labor_days:
        return False
    holder_stock = world.get_stock(holder.stock_id)
    for good, amount in sorted(recipe.inputs.items()):
        if barn.amounts.get(good, 0.0) + holder_stock.amounts.get(good, 0.0) + EPSILON < amount:
            return False
    for good, amount in sorted(recipe.inputs.items()):
        if not _take_for_kit(world, barn, holder_stock, good, amount, date):
            return False
    processing = world.get_stock("sink:processing")
    allowed = set(recipe.outputs) | set(recipe.loss)
    for good, amount in sorted(recipe.outputs.items()):
        if amount > 0:
            world.ledger.emit(processing, barn, good, amount, KIT_RECIPE, date, allowed)
    waste = world.get_stock("sink:waste")
    for good, amount in sorted(recipe.loss.items()):
        if amount > 0:
            world.ledger.emit(processing, waste, good, amount, KIT_RECIPE, date, allowed)
    if abs(processing.total()) >= 1e-6:
        raise AssertionError(f"Рецепт '{KIT_RECIPE}' не сбалансирован в амбаре")
    holder.labor_days = max(0.0, holder.labor_days - recipe.labor_days)
    world.bump("thegn_kits")
    return True


def _issue_to_holder(world: World, manor, barn, holder, date: SimDate) -> bool:
    """Выдать комплект с собственного стола на держателя (рычаг тэна).

    Держатель без комплекта — мужик с кольём: пока на нём нет минимума,
    `mustered` ложен. Выдача — перевод внутри книги, не создание материи.
    """
    if holder is None or barn is None:
        return False
    if holder_kits(world, manor) + EPSILON >= KIT_MIN_HOLDER:
        return False
    if barn.amounts.get(KIT_GOOD, 0.0) + EPSILON < KIT_MIN_HOLDER:
        return False
    world.ledger.transfer(
        barn, world.get_stock(holder.stock_id), KIT_GOOD, KIT_MIN_HOLDER,
        "issue_kit", date,
    )
    return True


def _fief_iron(world: World, manor, barn, holder) -> float:
    """Крица книги фьефа: амбар + двор держателя (сырьё комплектов)."""
    total = 0.0
    if barn is not None:
        total += float(barn.amounts.get(KIT_STOCK, 0.0))
    if holder is not None:
        total += float(
            world.get_stock(holder.stock_id).amounts.get(KIT_STOCK, 0.0)
        )
    return total


def _threat_weights(world: World, manor, holder, date: SimDate) -> dict[str, float]:
    """Угрозы, которые тэн узнал слухом на СВОЁМ кольце.

    Видит только свою кромку (своя клетка + соседи): дальняя чужая клетка
    корня сюда не попадает никогда — всеведения карты нет. Вес опасности —
    напор (популяция × интенсивность), вес срыва воза — половина комплекта.
    """
    ring = set(guard_ring(world, manor))
    seen = _perceived_tiles(world, holder.current_tile_id)
    rim = sorted((ring & seen) - {holder.current_tile_id})
    weights: dict[str, float] = {}
    for tile_id in rim:
        hazard = strongest_hazard(world, tile_id)
        if hazard is not None:
            weights[tile_id] = max(
                weights.get(tile_id, 0.0),
                float(hazard.population) * float(hazard.intensity),
            )
    for pack in world.packs.values():
        if pack.lost_date != date or pack.status != "lost":
            continue
        if pack.destination_tile_id in weights or pack.destination_tile_id in rim:
            weights[pack.destination_tile_id] = max(
                weights.get(pack.destination_tile_id, 0.0), PACK_LOSS_WEIGHT
            )
    return weights


def _maybe_sortie(world: World, manor, holder, date: SimDate) -> str | None:
    """Послать себя/серванта малым pack на свою кромку при угрозе.

    Та же `send_party`, что у корня, только радиус меньше: цель — клетка
    кольца, которую двор узнал слухом. Весть корню — по старым правилам
    (`messenger` при отправке, `messenger`/`silence` по исходу), мгновенной
    истины вылазки корень не получает.
    """
    adults = sorted(
        (
            pid
            for pid in holder.member_ids
            if (p := world.persons.get(pid)) is not None and p.age_class == "adult"
        ),
        # Ротация, а не лотерея вдовства: сначала серванты, держатель — последним.
        key=lambda pid: (pid == manor.holder_person_id, pid),
    )
    if not adults:
        return None
    if holder.hunger_days > 0:
        return None
    if holder_kits(world, manor) + EPSILON < KIT_MIN_HOLDER:
        return None
    weights = _threat_weights(world, manor, holder, date)
    if not weights:
        return None
    best = max(weights.values())
    destination = sorted(tid for tid, w in weights.items() if w == best)[0]
    if world.rng.world.random() >= SORTIE_CHANCE:
        return None
    send_party(world, holder.id, [adults[0]], destination, kind="party", actor="thegn")
    world.bump("thegn_sorties")
    return destination


def thegn_month(world: World, date: SimDate) -> None:
    """Месяц выбора каждого живого тэна: еда → комплект → кромка → земля.

    Порядок дыр в голове тэна: (1) двор не мрёт — при голоде ни сборки, ни
    вылазки; (2) комплект лучше мужика с кольём — сборка и выдача при закрытом
    голоде; (3) угроза на кольце — вылазка вооружённым; (4) улучшение земли —
    обычным трудом тяглых (уже идёт фазами тика, здесь не трогаем).
    """
    for manor in nested_manors(world):
        holder = holder_household(world, manor)
        if holder is None or holder.left_at is not None:
            continue
        person = world.persons.get(manor.holder_person_id)
        if person is None or person.health <= 0:
            continue
        barn = manor_stock(world, manor)
        # Крица двора — в казну фьефа, пока мелкое действие не нашло ей иного
        # дела: комплекты собираются из амбара.
        if barn is not None:
            holder_stock = world.get_stock(holder.stock_id)
            iron = holder_stock.amounts.get(KIT_STOCK, 0.0)
            if iron > EPSILON:
                world.ledger.transfer(
                    holder_stock, barn, KIT_STOCK, iron, "thegn_warchest", date
                )
        hungry = holder.hunger_days > 0
        secure = not hungry and food_months(world, holder) >= FOOD_SECURE_MONTHS
        required = float(manor.service_kits_required)
        assembled = False
        if secure and fief_kits(world, manor) + EPSILON < required:
            assembled = _assemble_one(world, manor, barn, holder, date)
        issued = _issue_to_holder(world, manor, barn, holder, date)
        short = fief_kits(world, manor) + EPSILON < required
        if secure and short and _fief_iron(world, manor, barn, holder) < 1.0 - EPSILON:
            if not manor.iron_requested:
                manor.iron_requested = True
                world.manor_log.append(
                    {"date": str(date), "thegn_iron_request": manor.id}
                )
                # Барон просьбу слышит гонцом с задержкой, а не чтением книги:
                # без вести его рычаг выдачи слеп.
                channel = CHANNELS[MESSENGER]
                make_report(
                    world,
                    MESSENGER,
                    "manor",
                    manor.id,
                    f"Тэн {manor.id} просит железа: комплектов "
                    f"{fief_kits(world, manor):.0f} из {required:.0f}.",
                    {
                        "kits_held": round(fief_kits(world, manor), 3),
                        "kits_required": required,
                    },
                    date,
                    channel.delay_months,
                    channel.confidence,
                    distorted=False,
                    noise=channel.noise,
                )
        elif not short:
            manor.iron_requested = False
        sortie = _maybe_sortie(world, manor, holder, date)
        world.manor_log.append(
            {
                "date": str(date),
                "thegn": {
                    manor.id: {
                        "kits_held": round(fief_kits(world, manor), 3),
                        "assembled": assembled,
                        "issued": issued,
                        "iron_requested": manor.iron_requested,
                        "sortie": sortie,
                    }
                },
            }
        )

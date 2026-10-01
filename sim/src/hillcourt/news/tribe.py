"""Племенные формулировки вестей: `Settlement.kind=native_village` (ADR 0059/0060).

Племя — данные, а не скрытый маркер (ADR 0060, уточняет 0059): источник истины
`Settlement.kind == "native_village"`; старый маркер `tribal` на клетке остаётся
подсказкой вида и текста вести не определяет. Тип знания не новый: тот же
`Report`, те же поля, те же числа (`facts`) — меняется только формулировка
`content` (обычай, обмен, гости), когда канал уже принёс весть о клетке.

Каналы (0059): `eye_from_hill` в радиусе глаза, `adjacent_daily`, `caravan`,
`silence`. Новых каналов, задержек и полей онтологии нет: задержка,
`confidence`, `noise` и `facts` — свойства вызывающего канала, они проходят
через без изменений. Прочие источники (в том числе `messenger` о ворогах или
новой тропе) не трогаются: их рассказ не о племени.

Граница с продюсерами (правки чужих папок не делаем): вызывающая сторона
(`engine/seat.py` — глаз, `info/briefing.py` — сосед/обоз/молчание о деревне)
передаёт свой обычный текст и свои параметры в `make_village_report`; племенная
формулировка подставляется только при `kind == "native_village"`. `known_tiles`
не расширяется: весть приходит обычным `Report` существующего канала, а племя
само по себе клетку в знание не тащит (И-3).
"""

from __future__ import annotations

from ..economy.tribe import TribeAnswer
from ..info.sources import ADJACENT_DAILY, CARAVAN, EYE_FROM_HILL, MESSENGER, SILENCE
from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report

NATIVE_KIND = "native_village"
TRIBE_CHANNELS = (EYE_FROM_HILL, ADJACENT_DAILY, CARAVAN, SILENCE)


def native_settlement(world: World, tile_id: str):
    """Поселение племени на клетке или None (вид — данные, не маркер)."""
    tile = world.tiles.get(tile_id)
    if tile is None or tile.settlement_id is None:
        return None
    settlement = world.settlements.get(tile.settlement_id)
    if settlement is None or settlement.kind != NATIVE_KIND:
        return None
    return settlement


def is_native_tile(world: World, tile_id: str) -> bool:
    """Племенная ли клетка по виду поселения (без чтения маркера `tribal`)."""
    return native_settlement(world, tile_id) is not None


def _grain_approx(facts: dict) -> float:
    value = facts.get("grain_approx", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def tribe_content(
    world: World,
    tile_id: str,
    source: str,
    content: str,
    facts: dict | None = None,
) -> str:
    """Племенная формулировка канала или исходный текст без изменений.

    Не племенная клетка, не канал племенных вестей или не тот факт, который
    канал несёт (молчание зерна не несёт) — возвращаем `content` как есть.
    """
    settlement = native_settlement(world, tile_id)
    if settlement is None or source not in TRIBE_CHANNELS:
        return content
    facts = facts or {}
    name = settlement.name
    if source == SILENCE:
        return f"Из племенной деревни '{name}' вестей нет."
    grain = _grain_approx(facts)
    if source == EYE_FROM_HILL:
        return (
            f"Видно с холма: на клетке '{tile_id}' племенная деревня '{name}' — "
            f"обычай держат, зерна около {grain:.0f}."
        )
    if source == ADJACENT_DAILY:
        return (
            f"Сосед племени '{name}': обычай такой, зерна, сказывают, "
            f"около {grain:.0f}."
        )
    return (
        f"Обоз сказывает: в племенной деревне '{name}' обмен идёт, зерна около "
        f"{grain:.0f}."
    )


def make_village_report(
    world: World,
    source: str,
    tile_id: str,
    date: SimDate,
    content: str,
    facts: dict,
    delay_months: int,
    confidence: float,
    noise: float = 0.0,
    distorted: bool = False,
) -> Report:
    """Родить весть существующего канала о клетке; племени — племенный текст.

    Формат, канал, задержка, `confidence`, `noise`, `distorted` и `facts` —
    вызывающего канала (без изменений); для `Settlement.kind=native_village`
    меняется только `content`. Тот же `Report`, то же знание, тот же `about`
    (`tile_id`): ни поля, ни канала, ни задержки не добавлено.
    """
    return make_report(
        world,
        source,
        "tile",
        tile_id,
        tribe_content(world, tile_id, source, content, facts),
        dict(facts),
        date,
        delay_months,
        confidence,
        distorted=distorted,
        noise=noise,
    )


TRIBE_ANSWER_DELAY_DAYS = 15
TRIBE_ANSWER_NOISE = 0.1
TRIBE_ANSWER_TEXTS: dict[str, tuple[str, ...]] = {
    "accepted": (
        "Племя приняло вызов и обещало выслать требуемых людей.",
        "Староста сообщает: вызов племя принимает, отряд будет собран.",
    ),
    "unable": (
        "Племя сообщает: принять вызов сейчас не может, сил не хватает.",
        "Староста отвечает, что племя не может выставить требуемый отряд.",
    ),
    "refused": (
        "Племя отказалось принять вызов.",
        "Совет племени сообщает: вызов отклонён по решению совета.",
    ),
}


def make_tribe_answer_report(
    world: World,
    answer: TribeAnswer,
    delay_days: int = TRIBE_ANSWER_DELAY_DAYS,
) -> Report:
    """Создать весть о явно заданном ответе племени с честным наблюдателем.

    Ответ — уже решение племени: числа и статус копируются в `facts`, а
    `call_status` повинности не читается. Доставка идёт отдельным каналом через
    `delay_days`; формулировка и шум выбираются только потоком `rng.news`.
    """
    if answer.status not in TRIBE_ANSWER_TEXTS:
        raise ValueError(f"Неизвестный ответ племени '{answer.status}'")
    delivery_date = answer.call_date.advance_days(int(delay_days))
    text = str(world.rng.news.choice(TRIBE_ANSWER_TEXTS[answer.status]))
    noise = world.rng.news.uniform(0.0, TRIBE_ANSWER_NOISE)
    report = make_report(
        world,
        MESSENGER,
        "tribe",
        answer.tribe_id,
        text,
        {
            "subject": "tribe_muster",
            "tribe_id": answer.tribe_id,
            "answer": answer.status,
            "reason": answer.reason,
            "required_persons_count": int(answer.required_persons_count),
            "persons_available": int(answer.persons_available),
            "adults_available": int(answer.adults_available),
            "kits_available": float(answer.kits_available),
            "food_available": float(answer.food_available),
            "food_needed": float(answer.food_needed),
            "period_months": int(answer.period_months),
            "due_date": str(answer.due_date),
            "delay_days": int(delay_days),
        },
        answer.call_date,
        0,
        0.5,
        distorted=False,
        noise=noise,
        observer_id=answer.tribe_id,
    )
    report.delivery_date = delivery_date
    return report

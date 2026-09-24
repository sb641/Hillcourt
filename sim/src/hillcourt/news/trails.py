"""Весть о новой тропе: глаз видит, даль молчит (этап троп, цикл 27).

Механика троп (уровни тропа → грунтовка → строеная, профили хода, память
возниц — зона Implementer) метит каждую новорождённую тропу для Info: штампы
месяца `trail_born_<tile_id>` и `dirt_born_<tile_id>` в `world.stats`
(`year * 12 + month`); тропы не было — метки нет. Весть рождает эта сторона
в `phase_inform` по метке текущего месяца. Без вести тропа невидима.

Граница с механикой (согласована чтением `engine/seat.py` и
`engine/path.py`, правок чужого нет). Строка хозяина:
- в радиусе глаза (`engine/seat.py::eye_tiles`, манхэттен 1 от зала) —
  `eye_from_hill` с задержкой 0 (`confidence` 1.0, `noise` 0.0: глаз точен,
  как глазные отчёты `make_seat_eye_reports`), формулировки — здешние;
- вдали — молчание: отчёта нет, пока свой воз не пройдёт (проход освещают
  существующие отчёты обоза о рейсе `subject_kind="route"` и память дорог —
  новых отчётов не требуется);
- тропа не расширяет `known_tiles` (`engine/path.py` читает только
  доставленные `Report`): дальняя клетка без вести в знание не попадает —
  знание без вести запрещено (И-3). Глазные клетки и так известны глазу.

Новых каналов/задержек/сущностей нет. Материю весть не двигает (И-1):
тропа — состояние ходьбы, не вещество.
"""

from __future__ import annotations

from ..info.sources import EYE_FROM_HILL
from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report


def _eye_tiles(world: World) -> set[str]:
    """Клетки радиуса глаза без почты (чтение `engine/seat.py`, не правка)."""
    from ..engine.seat import eye_tiles

    return set(eye_tiles(world))


def make_trail_report(world: World, tile_id: str, date: SimDate) -> Report:
    """Родить глазную весть о новой тропе; вернуть рождённый Report.

    Канал — существующий `eye_from_hill`, задержка 0, `confidence` 1.0,
    `noise` 0.0 (глаз точен). Рассказ — о клетке (`subject_kind="tile"`,
    `subject_id=tile_id`, `facts['tile']` = about по правилу tile-about).
    Факт — присутствие тропы (`trail: True`): уровень и профиль — дело
    механики, слух несёт только «хоженая земля».
    """
    return make_report(
        world,
        EYE_FROM_HILL,
        "tile",
        tile_id,
        f"Видно с холма: к клетке '{tile_id}' натоптана тропа — ходят.",
        {"trail": True, "tile": tile_id},
        date,
        0,
        1.0,
        distorted=False,
        noise=0.0,
    )


def report_new_trails(world: World) -> list[Report]:
    """Родить вести по свежим меткам троп; вернуть рождённые Report.

    Вызывается из `phase_inform`. Метка со штампом текущего месяца —
    новорождённая тропа: в глазу — весть, вдали — молчание (ничего не рождаем,
    клетка в `known_tiles` не попадает). Прошлые штампы не трогаем, повторный
    вызов в том же месяце не дублирует (та же весть о тропе уже рождена;
    месячный глазной отчёт о зерне дедупу не мешает — новость другая).
    """
    date = world.clock.date
    stamp = float(world.clock.year * 12 + world.clock.month)
    try:
        eye = _eye_tiles(world)
    except (ImportError, AttributeError):  # pragma: no cover — глаз всегда собран
        eye = set()
    produced: list[Report] = []
    for tile_id in sorted(world.tiles):
        trail_born = world.stats.get(f"trail_born_{tile_id}") == stamp
        dirt_born = world.stats.get(f"dirt_born_{tile_id}") == stamp
        if not trail_born and not dirt_born:
            continue
        if tile_id not in eye:
            continue
        if any(
            report.source == EYE_FROM_HILL
            and report.subject_id == tile_id
            and report.event_date == date
            and report.facts.get("trail")
            for report in world.reports
        ):
            continue
        produced.append(make_trail_report(world, tile_id, date))
    return produced

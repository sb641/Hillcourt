"""Вести о новых угрозах: топ-ап стаи и рождение ватаги (ADR 0042/0045).

Топ-ап (`wolves_den`, `mode: topup`): фаза (`phase_hazard` →
`_apply_hazard_topups`, зона Implementer) дотягивает СУЩЕСТВУЮЩИХ волков.
Рождение (`band_camp`, `mode: spawn`): фаза рождает НОВУЮ стоячую ватагу на
топи за гейтом (`terrain: marsh` + `min_households: 3`, `intensity_init` 1.0 /
`population_init` 3.0, потолки `intensity_cap` 1.0 / `population_cap` 6.0).
Оба правила требуют `report_required: true`: угроза без вести врёт И-3,
поэтому каждое срабатывание фаза метит для Info, а весть рождает эта сторона
в `phase_inform` по метке. Без вести фаза не едет — это и есть гейт.

Граница с фазой (согласована чтением `engine/tick.py`, правок чужого нет):
метки — счётчик и штамп месяца в `world.stats` (`year * 12 + month`):
топ-ап — `wolves_den_topup` / `wolves_den_topup_<hazard_id>` (ADR 0043);
рождение — `<rule_id>_birth` / `<rule_id>_birth_<hazard_id>` (фаза читает
ответ: рождение откатывается, если читатель не вернул весть о клетке с датой
сегодня — наказ Critic «рождение только с вестью» исполнен структурно).
Пометки, которые ждёт эта сторона: штамп текущего месяца —
`report_pending_topups(world)` (топ-ап) вызывается из `phase_inform`;
`report_pending_births(world)` (рождения) фаза зовёт сама сразу после метки
и проверяет ответ. Фаза бросает вероятность своим потоком `rng.hazard`, а
вести шумят только потоком `rng.news` (поток `hazard` не трогаем —
детерминизм И-6 и хеши канона целы). Материю вести не двигают: числа угроз —
скаляры, не вещество (И-1).
"""

from __future__ import annotations

from ..info.sources import MESSENGER
from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report

NOISE_FOR_THREAT = 0.15


def make_threat_report(world: World, hazard, date: SimDate) -> Report:
    """Родить весть о подросшей угрозе; вернуть рождённый Report.

    Канал — существующий `messenger` (гонец/шериф), задержка 0 (событийная
    весть, как отчёты об уходе/прибытии — новых задержек нет), `confidence`
    0.5, `noise` 0.15. Рассказ — о клетке (`subject_kind="tile"`,
    `subject_id=tile_id`, `facts['tile']` = about по правилу tile-about, а не
    координата кого-либо). Численность — слуховая (`population_approx` ±15 %
    потоком `rng.news`, как счёт гонца; `distorted`, когда не сошлось), вид —
    точный (`kind`), чтобы `rumor.perceived_risk` считал по слуху, а не по
    истинному `Hazard` (И-3).
    """
    tile_id = hazard.tile_id
    reported = float(hazard.population) * (
        1.0 + world.rng.news.uniform(-NOISE_FOR_THREAT, NOISE_FOR_THREAT)
    )
    return make_report(
        world,
        MESSENGER,
        "tile",
        tile_id,
        f"Гонец сказывает: на клетке '{tile_id}' стая '{hazard.kind}' "
        f"подросла — волков около {max(0.0, reported):.0f}.",
        {
            "hazard": {
                "kind": hazard.kind,
                "population_approx": round(reported, 2),
            },
            "tile": tile_id,
        },
        date,
        0,
        0.5,
        distorted=abs(reported - float(hazard.population)) > 1e-9,
        noise=NOISE_FOR_THREAT,
    )


def make_birth_report(world: World, hazard, date: SimDate) -> Report:
    """Родить весть о народившейся угрозе; вернуть рождённый Report.

    Та же форма, что у вести топ-апа (`make_threat_report`): канал
    `messenger`, задержка 0, `confidence` 0.5, `noise` 0.15, рассказ о клетке
    (`tile` = about), численность слуховая (`population_approx` ±15 % потоком
    `rng.news`), вид точный. Отличается только текст: угроза объявилась, а не
    подросла — игрок читает рождение, а не дрейф чисел.
    """
    tile_id = hazard.tile_id
    reported = float(hazard.population) * (
        1.0 + world.rng.news.uniform(-NOISE_FOR_THREAT, NOISE_FOR_THREAT)
    )
    return make_report(
        world,
        MESSENGER,
        "tile",
        tile_id,
        f"Гонец сказывает: на клетке '{tile_id}' засела новая шайка "
        f"'{hazard.kind}' — лихих людей около {max(0.0, reported):.0f}.",
        {
            "hazard": {
                "kind": hazard.kind,
                "population_approx": round(reported, 2),
            },
            "tile": tile_id,
        },
        date,
        0,
        0.5,
        distorted=abs(reported - float(hazard.population)) > 1e-9,
        noise=NOISE_FOR_THREAT,
    )


def _already_reported(world: World, tile_id: str, date: SimDate) -> bool:
    """Уже рождалась ли весть о клетке с датой события — не дублировать."""
    return any(
        report.source == MESSENGER
        and report.subject_id == tile_id
        and report.event_date == date
        for report in world.reports
    )


def report_pending_topups(world: World) -> list[Report]:
    """Родить вести по свежим меткам топ-апа; вернуть рождённые Report.

    Вызывается из `phase_inform` того же месяца, что и `phase_hazard` (порядок
    фаз тика): метка со штампом текущего месяца — подросшая угроза без вести,
    рождаем `make_threat_report`. Прошлые штампы не трогаем (опоздавшая
    весть — тоже враньё), повторный вызов в том же месяце не дублирует.
    Роста не было — вести нет: тишина, а не «всё хорошо».
    """
    date = world.clock.date
    stamp = float(world.clock.year * 12 + world.clock.month)
    produced: list[Report] = []
    for hazard_id in sorted(world.hazards):
        if world.stats.get(f"wolves_den_topup_{hazard_id}") != stamp:
            continue
        hazard = world.hazards.get(hazard_id)
        if hazard is None or not hazard.active:
            continue
        if _already_reported(world, hazard.tile_id, date):
            continue
        produced.append(make_threat_report(world, hazard, date))
    return produced


def report_pending_births(world: World) -> list[Report]:
    """Родить вести по свежим меткам рождений; вернуть рождённые Report.

    Зовёт фаза сразу после метки (`_apply_hazard_spawns`) и проверяет ответ:
    весть о клетке рождения с датой сегодня обязана быть в нём, иначе фаза
    откатывает рождение. Сканируем все правила `mode: spawn` с
    `report_required` (чтение каталога, не правка): метка `<rule_id>_birth_
    <hazard_id>` со штампом текущего месяца — народившаяся угроза без вести,
    рождаем `make_birth_report`. Прошлые штампы и дубли игнорируем, как выше.
    """
    date = world.clock.date
    stamp = float(world.clock.year * 12 + world.clock.month)
    produced: list[Report] = []
    for rule_id in sorted(world.catalogs.spawn_rules):
        rule = world.catalogs.spawn_rules[rule_id]
        if rule.target != "hazard" or rule.params.get("mode") != "spawn":
            continue
        if not rule.params.get("report_required", False):
            continue
        prefix = f"{rule.id}_birth_"
        for hazard_id in sorted(world.hazards):
            if world.stats.get(f"{prefix}{hazard_id}") != stamp:
                continue
            hazard = world.hazards.get(hazard_id)
            if hazard is None or not hazard.active:
                continue
            if _already_reported(world, hazard.tile_id, date):
                continue
            produced.append(make_birth_report(world, hazard, date))
    return produced

# Пакет решений Game Owner для art production

## Назначение

Документ фиксирует решения, которые нельзя принимать внутри art production без владельца игры. Он не меняет sim, client или ontology и нужен только для безопасного перехода от независимых plates к final map/client atlas.

Текущий art подготовлен независимо от этих решений: `asset_library_v0`, `asset_library_v1`, terrain sources, architecture plates, goods/props plates и pose references.

## Правила

### Уже зафиксировано

- Топология: axial coordinates, odd-r layout, 6 соседей, `engine/hexgrid.py`.
- Текущие terrain IDs: `hill`, `field`, `pasture`, `forest`, `marsh`, `heath`, `salt_flat`, `ruin`, `water`.
- Текущие settlement kinds и renderer forms принимаются только из текущей онтологии и `tile_view.py`.
- `client/` заморожен в фазе 0; независимые plates не считаются client integration.
- `remembered` не заводится; работа идут с `observed`, `unknown` и `silence`.

### Ответ Game Owner

| Вопрос | Решение | Влияние на art |
|---|---|---|
| Разморозка client | Не сейчас; `client/` остаётся замороженным | Final client atlas и placement не начинаются |
| Base cell | `128×128` зафиксирован как единица атласа | Cell canvas и anchors сохраняются |
| Camera projection | Не фиксируется до разморозки client | Текущие isometric plates — reference; следующий target — flat 2D plates |
| Hex footprint | Сетка axial/6-соседная; до 5 дворов на rural hex; крупные постройки могут занимать несколько hex | Hex plate должен содержать до 5 household cues, а не один двор |
| Knowledge states | `observed`, `unknown` и `silence`; `remembered` не заводится | Не готовить remembered/fog plates |
| Data/render boundary | Подтверждена: `tile_view` — view-only, art не содержит логики | Semantic IDs не смешиваются с art IDs |

### Текущий режим art production

1. Independent flat 2D plates в ячейках `128×128` — следующий target.
2. Текущие isometric source/crop plates остаются visual reference и style QA material.
3. Изометрический projection не считается frozen; возможен отдельный будущий pass.
4. `observed`, `unknown` и `silence` — единственные разрешённые knowledge cues.
5. `watchtower` и `granary_candidate` остаются concept candidates до появления соответствующей entity/ADR.

### Что можно продолжать сейчас

- Улучшать alpha, contour, material separation и anchors.
- Чистить architecture/item/prop/actor plates.
- Расширять frozen vocabulary plates — только решением владельца: девять terrain id,
  которые ставят сценарии, это ровно те девять, что нарисованы.
- Делать static pose references — **нельзя**: поля `pose` в онтологии нет, носителя нет
  (ADR 0178). Позиции остаются `reference_no_carrier`.
- Готовить terrain transition prototypes — **нельзя**: `Tile.terrain` нигде не
  присваивается, все вхождения в `sim/` — сравнения, а слова `transition` нет ни в каталогах,
  ни в `docs/03`, ни в `docs/04`. Механизма перехода не существует; это та же ловушка, что
  была с позами.
- Валидировать source → plate → game mapping — сделано и проверяется автоматически:
  `design/art/validate_mapping.py` и его `--self-test` (ADR 0181).

### Что блокируется

- Final client atlas.
- Camera/zoom-specific variants.
- Fog rendering и client integration.
- Placement и multi-hex assembly.
- Animation timing.

## Проверка

```bash
python3 - <<'PY'
from pathlib import Path

text = Path("design/art/GAME_OWNER_DECISION_PACKET.md").read_text(encoding="utf-8")
assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
for marker in ("client", "camera", "remembered", "128×128", "ADR"):
    assert marker in text
print("decision packet: ready")
PY
```

Ручной критерий: Game Owner может ответить отдельно по client freeze, camera, cell size, hex footprint и `remembered`; до этих ответов art work продолжается только в independent plates и не выдаётся за final client integration.

## Вопрос 0190-A: опасности — три плитки по `Hazard.kind`, и как они рисуются

**Что показал аудит кода, а не догадка.**

- `Hazard` — существующая датакласса: `sim/src/hillcourt/ontology.py:310`, поля `id`, `kind`,
  `tile_id`, `intensity`, `active`, `spawn_rule_id`, `population`, `satiety`.
- `Hazard.kind` **читается рантаймом**: `sim/src/hillcourt/hazards/travel.py:57`
  (`base_risk_for(world, hazard.kind)`) и там же в вести
  `{"hazard": {"kind": hazard.kind, ...}}`. Это настоящий носитель, а не `game_entities`.
- Ровно три значения `kind`, и они же `id` в `design/catalogs/hazards.yml`:
  **`wolves`**, **`bog`**, **`band`**.
- `Hazard` реально создаётся в тике: `sim/src/hillcourt/engine/tick.py:622`
  и `sim/src/hillcourt/scenario.py:965`. То есть опасности — не абстракция.

**Требует решения хозяина:**

1. Рисовать три плитки `hazard_wolves` / `hazard_bog` / `hazard_band` по `Hazard.kind`?
2. **`bog` дублирует terrain.** В `hazards.yml` у `bog` параметр `terrain: marsh`, а плитка
   `terrain_marsh_hex` уже нарисована. Опасность «топь» и клетка «топь» — это одно и то же
   место. Рисовать вторую плитку на ту же клетку, или `bog` не получает art вовсе?
3. **Опасность — это метка на клетке или вещь, стоящая на клетке?** От этого зависит,
   входит ли плитка в hex plate (как terrain) или рисуется отдельным объектом поверх клетки.
   Топь, волчье логово и стоящая ватага выглядят по-разному, и одна схема на все три,
   скорее всего, будет неверной хотя бы для одной.

## Вопрос 0190-B: «лагеря» как сущности не существует

- `band_camp` («Ватага в топи») — это **правило появления** в `spawn_rules.yml`, а не
  сущность. Класс `Camp` в онтологии отсутствует.
- `Pack` — да, есть, но это **идущая партия**, а не место на карте: у неё `route`, `cargo`,
  `status`, а нет `tile_id` как места стоянки. Art для места требует носителя на `Tile` или
  `Settlement`, и у `Pack` его нет.
- Значит «нарисовать лагерь» нельзя: нечего выбирать плитку.

**Варианты, которые нужно выбрать хозяину:**

| | вариант | что это значит |
|---|---|---|
| **A** | никакого camp-art | ждать сущности. Рекомендую: пока нет `Camp`, любая плитка «лагеря» будет картинкой без носителя — ровно та ловушка, что закрыла `watchtower` |
| **B** | ватага = `Hazard.kind == "band"` | стоячая ватага на топях уже **является** `Hazard`; отдельная сущность «лагерь» её бы продублировала. Рекомендую вместе с A: «лагерь» = плитка `hazard_band` на клетке |
| **C** | владелец заводит `Camp` в онтологии | тогда появляется поле-носитель, и art становится возможен. Это правка онтологии — не зона art |

## Вопрос 0190-C: противоречие, которое надо отдать Systems, а не рисовать

`design/catalogs/spawn_rules.yml` утверждает в комментариях, что исполнителя
`target == hazard` в тике нет и правила молчат, и говорит, что это «зафиксировано тестом»:

> `Исполнителя target==hazard в тике нет: без фазы правило молчит (зафиксировано тестом).`

**Код утверждает обратное.** В `sim/src/hillcourt/engine/tick.py` есть оба исполнителя:

- `tick.py:465` — `mode == "topup`, дотягивает **существующие** угрозы (это `wolves_den`);
- `tick.py:545` — `mode == "spawn`, рождает **стоячие** угрозы (это `band_camp`), и именно
  там `tick.py:622` конструирует `Hazard`.

Комментарий каталога устарел, а тест, который, по его словам, это «зафиксирует», evidently
утверждает обратное. Каталог принадлежит Economist, тик — Implementer; **ни то, ни другое не
моя зона**, и править я это не буду.

Но это напрямую решает вопрос 0190-A: если бы исполнителей не было, `Hazard.kind` был бы
полем без живых значений и рисовать было бы нечего. По коду — значения живые, и
`band_camp` в принципе может выстрелить (сейчас не стреляет: гейт `min_households: 3` при
нуле дворов на топях).

**Прошу хозяина отдать это Systems/Economist:** комментарий в `spawn_rules.yml` противоречит
`tick.py` и должен быть приведён к коду, иначе следующий агент честно нарисует несуществующее.

## Проверка аудита

```bash
grep -n "class Hazard" -A 10 sim/src/hillcourt/ontology.py
grep -n "hazard.kind" sim/src/hillcourt/hazards/travel.py
grep -n 'rule.target != "hazard"' sim/src/hillcourt/engine/tick.py
grep -n "^- id:\|  kind:" design/catalogs/hazards.yml
```

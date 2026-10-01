# Отчёт по moodboard HILLCOURT

## Назначение

Отчёт фиксирует визуальное направление HILLCOURT после сравнения двух поколений изображений, переноса стиля с пользовательского reference и независимой критической приёмки.

Принятый итог находится в `design/art/illustrations_detailed/`. Исходный концептуальный moodboard сохранён в `design/art/illustrations/` и не используется как финальный production source.

## Правила

### Состав каталогов

| Путь | Роль |
|---|---|
| `design/art/illustrations/` | Первый moodboard из 11 изображений; сохранён как ранняя концепция |
| `design/art/references/detail_style_anchor.png` | Пользовательский reference: детализация, контуры, материалы и модульность |
| `design/art/references/detail_style_anchor_crop.png` | Style-only crop без крупных сцен; предотвращает копирование композиции reference |
| `design/art/style_tests/` | Два первых теста нового стиля: `01` и `03` |
| `design/art/illustrations_detailed/` | Принятый детальный набор из 11 изображений |
| `design/art/ASSET_BREAKDOWN.md` | Реестр видимых модулей, слоёв и будущих anchor/footprint |
| `design/art/PRODUCTION_GRID.md` | Рабочий контракт axial-hex grid и knowledge-state раскладки |
| `design/art/production_grid/` | Два guide-atlas и manifest crops; transparent production plates ещё не готовы |

### Метод генерации

- Модель: `gemini-3-pro-image`.
- Каждый запрос содержит полный `Common visual lock for HILLCOURT` из `docs/fiction/MOODBOARD.md`.
- Каждый запрос содержит соответствующий Master prompt или конкретный prompt без сокращения visual lock.
- Детальный стиль переносился через image reference, а не через пересказ содержимого reference.
- Style-only crop применялся там, где полный reference мог скопировать композицию.
- `01` и `03` прошли отдельную приёмку и затем были включены в финальный набор без повторной генерации.
- Остальные девять изображений сначала были сгенерированы с полным reference.
- После критического просмотра повторно сделаны `05`, `07`, `08`, `09`.
- Script `tools/generate_illustration.py` не изменялся; локальный SDK-adapter использовался только во время выполнения.
- Байты, полученные от модели как JPEG, lossless-конвертированы в настоящий PNG без изменения пикселей и с удалением metadata.

### Style lock принятого набора

1. **Формат:** overview, tile board, focused scene, atmospheric vignette и asset board имеют разные роли и не смешиваются в production grid.
2. **Камера:** приподнятый orthographic three-quarter; для изометрических сцен — 2:1 axonometric. `09` остаётся атмосферным исключением.
3. **Контур:** тёмный выразительный ink-outline переменной толщины, без чистого vector look.
4. **Текстура:** ручная clustered stroke и hand-painted pixel-art density; straw, timber, stone, soil, foliage и water различимы материалом.
5. **Палитра:** peat brown, weathered timber, ochre thatch, rough grey stone, moss и olive, muted slate, cold river blue, rust red.
6. **Свет:** приглушённый overcast daylight, мягкий ambient и компактные contact shadows; локальный fire/torch light остаётся dull amber.
7. **Архитектура:** timber, thatch, wattle, rough stone, простые steep roofs, modest palisades и плотные, но небольшие workshops; без palace, keep и imperial monumentality.
8. **Масштаб:** человек, дверь, fence, cart, animal и building читаются вместе; один объект имеет один главный силуэт.
9. **Запрещено:** magic, runes, fantasy glow, plate armor, XV-century knights, modern objects, readable text, labels, UI, frames, logos и watermark.
10. **Production principle:** сначала clean plate и anchor, затем sprite atlas; готовая panorama не режется механически на тайлы.

### Независимая приёмка

| Файл | Вердикт | Решение |
|---|---|---|
| `00_master_moodboard.png` | Принять | Общий landscape, manor, river, port, forest и resource identities читаются |
| `01_barony_overview.png` | Принять | Типы поселений и большие расстояния показаны без имперской парадности |
| `02_rural_tiles_sheet.png` | Принять как board | Изолированные rural-варианты пригодны для сравнения и будущей модулизации |
| `03_hill_manor.png` | Принять | Manor остаётся рабочей hill seat, а не королевским дворцом |
| `04_village_fair_tavern.png` | Принять | Crossroads, well, market, livestock и tavern образуют правдоподобную сельскую экономику |
| `05_river_ford_port.png` | Принять после повтора | Удалены horizon, distant city и stone bridge; остались ford, landing, pier и малые суда |
| `06_resource_settlements.png` | Принять | Salt, peat и forest work различаются формой, цветом и infrastructure |
| `07_thegn_frontier.png` | Принять после повтора | Удалено копирование reference; осталась самостоятельная timber frontier estate |
| `08_city_quarter.png` | Принять после повтора | Убраны поздние red-tile и fantasy elements; квартал остался локальным и ремесленным |
| `09_scouting_discovery.png` | Принять как vignette | Forest-edge investigation читается; сцена не предназначена для прямой нарезки как tile |
| `10_goods_crafts_board.png` | Принять как board | Resources, crafts, transport и containers изолированы и читаются |

Итог независимого review: `11/11`, обязательных регенераций нет.

### Манифест принятого набора

| Файл | Размер | Размер файла |
|---|---:|---:|
| `00_master_moodboard.png` | 1264×842 | 2128861 B |
| `01_barony_overview.png` | 1376×768 | 2140220 B |
| `02_rural_tiles_sheet.png` | 1264×842 | 1454606 B |
| `03_hill_manor.png` | 1456×720 | 1810427 B |
| `04_village_fair_tavern.png` | 1200×896 | 2400801 B |
| `05_river_ford_port.png` | 1579×672 | 2160395 B |
| `06_resource_settlements.png` | 1264×842 | 2116349 B |
| `07_thegn_frontier.png` | 1579×672 | 2209706 B |
| `08_city_quarter.png` | 1579×672 | 2289264 B |
| `09_scouting_discovery.png` | 1456×720 | 1868030 B |
| `10_goods_crafts_board.png` | 1264×842 | 1429467 B |

### Ограничения

- Изображения имеют разные native dimensions и aspect ratios; это концепты, а не готовая sprite sheet.
- Прозрачные crop plates для architecture, items, props и actors собраны в `asset_library_v0/`; финальная очистка alpha-краёв, contact-shadow separation и atlas packing ещё не выполнены.
- `00`, `01`, `04`, `06`, `07`, `08` являются composition sources, а не прозрачными sprite plates.
- `02` и `10` являются boards; из них извлекаются объекты, но board целиком не является игровым активом.
- `09` является vignette и не должен натягиваться на production hex без перерисовки глубины.
- Мелкие figures в `04` и `08` могут сливаться при агрессивном уменьшении; потребуется actor cleanup.
- Анимации людей и животных ещё не созданы.
- Состояния знания, rumor и memory rendering ещё не созданы.
- Изображения не кодируют игровую истину, числа, права или экономические правила.

### Следующий этап

1. Утвердить или изменить рабочий base isometric cell `128×128` и human scale.
2. Разделить boards `02` и `10` на clean transparent plates с anchor points.
3. Перерисовать перекрытые architecture assemblies из `04`, `06`, `07`, `08` в модульные части.
4. Собрать terrain, building, prop, actor и effect atlases.
5. Определить idle/work poses и animation rigs для человека, livestock и транспорта.
6. После фиксации observed rendering state отдельно проектировать remembered и unknown states.

Production grid не считается утверждённым этим отчётом.

## Проверка

```bash
python3 - <<'PY'
from pathlib import Path
import struct

root = Path("design/art/illustrations_detailed")
expected = [
    "00_master_moodboard.png",
    "01_barony_overview.png",
    "02_rural_tiles_sheet.png",
    "03_hill_manor.png",
    "04_village_fair_tavern.png",
    "05_river_ford_port.png",
    "06_resource_settlements.png",
    "07_thegn_frontier.png",
    "08_city_quarter.png",
    "09_scouting_discovery.png",
    "10_goods_crafts_board.png",
]
assert sorted(path.name for path in root.glob("*.png")) == expected
for name in expected:
    data = (root / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width >= 1200 and height >= 672
for name in ("ASSET_BREAKDOWN.md", "MOODBOARD_REPORT.md"):
    text = Path("design/art", name).read_text(encoding="utf-8")
    assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
report = Path("design/art/MOODBOARD_REPORT.md").read_text(encoding="utf-8")
for marker in ("11/11", "gemini-3-pro-image", "detail_style_anchor.png", "illustrations_detailed"):
    assert marker in report
print("moodboard report: 11/11")
PY
```

Ручной критерий: открыть все 11 файлов из `illustrations_detailed/`; на каждом нет читаемого текста, UI, магического свечения, plate armor, позднего castle или imperial capital; `02`, `09` и `10` сохраняют свои роли board/vignette; `05`, `07`, `08`, `09` соответствуют принятым исправлениям из таблицы приёмки.

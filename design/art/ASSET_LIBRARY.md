# Библиотека ассетов HILLCOURT v0

## Назначение

Библиотека отделяет универсальные visual plates от игрового интерфейса. Здесь хранятся прозрачные crops и рабочие atlas guide для архитектуры, ресурсов, реквизита и фигур, которые нужны независимо от финального client camera, zoom и knowledge UI.

Библиотека не является утверждённым production client asset set: `client/` остаётся замороженным, а камера, размер тайла и `remembered`-визуализация ещё не зафиксированы Game Owner.

## Правила

### Состав

| Файл | Назначение |
|---|---|
| `architecture_atlas_v0.png` | Прозрачные architecture plates: cottage, shed, manor reference, palisade, watchtower candidate, bridge, gate |
| `items_atlas_v0.png` | Прозрачные resource/item plates: salt, peat, grain, wool, timber, ceramics, boat, rope |
| `props_atlas_v0.png` | Прозрачные prop/transport plates: cart, tools, boat, net, barrels, handcart context |
| `actors_atlas_v0.png` | Прозрачные actor/equipment plates и два composition context plate |
| `supported_goods_props_atlas_v0.png` | Filtered atlas только для существующих `Good.id` |
| `actor_pose_reference_atlas_v0.png` | Четыре actor pose references; не animation set |
| `terrain_atlas_v0.png` | Source-crop atlas для 9 terrain IDs; финальные hex masks ещё не вырезаны |
| `terrain_guides/terrain_guide_v0.png` | Подписанный terrain review sheet |
| `terrain_manifest.yml` | Source crops и game terrain mappings |
| `guides/*_guide_v0.png` | Подписанные review sheets; подписи не входят в clean atlases |
| `plates/*.png` | Отдельные transparent plates с единым target canvas `128×128` |
| `asset_library_manifest.yml` | Source crops, game mappings, anchors и статусы |
| `asset_library_v1/architecture/architecture_manifest.yml` | Clean architecture v1: autonomous modules, exclusions и game form mapping |
| `asset_library_v1/items/goods_manifest.yml` | Supported Good IDs и clean goods/props plates |
| `asset_library_v1/actors/actors_manifest.yml` | Clean actor/equipment plates и pose references |
| `asset_library_v1/terrain/terrain_edge_manifest.yml` | Client-independent edge directions и transition prototypes |
| `terrain_manifest.yml` | Отдельный source-crop реестр terrain IDs; финальные hex masks ещё не вырезаны |
| `FLAT2D_PLATE_SPEC.md` | Следующий target: flat 2D plates вместо isometric reference |
| `flat2d_proof/flat2d_proof_manifest.yml` | Первый flat 2D proof: terrain, architecture, prop, actor |
| `GAME_OWNER_DECISION_PACKET.md` | Вопросы client/camera/cell/fog, которые нельзя решать внутри art |
| `supported_goods_props_manifest.yml` | Filtered mappings для существующих Good IDs |
| `actor_pose_reference_manifest.yml` | Позы без заявления об animation integration |

### Cell contract

- Cell canvas: `128×128` px.
- Логический anchor: `(64, 80)`.
- Background clean plates: transparent.
- Guide background: `#10242a`.
- Architecture и objects используют нижний центр контакта с землёй.
- `composition_reference` и `needs_isolation` нельзя выпускать как финальный autonomous sprite без дополнительной очистки.
- Текущие v0/v1 plates используют `isometric_2to1` как reference projection; следующий target — flat 2D plate в ячейке `128×128`.

### Соответствие игре

- Архитектура сопоставляется с текущими `TileView` forms и `Settlement`/`Manor`, но art ID не создаёт новые world entities.
- Items/props используют только существующие `Good.id` из `design/catalogs/goods.yml`; `fish`, `plank`, `rope`, `basket` и `net` не имеют текущего Good и помечены `concept_only`.
- `cart`, `boat`, `log`, `grain`, `salt`, `peat`, `wool` и `iron` имеют текущие game mappings.
- Actors — только silhouettes/equipment для `Person`, `Household` и `Pack`; классы `Knight` и `Explorer` не создаются.
- `watchtower` и isolated `granary` не выпускаются как final assets: первая требует ontology check, вторая пока имеет только context crop.

### Статусы

| `status` | Значение |
|---|---|
| `crop_reference` | Прозрачный crop с читаемым anchor; требует ручной проверки/cleanup |
| `composition_reference` | Целый контекстный объект или сцена; не autonomous plate |
| `needs_isolation` | Объект виден, но отделён от соседней architecture массы |
| `needs_ontology_check` | Art candidate без текущей entity/form |
| `concept_only` | Визуальный объект без существующего Good или runtime entity |

### Граница Game Owner

Разрешено делать независимые plates по frozen vocabulary. Нельзя считать готовыми:

- client atlas packing;
- camera-dependent crops и zoom variants;
- final hex placement;
- `observed`/`unknown` rendering и `silence` как knowledge cue; `remembered` не готовится;
- final flat 2D atlas и placement;
- animations и interaction poses.

`remembered` не готовится и не является формальным runtime state. Разрешены только `observed` и `unknown`, а `silence` остаётся knowledge cue без отдельного visual state.

## Проверка

```bash
python3 - <<'PY'
from pathlib import Path
import yaml

root = Path("design/art/asset_library_v0")
manifest = yaml.safe_load((root / "asset_library_manifest.yml").read_text(encoding="utf-8"))
assert manifest["atlas_contract"]["cell_canvas_px"] == [128, 128]
assert manifest["atlas_contract"]["anchor_px"] == [64, 80]
assert manifest["atlas_contract"]["background"] == "transparent"
assert manifest["atlas_contract"]["target_render_mode"] == "flat_2d_plate"
assert manifest["atlas_contract"]["reference_projection"] == "isometric_2to1"
assert len(manifest["atlases"]) == 4
for supporting in manifest["supporting_manifests"]:
    assert (root / supporting["file"]).is_file()
for atlas in manifest["atlases"]:
    for relative in (atlas["file"], atlas["guide"]):
        path = root / relative
        assert path.is_file()
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    for cell in atlas["cells"]:
        assert cell["id"]
        assert cell["name"]
        assert cell["status"] in manifest["atlas_contract"]["status_values"]
        plate = root / atlas["plate_dir"] / f"{cell['id']}.png"
        assert plate.is_file()
        assert plate.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
terrain = yaml.safe_load((root / "terrain_manifest.yml").read_text(encoding="utf-8"))
assert len(terrain["cells"]) == 9
supported = yaml.safe_load((root / "supported_goods_props_manifest.yml").read_text(encoding="utf-8"))
assert len(supported["cells"]) == 9
assert all(cell["game_goods"] for cell in supported["cells"])
pose = yaml.safe_load((root / "actor_pose_reference_manifest.yml").read_text(encoding="utf-8"))
assert len(pose["cells"]) == 4
for atlas in (supported, pose):
    for relative in (atlas["atlas"]["file"], atlas["atlas"]["guide"]):
        path = Path("design/art") / relative
        assert path.is_file()
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
for relative, expected_count in (("architecture/architecture_manifest.yml", 10), ("items/goods_manifest.yml", 9), ("actors/actors_manifest.yml", 5)):
    path = root.parent / "asset_library_v1" / relative
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["cells"]) == expected_count
    for cell in data["cells"]:
        plate = root.parent / data["atlas"]["plate_dir"] / f"{cell['id']}.png"
        assert plate.is_file()
        assert plate.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
edges = yaml.safe_load((root.parent / "asset_library_v1/terrain/terrain_edge_manifest.yml").read_text(encoding="utf-8"))
assert edges["topology"]["direction_order"] == ["dir_0", "dir_1", "dir_2", "dir_3", "dir_4", "dir_5"]
assert (root.parent / "asset_library_v1/terrain/guides/terrain_edge_guide_v1.png").is_file()
text = (root.parent / "ASSET_LIBRARY.md").read_text(encoding="utf-8")
assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
for marker in ("128×128", "transparent", "game_goods", "concept_only", "client"):
    assert marker in text
print("asset library: 4 atlases, 9 terrain sources, v1 architecture/goods/poses")
PY
```

Ручной критерий: открыть clean atlas и guide для каждой категории, затем terrain guide, `supported_goods_props_guide_v0` и `actor_pose_reference_guide_v0`; вне контура должен быть прозрачный фон, подписи остаются только в guide, terrain plates имеют 9 правильных ID, а `concept_only`, `needs_isolation`, `needs_ontology_check` и `composition_reference` не выдаются за финальные flat 2D plates.

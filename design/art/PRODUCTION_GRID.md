# Production grid HILLCOURT

## Назначение

Документ связывает принятые иллюстрации с текущими сущностями и renderer-формами HILLCOURT. Это рабочая карта раскладки и anchors; независимые transparent plates вынесены в `design/art/ASSET_LIBRARY.md`. Он не меняет `sim/`, каталоги или `client/`.

Рабочая визуальная проверка находится в `design/art/production_grid/`:

- `rural_hex_guide_v0.png` — шесть building crops, четыре rural composition references и отдельный source для rural hex с пятью дворами.
- `manor_complex_guide_v0.png` — root manor, thegn estate, infrastructure и composition references.
- `atlas_manifest.yml` — координаты crops, target canvas, footprint, anchor, layer и соответствующие game forms.

Guide-изображения содержат подписи только для ревью и не являются финальными игровыми sprites.

## Правила

### Топология

- Игровая карта использует axial hex coordinates и odd-r layout.
- У клетки шесть соседей; сетка не является квадратной ромбовидной сеткой из иллюстраций.
- `v0_barony_100` содержит 10 000 клеток, 200 water cells, 3 начальных ford и 2 начальных bridge.
- `TILE_MAX_HOUSEHOLDS = 5`; urban marker разрешает до 20 дворов на гекс. Один rural hex — это контейнер максимум для пяти household cues, а не одна жилая единица; крупные постройки могут занимать несколько hex.
- Текущие settlement kinds: `hill_court`, `farmstead`, `salt_village`, `village`, `native_village`.
- Текущий `coarse` renderer не импортирует art; этот документ только описывает будущий визуальный слой.

### Базовый визуальный cell

| Параметр | Значение |
|---|---|
| `topology` | `axial_hex` |
| `layout` | `odd_r` |
| `reference_projection` | `isometric_2to1` |
| `target_render_mode` | `flat_2d_plate` |
| `projection_frozen` | `false` |
| `neighbor_count` | `6` |
| `logical_cell_canvas_px` | `128 × 128` |
| `reference_ground_mask_px` | `128 × 64` |
| `reference_anchor_px` | `(64, 80)` |
| `final_hex_anchor_px` | Pending flat 2D proof |
| `guide_cell_canvas_px` | `256 × 192` |
| `anchor` | Reference anchor; final flat 2D anchor will be fixed by proof |
| `footprint` | По основанию/опоре, не по bounding box крыши |

Многоhexовые compositions используют `footprint_hexes`, а не искусственно одинаковые квадратные bounding boxes. Root manor и thegn estate — reference compositions с отдельными footprint; они не должны автоматически превращаться в один sprite.

### Слои

```text
terrain_base
water_and_wetland
roads_and_contact_shadows
fields_and_gardens
rear_structures_and_background
front_structures
infrastructure
props_and_resources
actors_and_animals
smoke_fire_water_effects
knowledge_render_state
```

Порядок означает compositing order, а не порядок симуляции. `TileView` и render pipeline не меняются этим документом.

### Текущие terrain и renderer cues

| Игровой вход | Визуальный кандидат | Примечание |
|---|---|---|
| `terrain=hill` | `terrain_hill` | Root seat и manor relief |
| `terrain=field` | `terrain_field`, `lord_field` | Field works и demesne cue |
| `terrain=pasture` | `terrain_pasture` | Livestock cue |
| `terrain=forest` | `terrain_forest` | Timber, forage and forest work |
| `terrain=marsh` | `terrain_marsh`, `bog_iron` | Wetland и bog-iron cue |
| `terrain=heath` | `terrain_heath` | Пустошь и wild land |
| `terrain=salt_flat` | `terrain_salt_flat`, `salt_settlement` | Salt works и salt village |
| `terrain=ruin` | `terrain_ruin`, `ruin_site` | Ruin не даёт loot и relic |
| `terrain=water` | `terrain_water`, `pier` | Water без crossing может оставаться `waste` |

Текущий `tile_view` также использует forms `open_field`, `forest`, `waste`, `single_homestead`, `tent_earth_homestead`, `timber_house`, `multi_storey_house`, `village`, `thegn_estate`, `hall_on_hill`, `lord_field`, `crossing`, `bog_iron`, `quarry`, `foot_pot_camp`, `campfire_camp`, `tent_camp`, `pavilion_camp`, `wagon_camp`, `fortified_camp`, `pier`, `trail`, `dirt_road`, `built_road`, `waystation`, `tavern_site`, `iron_mine`, `salt_settlement`, `ruin_site`, `hermitage`, `smoke_site`, `lost_caravan`, `city_quarter`, `large_village`, `tribal_village`, `baron_castle`.

`quarry` сейчас является reserve form без отдельного stone good/recipe. `tavern_site` — place marker, а не готовая tavern entity. `city_quarter` и dwelling forms требуют отдельной проверки перед созданием production art.

### Infrastructure и markers

| Current input | Visual candidates | Layer |
|---|---|---|
| `trail`, `dirt_road`, `built_road` | `road_dirt`, `road_cobble`, `track_mud` | infrastructure overlay |
| `ford`, `bridge` | `crossing`, `ford_stones`, `bridge_timber` | infrastructure overlay |
| `mooring` | `pier`, `dock_timber`, `boat_small` | water infrastructure |
| `waystation` | `waystation` | settlement marker |
| `tavern` | `tavern_site` | place marker, не building entity |
| `mine` | `iron_mine` | place marker, не отдельная mine entity |
| `urban` | `city_quarter` | urban composition |
| `hermitage` | `hut_rough`, `hermitage` | discovery composition |
| `smoke` | `smoke_site` | discovery cue |
| `lost_caravan` | `wagon_camp`, `lost_caravan` | event cue |
| `castle` | `baron_castle` | current view form, не новый world entity |
| `Regime.demesne` | `lord_field` | tenure/regime cue |
| `Right.common` | `common_access` cue | не создаётся отдельная settlement form |

### Goods и resources

Визуальные candidates из `ASSET_BREAKDOWN.md` не добавляют новые `Good`. Текущий каталог остаётся источником истины для `Good.id`, `category` и `storage`.

| Категория | Текущие IDs для визуального реестра |
|---|---|
| Food | `grain`, `flour`, `milk`, `cheese`, `butter`, `eggs`, `meat`, `roots`, `greens`, `mushrooms`, `berries` |
| Material | `straw`, `firewood`, `log`, `peat`, `hay`, `iron`, `iron_bloom`, `wool`, `cloth`, `hide`, `salt`, `silver`, `brine`, `axe`, `cart`, `raft`, `boat`, `wooden_plough`, `iron_share`, `butter_churn`, `war_kit` |
| Livestock | `pig`, `goat`, `sheep`, `hen`, `duck`, `goose`, `ox_m`, `ox_f`, `donkey_m`, `donkey_f`, `horse_m`, `horse_f`, `ox_calf`, `donkey_foal`, `horse_foal` |

`salt_pile`, `peat_brick_stack`, `peat_stack`, `grain_sack`, `wool_bale`, `log_stack`, `cart_loaded`, `boat_small`, `fishing_net` и `tool_iron` — art IDs или composition candidates, а не новые goods. `cart`, `raft` и `boat` имеют runtime mechanics, но не отдельные Goods view forms.

### Actors и Pack cues

В репозитории нет art entity `Actor`, класса `Knight` или `Explorer`. Визуальные actor IDs описывают только silhouettes и equipment:

- `villager_adult` и `craftsman_small` — обычные жители и ремесленники;
- `estate_worker_small` — работник усадьбы;
- `retainer_shield` и `retainer_spear` — вооружённый frontier cue без plate armor;
- `scout_forester` — наблюдатель с луком и колчаном;
- `horse`, `sheep`, `pig`, `chicken` — livestock cues.

`MOVEMENT_PROFILES` и `PACK_SCOUT_PROFILES` остаются runtime-конфигурацией. `purpose=scout` и observation coverage не превращаются в новые actor classes.

### Knowledge render states

`observed` и `unknown` — единственные визуальные состояния renderer; `silence` — отсутствие наблюдения, а не третий стиль.

| Состояние | Основание в текущей системе | Визуальный контраст |
|---|---|---|
| `observed` | Доставленный `Report` с фактом или ближайший eye/scout view | Полная детализация, actors, текущие props и effects |
| `unknown` | Нет `latest(about)`, silence или нет наблюдения | Fog/terrain silhouette без достоверных зданий и actors |

`remembered` не создаётся и не готовится до отдельного ADR. Наблюдаемое и неизвестное используют одну геометрию и один anchor.

### Guide atlases

| Файл | Что показывает | Что ещё не сделано |
|---|---|---|
| `production_grid/rural_hex_guide_v0.png` | Шесть building crops, четыре rural composition references и `village_hex_5hh` source | Source создан, но flat 2D final plate и multi-household placement ещё не готовы |
| `production_grid/manor_complex_guide_v0.png` | Root manor, thegn estate, granary context, palisade, tower candidate, bridge, logging, field, supply, gate | Granary и watchtower требуют ontology/ runtime check; composition plates остаются provisional |

`atlas_manifest.yml` хранит coordinates, target canvas, footprint, anchor, layer и status каждой ячейки. Guide labels не переносятся в game assets.

`design/art/asset_library_v0/` и `asset_library_v1/` содержат независимые reference plates; они не являются client atlas и сейчас используют isometric source projection. Следующий target — flat 2D plates в зафиксированной ячейке `128×128`.

## Проверка

```bash
python3 - <<'PY'
from pathlib import Path
import yaml

root = Path("design/art")
manifest = yaml.safe_load((root / "production_grid/atlas_manifest.yml").read_text(encoding="utf-8"))
assert manifest["grid"]["topology"] == "axial_hex"
assert manifest["grid"]["neighbor_count"] == 6
assert manifest["grid"]["reference_projection"] == "isometric_2to1"
assert manifest["grid"]["target_render_mode"] == "flat_2d_plate"
assert manifest["grid"]["projection_frozen"] is False
assert manifest["grid"]["visual_render_states"] == ["observed", "unknown"]
assert manifest["grid"]["logical_cell_canvas_px"] == [128, 128]
assert manifest["game_contract"]["rural_household_cap"] == 5
assert manifest["game_contract"]["urban_household_cap"] == 20
assert len(manifest["atlases"]) == 2
for atlas in manifest["atlases"]:
    path = root / atlas["file"]
    assert path.is_file()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert atlas["cells"]
    for cell in atlas["cells"]:
        assert cell["id"]
        assert cell["name"]
        assert cell["target_canvas_px"] == [256, 192] or cell["target_canvas_px"] == [128, 128]
        assert len(cell["footprint_hexes"]) == 2
        assert len(cell["anchor_px"]) == 2
text = (root / "PRODUCTION_GRID.md").read_text(encoding="utf-8")
assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
for marker in ("axial_hex", "odd_r", "rural_household_cap", "village_hex_5hh", "observed", "flat_2d_plate", "silence"):
    assert marker in text
assert "remembered" not in manifest["grid"]["visual_render_states"]
print("production grid: 2 atlases")
PY
```

Ручной критерий: открыть `rural_hex_guide_v0.png` и `manor_complex_guide_v0.png`; каждый crop соответствует подписи, `village_hex_5hh` показан как отдельный source, а isometric plates явно считаются reference до flat 2D proof. Перед production pass каждый `crop_reference` должен быть заменён clean flat 2D plate с зафиксированной ячейкой `128×128`; `needs_ontology_check` и `source_generated` нельзя молча выпускать в atlas.

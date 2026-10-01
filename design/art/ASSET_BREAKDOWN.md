# Разбор ассетов HILLCOURT

## Назначение

Этот документ разбирает принятый детальный набор из `design/art/illustrations_detailed/` на будущие игровые элементы. Он фиксирует видимые формы, возможные модули, порядок слоёв и правила нарезки, но не объявляет картинки готовыми production-спрайтами.

Окончательные размеры, transparencies, pivot points, atlas layout и анимации определяются на следующем этапе production grid.

## Правила

### Роли форматов

| `source_role` | Файлы | Назначение |
|---|---|---|
| `overview` | `00`, `01` | Композиция большой территории и проверка масштаба поселений |
| `moodboard_board` | `02` | Изоляция rural-вариантов и сравнение силуэтов |
| `focused_scene` | `03`–`08` | Детальный источник зданий, инфраструктуры и фигур |
| `atmospheric_vignette` | `09` | Атмосферная сцена разведки; не production tile |
| `asset_board` | `10` | Изолированные сырьевые, ремесленные и транспортные объекты |

Форматы нельзя превращать в один кадр одинаковой плотности. `overview` задаёт крупный масштаб, `02` и `10` дают чистый визуальный материал, `09` допускает живописную глубину.

### Общие правила извлечения

- `anchor` здания, дерева или пропа — нижний центр видимого контакта с землёй.
- `footprint` считается по основанию или опоре, а не по bounding box крыши.
- Ромбовидные основания используют 2:1 axonometric; atmospheric vignette `09` остаётся исключением.
- `contact_shadow`, дым, вода и свет выделяются отдельно от геометрии.
- Мелкие соседние здания сохраняются общей архитектурной массой, если их крыши перекрывают друг друга.
- Панорамы `00`, `01`, `04`, `06`, `07`, `08` не режутся напрямую на игровые тайлы без реконструкции чистых слоёв.
- Тонкий контур, ручная штриховка и разделение материалов сохраняются во всех модулях.
- Один объект должен читаться после уменьшения: главный силуэт, 2–3 тональные массы, одна понятная функция.

### Общий порядок слоёв

```text
terrain_base
water_and_wetland
roads_and_contact_shadows
fields_and_gardens
rear_structures_and_background
front_structures
infrastructure
props_and_resources
figures_and_animals
smoke_fire_water_effects
selection_or_memory_state
```

## 00. Генеральный мудборд — `00_master_moodboard.png`

**Роль:** `overview`. Общий style anchor и визуальная инвентаризация баронства.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `manor_hill_compound`, `house_long_thatch`, `cottage_gable`, `granary_small`, `shed_open`, `gatehouse_timber`, `awning_canvas` | Двор усадьбы, каменные и деревянные дома, открытые навесы отделяются как разные семейства. Замкнутый двор остаётся композицией. |
| Инфраструктура | `road_dirt`, `field_plot`, `fence_log`, `palisade_segment`, `river_bank`, `bridge_timber`, `pier_timber`, `salt_basin_frame` | Дороги и поля — terrain overlays. Мосты, пирсы и частоколы — edge-модули с собственными anchor. |
| Пропсы | `barrel`, `bench`, `log_stack`, `wagon_cart`, `produce_bed`, `crate`, `tool_rack` | Повторяемые бочки, штабеля и телеги выделяются в отдельные sprite families. |
| Фигуры | `villager_small`, `farmer_small`, `porter_small`, `horse`, `sheep`, `pig` | Сохраняются как scale cues; на обзоре допускается упрощённый силуэт. |
| Ландшафт | `terrain_hill`, `terrain_pasture`, `terrain_field`, `terrain_marsh`, `terrain_river`, `terrain_forest` | Ландшафт разделяется на крупные маски; мелкие деревья остаются deco. |

**Слои:** `terrain/water → roads/fields → rear trees/fences → buildings → front fences/props → people/animals → smoke`.

**Footprint:** compound Manor и field plot имеют неправильные ромбовидные основания; gatehouse anchor — порог, palisade anchor — середина секции, pier anchor — соединение с берегом.

## 01. Общая земля баронства — `01_barony_overview.png`

**Роль:** `overview`. Проверка композиции farming, forestry, quarry, wetland, river и port на одном масштабе.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `cottage_gable`, `field_shed`, `manor_hill_compound`, `mine_headframe`, `quarry_working`, `logging_camp`, `port_cluster` | Mine headframe, cottage, field shed и manor отделяются. Quarry ridge, marsh network и port остаются контекстными. |
| Инфраструктура | `road_dirt`, `field_plot`, `bridge_timber`, `boardwalk_marsh`, `pier_timber`, `fence_log`, `quarry_ramp` | Boardwalk и bridge используют разные anchor: midpoint span и bank connection. |
| Пропсы | `wagon_covered`, `cart_small`, `barrel`, `log_stack`, `timber_stack`, `sack`, `field_bundle` | Covered wagon и cart становятся самостоятельными транспортными модулями. |
| Фигуры | `villager_small`, `miner_small`, `logger_small`, `porter_small`, `draft_horse`, `sheep` | Различить профессию только через контур и equipment. |
| Ландшафт | `terrain_field`, `terrain_forest`, `terrain_quarry`, `terrain_marsh`, `terrain_river`, `terrain_salt_flat` | Каждая ресурсная зона получает отдельную terrain mask. |

**Слои:** `macro terrain → water/wetland → roads → resource patches → buildings → infrastructure/props → people/animals → smoke`.

**Footprint:** field и pond patches прямоугольны в мировых координатах и становятся ромбами в камере; buildings anchor по центру основания, bridge — по центру пролёта.

## 02. Семейство сельских vignette — `02_rural_tiles_sheet.png`

**Роль:** `moodboard_board`. Четыре изолированных rural-композиции на тёмном фоне.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `cottage_gable`, `house_pair`, `farmhouse_long`, `shed_open`, `granary_small` | Связанные крыши сохраняются единым silhouette. Каждый полный участок остаётся moodboard-композицией. |
| Инфраструктура | `fence_log`, `gate_timber`, `path_dirt`, `garden_bed`, `well_stone` | Задний и передний fence разделяются по глубине. Garden beds выравниваются по soil footprint. |
| Пропсы | `barrel`, `bench`, `handcart`, `log_stack`, `pot`, `bucket`, `haystack`, `firepit` | Firepit и smoke выделяются отдельно; contact shadow остаётся под слоем объектов. |
| Фигуры | `chicken`, `pig` | Людей нет; животные дают масштаб dwelling. |
| Ландшафт | `terrain_grass_edge`, `terrain_dirt_yard`, `terrain_garden` | Тёмный фон не является частью terrain; сохраняется только grass skirt. |

**Слои:** `diamond terrain → rear fence → rear buildings → yard props/crops → front fence → animals → smoke`.

**Footprint:** anchor — центр ромбовидного основания. Cottage footprint примерно `1×2` или `2×2` условных ромба, farmhouse — `2×3`; окончательные размеры откладываются до production grid.

## 03. Холм и усадьба — `03_hill_manor.png`

**Роль:** `focused_scene`. Эталон manor hierarchy и hill footprint.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `manor_hill`, `house_long_thatch`, `outbuilding_thatch`, `watchtower_timber`, `gatehouse_timber`, `supply_awning` | Manor hall не разбирается на крыши; остальные постройки отделяются. |
| Инфраструктура | `palisade_segment`, `gate_timber`, `road_dirt`, `field_fence`, `terrain_plateau`, `terrain_cut_slope` | Cut slope и plateau остаются terrain assembly; palisade — repeating edge modules. |
| Пропсы | `barrel`, `log_stack`, `bench`, `crate`, `sack`, `tool_rack`, `firepit` | Supply area отделяется от courtyard. |
| Фигуры | `estate_worker_small`, `farmer_small`, `sheep`, `chicken` | Фигура в supply area задаёт human scale. |
| Ландшафт | `terrain_hill`, `terrain_field`, `terrain_river`, `terrain_forest` | Дальний landscape — backdrop, не игровой модуль. |

**Слои:** `distant landscape → plateau/cliff → roads/shadows → rear palisade → buildings → courtyard props → front palisade/gate → people/animals → smoke`.

**Footprint:** manor anchor — центр каменного основания; cliff footprint нерегулярный и не требует квадратного production tile.

## 04. Деревня, ярмарка и таверна — `04_village_fair_tavern.png`

**Роль:** `focused_scene`. Эталон плотного rural crossroads без городской парадности.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `cottage_plaster_thatch`, `farmhouse_long`, `tavern_slate`, `barn_plank`, `market_awning`, `well_small` | Tavern, barn и cottages имеют разные roof/material families. Sign не содержит читаемого текста. |
| Инфраструктура | `road_dirt`, `road_cobble`, `puddle_mud`, `fence_log`, `garden_bed`, `livestock_pen` | crossroads остаётся контекстом; дороги делятся на dirt и cobble overlays. |
| Пропсы | `barrel`, `sack`, `crate`, `bench`, `produce_table`, `bucket`, `log_stack`, `handcart`, `cart_horse` | Market table, awning и well отделяются; cart anchor между осями. |
| Фигуры | `villager_adult`, `farmer_adult`, `trader_adult`, `traveler_small`, `horse`, `sheep`, `pig`, `poultry` | Ремесленная одежда и корзины дают activity cues без классов персонажей. |
| Ландшафт | `terrain_village_mud`, `terrain_garden`, `terrain_grass` | Puddles и wheel ruts — decals. |

**Слои:** `ground/mud/cobbles → rear gardens/fences → buildings → well/stall/props → carts/people → foreground buildings/fences → smoke`.

**Footprint:** cottage — компактный ромб, farmhouse — вытянутый, tavern — увеличенный составной ромб. Market awning и well используют собственные anchor.

## 05. Река, брод и порт — `05_river_ford_port.png`

**Роль:** `focused_scene`. Эталон водной инфраструктуры без большого harbor.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `boat_sail`, `boat_row`, `boat_work`, `hut_working`, `cottage_stone_base`, `shed_open` | Три boat variants сравниваются по hull silhouette, а не по одному шаблону. |
| Инфраструктура | `river_channel`, `ford_stones`, `pier_timber`, `bank_path`, `net_frame`, `mooring_post` | River хранится отдельным spline/terrain asset; ford stones — flow-aware overlays. |
| Пропсы | `fishing_net`, `rope_coil`, `barrel`, `crate`, `sack`, `log_stack`, `cart_loaded`, `crate_small` | Тонкие ropes и nets требуют alpha-preserving cleanup. |
| Фигуры | нет явных людей или животных | Человеческий масштаб задают постройки, лодки и телега. |
| Ландшафт | `terrain_river`, `terrain_bank_mud`, `terrain_gravel`, `terrain_conifer` | Forest edge и opposite bank остаются backdrop. |

**Слои:** `water/current → submerged stones → banks/paths → rear forest/buildings → docks/boats → nets/cart → foreground ripples`.

**Footprint:** boat anchor — геометрический центр hull; pier — connection с bank; net frame — центр между опорами. Rope и net не включать в bounding box building.

## 06. Ресурсные поселения — `06_resource_settlements.png`

**Роль:** `focused_scene`. Salt, peat и forest work читаются через цвет, форму и инфраструктуру.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `house_gable`, `house_gable_large`, `shed_open`, `granary_small`, `hut_working` | Три settlement clusters остаются композициями; повторяемые дома отделяются. |
| Инфраструктура | `salt_basin_frame`, `peat_channel`, `boardwalk_timber`, `stone_border`, `path_dirt`, `boat_landing` | Salt pans, peat cuts и boardwalk образуют три разных infrastructure families. |
| Пропсы | `log_stack`, `plank_stack`, `barrel`, `basket`, `crate`, `sack`, `handcart`, `saw_horse`, `salt_pile`, `peat_stack`, `tool_hand` | Resource-specific props не смешивать: соль, торф и timber имеют разные stack anchors. |
| Фигуры | `salt_worker_small`, `peat_worker_small`, `logger_small`, `settlement_worker_small` | Profession читается через location и tool, не через отдельный actor class. |
| Ландшафт | `terrain_salt_flat`, `terrain_peat_marsh`, `terrain_water`, `terrain_rock`, `terrain_forest` | Wet highlights и shallow pools отделяются от terrain. |

**Слои:** `terrain → water basins → paths → building shells → open timber structures → resource props → figures → fire/smoke/shadows`.

**Footprint:** buildings и stalls anchor по нижнему контакту; salt basin footprint следует внешнему ромбу рамы; peat channels используют маски русла.

## 07. Тэн и пограничная земля — `07_thegn_frontier.png`

**Роль:** `focused_scene`. Эталон организованной frontier estate без каменного замка.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `hall_timber_stone`, `granary_stilted`, `house_tenant`, `gate_timber`, `tent_working`, `shed_open` | Hall, granary, tenant houses и tents отделяются; estate остаётся композицией. |
| Инфраструктура | `palisade_segment`, `palisade_corner`, `gate_timber`, `bridge_timber`, `stream`, `path_dirt`, `field_fence` | Straight, corner, gate и bridge modules имеют разные connection points. |
| Пропсы | `field_plot`, `barrel`, `sack`, `crate`, `log_stack`, `bench`, `bucket`, `tent_rope` | Shields и spears остаются equipment-слоями персонажей. |
| Фигуры | `retainer_shield`, `retainer_spear`, `estate_worker_small`, `tenant_small` | Круглые щиты и копья допустимы; plate armor запрещена. |
| Ландшафт | `terrain_forest`, `terrain_stream`, `terrain_mud`, `terrain_field` | Forest depth и stream routing — backdrop/terrain. |

**Слои:** `forest background → stream/ground → paths/gardens → palisade/gates → buildings → props → figures → atmosphere`.

**Footprint:** hall anchor — центр каменного цоколя; granary — центр опор; gate — порог; palisade — midpoint edge; bridge — midpoint crossing.

## 08. Городской квартал — `08_city_quarter.png`

**Роль:** `focused_scene`. Плотный, но локальный квартал без capital-city monumentality.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `house_stone_timber`, `house_timber`, `roof_slate`, `roof_thatch`, `awning_workshop`, `forge_open`, `stall_butcher` | Перекрытые фасады остаются street assembly; prototypes домов отделяются. |
| Инфраструктура | `street_mud`, `well_stone`, `stone_wall_segment`, `workshop_awning`, `puddle_mud` | Street network и courtyard остаются layout master; well и forge — самостоятельные объекты. |
| Пропсы | `workbench`, `bench`, `barrel`, `crate`, `sack`, `ceramic_pot`, `tool_iron`, `cart_loaded`, `goods_hung` | Товары у навеса отделяются от architecture. |
| Фигуры | `craftsman_small`, `porter_small`, `vendor_small`, `town_worker_small` | Ремесло читается через workstation и carried goods. |
| Ландшафт | `terrain_street_mud`, `terrain_stone`, `terrain_moss` | Puddles, moss и tracks — decals. |

**Слои:** `street ground → building bases → facades → roofs → awnings/stalls → well/forge → props/figures → smoke/glow/shadows`.

**Footprint:** building footprint считается по стенам; roof отделяется и может иметь overhang. Forge, well и cart anchor по основанию или осям.

## 09. Разведка и редкая находка — `09_scouting_discovery.png`

**Роль:** `atmospheric_vignette`. Источник mood и scout equipment, но не цельный production tile.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | `hut_rough`, `shelter_timber` | Hut выделяется как самостоятельный объект; лесная расчистка остаётся backdrop. |
| Инфраструктура | `path_dirt`, `camp clearing`, `stream_ravine` | Нет регулярной дорожной сети; track — decal. |
| Пропсы | `campfire_ring`, `bedroll`, `clay_pot`, `branch_stack`, `stone_small`, `mushroom` | Campfire и smoke отделяются от hut. |
| Фигуры | `scout_forester` с луком и колчаном | Фигура мала в будущем tile; pose используется как discovery cue. |
| Ландшафт | `terrain_forest`, `terrain_moss_rock`, `terrain_stream`, `terrain_mud` | Дальние кроны и овраг остаются глубинной композицией. |

**Слои:** `far forest → ground/rocks → stream/ravine → hut/camp → scout → smoke/mist`.

**Footprint:** scout anchor — ступни; hut — нижний край входа; campfire — центр каменного кольца. Сцена не должна натягиваться на production hex без реконструкции глубины.

## 10. Ресурсы, товары и ремёсла — `10_goods_crafts_board.png`

**Роль:** `asset_board`. Изолированные сырьевые, ремесленные и транспортные модули.

| Категория | Видимые модули | Нарезка и использование |
|---|---|---|
| Строения | нет | Board состоит только из objects и небольших clusters. |
| Инфраструктура | нет | Cart, fish rack и boat являются props/transport, а не infrastructure. |
| Пропсы | `peat_brick_stack`, `salt_pile`, `grain_sack`, `wool_bale`, `straw_bundle`, `fish_rack`, `cart_loaded`, `plank_stack`, `log_stack`, `ceramic_pot`, `bowl`, `tool_iron`, `rope_coil`, `boat_small`, `fishing_net` | Каждый объект пригоден для отдельной transparent plate. Resource clusters можно сохранить вместе при совпадении anchor. |
| Фигуры | нет | Human scale задаётся позже по `03`, `04`, `07`, `08`. |
| Ландшафт | нет | Тёмно-синий фон и contact shadows отделяются от объектов. |

**Слои:** `contact shadows → resource stacks → food/fish → containers/ceramics → tools/rope → boat/net`.

**Footprint:** object anchor — нижняя точка опоры; cart — между осями; boat — центр hull; fish rack — между ножками; sacks и piles — по центру основания.

## Сводный реестр кандидатов

| Семейство | Кандидаты |
|---|---|
| Terrain | `terrain_hill`, `terrain_field`, `terrain_pasture`, `terrain_marsh`, `terrain_river`, `terrain_forest`, `terrain_quarry`, `terrain_salt_flat`, `terrain_peat`, `terrain_street_mud` |
| Buildings | `cottage_gable`, `house_long_thatch`, `house_stone_timber`, `manor_hill`, `hall_timber_stone`, `granary_stilted`, `barn_plank`, `shed_open`, `hut_working`, `watchtower_timber`, `tavern_slate` |
| Infrastructure | `road_dirt`, `road_cobble`, `fence_log`, `palisade_segment`, `gate_timber`, `bridge_timber`, `boardwalk_marsh`, `pier_timber`, `ford_stones`, `well_stone`, `salt_basin_frame`, `peat_channel`, `market_awning` |
| Resources | `salt_pile`, `peat_brick_stack`, `peat_stack`, `log_stack`, `plank_stack`, `grain_sack`, `wool_bale`, `fish_rack`, `produce_bed`, `field_bundle` |
| Props | `barrel`, `crate`, `sack`, `bench`, `handcart`, `cart_loaded`, `wagon_covered`, `fishing_net`, `rope_coil`, `ceramic_pot`, `tool_iron`, `firepit`, `workbench` |
| Actors | `villager_adult`, `craftsman_small`, `estate_worker_small`, `retainer_shield`, `scout_forester`, `horse`, `sheep`, `pig`, `chicken` |

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
files = sorted(path.name for path in root.glob("*.png"))
assert files == expected
for name in expected:
    data = (root / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width >= 1200 and height >= 672
text = Path("design/art/ASSET_BREAKDOWN.md").read_text(encoding="utf-8")
assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
for marker in ("00_master_moodboard", "10_goods_crafts_board", "anchor", "footprint", "Сводный реестр"):
    assert marker in text
print("asset breakdown: 11/11")
PY
```

Ручной критерий: для каждого принятого изображения элементы реестра видны без домысливания; `00`, `01`, `04`, `06`, `07`, `08` не используются как transparent sprite plates; `09` не используется как production tile; `02` и `10` допускают изоляцию объектов с удалением фона и сохранением `contact_shadow`.

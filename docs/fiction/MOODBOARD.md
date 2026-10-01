# Moodboard: визуальный язык HILLCOURT

## Назначение

Это арт-замысел для Gemini Omni и будущего графического контура: не описание отдельного скриншота, а набор связанных иллюстраций, из которых потом можно собрать тайлы, settlement pieces, manor, порт, город, дороги, предметы и большие карты. Визуальный язык должен показывать Early Medieval frontier manor: земля, дерево, вода, соль, торф, пашня и люди — без магии, империи и современного фэнтези.

## Правила

### Общий visual lock

Все промпты ниже используют одну и ту же стилистику. Перед каждым отдельным промптом добавляй этот блок:

```text
Common visual lock for HILLCOURT:
2D isometric illustrated game art, orthographic three-quarter view, no perspective distortion, no 3D render, no photorealism, designed to be readable and later reduced into pixel-art game assets. Early medieval northern-European frontier, weathered timber, thatch, earth, iron, wool, rope, wood, dirt roads, restrained hand-painted texture, clear silhouettes, strong shape hierarchy, readable at small tile size, subtle material detail, lived-in but not decorative, quiet and serious, dark naturalism rather than grimdark. Palette: peat brown, charcoal blue-grey, moss green, muted olive, slate, oxidized iron, wheat ochre, salt white, rust red, dull amber hearth light, cold river blue. Lighting is natural and restrained: overcast daylight, damp air, late summer or early autumn, occasional firelight; no neon, no fantasy glow, no bright heroic saturation. The land is a large working barony, not an empire and not a city. No readable text, labels, logo, watermark, UI frame, border, title card, map text, or infographic. No magic, monsters, dragons, fantasy spells, plate-armored knights, modern objects, skyscrapers, steampunk, or grand palace architecture.
```

### Master prompt для moodboard

```text
Create a high-resolution 2D isometric visual moodboard for a serious early-medieval frontier management game called HILLCOURT. Show a large but believable barony landscape: a low wooded hill with a modest timber manor and its granary, nearby fields and footpaths, a small farming settlement, a dirt road, a narrow river with a simple ford, a distant salt basin, a small port settlement, woods, peat ground, pasture, and several tiny settlements whose forms grow out of local resources. The image must feel like a premium painted isometric strategy-game backdrop that can later be cut into 2D pixel-art tiles. Use orthographic isometric composition, readable silhouettes, muted natural colors, weathered materials, damp air, and low-key natural lighting. The world should feel inhabited, economically specific, and full of possible stories, not empty scenery. Tiny people, animals, carts, tools, smoke, boats, and roads are welcome as scale cues, but no readable interface or text. No castle empire, no magic, no fantasy monsters, no 3D, no photorealism.
```

### Десять иллюстраций

#### 1. Общая земля баронства

```text
Use the common visual lock. Create a wide isometric overview of a large early-medieval barony, not a single close-up village. A modest hill manor sits slightly off-center; a dirt road links nearby fields, a river crossing, a small port, a salt basin, pasture, peat wetlands, several resource settlements, a thegn's distant estate, a forest edge, and a few distant villages. The settlements must have visibly different economic identities: farming, fishing, forestry, peat-cutting, and salt-making. Leave quiet empty land between them so the map feels large and unexplored. Tiny travelers and one caravan show scale. No labels, no borders, no interface.
```

#### 2. Семейство сельских тайлов

```text
Use the common visual lock. Create a clean asset concept sheet showing four related isometric settlement tiles at exactly the same camera angle, scale, lighting, and footprint: one small timber house; two small houses and a shared workyard; three houses with a narrow path and a small field; a full rural village tile with five compact household buildings, fences, hay, a well or communal work area, and a dirt road. The growth must feel organic, not like a city menu. Use simple silhouettes, controlled detail, and a clear color key that can be converted into separate pixel-art tiles. No labels, no UI, no text, no repeated building kits, no modern objects.
```

#### 3. Холм и усадьба

```text
Use the common visual lock. Create an isometric asset illustration of a modest early-medieval hill manor: a timber hall with a steep thatched roof, a smaller hall or service building, a granary, a simple fence or earthwork, a few domestic animals, a kitchen yard, and narrow fields below the hill. The manor should feel like a real working household seat, not a castle for a king. Keep the silhouette clear and compact enough to become a special estate tile or a multi-tile landmark. Add a dirt track, stacked timber, tools, smoke, and a muted view toward distant water.
```

#### 4. Деревня, ярмарка и таверна

```text
Use the common visual lock. Create an isometric rural crossroads settlement with several small timber-and-thatch houses, kitchen gardens, animal pens, a shared well, a rough market awning, and a modest roadside tavern or posting house. Farmers, a trader, a mule cart, a hunter, and a few travelers should be tiny scale cues. The place should feel like a real village economy, not a fantasy town: muddy ground, repaired fences, smoke, tools, trade goods, and uneven buildings. No grand architecture, no readable signs, no UI, no modern objects.
```

#### 5. Река, брод и порт

```text
Use the common visual lock. Create an isometric river settlement at a natural crossing: a narrow river, a simple ford, a small landing, two fishing boats, temporary worker huts, drying nets, a modest wooden pier, a salt or grain cart, and a few permanent houses beginning to form around the water. Show a trade route approaching from the forest and a distant road toward the barony. The port should look like a useful place where fish, salt, timber, and grain pass between people, not a grand harbor. No modern port equipment, no text, no UI, no fantasy ships.
```

#### 6. Ресурсные поселения

```text
Use the common visual lock. Create a comparative isometric asset sheet of three remote resource settlements: a small salt works with shallow basins and evaporating trays; a peat-cutting hamlet with wet ground, stacked peat, tools, and low huts; and a forest-resource camp with timber stacks, a saw or work yard, carts, and a narrow access path. Each settlement must include a small food and living presence so it reads as a community, not a lone resource icon. Use the same camera, scale, lighting, and palette. No fantasy ores, no magic crystals, no text, no UI, no modern machinery.
```

#### 7. Тэн и пограничная земля

```text
Use the common visual lock. Create an isometric frontier estate belonging to a professional landholder: a compact fortified timber hall, a watch yard, a granary, several small tenant houses, a field strip, a local road, and a guarded bridge or crossing. The estate should feel militarily organized but economically ordinary: spears, shields, a few armed retainers, repair work, stored grain, and trade goods, not a noble fantasy castle. Show a second, smaller settlement beginning near a forest or resource site. No knights, no banners with text, no grand keep, no 3D.
```

#### 8. Городской квартал

```text
Use the common visual lock. Create an isometric early-medieval city quarter: a compact cluster of taller timber-fronted houses, narrow lanes, small workshops, a shared courtyard, a well, carts, a market edge, and a few people carrying goods. The quarter should be denser than a village but still modest and practical, with workshops for a carpenter, smith, miller, butcher, or cooper. Show specialization through tools, goods, and building fronts rather than labels or magical signs. The design should be readable as a future city tile, not a capital city. No skyscrapers, no fantasy guild halls, no readable text, no UI.
```

#### 9. Разведка и редкая находка

```text
Use the common visual lock. Create an isometric forest-edge scene showing a hunter or forester noticing something unusual in the distance: a faint trail of smoke, a small abandoned camp, a hidden hermit's shelter partly hidden by trees, or an old watchtower in the woods. The observer must be small and recognizable by equipment, not a heroic hero. The mood should be quiet investigation, not combat. Include layered terrain, trees, mist, animal tracks, and a narrow path so the same scene can become a scouting or discovery illustration. No random magical glow, no monster, no treasure chest, no text, no UI.
```

#### 10. Ресурсы, товары и ремёсла

```text
Use the common visual lock. Create a clean isometric object-and-material concept board for the barony: salt crystals or coarse salt, peat bricks, cut timber, logs, grain sacks, wool, fish, iron tools, a hand plow, a cart, rope, a simple boat, and a few small craft objects arranged as believable market goods and workshop materials. Use one consistent lighting and scale, with objects grouped naturally rather than as a technical diagram. Materials must look used and hand-made, with restrained color variation and clear silhouettes. No labels, no numbers, no UI, no modern products, no fantasy artifacts, no gems or magical objects.
```

### Общие ограничения для всех результатов

- Не добавлять читаемый текст, логотипы, подписи, рамки или интерфейс.
- Не превращать баронство в королевство, столицу или город-империю.
- Не добавлять магию, драконов, чар, мифические артефакты и фантастические монстры.
- Не делать 3D-рендер, изометрическую фотографию, космическую или современную архитектуру.
- Сохранять тихую, земную, damp/overcast палитру, но не превращать каждую сцену в ночную или grimdark.
- Проверять результат как источник игровых ассетов: силуэт должен читаться, материалы должны различаться, детали должны переживать уменьшение до тайла.

## Проверка

```bash
test -f docs/fiction/MOODBOARD.md
python3 - <<'PY'
from pathlib import Path
text = Path("docs/fiction/MOODBOARD.md").read_text(encoding="utf-8")
assert all(section in text for section in ("## Назначение", "## Правила", "## Проверка"))
for marker in ("Common visual lock", "Master prompt", "Десять иллюстраций", "isometric", "Early medieval"):
    assert marker in text
for forbidden in ("3D render", "photorealism", "magic", "readable text"):
    assert forbidden in text
PY
```

Критерий: все десять промптов используют один visual lock, показывают одну и ту же Early Medieval frontier barony и пригодны для последующего разрезания на изометрические игровые тайлы и предметы; law-код не изменяется.

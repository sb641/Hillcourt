#!/usr/bin/env python3
"""Validate the source -> plate -> game mapping across the whole art library.

`verify_proof.py` proves a plate is the right pixels: geometry, anchor, material,
contour, silhouette. It says nothing about whether the plate means anything. A
salt heap can be a perfect salt heap and still depict a good that the catalog
does not have, on a tile whose terrain the simulation never uses.

This script checks the other half of the chain, in both directions:

  * every plate's data carrier resolves to something real - a good in the
    catalog, a terrain the simulation actually places, an age class the
    ontology documents, a field that is declared or genuinely optional;
  * every library cell that claims a drawn plate has one, and every plate in
    the proof is claimed by exactly one library, the terrain list, or the
    no-carrier registry - no orphans in either direction;
  * library fields resolve: a building's game_forms are real tile_view forms,
    an actor's game_entities are real ontology classes, a good's game_goods are
    real catalog goods;
  * a cell the ontology has not accepted has no plate anywhere;
  * a good mapped by two plates says so, rather than leaving the runtime a
    choice it cannot make.

Run:  python3 design/art/validate_mapping.py
Exit code 0 means the mapping holds.

  --self-test  injects each defect this script exists to catch, one at a time
               into a scratch tree, and requires that every one is reported. A
               validator that has never been watched to fail proves nothing; this
               is the same contract mutation_test.py has for verify_proof.py.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "flat2d_proof"))
from verify_proof import carrier_is_real  # noqa: E402  one rule, one implementation

REPO = Path(__file__).resolve().parents[2]
PROOF_MANIFEST = REPO / "design/art/flat2d_proof/flat2d_proof_manifest.yml"
LIBRARIES = {
    "architecture": REPO / "design/art/asset_library_v1/architecture"
                            / "architecture_manifest.yml",
    "goods": REPO / "design/art/asset_library_v1/items/goods_manifest.yml",
    "actors": REPO / "design/art/asset_library_v1/actors/actors_manifest.yml",
    "hazards": REPO / "design/art/asset_library_v1/hazards/hazards_manifest.yml",
    "camps": REPO / "design/art/asset_library_v1/camps/camps_manifest.yml",
    "sites": REPO / "design/art/asset_library_v1/sites/sites_manifest.yml",
}
PRODUCTION_GRID = REPO / "design/art/production_grid/atlas_manifest.yml"
CATALOG_GOODS = REPO / "design/catalogs/goods.yml"
ONTOLOGY = REPO / "sim/src/hillcourt/ontology.py"
TILE_VIEW = REPO / "sim/src/hillcourt/engine/tile_view.py"
ONTOLOGY_DOC = REPO / "docs/03_ontology.md"
SCENARIOS = REPO / "design/scenarios"
NEEDS = REPO / "design/catalogs/needs.yml"

# Plates the proof keeps for geometry or for pipeline coverage rather than as
# assets. They are the keyed path (key -> trim -> resize -> anchor) and two
# estate parts with no field of their own. The manifest publishes the same list
# and the two must agree; this is the reference side of that agreement.
KEYED_REFERENCE_PREFIX = "keyed_from_render"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def catalog_good_ids():
    data = load(CATALOG_GOODS)
    goods = data.get("goods", data)
    if isinstance(goods, list):
        return {entry["id"] for entry in goods}
    return set(goods)


def scenario_tile_goods():
    """Goods a scenario drops ON A TILE, which are goods the player can see.

    `starting_stocks` keys are stock ids and the `tile:` prefix is the only
    thing that says the matter is lying on the ground rather than sitting in a
    household or a settlement store. Only the `tile:` ones are map matter; a
    store the player never opens is not an object on a tile.
    """
    import yaml
    found: dict[str, str] = {}
    for path in sorted((REPO / "design/scenarios").glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for key, amounts in (data.get("starting_stocks") or {}).items():
            if not str(key).startswith("tile:"):
                continue
            for good, amount in (amounts or {}).items():
                if amount:
                    found.setdefault(str(good), path.name)
    return found


def reachable_camp_forms():
    """The form names `camp_form_for_pack()` can actually return.

    Read out of the code rather than hand-listed, because a hand list is a
    promise and the code is the promise. A camp plate whose `game_form` is not
    in here is a plate for a camp the game cannot produce - which is exactly how
    a picture gets drawn for nothing.
    """
    import re
    path = REPO / "sim/src/hillcourt/engine/tile_view.py"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    start = text.index("def camp_form_for_pack")
    end = text.index("def camp_form(", start)
    return set(re.findall(r'return "([a-z_]+)"', text[start:end]))


def reachable_site_forms():
    """The form names `tile_form()` can actually return.

    A site plate is a TILE FORM: `tile_form()` returns one form per tile and it
    replaces the terrain plate, so a site whose `game_form` is not returned from
    `tile_form()` is a plate the game can never show. Read out of the code for
    the same reason `reachable_camp_forms()` is: a hand list is a promise, and
    the code is the promise.
    """
    import re
    if not TILE_VIEW.exists():
        return set()
    text = TILE_VIEW.read_text(encoding="utf-8")
    start = text.index("def tile_form(")
    return set(re.findall(r'return "([a-z_]+)"', text[start:]))


def land_marks():
    """The named tile marks `has_mark()` will accept, read from tile_view.py.

    A site carried by a mark that is not in LAND_MARKS cannot exist: `has_mark`
    raises on an unknown mark rather than quietly returning False, so a plate
    naming a mark outside the tuple is a plate for a mark the game rejects.
    """
    import re
    if not TILE_VIEW.exists():
        return set()
    text = TILE_VIEW.read_text(encoding="utf-8")
    match = re.search(r"^LAND_MARKS: tuple\[str, \.\.\.\] = \((.*?)^\)",
                      text, re.S | re.M)
    if not match:
        return set()
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def ontology_classes():
    return set(re.findall(r"^class ([A-Za-z_][A-Za-z_0-9]*):",
                          ONTOLOGY.read_text(encoding="utf-8"), re.M))


def tile_view_forms():
    """The form table keys in tile_view.py - the coarse tile-level view names."""
    return set(re.findall(r'^    "([a-z_]+)": \{$',
                          TILE_VIEW.read_text(encoding="utf-8"), re.M))


def documented_age_classes():
    """The age_class set as docs/03 states it, parsed rather than restated.

    If the documentation changes, this follows it and the plates stop matching,
    which is the point: the set of figures is a consequence of the law, not a
    choice made here.
    """
    text = ONTOLOGY_DOC.read_text(encoding="utf-8")
    match = re.search(r"`age_class\s*∈\s*\{([^}]*)\}`", text)
    if not match:
        return None
    return {value.strip().strip("`") for value in match.group(1).split(",")
            if value.strip()}


def catalog_hazard_ids():
    """Every `kind` in design/catalogs/hazards.yml, which is what a
    `Hazard.kind` plate has to resolve to."""
    import yaml
    path = REPO / "design/catalogs/hazards.yml"
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {h["id"] for h in (data.get("hazards") or []) if h.get("id")}


def simulation_terrain_ids():
    """Terrain ids the simulation actually uses: placed by scenarios or tested in code."""
    found = set()
    for path in SCENARIOS.glob("*.yml"):
        found |= set(re.findall(r"terrain:\s*([a-z_]+)",
                                path.read_text(encoding="utf-8")))
    for path in (REPO / "sim/src/hillcourt").rglob("*.py"):
        found |= set(re.findall(r"terrain\s*==\s*\"([a-z_]+)\"",
                                path.read_text(encoding="utf-8")))
    return found


def livestock_keys():
    """The animal keys the simulation actually feeds.

    This is the list the art has to cover, taken from the sim's own feed table
    rather than from a list written here. A species the simulation keeps and the
    art library forgot is the failure this exists to catch.
    """
    needs = load(NEEDS)
    return set(needs.get("livestock", {}).get("feed_per_month") or {})


def terrain_id_of(plate_id):
    """`terrain_salt_flat_hex` -> `salt_flat`."""
    match = re.fullmatch(r"terrain_(.+)_hex", plate_id)
    return match.group(1) if match else None


def main():
    problems = []
    notes = []

    if not PROOF_MANIFEST.exists():
        print(f"mapping: FAIL (no proof manifest at {PROOF_MANIFEST})")
        return 1
    proof = load(PROOF_MANIFEST)
    cells = {cell["id"]: cell for cell in proof["cells"]}
    pipeline = proof.get("pipeline") or {}
    goods_ids = catalog_good_ids()
    classes = ontology_classes()
    hazard_ids = catalog_hazard_ids()
    forms = tile_view_forms()
    age_classes = documented_age_classes()
    terrains = simulation_terrain_ids()
    if age_classes is None:
        problems.append("docs/03_ontology.md no longer states an age_class set; "
                        "the figure count has lost its basis")

    libraries = {name: load(path) for name, path in LIBRARIES.items()}
    grid = load(PRODUCTION_GRID) if PRODUCTION_GRID.exists() else None
    grid_cells = {}
    if grid:
        for atlas in grid.get("atlases") or []:
            for cell in atlas.get("cells") or []:
                grid_cells[cell["id"]] = cell
    lib_cells = {name: {cell["id"]: cell for cell in data["cells"]}
                 for name, data in libraries.items()}

    # --- 1. every carrier value resolves -----------------------------------
    for plate_id, cell in sorted(cells.items()):
        carrier = cell.get("data_carrier")
        if carrier is None:
            continue
        if carrier == "Good.id":
            entry = lib_cells["goods"].get(plate_id)
            if entry is not None:
                mapped = entry.get("game_goods", [])
            else:
                # an animal is not a cell in `cells`: it is a livestock entry
                # that serves several goods of one species
                mapped = next((e.get("serves_goods") or [] for e in
                               (libraries["goods"].get("livestock") or [])
                               if e["id"] == plate_id), [])
            if not mapped:
                problems.append(f"{plate_id}: carries Good.id but the goods "
                                "library maps it to no good")
            for good in mapped:
                if good not in goods_ids:
                    problems.append(f"{plate_id}: good {good!r} is not in "
                                    "design/catalogs/goods.yml")
        elif carrier == "Tile.terrain":
            terrain = terrain_id_of(plate_id)
            if terrain is None:
                problems.append(f"{plate_id}: plate id does not name a terrain, "
                                "so its Tile.terrain carrier cannot be read")
            elif terrain not in terrains:
                problems.append(f"{plate_id}: terrain {terrain!r} is placed by "
                                "no scenario and compared by no simulation code")
        elif carrier == "Person.age_class":
            value = lib_cells["actors"].get(plate_id, {}).get("carrier_value")
            if value is None:
                problems.append(f"{plate_id}: carries Person.age_class but "
                                "declares no carrier_value")
            elif age_classes and value not in age_classes:
                problems.append(f"{plate_id}: age_class {value!r} is not in the "
                                f"set docs/03 states ({sorted(age_classes)})")
        elif carrier == "Hazard.kind":
            value = cell.get("material_carrier_value")
            if value is None:
                problems.append(f"{plate_id}: carries Hazard.kind but declares "
                                "no material_carrier_value")
            elif hazard_ids and value not in hazard_ids:
                problems.append(f"{plate_id}: hazard {value!r} is not in "
                                "design/catalogs/hazards.yml")
        else:
            # Field existence is verify_proof's job and it has the real rule:
            # a declared_field must be a declared dataclass field, an
            # optional_attribute must be read through getattr. Do not re-check
            # it here with a weaker test - that is how a validator starts
            # reporting fields that plainly exist as missing.
            fault = carrier_is_real(carrier, cell.get("data_carrier_kind"))
            if fault:
                problems.append(f"{plate_id}: {fault}")

    # --- 2. no orphan plates, no orphan library cells -----------------------
    terrain_plates = {pid for pid in cells if terrain_id_of(pid)}
    claimed = set(terrain_plates)
    for name, table in lib_cells.items():
        for cell_id, cell in table.items():
            if cell.get("status") == "drawn_flat_2d":
                if cell_id not in cells:
                    problems.append(f"{name}: {cell_id} claims status "
                                    "drawn_flat_2d but has no plate in the proof")
                else:
                    claimed.add(cell_id)
            elif cell_id in cells and cell.get("material") == "drawn":
                problems.append(f"{name}: {cell_id} has a drawn plate but the "
                                f"library status is {cell.get('status')!r}")
    # the livestock section claims plates the same way cells do
    livestock_ids = {entry["id"] for entry
                     in (libraries["goods"].get("livestock") or [])}
    claimed |= livestock_ids
    clothing_ids = {entry["id"] for entry
                    in (libraries["actors"].get("clothing", {}).get("plates")
                        or [])}
    claimed |= clothing_ids
    for plate_id in sorted(set(cells) - claimed):
        material = cells[plate_id].get("material")
        if material == KEYED_REFERENCE_PREFIX or cells[plate_id].get(
                "data_carrier") is None:
            claimed.add(plate_id)     # declared in the no-carrier registry
    for plate_id in sorted(set(cells) - claimed):
        problems.append(f"{plate_id}: plate exists in the proof but no library "
                        "cell claims it and it is not a declared reference")

    # --- 3. the no-carrier registry agrees with the cells -------------------
    declared_no_carrier = set(pipeline.get("no_data_carrier") or [])
    actual_no_carrier = {pid for pid, cell in cells.items()
                         if cell.get("data_carrier") is None}
    for plate_id in sorted(actual_no_carrier - declared_no_carrier):
        problems.append(f"{plate_id}: has no data_carrier but is not listed in "
                        "pipeline.no_data_carrier")
    for plate_id in sorted(declared_no_carrier - actual_no_carrier):
        problems.append(f"{plate_id}: listed in pipeline.no_data_carrier but "
                        "the cell does declare a carrier")
    if not declared_no_carrier:
        problems.append("pipeline.no_data_carrier is missing; a carrier-less "
                        "plate could appear without being declared")

    # --- 4. library fields resolve against the game side --------------------
    for cell_id, cell in sorted(lib_cells["architecture"].items()):
        for form in cell.get("game_forms") or []:
            if form not in forms:
                problems.append(f"{cell_id}: game_form {form!r} is not a form "
                                "in tile_view.py")
    for cell_id, cell in sorted(lib_cells["actors"].items()):
        for entity in cell.get("game_entities") or []:
            if entity not in classes:
                problems.append(f"{cell_id}: game_entity {entity!r} is not a "
                                "class in ontology.py")
    for cell_id, cell in sorted(lib_cells["goods"].items()):
        for good in cell.get("game_goods") or []:
            if good not in goods_ids:
                problems.append(f"{cell_id}: game_good {good!r} is not in "
                                "design/catalogs/goods.yml")

    # --- 5. one good, two plates: the choice must be declared ---------------
    by_good = {}
    for cell_id, cell in sorted(lib_cells["goods"].items()):
        for good in cell.get("game_goods") or []:
            by_good.setdefault(good, []).append(cell_id)
    for good, plate_ids in sorted(by_good.items()):
        if len(plate_ids) < 2:
            continue
        variants = [pid for pid in plate_ids
                    if lib_cells["goods"][pid].get("variant_of")]
        if len(variants) != len(plate_ids) - 1:
            problems.append(
                f"good {good!r} is mapped by {len(plate_ids)} plates "
                f"({', '.join(plate_ids)}) and only {len(variants)} declare "
                "variant_of; the runtime has no way to choose")

    # --- 6. livestock: every animal the sim feeds has exactly one plate -----
    served = {}
    for entry in libraries["goods"].get("livestock") or []:
        plate_id = entry["id"]
        for good in entry.get("serves_goods") or []:
            if good in served:
                problems.append(
                    f"good {good!r} is served by two plates: {served[good]} and "
                    f"{plate_id}")
            served[good] = plate_id
            if good not in goods_ids:
                problems.append(f"{plate_id}: serves good {good!r}, which is "
                                "not in design/catalogs/goods.yml")
        if plate_id not in cells:
            problems.append(f"{plate_id}: livestock entry has no plate in the "
                            "proof")
        elif cells[plate_id].get("material") != "drawn":
            problems.append(f"{plate_id}: livestock entry points at a plate "
                            "that is not drawn")
    wanted = livestock_keys()
    for good in sorted(wanted - set(served)):
        problems.append(f"animal {good!r} is fed by the simulation but no "
                        "livestock plate serves it")
    for good in sorted(set(served) - wanted):
        notes.append(f"note: {good!r} is served by a plate but is not in the "
                     "feed table (it may be a product, not a live animal)")
    for plate_id in sorted(cells):
        if plate_id.startswith("animal_") and plate_id not in {
                entry["id"] for entry in
                (libraries["goods"].get("livestock") or [])}:
            problems.append(f"{plate_id}: drawn animal is not listed in the "
                            "goods library livestock section")

    # --- 7. an entity the ontology has not accepted has no plate ------------
    for cell_id, cell in sorted(lib_cells["architecture"].items()):
        if cell.get("status") == "needs_ontology_check" and cell_id in cells:
            problems.append(f"{cell_id}: status is needs_ontology_check but the "
                            "proof has a plate for it; blocked means no art")
    for cell_id in sorted((pipeline.get("actor_excluded") or {})):
        if cell_id in cells:
            problems.append(f"{cell_id}: listed as actor_excluded but the proof "
                            "has a plate for it")

    # --- 7. the isometric production grid points at the drawn plates -------
    # The grid's own `production_status` stays honest about its own layer: a
    # module there is still an isometric crop, because that is what it is. What
    # was missing is a pointer to the drawn flat 2D plate, so a reader concluded
    # the asset only ever existed as a crop. The pointer is what can go stale,
    # so it is checked in both directions and against the carrier table.
    if grid is None:
        problems.append("design/art/production_grid/atlas_manifest.yml is "
                        "missing; the isometric grid has no index of drawn plates")
    else:
        linked = {}
        for cell_id, cell in sorted(grid_cells.items()):
            plate = cell.get("flat_2d_plate")
            if not plate:
                continue
            plate_id = plate.rsplit("/", 1)[-1].removesuffix(".png")
            linked[cell_id] = plate_id
            if plate_id not in cells:
                problems.append(f"{cell_id}: flat_2d_plate names {plate_id}, "
                                "which the proof does not have")
                continue
            if cells[plate_id].get("material") != "drawn":
                problems.append(f"{cell_id}: flat_2d_plate points at "
                                f"{plate_id}, which is not a drawn plate")
            if cell.get("flat_2d_status") != "drawn":
                problems.append(f"{cell_id}: has a flat_2d_plate but "
                                f"flat_2d_status is {cell.get('flat_2d_status')!r}")
            if "flat_2d_data_carrier" not in cell:
                problems.append(f"{cell_id}: flat_2d_data_carrier is not declared")
            elif cell["flat_2d_data_carrier"] != cells[plate_id].get("data_carrier"):
                problems.append(
                    f"{cell_id}: flat_2d_data_carrier "
                    f"{cell['flat_2d_data_carrier']!r} disagrees with the proof "
                    f"({cells[plate_id].get('data_carrier')!r})")
            elif cell["flat_2d_data_carrier"] is None and not cell.get("flat_2d_note"):
                problems.append(f"{cell_id}: flat_2d_data_carrier is null with "
                                "no flat_2d_note")
        # the other direction: every drawn building is reachable from the grid
        for plate_id in sorted(cells):
            if not plate_id.startswith("architecture_"):
                continue
            if cells[plate_id].get("material") != "drawn":
                continue
            if plate_id not in linked.values():
                problems.append(f"{plate_id}: drawn building is not linked from "
                                "the isometric production grid")
        # blocked and un-isolated cells must have no flat 2D plate at all
        for cell_id, cell in sorted(grid_cells.items()):
            status = cell.get("production_status")
            if status in ("needs_ontology_check", "needs_isolation") and \
                    cell.get("flat_2d_plate"):
                problems.append(f"{cell_id}: production_status is {status} but "
                                "it points at a flat 2D plate; blocked means no art")

    # --- 9. the clothing layers line up with the proof -----------------------
    clothing = libraries["actors"].get("clothing") or {}
    if not clothing:
        problems.append("actors manifest has no clothing section; sixteen "
                        "garment plates are unaccounted for")
    else:
        if clothing.get("sprite_selector", "none") != "none":
            problems.append("clothing.sprite_selector must be none while "
                            "client/ is frozen")
        for entry in clothing.get("plates") or []:
            plate_id = entry["id"]
            if plate_id not in cells:
                problems.append(f"{plate_id}: clothing entry has no plate in "
                                "the proof")
                continue
            for field in ("material_layer", "material_wealth_step",
                          "material_age_class", "body_id"):
                if entry.get(field) != cells[plate_id].get(field):
                    problems.append(
                        f"{plate_id}: clothing {field} "
                        f"{entry.get(field)!r} disagrees with the proof "
                        f"({cells[plate_id].get(field)!r})")
        # every drawn layer must be in the library, and every layer the proof
        # draws for a body must name the body it overlays
        declared_ages = clothing.get("age_classes") or []
        if not declared_ages:
            problems.append("clothing.age_classes is missing; the bodies a "
                            "layer is drawn for are not declared")
        by_base = {}
        for plate_id, cell in sorted(cells.items()):
            if not plate_id.startswith("garment_"):
                continue
            if plate_id not in clothing_ids:
                problems.append(f"{plate_id}: drawn garment is not in the "
                                "actors manifest clothing section")
            elif not cell.get("material_carrier_field"):
                problems.append(f"{plate_id}: a garment must name the field it "
                                "answers to")
            body = cell.get("body_id")
            if not body:
                problems.append(f"{plate_id}: a garment must name the body it "
                                "overlays")
            elif body not in cells:
                problems.append(f"{plate_id}: body_id {body!r} is not a plate in "
                                "the proof")
            by_base.setdefault(plate_id.rsplit("_", 1)[0], set()).add(
                cell.get("material_age_class"))
        # A garment exists once per body. A layer drawn for the adult and not
        # for the child means the child of a thegn is drawn in nothing, or in
        # the adult's cut - and no single plate is wrong in isolation, so this
        # is the only place the gap shows up.
        for base, ages in sorted(by_base.items()):
            if declared_ages and ages != set(declared_ages):
                problems.append(
                    f"{base}: drawn for {sorted(ages)} but clothing "
                    f"declares {sorted(declared_ages)}; a layer is drawn once "
                    "per body, or the missing body wears nothing")

    # --- 9a. matter a scenario puts ON A TILE must be drawn -----------------
    # This is the check whose absence let a real hole through. The proof
    # verified that every PLATE resolved into the game, and never that every
    # VISIBLE THING resolved to a plate, so `stone` sat unplated next to `log`
    # in the same stock line, in all nine scenarios, with every check green:
    #   "tile:t_00_01": {log: 12.0, stone: 12.0}
    # A player looking at that tile saw stumps and no stones.
    tile_goods = scenario_tile_goods()
    # The serving map is authoritative in the LIBRARY, not in the proof
    # manifest: a plate for a heap of salt declares `game_goods: [salt]`, and
    # one plate may serve several goods. Reading the proof manifest instead
    # reported `log` and `grain` as unserved while they are drawn, which is the
    # same class of error as the hole this check exists to close - a green
    # result from the wrong source.
    served_goods: dict[str, list[str]] = {}
    for entry in (libraries["goods"].get("cells") or []):
        for good in (entry.get("game_goods") or []):
            served_goods.setdefault(str(good), []).append(entry["id"])
    for entry in (libraries["goods"].get("livestock") or []):
        for good in (entry.get("serves_goods") or []):
            served_goods.setdefault(str(good), []).append(entry["id"])
    for good, scenario in sorted(tile_goods.items()):
        if not served_goods.get(good):
            problems.append(
                f"good {good!r} is placed on a TILE by {scenario} but no plate "
                "serves it; map matter the player can see has to be drawn, or "
                "the tile renders half of what it holds")

    # --- 9b. every hazard in the catalog has exactly one plate, and every
    #         hazard plate names a hazard the catalog actually has -----------
    hazard_cells = {pid: c for pid, c in cells.items()
                    if c.get("data_carrier") == "Hazard.kind"}
    served: dict[str, list[str]] = {}
    for pid, c in hazard_cells.items():
        served.setdefault(c.get("material_carrier_value"), []).append(pid)
    for hazard_id in sorted(hazard_ids):
        plates = served.get(hazard_id) or []
        if not plates:
            problems.append(
                f"hazard {hazard_id!r} is in design/catalogs/hazards.yml but no "
                "plate carries it; the runtime reads Hazard.kind, so the kind "
                "has to resolve to art somewhere")
        elif len(plates) > 1:
            problems.append(
                f"hazard {hazard_id!r} is served by {sorted(plates)}; exactly "
                "one plate per kind, or the runtime cannot choose")
    for pid, c in sorted(hazard_cells.items()):
        if c.get("material_carrier_value") not in hazard_ids:
            problems.append(f"{pid}: plate carries hazard "
                            f"{c.get('material_carrier_value')!r}, which is not "
                            "in design/catalogs/hazards.yml")

    # --- 9c. a camp plate must be a camp the code can actually put on a tile
    reachable = reachable_camp_forms()
    if not reachable:
        problems.append("cannot read camp_form_for_pack from tile_view.py; the "
                        "camp plates are unverifiable")
    camp_cells = {pid: c for pid, c in cells.items()
                  if c.get("game_form")}
    for pid, cell in sorted(camp_cells.items()):
        form = cell["game_form"]
        if form in reachable:
            continue
        # a form the code can also return from elsewhere, e.g. lost_caravan,
        # is fine as long as it is a real tile_view form at all
        if form in tile_view_forms():
            continue
        problems.append(
            f"{pid}: game_form {form!r} is neither one camp_form_for_pack can "
            f"return ({sorted(reachable)}) nor a real tile_view form; a camp "
            "plate for a form the game cannot produce is a picture of nothing")

    # --- 9d. a site plate must be a form tile_form() can return, and its
    # carrier must be one the runtime really reads.
    #
    # Note the key names: the proof manifest calls these `data_carrier` and
    # `data_carrier_kind`. My first version of this check read `carrier_kind`,
    # matched no cell at all, and passed - a check that cannot fail is not a
    # check. The self-test below is what made it visible.
    reachable = reachable_site_forms()
    marks = land_marks()
    if not reachable:
        problems.append("cannot read tile_form from tile_view.py; the site "
                        "plates are unverifiable")
    if not marks:
        problems.append("cannot read LAND_MARKS from tile_view.py; site marks "
                        "are unverifiable")
    ontology_text = ONTOLOGY.read_text(encoding="utf-8")
    site_cells = {pid: c for pid, c in cells.items()
                  if str(c.get("id", "")).startswith("site_")}
    if not site_cells:
        problems.append("no site_ cells found in the proof manifest; the site "
                        "check would be a no-op")
    for pid, cell in sorted(site_cells.items()):
        form = cell.get("game_form")
        if form not in reachable:
            problems.append(
                f"{pid}: game_form {form!r} is not one tile_form() can return "
                f"({sorted(reachable)}); a site plate for a form the game can "
                "never show is a picture of nothing")
        carrier = cell.get("data_carrier", "")
        kind = cell.get("data_carrier_kind", "")
        if kind == "optional_attribute":
            # a named mark: has_mark() raises on anything outside LAND_MARKS,
            # so a mark the tuple does not have is a carrier that cannot exist
            mark = carrier.split(".", 1)[-1]
            if mark not in marks:
                problems.append(
                    f"{pid}: carrier {carrier!r} is not in LAND_MARKS "
                    f"({sorted(marks)}); has_mark() raises on an unknown mark, "
                    "so this carrier cannot exist")
        elif kind == "declared_field":
            if carrier.startswith("Settlement."):
                if carrier != "Settlement.kind":
                    problems.append(
                        f"{pid}: a Settlement carrier must be Settlement.kind, "
                        f"not {carrier!r}; is_salt_settlement and "
                        "is_native_settlement both read settlement.kind")
            else:
                field = carrier.split(".", 1)[-1]
                if not re.search(rf"^\s+{field}:", ontology_text, re.M):
                    problems.append(
                        f"{pid}: carrier {carrier!r} is declared a field but "
                        f"{field!r} is not a field on any ontology dataclass")
        else:
            problems.append(
                f"{pid}: data_carrier_kind is {kind!r}, which says nothing about "
                "how the carrier is read; a carrier of unknown kind is not "
                "verified by anything")

    # --- 10. no library may claim a sprite selector -------------------------
    for name, data in libraries.items():
        block = data.get("flat_2d_proof") or data.get("drawn_figures") or {}
        if block.get("sprite_selector", "none") != "none":
            problems.append(f"{name}: flat_2d_proof.sprite_selector must be "
                            "none while client/ is frozen")

    # --- report -------------------------------------------------------------
    notes.append(f"plates checked: {len(cells)}")
    notes.append(f"terrain ids the sim uses: {len(terrains)}")
    notes.append(f"catalog goods: {len(goods_ids)}")
    notes.append(f"tile_view forms: {len(forms)}")
    notes.append(f"plates with no data carrier: {len(actual_no_carrier)} "
                 f"({', '.join(sorted(actual_no_carrier))})")
    for note in notes:
        print(f"  {note}")
    if problems:
        print(f"mapping: FAIL ({len(problems)} problems)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("mapping: OK (every plate resolves, no orphans, no undeclared "
          "carrier-less plate)")
    return 0


# --- self-test -------------------------------------------------------------

# (label, file relative to the repo root, old text, new text, expected report)
SELF_TEST_CASES = [
    # The check that matters most: a good a scenario drops on a TILE and no
    # plate serves. `stone` sat unplated next to `log` in the same stock line
    # in all nine scenarios with every check green.
    ("a good on a tile in a scenario has no plate",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "    game_goods: [stone]", "    game_goods: []",
     "but no plate serves it"),
    # A site for a form `tile_form()` cannot return. I wrote nine carriers as
    # declared Tile fields before reading `has_mark()`; they are named marks, and
    # this case is what catches the next one that guesses.
    ("a site plate for a form the game cannot show",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    game_form: pier",
     "    game_form: knight_keep",
     "is not one tile_form() can return"),
    # A site carried by a mark `has_mark()` would reject: it raises on an unknown
    # mark rather than returning False, so the mark cannot exist.
    ("a site carried by a mark LAND_MARKS does not have",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    data_carrier: Tile.mooring",
     "    data_carrier: Tile.moon_gate",
     "is not in LAND_MARKS"),
    # A site whose carrier claims to be a real field but is not one.
    ("a site claims a tile field the ontology does not have",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    data_carrier: Tile.ruin_id",
     "    data_carrier: Tile.mooring",
     "is declared a field but"),
    # A carrier whose kind says nothing about how it is read.
    ("a site has a carrier of unknown kind",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    data_carrier_kind: declared_field",
     "    data_carrier_kind: whatever",
     "which says nothing about how the carrier is read"),
    # A camp for a form the code cannot produce. `reachable_camp_forms()` reads
    # the return values straight out of `camp_form_for_pack`, so this cannot rot.
    ("a camp plate for a form the game cannot produce",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    game_form: wagon_camp", "    game_form: knight_camp",
     "is neither one camp_form_for_pack can return"),
    # A hazard in the catalog with no plate is invisible from the art side: the
    # kind simply resolves to nothing and the runtime still reads it. Same in
    # reverse - a plate for a kind the catalog dropped.
    ("a hazard carries a kind the catalog does not have",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    material_carrier_value: band",
     "    material_carrier_value: wargs",
     "is not in design/catalogs/hazards.yml"),
    ("a hazard plate is served twice by one kind",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    material_carrier_value: wolves",
     "    material_carrier_value: bog",
     "is served by"),
    ("a good that is not in the catalog",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "game_goods: [salt]", "game_goods: [sallt]",
     "is not in design/catalogs/goods.yml"),
    ("a game_form that is not a tile_view form",
     "design/art/asset_library_v1/architecture/architecture_manifest.yml",
     "game_forms: [timber_house, village]",
     "game_forms: [timber_house, villagge]",
     "is not a form in tile_view.py"),
    ("a game_entity that is not an ontology class",
     "design/art/asset_library_v1/actors/actors_manifest.yml",
     "game_entities: [Person]", "game_entities: [Persson]",
     "is not a class in ontology.py"),
    # Marking the watchtower as drawn cannot reach the blocked-with-a-plate
    # branch on its own, because it has no plate to be inconsistent with: the
    # orphan check reports that first, which is the more precise complaint. To
    # reach the branch the mutation is inverted - a cell that does have a plate
    # is marked as blocked, which is the same rule seen from the other side.
    ("a cell with a plate marked as blocked by the ontology",
     "design/art/asset_library_v1/architecture/architecture_manifest.yml",
     "  - id: architecture_gate\n    name: Gate and fence\n"
     "    source_file: references/detail_style_anchor.png\n"
     "    source_crop_px: [180, 750, 65, 95]\n"
     "    game_forms: [hall_on_hill, thegn_estate]\n"
     "    status: drawn_flat_2d",
     "  - id: architecture_gate\n    name: Gate and fence\n"
     "    source_file: references/detail_style_anchor.png\n"
     "    source_crop_px: [180, 750, 65, 95]\n"
     "    game_forms: [hall_on_hill, thegn_estate]\n"
     "    status: needs_ontology_check",
     "blocked means no art"),
    ("an entity the ontology has not accepted, marked as drawn",
     "design/art/asset_library_v1/architecture/architecture_manifest.yml",
     "    status: needs_ontology_check", "    status: drawn_flat_2d",
     "has no plate in the proof"),
    ("an age class the documentation does not list",
     "design/art/asset_library_v1/actors/actors_manifest.yml",
     "carrier_value: elder", "carrier_value: teenager",
     "is not in the set docs/03 states"),
    ("a plate wrongly listed as having no carrier",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "  no_data_carrier:\n", "  no_data_carrier:\n    - architecture_ruin_hex\n",
     "does declare a carrier"),
    ("a library cell claiming a plate that does not exist",
     "design/art/asset_library_v1/architecture/architecture_manifest.yml",
     "  - id: architecture_gate\n", "  - id: architecture_gate_typo\n",
     "has no plate in the proof"),
    ("a terrain the simulation never places",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "terrain_heath_hex", "terrain_glacier_hex",
     "placed by no scenario"),
    # The "no carrier declared at all" rule lives in verify_proof.py. What this
    # script can see is the registry disagreeing with the cells, and that is the
    # message it gives - which is the right one, because the registry is its own
    # subject.
    ("a plate the manifest never declares a carrier for",
     "design/art/flat2d_proof/flat2d_proof_manifest.yml",
     "    data_carrier: Good.id", "    note: Good.id",
     "has no data_carrier but is not listed in pipeline.no_data_carrier"),
    # --- the livestock ------------------------------------------------------
    # Dropping a real fed key - not inventing a fake one. A fake good trips the
    # catalog check first, which is a different rule and the more precise fault.
    ("an animal the simulation feeds has no plate",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "    serves_goods: [ox_m, ox_f, ox_calf]", "    serves_goods: [ox_m, ox_f]",
     "is fed by the simulation but no livestock plate serves it"),
    ("one animal served by two plates",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "    serves_goods: [hen]", "    serves_goods: [hen, duck, goose]",
     "is served by two plates"),
    # Renaming the library entry orphans the plate, and the Good.id resolution
    # notices first - that is the honest first fault, so that is what is asserted.
    ("an animal plate no library entry resolves",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "  - id: animal_duck\n    name: Duck\n    status: drawn_flat_2d",
     "  - id: animal_duck_typo\n    name: Duck\n    status: drawn_flat_2d",
     "carries Good.id but the goods library maps it to no good"),
    # --- the isometric production grid index --------------------------------
    ("the grid points at a plate the proof does not have",
     "design/art/production_grid/atlas_manifest.yml",
     "flat_2d_plate: flat2d_proof/plates/architecture_bridge.png",
     "flat_2d_plate: flat2d_proof/plates/architecture_drawfbridge.png",
     "which the proof does not have"),
    ("the grid disagrees with the proof about a carrier",
     "design/art/production_grid/atlas_manifest.yml",
     "flat_2d_data_carrier: Tile.bridge",
     "flat_2d_data_carrier: Tile.road",
     "disagrees with the proof"),
    ("a drawn building the grid never links",
     "design/art/production_grid/atlas_manifest.yml",
     "        flat_2d_plate: flat2d_proof/plates/architecture_gate.png\n",
     "",
     "is not linked from"),
    # The whole flat_2d block has to be added, not just the plate: a plate with
    # no flat_2d_status trips the more precise check first, which is the correct
    # complaint but not the rule this case is meant to exercise.
    ("a cell blocked by the ontology points at a plate",
     "design/art/production_grid/atlas_manifest.yml",
     "        production_status: needs_ontology_check",
     "        production_status: needs_ontology_check\n"
     "        flat_2d_plate: flat2d_proof/plates/architecture_gate.png\n"
     "        flat_2d_status: drawn\n"
     "        flat_2d_data_carrier: null\n"
     "        flat_2d_note: forced by the self-test",
     "blocked means no art"),
    ("a grid cell claims a plate without saying it is drawn",
     "design/art/production_grid/atlas_manifest.yml",
     "        flat_2d_status: drawn",
     "        flat_2d_status: maybe",
     "flat_2d_status is"),
    ("a good mapped by two plates with no declared variant",
     "design/art/asset_library_v1/items/goods_manifest.yml",
     "    variant_of: good_boat", "    variant_note: removed",
     "the runtime has no way to choose"),
]

# The scratch tree symlinks everything the validator only reads, and copies the
# files a case mutates. Copying the whole repository per case would make the
# self-test slower than the thing it is testing.
SCRATCH_LAYOUT = [
    "sim/src/hillcourt", "design/scenarios", "design/catalogs",
    "docs/03_ontology.md",
    "design/art/production_grid/atlas_manifest.yml",
    # verify_proof.py is imported for carrier_is_real, so the scratch needs it.
    # Its absence makes every case crash, which the self-test reports as a crash
    # rather than a pass - but a self-test that only ever crashes teaches nothing.
    "design/art/flat2d_proof/verify_proof.py",
]


def _build_scratch(root):
    import tempfile
    base = Path(tempfile.mkdtemp())
    for relative in SCRATCH_LAYOUT:
        source = REPO / relative
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            # __pycache__ is several megabytes of compiled bytecode the
            # validator never reads, and copying it per case filled the
            # temporary filesystem twice
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
    for relative in list(LIBRARIES.values()) + [PROOF_MANIFEST]:
        target = base / relative.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, target)
    script = base / "design/art/validate_mapping.py"
    shutil.copy2(Path(__file__), script)
    return base, script


def self_test():
    import shutil
    import tempfile
    missed, crashed, wrong = [], [], []
    for label, relative, old, new, expected in SELF_TEST_CASES:
        base, script = _build_scratch(REPO)
        try:
            target = base / relative
            text = target.read_text(encoding="utf-8")
            if old not in text:
                raise SystemExit(f"self-test pattern not found for {label!r}: "
                                 f"{old!r}")
            target.write_text(text.replace(old, new), encoding="utf-8")
            result = subprocess.run([sys.executable, str(script)],
                                    capture_output=True, text=True,
                                    env={**os.environ,
                                         "HILLCOURT_REPO": str(base)})
            if result.returncode == 0:
                missed.append(label)
            elif "Traceback" in result.stderr:
                crashed.append((label, result.stderr.strip().splitlines()[-1][:60]))
            elif expected not in result.stdout:
                first = next((line.strip(" -") for line in
                              result.stdout.splitlines()
                              if line.strip().startswith("- ")), result.stdout)
                wrong.append((label, expected, first))
            else:
                hit = next(line.strip(" -") for line in result.stdout.splitlines()
                           if expected in line)
                print(f"  caught    {label:52s} - {hit[:96]}")
        finally:
            shutil.rmtree(base, ignore_errors=True)
    if missed or crashed or wrong:
        print(f"mapping self-test: FAIL ({len(missed)} missed, {len(crashed)} "
              f"crashed, {len(wrong)} caught by the wrong check)")
        for label in missed:
            print(f"  missed: {label}")
        for label, why in crashed:
            print(f"  crashed: {label} - {why}")
        for label, expected, got in wrong:
            print(f"  wrong check: {label} expected {expected!r}, got {got!r}")
        return 1
    print(f"mapping self-test: OK ({len(SELF_TEST_CASES)} defects, every one "
          "caught with the right reason)")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())

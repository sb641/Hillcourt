"""Build the HILLCOURT flat 2D proof plates.

Pipeline per cell. The order is the whole point: quantising or stroking before
the final resize is undone by the resampler, which interpolates the ramp back
into hundreds of colours and blurs a 1px contour away.

  1. key the background, per asset type, from a sign test on the tone
  2. trim, then resize to the target plate box
  3. terrain only: scale the field to the hex footprint and apply the mask,
     both in cell coordinates
  4. retouch: quantise the RGB ramp to the declared style colours, then
     restore the alpha channel that the quantisation flattened
  5. scrub: repaint any colour too cold for the style ramp
  6. re-stroke a 1px contour, derived from a thresholded copy of the alpha so
     the stroke is one hard colour rather than a ramp of blends
  7. place: terrain at the hex centre, objects with their ground contact run
     centred exactly on x=64 and its bottom on y=64, which is the same pixel
  8. soft contact shadow for objects only
  9. measure everything and write the manifest from those measurements

The hex is drawn here, never taken from a render. The build exits non-zero if
any cell fails its own geometry check, so a broken build cannot quietly write
a manifest that claims to be fine.

Run:  python3 design/art/flat2d_proof/build_proof.py
"""

from pathlib import Path
import collections
import math
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pngmeasure import alpha_map, bbox  # noqa: E402

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources"
PLATES = ROOT / "plates"
GUIDES = ROOT / "guides"

CELL = 128
# One reference point, not two: the object stands in the middle of its tile, so
# the ground contact and the hex centre are the same pixel. An earlier revision
# put the contact on the hex bottom vertex, which made every object hang off
# the front edge of its own tile.
GROUND_Y = 64
HEX_CENTRE = (64, 64)
HEX_R = 48
HEX_W = 42

GUIDE_CELL = 192
GUIDE_SPRITE = 130
GUIDE_GUTTER = 16
GUIDE_COLUMNS = 2
GUIDE_BACKGROUND = "#071419"
GUIDE_CELL_FILL = "#10242a"
# The sprite is scaled below 100% of the cell and placed with a north gravity,
# so it is centred horizontally and pinned to the top. The offset is declared
# here and checked by the verifier at that exact position: a check that searched
# for the best offset would absorb any displacement it is meant to catch.
GUIDE_SPRITE_OFFSET_X = (GUIDE_CELL - round(CELL * GUIDE_SPRITE / 100)) // 2

REVISION = 10
CONTOUR = "#181410"

# Upper bound on distinct RGB values allowed in a finished plate. The quantise
# step declares it per cell; the verifier refuses anything above the maximum of
# those numbers, so render noise cannot come back through a resample.
MAX_UNIQUE_RGB = 32

# The contact shadow is an ellipse centred on the ground contact. Its radius
# follows the object width but is capped, so on the two widest objects the cap
# is what applies: a shadow wider than its object reads as a smudge.
CONTACT_SHADOW_RATIO = 0.55
CONTACT_SHADOW_MIN_RADIUS = 5
CONTACT_SHADOW_MAX_RADIUS = 24

# The contact run of an even width is centred between two pixel centres, so the
# closest reachable anchor is half a pixel off. This is the rasterisation limit.
ANCHOR_TOLERANCE_PX = 0.5

STYLE_COLORS = {
    "terrain_field_hex": 24,
    "terrain_hill_hex": 20,
    "terrain_pasture_hex": 20,
    "terrain_forest_hex": 16,
    "terrain_marsh_hex": 18,
    "terrain_heath_hex": 20,
    "terrain_salt_flat_hex": 12,
    "terrain_ruin_hex": 20,
    "terrain_water_hex": 18,
    "architecture_cottage_plate": 12,
    "prop_flat_cart_empty": 10,
    "actor_villager_plate": 8,
    # A drawn building mixes one wall ramp, one thatch ramp, one grey ramp and
    # the fixed detail colours (door, glazing, joints, contour). The cap is set
    # above that total so quantisation cannot merge two structural tones.
    "architecture_cottage_gable_a": 28,
    "architecture_cottage_gable_b": 28,
    "architecture_house_timber": 28,
    "architecture_shed_small": 24,
    "architecture_yard_shelter": 24,
    "architecture_supply_shed": 24,
    "architecture_palisade": 20,
    "architecture_bridge": 20,
    "architecture_gate": 20,
    # A prop mixes at most two ramps plus the fixed detail colours, so the cap
    # stays well under the 32-colour ceiling the verifier enforces.
    "good_salt_pile": 16,
    "good_peat_bricks": 20,
    "good_grain_sacks": 20,
    "good_wool_bale": 16,
    "good_log_stack": 24,
    "good_boat": 20,
    "good_boat_small": 16,
    "good_cart": 24,
    "good_iron_tools": 20,
    "good_stone": 16,
    "good_firewood": 24,
    "good_hay": 20,
    "good_straw": 20,
    "good_axe": 24,
    "good_wooden_plough": 24,
    "good_board": 24,
    "good_plank": 24,
    "good_raft": 24,
    "good_cloth": 16,
    "good_hide": 20,
    "good_meat": 28,
    "good_manure": 16,
    "good_butter_churn": 24,
    "good_iron_bloom": 24,
    "good_iron_share": 20,
    # A figure is four masses, each with its own ramp: skin, cloth, leather and
    # the fixed hair and boot tones. The cap covers all of them.
    # An animal mixes one coat ramp and one dark ramp, plus horn, beak and eye.
    "animal_ox": 20,
    "animal_horse": 20,
    "animal_donkey": 20,
    "animal_sheep": 20,
    "animal_goat": 20,
    "animal_pig": 20,
    "animal_hen": 20,
    "animal_goose": 20,
    "animal_duck": 20,
    # wild game: the same quadruped, so the same cap
    "animal_deer": 20,
    "animal_boar": 20,
    "animal_rabbit": 20,
    "animal_squirrel": 20,
    "actor_child": 20,
    "actor_adult": 20,
    "actor_elder": 20,
}

# Background keys. The renders sit on a cold blue-grey vignette, but the
# terrain render also carries a green turf rim, so the two need different tests:
#   cold vignette  -> B > R   (objects: every asset pixel is warm earth/timber)
#   turf and leaves -> R - G too small (soil is warm, R - G is clearly positive)
KEY_TESTS = {
    # A field is soil plus turf. Soil is warm (R > G, R > B); turf is green but
    # never cold (G > R, G > B, and B still below R). The cold blue vignette
    # fails both halves, so it still keys out. Dry leaves sit between the two
    # and are left to the scrub pass, which keeps a few warm flecks instead of
    # dropping them all.
    "terrain_field_hex": "((u.r > u.b && u.r > u.g) || (u.g > u.b && u.g >= u.r))"
                         " ? 1 : 0",
    "architecture_cottage_plate": "u.b > u.r ? 0 : 1",
    "prop_flat_cart_empty": "u.b > u.r ? 0 : 1",
    "actor_villager_plate": "u.b > u.r ? 0 : 1",
}

# Minimum red-blue spread a colour must have to belong to the style ramp.
# Anything flatter is scrubbed after quantisation. The fallback colour chosen
# by scrub() is drawn from the same set, so it always passes its own check.
# Minimum red-blue spread a colour must have to survive the scrub pass.
# The terrain plates are drawn from scratch, so no render background ever
# reaches them and nothing has to be scrubbed: water is legitimately colder
# than any warmth threshold, and a rule that repaints cold colours would turn
# the river into mud. The object plates are keyed out of a render and do need
# it.

# The contact point is read at the same alpha threshold the verifier uses, so
# the build and the check can never disagree about where the ground is.
CONTACT_ALPHA = 160

TERRAIN_ORDER = [
    "terrain_field_hex",
    "terrain_hill_hex",
    "terrain_pasture_hex",
    "terrain_forest_hex",
    "terrain_marsh_hex",
    "terrain_heath_hex",
    "terrain_salt_flat_hex",
    "terrain_ruin_hex",
    "terrain_water_hex",
]

# Architecture plates are drawn for the same reason the terrain is: a crop of
# the isometric sheet is not a flat 2D plate, it is a smaller isometric render
# with a plate-shaped border. Each building declares its parts - wall, roof,
# openings - and the front elevation is composed from them.
#
# The whole set is drawn in strict front elevation on the same ground line the
# terrain uses, so a building can be stood on a tile without any per-object
# correction. Proportions are the load-bearing numbers: the ridge height, the
# eave overhang and the opening size are what make a gable read as a gable.
ARCH = {
    "architecture_cottage_gable_a": {
        "name": "Cottage, gable A", "roof": "gable", "wall": "timber",
        "width": 56, "wall_h": 26, "ridge_h": 20, "overhang": 5,
        "door": (6, 13, 4), "windows": [(1, 1), (2, 1)], "chimney": None,
    },
    "architecture_cottage_gable_b": {
        "name": "Cottage, gable B", "roof": "gable", "wall": "timber",
        "width": 48, "wall_h": 22, "ridge_h": 18, "overhang": 4,
        "door": (5, 12, 4), "windows": [(2, 1)], "chimney": "left",
    },
    "architecture_house_timber": {
        "name": "Timber house", "roof": "gable", "wall": "timber",
        "width": 64, "wall_h": 30, "ridge_h": 22, "overhang": 5,
        "door": (7, 14, 4), "windows": [(1, 1), (3, 1), (2, 2)], "chimney": "right",
    },
    "architecture_shed_small": {
        "name": "Small shed", "roof": "shed", "wall": "timber",
        "width": 34, "wall_h": 18, "ridge_h": 11, "overhang": 3,
        "door": (5, 11, 4), "windows": [], "chimney": None,
    },
    "architecture_yard_shelter": {
        "name": "Yard shelter", "roof": "shed", "wall": "open",
        "width": 40, "wall_h": 17, "ridge_h": 12, "overhang": 4,
        "door": None, "windows": [], "chimney": None,
    },
    "architecture_supply_shed": {
        "name": "Supply shed", "roof": "gable", "wall": "plank",
        "width": 44, "wall_h": 20, "ridge_h": 15, "overhang": 4,
        "door": (6, 12, 4), "windows": [(2, 1)], "chimney": None,
    },
    "architecture_palisade": {
        "name": "Palisade", "roof": "none", "wall": "palisade",
        "width": 44, "wall_h": 30, "ridge_h": 0, "overhang": 0,
        "door": None, "windows": [], "chimney": None,
    },
    "architecture_bridge": {
        "name": "Bridge", "roof": "deck", "wall": "stone",
        "width": 72, "wall_h": 20, "ridge_h": 9, "overhang": 4,
        "door": None, "windows": [], "chimney": None,
    },
    "architecture_gate": {
        "name": "Gate", "roof": "flat", "wall": "gate",
        "width": 44, "wall_h": 26, "ridge_h": 0, "overhang": 0,
        "door": None, "windows": [], "chimney": None,
    },
}

ARCH_RAMP = {              # the four tones every drawn building may use
    "timber": ((58, 42, 28), (78, 58, 38), (100, 76, 50), (122, 96, 64)),
    "plank": ((64, 50, 34), (86, 68, 46), (110, 88, 60), (134, 110, 76)),
    "palisade": ((50, 36, 24), (68, 50, 32), (88, 66, 42), (108, 84, 54)),
    "stone": ((56, 54, 50), (76, 74, 68), (98, 95, 88), (120, 116, 108)),
    "open": ((58, 44, 30), (80, 62, 42), (104, 82, 56), (128, 104, 72)),
    "gate": ((52, 38, 26), (70, 52, 34), (90, 68, 44), (110, 86, 58)),
}
ARCH_ROOF = (              # thatch, the steepest tone in the set
    (52, 42, 26), (74, 60, 36), (98, 80, 48), (124, 104, 66),
)
ARCH_DARK = (24, 20, 16)
ARCH_THATCH = ARCH_ROOF
ARCH_GREY = ((38, 42, 40), (58, 63, 60), (80, 85, 82), (104, 108, 104))
# the fixed detail colours: door leaf, glazing, log/plank/stone joints, post
# seams. They are part of the material, so the verifier is told about them.
ARCH_DETAIL = (
    (18, 14, 11), (22, 17, 12), (26, 25, 23), (34, 24, 17), (16, 13, 10),
    (74, 88, 96), (20, 15, 11),
)
# animal detail: horn, beak, hoof and the eye
ANIMAL_DETAIL = (
    (28, 22, 16), (44, 36, 26), (20, 16, 12), (150, 142, 96),
)
# garment detail: seams, buckles, mail rings
GARMENT_DETAIL = ((26, 20, 15), (18, 14, 10), (44, 34, 24))
# actor detail: hair, boots and the eye line
ACTOR_DETAIL = (
    (34, 26, 20), (52, 40, 30), (24, 18, 14), (70, 56, 42),
)
# prop detail colours: seams, straps, spoke gaps, tool heads
PROP_DETAIL = (
    (20, 16, 12), (24, 20, 16), (30, 26, 21), (16, 18, 20),
)

# The watchtower is deliberately absent from the drawn set: it is
# `needs_ontology_check`, and a plate for an entity the constitution has not
# accepted would be a claim, not an asset.
ARCH_ORDER = [arch_id for arch_id in ARCH if arch_id != "architecture_watchtower"]



# Props and goods are drawn for the third reason, and it is the same reason.
# A crop of the isometric goods board is not a front elevation: the sacks on it
# are seen from above, the log ends are ellipses, and the cart is a rhombus.
#
# Each prop is a short list of parts with declared materials. The vocabulary is
# deliberately small - heap, brick course, sack, log end, hull, wheel, tool - so
# a prop is a description rather than a picture, and the material check can read
# the same `material_ramp` contract the buildings use.
HAZARD = {
    # One plate per `kind` in design/catalogs/hazards.yml. `Hazard.kind` is a
    # declared field (ontology.py:314) and the runtime reads it: the travel
    # risk is `base_risk_for(world, hazard.kind)`, and the report says
    # `{"hazard": {"kind": hazard.kind, ...}}`. That makes it a carrier, so
    # unlike `watchtower` these are allowed to be drawn.
    "hazard_wolves": {
        "catalog_id": "wolves", "name": "Wolf den", "form": "den",
        "width": 34, "height": 20, "mats": ["earth", "fur"],
    },
    "hazard_bog": {
        # `bog` is gated on `terrain: marsh`, so it stands on the marsh plate.
        # It is not a second marsh: the marsh is the ground, and this is the
        # soft patch that can be walked into. Terrain says where you are; the
        # hazard says what it will cost you there.
        "catalog_id": "bog", "name": "Bog", "form": "mire",
        "width": 46, "height": 13, "mats": ["peat", "water"],
    },
    "hazard_band": {
        # A CAMP, and deliberately not a menace. The owner is explicit: a camp
        # may be peaceful and need not be a hazard at all, but to a traveller
        # standing on this tile the two look the same. So this plate shows a camp
        # - fire, lean-to, stacked gear, smoke - and carries NO information
        # about whether it is friendly. That arrives as a `Report`, which is the
        # constitution's rule: the player is given news, not the cell's contents.
        # A later agent who finds this plate too friendly has not found a bug:
        # they have found the design and are about to break it.
        "catalog_id": "band", "name": "Camp", "form": "camp",
        "width": 40, "height": 30, "mats": ["timber", "cloth", "smoke", "ember"],
    },
}
SITE = {
# --- the marked places -----------------------------------------------------
    # `tile_form()` returns ONE form for a tile, and a mark takes precedence
    # over the terrain: a tile carrying `mooring` is drawn as `pier`, not as
    # marsh. So these are not overlays on a terrain plate - they ARE what the
    # tile looks like instead, exactly as the nine buildings and the seven camps
    # are. I had told the owner these were "low risk, the same mechanism as the
    # camps" and separately that the dwelling and road forms were a "high risk"
    # class; that split was wrong. There is one class, and its composition
    # contract was already set by the architecture plates.
    #
    # The carriers are what the code branches on: eight of them are tile marks
    # read through getattr, two are a settlement's kind, and one is a declared
    # tile field.
    "site_pier": {
        "game_form": "pier", "name": "Pier",
        "carrier_field": "Tile.mooring", "carrier_kind": "optional_attribute",
        "form": "pier", "width": 44, "height": 20,
        "mats": ["timber", "hull", "cloth", "water"],
    },
    "site_waystation": {
        "game_form": "waystation", "name": "Waystation",
        "carrier_field": "Tile.waystation", "carrier_kind": "optional_attribute",
        "form": "shed_with_stack", "width": 40, "height": 26,
        "mats": ["timber", "cloth", "smoke"],
    },
    "site_tavern": {
        "game_form": "tavern_site", "name": "Tavern site",
        "carrier_field": "Tile.tavern", "carrier_kind": "optional_attribute",
        "form": "wide_house", "width": 44, "height": 30,
        "mats": ["timber", "cloth", "thatch"],
    },
    "site_iron_mine": {
        "game_form": "iron_mine", "name": "Iron mine",
        "carrier_field": "Tile.mine", "carrier_kind": "optional_attribute",
        "form": "adit", "width": 34, "height": 24,
        "mats": ["earth", "timber", "ash"],
    },
    "site_hermitage": {
        "game_form": "hermitage", "name": "Hermitage",
        "carrier_field": "Tile.hermitage", "carrier_kind": "optional_attribute",
        "form": "lean_to", "width": 26, "height": 22,
        "mats": ["timber", "cloth"],
    },
    "site_smoke": {
        # not a building: the observable sign that somebody is there, and the
        # truth about who is what arrives as a Report, not as a picture
        "game_form": "smoke_site", "name": "Smoke",
        "carrier_field": "Tile.smoke", "carrier_kind": "optional_attribute",
        "form": "smoke_only", "width": 18, "height": 30,
        "mats": ["smoke"],
    },
    "site_baron_castle": {
        "game_form": "baron_castle", "name": "Baron's castle",
        "carrier_field": "Tile.castle", "carrier_kind": "optional_attribute",
        "form": "keep", "width": 52, "height": 44,
        "mats": ["stone", "timber", "thatch"],
    },
    "site_city_quarter": {
        "game_form": "city_quarter", "name": "City quarter",
        "carrier_field": "Tile.urban", "carrier_kind": "optional_attribute",
        "form": "row_houses", "width": 54, "height": 32,
        "mats": ["timber", "thatch", "cloth"],
    },
    "site_ruin": {
        "game_form": "ruin_site", "name": "Ruin",
        "carrier_field": "Tile.ruin_id",
        "form": "ruin", "width": 40, "height": 24,
        "mats": ["stone", "earth", "timber"],
    },
    "site_salt_works": {
        "game_form": "salt_settlement", "name": "Salt works",
        "carrier_field": "Settlement.kind",
        "form": "salt_pans", "width": 46, "height": 18,
        "mats": ["water", "timber", "salt"],
    },
    "site_tribal_village": {
        "game_form": "tribal_village", "name": "Tribal village",
        "carrier_field": "Settlement.kind",
        "form": "native_huts", "width": 44, "height": 26,
        "mats": ["timber", "cloth", "earth", "thatch"],
    },}
CAMP = {
    # --- the camps ----------------------------------------------------------
    # Seven plates, and every one of them is a form `tile_view.py` can already
    # return. `camp_form_for_pack()` (tile_view.py:620) reads `pack.kind`, the
    # owner's legal status and the number of members; `lost_caravan` is not a
    # Pack at all but a named tile field read through `getattr` (tile_view.py:764).
    #
    # I wrote in ADR 0217 that a camp had no entity and therefore no art. That
    # was wrong: I judged from the ontology and never opened tile_view.py. The
    # carrier exists, and it is exactly the surface art maps onto.
    "camp_wagon": {
        "game_form": "wagon_camp", "name": "Wagon camp",
        "carrier_field": "Pack.kind", "carrier_value": "caravan",
        "form": "wagon_camp", "width": 46, "height": 26,
        "mats": ["timber", "cloth", "smoke", "ember"],
    },
    "camp_tent": {
        "game_form": "tent_camp", "name": "Tent camp",
        "carrier_field": "Pack.kind", "carrier_value": "household_move",
        "form": "tent_camp", "width": 44, "height": 24,
        "mats": ["cloth", "timber", "ember", "smoke"],
    },
    "camp_pavilion": {
        "game_form": "pavilion_camp", "name": "Lord's pavilion",
        "carrier_field": "Household.legal_status_id", "carrier_value": "thegn",
        "form": "pavilion_camp", "width": 42, "height": 34,
        "mats": ["cloth", "timber", "ember", "smoke", "ash"],
    },
    "camp_fortified": {
        "game_form": "fortified_camp", "name": "Palisaded host camp",
        "carrier_field": "Pack.purpose", "carrier_value": "party",
        "form": "fortified_camp", "width": 52, "height": 30,
        "mats": ["timber", "cloth", "smoke", "ember"],
    },
    "camp_campfire": {
        "game_form": "campfire_camp", "name": "Campfire",
        "carrier_field": "Pack.purpose", "carrier_value": "party",
        "form": "campfire_camp", "width": 34, "height": 16,
        "mats": ["smoke", "ember", "timber"],
    },
    "camp_foot_pot": {
        "game_form": "foot_pot_camp", "name": "Watchman's pot",
        "carrier_field": "Pack.purpose", "carrier_value": "scout",
        "form": "foot_pot_camp", "width": 26, "height": 16,
        "mats": ["smoke", "ember", "timber"],
    },
    "camp_lost_caravan": {
        # not a Pack in transit: a mark the world leaves where a caravan died
        "game_form": "lost_caravan", "name": "Lost caravan",
        "carrier_field": "Tile.lost_caravan", "carrier_value": None,
        "carrier_kind": "optional_attribute",
        "form": "lost_camp", "width": 40, "height": 20,
        "mats": ["timber", "ash", "cloth"],
    },
}
HAZARD_RAMP = {
    "earth": ((46, 34, 24), (66, 50, 34), (88, 68, 46), (112, 88, 60)),
    "fur": ((52, 46, 42), (74, 66, 60), (98, 88, 80), (124, 112, 100)),
    "peat": ((38, 30, 24), (54, 44, 34), (72, 60, 46), (92, 78, 60)),
    "water": ((34, 44, 48), (48, 62, 66), (64, 82, 84), (84, 104, 104)),
    "timber": ((44, 32, 22), (66, 50, 34), (90, 70, 46), (116, 92, 62)),
    "cloth": ((72, 62, 50), (94, 82, 66), (118, 104, 84), (142, 126, 104)),
    "smoke": ((92, 90, 86), (120, 118, 112), (150, 148, 142), (178, 176, 170)),
    # the only live light in the family: firelight, warm and low
    "ember": ((150, 62, 30), (186, 96, 40), (214, 136, 62), (236, 176, 96)),
}
HAZARD_DETAIL = ((22, 18, 14), (16, 13, 10), (34, 28, 22))
CAMP_RAMP = {
    "timber": ((44, 32, 22), (66, 50, 34), (90, 70, 46), (116, 92, 62)),
    "cloth": ((66, 58, 48), (88, 78, 64), (112, 100, 82), (138, 124, 104)),
    "smoke": ((92, 90, 86), (120, 118, 112), (150, 148, 142), (178, 176, 170)),
    "ember": ((150, 62, 30), (186, 96, 40), (214, 136, 62), (236, 176, 96)),
    "ash": ((56, 52, 48), (78, 74, 68), (102, 98, 92), (126, 122, 116)),
}
CAMP_DETAIL = ((22, 18, 14), (16, 13, 10), (34, 28, 22))
CAMP_RAMP["hull"] = ((50, 36, 24), (70, 52, 34), (92, 68, 46),
                      (114, 88, 58))
CAMP_RAMP["earth"] = ((46, 34, 24), (66, 50, 34), (88, 68, 46),
                      (112, 88, 60))
CAMP_RAMP["stone"] = ((64, 62, 58), (88, 86, 80), (114, 112, 106),
                      (142, 140, 132))
CAMP_RAMP["thatch"] = ((84, 66, 32), (108, 86, 42), (134, 110, 56),
                       (160, 134, 72))
CAMP_RAMP["water"] = ((34, 44, 48), (48, 62, 66), (64, 82, 84), (84, 104, 104))
CAMP_RAMP["salt"] = ((146, 144, 136), (172, 170, 162), (198, 196, 188),
                     (220, 218, 210))
SITE_ORDER = list(SITE)
CAMP_ORDER = list(CAMP)
CAMP_STYLE = {c: 18 for c in CAMP_ORDER}
SITE_STYLE = {s: 22 for s in SITE_ORDER}

PROP = {
    "good_salt_pile": {
        "name": "Salt pile", "form": "heap",
        "width": 40, "height": 22, "mats": ["salt"],
    },
    "good_peat_bricks": {
        "name": "Peat bricks", "form": "brick_stack",
        "brick_w": 11, "brick_h": 6, "courses": 3, "per_course": 3,
        "mats": ["peat"],
    },
    "good_grain_sacks": {
        "name": "Grain sacks", "form": "sacks",
        "sack_w": 19, "sack_h": 24, "count": 2, "mats": ["burlap"],
    },
    "good_wool_bale": {
        "name": "Wool bale", "form": "bale",
        "width": 34, "height": 22, "straps": 2, "mats": ["wool"],
    },
    "good_log_stack": {
        "name": "Log stack", "form": "log_stack",
        "log_len": 38, "log_h": 11, "rows": 3, "logs_per_row": 1,
        "mats": ["bark", "endgrain"],
    },
    "good_boat": {
        "name": "Boat with net", "form": "hull",
        "width": 48, "hull_h": 16, "mast_h": 20, "mats": ["hull"],
    },
    "good_boat_small": {
        "name": "Small boat", "form": "hull",
        "width": 30, "hull_h": 11, "mast_h": 0, "mats": ["hull"],
    },
    "good_cart": {
        "name": "Loaded cart", "form": "cart",
        "bed_w": 40, "bed_h": 12, "wheel_d": 12,
        "mats": ["bark", "iron", "burlap"],
    },
    "good_iron_tools": {
        "name": "Iron tools", "form": "tool_board",
        "board_w": 26, "board_h": 30, "tools": 3, "mats": ["iron", "bark"],
    },
    "good_stone": {
        "name": "Field stone", "form": "rubble",
        "width": 40, "height": 18, "stones": 4, "mats": ["stone"],
    },
    "good_firewood": {
        "name": "Split firewood", "form": "billets",
        "width": 32, "height": 20, "rows": 4, "mats": ["bark", "endgrain"],
    },
    "good_hay": {
        "name": "Hayrick", "form": "hayrick",
        "width": 36, "height": 30, "mats": ["hay"],
    },
    "good_straw": {
        "name": "Straw sheaf", "form": "sheaf",
        "width": 22, "height": 24, "mats": ["hay"],
    },
    "good_axe": {
        "name": "Axe", "form": "axe",
        "haft": 26, "head_w": 17, "mats": ["bark", "iron"],
    },
    "good_wooden_plough": {
        "name": "Wooden plough", "form": "plough",
        "beam": 36, "mats": ["bark", "iron"],
    },
    "good_board": {
        "name": "Sawn boards", "form": "boards",
        "board_w": 32, "board_h": 5, "count": 5, "mats": ["endgrain", "bark"],
    },
    "good_plank": {
        "name": "Planks", "form": "boards",
        "board_w": 28, "board_h": 4, "count": 4, "mats": ["endgrain", "bark"],
    },
    "good_raft": {
        "name": "Lashed raft", "form": "raft",
        "width": 40, "height": 12, "poles": 6, "mats": ["bark", "hull"],
    },
    "good_cloth": {
        "name": "Cloth bolt", "form": "bolt",
        "width": 28, "height": 20, "folds": 3, "mats": ["wool"],
    },
    "good_hide": {
        "name": "Cured hide", "form": "hide",
        "width": 32, "height": 26, "mats": ["hide"],
    },
    "good_meat": {
        "name": "Butchered meat", "form": "carcass",
        "width": 34, "height": 20, "mats": ["meat", "bone"],
    },
    "good_manure": {
        "name": "Manure heap", "form": "heap",
        "width": 28, "height": 13, "mats": ["manure"],
    },
    "good_butter_churn": {
        "name": "Butter churn", "form": "churn",
        "width": 18, "height": 26, "mats": ["bark", "endgrain"],
    },
    "good_iron_bloom": {
        "name": "Iron bloom", "form": "bloom",
        "width": 30, "height": 14, "mats": ["iron", "ash"],
    },
    "good_iron_share": {
        "name": "Iron ploughshare", "form": "share",
        "length": 30, "mats": ["iron"],
    },
}

PROP_RAMP = {
    "salt": ((146, 144, 136), (172, 170, 162), (198, 196, 188), (220, 218, 210)),
    "peat": ((26, 20, 15), (38, 30, 22), (52, 42, 32), (68, 56, 44)),
    "burlap": ((112, 94, 62), (136, 116, 80), (160, 138, 98), (182, 160, 118)),
    "wool": ((172, 168, 156), (194, 190, 178), (214, 210, 198), (232, 228, 216)),
    "bark": ((54, 40, 26), (74, 56, 36), (96, 74, 48), (118, 94, 62)),
    "endgrain": ((128, 100, 66), (152, 122, 82), (176, 144, 100), (198, 166, 120)),
    "hull": ((50, 36, 24), (70, 52, 34), (92, 68, 46), (114, 88, 58)),
    "iron": ((36, 40, 44), (56, 61, 66), (78, 83, 88), (102, 106, 110)),
    "stone": ((72, 70, 66), (96, 94, 90), (122, 120, 114), (148, 146, 138)),
    "hay": ((120, 96, 40), (148, 122, 56), (176, 150, 78), (200, 176, 104)),
    "hide": ((74, 54, 38), (100, 76, 54), (126, 98, 72), (152, 122, 92)),
    "meat": ((92, 44, 44), (122, 62, 58), (152, 84, 76), (180, 112, 100)),
    "bone": ((146, 138, 122), (172, 166, 150), (198, 194, 180), (218, 216, 206)),
    "manure": ((34, 26, 18), (50, 40, 26), (68, 56, 36), (86, 72, 48)),
    "ash": ((56, 52, 48), (78, 74, 68), (102, 98, 92), (126, 122, 116)),
}

PROP_ORDER = [prop_id for prop_id in PROP]

# An actor is drawn for the same reason as a building and a prop, with one extra
# difficulty: a figure is not one solid. Head, torso, arms and legs are separate
# masses, and shading the whole silhouette as a single form turns a person into a
# blob. Every part is therefore shaded against its own bounds.
#
# Only three actor plates are drawn, and the reason is the ontology, not taste.
# `Person.age_class` is documented in docs/03_ontology.md as exactly
# {child, adult, elder} and is read by demography, needs, decisions, manor, seat
# and thegn. That is a carrier: the runtime can select a plate from it. There is
# no `pose`, `stance`, `occupation` or carried-item field anywhere, so the four
# pose references in asset_library_v1 have no carrier and stay references. A
# fifth plate for a fifth pose would be art for a field that does not exist.
ACTOR = {
    # A figure has to be tall enough to be a person. At 26/40/36 the adult's
    # silhouette was 29x43 inside a 128px cell: a third of the tile, with a
    # seven-pixel head. No amount of extra garment detail could survive that,
    # because a nasal helm, a brooch and a neck ring are all 3-6px objects. The
    # figures are now 40/58/52, which is 45% of the tile and gives the head ten
    # pixels across. The ceiling is the anchor: the feet stand on GROUND_Y at
    # y=64, so nothing can be taller than the 64 rows above it.
    "actor_child": {
        "name": "Child", "age_class": "child",
        "height": 40, "head_frac": 0.31, "shoulder_frac": 0.34,
        "hip_frac": 0.32, "leg_frac": 0.13, "arm_frac": 0.11,
        "stoop": 0.0, "staff": False, "mats": ["skin", "cloth", "leather"],
    },
    "actor_adult": {
        "name": "Adult", "age_class": "adult",
        "height": 58, "head_frac": 0.19, "shoulder_frac": 0.36,
        "hip_frac": 0.30, "leg_frac": 0.12, "arm_frac": 0.10,
        "stoop": 0.0, "staff": False, "mats": ["skin", "cloth", "leather"],
    },
    "actor_elder": {
        "name": "Elder", "age_class": "elder",
        "height": 52, "head_frac": 0.20, "shoulder_frac": 0.33,
        "hip_frac": 0.29, "leg_frac": 0.11, "arm_frac": 0.10,
        # an elder stands stooped: the head sits forward of the spine, which is
        # the one silhouette cue that separates the third figure from the first
        "stoop": 4.0, "staff": True,
        "mats": ["skin", "cloth", "leather"],
    },
}

ACTOR_RAMP = {
    "skin": ((126, 92, 68), (152, 114, 84), (176, 136, 102), (198, 158, 122)),
    "cloth": ((58, 50, 44), (82, 72, 62), (108, 96, 82), (134, 120, 102)),
    "leather": ((44, 32, 22), (64, 48, 32), (86, 66, 44), (110, 86, 58)),
}

ACTOR_ORDER = [actor_id for actor_id in ACTOR]

# A plate has a data carrier, not a sprite selector, and the difference is the
# whole point of this table.
#
#   data_carrier  - the simulation field that identifies the thing the plate
#                   depicts. This is checkable against the code, so it is checked.
#   sprite_selector - the field that picks WHICH plate to draw. There is none,
#                   and there cannot be one yet: `grep` finds no sprite, icon,
#                   atlas or plate lookup anywhere in sim/, and `client/` is
#                   frozen. `tile_view.py` says so itself - it is "a snapshot
#                   for an icon, not a renderer", and the icon is drawn later.
#
# Writing "the runtime selects this plate" would be false. What is true is that
# the runtime can read the field and a renderer, once unfrozen, would key on it.
#
# Two carrier kinds, because they are checked differently:
#   declared_field     - a real dataclass field on the class in ontology.py
#   optional_attribute - read through getattr(), so it exists in the data but is
#                        not declared on the dataclass
DATA_CARRIER = {
    "architecture_bridge": ("Tile.bridge", "declared_field"),
    "architecture_cottage_gable_a": ("Tile.dwelling", "optional_attribute"),
    "architecture_cottage_gable_b": ("Tile.dwelling", "optional_attribute"),
    "architecture_house_timber": ("Tile.dwelling", "optional_attribute"),
    "architecture_shed_small": ("Tile.dwelling", "optional_attribute"),
    "architecture_yard_shelter": ("Tile.dwelling", "optional_attribute"),
    "architecture_supply_shed": ("Tile.dwelling", "optional_attribute"),
    # A palisade and a gate are parts of the thegn's and the root's estate, but
    # no field records that a palisade or a gate exists on a tile. The seat forms
    # in tile_view describe the estate, not a wall with a gap in it.
    "architecture_palisade": (None, None),
    "architecture_gate": (None, None),
    # The three keyed plates exist to keep the key/trim/resize/anchor path under
    # test. They depict a generic cottage, an empty cart and a generic villager,
    # and no field anywhere distinguishes those three from their drawn
    # counterparts.
    "architecture_cottage_plate": (None, None),
    "prop_flat_cart_empty": (None, None),
    "actor_villager_plate": (None, None),
}

# terrain, goods and figures carry a declared field, so they are filled in here
# rather than listed one by one above.
DATA_CARRIER.update({terrain_id: ("Tile.terrain", "declared_field")
                     for terrain_id in TERRAIN_ORDER})
DATA_CARRIER.update({prop_id: ("Good.id", "declared_field")
                     for prop_id in PROP_ORDER})
DATA_CARRIER.update({actor_id: ("Person.age_class", "declared_field")
                     for actor_id in ACTOR_ORDER})
# an animal is backed by a catalog good, but a species serves several goods
# (ox_m, ox_f, ox_calf), so the carrier names the field and the cell lists them

# Livestock. Nine species, and the sex/age keys in needs.yml are the same animal
# at a different size rather than a different thing: `ox_m`, `ox_f` and `ox_calf`
# are all an ox. Each plate therefore declares `serves_goods`, and the validator
# requires every livestock key in the catalog to be served by exactly one plate -
# otherwise the runtime is left choosing between `horse` and `horse_foal`.
#
# Quadrupeds are drawn as an orthographic SIDE elevation, not a front elevation.
# A four-legged animal has no readable front view: you see a head, and the body
# that distinguishes an ox from a goat disappears. Side is the honest
# orthographic projection here, the same argument as the log stack in ADR 0187.
ANIMAL = {
    "animal_ox": {
        "name": "Ox", "form": "quadruped", "serves_goods": ["ox_m", "ox_f", "ox_calf"],
        "body_len": 44, "body_h": 15, "leg_len": 11, "neck_len": 9,
        "head_len": 10, "head_h": 7, "tail": "thin", "ears": "small",
        "horns": "wide", "coat": "hump", "mats": ["hide", "hide_dark"],
    },
    "animal_horse": {
        "name": "Horse", "form": "quadruped", "serves_goods": ["horse_m", "horse_f", "horse_foal"],
        "body_len": 42, "body_h": 14, "leg_len": 14, "neck_len": 15,
        "head_len": 12, "head_h": 6, "tail": "full", "ears": "small",
        "horns": "none", "coat": "mane", "mats": ["hide", "hide_dark"],
    },
    "animal_donkey": {
        "name": "Donkey", "form": "quadruped", "serves_goods": ["donkey_m", "donkey_f", "donkey_foal"],
        "body_len": 34, "body_h": 12, "leg_len": 12, "neck_len": 9,
        "head_len": 9, "head_h": 6, "tail": "tuft", "ears": "long",
        "horns": "none", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
    "animal_sheep": {
        "name": "Sheep", "form": "quadruped", "serves_goods": ["sheep"],
        "body_len": 26, "body_h": 12, "leg_len": 5, "neck_len": 4,
        "head_len": 6, "head_h": 4, "tail": "tuft", "ears": "small",
        "horns": "none", "coat": "fleece", "mats": ["wool", "wool_dark"],
    },
    "animal_goat": {
        "name": "Goat", "form": "quadruped", "serves_goods": ["goat"],
        "body_len": 23, "body_h": 10, "leg_len": 7, "neck_len": 6,
        "head_len": 6, "head_h": 4, "tail": "short_up", "ears": "pointy",
        "horns": "back", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
    "animal_pig": {
        "name": "Pig", "form": "quadruped", "serves_goods": ["pig"],
        "body_len": 26, "body_h": 11, "leg_len": 3, "neck_len": 2,
        "head_len": 7, "head_h": 5, "tail": "curl", "ears": "pointy",
        "horns": "none", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
    "animal_hen": {
        "name": "Hen", "form": "bird", "serves_goods": ["hen"],
        "body_len": 14, "body_h": 11, "neck_len": 3, "beak_len": 4,
        "leg_len": 4, "tail": "fan", "crest": "comb", "mats": ["feather", "feather_dark"],
    },
    "animal_goose": {
        "name": "Goose", "form": "bird", "serves_goods": ["goose"],
        "body_len": 22, "body_h": 13, "neck_len": 10, "beak_len": 5,
        "leg_len": 4, "tail": "pointed", "crest": "none", "mats": ["feather", "feather_dark"],
    },
    "animal_duck": {
        "name": "Duck", "form": "bird", "serves_goods": ["duck"],
        "body_len": 17, "body_h": 10, "neck_len": 4, "beak_len": 6,
        "leg_len": 2, "tail": "pointed", "crest": "none", "mats": ["feather", "feather_dark"],
    },
    # --- wild game ------------------------------------------------------------
    # The same `quadruped` draw with different proportions, and three feature
    # values it did not have before: `antlers`, `tusks` and `bush`. Wild game is
    # not a new kind of thing to draw, it is the same animal with a different
    # head and a different tail - and that is why these four cost a table entry
    # each rather than a new form.
    #
    # `deer`, `boar`, `rabbit` and `squirrel` are all produced by `take_game_*`
    # recipes, so they are the hunter's catch lying on the ground: the owner's
    # rule "what physically lies" reaches them, and they belong in the animal
    # family because an animal plate already declares `serves_goods`.
    "animal_deer": {
        "name": "Deer", "form": "quadruped", "serves_goods": ["deer"],
        "body_len": 36, "body_h": 13, "leg_len": 13, "neck_len": 11,
        "head_len": 9, "head_h": 5, "tail": "short_up", "ears": "pointy",
        "horns": "antlers", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
    "animal_boar": {
        "name": "Wild boar", "form": "quadruped", "serves_goods": ["boar"],
        "body_len": 34, "body_h": 15, "leg_len": 7, "neck_len": 3,
        "head_len": 12, "head_h": 8, "tail": "tuft", "ears": "small",
        "horns": "tusks", "coat": "bristle", "mats": ["hide", "hide_dark"],
    },
    "animal_rabbit": {
        "name": "Hare", "form": "quadruped", "serves_goods": ["rabbit"],
        "body_len": 17, "body_h": 9, "leg_len": 4, "neck_len": 2,
        "head_len": 6, "head_h": 5, "tail": "puff", "ears": "very_long",
        "horns": "none", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
    "animal_squirrel": {
        "name": "Squirrel", "form": "quadruped", "serves_goods": ["squirrel"],
        "body_len": 11, "body_h": 6, "leg_len": 3, "neck_len": 1,
        "head_len": 5, "head_h": 5, "tail": "bush", "ears": "pointy",
        "horns": "none", "coat": "hide", "mats": ["hide", "hide_dark"],
    },
}

ANIMAL_RAMP = {
    "hide": ((58, 42, 28), (80, 60, 40), (104, 80, 54), (128, 102, 70)),
    "hide_dark": ((36, 26, 18), (48, 36, 25), (62, 48, 33), (78, 62, 44)),
    "wool": ((150, 144, 132), (176, 170, 158), (200, 196, 184), (222, 218, 206)),
    "wool_dark": ((104, 98, 88), (122, 116, 106), (142, 136, 126), (162, 156, 146)),
    "feather": ((92, 74, 52), (114, 94, 66), (138, 116, 82), (162, 140, 104)),
    "feather_dark": ((58, 46, 32), (72, 58, 40), (88, 72, 50), (106, 88, 62)),
    # horn, beak and hoof are one bone material at three thicknesses. It is a
    # ramp rather than a single colour because capsule() shades whatever it is
    # given, and handing it one colour makes it return a number.
    "bone": ((112, 106, 88), (134, 128, 106), (156, 150, 126), (178, 172, 148)),
}

ANIMAL_ORDER = list(ANIMAL)

# an animal is backed by a catalog good, but a species serves several
# goods (ox_m, ox_f, ox_calf), so the carrier names the field and the
# cell lists what it serves
DATA_CARRIER.update({animal_id: ("Good.id", "declared_field")
                     for animal_id in ANIMAL_ORDER})


# Clothing is seven independent layers, not twenty-four finished figures. The
# body is the `age_class` plate; every layer below is drawn on the SAME body
# landmarks, so a garment is an overlay rather than a second figure.
#
# The wealth axis is DYE, not embroidery. Undyed homespun is what poor cloth
# actually looked like, and it is the single strongest status signal of the
# period - stronger than any pattern. Wealthy cloth is dyed: weld yellow, madder
# red, woad blue; the very rich get kermes and silk. So a peasant and a thegn
# differ in colour before they differ in anything else, which is also the one
# thing that survives at 128 px.
#
# Carrier: `Household.legal_status_id` for armour, head and rank badge; wealth
# for legs, footwear and outer. Both are read by the simulation. No field was
# added. `sprite_selector` stays none - see ADR 0180 - and ADR 0187 records that
# this ADR fixes the design, not the delivery.
#
# Three armour steps, not eight. All eight legal statuses map onto the three
# explicitly below, so none is left unaccounted for.
GARMENT = {
    # --- layer 1: the tunic. everybody wears one.
    "garment_tunic_homespun": {
        "layer": "tunic", "step": 0, "form": "tunic_body",
        "name": "Tunic, undyed homespun", "mats": ["undyed"],
    },
    "garment_tunic_dyed": {
        "layer": "tunic", "step": 1, "form": "tunic_body",
        "name": "Tunic, dyed wool", "mats": ["dyed"],
    },
    "garment_tunic_silk": {
        "layer": "tunic", "step": 2, "form": "tunic_body",
        "name": "Tunic, silk", "mats": ["silk"],
    },
    # --- layer 2: the legs. bare, wrapped, or woollen hose.
    "garment_legs_bare": {
        "layer": "legs", "step": 0, "form": "legs_none",
        "name": "Bare legs", "mats": [],
    },
    "garment_legs_wrapped": {
        "layer": "legs", "step": 1, "form": "legs_wraps",
        "name": "Cloth leg wrappings", "mats": ["undyed"],
    },
    "garment_hose_wool": {
        "layer": "legs", "step": 2, "form": "legs_hose",
        "name": "Woollen hose", "mats": ["undyed"],
    },
    # --- layer 3: footwear. wealth, not rank.
    "garment_feet_bare": {
        "layer": "feet", "step": 0, "form": "feet_none",
        "name": "Barefoot", "mats": [],
    },
    "garment_feet_hide": {
        "layer": "feet", "step": 1, "form": "feet_low",
        "name": "Crude hide shoes", "mats": ["leather"],
    },
    "garment_feet_boot": {
        "layer": "feet", "step": 2, "form": "feet_high",
        "name": "Good leather boots", "mats": ["leather"],
    },
    # --- layer 4: the outer layer. wealth again.
    "garment_outer_none": {
        "layer": "outer", "step": 0, "form": "outer_none",
        "name": "No outer layer", "mats": [],
    },
    "garment_outer_apron": {
        "layer": "outer", "step": 0, "form": "outer_apron",
        "name": "Work apron", "mats": ["leather"],
    },
    "garment_outer_cloak": {
        "layer": "outer", "step": 1, "form": "outer_cloak",
        "name": "Wool cloak", "mats": ["dyed", "silver"],
    },
    "garment_outer_fur": {
        "layer": "outer", "step": 2, "form": "outer_cloak",
        "name": "Fur-lined cloak", "mats": ["fur", "silver"],
    },
    # --- layer 5: armour. rank, and leather before mail.
    "garment_armour_none": {
        "layer": "armour", "step": 0, "form": "armour_none",
        "name": "No armour", "mats": [],
    },
    "garment_armour_leather": {
        "layer": "armour", "step": 1, "form": "armour_byrnie",
        "name": "Leather byrnie", "mats": ["leather"],
    },
    "garment_armour_mail": {
        "layer": "armour", "step": 2, "form": "armour_byrnie",
        "name": "Mail byrnie", "mats": ["mail"],
    },
    # --- layer 6: the head. A veiled head is a married woman; a helm is rank.
    "garment_head_bare": {
        "layer": "head", "step": 0, "form": "head_none",
        "name": "Head bare", "mats": [],
    },
    "garment_head_coif": {
        "layer": "head", "step": 0, "form": "head_cover",
        "name": "Linen coif or straw hat", "mats": ["undyed"],
    },
    "garment_head_helm": {
        "layer": "head", "step": 2, "form": "head_helm",
        "name": "Nasal helm", "mats": ["mail"],
    },
    # --- layer 7: the badge of rank. Iron, silver, or a neck ring.
    "garment_rank_none": {
        "layer": "rank", "step": 0, "form": "rank_none",
        "name": "No badge", "mats": [],
    },
    "garment_rank_iron": {
        "layer": "rank", "step": 1, "form": "rank_pin",
        "name": "Iron brooch", "mats": ["iron"],
    },
    "garment_rank_silver": {
        "layer": "rank", "step": 2, "form": "rank_torc",
        "name": "Silver brooch and neck ring", "mats": ["silver"],
    },
}

GARMENT_RAMP = {
    "undyed": ((104, 98, 86), (126, 120, 106), (148, 142, 126), (170, 164, 146)),
    # woad, not magenta. The old light end was (144,122,156), which is a
    # purple-pink and read as synthetic: the one thing homespun-cloth peasantry
    # would never have worn.
    "dyed": ((48, 60, 88), (66, 82, 114), (88, 108, 144), (114, 136, 172)),
    # madder/kermes, a deep brick red rather than the salmon it was
    "silk": ((104, 34, 36), (136, 50, 48), (168, 68, 62), (198, 94, 84)),
    "fur": ((92, 74, 56), (114, 94, 72), (138, 116, 90), (162, 138, 110)),
    "leather": ((44, 32, 22), (64, 48, 32), (86, 66, 44), (110, 86, 58)),
    "mail": ((58, 60, 64), (80, 83, 88), (104, 108, 112), (130, 134, 138)),
    "iron": ((40, 44, 48), (58, 63, 68), (80, 85, 90), (104, 108, 112)),
    "silver": ((116, 118, 122), (146, 148, 152), (176, 178, 182), (206, 208, 212)),
}

# Hem length is a third wealth cue, beside colour and cloth. A work tunic stops
# above the knee so it can be worked in; a silk tunic falls to the ankle. The
# multiplier scales `tunic_len`, which the body shares, so a long hem is a
# longer covering rather than a stretched one.
GARMENT_LENGTH = {
    "undyed": 0.80,   # homespun: short, above the knee
    "dyed": 0.94,     # dyed wool: to the knee
    "silk": 1.18,     # silk: to the ankle
    "fur": 1.00,
    "leather": 0.90,
    "mail": 0.86,     # a byrnie is short by construction
    "iron": 1.00,
    "silver": 1.00,
}

GARMENT_DETAIL = ((26, 20, 15), (18, 14, 10), (44, 34, 24))

# A "wear nothing" step is the absence of a garment, not an asset, and a fully
# transparent plate is not something the verifier will sign off on. Those steps
# are therefore declared rather than drawn: GARMENT_EMPTY says which layer
# carries no plate and why.
GARMENT_EMPTY_FORMS = {"legs_none", "feet_none", "outer_none", "armour_none",
                       "head_none", "rank_none"}
# A layer is drawn once per body it can be worn on, not once per garment.
# `GARMENT` above is the catalogue of garments; this is the catalogue of
# *plates*, and a plate is a garment on a specific body. Sixteen adult plates
# meant a child of a thegn was drawn in a thegn's silk tunic, at the thegn's
# hem length, on a body two thirds of the height - a garment that fitted nobody.
# `Person.age_class` is a real carrier, so the body is part of the key and the
# manifest says which body each plate overlays.
GARMENT_AGE_CLASSES = ("child", "adult", "elder")


def garment_plate_id(base, age_class):
    return f"{base}_{age_class}"


def garment_base_of(plate_id):
    return plate_id.rsplit("_", 1)[0]


def garment_age_of(plate_id):
    return plate_id.rsplit("_", 1)[1]


# base garment id -> plate id, one per body
GARMENT_PLATE_OF = {
    garment_plate_id(base, age): base
    for base in GARMENT for age in GARMENT_AGE_CLASSES
}
GARMENT_ORDER = [garment_plate_id(base, age)
                 for base in GARMENT
                 for age in GARMENT_AGE_CLASSES
                 if GARMENT[base]["form"] not in GARMENT_EMPTY_FORMS]
GARMENT_EMPTY = sorted(garment_plate_id(base, age)
                       for base in GARMENT for age in GARMENT_AGE_CLASSES
                       if GARMENT[base]["form"] in GARMENT_EMPTY_FORMS)
GARMENT_OF_PLATE = {plate: GARMENT[base] for plate, base in GARMENT_PLATE_OF.items()}
GARMENT_AGE_OF_PLATE = {plate: garment_age_of(plate) for plate in GARMENT_PLATE_OF}
GARMENT_BODY_OF_PLATE = {plate: f"actor_{garment_age_of(plate)}"
                         for plate in GARMENT_PLATE_OF}
GARMENT_STYLE = {g: 16 for g in GARMENT_ORDER}
HAZARD_ORDER = list(HAZARD)
HAZARD_STYLE = {h: 17 for h in HAZARD_ORDER}

# A clothing layer is keyed by the social status it answers to, not by a
# material. `Household.legal_status_id` is read by the simulation; the wealth
# steps (legs, footwear, outer) are the same field read as an ordering.
DATA_CARRIER.update({g: ("Household.legal_status_id", "declared_field")
                     for g in GARMENT_ORDER})
# `Hazard.kind` is read at runtime - `base_risk_for(world, hazard.kind)` and
# the report's `{"hazard": {"kind": ...}}` - so it carries a plate the same way
# `legal_status_id` carries a garment.
DATA_CARRIER.update({h: ("Hazard.kind", "declared_field") for h in HAZARD})
# A camp is a derived view form, so its carrier is the field the code actually
# branches on. `lost_caravan` is not a Pack at all but a named tile field read
# through getattr - the second carrier kind the verifier knows.
DATA_CARRIER.update({c: (CAMP[c]["carrier_field"],
                         CAMP[c].get("carrier_kind", "declared_field"))
                     for c in CAMP_ORDER})
DATA_CARRIER.update({s: (SITE[s]["carrier_field"],
                         SITE[s].get("carrier_kind", "declared_field"))
                     for s in SITE_ORDER})
# The order the layers are put on, outside in. It is published because the
# order decides what covers what, and "covered by what" is a rule, not a detail
# of the drawing.
LAYER_ORDER = ("tunic", "legs", "feet", "outer", "armour", "head", "rank")

# All eight legal statuses are placed on the three armour steps explicitly, so
# none is left to guesswork. `validator` and the ADR both rely on this.
STATUS_ARMOUR_STEP = {
    "free_landless": 0,
    "cotter": 0,
    "villein": 0,
    "geneat": 1,
    "sokeman": 1,
    "thegn": 2,
    "holder": 2,
    "slave": 0,
}

STYLE_COLORS.update(GARMENT_STYLE)
STYLE_COLORS.update(HAZARD_STYLE)
STYLE_COLORS.update(CAMP_STYLE)
STYLE_COLORS.update(SITE_STYLE)

WARMTH = {
    **{terrain_id: -(255) for terrain_id in TERRAIN_ORDER},
    "architecture_cottage_plate": 8,
    "prop_flat_cart_empty": 8,
    "actor_villager_plate": 8,
    **{arch_id: -(255) for arch_id in ARCH_ORDER},
    **{prop_id: -(255) for prop_id in PROP_ORDER},
    **{actor_id: -(255) for actor_id in ACTOR_ORDER},
    **{animal_id: -(255) for animal_id in ANIMAL_ORDER},
    **{g: -(255) for g in GARMENT_ORDER},
    **{h: -(255) for h in HAZARD_ORDER},
    **{c: -(255) for c in CAMP_ORDER},
    **{s: -(255) for s in SITE_ORDER},
}

OBJECT_SPECS = [
    ("architecture_cottage_plate", "architecture_cottage_plate", 60, 60),
    ("prop_flat_cart_empty", "prop_flat_cart_empty", 72, 40),
    ("actor_villager_plate", "actor_villager_plate", 20, 40),
]

SPECS = (
    [(terrain_id, terrain_id, 112, 112, "hex") for terrain_id in TERRAIN_ORDER]
    + [(stem, stem, w, h, "object") for stem, _s, w, h in OBJECT_SPECS]
    # a drawn building or prop is already at its final size and already on the
    # anchor, so the box numbers are the measured ones, not a target to resize to
    + [(arch_id, arch_id, 0, 0, "arch") for arch_id in ARCH_ORDER]
    + [(prop_id, prop_id, 0, 0, "prop") for prop_id in PROP_ORDER]
    + [(actor_id, actor_id, 0, 0, "actor") for actor_id in ACTOR_ORDER]
    + [(animal_id, animal_id, 0, 0, "animal") for animal_id in ANIMAL_ORDER]
    + [(g, g, 0, 0, "garment") for g in GARMENT_ORDER]
    + [(h, h, 0, 0, "hazard") for h in HAZARD_ORDER]
    + [(c, c, 0, 0, "camp") for c in CAMP_ORDER]
    + [(s, s, 0, 0, "site") for s in SITE_ORDER]
)

# The atlas and the guide sheet are laid out from the number of plates, not
# from a hard-coded 2x2: the cell count changed when the terrains went from one
# to nine, and a layout that does not follow it silently drops plates.
ATLAS_STRIDE = 160          # 128 px cell + 32 px gutter
GUIDE_STRIDE = 208          # 192 px cell + 16 px gutter


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def strip_clock(path):
    """Drop the timestamps ImageMagick writes into every PNG it saves.

    Without this the build is only pixel-reproducible: two runs of the same
    source differ in their bytes, so a diff between two builds shows a change
    in every file and a real change hides in the noise. The pixels are not
    touched; only the `tIME` and `date:*` chunks are dropped.
    """
    run("convert", str(path), "-define", "png:exclude-chunk=date,time",
        "-strip", f"PNG32:{path}")


def key(source, target, stem):
    """Build a clean alpha from the source using the per-cell key test."""
    mask = target.with_suffix(".key.png")
    run("convert", source, "-alpha", "off", "-fx", KEY_TESTS[stem], str(mask))
    run("convert", source, str(mask), "-alpha", "off",
        "-compose", "CopyOpacity", "-composite", str(target))
    mask.unlink()


def retouch(target, colors):
    """Quantise the RGB ramp, then restore the alpha channel.

    Order matters: this runs after the final resize. Quantising before the
    downscale lets the resampler interpolate the ramp back into hundreds of
    colours, which is what left render noise inside the previous plates.
    """
    alpha = target.with_suffix(".a.png")
    run("convert", str(target), "-alpha", "extract", str(alpha))
    run("convert", str(target), "-channel", "RGB", "-colors", str(colors),
        "-dither", "None", f"PNG32:{target.with_suffix('.rgb.png')}")
    run("convert", str(target.with_suffix(".rgb.png")), str(alpha),
        "-compose", "CopyOpacity", "-composite", f"PNG32:{target}")
    target.with_suffix(".rgb.png").unlink()
    alpha.unlink()


def stroke_contour(target):
    """Re-stroke the 1px silhouette contour.

    This runs after scrub, not before: the contour colour is deliberately
    near-neutral, so a correctly implemented scrub would otherwise classify it
    as cold paint and repaint it away.
    """
    alpha = target.with_suffix(".a.png")
    run("convert", str(target), "-alpha", "extract", str(alpha))
    # The contour is derived from a thresholded copy of the alpha: the plate
    # edge stays antialiased, but the stroke itself is a hard 1px line, so it
    # adds exactly one colour instead of a ramp of blends.
    hard = target.with_suffix(".h.png")
    run("convert", str(alpha), "-threshold", "50%", str(hard))
    inner = target.with_suffix(".i.png")
    edge = target.with_suffix(".e.png")
    w, h = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(target)], text=True).split())
    run("convert", str(hard), "-morphology", "Erode", "Octagon:1", str(inner))
    run("convert", str(hard), str(inner), "-compose", "Minus_Src",
        "-composite", str(edge))
    run("convert", "-size", f"{w}x{h}", f"xc:{CONTOUR}", str(edge),
        "-compose", "CopyOpacity", "-composite", str(edge))
    # -composite stacks the *second* image on top, so the contour goes last
    run("convert", str(target), str(edge), "-compose", "Over", "-composite",
        f"PNG32:{target}")
    for path in (alpha, hard, inner, edge):
        path.unlink()


def scrub(target, warmth=10, fallback=None):
    """Replace any colour that is not part of the warm ramp.

    The key tests are hue-sign tests, so a handful of cold or near-neutral
    pixels survive them. Left alone they read as holes punched in the plate,
    so every colour whose red-blue spread is below the ramp width is repainted
    with the most common warm colour already in the plate. Most common rather
    than nearest: a near match can be as dark as the pixel it replaces and
    still read as a stain.
    """
    listing = subprocess.check_output(
        ["convert", str(target), "-alpha", "off", "-unique-colors", "txt:-"],
        text=True)
    warm, cold = [], []
    for line in listing.splitlines()[1:]:
        _, _, value = line.partition(":")
        if "(" not in value:
            continue
        rgb = [float(v) for v in
               value[value.find("(") + 1:value.find(")")].split(",")[:3]]
        # -unique-colors txt: already reports 0-255 components
        (warm if (rgb[0] - rgb[2]) >= warmth else cold).append(rgb)
    if not cold:
        return 0
    if fallback is None:
        counts = collections.Counter()
        for entry in subprocess.check_output(
                ["convert", str(target), "-alpha", "off", "-format", "%c",
                 "histogram:info:"], text=True).splitlines():
            head, _, tail = entry.partition(":")
            if "(" not in tail:
                continue
            rgb = [float(v) for v in
                   tail[tail.find("(") + 1:tail.find(")")].split(",")[:3]]
            if (rgb[0] - rgb[2]) >= warmth:
                counts[tuple(rgb)] += int(head.strip())
        if not counts:
            return 0
        fallback = "srgb(" + ",".join(
            f"{v:.6f}" for v in counts.most_common(1)[0][0]) + ")"
    for rgb in cold:
        source = "srgb(" + ",".join(f"{v:.6f}" for v in rgb) + ")"
        run("convert", str(target), "-fuzz", "0", "-fill", fallback,
            "-opaque", source, f"PNG32:{target}")
    return len(cold)


def hex_mask():
    path = ROOT / "hex_mask.png"
    run("convert", "-size", f"{CELL}x{CELL}", "xc:none",
        "-fill", "white",
        "-draw", f"polygon {HEX_CENTRE[0]},{HEX_CENTRE[1] - HEX_R} "
                 f"{HEX_CENTRE[0] + HEX_W},{HEX_CENTRE[1] - HEX_R // 2} "
                 f"{HEX_CENTRE[0] + HEX_W},{HEX_CENTRE[1] + HEX_R // 2} "
                 f"{HEX_CENTRE[0]},{HEX_CENTRE[1] + HEX_R} "
                 f"{HEX_CENTRE[0] - HEX_W},{HEX_CENTRE[1] + HEX_R // 2} "
                 f"{HEX_CENTRE[0] - HEX_W},{HEX_CENTRE[1] - HEX_R // 2}",
        str(path))
    return path


# Terrain plates are drawn here for the same reason the hex mask is: the
# renders cross their own structures (two furrow directions read as basketry),
# and a tile has to be readable before it can be a tile. The material is the
# plate's own business, so every terrain declares a base ramp, an edge ramp and
# a pattern, and the patterns are written rather than sampled.
#
# The two ramps of a terrain are kept apart on purpose. When a base and an
# edge ramp sit close together the quantise pass averages across the boundary
# and the edge turns into a band that is neither material.
FIELD_FURROW_ANGLE = 30          # degrees, parallel to the hex's flat edges
FIELD_FURROW_STEP = 8            # px between furrow centres
FIELD_TURF_DEPTH = 4            # px of edge material inside the hex edge
FIELD_HEADLAND = 3              # px of unploughed margin inside the edge

# base: darkest to lightest. edge: the material that meets the tile boundary.
TERRAIN = {
    "terrain_field_hex": {
        "name": "Field", "pattern": "furrows", "base": (
            (54, 40, 30), (72, 55, 41), (90, 70, 52), (108, 86, 64)),
        "edge": ((48, 74, 38), (62, 90, 44), (78, 106, 52)),
    },
    "terrain_hill_hex": {
        # a hill is grass over rock: green surface, stone breaking through
        "name": "Hill", "pattern": "outcrop", "base": (
            (58, 68, 34), (76, 88, 44), (96, 110, 56), (118, 132, 70)),
        "edge": ((48, 74, 38), (62, 90, 44), (78, 106, 52)),
        # the stone breaking through the grass is part of the material and is
        # declared as such; an undeclared tone is a defect, not a highlight
        "extra": ((112, 104, 92), (128, 120, 106), (146, 138, 122)),
    },
    "terrain_pasture_hex": {
        "name": "Pasture", "pattern": "blades", "base": (
            (50, 62, 32), (70, 84, 42), (92, 108, 54), (116, 132, 68)),
        "edge": ((44, 60, 30), (58, 78, 38), (74, 96, 48)),
    },
    "terrain_forest_hex": {
        "name": "Forest", "pattern": "canopy", "base": (
            (24, 40, 26), (34, 54, 32), (46, 70, 40), (62, 90, 52)),
        "edge": ((20, 36, 24), (30, 50, 30), (42, 66, 38)),
    },
    "terrain_marsh_hex": {
        # standing water first, reeds as thin bright stems through it
        "name": "Marsh", "pattern": "reeds", "base": (
            (26, 40, 44), (36, 54, 58), (48, 70, 72), (62, 88, 86)),
        "edge": ((34, 48, 30), (46, 64, 36), (60, 82, 44)),
    },
    "terrain_heath_hex": {
        "name": "Heath", "pattern": "moss", "base": (
            (52, 48, 34), (72, 66, 44), (94, 86, 58), (116, 108, 74)),
        "edge": ((44, 56, 34), (58, 74, 44), (74, 92, 54)),
    },
    "terrain_salt_flat_hex": {
        # dried crust: warm off-white, with the seams well below the plates
        "name": "Salt flat", "pattern": "cracks", "base": (
            (96, 90, 80), (122, 116, 104), (146, 140, 126), (168, 162, 146)),
        "edge": ((84, 80, 70), (106, 100, 88), (128, 122, 108)),
    },
    "terrain_ruin_hex": {
        "name": "Ruin", "pattern": "rubble", "base": (
            (50, 44, 36), (68, 60, 48), (88, 78, 62), (108, 96, 78)),
        "edge": ((42, 44, 32), (58, 60, 42), (76, 78, 54)),
    },
    "terrain_water_hex": {
        "name": "Water", "pattern": "ripples", "base": (
            (30, 46, 62), (44, 64, 82), (60, 84, 104), (80, 108, 128)),
        "edge": ((26, 40, 54), (38, 56, 74), (52, 74, 96)),
    },
}

# The old per-material constants are kept as aliases so the plate that was
# already signed off keeps its numbers: the field is the furrow reference.
FIELD_RAMP = TERRAIN["terrain_field_hex"]["base"]
FIELD_TURF = TERRAIN["terrain_field_hex"]["edge"]


def inside_hex(x, y):
    """Is (x, y) inside the declared pointy-top hex?"""
    cx, cy = HEX_CENTRE
    if abs(x - cx) > HEX_W or abs(y - cy) > HEX_R:
        return False
    shoulder = HEX_R / 2
    for sign in (1, -1):
        for side in (-1, 1):
            ax, ay = cx, cy + sign * HEX_R
            bx, by = cx + side * HEX_W, cy + sign * shoulder
            cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
            if cross * side * sign > 0:
                return False
    return True


def in_edge_band(x, y, depth):
    """True when (x, y) is within `depth` of the hex boundary.

    The point is pushed *away* from the centre: if it leaves the hexagon it was
    sitting against the edge. Measuring the band this way keeps it the same
    width all the way round, where the distance to the infinite side lines
    produces corner wedges big enough to let the edge material eat half the tile.
    """
    cx, cy = HEX_CENTRE
    dx, dy = x - cx, y - cy
    length = (dx * dx + dy * dy) ** 0.5
    if not length:
        return False
    return not inside_hex(x + dx / length * depth, y + dy / length * depth)


def blend_tone(ramp, phase):
    """Interpolate a tone along a ramp. Keeps structure from banding."""
    if len(ramp) == 1:
        return ramp[0]
    scaled = phase * (len(ramp) - 1)
    low = min(int(scaled), len(ramp) - 2)
    weight = scaled - low
    return tuple(int(round(ramp[low][c] + (ramp[low + 1][c] - ramp[low][c])
                          * weight)) for c in range(3))


def value_noise(x, y, seed):
    """Cheap deterministic hash noise in [0, 1). Used to break up flat areas."""
    n = (int(x) * 374761393 + int(y) * 668265263 + seed * 1274126177)
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFF) / 65536.0


def pattern_tone(pattern, base, x, y, across, along, seed):
    """The tone of the tile interior at (x, y), by material."""
    import math
    theta = math.radians(FIELD_FURROW_ANGLE)
    fx, fy = math.cos(theta), math.sin(theta)
    if pattern == "furrows":
        step = (across % FIELD_FURROW_STEP) / FIELD_FURROW_STEP
        return blend_tone(base, step)
    if pattern == "strata":
        # rock reads in layers, but a perfect grid of bands looks like tiling,
        # so each band is pushed by a slow drift and broken by noise
        drift = value_noise(int(x // 11), int(y // 26), seed) * 7
        step = ((y * 0.75 + drift) % 9) / 9.0
        return blend_tone(base, (step + value_noise(x, y, seed) * 0.35) % 1.0)
    if pattern == "outcrop":
        # grass with rock breaking through in irregular patches
        rock = (value_noise(int(x // 10), int(y // 10), seed) +
                value_noise(int(x // 4), int(y // 4), seed + 3) * 0.35)
        if rock > 0.82:
            stone = (value_noise(x, y, seed + 9) * 0.5 +
                     value_noise(int(x // 3), int(y // 3), seed) * 0.5)
            return (112 + int(stone * 34), 104 + int(stone * 30),
                    92 + int(stone * 26))
        blades = (across * 0.5 + along * 0.3) % 1.0
        return blend_tone(base, (blades + value_noise(x, y, seed) * 0.4) % 1.0)
    if pattern == "blades":
        # grass leans; two lean directions is what grass is, not basketry
        lean = (across * 0.6 + along * 0.35) % 1.0
        return blend_tone(base, (lean + value_noise(x, y, seed) * 0.4) % 1.0)
    if pattern == "canopy":
        # three octaves of blobs, so the canopy is a mass of crowns of
        # different sizes. A single lattice reads as a net, not as woodland.
        tone = 0.0
        for octave, weight in ((4, 0.5), (7, 0.3), (11, 0.2)):
            n = value_noise(int(x // octave), int(y // octave),
                            seed + octave)
            tone += (1.0 - n) * weight
        speck = value_noise(x, y, seed) * 0.15
        return blend_tone(base, (tone + speck) % 1.0)
    if pattern == "reeds":
        # standing water, with stems standing through it on a wandering pitch
        # so they do not line up into a picket fence
        water = (along * 0.4 + value_noise(x, y, seed) * 0.3) % 1.0
        pitch = 6 + int(value_noise(int(x // 12), int(y // 12), seed) * 4)
        offset = int(value_noise(int(x // 12), int(y // 12), seed + 5) * pitch)
        stem = abs(((x + offset) % pitch) - pitch / 2) / (pitch / 2)
        return blend_tone(base, min(1.0, water * 0.55 + stem * 0.45))
    if pattern == "moss":
        patch = (value_noise(int(x // 7), int(y // 7), seed) +
                 value_noise(x, y, seed) * 0.4) % 1.0
        return blend_tone(base, patch)
    if pattern == "cracks":
        # Salt crust lifts into plates with wandering seams. The seam is a
        # contour of the noise field, not a grid: seams on two axes draw a
        # rhombus lattice, which is a pattern rather than a crust.
        field = (value_noise(int(x // 6), int(y // 6), seed) +
                 value_noise(x, y, seed) * 0.5)
        seam = abs(field - 0.75) * 4.0
        plate = value_noise(int(x // 9), int(y // 9), seed + 11) * 0.4
        return blend_tone(base, min(1.0, seam * 0.6 + plate))
    if pattern == "rubble":
        block = value_noise(int(x // 5), int(y // 5), seed)
        seam = min((x % 5) / 5.0, (y % 5) / 5.0)
        return blend_tone(base, (block * 0.8 + seam * 0.2) % 1.0)
    if pattern == "ripples":
        # water reads as long horizontal bands, brightest where it catches
        wave = (along * 0.5 + math.sin(across * 0.18) * 0.8) % 1.0
        return blend_tone(base, (wave + value_noise(x, y, seed) * 0.2) % 1.0)
    return blend_tone(base, 0.5)


def draw_terrain(terrain_id, terrain):
    """Write one terrain plate. The pattern is drawn, never sampled."""
    import math

    width = 2 * HEX_W + 1
    height = 2 * HEX_R + 1
    cx, cy = HEX_CENTRE
    theta = math.radians(FIELD_FURROW_ANGLE)
    fx, fy = math.cos(theta), math.sin(theta)
    px, py = -fy, fx
    seed = sum(ord(c) for c in terrain_id)

    # the plate is drawn in its own tight box, so every geometric test has to
    # be asked about the canvas coordinates the plate will actually sit at
    origin_x = cx - width / 2
    origin_y = cy - height / 2
    data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            canvas_x = origin_x + x + 0.5
            canvas_y = origin_y + y + 0.5
            in_edge = in_edge_band(canvas_x, canvas_y, FIELD_TURF_DEPTH)
            in_headland = in_edge or in_edge_band(
                canvas_x, canvas_y, FIELD_TURF_DEPTH + FIELD_HEADLAND)
            across = (canvas_x - cx) * px + (canvas_y - cy) * py
            along = (canvas_x - cx) * fx + (canvas_y - cy) * fy
            i = (y * width + x) * 4

            if in_edge:
                blend = (abs(along) * 0.6) % 1.0
                data[i:i + 4] = bytes((*blend_tone(terrain["edge"], blend),
                                       255))
                continue
            if in_headland:
                data[i:i + 4] = bytes((*terrain["base"][2], 255))
                continue

            colour = pattern_tone(terrain["pattern"], terrain["base"],
                                  x, y, across, along, seed)
            data[i:i + 4] = bytes((*colour, 255))

    # the PNG is written from the raw buffer directly, so no resampler ever
    # touches the structure: a single resample is what turned parallel lines
    # into the noise the previous plate had
    path = PLATES / f"{terrain_id}.drawn.png"
    write_rgba_png(path, width, height, data)
    return path


def inside_quad(x, y, points):
    """Even-odd containment test for a closed polygon given as (x, y) pairs."""
    inside = False
    count = len(points)
    for index in range(count):
        ax, ay = points[index]
        bx, by = points[(index + 1) % count]
        if (ay > y) != (by > y):
            cross_x = (bx - ax) * (y - ay) / (by - ay) + ax
            if x < cross_x:
                inside = not inside
    return inside


def draw_architecture(arch_id, arch):
    """Compose one building in strict front elevation, on the tile ground line."""
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    base_left = cx - arch["width"] / 2
    wall_top = ground - arch["wall_h"]
    overhang = arch["overhang"]
    ramp = ARCH_RAMP[arch["wall"]]
    data = bytearray(width * height * 4)

    def put(px, py, colour):
        # None leaves the pixel empty. A silhouette that needs a shaped edge -
        # a stake cut to a point, an arch - is cut, not painted black: a black
        # pixel is opaque matter, and matter is what the material check reads.
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                if 0 <= px < width and 0 <= py < height:
                    put(px, py, colour_fn(px, py))

    def outline(points, colour=ARCH_DARK):
        """A 1px contour along a polygon edge: what makes it read as flat 2D."""
        for index in range(len(points)):
            ax, ay = points[index]
            bx, by = points[(index + 1) % len(points)]
            steps = int(max(abs(bx - ax), abs(by - ay))) + 1
            for step in range(steps + 1):
                t = step / max(1, steps)
                put(round(ax + (bx - ax) * t), round(ay + (by - ay) * t), colour)

    def log_tone(px, py):
        # stacked logs: a dark seam between courses, and one staggered end
        # joint per course. Both run with the wall, never across it.
        above = ground - py
        course = int(above // 4)
        if above % 4 == 0:
            return ARCH_DETAIL[0]
        if int(px) % 23 == (course * 9) % 23:
            return ARCH_DETAIL[0]
        return ramp[(course * 3 + int(px) // 11) % 4]

    def plank_tone(px, py):
        board = (ground - py) % 5
        seam = board == 0
        return (22, 17, 12) if seam else ramp[min(3, int(board * 0.7) % 4)]

    def stone_tone(px, py):
        block = int((ground - py) // 4) * 13 + int(px // 7) * 5
        seam = ((ground - py) % 4 == 0) or (px % 7 == 0)
        return ARCH_DETAIL[2] if seam else ramp[(block // 7) % 4]

    def palisade_tone(px, py):
        # Every stake is cut to a point. The tip is a 3px triangle on top of a
        # 5px shaft, and alternate stakes stand one course taller, so the top
        # edge is a run of points and not a flat cap.
        post = int(px) % 6
        tip_height = 3
        top = ground - arch["wall_h"] - (3 if (int(px) // 6) % 2 else 0)
        shaft_top = top + tip_height
        if py < top:
            return None
        if py < shaft_top:
            # narrow the tip toward the stake centre
            if post < 1 or post > 3:
                return None
        if post == 5:
            return ARCH_DETAIL[6]     # the seam between two stakes
        if abs(py - (ground - arch["wall_h"] // 2)) < 2:
            return ARCH_DETAIL[0]     # the binding rail
        return ramp[1 + ((int(px) // 6) + int(ground - py)) % 3]

    def thatch_tone(px, py, slope):
        # thatch runs down the slope; the tone follows the course, not the pixel
        course = (slope * 0.9 + px * 0.15) % 6
        return ARCH_THATCH[min(3, int(course))]

    # --- wall ---------------------------------------------------------
    wall_x0 = base_left
    wall_x1 = base_left + arch["width"]
    if arch["wall"] == "timber":
        rect(wall_x0, wall_top, wall_x1, ground, log_tone)
    elif arch["wall"] == "plank":
        rect(wall_x0, wall_top, wall_x1, ground, plank_tone)
    elif arch["wall"] == "stone" and arch["roof"] != "deck":
        # a bridge has no wall: its deck owns the whole silhouette, and a wall
        # painted first would fill the arch void that the deck later leaves
        rect(wall_x0, wall_top, wall_x1, ground, stone_tone)
    elif arch["wall"] == "palisade":
        rect(wall_x0, wall_top - 6, wall_x1, ground, palisade_tone)
    elif arch["wall"] == "open":
        # a shelter has posts, not a wall: only the uprights are drawn
        for px in (wall_x0 + 2, wall_x1 - 6, (wall_x0 + wall_x1) / 2 - 2):
            rect(px, wall_top, px + 4, ground, log_tone)
    elif arch["wall"] == "gate":
        rect(wall_x0, wall_top, wall_x0 + 7, ground, palisade_tone)
        rect(wall_x1 - 7, wall_top, wall_x1, ground, palisade_tone)
        rect(wall_x0, wall_top + 5, wall_x0 + 7, wall_top + 8,
             lambda px, py: ramp[1])
        rect(wall_x1 - 7, wall_top + 5, wall_x1, wall_top + 8,
             lambda px, py: ramp[1])

    # --- roof geometry, needed before the chimney --------------------
    eave = wall_top
    ridge = wall_top - arch["ridge_h"]
    left = wall_x0 - overhang
    right = wall_x1 + overhang

    # --- chimney ------------------------------------------------------
    # Drawn before the roof on purpose: a chimney whose base stops at the
    # ridge line floats above a sloped roof instead of standing on it.
    if arch["chimney"]:
        chx = (left + 5) if arch["chimney"] == "left" else (right - 12)
        rect(chx, ridge - 7, chx + 6, ridge + 12, stone_tone)
        rect(chx - 1, ridge - 8, chx + 7, ridge - 6,
             lambda px, py: ARCH_DARK)
        rect(chx + 1, ridge - 6, chx + 5, ridge - 5,
             lambda px, py: ARCH_DETAIL[0])

    if arch["roof"] == "gable":
        apex = ((wall_x0 + wall_x1) / 2, ridge)
        for py in range(int(ridge), int(eave) + 1):
            half = (py - ridge) / max(1, (eave - ridge))
            x0 = apex[0] - (apex[0] - left) * half
            x1 = apex[0] + (right - apex[0]) * half
            rect(x0, py, x1, py, lambda px, y: thatch_tone(px, y, y - ridge))
        outline([(left, eave), (apex[0], ridge), (right, eave)])
    elif arch["roof"] == "shed":
        high = (left, ridge)
        low = (right, eave + 3)
        for px in range(int(left), int(right) + 1):
            t = (px - left) / max(1, (right - left))
            top_y = ridge + (eave + 3 - ridge) * t
            rect(px, top_y, px, eave + 3,
                 lambda x, y: thatch_tone(x, y, y - ridge))
        outline([high, low, (right, eave + 3)])
    elif arch["roof"] == "deck":
        # A bridge is a span, and a span is read from the arch under it: two
        # abutments carrying a deck, with the void between them left open. A
        # solid block between the piers would read as a wall, not a crossing.
        deck_y = eave - 4
        span_cx = (left + right) / 2
        # The arch is a void, so it is left empty and not painted: what shows
        # through is whatever tile the bridge crosses. Its radius is set by the
        # clear height under the deck, so the crown always meets the deck and
        # the springing always meets the abutments.
        arch_r = max(6, int(ground - deck_y) - 2)
        inner = arch_r + 2
        # abutments, then the spandrel: solid stone everywhere the arch is not
        for py in range(int(deck_y), int(ground) + 1):
            rise = ground - py
            void = (int((arch_r * arch_r - rise * rise) ** 0.5)
                    if rise < arch_r else 0)
            rect(left, py, right, py,
                 lambda px, y: (None if (void
                                          and abs(px - span_cx) < void)
                                else ARCH_RAMP["stone"][
                                    min(3, int((px - left) / 6) % 4)]))
        outline([(left, deck_y), (left, ground)])
        # deck and its two rails
        rect(left - 2, deck_y - 4, right + 2, deck_y,
             lambda px, py: ARCH_RAMP["plank"][min(3, int((px - left) / 6) % 4)])
        for rail in (0, 4):
            rect(left, deck_y - 4 - arch["ridge_h"] + rail,
                 right, deck_y - 4 - arch["ridge_h"] + rail + 2,
                 lambda px, py: ARCH_RAMP["palisade"][1 + rail // 4])
        post_x = left + 3
        while post_x < right - 3:
            rect(post_x, deck_y - 4 - arch["ridge_h"], post_x + 2, deck_y - 4,
                 lambda px, py: ARCH_RAMP["palisade"][3])
            post_x += 10
        outline([(left - 2, deck_y - 4), (right + 2, deck_y - 4),
                 (right + 2, deck_y), (left - 2, deck_y)])
        outline([(left, deck_y - 4 - arch["ridge_h"]),
                 (right, deck_y - 4 - arch["ridge_h"]),
                 (right, deck_y - 4 - arch["ridge_h"] + 6),
                 (left, deck_y - 4 - arch["ridge_h"] + 6)])
    elif arch["roof"] == "rail":
        # a gate gets a lintel; a palisade gets nothing, because its stakes
        # are already the top edge
        rect(left, eave - 3, right, eave, lambda px, py: ramp[2])
        outline([(left, eave - 3), (right, eave - 3)])
    else:
        rect(left, eave - 2, right, eave + 2,
             lambda px, py: ramp[min(3, int((px - left) / 8) % 4)])

    # --- openings -----------------------------------------------------
    if arch["door"]:
        dx, dy, dw = arch["door"]
        rect(wall_x0 + dx, ground - dy, wall_x0 + dx + dw, ground,
             lambda px, py: ARCH_DARK if (int(px) in
                                           (int(wall_x0 + dx),
                                            int(wall_x0 + dx + dw - 1)))
             else (34, 24, 17))
    for wx, row in arch["windows"]:
        wsize = 7
        wy = ground - 8 - row * 9
        rect(wall_x0 + 6 + wx * 15, wy, wall_x0 + 6 + wx * 15 + wsize,
             wy + 6, lambda px, py: (16, 13, 10))
        rect(wall_x0 + 7 + wx * 15, wy + 1, wall_x0 + 5 + wx * 15 + wsize,
             wy + 5, lambda px, py: (74, 88, 96))

    path = PLATES / f"{arch_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def draw_hazard(hazard_id, hazard):
    """One hazard, standing on the tile ground line as an object on the tile.

    A hazard is a thing that is on the ground, not a state of the ground: a den
    in the wood, a soft patch you can walk into, a camp with a fire in it. So it
    is drawn and anchored the way props are, and it does not go inside the hex
    plate the way terrain does.

    The camp carries no hostility. See `HAZARD["hazard_band"]`.
    """
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def shade(px, py, left, right, top, bottom, tones):
        # Light from the upper left, and ONE side of it. The symmetric version
        # measured the distance to the NEAREST vertical edge, which put the
        # highlight at the top centre and painted every building with the same
        # bright inverted V - the same defect I had already fixed in the garment
        # torso. A one-sided falloff reads as a lit face and a shadowed one.
        edge = 1.0 - max(0.0, min(1.0, (px - left) / max(1.0, right - left)))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return tones[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def disc(ccx, ccy, radius, colour_fn):
        for py in range(int(ccy - radius), int(ccy + radius) + 1):
            dy = (py - ccy) / max(0.001, radius)
            if abs(dy) > 1:
                continue
            half = radius * (1 - dy * dy) ** 0.5
            for px in range(int(round(ccx - half)), int(round(ccx + half)) + 1):
                put(px, py, colour_fn(px, py))

    form = hazard["form"]
    earth = HAZARD_RAMP["earth"]
    fur = HAZARD_RAMP["fur"]
    peat = HAZARD_RAMP["peat"]
    water = HAZARD_RAMP["water"]
    timber = HAZARD_RAMP["timber"]
    cloth = HAZARD_RAMP["cloth"]
    smoke = HAZARD_RAMP["smoke"]

    if form == "den":
        # a low earth mound with a dark arched mouth: a den, not a wolf. The
        # wolf is what comes out of it, and the plate is where it lives
        half = hazard["width"] / 2
        top = ground - hazard["height"]
        for py in range(int(top), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.50 + 0.50 * u)      # a mound, widest at the base
            rect(cx - span, py, cx + span, py,
                 lambda px, y, a=cx - span, b=cx + span: shade(
                     px, y, a, b, top, ground, earth))
        # The mouth is an ARCH, not a rectangle. A rectangular hole in a mound
        # reads as a doorway in a wall; a domed one reads as a hole in the
        # ground, which is the whole claim of the plate.
        mouth_h = hazard["height"] * 0.52
        mouth_w = half * 0.34
        mouth_top = ground - 1 - mouth_h
        for py in range(int(mouth_top), int(ground) + 1):
            # clamped: a fractional power of a negative float is a complex
            # number in python, and one row of float drift is enough to raise
            # `(1 - u) ** 0.5` to one
            u = min(1.0, max(0.0, (py - mouth_top) / max(1.0, ground - mouth_top)))
            # round shoulders: full width low down, closing to a dome on top
            span = mouth_w * (1 - (1 - u) ** 2) ** 0.5
            if span <= 0.4:
                continue
            solid(cx - span, py, cx + span, py, HAZARD_DETAIL[1])
        # turf over the crown, so the den is part of the ground it sits on
        for step in range(int(half * 1.6)):
            put(round(cx - half * 0.8 + step),
                round(top + (1 if step % 2 else 0) + step // 12),
                fur[2 if step % 3 else 0])

    elif form == "mire":
        # Standing water in peat, with reeds.
        #
        # Two earlier versions read as a BOAT, and both were the same mistake:
        # a smooth lens, widest in the middle, tapering to points at both ends.
        # That is a hull. A mire is a flat wet hollow, so the profile is widest
        # at the ground and the top edge is RAGGED - the waterline of a puddle
        # is irregular, and irregularity is the whole difference between a
        # puddle and a boat.
        half = hazard["width"] / 2
        top = ground - hazard["height"]
        span_at = lambda u: half * (0.72 + 0.28 * u)
        for px in range(int(cx - half), int(cx + half) + 1):
            off = abs(px - cx) / half
            # a per-column top: the waterline wobbles, and the wobble grows
            # away from the middle so the shape is not a symmetric lens
            wobble = (3.0 * ((px * 7919) % 5) / 4.0) * (0.35 + 0.65 * off)
            col_top = int(top + wobble)
            for py in range(col_top, int(ground) + 1):
                u = min(1.0, max(0.0, (py - col_top) /
                                  max(1.0, ground - col_top)))
                if off > span_at(1) / half:
                    continue
                put(px, py, water[min(3, int(u * 3))])
        # peat at the near edge, so the water sits IN something
        for px in range(int(cx - half), int(cx + half) + 1):
            put(px, int(ground), peat[2])
            put(px, int(ground) + 1, peat[0])
        # two short glints on the surface, which is what reads as water
        for offset, length in ((-9, 4), (2, 6)):
            for step in range(length):
                put(round(cx + offset + step), round(top + 3), water[3])
        # reeds, so it is bog and not a puddle
        for offset, lean in ((-13, -2), (-6, 1), (7, -1), (14, 2)):
            rx = cx + offset
            for step in range(int(hazard["height"] * 1.7)):
                put(round(rx + lean * step / 4), round(ground - step),
                    peat[3] if step % 3 else timber[2])

    elif form == "camp":
        # A CAMP. Fire, lean-to, a bundle of gear, and smoke.
        #
        # Nothing here says whether the people in it will sell to you or rob
        # you. The owner is explicit that a camp may be peaceful and need not
        # be a hazard at all, but to a traveller on this tile the two are the
        # same thing: smoke, a fire, a shelter. The truth arrives as a `Report`.
        # A later agent who thinks this plate is too friendly has not found a
        # bug - they have found the design, and they are about to break it by
        # drawing a menacing warband, which would tell the player something the
        # game has not told them yet.
        half = hazard["width"] / 2
        top = ground - hazard["height"]
        base = ground - 1

        # the lean-to: a ridge pole on one post, the skin draped off it. Drawn
        # as a few tone bands, not one band per pixel - at 1px the skin turned
        # into a barcode and read as nothing at all.
        post_x = cx - half * 0.58
        for py in range(int(top), int(base) + 1):
            u = (py - top) / max(1.0, base - top)
            # the ridge falls away to the right; the skin is the triangle under it
            edge = post_x + (cx + half * 0.40 - post_x) * u
            for px in range(int(post_x), int(edge) + 1):
                # tone by height within the skin, so it has one light direction
                put(px, py, cloth[1 + (1 if py % 7 == 0 else 0)])
        solid(post_x, top, post_x, base, timber[2])          # the post
        solid(post_x, top, cx + half * 0.40, top, timber[3])  # the ridge pole
        # a peg and a guy line on the open side
        solid(cx + half * 0.40, top, cx + half * 0.40, base, timber[2])

        # the fire: embers, and two tongues of flame. Warm, and the only warm
        # light in the whole family, because it is the only thing here that is
        # alive.
        fx = cx + half * 0.14
        for step in range(7):                                # the stick bed
            put(round(fx - 3 + step), base, timber[1] if step % 2 else timber[2])
        disc(fx, base - 1, 2.6,
             lambda px, py: shade(px, py, fx - 2.6, fx + 2.6, base - 4, base,
                                  HAZARD_RAMP["ember"]))
        # `tongue`, not `height`: a loop variable called `height` shadows the
        # canvas height, and the plate was written 128x7 - the last flame
        # length - with an empty alpha channel.
        for dx, tongue in ((-1, 5), (1, 4), (0, 7)):
            for step in range(tongue):
                put(round(fx + dx), round(base - 2 - step),
                    HAZARD_RAMP["ember"][3 if step < tongue * 0.4 else 2])

        # gear: a bundle and a bundle, so the camp has been lived in
        for dx, dy, tone in ((-half * 0.24, 2, 1), (-half * 0.24, 0, 2),
                             (half * 0.42, 1, 2)):
            disc(cx + dx, base - dy - 1, 2.4,
                 lambda px, py, tn=tone: cloth[tn])

        # The smoke. It is the one thing a traveller can see from far off and
        # the only reason this plate is worth drawing, so it has to read - but
        # as puffs, not as a streak. The first version drew one disc per row at
        # a light tone and they merged into a white diagonal that was the
        # loudest thing in the tile and read as a sail.
        for step in range(0, int(hazard["height"] * 0.8), 2):
            u = step / max(1.0, hazard["height"] * 0.8)
            drift = u * u * 5
            put(round(fx + drift), round(base - 5 - step),
                smoke[min(2, int(u * 3))])
            if step % 4 == 0:                      # a puff off the main thread
                put(round(fx + drift + 1), round(base - 5 - step),
                    smoke[min(1, int(u * 3))])

    path = PLATES / f"{hazard_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def draw_camp(camp_id, camp):
    """One camp, standing on the tile ground line.

    A camp is a *derived view form*: `tile_view.camp_form()` returns the form
    from the pack's kind, the owner's status and the head count. So the plate is
    keyed on the form, and the manifest records the exact discriminator the code
    branches on, so a plate for a form the code cannot return is caught by the
    validator rather than shipped.
    """
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def shade(px, py, left, right, top, bottom, tones):
        # Light from the upper left, and ONE side of it. The symmetric version
        # measured the distance to the NEAREST vertical edge, which put the
        # highlight at the top centre and painted every building with the same
        # bright inverted V - the same defect I had already fixed in the garment
        # torso. A one-sided falloff reads as a lit face and a shadowed one.
        edge = 1.0 - max(0.0, min(1.0, (px - left) / max(1.0, right - left)))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return tones[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def disc(ccx, ccy, radius, colour_fn):
        for py in range(int(ccy - radius), int(ccy + radius) + 1):
            dy = (py - ccy) / max(0.001, radius)
            if abs(dy) > 1:
                continue
            half = radius * (1 - dy * dy) ** 0.5
            for px in range(int(round(ccx - half)), int(round(ccx + half)) + 1):
                put(px, py, colour_fn(px, py))

    timber = CAMP_RAMP["timber"]
    cloth = CAMP_RAMP["cloth"]
    smoke = CAMP_RAMP["smoke"]
    ember = CAMP_RAMP["ember"]
    ash = CAMP_RAMP["ash"]
    stone = CAMP_RAMP["stone"]
    thatch = CAMP_RAMP["thatch"]
    water = CAMP_RAMP["water"]
    salt = CAMP_RAMP["salt"]
    earth = CAMP_RAMP["timber"]
    hull = CAMP_RAMP["hull"]
    form = camp["form"]
    half = camp["width"] / 2

    def tent_shape(px_top, apex, span, skin, ridge):
        """A ridge tent: two poles and a skin, wide at the foot."""
        for py in range(int(px_top), int(ground) + 1):
            u = (py - px_top) / max(1.0, ground - px_top)
            w = span * u ** 0.85
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, shade(px, py, cx - w, cx + w, px_top, ground, skin))
        solid(int(cx - span), px_top, int(cx - span), ground, ridge)
        solid(int(cx + span), px_top, int(cx + span), ground, ridge)
        solid(int(cx - span), int(px_top), int(cx + span), int(px_top), ridge)

    def fire(fx, size=3.0):
        for step in range(7):
            put(round(fx - 3 + step), ground - 1, timber[1 if step % 2 else 2])
        disc(fx, ground - 2, size,
             lambda px, py: shade(px, py, fx - size, fx + size,
                                  ground - 2 - size, ground, ember))
        for dx, tongue in ((-1, 4), (1, 3), (0, 6)):
            for step in range(tongue):
                put(round(fx + dx), round(ground - 3 - step),
                    ember[3 if step < tongue * 0.4 else 2])

    def smoke_plume(fx, height, lean=5.0):
        for step in range(0, int(height), 2):
            u = step / max(1.0, height)
            put(round(fx + u * u * lean), round(ground - 5 - step),
                smoke[min(2, int(u * 3))])
            if step % 4 == 0:
                put(round(fx + u * u * lean + 1), round(ground - 5 - step),
                    smoke[min(1, int(u * 3))])

    if form == "wagon_camp":
        # A wagon drawn up on a camp: a bed, a tilt over it, and a fire beside.
        bed_w, bed_h = 26, 11
        bx0, bx1 = cx - 12, cx + 14
        by0 = ground - 4 - bed_h
        for py in range(int(by0), int(ground - 3) + 1):      # the bed
            rect(bx0, py, bx1, py,
                 lambda px, y: shade(px, y, bx0, bx1, by0, ground, timber))
        for py in range(int(by0), int(by0 + 3) + 1):          # the tilt
            solid(bx0, py, bx1, py, cloth[2 if py % 2 else 3])
        for py in range(int(ground - 3), int(ground) + 1):
            solid(bx0, py, bx1, py, timber[0])
        for wx in (bx0 + 5, bx1 - 5):                        # two wheels
            for py in range(int(ground - 9), int(ground) + 1):
                u = (py - (ground - 9)) / 9.0
                w = 5 * (1 - (u - 0.5) ** 2 * 4) ** 0.5 if abs(u - 0.5) < 0.5 else 0
                if w <= 0.4:
                    continue
                for px in range(int(wx - w), int(wx + w) + 1):
                    put(px, py, shade(px, py, wx - w, wx + w, ground - 9,
                                      ground, timber))
        for step in range(int(bed_w * 0.9)):                  # the shaft
            put(round(bx1 + 2 + step), round(ground - 2 - step * 0.25),
                timber[2 if step % 3 else 1])
        fire(cx + half - 6)
        smoke_plume(cx + half - 6, 12)

    elif form == "tent_camp":
        # Settlers' tents: two ridge tents and a small fire. A tent is a
        # triangle, so the second tent is what makes it a camp and not a hut.
        tent_shape(ground - 22, ground - 22, 11, cloth, timber[2])
        tent_shape(ground - 15, ground - 15, 8, cloth, timber[2])
        fire(cx + half - 4, 2.4)
        smoke_plume(cx + half - 4, 9, 3.0)

    elif form == "pavilion_camp":
        # A lord's pavilion: one large tent, taller and wider than the settlers'
        # tents, with a banner pole - the only way a player can tell whose camp
        # this is, and the code says it is a holder or a thegn.
        tent_shape(ground - 32, ground - 32, 15, cloth, timber[3])
        for step in range(18):                     # the banner
            bx = cx + half - 4
            put(round(bx), round(ground - 32 - step), timber[2])
            if 2 <= step <= 12:
                for px in range(int(bx + 1), int(bx + 7) + 1):
                    put(px, round(ground - 32 - step), ash[2])
        fire(cx - half + 4, 2.6)
        smoke_plume(cx - half + 4, 10, 3.0)

    elif form == "fortified_camp":
        # A host of four or more, so the camp is palisaded: a stake wall round
        # the back and sides, tents inside, and a smoke column.
        top = ground - camp["height"]
        for py in range(int(top), int(ground) + 1):          # the palisade
            for px in range(int(cx - half), int(cx + half) + 1):
                # only the back and the two sides, so the camp is open to the
                # front and the silhouette is a cup, not a box
                near_side = (px < cx - half + 7 or px > cx + half - 7)
                if not (near_side or py < top + 6):
                    continue
                # the stakes are notated only along the top; a checkerboard
                # over the whole wall is 50% hole, and a plate that is half
                # holes is a plate that is half its own contour
                # the stake notches are only along the top; a checkerboard
                # over the whole wall is half holes, and a plate that is half
                # holes is a plate that is half its own contour
                put(px, py, timber[3] if (px + py) % 5 == 0 else
                    (timber[1] if near_side else timber[2]))
        for offset in (-10, 2):                              # tents inside
            tx = cx + offset
            for py in range(int(ground - 13), int(ground) + 1):
                u = (py - (ground - 13)) / 13.0
                w = 8 * u ** 0.8
                for px in range(int(tx - w), int(tx + w) + 1):
                    put(px, py, shade(px, py, tx - w, tx + w, ground - 13,
                                      ground, cloth))
            solid(int(tx - 8), int(ground - 13), int(tx + 8), int(ground - 13),
                  timber[2])
        fire(cx + 2, 2.6)
        smoke_plume(cx + 2, 16, 4.0)

    elif form == "campfire_camp":
        # A night's camp for three: no tent in the rules, and the plate should
        # not invent one. A fire, a pot, and bedding rolled at the edge.
        fire(cx, 3.4)
        smoke_plume(cx, 14, 4.0)
        for offset, length in ((-13, 11), (9, 9)):            # rolled bedding
            for step in range(length):
                px = cx + offset + step
                for row in range(5):                           # five deep
                    put(round(px), ground - row,
                        cloth[3 - (row + step) % 2] if row else cloth[1])
        for step in range(3):                                  # a low bank
            for px in range(int(cx - half), int(cx + half) + 1):
                put(px, ground - 1, cloth[0] if (px + step) % 3 else cloth[1])
        for step in range(4):                                # the pot on a tripod
            put(round(cx - 5 + step * 3), round(ground - 8 + step), timber[1])

    elif form == "foot_pot_camp":
        # A watch of two: the smallest camp, and it must read as small. A pot on
        # a tripod, a small fire, and nothing else - no tent, no bedding.
        fire(cx, 2.6)
        smoke_plume(cx, 9, 3.0)
        for step in range(5):                                # the tripod
            put(round(cx - 4 + step * 2), round(ground - 9 + step * 2), timber[1])
        for py in range(int(ground - 4), int(ground) + 1):   # the pot
            w = 3.4 * (1 - ((py - (ground - 4)) / 5.0 - 0.4) ** 2)
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, shade(px, py, cx - w, cx + w, ground - 4, ground,
                                  timber))

    elif form == "lost_camp":
        # What a caravan leaves behind: an overturned cart, a spilled load, and
        # cold ash. No fire, no smoke, no tent - the code says the pack is lost,
        # and a live camp would say the opposite.
        bed_w, bed_h = 20, 9
        bx0, bx1 = cx - 10, cx + 10
        for py in range(int(ground - 6), int(ground) + 1):   # the bed, tilted
            u = (py - (ground - 6)) / 6.0
            rect(bx0, py, int(bx1 - 8 * u), py,
                 lambda px, y: shade(px, y, bx0, bx1, ground - 6, ground,
                                     timber))
        for py in range(int(ground - 5), int(ground) + 1):   # a broken wheel
            u = (py - (ground - 5)) / 5.0
            w = 5 * (1 - (u - 0.5) ** 2 * 4) ** 0.5 if abs(u - 0.5) < 0.5 else 0
            if w <= 0.4:
                continue
            for px in range(int(cx + 4 - w), int(cx + 4 + w) + 1):
                put(px, py, shade(px, py, cx + 4 - w, cx + 4 + w, ground - 5,
                                  ground, timber))
        for step in range(9):                                # cold ash
            put(round(cx - 6 + step), round(ground - 1 - (step % 2)), ash[1])
        for step in range(6):                                # the spilled load
            put(round(cx + 8 + (step % 3)), round(ground - 1), cloth[1 + step % 2])

# --- the marked places ---------------------------------------------------
    elif form == "pier":
        # A pier is a thin structure, and drawn literally it is nearly all its
        # own contour - the verifier refused it three times for that, and it was
        # right each time. So the composition is a BOAT at the mooring and a
        # short jetty, not a long deck on posts: the hull is the mass, the
        # jetty is four thick piles and a stub, and the water carries the rest.
        hull_x = int(cx - half - 10)
        hull_len, hull_max = 24, 13
        for step in range(hull_len):                               # the hull
            u = step / (hull_len - 1.0)
            depth = 4 + (hull_max - 4) * (1 - abs(u - 0.5) * 2) ** 0.5
            for row in range(int(depth) + 1):
                # three tones across the sheer: strake, planking, bilge
                tone = 3 if row == 0 else (2 if row < depth * 0.7 else 0)
                put(hull_x + step, int(ground - 3 - row), hull[tone])
        for step in range(5):                                      # the bow post
            put(hull_x + hull_len - 1, int(ground - hull_max - step), timber[2])
        mast_x = hull_x + 10
        for step in range(hull_max + 4):                           # the mast
            for col in range(2):
                put(mast_x + col, int(ground - 4 - step), timber[2 - col])
        for step in range(8):                                      # the sail
            for col in range(6 - step // 2):
                put(mast_x + 2 + col, int(ground - hull_max + 2 - step),
                    cloth[3 - (col + step) % 2])
        jetty_x = int(cx + half - 16)
        for px in range(jetty_x, jetty_x + 15):                   # a short jetty
            put(px, ground - 11, timber[3])
            put(px, ground - 10, timber[1 + (px % 5 == 0)])
            put(px, ground - 9, timber[2])
            put(px, ground - 8, timber[0])
        for px in (jetty_x, jetty_x + 7, jetty_x + 14):            # the piles
            for py in range(int(ground - 8), int(ground) + 1):
                for col in range(3):
                    put(px + col, py, timber[2 if col == 0 else 0])
        for step in range(20):                                     # the water
            u = step / 19.0
            px = int(jetty_x + 16 + u * 9)
            put(px, int(ground - 1 - 1.6 * (1 - u)), water[2])
            put(px + 1, int(ground - 1 - 1.6 * (1 - u)), water[1])
            if step % 3 == 0:
                put(px + 1, int(ground - 3), water[3])

    elif form == "shed_with_stack":
        # a waystation is a shed and a stack: goods waiting to change hands
        for py in range(int(ground - 16), int(ground) + 1):
            u = (py - (ground - 16)) / 16.0
            w = half * (0.55 + 0.45 * u)
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, shade(px, py, cx - w, cx + w, ground - 16,
                                  ground, timber))
        for step in range(int(camp["width"] * 0.5)):               # the thatch
            put(round(cx - half * 0.5 + step), round(ground - 18 - step * 0.25),
                thatch[3 if step % 4 else 2])
        for step in range(9):                                      # the stack
            put(round(cx + half * 0.5 + (step % 3) - 1),
                round(ground - 3 - (step // 3) * 3), cloth[3 - step % 3])

    elif form == "wide_house":
        # a tavern is wide and low and has a door big enough to matter
        top = ground - camp["height"]
        for py in range(int(top + 6), int(ground) + 1):
            for px in range(int(cx - half), int(cx + half) + 1):
                put(px, py, shade(px, py, cx - half, cx + half, top, ground,
                                  timber))
        for step in range(int(camp["width"])):                     # the roof
            px = cx - half + step
            put(round(px), round(top + 6 - (1 - abs(step - camp["width"] / 2) /
                                              (camp["width"] / 2)) * 6),
                thatch[2 + (step % 2)])
        for py in range(int(top + 10), int(ground) + 1):          # the door
            put(round(cx + half * 0.3), py, timber[0])
        put(round(cx + half * 0.3), ground - 14, thatch[3])       # the sign

    elif form == "adit":
        # a mine mouth: a cut in the hillside, timbered, with spoil and ash
        for py in range(int(ground - camp["height"]), int(ground) + 1):
            u = (py - (ground - camp["height"])) / camp["height"]
            w = half * (1.0 - 0.25 * u)
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, earth[1 + (py % 3 == 0)])
        mouth_top = ground - 12
        for py in range(int(mouth_top), int(ground) + 1):          # the void
            u = (py - mouth_top) / 12.0
            w = half * 0.34 * (1 - 0.3 * u)
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, earth[0])
        for side in (-1, 1):                                      # the timbering
            for py in range(int(mouth_top) - 2, int(ground) + 1):
                put(round(cx + side * half * 0.4), py, timber[2])
        for step in range(int(half)):                             # the spoil
            put(round(cx + half * 0.7 + (step % 3)), round(ground - 1 - step // 3),
                earth[3 - step % 2])

    elif form == "lean_to":
        # a hermitage: one small leaning shelter and nothing else
        for py in range(int(ground - camp["height"]), int(ground) + 1):
            u = (py - (ground - camp["height"])) / camp["height"]
            w = half * (0.5 + 0.5 * u)
            for px in range(int(cx - w), int(cx + w) + 1):
                put(px, py, shade(px, py, cx - w, cx + w, ground - camp["height"],
                                  ground, timber))
        for step in range(int(camp["width"] * 0.6)):              # the skin
            put(round(cx - half * 0.5 + step), round(ground - camp["height"] - 1),
                cloth[3 if step % 3 else 2])
        for step in range(4):                                      # the woodpile
            put(round(cx + half * 0.7 + (step % 2) - 1),
                round(ground - 2 - step // 2), timber[3])

    elif form == "smoke_only":
        # No building. A tile can be marked `smoke` without anything standing on
        # it, and who is making that smoke is a Report, not a picture: drawing
        # a hut here would tell the player something the game has not said.
        for step in range(0, camp["height"], 2):
            u = step / float(camp["height"])
            r = 1.0 + 2.2 * u
            # the full ramp, and a darker core under each puff: a plume in
            # three tones of one ramp is a grey smear, not smoke
            disc(round(cx + u * u * 6), round(ground - 2 - step), r,
                 lambda px, py: smoke[min(3, int(u * 4))])
            if step % 6 == 0:
                disc(round(cx + u * u * 6), round(ground - 2 - step), r * 0.6,
                     lambda px, py: smoke[0])

    elif form == "keep":
        # the baron's seat: a stone tower over a hall, taller than anything else
        top = ground - camp["height"]
        for py in range(int(top + 12), int(ground) + 1):          # the hall
            for px in range(int(cx - half), int(cx + half) + 1):
                put(px, py, shade(px, py, cx - half, cx + half, top + 12,
                                  ground, stone))
        for py in range(int(top), int(top + 14) + 1):             # the tower
            for px in range(int(cx - half * 0.3), int(cx + half * 0.3) + 1):
                put(px, py, shade(px, py, cx - half * 0.3, cx + half * 0.3, top,
                                  top + 14, stone))
        for step in range(int(camp["width"] * 0.5)):              # the roof
            put(round(cx - half + step), round(top + 12 - step * 0.2),
                thatch[2 + (step % 2)])
        for py in range(int(top + 4), int(top + 9) + 1):          # the windows
            for px in (int(cx - half * 0.16), int(cx + half * 0.16)):
                put(round(px), py, timber[0])
        for px in range(int(cx - half * 0.1), int(cx + half * 0.1) + 1):
            put(round(px), ground - 4, timber[0])                 # the door

    elif form == "row_houses":
        # a city quarter: gable after gable, so the plate is a rhythm and not a
        # single house that happens to be wide
        for index, offset in enumerate((-14, 0, 14)):
            hx = cx + offset
            hw = half * 0.36
            htop = ground - camp["height"] + (4 if index == 1 else 0)
            for py in range(int(htop + 5), int(ground) + 1):
                for px in range(int(hx - hw), int(hx + hw) + 1):
                    put(px, py, shade(px, py, hx - hw, hx + hw, htop, ground,
                                      timber))
            for step in range(int(hw * 2)):                       # the gable
                px = hx - hw + step
                put(round(px), round(htop + 5 - (1 - abs(step - hw) / hw) * 5),
                    thatch[2 + (step % 2)])
            for py in range(int(htop + 10), int(ground) + 1):    # the door
                put(round(hx), py, timber[0])

    elif form == "ruin":
        # a ruin is what is LEFT: broken wall ends, no roof, and the ground
        # showing through. Drawing it as a small house would say it is lived in.
        top = ground - camp["height"]
        for side in (-1, 1):                                      # two wall ends
            for py in range(int(top), int(ground) + 1):
                u = (py - top) / camp["height"]
                h = int(6 + u * (camp["height"] - 4))             # broken tops
                for px in range(int(cx + side * half - 5),
                                int(cx + side * half - 5) + 6):
                    put(px, py, stone[1 + (px + py) % 3])
                del h
        for py in range(int(top + 10), int(ground) + 1):          # low middle
            for px in range(int(cx - half * 0.4), int(cx + half * 0.4) + 1):
                put(px, py, stone[1 + (py % 3 == 0)])
        for step in range(int(half * 1.4)):                       # fallen stone
            put(round(cx - half * 0.7 + step), round(ground - 1 - step // 4),
                stone[3 - (step % 2)])
        for step in range(6):                                      # a burnt post
            put(round(cx + half * 0.5 + (step % 2) - 1),
                round(ground - 2 - step // 2), earth[0])

    elif form == "salt_pans":
        # A salt works is shallow pans, but drawn as two flat bands the contour
        # ate two thirds of the plate. So the pans sit in a raised timber frame:
        # the baulks stand proud of the pans, which is both what a real salt
        # works looks like and what gives the plate an interior.
        frame_top = ground - 9
        for px in range(int(cx - half), int(cx + half) + 1):       # the baulk
            put(px, frame_top, timber[3])
            put(px, frame_top + 1, timber[1 + (px % 6 == 0)])
            put(px, frame_top + 2, timber[0])
        for index in range(4):                                     # the pans
            px0 = cx - half + 3 + index * (camp["width"] - 6) / 4
            w = (camp["width"] - 6) / 8
            for px in range(int(px0), int(px0 + w) + 1):
                u = (px - px0) / max(1.0, w)
                depth = max(1, int(ground - frame_top - 3))
                for row in range(depth):            # water graded down the pan
                    put(px, int(frame_top + 3 + row),
                        water[min(3, int(u * 4) + (row > depth * 0.6))])
                put(px, frame_top + 3, water[3])
        for step in range(int(half)):                              # the salt heap
            u = step / max(1.0, half)
            for row in range(5):
                put(round(cx + half - step), round(frame_top - 1 - row),
                    salt[min(3, int(u * 3) + (row > 2))])
        for step in range(int(half * 0.8)):                        # rakes
            put(round(cx - half + 2 + step), round(frame_top - 2 - step // 3),
                timber[2 if step % 3 else 1])

    elif form == "native_huts":
        # a tribal village is round huts under a thatch roof to the ground. The
        # roof IS the hut: a wall with a cone on it is a house of ours.
        for offset, radius in ((-11, 8), (2, 10), (13, 7)):
            hx = cx + offset
            for py in range(int(ground - radius * 0.7), int(ground) + 1):
                u = (py - (ground - radius * 0.7)) / max(1.0, radius * 0.7)
                w = radius * (0.72 + 0.28 * u)
                for px in range(int(hx - w), int(hx + w) + 1):
                    put(px, py, shade(px, py, hx - w, hx + w,
                                      ground - radius * 0.7, ground, earth))
            for step in range(int(radius * 2.2)):                 # the cone
                put(round(hx - radius * 1.1 + step),
                    round(ground - radius * 0.7 - (1 - abs(step - radius * 1.1) /
                                                   (radius * 1.1)) * radius),
                    thatch[2 + (step % 2)])
    path = PLATES / f"{camp_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def draw_prop(prop_id, prop):
    """Compose one prop in strict front elevation, on the tile ground line."""
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def disc(ccx, ccy, radius, colour_fn):
        for py in range(int(ccy - radius), int(ccy + radius) + 1):
            dy = py - ccy
            if abs(dy) > radius:
                continue
            half = radius * (1 - (dy / radius) ** 2) ** 0.5
            for px in range(int(round(ccx - half)), int(round(ccx + half)) + 1):
                put(px, py, colour_fn(px, py))

    def outline(points, colour=ARCH_DARK):
        for index in range(len(points)):
            ax, ay = points[index]
            bx, by = points[(index + 1) % len(points)]
            steps = int(max(abs(bx - ax), abs(by - ay))) + 1
            for step in range(steps + 1):
                u = step / max(1, steps)
                put(round(ax + (bx - ax) * u), round(ay + (by - ay) * u),
                    colour)

    def shade(px, py, left, right, top, bottom, ramp):
        """Tone by distance to the silhouette edge and by height.

        Tone must not be a function of horizontal distance from the centre of
        the object. That paints vertical stripes, and a striped round object
        reads as a crate: the same defect as the chevron in the log wall. What
        shades a solid form is how much of it faces the viewer, so the index
        comes from the distance to the nearest edge plus the light from above.
        """
        span = max(1.0, (right - left) / 2)
        edge = min(1.0, max(0.0, min(px - left, right - px) / span))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return ramp[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def grain(px, py, seed=0):
        """A stable per-pixel dither, so a loose material is not smooth plastic."""
        return ((px * 7 + py * 13 + seed * 29) % 5) < 2

    form = prop["form"]

    # --- a loose heap: salt, and anything else that piles up --------------
    if form == "heap":
        ramp = PROP_RAMP[prop["mats"][0]]
        half_max = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top), int(ground) + 1):
            t = (ground - py) / prop["height"]
            # a heap slumps: a rounded shoulder, a peak off centre, and a
            # spread foot. A clean cone reads as a tent, not as loose material.
            lean = (t ** 1.6) * 3.0
            half = half_max * (1 - t ** 1.9) ** 0.42
            half += grain(int(ground - py), 3) * (1.4 if t > 0.12 else 0.0)
            if half <= 0:
                continue
            rect(cx - half + lean, py, cx + half + lean, py,
                 lambda px, y, l=cx - half + lean, r=cx + half + lean: (
                     ramp[1] if grain(px, y, 5)
                     else shade(px, y, l, r, top, ground, ramp)))
        # a couple of spilled grains at the foot, so the heap meets the ground
        for px, py in ((cx - half_max - 3, ground - 1), (cx + half_max + 2,
                                                         ground - 1)):
            put(px, py, ramp[1])

    # --- a coursed stack: peat bricks --------------------------------------
    elif form == "brick_stack":
        ramp = PROP_RAMP[prop["mats"][0]]
        bw, bh = prop["brick_w"], prop["brick_h"]
        pitch = bw + 1
        span = prop["per_course"] * pitch - 1
        for course in range(prop["courses"]):
            y1 = ground - course * (bh + 1)
            y0 = y1 - bh
            # an odd count with a half-brick stagger on the middle courses:
            # running bond. The stagger is applied symmetrically so the stack
            # stays centred instead of walking off to one side.
            stagger = (pitch // 2) if course % 2 else 0
            x = cx - span / 2 - stagger
            for brick in range(prop["per_course"]):
                if not (cx - span / 2 - 1 <= x <= cx + span / 2):
                    x += pitch
                    continue
                solid(x, y0, x + bw, y1, ramp[(course * 2 + brick) % 4])
                outline([(x, y0), (x + bw, y0), (x + bw, y1), (x, y1)])
                x += pitch

    # --- sacks: a bulging bag, wide at the foot, tied at the neck ----------
    elif form == "sacks":
        ramp = PROP_RAMP[prop["mats"][0]]
        sw, sh = prop["sack_w"], prop["sack_h"]
        gap = 3
        x = cx - (prop["count"] * sw + (prop["count"] - 1) * gap) / 2
        for index in range(prop["count"]):
            base = ground
            top = base - sh
            for py in range(int(top), int(base) + 1):
                t = (base - py) / sh
                if t < 0.16:                      # the foot spreads on the ground
                    half = (sw / 2) * (0.86 + 0.14 * (t / 0.16))
                elif t < 0.72:                    # the body is at its widest
                    half = (sw / 2) * (1.0 - 0.06 * ((t - 0.16) / 0.56) ** 2)
                else:                             # it draws in to the tie
                    u = (t - 0.72) / 0.28
                    half = (sw / 2) * (0.94 - 0.66 * u ** 1.5)
                rect(x + 1, py, x + sw - 1, py,
                     lambda px, y: shade(px, y, x + 1, x + sw - 1, top, base,
                                         ramp))
            # the tie, and the two ears of cloth above it
            neck = top + int(sh * 0.16)
            solid(x + sw / 2 - 4, neck - 2, x + sw / 2 + 4, neck + 1,
                  PROP_DETAIL[0])
            solid(x + sw / 2 - 3, top, x + sw / 2 + 3, neck - 2,
                  ramp[2])
            x += sw + gap

    # --- a bound bale: rounded block, rope over the short axis ------------
    elif form == "bale":
        ramp = PROP_RAMP[prop["mats"][0]]
        w, h = prop["width"], prop["height"]
        cut = 6
        for py in range(int(ground - h), int(ground) + 1):
            # A compressed bale is soft on every corner. Squaring the base to
            # sit on the ground turns it into a crate with straps.
            inset = (max(0.0, (ground - h) - py)
                     + max(0.0, py - (ground - 2))) * 1.1
            half = w / 2 - inset
            if half <= 0.5:
                continue
            rect(cx - half, py, cx + half, py,
                 lambda px, y, l=cx - half, r=cx + half: shade(
                     px, y, l, r, ground - h, ground, ramp))
        for strap in range(prop["straps"]):
            sx = round(cx - w / 4 + strap * (w / 2))
            rect(sx - 1, ground - h, sx + 1, ground,
                 lambda px, py: PROP_DETAIL[1])
        outline([(cx - w / 2 + cut, ground - h), (cx + w / 2 - cut, ground - h),
                 (cx + w / 2, ground - h + cut), (cx + w / 2, ground - 2),
                 (cx + w / 2 - 2, ground), (cx - w / 2 + 2, ground),
                 (cx - w / 2, ground - 2), (cx - w / 2, ground - h + cut)])

    # --- logs: cylinders seen from the side, because end-on is unreadable --
    # Nine 9px circles cannot carry a growth ring: at that size the rings and
    # the bark compete for the same pixels and the stack reads as a bunch of
    # blobs. A horizontal cylinder is unmistakable at any size, so the stack is
    # drawn side on - which is also what a front elevation of a woodpile is.
    elif form == "log_stack":
        bark = PROP_RAMP[prop["mats"][0]]
        length, thick = prop["log_len"], prop["log_h"]
        for row in range(prop["rows"]):
            y = ground - thick / 2 - row * (thick - 1)
            x = cx - length / 2 + (1.5 if row % 2 else 0)
            for log in range(prop["logs_per_row"]):
                # a cylinder: a capsule, lit from above, grooved along its length
                for py in range(int(y - thick / 2), int(y + thick / 2) + 1):
                    u = (py - (y - thick / 2)) / thick
                    inset = thick / 2 * (1 - (1 - min(1.0, max(0.0, u)) ** 2) ** 0.5)
                    a, b = x + inset, x + length - inset
                    if b - a < 1:
                        continue
                    rect(a, py, b, py,
                         lambda px, yy: bark[min(3, int(
                             (0.35 + 0.65 * (1 - abs(u - 0.28) * 1.7)) * 3.999))])
                # the sawn end, and one bark groove, so it is a log and not a bar
                solid(x + 1, int(y - thick / 2) + 1, x + 3,
                      int(y + thick / 2) - 1, PROP_RAMP["endgrain"][1])
                rect(x + 5, int(y - 1), x + length - 5, int(y),
                     lambda px, py: PROP_DETAIL[0])
                x += length - 2

    # --- a hull: pointed bow, rounded stern, curved sheer -----------------
    elif form == "hull":
        ramp = PROP_RAMP[prop["mats"][0]]
        w, hh = prop["width"], prop["hull_h"]
        top = ground - hh
        for py in range(int(top), int(ground) + 1):
            # t runs from the sheer down to the keel. A hull is widest at the
            # sheer and draws in to the keel; measuring from the keel instead
            # gives the widest point at the bottom, which is a mound, not a boat.
            t = (py - top) / hh
            half = (w / 2) * (0.14 + 0.86 * (1 - min(1.0, t)) ** 0.5)
            if half <= 0.4:
                continue
            l, r = cx - half, cx + half
            rect(l, py, r, py,
                 lambda px, y, a=l, b=r: shade(px, y, a, b, top, ground, ramp))
        # the gunwale: a lighter strake along the sheer, so it is a container
        solid(cx - w / 2, top, cx + w / 2, top + 1, ramp[3])
        if prop["mast_h"]:
            solid(cx - 1, top - prop["mast_h"], cx + 1, top + 1,
                  PROP_RAMP["bark"][3])
            # A furled sail is a narrow bundle lashed to the mast. A fat oval
            # on a stick is a lollipop, not a boat.
            bundle_top = top - prop["mast_h"] + 3
            for py in range(int(bundle_top), int(bundle_top + 15)):
                u = (py - bundle_top) / 15
                half = 1.5 + 1.2 * (1 - abs(u - 0.45) * 1.5)
                rect(cx - half, py, cx + half, py,
                     lambda px, y, a=cx - half, b=cx + half: shade(
                         px, y, a, b, bundle_top, bundle_top + 15, ramp))
            for lash in range(3):
                ly = bundle_top + 3 + lash * 4
                rect(cx - 3, ly, cx + 3, ly + 1,
                     lambda px, py: PROP_DETAIL[0])

    # --- a cart: a real wheel, a bed, and a load that shows ---------------
    elif form == "cart":
        bark = PROP_RAMP[prop["mats"][0]]
        iron = PROP_RAMP[prop["mats"][1]]
        bw, bh = prop["bed_w"], prop["bed_h"]
        wd = prop["wheel_d"]
        wr = wd / 2
        bed_bottom = ground - wd + 3
        bed_top = bed_bottom - bh
        # The load sits on top of the bed, so it is measured down from its own
        # peak: 0 at the crest, 1 at the boards. Measuring from the bed floor
        # instead puts the crest at t>1 and the profile leaves the real line.
        load_top = bed_top - 7
        for py in range(int(load_top), int(bed_top - 1) + 1):
            t = (py - load_top) / max(1.0, (bed_top - 1) - load_top)
            half = (bw / 2 - 1) * max(0.0, 1 - (1 - t) ** 2) ** 0.45
            if half <= 0.5:
                continue
            l, r = cx - half, cx + half
            rect(l, py, r, py,
                 lambda px, y, a=l, b=r: shade(px, y, a, b, load_top,
                                               bed_top, PROP_RAMP["burlap"]))
        # the bed: two side planks and a floor
        solid(cx - bw / 2, bed_top, cx + bw / 2, bed_top + 2, bark[3])
        solid(cx - bw / 2, bed_bottom - 2, cx + bw / 2, bed_bottom, bark[1])
        for post in (-1, 1):
            solid(cx + post * (bw / 2) - 1, bed_top, cx + post * (bw / 2) + 1,
                  bed_bottom + 1, bark[2])
        outline([(cx - bw / 2, bed_top), (cx + bw / 2, bed_top),
                 (cx + bw / 2, bed_bottom), (cx - bw / 2, bed_bottom)])
        # wheels: an iron rim, four spokes and a hub
        for side in (-1, 1):
            wx = cx + side * (bw / 2 - 5)
            wy = ground - wr
            disc(wx, wy, wr, lambda px, py: iron[1])
            # the inside of the wheel is open, so the spokes read against the
            # tile behind. A fill here would close the wheel into a disc.
            disc(wx, wy, wr - 2, lambda px, py: None)
            import math
            for spoke in range(4):
                a = spoke * math.pi / 2 + 0.4
                for step in range(int((wr - 2) * 2)):
                    u = step / max(1, int((wr - 2) * 2) - 1)
                    put(round(wx + u * (wr - 2) * math.cos(a)),
                        round(wy + u * (wr - 2) * math.sin(a)), bark[2])
            disc(wx, wy, 2.5, lambda px, py: PROP_RAMP["iron"][3])
            disc(wx, wy, wr, lambda px, py, dx=wx, dy=wy, r0=wr: (
                iron[0] if ((px - dx) ** 2 + (py - dy) ** 2) ** 0.5 > r0 - 1.5
                else None))
        # the shaft, so the cart is something that was pulled
        solid(cx - bw / 2 - 7, bed_top + 1, cx - bw / 2, bed_top + 2, bark[2])

    # --- the goods that physically lie somewhere ---------------------------
    elif form == "rubble":
        # Field stone, and deliberately NOT a wall. Four stones, big enough to
        # have an interior: the first version scattered seven discs of radius
        # two, which is all outline, and the verifier was right to call it a
        # plate that is mostly its own edge.
        stone = PROP_RAMP["stone"]
        half = prop["width"] / 2
        stones = ((-0.66, 5.6), (-0.18, 6.8), (0.32, 5.0), (0.78, 4.0))
        for index, (fraction, radius) in enumerate(stones):
            sx = cx + half * fraction
            cy_ = ground - radius * 0.85
            disc(sx, cy_, radius,
                 lambda px, py, r=radius, s=sx, c=cy_: shade(
                     px, py, s - r, s + r, c - r, c + r, stone))
            # a lit face on the upper left, so no two stones read as one blob
            for dx, dy in ((-1, -1), (0, -1), (-1, 0)):
                put(round(sx + dx * radius * 0.4),
                    round(cy_ + dy * radius * 0.4), stone[3])
            if index:
                put(round(sx - radius * 0.7), round(cy_ + radius * 0.5),
                    stone[0])

    elif form == "billets":
        # Split firewood. Short and square-cut, because it sits in the same
        # catalogue as the long round `log_stack` and the two must never be
        # mistaken for one another at a glance.
        bark, end = PROP_RAMP["bark"], PROP_RAMP["endgrain"]
        half = prop["width"] / 2
        rows, bh = prop["rows"], prop["height"] / prop["rows"]
        for row in range(rows):
            y0 = int(ground - bh * (row + 1))
            span = half * (1 - 0.08 * row)
            for step in range(int(span * 2)):
                px = int(cx - span + step)
                # the cut face on top, the split face below it: two tones per
                # billet is the whole difference between wood and a striped box
                put(px, y0, end[3 if step % 5 == 0 else 2])
                solid(px, y0 + 1, px, y0 + max(1, int(bh) - 1),
                      bark[1 + (row % 2)])
            for px in range(int(cx - span), int(cx + span) + 1, 4):
                put(px, y0, end[1])

    elif form == "hayrick":
        # A rick: a cone of loose hay, widest at the base, with the holding
        # pole up the middle. A rick is loose, so the silhouette is uneven -
        # a smooth cone reads as a tent.
        hay = PROP_RAMP["hay"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.14 + 0.86 * u ** 0.8)
            for px in range(int(cx - span), int(cx + span) + 1):
                if abs(px - cx) > span - 1.2 and (px + py) % 3:
                    continue
                put(px, py, shade(px, py, cx - span, cx + span, top, ground, hay))
        solid(cx, top - 5, cx, ground - 1, PROP_RAMP["bark"][2])
        solid(cx - 2, top - 6, cx + 2, top - 5, PROP_RAMP["bark"][3])

    elif form == "sheaf":
        # A tied sheaf of straw, standing on its butt end - the shape it is
        # actually stored in, and the reason it is not a second smaller rick.
        hay = PROP_RAMP["hay"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.55 + 0.45 * u)
            rect(cx - span, py, cx + span, py,
                 lambda px, y, a=cx - span, b=cx + span: shade(
                     px, y, a, b, top, ground, hay))
        for tie in (0.34, 0.62):                  # two ties, so it is bound
            ty = top + prop["height"] * tie
            span = half * (0.55 + 0.45 * tie)
            solid(cx - span, ty, cx + span, ty, PROP_RAMP["bark"][2])
        for step in range(int(prop["height"] * 0.5)):   # loose tops
            put(round(cx - 1 + (step % 3) - 1), round(top - step), hay[3])

    elif form == "axe":
        # An axe standing on its head, haft up. Both parts are deliberately
        # fat: the first version drew a three-pixel haft twenty-eight pixels
        # long, which is a line, and a line is all contour.
        bark, iron = PROP_RAMP["bark"], PROP_RAMP["iron"]
        head_w = prop["head_w"]
        head_h = 12
        head_top = ground - head_h
        # the head, shaded across its own width
        for py in range(int(head_top), int(ground) + 1):
            u = (py - head_top) / max(1.0, head_h)
            # the bit flares out at the bottom, the poll is square
            half = head_w / 2 * (0.78 + 0.34 * u)
            rect(cx - half, py, cx + half, py,
                 lambda px, y, a=cx - half, b=cx + half: shade(
                     px, y, a, b, head_top, ground, iron))
        # the edge: a bright line down the bit, which is what makes it an axe
        for py in range(int(head_top + 2), int(ground) + 1):
            put(round(cx + head_w / 2 - 1), py, iron[3])
        # the poll, where the haft goes through
        for py in range(int(head_top), int(head_top + 3) + 1):
            solid(cx - 2, py, cx + 2, py, iron[0])
        # the haft, four pixels wide, with two bindings
        for py in range(int(head_top - prop["haft"]), int(head_top) + 1):
            solid(cx - 2, py, cx + 1, py, bark[2 if py % 3 else 1])
        for tie in (0.3, 0.62):
            ty = head_top - int(prop["haft"] * tie)
            solid(cx - 2, ty, cx + 1, ty, PROP_DETAIL[2] if "PROP_DETAIL" in dir() else (26, 20, 15))

    elif form == "plough":
        # An ard: a heavy beam, an upright stilt, and a mouldboard. Every part
        # is a solid mass with a real interior. The first version was a
        # diagonal line with two twigs on it: three tones, no interior, and the
        # verifier was right to refuse it twice.
        bark, iron = PROP_RAMP["bark"], PROP_RAMP["iron"]
        beam = prop["beam"]
        beam_y = ground - 7
        # the beam: six pixels thick, rising to the right, lit along its top
        for step in range(int(beam)):
            u = step / max(1.0, beam - 1)
            bx = cx - beam / 2 + step
            by = beam_y - u * 5
            for thick in range(8):
                tone = 3 if thick == 0 else (2 if thick < 4 else 1)
                put(round(bx), round(by) + thick, bark[tone])
            put(round(bx), round(by) + 7, bark[0])
        # the stilt and the handle, both solid uprights at the near end
        for step in range(int(20)):
            put(round(cx - beam / 2 + 5), round(beam_y - 3 - step),
                bark[2 if step % 4 else 1])
        for step in range(int(16)):
            put(round(cx - beam / 2 + 12), round(beam_y - 5 - step),
                bark[2])
        solid(cx - beam / 2 + 3, beam_y - 23, cx - beam / 2 + 6,
              beam_y - 22, bark[3])            # the grip
        # the mouldboard and share: a solid wedge of iron at the far end
        for step in range(11):
            u = step / 10.0
            sx = cx + beam / 2 - 6 + step
            thick = max(2, int(11 * (1 - u * 0.55)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(beam_y - 4 - row), iron[tone])
        solid(cx + beam / 2 + 4, beam_y - 6, cx + beam / 2 + 7,
              beam_y - 2, iron[2])

    elif form == "raft":
        # A lashed raft. The poles TOUCH: the first version left gaps between
        # them, and six separate blocks with gaps read as a fence, not a raft -
        # and each four-pixel column lost both edges to the contour, so the
        # plate was 62% its own outline.
        bark, hull = PROP_RAMP["bark"], PROP_RAMP["hull"]
        half = prop["width"] / 2
        poles = prop["poles"]
        pitch = (prop["width"] - 2) / poles
        top = ground - 8
        for index in range(poles):
            px = cx - half + 1 + index * pitch
            for py in range(int(top), int(ground) + 1):
                # each pole is `pitch` wide: lit, mid, shaded, dark
                for column in range(int(pitch)):
                    tone = 3 if column == 0 else (2 if column == 1 else 1)
                    put(round(px) + column, py, hull[tone])
        # the two lashings thrown over the deck
        for lash in (0.26, 0.72):
            lx = cx - half + prop["width"] * lash
            for py in range(int(top) - 1, int(ground) + 1):
                put(round(lx), py, bark[3])
                put(round(lx) + 1, py, bark[2])
            solid(round(lx) - 1, int(top) - 1, round(lx) + 2, int(top) - 1,
                  bark[1])
        # a pole shipped at one end, so it is a raft and not a stack
        for step in range(int(prop["width"] * 0.7)):
            put(round(cx + half - 2 + step), round(top - 2 - step * 0.2),
                bark[2 if step % 3 else 1])

    elif form == "share":
        # An iron ploughshare: a solid socket block and a broad blade. A one
        # pixel curve has no interior, so it is all contour and two tones.
        iron = PROP_RAMP["iron"]
        length = prop["length"]
        socket = 9
        top = ground - 16
        # the blade: a wedge, thick at the socket and tapering to a point
        for step in range(int(length)):
            u = step / max(1.0, length - 1)
            sx = cx - length / 2 + step
            thick = max(3, int(12 * (1 - u ** 1.3)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(ground - 2 - row), iron[tone])
        # the socket: a solid block the beam is fitted into
        for py in range(int(top), int(ground - 1) + 1):
            for px in range(int(cx - length / 2 - 2), int(cx - length / 2 + socket) + 1):
                edge = (px - (cx - length / 2 - 2)) / max(1.0, socket + 2.0)
                put(px, py, shade(px, py, cx - length / 2 - 2,
                                  cx - length / 2 + socket, top, ground, iron))
        solid(cx - length / 2 - 2, top, cx - length / 2 + socket, top, iron[3])
        for step in range(3):                       # the bolt hole
            put(round(cx - length / 2 + 2), round(top + 4 + step), iron[0])
        put(round(cx - length / 2 + 3), round(top + 5), iron[2])

    elif form == "boards":
        # Sawn boards stacked flat, each one showing its cut end. The ends are
        # what make a stack of boards a stack of boards.
        end, bark = PROP_RAMP["endgrain"], PROP_RAMP["bark"]
        bw, bh, count = prop["board_w"], prop["board_h"], prop["count"]
        for index in range(count):
            y0 = int(ground - bh * (index + 1))
            x0 = cx - bw / 2 + (index % 2) * 1.5
            solid(x0, y0, x0 + bw, y0, end[3 if index % 2 else 2])
            if index < count - 1:
                solid(x0, y0 + 1, x0 + bw, y0 + max(1, int(bh) - 1),
                      bark[1 + (index % 2)])
        for step in range(int(bw)):              # one end, so it reads sawn
            put(round(cx - bw / 2 + step), round(ground - 1),
                end[3] if step % 3 else end[1])

    elif form == "plough":
        # An ard: a heavy beam, an upright stilt, and a mouldboard. Every part
        # is a solid mass with a real interior. The first version was a
        # diagonal line with two twigs on it: three tones, no interior, and the
        # verifier was right to refuse it twice.
        bark, iron = PROP_RAMP["bark"], PROP_RAMP["iron"]
        beam = prop["beam"]
        beam_y = ground - 7
        # the beam: six pixels thick, rising to the right, lit along its top
        for step in range(int(beam)):
            u = step / max(1.0, beam - 1)
            bx = cx - beam / 2 + step
            by = beam_y - u * 5
            for thick in range(6):
                tone = 3 if thick == 0 else (2 if thick < 3 else 1)
                put(round(bx), round(by) + thick, bark[tone])
            put(round(bx), round(by) + 5, bark[0])
        # the stilt and the handle, both solid uprights at the near end
        for step in range(int(20)):
            put(round(cx - beam / 2 + 5), round(beam_y - 3 - step),
                bark[2 if step % 4 else 1])
        for step in range(int(16)):
            put(round(cx - beam / 2 + 12), round(beam_y - 5 - step),
                bark[2])
        solid(cx - beam / 2 + 3, beam_y - 23, cx - beam / 2 + 6,
              beam_y - 22, bark[3])            # the grip
        # the mouldboard and share: a solid wedge of iron at the far end
        for step in range(11):
            u = step / 10.0
            sx = cx + beam / 2 - 6 + step
            thick = max(2, int(11 * (1 - u * 0.55)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(beam_y - 4 - row), iron[tone])
        solid(cx + beam / 2 + 4, beam_y - 6, cx + beam / 2 + 7,
              beam_y - 2, iron[2])

    elif form == "raft":
        # A lashed raft. The poles TOUCH: the first version left gaps between
        # them, and six separate blocks with gaps read as a fence, not a raft -
        # and each four-pixel column lost both edges to the contour, so the
        # plate was 62% its own outline.
        bark, hull = PROP_RAMP["bark"], PROP_RAMP["hull"]
        half = prop["width"] / 2
        poles = prop["poles"]
        pitch = (prop["width"] - 2) / poles
        top = ground - 8
        for index in range(poles):
            px = cx - half + 1 + index * pitch
            for py in range(int(top), int(ground) + 1):
                # each pole is `pitch` wide: lit, mid, shaded, dark
                for column in range(int(pitch)):
                    tone = 3 if column == 0 else (2 if column == 1 else 1)
                    put(round(px) + column, py, hull[tone])
        # the two lashings thrown over the deck
        for lash in (0.26, 0.72):
            lx = cx - half + prop["width"] * lash
            for py in range(int(top) - 1, int(ground) + 1):
                put(round(lx), py, bark[3])
                put(round(lx) + 1, py, bark[2])
            solid(round(lx) - 1, int(top) - 1, round(lx) + 2, int(top) - 1,
                  bark[1])
        # a pole shipped at one end, so it is a raft and not a stack
        for step in range(int(prop["width"] * 0.7)):
            put(round(cx + half - 2 + step), round(top - 2 - step * 0.2),
                bark[2 if step % 3 else 1])

    elif form == "share":
        # An iron ploughshare: a solid socket block and a broad blade. A one
        # pixel curve has no interior, so it is all contour and two tones.
        iron = PROP_RAMP["iron"]
        length = prop["length"]
        socket = 9
        top = ground - 16
        # the blade: a wedge, thick at the socket and tapering to a point
        for step in range(int(length)):
            u = step / max(1.0, length - 1)
            sx = cx - length / 2 + step
            thick = max(3, int(12 * (1 - u ** 1.3)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(ground - 2 - row), iron[tone])
        # the socket: a solid block the beam is fitted into
        for py in range(int(top), int(ground - 1) + 1):
            for px in range(int(cx - length / 2 - 2), int(cx - length / 2 + socket) + 1):
                edge = (px - (cx - length / 2 - 2)) / max(1.0, socket + 2.0)
                put(px, py, shade(px, py, cx - length / 2 - 2,
                                  cx - length / 2 + socket, top, ground, iron))
        solid(cx - length / 2 - 2, top, cx - length / 2 + socket, top, iron[3])
        for step in range(3):                       # the bolt hole
            put(round(cx - length / 2 + 2), round(top + 4 + step), iron[0])
        put(round(cx - length / 2 + 3), round(top + 5), iron[2])

    elif form == "bolt":
        # A bolt of cloth: a roll, wound, with the loose end over the top. A
        # flat folded rectangle reads as a bale of wool, which is already
        # drawn and is a different good.
        wool = PROP_RAMP["wool"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.72 + 0.28 * u)
            rect(cx - span, py, cx + span, py,
                 lambda px, y, a=cx - span, b=cx + span: shade(
                     px, y, a, b, top, ground, wool))
        disc(cx - half * 0.5, top + prop["height"] * 0.45, prop["height"] * 0.30,
             lambda px, py: wool[1 if (px + py) % 4 else 3])
        for fold in range(prop["folds"]):        # the loose end over the top
            fy = top + 2 + fold * 3
            solid(cx - half * 0.2, fy, cx + half * 0.7, fy, wool[3 - fold % 2])

    elif form == "hide":
        # A hide stretched on a frame to cure, pegged at the corners. The frame
        # is the claim: a loose skin is a rumpled shape nobody can read.
        hide = PROP_RAMP["hide"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for px in range(int(cx - half), int(cx + half) + 1):     # the skin
            u = (px - (cx - half)) / max(1.0, prop["width"])
            edge = 2.0 * abs(u - 0.5)                              # belly dips
            for py in range(int(top + edge), int(ground) + 1):
                put(px, py, shade(px, py, cx - half, cx + half, top, ground, hide))
        for corner, (px, py) in enumerate((        # the four pegs
                ((cx - half, top), (cx + half, top),
                 (cx - half, ground), (cx + half, ground)))):
            put(round(px), round(py), PROP_RAMP["bark"][3 - corner % 2])
        solid(cx - half - 1, top, cx - half - 1, ground, PROP_RAMP["bark"][2])
        solid(cx + half + 1, top, cx + half + 1, ground, PROP_RAMP["bark"][2])

    elif form == "carcass":
        # Butchered meat on a block: two hanging pieces and the block. Drawn as
        # cut pieces rather than an animal, because the good is meat and the
        # animal already has a plate of its own.
        meat, bone = PROP_RAMP["meat"], PROP_RAMP["bone"]
        bx0, bx1 = cx - prop["width"] / 2, cx + prop["width"] / 2
        solid(bx0, ground - 5, bx1, ground, PROP_RAMP["bark"][1])   # the block
        solid(bx0, ground - 6, bx1, ground - 6, PROP_RAMP["bark"][3])
        for index, (mx, mh, mw) in enumerate(((-7, 13, 5), (6, 9, 4))):
            top = ground - 7 - mh
            for py in range(int(top), int(ground - 6) + 1):
                u = (py - top) / max(1.0, ground - 6 - top)
                span = mw * (0.72 + 0.28 * u)
                rect(cx + mx - span, py, cx + mx + span, py,
                     lambda px, y, a=cx + mx - span, b=cx + mx + span, t=top:
                         shade(px, y, a, b, t, ground - 6, meat))
            solid(cx + mx - 3, top, cx + mx + 3, top, bone[2 + index % 2])

    elif form == "churn":
        # A butter churn: a barrel with a lid and a dasher. A plain barrel is
        # a barrel, and barrels are not this good.
        bark, end = PROP_RAMP["bark"], PROP_RAMP["endgrain"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top + 4), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.72 + 0.28 * u)
            rect(cx - span, py, cx + span, py,
                 lambda px, y, a=cx - span, b=cx + span: shade(
                     px, y, a, b, top, ground, end))
            if py % 6 == 0:                       # the hoops
                solid(cx - span, py, cx + span, py, bark[2])
        solid(cx - half, top + 3, cx + half, top + 4, bark[3])     # the lid
        solid(cx - 1, top - 6, cx + 1, top + 3, bark[2])           # the dasher

    elif form == "bloom":
        # An iron bloom: the spongy mass a smith pounds out of the furnace.
        # Spongy is the claim, so the raggedness belongs on the OUTER two
        # pixels - the first version punched holes across the whole mass, and a
        # plate that is mostly holes is a plate that is mostly its own contour.
        iron, ash = PROP_RAMP["iron"], PROP_RAMP["ash"]
        half = prop["width"] / 2
        top = ground - prop["height"]
        for py in range(int(top), int(ground) + 1):
            u = (py - top) / max(1.0, ground - top)
            span = half * (0.55 + 0.45 * u)
            for px in range(int(cx - span), int(cx + span) + 1):
                edge = span - abs(px - cx)
                if edge < 2.5 and (px * 5 + py * 3) % 4 == 0:
                    continue
                put(px, py, shade(px, py, cx - span, cx + span, top, ground, iron))
        # the scale and slag on top, and the hearth ash at the foot
        for step in range(int(prop["width"] * 0.6)):
            put(round(cx - half * 0.55 + step), round(top + 2), ash[3])
        for step in range(int(half)):
            put(round(cx - half + step), round(ground), ash[1])
        for step in range(int(half * 0.7)):       # a couple of hot spots
            put(round(cx - half * 0.4 + step), round(top + 4), iron[3])

    elif form == "plough":
        # An ard: a heavy beam, an upright stilt, and a mouldboard. Every part
        # is a solid mass with a real interior. The first version was a
        # diagonal line with two twigs on it: three tones, no interior, and the
        # verifier was right to refuse it twice.
        bark, iron = PROP_RAMP["bark"], PROP_RAMP["iron"]
        beam = prop["beam"]
        beam_y = ground - 7
        # the beam: six pixels thick, rising to the right, lit along its top
        for step in range(int(beam)):
            u = step / max(1.0, beam - 1)
            bx = cx - beam / 2 + step
            by = beam_y - u * 5
            for thick in range(6):
                tone = 3 if thick == 0 else (2 if thick < 3 else 1)
                put(round(bx), round(by) + thick, bark[tone])
            put(round(bx), round(by) + 5, bark[0])
        # the stilt and the handle, both solid uprights at the near end
        for step in range(int(20)):
            put(round(cx - beam / 2 + 5), round(beam_y - 3 - step),
                bark[2 if step % 4 else 1])
        for step in range(int(16)):
            put(round(cx - beam / 2 + 12), round(beam_y - 5 - step),
                bark[2])
        solid(cx - beam / 2 + 3, beam_y - 23, cx - beam / 2 + 6,
              beam_y - 22, bark[3])            # the grip
        # the mouldboard and share: a solid wedge of iron at the far end
        for step in range(11):
            u = step / 10.0
            sx = cx + beam / 2 - 6 + step
            thick = max(2, int(11 * (1 - u * 0.55)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(beam_y - 4 - row), iron[tone])
        solid(cx + beam / 2 + 4, beam_y - 6, cx + beam / 2 + 7,
              beam_y - 2, iron[2])

    elif form == "raft":
        # A lashed raft. The poles TOUCH: the first version left gaps between
        # them, and six separate blocks with gaps read as a fence, not a raft -
        # and each four-pixel column lost both edges to the contour, so the
        # plate was 62% its own outline.
        bark, hull = PROP_RAMP["bark"], PROP_RAMP["hull"]
        half = prop["width"] / 2
        poles = prop["poles"]
        pitch = (prop["width"] - 2) / poles
        top = ground - 8
        for index in range(poles):
            px = cx - half + 1 + index * pitch
            for py in range(int(top), int(ground) + 1):
                # each pole is `pitch` wide: lit, mid, shaded, dark
                for column in range(int(pitch)):
                    tone = 3 if column == 0 else (2 if column == 1 else 1)
                    put(round(px) + column, py, hull[tone])
        # the two lashings thrown over the deck
        for lash in (0.26, 0.72):
            lx = cx - half + prop["width"] * lash
            for py in range(int(top) - 1, int(ground) + 1):
                put(round(lx), py, bark[3])
                put(round(lx) + 1, py, bark[2])
            solid(round(lx) - 1, int(top) - 1, round(lx) + 2, int(top) - 1,
                  bark[1])
        # a pole shipped at one end, so it is a raft and not a stack
        for step in range(int(prop["width"] * 0.7)):
            put(round(cx + half - 2 + step), round(top - 2 - step * 0.2),
                bark[2 if step % 3 else 1])

    elif form == "share":
        # An iron ploughshare: a solid socket block and a broad blade. A one
        # pixel curve has no interior, so it is all contour and two tones.
        iron = PROP_RAMP["iron"]
        length = prop["length"]
        socket = 9
        top = ground - 16
        # the blade: a wedge, thick at the socket and tapering to a point
        for step in range(int(length)):
            u = step / max(1.0, length - 1)
            sx = cx - length / 2 + step
            thick = max(3, int(12 * (1 - u ** 1.3)))
            for row in range(thick):
                tone = 3 if row == 0 else (1 if row < thick - 1 else 0)
                put(round(sx), round(ground - 2 - row), iron[tone])
        # the socket: a solid block the beam is fitted into
        for py in range(int(top), int(ground - 1) + 1):
            for px in range(int(cx - length / 2 - 2), int(cx - length / 2 + socket) + 1):
                edge = (px - (cx - length / 2 - 2)) / max(1.0, socket + 2.0)
                put(px, py, shade(px, py, cx - length / 2 - 2,
                                  cx - length / 2 + socket, top, ground, iron))
        solid(cx - length / 2 - 2, top, cx - length / 2 + socket, top, iron[3])
        for step in range(3):                       # the bolt hole
            put(round(cx - length / 2 + 2), round(top + 4 + step), iron[0])
        put(round(cx - length / 2 + 3), round(top + 5), iron[2])

    # --- a tool board: a thin board with tools in front of it -------------
    # --- a tool board: a thin board with tools in front of it -------------
    elif form == "tool_board":
        iron = PROP_RAMP[prop["mats"][0]]
        bw, bh = prop["board_w"], prop["board_h"]
        x0, y0 = cx - bw / 2, ground - bh
        solid(x0, y0, x0 + bw, ground, PROP_RAMP["bark"][0])
        for py in range(int(y0), int(ground) + 1):
            if grain(0, py, 11):
                solid(x0, py, x0 + bw, py, PROP_RAMP["bark"][1])
        outline([(x0, y0), (x0 + bw, y0), (x0 + bw, ground), (x0, ground)])
        # The three tools are spaced wider than any head is wide. At a tighter
        # pitch the heads touch and the board reads as a crate with one grey
        # band across it, which is what a tool rack must never look like.
        pitch = (bw - 6) / prop["tools"]
        for tool in range(prop["tools"]):
            tx = x0 + 3 + pitch * (tool + 0.5)
            handle_top = y0 + 9
            handle_bottom = ground - 2
            for py in range(int(handle_top), int(handle_bottom) + 1):
                u = (py - handle_top) / max(1, handle_bottom - handle_top)
                half = 1.7 - 0.7 * u
                rect(tx - half, py, tx + half, py,
                     lambda px, y: PROP_RAMP["bark"][2])
            # a ferrule, so the handle reads as an object and not a stripe
            solid(tx - 2, handle_bottom - 3, tx + 2, handle_bottom,
                  iron[1])
            if tool == 0:                      # an axe: a bit on one side
                solid(tx - 1, y0 + 4, tx + 2, handle_top, iron[1])
                solid(tx + 1, y0 + 2, tx + 4, y0 + 6, iron[3])
            elif tool == 1:                    # a hammer: a face on one side
                solid(tx - 1, y0 + 6, tx + 2, handle_top, iron[1])
                solid(tx - 3, y0 + 3, tx + 2, y0 + 7, iron[3])
            else:                              # a pick: two tines, splayed
                solid(tx - 1, y0 + 5, tx + 2, handle_top, iron[1])
                solid(tx - 4, y0 + 2, tx - 1, y0 + 4, iron[3])
                solid(tx + 2, y0 + 2, tx + 4, y0 + 4, iron[3])

    path = PLATES / f"{prop_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def actor_landmarks(actor, cx, ground):
    """The body landmarks a garment has to land on.

    One function, used by both the body and every clothing layer. When the
    garment computed its own shoulder line it would be one constant away from
    the figure's, and every layer would sit slightly wrong in a way no pixel
    check would catch: the plate is still self-consistent, it is just on the
    wrong body. Sharing the arithmetic is the only thing that keeps an overlay
    an overlay.
    """
    H = actor["height"]
    head_h = H * actor["head_frac"]
    head_r = head_h * 0.46
    return {
        "H": H,
        "ground": ground,
        "head_h": head_h,
        "head_r": head_r,
        "head_cy": ground - H + head_r,
        "head_cx": cx + actor["stoop"],
        "neck_y": ground - H + head_h + head_r * 0.8,
        "shoulder_y": ground - H + head_h,
        "shoulder_w": H * actor["shoulder_frac"],
        "waist_y": ground - H * 0.60,
        "hip_y": ground - H * 0.48,
        "hip_w": H * actor["hip_frac"],
        "leg_w": H * actor["leg_frac"],
        "arm_w": H * actor["arm_frac"],
        "leg_cx": cx,
        "tunic_len": H * 0.62,      # a tunic falls to just below the knee
        "hose_len": H * 0.46,
    }


def draw_actor(actor_id, actor):
    """Compose one standing figure in strict front elevation, on the ground line.

    Every part is shaded against its own bounds. Shading the whole silhouette as
    one form is what makes a drawn person read as a blob: the arm merges into the
    torso because both take a tone from the same gradient.
    """
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)
    skin = ACTOR_RAMP["skin"]
    cloth = ACTOR_RAMP["cloth"]
    leather = ACTOR_RAMP["leather"]

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def disc(ccx, ccy, radius, colour_fn, squash=1.0):
        for py in range(int(ccy - radius * squash), int(ccy + radius * squash) + 1):
            dy = (py - ccy) / max(0.001, radius * squash)
            if abs(dy) > 1:
                continue
            half = radius * (1 - dy * dy) ** 0.5
            for px in range(int(round(ccx - half)), int(round(ccx + half)) + 1):
                put(px, py, colour_fn(px, py))

    def shade(px, py, left, right, top, bottom, ramp):
        span = max(1.0, (right - left) / 2)
        edge = min(1.0, max(0.0, min(px - left, right - px) / span))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return ramp[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def taper(x_centre, y_top, y_bottom, w_top, w_bottom, ramp):
        """A limb: a cylinder that narrows, shaded against its own width."""
        for py in range(int(y_top), int(y_bottom) + 1):
            u = (py - y_top) / max(1.0, y_bottom - y_top)
            half = (w_top + (w_bottom - w_top) * u) / 2
            if half <= 0.2:
                continue
            rect(x_centre - half, py, x_centre + half, py,
                 lambda px, y, a=x_centre - half, b=x_centre + half: shade(
                     px, y, a, b, y_top, y_bottom, ramp))

    M = actor_landmarks(actor, cx, ground)
    H, head_h, head_r = M["H"], M["head_h"], M["head_r"]
    shoulder_w, hip_w = M["shoulder_w"], M["hip_w"]
    leg_w, arm_w = M["leg_w"], M["arm_w"]
    head_cy, head_cx = M["head_cy"], M["head_cx"]
    neck_y, shoulder_y = M["neck_y"], M["shoulder_y"]
    waist_y, hip_y = M["waist_y"], M["hip_y"]

    # --- legs, then boots -------------------------------------------------
    for side in (-1, 1):
        lx = cx + side * (hip_w / 2 - leg_w / 2)
        taper(lx, hip_y, ground - 2, leg_w, leg_w * 0.82, cloth)
        solid(lx - leg_w * 0.5, ground - 2, lx + leg_w * 0.5, ground,
              ACTOR_DETAIL[2])

    # --- torso: shoulders down to the hip, narrowing at the waist ---------
    for py in range(int(shoulder_y), int(hip_y) + 1):
        u = (py - shoulder_y) / max(1.0, hip_y - shoulder_y)
        # shoulders are the widest point, the waist pulls in, the hip flares
        half = (shoulder_w / 2) * (1 - 0.30 * u + 0.22 * max(0.0, u - 0.62) ** 2 * 8)
        if half <= 0.2:
            continue
        rect(cx - half, py, cx + half, py,
             lambda px, y, a=cx - half, b=cx + half: shade(
                 px, y, a, b, shoulder_y, hip_y, cloth))
    # a belt, so the hip line is a garment and not a shadow
    solid(cx - hip_w / 2, waist_y, cx + hip_w / 2, waist_y + 2, leather[1])

    # --- arms, clear of the torso ----------------------------------------
    # An arm drawn against the torso edge merges into it: both take a tone from
    # the same gradient and the silhouette has no notch, so the figure reads as
    # one block. The arm therefore hangs a pixel outside the torso, joined only
    # at the shoulder by a short cap.
    for side in (-1, 1):
        ax = cx + side * (shoulder_w / 2 + arm_w * 0.62)
        solid(min(cx + side * shoulder_w / 2, ax), shoulder_y,
              max(cx + side * shoulder_w / 2, ax), shoulder_y + 2, cloth[2])
        taper(ax, shoulder_y + 2, hip_y + H * 0.16, arm_w, arm_w * 0.78, cloth)
        # a hand at the end of the sleeve
        disc(ax, hip_y + H * 0.17, arm_w * 0.6,
             lambda px, y, dx=ax, dy=hip_y + H * 0.17: skin[
                 min(3, int(abs(px - dx) * 0.9 + abs(y - dy) * 0.4) % 4)])

    # --- the staff an elder leans on -------------------------------------
    if actor["staff"]:
        sx = cx + shoulder_w / 2 + arm_w * 2.4
        solid(sx - 1, ground - H * 0.92, sx + 1, ground, leather[2])
        # a crook, so it reads as a walking staff and not as a post
        solid(sx - 3, ground - H * 0.92, sx + 3, ground - H * 0.92 + 1,
              leather[3])

    # --- head, hair and the eye line -------------------------------------
    disc(head_cx, head_cy, head_r,
         lambda px, y, dx=head_cx, dy=head_cy: skin[min(3, int(
             (abs(px - dx) / max(1.0, head_r) * 1.5
              + abs(y - dy) / max(1.0, head_r) * 0.9) * 2.2) % 4)])
    # Hair follows the skull. A full-width band across the top of the head is a
    # visor, not hair: it has to narrow with the crown and stop above the brow.
    hair_bottom = head_cy - head_r * 0.10
    for py in range(int(head_cy - head_r * 1.04), int(hair_bottom) + 1):
        dy = (py - head_cy) / head_r
        half = head_r * max(0.0, 1 - dy * dy) ** 0.5 * 1.06
        if half <= 0.3:
            continue
        rect(head_cx - half, py, head_cx + half, py,
             lambda px, y: ACTOR_DETAIL[
                 0 if y < head_cy - head_r * 0.45 else 1])
    # No eye line. The head is six to eight pixels across: a dark band there is
    # a blindfold, and a face at this size is not readable anyway.
    # the neck
    # the ramp, not a single tone: taper() indexes into whatever it is given,
    # so handing it one colour returns a number and the pixel write explodes
    taper(head_cx, neck_y - 1, shoulder_y + 1, head_r * 0.8, head_r * 0.95, skin)

    path = PLATES / f"{actor_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def draw_animal(animal_id, animal):
    """Compose one animal in orthographic side elevation, on the ground line.

    Everything is measured up from the ground line, and the proportions are set
    against the adult figure: an ox withers stands a little under a person, a
    pig at about half his height, a hen at his knee. A draft animal taller than
    the villager standing next to it would be a scale error, not a big animal.
    """
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)
    ramp = ANIMAL_RAMP[animal["mats"][0]]
    dark = ANIMAL_RAMP[animal["mats"][1]]

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def shade(px, py, left, right, top, bottom, tones):
        # Light from the upper left, and ONE side of it. The symmetric version
        # measured the distance to the NEAREST vertical edge, which put the
        # highlight at the top centre and painted every building with the same
        # bright inverted V - the same defect I had already fixed in the garment
        # torso. A one-sided falloff reads as a lit face and a shadowed one.
        edge = 1.0 - max(0.0, min(1.0, (px - left) / max(1.0, right - left)))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return tones[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def oval(ccx, ccy, rx, ry, tones):
        """A trunk. An animal's barrel is an oval, and an oval is unambiguous.

        The first attempt used a lens whose inset was reassigned three times
        with two of the formulas dead, and it narrowed to a point at the belly:
        every animal came out a paper cone. One closed form, no dead code.
        """
        for py in range(int(ccy - ry), int(ccy + ry) + 1):
            dy = (py - ccy) / max(0.001, ry)
            if abs(dy) > 1:
                continue
            half = rx * (1 - dy * dy) ** 0.5
            a, b = ccx - half, ccx + half
            rect(a, py, b, py,
                 lambda px, y, aa=a, bb=b: shade(px, y, aa, bb,
                                                 ccy - ry, ccy + ry, tones))

    def capsule(x0, y0, x1, y1, r0, r1, tones):
        """A limb or a neck: a tapered stroke between two centres."""
        length = math.hypot(x1 - x0, y1 - y0)
        steps = int(length) + 1
        for step in range(steps + 1):
            u = step / max(1, steps)
            px = x0 + (x1 - x0) * u
            py = y0 + (y1 - y0) * u
            r = r0 + (r1 - r0) * u
            for dy in range(int(-r), int(r) + 1):
                for dx in range(int(-r), int(r) + 1):
                    if dx * dx + dy * dy <= r * r:
                        put(round(px + dx), round(py + dy),
                            shade(px + dx, py + dy, px - r, px + r, y0, y1, tones))

    form = animal["form"]
    body_len = animal["body_len"]
    body_h = animal["body_h"]
    rear = cx - body_len / 2
    front = cx + body_len / 2
    leg_len = animal["leg_len"]
    withers = ground - leg_len - body_h
    belly = ground - leg_len

    if form == "quadruped":
        leg_r = max(1.8, body_h * 0.26)
        # --- far pair first, a tone down and set in from the near pair so they
        # are not swallowed by the trunk
        for frac in (0.40, 0.66):
            lx = rear + body_len * frac
            capsule(lx, belly - 2, lx, ground - 1, leg_r * 0.9, leg_r * 0.7, dark)
        # --- tail, from the top of the rump
        tail_root = (rear + 1, withers + 1)
        if animal["tail"] == "full":
            # hair to the hock, tapering to a point: a constant-width strip
            # reads as a plank nailed to the rump
            tail_end = tail_root[1] + body_h * 1.45
            capsule(tail_root[0], tail_root[1], tail_root[0] - 2, tail_end,
                    body_h * 0.22, body_h * 0.03, dark)
        elif animal["tail"] == "curl":
            for step in range(11):
                a = step / 10 * math.tau
                put(round(tail_root[0] - 1 + 2.6 * math.cos(a)),
                    round(tail_root[1] - 1 + 2.6 * math.sin(a)), ANIMAL_DETAIL[1])
        elif animal["tail"] == "short_up":
            capsule(tail_root[0], tail_root[1], tail_root[0] - 2,
                    tail_root[1] - 5, 1.1, 0.7, dark)
        elif animal["tail"] == "tuft":
            capsule(tail_root[0], tail_root[1], tail_root[0] - 1,
                    tail_root[1] + 5, 0.9, 0.6, dark)
        elif animal["tail"] == "puff":
            # a hare's scut: a small LIGHT disc. Dark, and it reads as a hole
            # punched in the rump.
            puff = max(1.4, body_h * 0.22)
            oval(tail_root[0] - 1, tail_root[1] + 1, puff, puff, ramp)
        elif animal["tail"] == "bush":
            # a squirrel's tail is the animal: bigger than the body and carried
            # up over the back, so an arc of shrinking discs, not a strip
            for step in range(7):
                u = step / 6.0
                fur = max(1.2, body_h * (0.72 - 0.38 * u))
                oval(tail_root[0] - 1 - 2.4 * u, tail_root[1] - 1 - 5.2 * u,
                     fur, fur, dark)
        else:
            capsule(tail_root[0], tail_root[1], tail_root[0] - 1,
                    belly - 1, 0.9, 0.6, dark)
        # --- trunk
        oval((rear + front) / 2, (withers + belly) / 2, body_len / 2,
             body_h / 2, ramp)
        if animal["coat"] == "fleece":
            # a fleece breaks the silhouette: scallop the back and the belly
            for step in range(0, int(body_len), 4):
                bump = 2.4 if (step // 4) % 2 else 1.0
                px = rear + body_len * 0.1 + step
                solid(px, withers - 1 - bump, px + 3, withers + 2, ramp[3])
                solid(px, belly - 2, px + 3, belly + 1 + bump * 0.7, ramp[1])
        # --- near pair, in full tone, drawn over the trunk
        for frac in (0.20, 0.84):
            lx = rear + body_len * frac
            capsule(lx, belly - 1, lx, ground, leg_r, leg_r * 0.75, ramp)
            solid(lx - leg_r * 0.9, ground - 1, lx + leg_r * 0.9, ground,
                  ANIMAL_RAMP["bone"][0])
        # --- neck: from the withers forward and up
        nl = animal["neck_len"]
        rise = nl * (0.85 if nl >= 10 else 0.72)
        neck_root = (front - body_len * 0.10, withers + body_h * 0.22)
        head_base = (neck_root[0] + nl * 0.55, neck_root[1] - rise)
        capsule(neck_root[0], neck_root[1], head_base[0], head_base[1],
                body_h * 0.30, body_h * 0.20, ramp)
        if animal["coat"] == "bristle":
            # a boar's ridge is the one thing that says boar and not pig: a
            # raised dark crest from the withers to the rump
            for step in range(int(body_len * 0.66)):
                u = step / max(1.0, body_len * 0.66)
                bx = rear + step
                by = withers - 1 - 3.4 * math.sin(u * math.pi)
                put(round(bx), round(by), ANIMAL_DETAIL[1])
                put(round(bx), round(by) - 1, dark[0])
        if animal["coat"] == "hump":
            # the withers hump is what separates a draught ox from a horse at a
            # glance, and it costs three pixels
            capsule(neck_root[0] - body_len * 0.16, withers + 1,
                    neck_root[0] - body_len * 0.02, withers - 2,
                    body_h * 0.24, body_h * 0.18, ramp)
        if animal["coat"] == "mane":
            # a crest along the top of the neck
            steps = int(math.hypot(head_base[0] - neck_root[0],
                                   head_base[1] - neck_root[1]))
            for step in range(steps + 1):
                u = step / max(1, steps)
                mx = neck_root[0] + (head_base[0] - neck_root[0]) * u
                my = neck_root[1] + (head_base[1] - neck_root[1]) * u
                r = body_h * (0.20 - 0.05 * u)
                for dy in range(int(-r), 0):
                    for dx in range(int(-r), int(r) + 1):
                        if dx * dx + dy * dy <= r * r:
                            put(round(mx + dx), round(my + dy), dark[2])
        # --- head: a long taper from the poll to the muzzle
        hl, hh = animal["head_len"], animal["head_h"]
        head_tip = (head_base[0] + hl * 0.80, head_base[1] + hl * 0.42)
        capsule(head_base[0], head_base[1], head_tip[0], head_tip[1],
                hh * 0.5, hh * 0.26, ramp)
        # the muzzle end, squared off, so the head has a front and not a point
        solid(head_tip[0] - hh * 0.3, head_tip[1] - hh * 0.26,
              head_tip[0] + hh * 0.2, head_tip[1] + hh * 0.26, ramp[2])
        put(round(head_base[0] + hl * 0.26), round(head_base[1] - hh * 0.12),
            ANIMAL_DETAIL[2])
        # --- ears, on the poll, pointing up and out
        if animal["ears"] == "very_long":
            # a hare is its ears, and they stand straight up rather than out
            ear_len = body_h * 1.5
            ear_r = max(1.5, body_h * 0.19)
            for side in (-1, 1):
                capsule(head_base[0] + side * hh * 0.22,
                        head_base[1] - hh * 0.20,
                        head_base[0] + side * ear_len * 0.10,
                        head_base[1] - hh * 0.20 - ear_len, ear_r, ear_r * 0.45,
                        dark)
        elif animal["ears"] == "long":
            # a donkey is its ears: broad, upright and rounded, close together
            ear_len = body_h * 0.95
            ear_r = max(1.6, body_h * 0.20)
            for side in (-1, 1):
                capsule(head_base[0] + side * hh * 0.30, head_base[1] - hh * 0.10,
                        head_base[0] + side * ear_len * 0.20,
                        head_base[1] - hh * 0.10 - ear_len, ear_r, ear_r * 0.55,
                        dark)
        else:
            ear_len = body_h * 0.34
            ear_r = max(1.1, body_h * 0.13)
            for side in (-1, 1):
                capsule(head_base[0] + side * hh * 0.40, head_base[1] - hh * 0.15,
                        head_base[0] + side * ear_len * 0.55,
                        head_base[1] - hh * 0.15 - ear_len, ear_r, ear_r * 0.5,
                        dark)
        # --- horns, off the poll
        if animal["horns"] == "wide":
            horn_r = max(1.0, body_h * 0.10)
            for side in (-1, 1):
                root = (head_base[0] + side * hh * 0.30,
                        head_base[1] - hh * 0.25)
                for index in range(6):
                    u0, u1 = index / 6, (index + 1) / 6
                    capsule(root[0] + side * u0 * body_h * 0.62,
                            root[1] - math.sin(u0 * 1.6) * body_h * 0.42,
                            root[0] + side * u1 * body_h * 0.62,
                            root[1] - math.sin(u1 * 1.6) * body_h * 0.42,
                            horn_r, horn_r * 0.8, ANIMAL_RAMP["bone"])
        elif animal["horns"] == "antlers":
            # A deer's antlers are a fork: a beam up and back, then tines
            # forward. Drawn like another pair of wide horns, a deer looks
            # like an ox.
            bone = ANIMAL_RAMP["bone"]
            beam_r = max(0.9, body_h * 0.075)
            for side in (-1, 1):
                root = (head_base[0] + side * hh * 0.26,
                        head_base[1] - hh * 0.30)
                tip = (root[0] + side * body_h * 0.34,
                       root[1] - body_h * 0.92)
                capsule(root[0], root[1], tip[0], tip[1], beam_r, beam_r * 0.5,
                        bone)
                for tine in (0.34, 0.62, 0.84):
                    bx = root[0] + side * body_h * 0.34 * tine
                    by = root[1] - body_h * 0.92 * tine
                    capsule(bx, by, bx + side * body_h * 0.30,
                            by - body_h * 0.20, beam_r * 0.8, beam_r * 0.4, bone)
        elif animal["horns"] == "tusks":
            # a boar's tusks come off the muzzle and curve up, so they are drawn
            # at the head tip and not on the poll
            bone = ANIMAL_RAMP["bone"]
            for side in (-1, 1):
                root = (head_tip[0] + side * hh * 0.18, head_tip[1] - hh * 0.10)
                capsule(root[0], root[1], root[0] + side * body_h * 0.14,
                        root[1] - body_h * 0.30, max(0.9, body_h * 0.07),
                        max(0.5, body_h * 0.035), bone)
        elif animal["horns"] == "back":
            horn_r = max(1.0, body_h * 0.09)
            for side in (-1, 1):
                for index in range(4):
                    u0, u1 = index / 4, (index + 1) / 4
                    capsule(head_base[0] + side * u0 * body_h * 0.34,
                            head_base[1] - hh * 0.30 - u0 * body_h * 0.38,
                            head_base[0] + side * u1 * body_h * 0.34,
                            head_base[1] - hh * 0.30 - u1 * body_h * 0.38,
                            horn_r, horn_r * 0.7, ANIMAL_RAMP["bone"])

    else:  # bird
        bl, bh = animal["body_len"], animal["body_h"]
        leg_len = animal["leg_len"]
        body_top = ground - leg_len - bh
        leg_r = 1.1
        # --- far leg
        capsule(cx + bl * 0.02, body_top + bh * 0.55, cx + bl * 0.02, ground,
                leg_r * 0.8, leg_r * 0.7, dark)
        # --- tail, behind the body
        if animal["tail"] == "fan":
            for step in range(int(bl * 0.5)):
                u = step / max(1, bl * 0.5)
                half = bl * 0.34 * (1 - u * 0.7)
                solid(cx - bl * 0.32 - step, body_top + bh * 0.2 - half,
                      cx - bl * 0.32 - step + 2, body_top + bh * 0.2 + half,
                      dark[1 + (step % 2)])
        else:
            for step in range(int(bl * 0.3)):
                solid(cx - bl * 0.32 - step, body_top + bh * 0.1,
                      cx - bl * 0.32 - step + 2,
                      body_top + bh * 0.1 - bh * 0.3 * (step / max(1, bl * 0.3)),
                      dark[2])
        # --- body
        oval(cx, body_top + bh / 2, bl / 2, bh / 2, ramp)
        # --- near leg, with a foot
        capsule(cx + bl * 0.12, body_top + bh * 0.55, cx + bl * 0.12, ground,
                leg_r, leg_r * 0.8, ramp)
        solid(cx + bl * 0.12 - 2, ground - 1, cx + bl * 0.12 + 2, ground,
              ANIMAL_RAMP["bone"][1])
        # --- neck and head
        nl = animal["neck_len"]
        hx = cx + bl * 0.26
        hy = body_top - nl * 0.5
        if nl > 6:      # a long-necked bird gets a real neck, not a stub
            capsule(cx + bl * 0.16, body_top + 2, hx, hy, 2.6, 2.0, ramp)
        else:
            capsule(cx + bl * 0.16, body_top + 2, hx, hy, 3.0, 2.0, ramp)
        capsule(hx, hy, hx + 3.4, hy - 1.4, 3.0, 2.6, ramp)
        put(round(hx + 1), round(hy - 1.4), ANIMAL_DETAIL[2])
        # --- beak
        for step in range(int(animal["beak_len"]) + 1):
            solid(hx + 3, hy - 2, hx + 4 + step, hy, ANIMAL_RAMP["bone"][3])
        if animal["crest"] == "comb":
            for step in range(4):
                solid(hx - 1 + step, hy - 5, hx + step, hy - 3,
                      ANIMAL_DETAIL[0])
        if animal["tail"] == "fan":
            solid(cx - bl * 0.10, body_top + bh * 0.2, cx + bl * 0.10,
                  body_top + bh * 0.2 + 2, ANIMAL_DETAIL[0])

    path = PLATES / f"{animal_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def draw_garment(garment_id, garment, age_class="adult"):
    """Draw one clothing layer on the body landmarks, not a second body.

    The plate is transparent everywhere the garment is not, so compositing it
    over the matching `age_class` figure is the whole point: the layer has to
    share the figure's geometry or it is not an overlay, it is a picture of
    somebody else. Anchors come from `actor_landmarks`, the same function the
    body uses.
    """
    width = CELL
    height = CELL
    cx = HEX_CENTRE[0]
    ground = float(GROUND_Y)
    data = bytearray(width * height * 4)
    M = actor_landmarks(ACTOR[f"actor_{age_class}"], cx, ground)
    mats = garment["mats"]
    ramp = GARMENT_RAMP[mats[0]] if mats else GARMENT_RAMP["undyed"]

    def put(px, py, colour):
        if colour is None:
            return
        if 0 <= px < width and 0 <= py < height:
            i = (int(py) * width + int(px)) * 4
            data[i:i + 4] = bytes((*colour, 255))

    def rect(x0, y0, x1, y1, colour_fn):
        for py in range(int(y0), int(y1) + 1):
            for px in range(int(x0), int(x1) + 1):
                put(px, py, colour_fn(px, py))

    def solid(x0, y0, x1, y1, colour):
        rect(x0, y0, x1, y1, lambda px, py: colour)

    def shade(px, py, left, right, top, bottom, tones):
        # Light from the upper left, and ONE side of it. The symmetric version
        # measured the distance to the NEAREST vertical edge, which put the
        # highlight at the top centre and painted every building with the same
        # bright inverted V - the same defect I had already fixed in the garment
        # torso. A one-sided falloff reads as a lit face and a shadowed one.
        edge = 1.0 - max(0.0, min(1.0, (px - left) / max(1.0, right - left)))
        lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, bottom - top)))
        return tones[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

    def disc(ccx, ccy, radius, colour_fn):
        for py in range(int(ccy - radius), int(ccy + radius) + 1):
            dy = (py - ccy) / max(0.001, radius)
            if abs(dy) > 1:
                continue
            half = radius * (1 - dy * dy) ** 0.5
            for px in range(int(round(ccx - half)), int(round(ccx + half)) + 1):
                put(px, py, colour_fn(px, py))

    form = garment["form"]
    shoulder_y, hip_y = M["shoulder_y"], M["hip_y"]
    waist_y = M["waist_y"]
    shoulder_w, hip_w = M["shoulder_w"], M["hip_w"]
    leg_w, arm_w = M["leg_w"], M["arm_w"]
    tunic_len = M["tunic_len"] * GARMENT_LENGTH.get(mats[0], 1.0)
    head_cy, head_r, head_cx = M["head_cy"], M["head_r"], M["head_cx"]

    if form in ("tunic_body", "armour_byrnie", "outer_cloak", "outer_apron"):
        def limb_cover(px, py, top, hem, ramp_, left, right, full=True):
            """Shade a covering against its own row, not the body's."""
            span = max(1.0, (right - left) / 2)
            edge = min(1.0, max(0.0, min(px - left, right - px) / span))
            lit = 1.0 - max(0.0, min(1.0, (py - top) / max(1.0, hem - top)))
            return ramp_[min(3, max(0, int((0.55 * edge + 0.45 * lit) * 3.999)))]

        def trunk_half(py, hem, flare):
            """Half-width of a torso covering on row `py`.

            This was a cone: widest at the shoulders, 22% narrower at the hem.
            That is a bell, and it is why every layer read as a sack. Clothing
            is shaped the other way round. A belt pulls it in at the waist and
            then the skirt falls away from the body, so the profile narrows from
            shoulder to waist and widens again to the hem. `flare` is how far
            past the waist it opens: a byrnie is a straight tube, a cloak flies.
            """
            sh_half = (shoulder_w / 2) * 0.94
            waist_half = min(shoulder_w, hip_w) / 2 * 0.76
            if py <= waist_y:
                u = (py - shoulder_y) / max(1.0, waist_y - shoulder_y)
                return sh_half + (waist_half - sh_half) * min(1.0, max(0.0, u))
            u = (py - waist_y) / max(1.0, hem - waist_y)
            return waist_half + (waist_half * flare - waist_half) * min(1.0, u)

        # --- the neckline is the single thing that makes a torso covering read
        #     as a garment. A rectangle with a hem is a slab; a rectangle with a
        #     hole at the throat is a tunic.
        neck_opening = head_r * 0.62

        if form == "outer_apron":
            bib_bottom = waist_y - 1
            hem = hip_y + tunic_len * 0.30
            bib_top = waist_y - tunic_len * 0.40
            for py in range(int(bib_top), int(waist_y) + 1):
                half = shoulder_w * 0.17
                rect(cx - half, py, cx + half, py,
                     lambda px, y, aa=cx - half, bb=cx + half: limb_cover(
                         px, y, bib_top, waist_y, ramp, aa, bb))
            for py in range(int(bib_bottom), int(hem) + 1):
                u = (py - bib_bottom) / max(1.0, hem - bib_bottom)
                half = shoulder_w * (0.17 + 0.17 * min(1.0, u))
                rect(cx - half, py, cx + half, py,
                     lambda px, y, aa=cx - half, bb=cx + half: limb_cover(
                         px, y, bib_bottom, hem, ramp, aa, bb))
            # the waist tie, which is what makes bib and skirt one garment
            solid(cx - shoulder_w * 0.16, waist_y, cx + shoulder_w * 0.16,
                  waist_y + 1, GARMENT_DETAIL[0])
            top = bib_top

        else:
            if form == "outer_cloak":
                # A cloak is a mantle over the shoulders with an opening down
                # the front, so the tunic underneath stays visible. Painting it
                # as a full-length covering turned the figure into a dress and
                # hid the very layer it is supposed to sit on.
                top, hem = shoulder_y - 2, hip_y + tunic_len * 0.18
            elif form == "armour_byrnie":
                # a byrnie ends at the hip; mail past that is a skirt
                top, hem = shoulder_y - 1, hip_y + 2
            else:
                # A work tunic is short and a fine one is long, and that is a
                # wealth signal in itself: the poor wear what they can work in.
                top, hem = (shoulder_y - 1, shoulder_y + tunic_len * 0.92)
            # a byrnie is a tube, a tunic and a cloak open away from the waist
            flare = {"armour_byrnie": 1.04, "outer_cloak": 1.62}.get(form, 1.50)
            # how much of the half-width the cloak leaves open down the front.
            # It was 0.0, which made the wedge below collapse to nothing and
            # turned the cloak into a closed dress.
            opening = 0.40 if form == "outer_cloak" else 0.0
            for py in range(int(top), int(hem) + 1):
                half = trunk_half(py, hem, flare)
                if half <= 0.4:
                    continue
                # the neckline: the top of the garment dips away from the throat
                if py < shoulder_y + neck_opening + 1:
                    cut = neck_opening * (1 - (py - top) /
                                          max(1.0, neck_opening + 1))
                    a, b = cx - half + cut, cx + half - cut
                else:
                    a, b = cx - half, cx + half
                if b - a <= 0.4:
                    continue
                if form == "outer_cloak" and py > shoulder_y + 3:
                    # a front opening: the cloak fastens at the throat and the
                    # gap widens the whole way down, so the tunic underneath
                    # shows as a wedge rather than a slit
                    gap = half * opening * min(
                        1.0, (py - shoulder_y) / max(1.0, hem - shoulder_y))
                    left_end, right_start = cx - gap, cx + gap
                    if right_start - left_end > 1.0:
                        rect(a, py, left_end, py,
                             lambda px, y, aa=a, bb=left_end: limb_cover(
                                 px, y, top, hem, ramp, aa, bb))
                        rect(right_start, py, b, py,
                             lambda px, y, aa=right_start, bb=b: limb_cover(
                                 px, y, top, hem, ramp, aa, bb))
                        continue
                rect(a, py, b, py,
                     lambda px, y, aa=a, bb=b: limb_cover(px, y, top, hem,
                                                          ramp, aa, bb))

        # --- sleeves, hanging clear of the body on both coverings
        sleeve_end = hip_y + tunic_len * 0.06
        for side in (-1, 1):
            ax = cx + side * (shoulder_w / 2 + arm_w * 0.62)
            solid(min(cx + side * shoulder_w / 2, ax), shoulder_y,
                  max(cx + side * shoulder_w / 2, ax), shoulder_y + 2, ramp[2])
            for py in range(int(shoulder_y + 2), int(sleeve_end) + 1):
                u = (py - shoulder_y) / max(1.0, tunic_len)
                half = arm_w * (0.62 - 0.18 * u)
                if half <= 0.3:
                    continue
                rect(ax - half, py, ax + half, py,
                     lambda px, y, aa=ax - half, bb=ax + half: limb_cover(
                         px, y, shoulder_y, sleeve_end, ramp, aa, bb))
            # a cuff, so the sleeve ends rather than stops
            solid(ax - arm_w * 0.62, sleeve_end, ax + arm_w * 0.62,
                  sleeve_end, GARMENT_DETAIL[0])

        # --- hems
        if form != "outer_apron":
            solid(cx - shoulder_w * 0.30, hem, cx + shoulder_w * 0.30, hem,
                  GARMENT_DETAIL[0])
        if form == "armour_byrnie" and mats[0] == "mail":
            # rings, so mail is not a plain leather shape in a different colour
            for py in range(int(top) + 2, int(hem) - 1, 3):
                for px in range(int(cx - shoulder_w / 2) + 1,
                                int(cx + shoulder_w / 2), 3):
                    if row_is_inside(px, py, cx, shoulder_w, top, hem, 0.30):
                        put(px, py, GARMENT_DETAIL[2])
            # a rivet band at the throat, which is what makes mail read as mail
            solid(cx - neck_opening - 1, shoulder_y, cx + neck_opening + 1,
                  shoulder_y, GARMENT_RAMP["iron"][3])
        if form == "outer_cloak" and mats[0] == "fur":
            # a fur edge, drawn along the hem of the cloak
            for step in range(int(shoulder_w * 0.72)):
                put(round(cx - shoulder_w * 0.36 + step),
                    round(hem - (step % 3)), GARMENT_RAMP["fur"][3])
        if form == "outer_cloak":
            disc(cx + shoulder_w * 0.30, shoulder_y + 1, 1.6,
                 lambda px, py: GARMENT_RAMP["silver"][2])

    elif form == "legs_wraps":
        for side in (-1, 1):
            lx = cx + side * (hip_w / 2 - leg_w / 2)
            top = hip_y
            for py in range(int(top), int(ground) + 1):
                half = leg_w * 0.58
                rect(lx - half, py, lx + half, py,
                     lambda px, y, aa=lx - half, bb=lx + half: shade(
                         px, y, aa, bb, top, ground, ramp))
                # the wrap is a spiral: a seam every few rows, offset each time
                if int(py - top) % 4 == 0:
                    off = ((int(py - top) // 4) * 2) % max(1, int(leg_w * 1.2))
                    solid(lx - half + off, py, lx - half + off + 1, py,
                          GARMENT_DETAIL[0])

    elif form == "legs_hose":
        for side in (-1, 1):
            lx = cx + side * (hip_w / 2 - leg_w / 2)
            top = hip_y
            for py in range(int(top), int(ground) + 1):
                half = leg_w * 0.62
                rect(lx - half, py, lx + half, py,
                     lambda px, y, aa=lx - half, bb=lx + half: shade(
                         px, y, aa, bb, top, ground, ramp))
            # hose are tied at the ankle
            solid(lx - half, ground - 2, lx + half, ground - 1,
                  GARMENT_DETAIL[0])

    elif form in ("feet_low", "feet_high"):
        # A boot is a shaft and a foot, not a three-row peg. Drawn as a peg the
        # 1px contour was over half the plate, and a plate that is mostly its
        # own outline does not read as footwear at any size.
        shaft = 10 if form == "feet_high" else 6
        for side in (-1, 1):
            lx = cx + side * (hip_w / 2 - leg_w / 2)
            top = ground - shaft
            for py in range(int(top), int(ground - 1) + 1):
                u = (py - top) / max(1.0, shaft)
                half = leg_w * (0.52 + 0.10 * u)
                rect(lx - half, py, lx + half, py,
                     lambda px, y, aa=lx - half, bb=lx + half: shade(
                         px, y, aa, bb, top, ground, ramp))
            # the foot itself: wider than the shaft, and carried forward
            for py in range(int(ground - 2), int(ground) + 1):
                u = (py - (ground - 2)) / 2.0
                half = leg_w * (0.62 + 0.30 * u)
                rect(lx - half, py, lx + half, py,
                     lambda px, y, aa=lx - half, bb=lx + half: shade(
                         px, y, aa, bb, ground - 2, ground, ramp))
            # a toe cap, so the foot points forward instead of being a peg
            rect(lx + leg_w * 0.48, ground - 1, lx + leg_w * 0.95, ground,
                 lambda px, py: ramp[1])

    elif form == "head_cover":
        # a coif: cloth over the crown and down to the nape, face left clear
        for py in range(int(head_cy - head_r * 1.15), int(head_cy + head_r * 0.55) + 1):
            dy = (py - head_cy) / max(0.001, head_r)
            if abs(dy) > 1.05:
                continue
            half = head_r * max(0.0, 1 - dy * dy) ** 0.5 * 1.16
            if half <= 0.4:
                continue
            rect(head_cx - half, py, head_cx + half, py,
                 lambda px, y: ramp[min(3, int(abs(px - head_cx) * 0.4) % 4)])

    elif form == "head_helm":
        # a nasal helm: dome, brow band, and a nose bar. NOT a plate helm -
        # plate is the fourteenth century and MOODBOARD.md forbids it.
        for py in range(int(head_cy - head_r * 1.2), int(head_cy + head_r * 0.15) + 1):
            dy = (py - head_cy) / max(0.001, head_r)
            if abs(dy) > 1.1:
                continue
            half = head_r * max(0.0, 1 - dy * dy) ** 0.5 * 1.10
            if half <= 0.4:
                continue
            rect(head_cx - half, py, head_cx + half, py,
                 lambda px, y: shade(px, y, head_cx - half, head_cx + half,
                                      head_cy - head_r, head_cy, ramp))
        solid(head_cx - head_r * 1.05, head_cy - head_r * 0.05,
              head_cx + head_r * 1.05, head_cy + head_r * 0.20, ramp[3])
        solid(head_cx - 1, head_cy - head_r * 0.05, head_cx + 1,
              head_cy + head_r * 0.85, ramp[2])

    elif form == "rank_pin":
        # a brooch on the shoulder, with a visible pin. At 5x4 it was a dot, and
        # a dot is not a badge: the head of an adult figure is seven pixels wide,
        # so anything pinned to it has to be drawn at a scale that reads.
        bx = cx + shoulder_w * 0.28
        by = shoulder_y + 2
        disc(bx, by, 3.0, lambda px, py: shade(px, y, bx - 3.0, bx + 3.0,
                                                by - 3.0, by + 3.0, ramp))
        disc(bx, by, 1.4, lambda px, py: GARMENT_DETAIL[2])
        for step in range(5):                      # the pin bar under it
            solid(bx - 4 + step, by + 4, bx - 3 + step, by + 5, ramp[1])

    elif form == "rank_torc":
        # a neck ring: the clearest single status object of the period
        ry = M["neck_y"] + 1
        half = head_r * 1.5
        for px in range(int(cx - half), int(cx + half) + 1):
            u = (px - cx) / max(1.0, half)
            if u * u > 1:
                continue
            dy = int(half * 0.30 * (1 - u * u))
            put(px, ry + dy, ramp[2])
            put(px, ry + dy - 1, ramp[3])
        disc(cx, ry + 1, 2.6, lambda px, py: shade(px, y, cx - 2.6, cx + 2.6,
                                                    ry - 2, ry + 4, ramp))

    path = PLATES / f"{garment_id}.body.png"
    write_rgba_png(path, width, height, data)
    return path


def row_is_inside(px, py, ccx, width, top, hem, waist_pull):
    """Is a point under the trunk covering? Used to dither mail rings only."""
    u = (py - top) / max(1.0, hem - top)
    half = (width / 2) * (1 - waist_pull * min(1.0, u))
    return abs(px - ccx) < half


def garment_band(layer, M):
    """The box a layer of this sort is allowed to occupy, from the landmarks.

    The verifier needs to know where a layer was *meant* to go, and it cannot
    get that from the plate: a plate reports where it is, not where it belongs.
    Bounding the layer to the figure's silhouette is far too weak - a helmet
    slid down to the ankles is still inside the body. So the band is derived
    from `actor_landmarks`, the same arithmetic the draw uses, and the plate
    has to land inside it.

    This cannot catch a wrong landmark value, because the band would move with
    it. That is deliberate: it catches the class of bug where a layer is drawn
    correctly and then moved, or drawn somewhere the landmarks do not reach.
    """
    head_cy, head_r = M["head_cy"], M["head_r"]
    neck_y, shoulder_y = M["neck_y"], M["shoulder_y"]
    waist_y, hip_y = M["waist_y"], M["hip_y"]
    tunic_len = M["tunic_len"]
    half = M["shoulder_w"]
    if layer == "legs":
        y0, y1 = hip_y - 2, M["ground"]
    elif layer == "feet":
        y0, y1 = M["ground"] - 14, M["ground"]
    elif layer == "tunic":
        y0, y1 = shoulder_y - 3, shoulder_y + tunic_len * 1.30
    elif layer == "outer":
        y0, y1 = shoulder_y - 4, hip_y + tunic_len * 0.60
    elif layer == "armour":
        y0, y1 = shoulder_y - 3, hip_y + 4
    elif layer == "head":
        y0, y1 = head_cy - head_r * 1.5, neck_y + 4
    elif layer == "rank":
        y0, y1 = shoulder_y - 2, neck_y + head_r * 1.2
    else:
        y0, y1 = 0, CELL
    return (int(64 - half), int(y0) - 1, int(64 + half) + 1, int(y1) + 1)


def write_rgba_png(path, width, height, data):
    """Write 8-bit RGBA bytes as a PNG, without going through a resampler."""
    import struct
    import zlib

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    scan = b"".join(b"\x00" + bytes(data[y * width * 4:(y + 1) * width * 4])
                    for y in range(height))
    out += chunk(b"IDAT", zlib.compress(scan))
    out += chunk(b"IEND", b"")
    path.write_bytes(out)


def opaque_count(path, threshold=8):
    """Number of visible pixels, so the manifest can pin the silhouette's area.

    A hole punched inside an object leaves its bounding box untouched, so the
    size checks all still pass. The pixel count does not survive a hole.
    """
    raw = subprocess.check_output(
        ["convert", str(path), "-alpha", "extract", "-depth", "8", "gray:-"])
    return sum(1 for value in raw if value > threshold)


def contact_at_alpha(path, threshold):
    """Same measurement, at an explicit alpha threshold."""
    raw = subprocess.check_output(
        ["convert", str(path), "-alpha", "extract", "-depth", "8", "gray:-"])
    w, h = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    rows = {}
    for y in range(h):
        line = raw[y * w:(y + 1) * w]
        xs = [x for x in range(w) if line[x] > threshold]
        if xs:
            rows[y] = xs
    bottom = max(rows)
    xs = rows[bottom]
    return (xs[0] + xs[-1]) / 2.0, bottom, xs[0], xs[-1]


def hex_checks(plate):
    """Verify the terrain plate is a regular pointy-top hex on the contract."""
    w, h, rows = alpha_map(plate)
    ys = sorted(rows)
    top, bottom = ys[0], ys[-1]
    x0 = min(rows[y][0] for y in ys)
    x1 = max(rows[y][-1] for y in ys)
    left = len(rows.get(top + HEX_R // 2, []))
    right = len(rows.get(bottom - HEX_R // 2, []))
    problems = []
    if abs((x0 + x1) / 2 - HEX_CENTRE[0]) > 1:
        problems.append(f"hex centre x {(x0 + x1) / 2} != {HEX_CENTRE[0]}")
    if abs((top + bottom) / 2 - HEX_CENTRE[1]) > 1:
        problems.append(f"hex centre y {(top + bottom) / 2} != {HEX_CENTRE[1]}")
    if bottom != HEX_CENTRE[1] + HEX_R:
        problems.append(f"hex bottom {bottom} != {HEX_CENTRE[1] + HEX_R}")
    if x1 - x0 + 1 != 2 * HEX_W + 1:
        problems.append(f"hex width {x1 - x0 + 1} != {2 * HEX_W + 1}")
    if bottom - top + 1 != 2 * HEX_R + 1:
        problems.append(f"hex height {bottom - top + 1} != {2 * HEX_R + 1}")
    if abs(left - right) > 2:
        problems.append(f"side edges differ: {left} vs {right} px")
    return problems, (x1 - x0 + 1, bottom - top + 1, x0, top)


results = []
mask = hex_mask()

for stem, source_stem, box_w, box_h, kind in SPECS:
    body = PLATES / f"{stem}.body.png"
    if kind == "hex":
        # the field is drawn, not keyed out of a render: the source's furrows
        # cross, and crossing furrows read as basketry rather than as a field
        drawn = draw_terrain(stem, TERRAIN[stem])
        run("convert", "-size", f"{CELL}x{CELL}", "xc:none", str(drawn),
            "-gravity", "center", "-composite", str(body))
        drawn.unlink()
    elif kind == "arch":
        # a building is composed straight onto the cell, standing on the
        # ground line, so it needs neither a background key nor a trim
        body = draw_architecture(stem, ARCH[stem])
    elif kind == "prop":
        body = draw_prop(stem, PROP[stem])
    elif kind == "actor":
        body = draw_actor(stem, ACTOR[stem])
    elif kind == "animal":
        body = draw_animal(stem, ANIMAL[stem])
    elif kind == "garment":
        body = draw_garment(stem, GARMENT_OF_PLATE[stem],
                            GARMENT_AGE_OF_PLATE[stem])
    elif kind == "hazard":
        body = draw_hazard(stem, HAZARD[stem])
    elif kind == "camp":
        body = draw_camp(stem, CAMP[stem])
    elif kind == "site":
        body = draw_camp(stem, SITE[stem])
    else:
        source = SOURCES / f"{source_stem}.png"
        key(source, body, stem)
        run("convert", str(body), "-trim", "+repage", str(body))
    if kind == "hex":
        # the mask is applied to the 128 cell canvas, in cell coordinates
        run("convert", str(body), str(mask), "-alpha", "off",
            "-compose", "CopyOpacity", "-composite", str(body))
    elif kind == "object":
        run("convert", str(body), "-resize", f"{box_w}x{box_h}>", str(body))
    # retouch after the final resize and after masking, never before
    retouch(body, STYLE_COLORS[stem])
    scrub(body, WARMTH[stem])
    stroke_contour(body)

    plate = PLATES / f"{stem}.png"
    if kind == "hex":
        run("convert", "-size", f"{CELL}x{CELL}", "xc:none", str(body),
            "-gravity", "center", "-composite", str(plate))
        problems, _box = hex_checks(plate)
    else:
        cx, cy, left, right = contact_at_alpha(body, CONTACT_ALPHA)
        # shift by the exact amount that lands the run on the anchor; the run
        # is an odd or even number of pixels wide, so one integer step is
        # enough and the result is checked rather than assumed
        x = int(round(64 - cx))
        y = GROUND_Y - cy
        # A clothing layer is the exception, and it is the whole point of
        # actor_landmarks: the layer is already composed in the actor's frame,
        # so it must be placed at (0, 0) and left there. Re-anchoring it by its
        # own bottom edge put the helm on the ground line - a helm's bottom edge
        # is its nose bar, a torc's is its chin - which threw away the frame the
        # layer was drawn against. Nothing caught it: each garment plate is
        # individually well-formed, correctly centred and correctly above the
        # ground, so every plate check passed on a layer in the wrong place.
        if kind == "garment":
            x, y = 0, 0
        placed = PLATES / f"{stem}.placed.png"
        run("convert", "-size", f"{CELL}x{CELL}", "xc:none", str(body),
            "-geometry", f"+{x}+{y}", "-composite", str(placed))
        new_cx, new_bottom, _l, _r = contact_at_alpha(placed, CONTACT_ALPHA)
        if new_cx != 64 and kind != "garment":
            x += int(round(64 - new_cx))
            run("convert", "-size", f"{CELL}x{CELL}", "xc:none", str(body),
                "-geometry", f"+{x}+{y}", "-composite", str(placed))
        placed.unlink()

        bw, bh, bx, by = bbox(body)
        # A keyed object is trimmed to its own origin, so the shift is the
        # placement. A drawn building is not trimmed: it already stands where
        # it was composed, so its placement is the body's own origin.
        ow, oh = bw, bh
        ox, oy = (bx + x, by + y)
        shadow = PLATES / f"{stem}.shadow.png"
        radius = max(CONTACT_SHADOW_MIN_RADIUS,
                     min(CONTACT_SHADOW_MAX_RADIUS,
                         int(round(bw * CONTACT_SHADOW_RATIO))))
        if kind == "garment":
            # No contact shadow. A shadow belongs to whatever stands on the
            # ground, and the actor already has one; a second ellipse baked into
            # the layer darkens the tiles under the figure again, at the feet,
            # on every garment plate.
            run("convert", str(body), "-geometry", f"+{x}+{y}",
                "-background", "none", "-extent", f"{CELL}x{CELL}", str(plate))
        else:
            run("convert", "-size", f"{CELL}x{CELL}", "xc:none",
                "-fill", "rgba(24,20,16,0.40)",
                "-draw", f"ellipse 64,{GROUND_Y} {radius},{max(2, radius // 6)} 0,360",
                str(shadow))
            run("convert", str(shadow), str(body), "-geometry", f"+{x}+{y}",
                "-composite", str(plate))
            shadow.unlink()
        problems = []

    body.unlink()
    pw, ph, px_, py_ = bbox(plate)
    results.append({
        "id": stem, "kind": kind,
        "placement": "actor_frame" if kind == "garment" else "ground_anchored",
        "band": (garment_band(GARMENT_OF_PLATE[stem]["layer"],
                              actor_landmarks(
                                  ACTOR[f"actor_{GARMENT_AGE_OF_PLATE[stem]}"],
                                  float(HEX_CENTRE[0]), float(GROUND_Y)))
                 if kind == "garment" else None),
        "plate_opaque_px": opaque_count(plate),
        "plate_wh": (pw, ph),
        "plate_xy": (px_, py_),
        "object_wh": (ow, oh) if kind != "hex" else (pw, ph),
        "object_xy": (ox, oy) if kind != "hex" else (px_, py_),
        "problems": problems,
    })

    strip_clock(plate)
    guide = GUIDES / f"{stem}.png"
    # The crosshair is expressed in PLATE coordinates and drawn on the scaled,
    # offset cell, so it has to be transformed into that frame or it marks the
    # wrong pixel. scale is the sprite factor, and the offset is the declared
    # north-gravity inset.
    guide_scale = GUIDE_SPRITE / 100

    def in_guide(px, py):
        return (round(px * guide_scale) + GUIDE_SPRITE_OFFSET_X,
                round(py * guide_scale))

    centre_y = HEX_CENTRE[1] if kind == "hex" else GROUND_Y
    gx0, gy0 = in_guide(56, centre_y)
    gx1, gy1 = in_guide(72, centre_y)
    gcx, gcy = in_guide(64, centre_y)
    gtx, gty = in_guide(64, centre_y - 8)
    gbx, gby = in_guide(64, centre_y + 8)
    cross = (f"line {gx0},{gy0} {gx1},{gy1} "
             f"line {gtx},{gty} {gcx},{gcy} line {gcx},{gcy} {gbx},{gby}")
    # The crosshair is drawn on the EXTENDED CELL, never on the plate. Drawn on
    # the plate it lands on the sprite, and for anything smaller than the 16 px
    # stroke - a brooch, a helm, a boot - it hides the sprite completely, so
    # the guide shows the crosshair instead of the art. The crosshair marks the
    # anchor; it must not be what the reader sees instead of the plate.
    #
    # A clothing layer gets its BODY drawn underneath it first. A layer is not
    # an object with a bottom edge: the helm's bottom edge is its nose bar, and
    # a crosshair at the ground line forty pixels below the art says nothing
    # about whether the layer lands on a body. With the body there, the guide
    # answers the only question a layer raises. The ghost is drawn at full
    # colour on purpose - dimming it would make every ghost pixel a blend, and
    # then the verifier could no longer prove the ghost is the declared body.
    if kind == "garment":
        body_id = GARMENT_BODY_OF_PLATE[stem]
        run("convert", PLATES / f"{body_id}.png",
            "-filter", "point", "-resize", f"{GUIDE_SPRITE}%",
            "-background", "none", "-gravity", "north",
            "-extent", f"{GUIDE_CELL}x{GUIDE_CELL}",
            PLATES / f"{stem}.guideghost.png")
        # The layer is sized in its OWN convert. A single convert taking both
        # ghost and layer applies -resize to every input, so the ghost - already
        # 192x192 - was resized a second time to 250x250 and then cropped by
        # -extent, which pushed the body right and down and left the layer
        # floating off it. The guide still looked plausible: a small mark, a
        # figure, a caption.
        run("convert", PLATES / f"{stem}.png",
            "-filter", "point", "-resize", f"{GUIDE_SPRITE}%",
            "-background", "none", "-gravity", "north",
            "-extent", f"{GUIDE_CELL}x{GUIDE_CELL}",
            PLATES / f"{stem}.guidesize.png")
        run("convert", PLATES / f"{stem}.guideghost.png",
            PLATES / f"{stem}.guidesize.png",
            "-background", "none", "-flatten",
            "-background", "#10242a", "-alpha", "remove", "-alpha", "off",
            str(guide))
    else:
        run("convert", PLATES / f"{stem}.png",
            # one uniform scale on both axes: the guide is an acceptance surface
            # and must not distort the geometry it exists to show
            "-filter", "point", "-resize", f"{GUIDE_SPRITE}%",
            "-background", "#10242a", "-gravity", "north",
            "-extent", f"{GUIDE_CELL}x{GUIDE_CELL}", str(guide))
    for scratch in (f"{stem}.guideghost.png", f"{stem}.guidesize.png"):
        leftover = PLATES / scratch
        if leftover.exists():
            leftover.unlink()
    run("convert", str(guide),
        "-stroke", "#d8c9a3", "-strokewidth", "1", "-fill", "none",
        "-draw", cross,
        "-gravity", "south", "-fill", "#d8c9a3",
        "-font", "DejaVu-Sans", "-pointsize", "11", "-annotate", "+0+8", stem,
        "-fill", "none", "-stroke", "#3c5961", "-strokewidth", "2",
        "-draw", f"rectangle 1,1 {GUIDE_CELL - 2},{GUIDE_CELL - 2}",
        str(guide))
    strip_clock(guide)

plates = [PLATES / f"{stem}.png" for stem, *_ in SPECS]
ATLAS_COLUMNS = 4 if len(plates) % 4 == 0 else 3
ATLAS_ROWS = -(-len(plates) // ATLAS_COLUMNS)
ATLAS_CELLS = [(index % ATLAS_COLUMNS * ATLAS_STRIDE,
                index // ATLAS_COLUMNS * ATLAS_STRIDE)
               for index in range(len(plates))]
ATLAS_CANVAS = (ATLAS_COLUMNS * ATLAS_STRIDE - 32,
                ATLAS_ROWS * ATLAS_STRIDE - 32)
atlas = ROOT / "flat2d_proof_atlas_v0.png"
# One convert call for the whole atlas, not one per cell. The loop version
# re-opened and re-wrote a full-canvas PNG fifty-eight times, and the atlas is
# the thing the mutation harness repacks for most of its cases, so its cost was
# paid over and over. `-geometry ... -composite` chained in a single command
# composites each cell at its offset with one process.
atlas_args = ["convert", "-size", f"{ATLAS_CANVAS[0]}x{ATLAS_CANVAS[1]}",
              "xc:none"]
for path, (dx, dy) in zip(plates, ATLAS_CELLS):
    atlas_args += [str(path), "-geometry", f"+{dx}+{dy}", "-composite"]
atlas_args.append(str(atlas))
subprocess.run(atlas_args, check=True)
strip_clock(atlas)

guide_list = [GUIDES / f"{stem}.png" for stem, *_ in SPECS]
# the guide sheet is laid out on its own stride: 192 px cells with a 16 px
# gutter, which is a different pitch from the 128 px atlas cells
guide_canvas = (ATLAS_COLUMNS * GUIDE_CELL + GUIDE_GUTTER * (ATLAS_COLUMNS + 1),
                ATLAS_ROWS * GUIDE_CELL + GUIDE_GUTTER * (ATLAS_ROWS + 1))
# Same single-call treatment for the guide sheet.
guide_args = ["convert", "-size", f"{guide_canvas[0]}x{guide_canvas[1]}",
              f"xc:{GUIDE_BACKGROUND}"]
for index, guide in enumerate(guide_list):
    col = index % ATLAS_COLUMNS
    line = index // ATLAS_COLUMNS
    gx = GUIDE_GUTTER + col * (GUIDE_CELL + GUIDE_GUTTER)
    gy = GUIDE_GUTTER + line * (GUIDE_CELL + GUIDE_GUTTER)
    guide_args += [str(guide), "-geometry", f"+{gx}+{gy}", "-composite"]
guide_sheet = GUIDES / "flat2d_proof_guide_v0.png"
guide_args.append(str(guide_sheet))
run(*guide_args)
strip_clock(guide_sheet)
for guide in guide_list:
    guide.unlink()
mask.unlink()

print(f"cell {CELL}x{CELL}  ground_y {GROUND_Y}  "
      f"hex centre {HEX_CENTRE} R {HEX_R} W {HEX_W}")
for r in results:
    flag = "OK" if not r["problems"] else "FAIL " + "; ".join(r["problems"])
    print(f"{r['id']:32s} {r['kind']:6s} "
          f"object {r['object_wh'][0]:3d}x{r['object_wh'][1]:<3d} "
          f"at ({r['object_xy'][0]:3d},{r['object_xy'][1]:3d})  "
          f"plate {r['plate_wh'][0]:3d}x{r['plate_wh'][1]:<3d}  {flag}")

lines = [
    "version: 0",
    "id: hillcourt_flat2d_proof_v0",
    "name: Flat 2D production proof v0",
    "",
    "target:",
    "  render_mode: flat_2d_plate",
    f"  cell_canvas_px: [{CELL}, {CELL}]",
    f"  proof_anchor_px: [64, {GROUND_Y}]",
    f"  proof_hex_center_px: [{HEX_CENTRE[0]}, {HEX_CENTRE[1]}]",
    f"  proof_hex_circumradius_px: {HEX_R}",
    f"  proof_hex_half_width_px: {HEX_W}",
    "  reference_projection: isometric_2to1",
    "  camera_frozen: false",
    "  sprite_selector: none",
    "  sprite_selector_reason: client_frozen_and_no_selection_code_in_sim",
    "  final_hex_mask: false",
    "  final_claims: false",
        f"  revision: {REVISION}",
    "",
    "target_extras:",
    f"  anchor_tolerance_px: {ANCHOR_TOLERANCE_PX}",
    f"  max_unique_rgb_per_plate: {MAX_UNIQUE_RGB}",
    f'  contour_colour: "{CONTOUR}"',
    "",
    "atlas:",
    "  file: flat2d_proof/flat2d_proof_atlas_v0.png",
    "  guide: flat2d_proof/guides/flat2d_proof_guide_v0.png",
    "  plate_dir: flat2d_proof/plates",
    f"  columns: {ATLAS_COLUMNS}",
    f"  rows: {ATLAS_ROWS}",
    f"  cell_canvas_px: [{CELL}, {CELL}]",
    f"  canvas_px: [{ATLAS_CANVAS[0]}, {ATLAS_CANVAS[1]}]",
    "  layout:",
]
for (stem, *_rest), (dx, dy) in zip(SPECS, ATLAS_CELLS):
    lines.append(f"    - id: {stem}")
    lines.append(f"      offset_px: [{dx}, {dy}]")
lines += [
    "  guide_layout:",
    f"    cell_canvas_px: [{GUIDE_CELL}, {GUIDE_CELL}]",
    f"    canvas_px: [{guide_canvas[0]}, {guide_canvas[1]}]",
    f"    sprite_scale_pct: {GUIDE_SPRITE}",
    f"    sprite_scale_axes: uniform",
    f"    sprite_offset_px: [{GUIDE_SPRITE_OFFSET_X}, 0]",
    f"    columns: {ATLAS_COLUMNS}",
    f"    rows: {ATLAS_ROWS}",
    f"    gutter_px: {GUIDE_GUTTER}",
    f"    background: \"{GUIDE_BACKGROUND}\"",
    "",
    "pipeline:",
    "  background_key:",
    "    terrain_field_hex: not_used_texture_is_drawn",
    "    architecture_cottage_plate: cool_vignette_test_b_gt_r",
    "    prop_flat_cart_empty: cool_vignette_test_b_gt_r",
    "    actor_villager_plate: cool_vignette_test_b_gt_r",
    "    architecture_*: not_used_building_is_drawn",
    "  retouch: quantise_rgb_ramp_to_style_colors_then_restore_alpha",
    f'  contour: "restroke_1px_{CONTOUR}_from_thresholded_alpha"',
    "  order: key_then_resize_then_mask_then_retouch_then_scrub_then_contour",
    "  scrub: repaint_any_colour_whose_r_minus_b_is_below_the_ramp_width",
    f"  placement: ground_contact_run_centred_on_x_64_within_"
    f"{ANCHOR_TOLERANCE_PX}px_bottom_on_{GROUND_Y}",
    f"  anchor_tolerance_px: {ANCHOR_TOLERANCE_PX}",
    "  anchor_tolerance_reason: even_width_contact_runs_centre_between_pixels",
    f"  contact_shadow: synthetic_ellipse_radius_{CONTACT_SHADOW_RATIO}_of_"
    f"object_width_capped_at_{CONTACT_SHADOW_MAX_RADIUS}",
    f'  contact_shadow_colour: "{CONTOUR}"',
    "  object_ground_rule: object_contact_is_the_hex_centre_one_reference_point",
    f'  guide_cell_fill: "{GUIDE_CELL_FILL}"',
    "  guide_overlay_colours:",
    "    - \"#d8c9a3\"",
    "    - \"#3c5961\"",
    "  style_colours:",
]
for _stem, _count in sorted(STYLE_COLORS.items()):
    lines.append(f"    {_stem}: {_count}")
lines += [
    "  ramp_min_r_minus_b:",
]
for _stem, _width in sorted(WARMTH.items()):
    lines.append(f"    {_stem}: {_width}")
lines += [
    f"  max_unique_rgb_per_plate: {MAX_UNIQUE_RGB}",
    "  style_reference: references/detail_style_anchor.png",
    "  style_reference_scope: form_material_and_detail_not_hue",
    "  field_texture: drawn_parallel_furrows_single_direction",
    f"  field_furrow_angle_deg: {FIELD_FURROW_ANGLE}",
    f"  field_furrow_step_px: {FIELD_FURROW_STEP}",
    f"  field_turf_depth_px: {FIELD_TURF_DEPTH}",
    "  field_turf: green_perimeter_margin_allowed",
    f"  terrain_count: {len(TERRAIN_ORDER)}",
    f"  object_count: {len(OBJECT_SPECS)}",
    f"  architecture_count: {len(ARCH_ORDER)}",
    f"  prop_count: {len(PROP_ORDER)}",
    f"  actor_count: {len(ACTOR_ORDER)}",
    f"  animal_count: {len(ANIMAL_ORDER)}",
    f"  garment_count: {len(GARMENT_ORDER)}",
    f"  hazard_count: {len(HAZARD_ORDER)}",
    f"  camp_count: {len(CAMP_ORDER)}",
    f"  site_count: {len(SITE_ORDER)}",
    "  hazard_carrier_field: Hazard.kind",
    "  garment_layers:",
    *[f"    - {layer}" for layer in sorted(LAYER_ORDER)],
    "  garment_age_classes:",
    *[f"    - {age}" for age in GARMENT_AGE_CLASSES],
    "  garment_carrier_field: Household.legal_status_id",
    "  garment_sprite_selector: none",
    "  garment_status_armour_step:",
    *[f"    - {status}: {step}" for status, step in
      sorted(STATUS_ARMOUR_STEP.items())],
    "  no_data_carrier:",
    *[f"    - {cell_id}" for cell_id, (carrier, _k) in sorted(
        DATA_CARRIER.items()) if carrier is None],
    "  no_data_carrier_note: >-",
    "    Every plate above names no simulation field. The list is published so",
    "    that a new carrier-less plate cannot appear without being declared here,",
    "    and so validate_mapping.py can require the list and the cells to agree.",
    "  actor_excluded:",
    "    pose_reference_01: no_carrier_no_pose_field",
    "    pose_reference_02: no_carrier_no_pose_field",
    "    pose_reference_03: no_carrier_no_pose_field",
    "    pose_reference_04: no_carrier_no_pose_field",
    "    actor_retainer_equipment: no_carrier_no_equipment_field",
    "  architecture_excluded:",
    "    architecture_watchtower: needs_ontology_check_not_drawn",
    "",
    "cells:",
]
def _carrier_lines(cell_id):
    """Publish what identifies this plate, or say plainly that nothing does."""
    carrier, kind = DATA_CARRIER.get(cell_id, (None, None))
    if carrier is None:
        return ["    data_carrier: null",
                "    data_carrier_kind: none",
                "    data_carrier_reason: >-",
                "      No simulation field identifies this plate, so a renderer"
                " cannot key on",
                "      one. It is kept for geometry or for pipeline coverage, not"
                " as an asset.",
                "    sprite_selector: none"]
    return [f"    data_carrier: {carrier}",
            f"    data_carrier_kind: {kind}",
            "    sprite_selector: none"]


def material_lines(entry):
    """Publish the material a drawn plate is allowed to use.

    The terrain plates are written from scratch, so the only colours that can
    legitimately be in one are its own ramp, its edge ramp and the contour.
    Declaring that turns "the plate looks like its material" from an opinion
    into something the verifier can check pixel by pixel.
    """
    if entry["kind"] == "arch":
        arch = ARCH[entry["id"]]
        tones = (list(ARCH_RAMP[arch["wall"]]) + list(ARCH_THATCH)
                 + list(ARCH_GREY) + list(ARCH_DETAIL))
        out = ["    material: drawn",
               f"    material_name: {arch['name']}",
               f"    material_roof: {arch['roof']}",
               f"    material_wall: {arch['wall']}",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "garment":
        garment = GARMENT_OF_PLATE[entry["id"]]
        tones = []
        for mat in garment["mats"]:
            tones += list(GARMENT_RAMP[mat])
        tones += list(GARMENT_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {garment['name']}",
               f"    material_layer: {garment['layer']}",
               f"    material_wealth_step: {garment['step']}",
               f"    material_form: {garment['form']}",
               f"    material_age_class: {GARMENT_AGE_OF_PLATE[entry['id']]}",
               f"    body_id: {GARMENT_BODY_OF_PLATE[entry['id']]}",
               f"    material_carrier_field: Household.legal_status_id",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "animal":
        animal = ANIMAL[entry["id"]]
        tones = []
        for mat in animal["mats"]:
            tones += list(ANIMAL_RAMP[mat])
        tones += list(ANIMAL_DETAIL) + list(ANIMAL_RAMP["bone"])
        out = ["    material: drawn",
               f"    material_name: {animal['name']}",
               f"    material_form: {animal['form']}",
               "    material_mats: [" + ", ".join(animal["mats"]) + "]",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        out.append("    serves_goods: [" + ", ".join(animal["serves_goods"]) + "]")
        return out
    if entry["kind"] == "hazard":
        hazard = HAZARD[entry["id"]]
        tones = []
        for mat in hazard["mats"]:
            tones += list(HAZARD_RAMP[mat])
        tones += list(HAZARD_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {hazard['name']}",
               f"    material_form: {hazard['form']}",
               "    material_carrier_field: Hazard.kind",
               f"    material_carrier_value: {hazard['catalog_id']}",
               f"    hazard_catalog_id: {hazard['catalog_id']}",
               "    knowledge_cue: observed",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "site":
        site = SITE[entry["id"]]
        tones = []
        for mat in site["mats"]:
            tones += list(CAMP_RAMP[mat])
        tones += list(CAMP_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {site['name']}",
               f"    material_form: {site['form']}",
               f"    game_form: {site['game_form']}",
               f"    material_carrier_field: {site['carrier_field']}",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "camp":
        camp = CAMP[entry["id"]]
        tones = []
        for mat in camp["mats"]:
            tones += list(CAMP_RAMP[mat])
        tones += list(CAMP_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {camp['name']}",
               f"    material_form: {camp['form']}",
               f"    game_form: {camp['game_form']}",
               f"    material_carrier_field: {camp['carrier_field']}",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "actor":
        actor = ACTOR[entry["id"]]
        tones = []
        for mat in actor["mats"]:
            tones += list(ACTOR_RAMP[mat])
        tones += list(ACTOR_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {actor['name']}",
               f"    material_carrier_field: Person.age_class",
               f"    material_carrier_value: {actor['age_class']}",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] == "prop":
        prop = PROP[entry["id"]]
        tones = []
        for mat in prop["mats"]:
            tones += list(PROP_RAMP[mat])
        tones += list(PROP_DETAIL)
        out = ["    material: drawn",
               f"    material_name: {prop['name']}",
               f"    material_form: {prop['form']}",
               "    material_mats: [" + ", ".join(prop["mats"]) + "]",
               "    material_ramp:"]
        for tone in tones:
            out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
        return out
    if entry["kind"] != "hex":
        return ["    material: keyed_from_render"]
    terrain = TERRAIN[entry["id"]]
    tones = (list(terrain["base"]) + list(terrain["edge"])
             + list(terrain.get("extra", ())))
    out = ["    material: drawn", f"    material_pattern: {terrain['pattern']}",
           f"    material_name: {terrain['name']}", "    material_ramp:"]
    for tone in tones:
        out.append(f'      - "#{tone[0]:02X}{tone[1]:02X}{tone[2]:02X}"')
    return out


for r in results:
    stem = r["id"]
    lines += [
        f"  - id: {stem}",
        ("    source_file: null  # drawn, not keyed from a render"
         if r["kind"] in ("hex", "arch", "prop", "actor", "animal", "garment") else
         f"    source_file: flat2d_proof/sources/{stem}.png"),
        f"    anchor_status: proof_hex_center_64_64" if r["kind"] == "hex"
        else "    anchor_status: proof_ground_contact_64_64",
        f"    placement: {r['placement']}",
        *([f"    guide_ghost: {GARMENT_BODY_OF_PLATE[r['id']]}"]
          if r["kind"] == "garment" else []),
        *([f"    expected_band_px: [{r['band'][0]}, {r['band'][1]}, "
            f"{r['band'][2]}, {r['band'][3]}]"] if r.get("band") else []),
        f"    object_size_px: [{r['object_wh'][0]}, {r['object_wh'][1]}]",
        f"    object_placement_px: [{r['object_xy'][0]}, {r['object_xy'][1]}]",
        f"    plate_size_px: [{r['plate_wh'][0]}, {r['plate_wh'][1]}]",
        f"    plate_opaque_px: {r['plate_opaque_px']}",
        *material_lines(r),
        f"    plate_placement_px: [{r['plate_xy'][0]}, {r['plate_xy'][1]}]",
        "    status: proof_generated",
        *_carrier_lines(stem),
    ]
# The manifest is only written when every cell passed its own geometry check.
# A manifest that describes geometry the build could not produce is worse than
# no manifest, because the verifier would then certify a broken artefact.
failed = [r for r in results if r["problems"]]
if failed:
    for r in failed:
        for problem in r["problems"]:
            print(f"  {r['id']}: {problem}", file=sys.stderr)
    print("build FAILED: no manifest written", file=sys.stderr)
    raise SystemExit(1)

(ROOT / "flat2d_proof_manifest.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")

# Drop plates and guide cells that this build no longer declares. Renaming a
# family - sixteen adult-only garments became forty-eight per-body ones - left
# the old files on disk: 115 PNGs for 90 cells. The atlas and the manifest are
# right, so every check passed, and the pollution only shows up in anything that
# globs the directory, starting with the byte-for-byte reproducibility check,
# which then reported a diff against files nothing had ever built this run.
declared = {c["id"] for c in results}
# the guide sheet is a build product, not a cell: its stem is not a plate id
KEEP = {"flat2d_proof_guide_v0"}
for folder in (PLATES, GUIDES):
    for stale in sorted(folder.glob("*.png")):
        if stale.stem not in declared and stale.stem not in KEEP:
            stale.unlink()
            print(f"removed stale {stale.relative_to(ROOT)}")

print("manifest written")
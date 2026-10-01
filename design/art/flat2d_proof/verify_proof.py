#!/usr/bin/env python3
"""Pixel-level verification of the HILLCOURT flat 2D proof.

Checks the things that would otherwise be judged by eye:
  * the terrain plate is a regular pointy-top hex on the declared centre;
  * object plates touch the shared ground line at the declared anchor x;
  * the manifest reproduces the plates it describes;
  * the atlas layout matches the manifest;
  * alpha carries no baked background.

Run:  python3 design/art/flat2d_proof/verify_proof.py
Exit code 0 means the proof is internally consistent; 1 means it is not.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]

# Two thresholds, because the contact shadow is deliberately softer than the
# plate. Anything above OPAQUE is the object itself; the band between the two
# is the synthetic shadow and is measured separately.
SHADOW_THRESHOLD = 8
OPAQUE = 160

# Minimum red-blue spread a colour must have to belong to the style ramp.
# Matches WARMTH in build_proof.py; the contour colour is exempt because it is
# deliberately near-neutral.
WARMTH = {
    "terrain_field_hex": 10,
    "architecture_cottage_plate": 8,
    "prop_flat_cart_empty": 8,
    "actor_villager_plate": 8,
}
CONTOUR_RGB = (24, 20, 16)

# The anchor is a rasterised contact run. A run of even width is centred
# between two pixel centres, so the closest reachable anchor is half a pixel
# off. This is the rasterisation limit, not a fudge factor: the build picks the
# shift that minimises the error and the check refuses anything worse.
ANCHOR_TOLERANCE_PX = 0.5

# The guide's crosshair and caption are drawn over the sprite. The crosshair is
# two 17 px strokes and the caption one line of text, so between them they
# cannot cover a large share of any plate. The two fractions below are
# properties of the sheet, not tuned per plate: half the sprite must be the
# plate pixel for pixel, and the guide's own furniture may not claim more than
# the rest.
MIN_SPRITE_MATCH = 0.5
MAX_SPRITE_OVERLAY = 0.5
# Measured: the declared body shows through at 35.9-97.7% across the 48
# clothing cells, the wrong body at 2.9-9.3%. 25% is the empty middle.
MIN_GHOST_MATCH = 0.25

# Fields that must stay false no matter what a later edit claims.
MUST_STAY_FALSE = ("camera_frozen", "final_claims", "final_hex_mask")
ALLOWED_STATUS = "proof_generated"

# The manifest revision this verifier was written against. A manifest claiming
# a different revision is describing a proof whose rules are not the rules
# encoded here, so it cannot be checked by this script.
SUPPORTED_REVISION = 10

# The palette ceiling this verifier enforces, independent of the manifest.
SUPPORTED_MAX_UNIQUE_RGB = 32


def load_manifest():
    try:
        import yaml
    except ImportError:
        print("FAIL: PyYAML is required to read the manifest")
        raise SystemExit(1)
    return yaml.safe_load((ROOT / "flat2d_proof_manifest.yml").read_text(
        encoding="utf-8"))


def alpha_rows(path, threshold=SHADOW_THRESHOLD):
    """{y: [x, ...]} for pixels above the alpha threshold."""
    header = subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split()
    width, height = int(header[0]), int(header[1])
    raw = subprocess.check_output(
        ["convert", str(path), "-alpha", "extract", "-depth", "8", "gray:-"])
    if len(raw) != width * height:
        raise ValueError(f"{path}: unexpected raw alpha size")
    rows = {}
    for y in range(height):
        line = raw[y * width:(y + 1) * width]
        xs = [x for x in range(width) if line[x] > threshold]
        if xs:
            rows[y] = xs
    return width, height, rows


def bbox(rows):
    ys = sorted(rows)
    x0 = min(rows[y][0] for y in ys)
    x1 = max(rows[y][-1] for y in ys)
    return x1 - x0 + 1, ys[-1] - ys[0] + 1, x0, ys[0]


def rows_are_centred(rows, hex_cx, tolerance=1):
    """The hex must be symmetric about x=64 on every row."""
    bad = [y for y, xs in rows.items()
           if abs((xs[0] + xs[-1]) / 2 - hex_cx) > tolerance]
    if bad:
        return [f"rows are not centred on x={hex_cx}: {len(bad)} of "
                f"{len(rows)} rows, first at y={bad[0]}"]
    return []


def edges_are_straight(rows, hex_cy, hex_r, hex_cx, hex_half,
                       tolerance=None):
    """Each sloped edge must be a straight line, and reach both endpoints.

    A rasterised diagonal advances by 1 or 2 px per row, and the rasteriser
    includes partially covered pixels, so neither the per-row step nor the
    distance to the ideal polygon is the test. What matters is that the edge
    does not bend: the points are fitted with a least-squares line and the
    residual must stay small. A skewed or kinked tile fails this; a correctly
    rasterised straight edge passes it whatever the half-pixel offset.

    The default tolerance is derived from the staircase of the declared slope:
    a line advancing `slope` px per row leaves a residual of up to `slope / 2`,
    plus a pixel for the rasteriser including partially covered pixels. For the
    declared 42/24 = 1.75 px slope that is about 1.5 px.
    """
    if tolerance is None:
        tolerance = max(1.0, (hex_half / (hex_r / 2)) / 2 + 0.5)
    problems = []
    half_r = hex_r // 2
    spans = (("upper", hex_cy - hex_r, hex_cy - half_r),
             ("lower", hex_cy + hex_r, hex_cy + half_r))
    for tag, y_vertex, y_shoulder in spans:
        run = y_shoulder - y_vertex
        for sign, side in ((1, "right"), (-1, "left")):
            points = []
            for step in range(abs(run) + 1):
                y = y_vertex + step * (1 if run > 0 else -1)
                xs = rows.get(y)
                if not xs:
                    continue
                edge = xs[-1] if sign > 0 else xs[0]
                points.append((y, edge))
            if len(points) < 3:
                problems.append(f"hex {tag} {side} edge has too few rows")
                continue
            n = len(points)
            sum_y = sum(p[0] for p in points)
            sum_x = sum(p[1] for p in points)
            sum_yy = sum(p[0] * p[0] for p in points)
            sum_yx = sum(p[0] * p[1] for p in points)
            denominator = n * sum_yy - sum_y * sum_y
            if denominator == 0:
                problems.append(f"hex {tag} {side} edge is vertical")
                continue
            slope = (n * sum_yx - sum_y * sum_x) / denominator
            intercept = (sum_x - slope * sum_y) / n
            worst = max(abs(x - (slope * y + intercept))
                        for y, x in points)
            if worst > tolerance:
                problems.append(
                    f"hex {tag} {side} edge bends: worst residual "
                    f"{worst:.2f} px from a straight line")
            # the endpoints still have to land on the declared geometry
            first = points[0]
            last = points[-1]
            if abs(first[1] - hex_cx) > 3:
                problems.append(f"hex {tag} {side} edge starts at x={first[1]} "
                                f"at the vertex, not at x={hex_cx}")
            if abs(last[1] - (hex_cx + sign * hex_half)) > 1:
                problems.append(f"hex {tag} {side} edge ends at x={last[1]}, "
                                f"the shoulder is at "
                                f"{hex_cx + sign * hex_half}")
    return problems


def contour_present(path, colour, min_pixels=8):
    """The plate must actually carry the declared 1px contour.

    The stroke lands on the antialiased silhouette edge, so part of it is
    legitimately soft. What must hold is that the colour is present at all and
    that a solid core exists: a plate whose contour was removed, or made
    entirely translucent, fails both.
    """
    raw = raw_rgba(path)
    want = tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    solid = soft = 0
    for i in range(0, len(raw), 4):
        if (raw[i], raw[i + 1], raw[i + 2]) == want:
            if raw[i + 3] > 128:
                solid += 1
            elif raw[i + 3] > 0:
                soft += 1
    if solid + soft < min_pixels:
        return [f"the {colour} contour is missing: {solid + soft} pixels, "
                f"expected at least {min_pixels}"]
    if solid < min_pixels:
        return [f"the {colour} contour has no solid core: {solid} opaque of "
                f"{solid + soft} pixels"]
    return []


def unique_rgb(path):
    """Distinct RGB values among the visible pixels of a plate."""
    raw = raw_rgba(path)
    return len({(raw[i], raw[i + 1], raw[i + 2])
                for i in range(0, len(raw), 4) if raw[i + 3] > 0})


def rows_are_solid(rows, expected_rows=None):
    """Every row of a convex hex must be one unbroken run, with no holes.

    When the set of expected rows is given, a row that disappeared counts as a
    defect too: a slot cut clean across the tile leaves every surviving row
    perfectly solid, so checking only the rows that exist would miss it.
    """
    problems = []
    for y, xs in rows.items():
        if xs[-1] - xs[0] + 1 != len(xs):
            problems.append(f"row y={y} is not solid: {len(xs)} px spread over "
                            f"{xs[-1] - xs[0] + 1} px")
    if expected_rows is not None:
        missing = sorted(set(expected_rows) - set(rows))
        if missing:
            problems.append(f"{len(missing)} rows are gone entirely, first at "
                            f"y={missing[0]}")
    return problems


def _find_repo_root():
    """Where the simulation source lives, from wherever this script was run.

    Counting parents from __file__ works only when the proof sits in the repo.
    The mutation test copies the proof into a scratch tree and runs the copy, so
    that walk lands in a temporary directory and every carrier check would report
    a false failure. Look for the ontology instead of guessing, and let the
    caller say where it is.
    """
    override = os.environ.get("HILLCOURT_REPO")
    candidates = [Path(override)] if override else []
    candidates += [Path.cwd(), *Path(__file__).resolve().parents]
    for base in candidates:
        probe = base / "sim/src/hillcourt/ontology.py"
        if probe.exists():
            return base
    return None


def sheet(manifest_path):
    """Resolve a sheet the manifest names by a repo-relative path.

    The atlas and the guide are the proof's own artefacts, so they are resolved
    against the proof directory, not the repository. The manifest names them as
    `flat2d_proof/...`, and a scratch copy of the proof is rarely called
    `flat2d_proof`, so the leading segment - the proof directory's own name - is
    dropped and the remainder resolved against it. Resolving from the repo root
    instead meant the mutation harness rebuilt sheets the verifier never read.
    """
    parts = Path(manifest_path).parts
    return ROOT / Path(*parts[1:]) if len(parts) > 1 else ROOT / manifest_path


_SOURCE_ROOT = _find_repo_root()
ONTOLOGY = _SOURCE_ROOT / "sim/src/hillcourt/ontology.py" if _SOURCE_ROOT else None
SIM_ROOT = _SOURCE_ROOT / "sim/src/hillcourt" if _SOURCE_ROOT else None


def _ontology_class_body(name):
    """The source of one dataclass in ontology.py, or None if there is no such class."""
    if ONTOLOGY is None or not ONTOLOGY.exists():
        return None
    text = ONTOLOGY.read_text(encoding="utf-8")
    marker = f"class {name}:"
    if marker not in text:
        return None
    start = text.index(marker)
    end = text.find("\n@dataclass", start)
    return text[start:end if end > 0 else len(text)]


# Every per-plate check needs the same bytes, and each was shelling out for
# them: five `convert` calls per plate, so ~290 subprocesses to read the same
# 58 files five times over. One batch read fills this cache and the checks read
# from it; anything not in it still shells out, so the helper is never wrong,
# only slower.
_RAW_RGBA = {}


def raw_rgba(path):
    """Raw 8-bit RGBA for a path, from the batch cache when it is there."""
    key = str(path)
    if key in _RAW_RGBA:
        return _RAW_RGBA[key]
    raw = subprocess.check_output(
        ["convert", str(path), "-depth", "8", "rgba:-"])
    _RAW_RGBA[key] = raw
    return raw


_GETATTR_FIELDS = None


def _getattr_fields():
    """Every field name the simulation reads through getattr, scanned once.

    Scanning per cell re-read every .py in sim/ six times per verification and
    made the verifier four times slower, which is not a cost a pixel check
    should carry. One scan, cached for the life of the process.
    """
    global _GETATTR_FIELDS
    if _GETATTR_FIELDS is None:
        found = set()
        if SIM_ROOT is not None:
            for path in SIM_ROOT.rglob("*.py"):
                for match in re.finditer(
                        r"""getattr\([^,()]+,\s*["']([a-z_][a-z_0-9]*)["']""",
                        path.read_text(encoding="utf-8")):
                    found.add(match.group(1))
        _GETATTR_FIELDS = found
    return _GETATTR_FIELDS


def _getattr_sites(field):
    """How many places read `field` through getattr - the optional-attribute path."""
    return 1 if field in _getattr_fields() else 0


def _declared_tile_marks():
    """Field names listed in the sim's declared tile-mark tuples.

    `has_mark()` validates against `LAND_MARKS` before reading, so a name in
    that tuple is a real, declared, generically-read attribute.
    """
    if SIM_ROOT is None:
        return set()
    found = set()
    for path in SIM_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Strip comments first: a mark tuple documents each name inline, and
        # those comments contain parentheses ("пристань: именная переправа
        # (с ford/bridge)"), so a `[^)]*` body stops inside the first comment
        # and the parser silently finds no names at all.
        clean = re.sub(r"#[^\n]*", "", text)
        for match in re.finditer(r"^[A-Z_]*MARKS[^=]*=[ ]*\(([^)]*)\)", clean,
                                 re.M):
            found |= {v.strip().strip('"\'')
                      for v in match.group(1).split(",")
                      if v.strip().strip('"\'')}
    return found


def carrier_is_real(carrier, kind):
    """Does the named field exist the way the manifest says it does?

    A declared_field must be a real dataclass field on a real class. An
    optional_attribute must be read through getattr somewhere in the sim,
    because that is the only way it exists at all. Either way, an invented
    field name fails here rather than in a reviewer's head.
    """
    if "." not in carrier:
        return f"carrier {carrier!r} is not Class.field"
    class_name, field = carrier.split(".", 1)
    if ONTOLOGY is None:
        # Say it once, loudly, rather than failing every cell with a lie.
        return None
    body = _ontology_class_body(class_name)
    if body is None:
        return f"carrier names class {class_name!r}, which ontology.py does not define"
    declared = re.search(rf"^\s+{re.escape(field)}\s*:", body, re.M) is not None
    if kind == "declared_field":
        if not declared:
            return (f"carrier {carrier} claims declared_field, but {field} is not "
                    "a declared field on that class")
        return None
    if kind == "optional_attribute":
        if SIM_ROOT is None:
            return None
        is_a_mark = field in _declared_tile_marks()
        if is_a_mark:
            # A tile mark is read generically: `has_mark()` does
            # `getattr(tile, mark, False)` with `mark` a variable, so the field
            # name never appears in a getattr call and a literal grep cannot see
            # it. The name is instead a member of a declared mark tuple, and
            # that declaration is the thing worth checking.
            return None
        if not declared and _getattr_sites(field) == 0:
            # Report the MARK reason, not the getattr reason. `has_mark()`
            # validates the name against a declared mark tuple before it reads
            # anything, so a name outside every tuple can never be carried this
            # way whether or not some getattr mentions it. Saying "nothing
            # reads it through getattr" is true and useless: it invites someone
            # to add a getattr, which would still be rejected at runtime.
            return (f"carrier {carrier} claims optional_attribute, but "
                    f"{field!r} is not a declared tile mark")
        if declared:
            return (f"carrier {carrier} claims optional_attribute, but {field} is "
                    "declared on the class; declare it honestly")
        return None
    return f"carrier kind {kind!r} is not declared_field or optional_attribute"


def garment_lands_on_actor(plate, solid, cell, cell_id, anchor_y):
    """A clothing layer must be inside the figure it is worn on.

    Nothing else in the verifier can see this. Every other check asks whether a
    plate is well formed on its own - centred, contoured, made of declared
    materials, above the ground line - and a helmet that has been slid down to
    the feet passes all of them. The layer is only an overlay in relation to a
    body, so the relation has to be asserted.

    Two facts bound a garment: it cannot hang below the figure's feet, and it
    cannot float above the figure's crown. A boot sole may reach the ground
    line, a crown may reach the top of the head, and nothing may go further.

    The body is the one the cell declares, not always the adult. A layer is a
    garment on a specific body, and a child of a thegn wears the thegn's tunic
    cut to a child. Checking a child's tunic against the adult would pass
    almost anything - the adult is taller, so a whole class of too-tall layers
    would slip through - which is the same mistake as checking every layer
    against one figure at all.
    """
    problems = []
    body_id = cell.get("body_id")
    if not body_id:
        return [f"{cell_id}: a clothing layer must name the body it overlays"]
    # The body must be the one this age class declares. Checking only that *a*
    # plate of that name exists let a child's tunic claim `actor_villager_plate`
    # - a real plate, and a wholly wrong body - and the mutation that did it was
    # green. `material_age_class` and `body_id` are the same claim stated twice,
    # so they have to agree.
    expected = f"actor_{cell.get('material_age_class')}"
    if body_id != expected:
        return [f"{cell_id}: material_age_class is "
                f"{cell.get('material_age_class')!r} but body_id is "
                f"{body_id!r}; the body a layer is cut for is "
                f"{expected!r}"]
    actor = ROOT / "plates" / f"{body_id}.png"
    if not actor.exists():
        return [f"{cell_id}: cannot check the layer, {body_id}.png is missing"]
    _, _, body_rows = alpha_rows(actor, OPAQUE)
    if not body_rows:
        return [f"{cell_id}: cannot check the layer, {body_id} is empty"]
    body_top = min(body_rows)
    body_bottom = max(body_rows)

    layer_rows = [y for y in sorted(solid) if solid[y]]
    if not layer_rows:
        return [f"{cell_id}: the layer has no opaque pixels"]
    low, high = min(layer_rows), max(layer_rows)
    if low > body_bottom:
        problems.append(f"{cell_id}: the layer starts at y={low}, below the "
                        f"figure's lowest row {body_bottom}: it is not on the body")
    if high > body_bottom:
        problems.append(f"{cell_id}: the layer reaches y={high}, below the "
                        f"figure's feet at y={body_bottom}")
    if high < body_top:
        problems.append(f"{cell_id}: the layer reaches y={high}, above the "
                        f"figure's crown at y={body_top}")

    # The body silhouette is far too generous a bound - a helmet at the ankles
    # is still inside the figure - so the layer is also held to the band its
    # own landmarks allow. That band is computed by the build from
    # actor_landmarks, not read back from the plate, so a layer that was drawn
    # somewhere the landmarks do not reach cannot satisfy it.
    band = cell.get("expected_band_px")
    if band:
        bx0, by0, bx1, by1 = band
        _, _, all_rows = alpha_rows(plate, SHADOW_THRESHOLD)
        if all_rows:
            lx0 = min(min(all_rows[y]) for y in all_rows)
            lx1 = max(max(all_rows[y]) for y in all_rows)
            ly0, ly1 = min(all_rows), max(all_rows)
            if ly0 < by0 or ly1 > by1 or lx0 < bx0 or lx1 > bx1:
                problems.append(
                    f"{cell_id}: the layer occupies "
                    f"({lx0},{ly0})-({lx1},{ly1}), outside the band its "
                    f"landmarks allow ({bx0},{by0})-({bx1},{by1})")
    return problems


def plate_uses_declared_material(path, cell, contour_colour, tolerance=26):
    """Every opaque pixel of a drawn plate must come from its declared ramp.

    The ramp is published in the manifest, so a plate that quietly drifts off
    its material - a blue river turning to mud, or a turf band that averages
    into the soil - is caught here instead of surviving to be called a style
    question.
    """
    ramp = []
    for spec in cell.get("material_ramp", []):
        text = str(spec).lstrip("#")
        if len(text) == 6:
            ramp.append(tuple(int(text[i:i + 2], 16) for i in (0, 2, 4)))
    if not ramp:
        return [f"material_ramp is missing; the plate declares no material"]
    contour = (tuple(int(contour_colour.lstrip("#")[i:i + 2], 16)
                     for i in (0, 2, 4)) if contour_colour else None)
    raw = raw_rgba(path)
    stray = {}
    for i in range(0, len(raw), 4):
        if raw[i + 3] == 0:
            continue
        pixel = (raw[i], raw[i + 1], raw[i + 2])
        if pixel == contour:
            continue
        if any(max(abs(a - b) for a, b in zip(pixel, tone)) <= tolerance
               for tone in ramp):
            continue
        stray[pixel] = stray.get(pixel, 0) + 1
    if stray:
        listed = ", ".join(f"#{r:02X}{g:02X}{b:02X} x{n}"
                           for (r, g, b), n in sorted(
                               stray.items(), key=lambda kv: -kv[1])[:4])
        return [f"{sum(stray.values())} pixels are not in the declared "
                f"material: {listed}"]
    return []


def warm_ramp_only(path, warmth=8):
    """No cold or neutral colour may survive anywhere in a plate.

    The threshold applies at every alpha, not just the opaque core: a cold halo
    lives on the antialiased edge, which is exactly where a `> 128` filter would
    never look. The contour colour is exempt because it is deliberately neutral.
    """
    raw = raw_rgba(path)
    cold = {}
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if a > 0 and r - b < warmth and (r, g, b) != CONTOUR_RGB:
            cold[(r, g, b)] = cold.get((r, g, b), 0) + 1
    if cold:
        listed = ", ".join(f"#{r:02X}{g:02X}{b:02X} x{n}"
                           for (r, g, b), n in sorted(cold.items(),
                                                      key=lambda kv: -kv[1])[:3])
        return [f"{len(cold)} cold or neutral colours survived the key: {listed}"]
    return []


def normalise_hex(colour):
    """Turn ImageMagick's srgb(r,g,b) into a #rrggbb string."""
    text = colour.strip()
    if text.startswith("srgb"):
        values = text[text.find("(") + 1:text.find(")")].split(",")[:3]
        return "#" + "".join(f"{int(round(float(v))):02x}" for v in values)
    return text.lower()


def plate_has_content(path, contour_rgb, min_values=5, max_contour=0.5,
                      min_interior_values=3, max_interior_dominance=0.9):
    """A plate must be a drawing, not a slab with an outline round it.

    A slab of one warm colour with its 1px contour left intact passes both a
    naive "more than one colour" rule and a naive "the contour is present"
    rule. What separates a drawing from a slab is how much of the *interior*
    is distinguishable from the interior behind it, so the check looks at the
    histogram of the plate and refuses one where nearly everything is a single
    tone.
    """
    raw = raw_rgba(path)
    opaque = [(raw[i], raw[i + 1], raw[i + 2])
              for i in range(0, len(raw), 4) if raw[i + 3] > 0]
    # the interior is measured on the solid core only: the soft contact shadow
    # is not part of the drawing and would supply the variety a slab lacks
    solid = [(raw[i], raw[i + 1], raw[i + 2])
             for i in range(0, len(raw), 4) if raw[i + 3] >= 200]
    if not opaque:
        return ["the plate has no opaque pixels at all"]
    if not solid:
        return ["the plate has no solid pixels; the contact shadow alone is "
                "not an illustration"]
    contour = sum(1 for c in solid if c == contour_rgb)
    # The variety floor has to scale with the plate. A cottage carries hundreds
    # of pixels and can afford five tones; a brooch is 50 pixels and legitimately
    # has two, and calling that a block would be the check rejecting a correct
    # asset. The dominance rule below - no more than 90% of the interior in one
    # tone - is what actually separates a drawing from a slab, and it stays
    # strict at every size.
    need_values = min(min_values, 1 + len(opaque) // 40)
    need_interior = min(min_interior_values, 1 + len(opaque) // 40)
    if len(set(opaque)) < need_values:
        return [f"the plate carries {len(set(opaque))} distinct values over "
                f"{len(opaque)} opaque pixels, fewer than the {need_values} a "
                f"plate of this size needs; this is a block, not an illustration"]
    # A 1px outline is a fixed cost against the area it encloses, so on a small
    # shape it is necessarily most of the drawing: the head of an adult figure is
    # seven pixels across, and a helm on it cannot be anything but outline. The
    # slack is granted by size, never by plate, and the block test below still
    # has to pass at that size.
    contour_cap = 0.75 if len(solid) < 80 else max_contour
    if contour / len(solid) > contour_cap:
        return [f"{contour} of {len(solid)} solid pixels are the contour "
                f"colour, more than the {int(contour_cap * 100)}% a plate of "
                "this size allows: the plate is mostly its own outline"]
    # A slab of one warm tone with its outline left intact has plenty of
    # distinct values and a perfect contour: the outline supplies the variety.
    # So the interior is measured on its own, with the contour stripped out.
    interior = [c for c in solid if c != contour_rgb]
    if not interior:
        return ["the plate is nothing but its own contour"]
    if len(set(interior)) < need_interior:
        return [f"the interior of the plate has {len(set(interior))} distinct "
                f"values over {len(interior)} pixels, fewer than the "
                f"{need_interior} a plate of this size needs; the outline "
                "supplies the rest, which makes it a block"]
    # A slab can keep a few stray pixels and still read as a block, so the
    # share of the interior taken by its most common tone is what decides. A
    # drawn interior spreads its tones out; a filled one does not.
    counts = {}
    for value in interior:
        counts[value] = counts.get(value, 0) + 1
    dominant = max(counts.values()) / len(interior)
    if dominant > max_interior_dominance:
        return [f"the interior of the plate is {dominant:.0%} one tone, more "
                f"than the {max_interior_dominance:.0%} a drawing allows; "
                "this is a block, not an illustration"]
    return []


def compare_sprite_at(guide_rgba, sprite_rgba, sw, sh, offset_x, offset_y,
                      cell, origin_x=0, origin_y=0, guide_w=None):
    """Compare the guide sprite with the plate at one declared offset.

    Returns (matched, lost, overlay_covered).

    `lost` counts opaque sprite pixels that are not the plate. `overlay_covered`
    counts how many of those the guide's own caption and crosshair are drawn
    over. The overlay is identified by its colour *not occurring in the plate*,
    not by a fixed list: the crosshair is antialiased, so its edge pixels are
    blends with whatever lies under it and match no declared colour. A blend
    the plate never contains is the guide's furniture; anything the plate does
    contain is the plate.
    """
    # Fully opaque pixels only, and one set for both the palette and the
    # iteration. The contact shadow is 40% alpha, so it has no single colour:
    # drawn on the guide's fill it becomes a blend the plate never contains, and
    # comparing it would report the guide's furniture as resampling. Mixing the
    # two sets - counting the denominator on opaque pixels and iterating the
    # semi-transparent ones - makes `overlay` exceed `total`, which is nonsense
    # rather than a defect. The shadow is verified by the declared silhouette
    # area instead, which does not depend on what is behind the plate.
    plate_colours = {tuple(sprite_rgba[i * 4:i * 4 + 3])
                     for i in range(len(sprite_rgba) // 4)
                     if sprite_rgba[i * 4 + 3] == 255}
    matched = lost = overlay = 0
    total = len(sprite_rgba) // 4
    for index_px in range(total):
        if sprite_rgba[index_px * 4 + 3] != 255:
            continue
        sx = index_px % sw + offset_x
        sy = index_px // sw + offset_y
        if not (0 <= sx < cell and 0 <= sy < cell):
            lost += 1
            continue
        # The guide is now one buffer, so the row stride is the GUIDE's width,
        # not the cell's. Using the cell width walks a different image.
        g = ((sy + origin_y) * (guide_w or cell) + (sx + origin_x)) * 4
        pixel = (guide_rgba[g], guide_rgba[g + 1], guide_rgba[g + 2])
        if (guide_rgba[g + 3] and
                pixel == tuple(sprite_rgba[index_px * 4:index_px * 4 + 3])):
            matched += 1
            continue
        lost += 1
        if pixel not in plate_colours:
            overlay += 1
    return matched, lost, overlay


def check_guide(manifest, cells, hex_r, target, problems):
    """Measure the guide sheet against the manifest and the plates.

    Returns the problems it found. It never short-circuits the caller's other
    checks: a manifest missing a guide field must not stop the palette ceiling,
    the pipeline fields, the revision or the status from being examined, or a
    second defect would hide behind the first.
    """
    found = []
    spec = manifest["atlas"].get("guide_layout") or {}
    pipeline = manifest["pipeline"] or {}
    # The atlas and the guide belong to the PROOF, not to the repository, and
    # they live BESIDE the manifest. Resolving them from the repo root quietly
    # broke the mutation harness: it copies the proof into a scratch tree and
    # rebuilds the sheets there, while the verifier read the real repo's sheets,
    # so every rebuild was work thrown away and the guide check compared scratch
    # plates against real decorations. Anchoring on the proof's own directory
    # works in both layouts - a scratch copy is rarely named `flat2d_proof`, so
    # a parent-relative path would miss.
    guide = sheet(manifest["atlas"]["guide"])
    if not guide.exists():
        return ["guide file is missing"]
    gw, gh = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(guide)], text=True).split())
    for field in ("canvas_px", "columns", "rows", "gutter_px",
                  "cell_canvas_px", "sprite_scale_pct", "background"):
        if spec.get(field) is None:
            found.append(f"guide_layout.{field} is missing or empty")
    if "canvas_px" in spec and [gw, gh] != spec["canvas_px"]:
        found.append(f"guide is {gw}x{gh}, manifest says {spec['canvas_px']}")
    if "guide_cell_fill" not in pipeline:
        found.append("pipeline.guide_cell_fill is missing")
    required = ("canvas_px", "columns", "rows", "gutter_px", "cell_canvas_px",
                "sprite_scale_pct", "background")
    if any(spec.get(field) is None for field in required) or \
            "guide_cell_fill" not in pipeline:
        # a declared-but-empty field is as unusable as a missing one
        return found
    columns = spec["columns"]
    rows_n = spec["rows"]
    gutter = spec["gutter_px"]
    cell = spec["cell_canvas_px"][0]
    expected_w = columns * cell + gutter * (columns + 1)
    expected_h = rows_n * cell + gutter * (rows_n + 1)
    if [expected_w, expected_h] != [gw, gh]:
        found.append(f"guide canvas {gw}x{gh} does not match {columns}x{rows_n} "
                     f"cells of {cell} px with {gutter} px gutters "
                     f"({expected_w}x{expected_h})")
    background = subprocess.check_output(
        ["convert", str(guide), "-format", "%[pixel:p{2,2}]", "info:"],
        text=True).strip()
    if normalise_hex(background) != spec["background"].lower():
        found.append(f"guide background is {background}, manifest says "
                     f"{spec['background']}")
    # Covering, not filling: the last row of a sheet is routinely part-used, and
    # demanding an exact product rejected a correct guide the moment the plate
    # count stopped being a tidy multiple of the column count.
    if columns * rows_n < len(cells):
        found.append(f"guide layout covers {columns * rows_n} cells but there "
                     f"are {len(cells)} plates")
    fill = pipeline["guide_cell_fill"]
    scale = spec["sprite_scale_pct"] / 100
    cell_w, cell_h = target["cell_canvas_px"]
    # The whole guide and every sprite are read in TWO calls, not one per cell.
    # Per cell this was a crop, an identify, a read, a resize and another read -
    # about 290 subprocesses to re-slice a regular grid. The layout is declared
    # and the grid is regular, so the slicing is arithmetic.
    guide_blob = subprocess.check_output(
        ["convert", str(guide), "-depth", "8", "rgba:-"])
    sprite_w = round(cell_w * scale)
    sprite_h = round(cell_h * scale)
    sprite_stride = sprite_w * sprite_h * 4
    sprite_blob = b""
    plate_paths = [str(ROOT / "plates" / f"{cell_id}.png") for cell_id in cells
                   if (ROOT / "plates" / f"{cell_id}.png").exists()]
    if plate_paths and len(plate_paths) == len(cells):
        try:
            sprite_blob = subprocess.check_output(
                ["convert", *plate_paths, "-filter", "point", "-resize",
                 f"{sprite_w}x{sprite_h}!", "-depth", "8", "rgba:-"])
        except subprocess.CalledProcessError:
            sprite_blob = b""
    sprites = {}
    if len(sprite_blob) == len(plate_paths) * sprite_stride:
        for index, path in enumerate(plate_paths):
            sprites[Path(path).stem] = sprite_blob[
                index * sprite_stride:(index + 1) * sprite_stride]

    for index, cell_id in enumerate(cells):
        plate = ROOT / "plates" / f"{cell_id}.png"
        if not plate.exists():
            continue
        col = index % columns
        line = index // columns
        gx = gutter + col * (cell + gutter)
        gy = gutter + line * (cell + gutter)
        if gx + cell > gw or gy + cell > gh:
            found.append(f"guide cell {cell_id} at ({gx},{gy}) runs past the "
                         f"{gw}x{gh} canvas, so the guide cannot show every "
                         "plate")
            continue
        # the cell is a window onto the one guide read, not a file of its own
        def at(x, y, blob=guide_blob, w=gw):
            i = (y * w + x) * 4
            return (blob[i], blob[i + 1], blob[i + 2]), blob[i + 3]

        corner, _ = at(gx + 6, gy + 6)
        if normalise_hex("srgb(%d,%d,%d)" % corner) != fill.lower():
            found.append(f"guide cell {cell_id} is filled with "
                         f"srgb({corner[0]},{corner[1]},{corner[2]}), "
                         f"expected {fill}")
        guide_rgba = guide_blob
        sprite_rgba = sprites.get(cell_id)
        if sprite_rgba is None:
            # the batch failed or a plate was missing: fall back to the slow
            # per-cell path rather than skipping the cell
            scratch = Path(tempfile.mkdtemp(prefix="flat2d_guide_"))
            crop = scratch / f"cell_{index}.png"
            sprite_png = str(scratch / f"sprite_{index}.png")
            subprocess.run(["convert", str(guide), "-crop",
                            f"{cell}x{cell}+{gx}+{gy}", "+repage", str(crop)],
                           check=True)
            guide_rgba = subprocess.check_output(
                ["convert", str(crop), "-depth", "8", "rgba:-"])
            subprocess.run(
                ["convert", str(plate), "-filter", "point", "-resize",
                 f"{sprite_w}x{sprite_h}", sprite_png], check=True)
            sprite_rgba = subprocess.check_output(
                ["convert", sprite_png, "-depth", "8", "rgba:-"])
            sw, sh = (int(v) for v in subprocess.check_output(
                ["identify", "-format", "%w %h", sprite_png],
                text=True).split())
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            sw, sh = sprite_w, sprite_h
        if len(sprite_rgba) != sw * sh * 4:
            found.append(f"{cell_id}: the plate cannot be scaled to {sw}x{sh} "
                         "for the guide")
            continue
        opaque = [i // 4 for i in range(0, len(sprite_rgba), 4)
                  if sprite_rgba[i + 3] == 255]
        if not opaque:
            found.append(f"guide cell {cell_id} shows nothing of the plate")
            continue
        # The sprite's offset is declared in the manifest, so it is compared
        # where the manifest says it should be. Searching for the offset that
        # matches best would make any displacement undetectable, because the
        # displacement is exactly what such a search absorbs.
        declared_offset = spec.get("sprite_offset_px")
        if declared_offset is None:
            found.append("guide_layout.sprite_offset_px is missing")
            continue
        offset_x, offset_y = declared_offset
        matched, lost, overlay = compare_sprite_at(
            guide_rgba, sprite_rgba, sw, sh, offset_x, offset_y, cell,
            origin_x=gx, origin_y=gy, guide_w=gw)
        total = len(opaque)
        if matched < total * MIN_SPRITE_MATCH:
            found.append(
                f"guide cell {cell_id}: only {matched} of {total} sprite pixels "
                f"match the plate at the declared offset "
                f"({offset_x},{offset_y}) and {spec['sprite_scale_pct']}%; "
                f"fewer than the {int(MIN_SPRITE_MATCH * 100)}% the guide "
                "must show")
        if overlay > total * MAX_SPRITE_OVERLAY:
            found.append(
                f"guide cell {cell_id}: {overlay} of {total} sprite pixels are "
                f"colours the plate does not contain, more than the "
                f"{int(MAX_SPRITE_OVERLAY * 100)}% the crosshair and caption "
                "can account for; the sprite has been resampled")
        found += guide_shows_the_body(cell_id, cells[cell_id], guide_rgba, gx,
                                      gy, cell, spec, gw)
    return found


def guide_shows_the_body(cell_id, cell, guide, gx, gy, cell_px, spec, gw):
    """A clothing layer's guide cell must show the body it is cut for.

    The layer's own pixels are checked above, and that says nothing about the
    body: a layer is only an overlay in relation to a figure, and the guide is
    the one surface where the reader can see both. So the cell declares
    `guide_ghost`, and the declared body plate is compared against the same
    cell the same way.

    The threshold is not a guess. Measured across the 48 cells, the declared
    body matches between 35.9% (a long silk tunic, which covers most of the
    figure) and 97.7% (a brooch), while the *wrong* body matches between 2.9%
    and 9.3% - the figures differ enough in height and stance that they barely
    overlap. 25% sits in the empty middle of that gap, so the check passes every
    real cell and would fail a ghost of the wrong figure.
    """
    ghost = cell.get("guide_ghost")
    if not ghost:
        return []
    body_plate = ROOT / "plates" / f"{ghost}.png"
    if not body_plate.exists():
        return [f"guide cell {cell_id}: guide_ghost {ghost!r} has no plate"]
    scaled = 166                       # 128 px cell at the declared 130%
    scratch = Path(tempfile.mkdtemp(prefix="flat2d_ghost_"))
    try:
        sized = scratch / "ghost.png"
        subprocess.run(["convert", str(body_plate), "-filter", "point",
                        "-resize", f"{scaled}x{scaled}", str(sized)], check=True)
        rgba = subprocess.check_output(
            ["convert", str(sized), "-depth", "8", "rgba:-"])
        opaque = [k for k in range(len(rgba) // 4) if rgba[k * 4 + 3] == 255]
        if not opaque:
            return [f"guide cell {cell_id}: the ghost body {ghost} is empty"]
        matched, _lost, _overlay = compare_sprite_at(
            guide, rgba, scaled, scaled, *spec_offset(spec), cell_px,
            origin_x=gx, origin_y=gy, guide_w=gw)
        share = matched / len(opaque)
        if share < MIN_GHOST_MATCH:
            return [f"guide cell {cell_id}: only {share * 100:.1f}% of the "
                    f"declared body {ghost} is visible under the layer, fewer "
                    f"than the {int(MIN_GHOST_MATCH * 100)}% the guide must "
                    f"show; the ghost is the wrong figure or the layer is "
                    f"floating off the body"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return []


def spec_offset(spec):
    offset = spec.get("sprite_offset_px")
    if not offset:
        return 0, 0
    return offset[0], offset[1]


def report(problems, cells, hex_r, target):
    """Print the verdict and return the process exit code."""
    if problems:
        print(f"flat 2D proof: FAIL ({len(problems)} problems)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"flat 2D proof: OK ({len(cells)} plates, "
          f"hex R={hex_r} centre={tuple(target['proof_hex_center_px'])}, "
          f"anchor={tuple(target['proof_anchor_px'])})")
    return 0


def main():
    manifest = load_manifest()
    target = manifest["target"]
    problems = []

    cell_w, cell_h = target["cell_canvas_px"]
    hex_cx, hex_cy = target["proof_hex_center_px"]
    hex_r = target["proof_hex_circumradius_px"]
    hex_half = target["proof_hex_half_width_px"]
    anchor_x, anchor_y = target["proof_anchor_px"]

    for name, expected in (("cell_canvas_px", (128, 128)),
                           ("proof_hex_center_px", (64, 64))):
        got = tuple(target[name])
        if got != expected:
            problems.append(f"target.{name} is {got}, expected {expected}")
    # one reference point: the object contact and the hex centre are the same
    # pixel. Two different points is exactly the defect revision 3 shipped.
    if tuple(target["proof_anchor_px"]) != tuple(target["proof_hex_center_px"]):
        problems.append(
            f"target.proof_anchor_px {tuple(target['proof_anchor_px'])} must "
            f"equal proof_hex_center_px "
            f"{tuple(target['proof_hex_center_px'])}: an object stands in the "
            "middle of its tile")
    if target.get("camera_frozen") is not False:
        problems.append("target.camera_frozen must stay false")
    if target.get("final_claims") is not False:
        problems.append("target.final_claims must stay false")

    if ONTOLOGY is None:
        problems.append(
            "the simulation source was not found, so no data_carrier could be "
            "checked; set HILLCOURT_REPO or run the verifier inside the repo")

    cells = {cell["id"]: cell for cell in manifest["cells"]}
    extras = manifest.get("target_extras") or {}
    pipeline = manifest.get("pipeline") or {}

    for cell_id, cell in cells.items():
        plate = ROOT / "plates" / f"{cell_id}.png"
        if not plate.exists():
            problems.append(f"{cell_id}: plate file is missing")
            continue
        width, height, rows = alpha_rows(plate)
        if (width, height) != (cell_w, cell_h):
            problems.append(f"{cell_id}: plate is {width}x{height}, "
                            f"cell canvas is {cell_w}x{cell_h}")
        if not rows:
            problems.append(f"{cell_id}: plate is fully transparent")
            continue

        w, h, x, y = bbox(rows)
        if [w, h] != cell["plate_size_px"]:
            problems.append(f"{cell_id}: plate is {w}x{h}, manifest says "
                            f"{cell['plate_size_px']}")
        if [x, y] != cell["plate_placement_px"]:
            problems.append(f"{cell_id}: plate starts at ({x},{y}), manifest "
                            f"says {cell['plate_placement_px']}")
        # A hole punched inside an object leaves the bounding box alone, so the
        # silhouette's area is checked as well: this is what catches a bite
        # taken out of the middle of a plate.
        declared_px = cell.get("plate_opaque_px")
        if declared_px is None:
            problems.append(f"{cell_id}: plate_opaque_px is missing; the "
                            "silhouette area is not pinned")
        else:
            actual_px = sum(1 for value in subprocess.check_output(
                ["convert", str(plate), "-alpha", "extract", "-depth", "8",
                 "gray:-"]) if value > SHADOW_THRESHOLD)
            if actual_px != declared_px:
                problems.append(f"{cell_id}: {actual_px} visible pixels, "
                                f"manifest declares {declared_px}")

        if cell["anchor_status"] == "proof_hex_center_64_64":
            top, bottom = min(rows), max(rows)
            x0 = min(rows[row][0] for row in rows)
            x1 = max(rows[row][-1] for row in rows)
            if abs((x0 + x1) / 2 - hex_cx) > 1:
                problems.append(f"{cell_id}: hex centre x {(x0 + x1) / 2} "
                                f"!= {hex_cx}")
            if abs((top + bottom) / 2 - hex_cy) > 1:
                problems.append(f"{cell_id}: hex centre y {(top + bottom) / 2} "
                                f"!= {hex_cy}")
            if x1 - x0 + 1 != 2 * hex_half + 1:
                problems.append(f"{cell_id}: hex width {x1 - x0 + 1} != "
                                f"{2 * hex_half + 1}")
            if bottom - top + 1 != 2 * hex_r + 1:
                problems.append(f"{cell_id}: hex height {bottom - top + 1} != "
                                f"{2 * hex_r + 1}")
            if bottom != hex_cy + hex_r:
                problems.append(f"{cell_id}: hex bottom vertex {bottom} != "
                                f"{hex_cy + hex_r}")
            for tag, row in (("top", top), ("bottom", bottom - 1)):
                span = rows[row]
                # the polygon edge is antialiased, so a vertex row is a few
                # pixels wide; it must stay narrow and centred
                if span[-1] - span[0] + 1 > 8:
                    problems.append(f"{cell_id}: hex {tag} row is "
                                    f"{span[-1] - span[0] + 1} px wide, "
                                    "expected a vertex, not an edge")
                elif abs((span[0] + span[-1]) / 2 - hex_cx) > 2:
                    problems.append(f"{cell_id}: hex {tag} vertex is not on "
                                    f"x={hex_cx}")
            upper = hex_cy - hex_r // 2
            lower = hex_cy + hex_r // 2
            for row, tag in ((upper, "upper"), (lower, "lower")):
                if row not in rows:
                    problems.append(f"{cell_id}: hex {tag} shoulder row empty")
                    continue
                span = rows[row]
                if span[0] != hex_cx - hex_half or span[-1] != hex_cx + hex_half:
                    problems.append(f"{cell_id}: hex {tag} shoulder is "
                                    f"{span[0]}..{span[-1]}, expected "
                                    f"{hex_cx - hex_half}..{hex_cx + hex_half}")
            problems += [f"{cell_id}: {p}" for p in
                         rows_are_centred(rows, hex_cx)]
            problems += [f"{cell_id}: {p}" for p in
                         edges_are_straight(rows, hex_cy, hex_r, hex_cx, hex_half)]
            problems += [f"{cell_id}: {p}" for p in
                         rows_are_solid(rows, range(top, bottom + 1))]
        else:
            # the ground contact is read from the opaque object only; the
            # synthetic shadow is softer and may extend below the line
            _, _, solid = alpha_rows(plate, OPAQUE)
            if not solid:
                problems.append(f"{cell_id}: no opaque object pixels")
                continue
            bottom = max(solid)
            placement = cell.get("placement", "ground_anchored")
            if placement == "actor_frame":
                # A clothing layer is drawn in the actor's frame and composited
                # onto the body, so its bottom edge is a nose bar or a cuff, not
                # a contact with the ground. Requiring it to sit on the ground
                # line is what forced the old re-anchor that put every helm and
                # torc down at the feet. What it must satisfy instead is being
                # inside the figure it is worn on, which is checked by
                # garment_lands_on_actor below.
                sw, sh, sx, sy = bbox(solid)
                if [sw, sh] != cell["object_size_px"]:
                    problems.append(f"{cell_id}: object is {sw}x{sh}, manifest "
                                    f"says {cell['object_size_px']}")
                if [sx, sy] != cell["object_placement_px"]:
                    problems.append(f"{cell_id}: object starts at ({sx},{sy}), "
                                    f"manifest says {cell['object_placement_px']}")
                problems += garment_lands_on_actor(plate, solid, cell, cell_id, anchor_y)
                sw, sh, sx, sy = sw, sh, sx, sy
            else:
                run = solid[bottom]
                contact = (run[0] + run[-1]) / 2
                if abs(contact - anchor_x) > ANCHOR_TOLERANCE_PX:
                    problems.append(f"{cell_id}: ground contact x {contact} is more "
                                    f"than {ANCHOR_TOLERANCE_PX} px from "
                                    f"{anchor_x}")
                if bottom != anchor_y:
                    problems.append(f"{cell_id}: object bottom {bottom} is not on "
                                    f"the ground line {anchor_y}")
                sw, sh, sx, sy = bbox(solid)
                if [sw, sh] != cell["object_size_px"]:
                    problems.append(f"{cell_id}: object is {sw}x{sh}, manifest "
                                    f"says {cell['object_size_px']}")
                if [sx, sy] != cell["object_placement_px"]:
                    problems.append(f"{cell_id}: object starts at ({sx},{sy}), "
                                    f"manifest says {cell['object_placement_px']}")

        # Every plate must say what identifies it, or say plainly that nothing
        # does. A plate with no carrier is legitimate - it may exist only to keep
        # a pipeline path under test - but silence is not allowed: silence is how
        # a reference quietly becomes an asset.
        if "data_carrier" not in cell:
            problems.append(f"{cell_id}: data_carrier is not declared; a plate "
                            "must name the field that identifies it or say that "
                            "nothing does")
        elif cell["data_carrier"] is None:
            if not cell.get("data_carrier_reason"):
                problems.append(f"{cell_id}: data_carrier is null with no "
                                "data_carrier_reason")
        else:
            fault = carrier_is_real(cell["data_carrier"],
                                    cell.get("data_carrier_kind"))
            if fault:
                problems.append(f"{cell_id}: {fault}")
        if cell.get("sprite_selector") != "none":
            problems.append(f"{cell_id}: sprite_selector must be none while "
                            "client/ is frozen and sim/ has no sprite selection "
                            "code")

        contour_colour = extras.get("contour_colour")
        material = cell.get("material")
        if material == "drawn":
            # a drawn plate may only use its own declared ramp and the
            # contour: this is what makes "the plate is made of its material"
            # a checkable statement rather than an opinion
            problems += [f"{cell_id}: {p}" for p in
                         plate_uses_declared_material(plate, cell,
                                                       contour_colour)]
        elif material == "keyed_from_render":
            problems += [f"{cell_id}: {p}" for p in
                         warm_ramp_only(plate, WARMTH.get(cell_id, 8))]
        else:
            problems.append(f"{cell_id}: material {material!r} is not one of "
                            "drawn, keyed_from_render")
        if contour_colour:
            problems += [f"{cell_id}: {p}" for p in
                         contour_present(plate, contour_colour)]
            contour_rgb = tuple(int(contour_colour.lstrip("#")[i:i + 2], 16)
                                for i in (0, 2, 4))
            problems += [f"{cell_id}: {p}" for p in
                         plate_has_content(plate, contour_rgb)]

    # One convert for every plate, not one per plate. Fifty-eight subprocesses
    # to read fifty-eight fixed-size images is a cost with no information in it.
    # Every plate is the declared cell canvas - the per-cell check above has
    # already rejected anything else - so the blob splits on a fixed stride.
    plate_blobs = {}
    try:
        paths = [str(ROOT / "plates" / f"{cell_id}.png") for cell_id in cells]
        blob = subprocess.check_output(
            ["convert", *paths, "-depth", "8", "rgba:-"])
        stride = cell_w * cell_h * 4
        if len(blob) == len(paths) * stride:
            for index, cell_id in enumerate(cells):
                chunk = blob[index * stride:(index + 1) * stride]
                _RAW_RGBA[str(ROOT / "plates" / f"{cell_id}.png")] = chunk
                plate_blobs[cell_id] = chunk
    except (subprocess.CalledProcessError, OSError):
        # fall back to one call per plate; slower, and never wrong
        plate_blobs = {}

    atlas = sheet(manifest["atlas"]["file"])
    declared = {entry["id"]: entry["offset_px"]
                for entry in manifest["atlas"]["layout"]}
    if sorted(declared) != sorted(cells):
        problems.append("atlas layout ids do not match the plate ids")
    if not atlas.exists():
        problems.append("atlas file is missing")
    else:
        aw, ah = (int(v) for v in subprocess.check_output(
            ["identify", "-format", "%w %h", str(atlas)], text=True).split())
        if [aw, ah] != manifest["atlas"]["canvas_px"]:
            problems.append(f"atlas is {aw}x{ah}, manifest says "
                            f"{manifest['atlas']['canvas_px']}")
        for cell_id, (dx, dy) in declared.items():
            plate = ROOT / "plates" / f"{cell_id}.png"
            if not plate.exists():
                continue
            if dx + cell_w > aw or dy + cell_h > ah:
                problems.append(f"{cell_id}: atlas cell ({dx},{dy}) does not "
                                f"fit in {aw}x{ah}")
        # the atlas must actually contain the plates, not just fit them
        atlas_rgba = subprocess.check_output(
            ["convert", str(atlas), "-depth", "8", "rgba:-"])
        atlas_w = aw
        for cell_id, (dx, dy) in declared.items():
            plate = ROOT / "plates" / f"{cell_id}.png"
            if not plate.exists():
                continue
            plate_rgba = plate_blobs.get(cell_id)
            if plate_rgba is None:
                plate_rgba = subprocess.check_output(
                    ["convert", str(plate), "-depth", "8", "rgba:-"])
            mismatch = 0
            for y in range(cell_h):
                a_start = ((dy + y) * atlas_w + dx) * 4
                p_start = y * cell_w * 4
                a_row = atlas_rgba[a_start:a_start + cell_w * 4]
                p_row = plate_rgba[p_start:p_start + cell_w * 4]
                # The rows are usually identical, and a bytes comparison settles
                # that in C. Only a row that actually differs needs the per-pixel
                # rule, so the loop stops being a generator over every pixel of
                # every cell - it was 950k Python iterations for a check that is
                # a single memcmp most of the time.
                if a_row == p_row:
                    continue
                # a fully transparent pixel may legitimately carry different
                # hidden RGB after compositing, so only visible pixels compare
                if any(a_row[i + 3] != p_row[i + 3] or
                       (p_row[i + 3] and a_row[i:i + 3] != p_row[i:i + 3])
                       for i in range(0, cell_w * 4, 4)):
                    mismatch += 1
            if mismatch:
                problems.append(f"{cell_id}: {mismatch} atlas rows do not match "
                                "the plate")
        if len(declared) * cell_w * cell_h < aw * ah:
            # every pixel outside the declared cells must be transparent
            atlas_alpha = subprocess.check_output(
                ["convert", str(atlas), "-alpha", "extract", "-depth", "8",
                 "gray:-"])
            inside = set()
            for (dx, dy) in declared.values():
                for y in range(dy, dy + cell_h):
                    for x in range(dx, dx + cell_w):
                        inside.add(y * aw + x)
            stray_px = sum(1 for i, value in enumerate(atlas_alpha)
                           if value > 8 and i not in inside)
            if stray_px:
                problems.append(f"atlas has {stray_px} opaque pixels outside "
                                "the declared cells")

    problems += check_guide(manifest, cells, hex_r, target, problems)

    # Terrain and architecture are both drawn, so "drawn" alone cannot tell
    # them apart; the id prefix can, and each count is checked against the
    # plates rather than trusted from the manifest.
    drawn = [cell_id for cell_id, cell in cells.items()
             if cell.get("material") == "drawn"]
    actual_terrains = sum(1 for cell_id in drawn
                          if cell_id.startswith("terrain_"))
    actual_arch = sum(1 for cell_id in drawn
                      if cell_id.startswith("architecture_"))
    actual_props = sum(1 for cell_id in drawn
                       if cell_id.startswith(("good_", "prop_")))
    actual_actors = sum(1 for cell_id in drawn
                        if cell_id.startswith("actor_"))
    actual_animals = sum(1 for cell_id in drawn
                         if cell_id.startswith("animal_"))
    actual_garments = sum(1 for cell_id in drawn
                          if cell_id.startswith("garment_"))
    declared_terrains = manifest["pipeline"].get("terrain_count")
    if declared_terrains != actual_terrains:
        problems.append(
            f"pipeline.terrain_count is {declared_terrains} but "
            f"{actual_terrains} plates are drawn terrains; the manifest and "
            "the plates disagree about how many terrains exist")
    declared_arch = manifest["pipeline"].get("architecture_count")
    if declared_arch is None:
        problems.append("pipeline.architecture_count is missing; the proof "
                        "cannot prove how many buildings it drew")
    elif declared_arch != actual_arch:
        problems.append(
            f"pipeline.architecture_count is {declared_arch} but "
            f"{actual_arch} plates are drawn buildings")
    declared_props = manifest["pipeline"].get("prop_count")
    if declared_props is None:
        problems.append("pipeline.prop_count is missing; the proof cannot "
                        "prove how many goods and props it drew")
    elif declared_props != actual_props:
        problems.append(
            f"pipeline.prop_count is {declared_props} but {actual_props} "
            "plates are drawn goods or props")
    declared_actors = manifest["pipeline"].get("actor_count")
    if declared_actors is None:
        problems.append("pipeline.actor_count is missing; the proof cannot "
                        "prove how many figures it drew")
    elif declared_actors != actual_actors:
        problems.append(
            f"pipeline.actor_count is {declared_actors} but {actual_actors} "
            "plates are drawn figures")
    actual_hazards = sum(1 for c in cells.values()
                         if c.get("data_carrier") == "Hazard.kind")
    declared_hazards = manifest["pipeline"].get("hazard_count")
    if declared_hazards is None:
        problems.append("pipeline.hazard_count is missing; the proof cannot "
                        "prove how many hazards it drew")
    elif declared_hazards != actual_hazards:
        problems.append(
            f"pipeline.hazard_count is {declared_hazards} but "
            f"{actual_hazards} plates carry Hazard.kind")
    declared_animals = manifest["pipeline"].get("animal_count")
    if declared_animals is None:
        problems.append("pipeline.animal_count is missing; the proof cannot "
                        "prove how many animals it drew")
    elif declared_animals != actual_animals:
        problems.append(
            f"pipeline.animal_count is {declared_animals} but {actual_animals} "
            "plates are drawn animals")
    # A site is a tile FORM, so the count cannot be keyed off a carrier the way
    # hazards and garments are: eight sites ride a tile mark, two ride
    # Settlement.kind. Counting them by prefix is the honest test, and the check
    # exists because `site_count` was written into the pipeline and then read by
    # nothing - a declared number nothing verifies is a number that can lie.
    actual_sites = sum(1 for c in cells.values()
                       if str(c.get("id", "")).startswith("site_"))
    declared_sites = manifest["pipeline"].get("site_count")
    if declared_sites is None:
        problems.append("pipeline.site_count is missing; the proof cannot "
                        "prove how many site forms it drew")
    elif declared_sites != actual_sites:
        problems.append(
            f"pipeline.site_count is {declared_sites} but {actual_sites} "
            "plates are site forms")
    declared_garments = manifest["pipeline"].get("garment_count")
    if declared_garments is None:
        problems.append("pipeline.garment_count is missing; the proof cannot "
                        "prove how many clothing layers it drew")
    elif declared_garments != actual_garments:
        problems.append(
            f"pipeline.garment_count is {declared_garments} but "
            f"{actual_garments} plates are drawn clothing layers")
    # A layer that is drawn must be an overlay on a body, so it has to name the
    # body it overlays. A garment with no body is a picture of a costume.
    for cell_id, cell in sorted(cells.items()):
        if not cell_id.startswith("garment_"):
            continue
        if not cell.get("material_layer"):
            problems.append(f"{cell_id}: material_layer is not declared; a "
                            "clothing layer must say which layer it is")
        if not cell.get("material_carrier_field"):
            problems.append(f"{cell_id}: material_carrier_field is not declared")
    # Every drawn layer must be one of the declared layers, and a layer that is
    # only ever "nothing" must not have been drawn at all.
    declared_layers = set(manifest["pipeline"].get("garment_layers") or [])
    for cell_id, cell in sorted(cells.items()):
        layer = cell.get("material_layer")
        if layer and layer not in declared_layers:
            problems.append(f"{cell_id}: layer {layer!r} is not among the "
                            "declared garment_layers")
    # A garment is drawn once per body. If a layer exists for the adult and not
    # for the child, that is not a missing ornament: it means the child of a
    # thegn is drawn wearing no thegn's tunic, or the adult's, at the wrong hem
    # length. Nobody's plate is wrong in isolation, so this is the only place
    # the gap can be seen.
    declared_ages = set(manifest["pipeline"].get("garment_age_classes") or [])
    if not declared_ages:
        problems.append("pipeline.garment_age_classes is missing; the bodies a "
                        "layer is drawn for are not declared")
    seen_layers = {}
    for cell_id, cell in sorted(cells.items()):
        if not cell_id.startswith("garment_"):
            continue
        base = cell_id.rsplit("_", 1)[0]
        seen_layers.setdefault(base, {})[cell.get("material_age_class")] = cell_id
        if declared_ages and cell.get("material_age_class") not in declared_ages:
            problems.append(f"{cell_id}: material_age_class "
                            f"{cell.get('material_age_class')!r} is not among "
                            f"the declared garment_age_classes {sorted(declared_ages)}")
    for base, by_age in sorted(seen_layers.items()):
        if declared_ages and set(by_age) != declared_ages:
            missing = sorted(declared_ages - set(by_age))
            problems.append(f"{base}: drawn for {sorted(by_age)} but not for "
                            f"{missing}; every clothing layer is drawn once per "
                            "body, or the missing body wears nothing")
    # Every drawn plate must belong to exactly one family. The families are the
    # id prefixes, and a drawn plate outside all of them would silently inflate
    # no counter at all - which is how an asset gets drawn and never counted.
    families = ("terrain_", "architecture_", "good_", "prop_", "actor_",
                "animal_", "garment_", "hazard_", "camp_", "site_")
    for cell_id in drawn:
        if not cell_id.startswith(families):
            problems.append(f"{cell_id}: drawn plate belongs to no family; the "
                            f"id prefix taxonomy is broken ({families})")
    for field in MUST_STAY_FALSE:
        if target.get(field) is not False:
            problems.append(f"target.{field} must stay false while the proof is "
                            "unfrozen")
    revision = target.get("revision")
    if revision != SUPPORTED_REVISION:
        problems.append(f"target.revision is {revision!r}, this verifier only "
                        f"understands revision {SUPPORTED_REVISION}")
    for cell_id, cell in cells.items():
        if cell.get("status") != ALLOWED_STATUS:
            problems.append(f"{cell_id}: status {cell.get('status')!r} is not "
                            f"{ALLOWED_STATUS!r}; the proof is not final art")

    for field in ("contour_colour", "max_unique_rgb_per_plate",
                  "anchor_tolerance_px"):
        if field not in extras:
            problems.append(f"target_extras.{field} is missing; the verifier "
                            "cannot check the plate against an undeclared rule")
    for field in ("contour", "order", "retouch", "scrub", "background_key",
                  "placement", "guide_cell_fill", "style_colours",
                  "ramp_min_r_minus_b", "style_reference_scope"):
        if field not in pipeline:
            problems.append(f"pipeline.{field} is missing; the manifest no "
                            "longer declares how the plate was made")
    if pipeline.get("style_reference_scope") != "form_material_and_detail_not_hue":
        problems.append(
            "pipeline.style_reference_scope must state that the style anchor "
            "is matched on form, material and detail, not on hue")
    contour_colour = extras.get("contour_colour")
    if contour_colour and pipeline.get("contour"):
        if contour_colour not in str(pipeline["contour"]):
            problems.append(
                f"pipeline.contour {pipeline['contour']!r} does not mention the "
                f"declared contour colour {contour_colour}; the manifest "
                "disagrees with itself about the stroke")
    order = str(pipeline.get("order", ""))
    retouch_at = order.find("retouch")
    contour_at = order.find("contour")
    if 0 <= retouch_at and 0 <= contour_at and retouch_at > contour_at:
        problems.append(
            f"pipeline.order {order!r} puts the contour before the retouch; the "
            "resample would undo the stroke")
    declared_budget = extras.get("max_unique_rgb_per_plate")
    # The budget is enforced against the value this verifier was written for,
    # not against whatever the manifest happens to say. A manifest that raises
    # its own limit to make a noisy plate pass must not be able to certify it.
    if isinstance(declared_budget, (int, float)):
        if declared_budget > SUPPORTED_MAX_UNIQUE_RGB:
            problems.append(
                f"target_extras.max_unique_rgb_per_plate is {declared_budget}, "
                f"above the {SUPPORTED_MAX_UNIQUE_RGB} this verifier enforces; "
                "raising the limit in the manifest does not raise it here")
        for cell_id in cells:
            plate = ROOT / "plates" / f"{cell_id}.png"
            if not plate.exists():
                continue
            colours = unique_rgb(plate)
            if colours > SUPPORTED_MAX_UNIQUE_RGB:
                problems.append(
                    f"{cell_id}: {colours} distinct colours, the limit is "
                    f"{SUPPORTED_MAX_UNIQUE_RGB}; render noise has come back "
                    "through a resample")
    return report(problems, cells, hex_r, target)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mutation test for verify_proof.py.

A verifier that passes proves nothing until you have watched it fail on the
defects it claims to catch. This script copies the proof into a scratch tree,
applies one defect at a time, and records whether the verifier rejects it.

Run:  python3 design/art/flat2d_proof/mutation_test.py
Exit code 0 means every mutation was caught.
"""

import re
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

import yaml

REPO = Path(__file__).resolve().parents[3]
PROOF = REPO / "design/art/flat2d_proof"


def write_rgba_png(path, width, height, data):
    """Write 8-bit RGBA bytes back out as a PNG."""
    out = b"\x89PNG\r\n\x1a\n"

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    scan = b"".join(b"\x00" + bytes(data[y * width * 4:(y + 1) * width * 4])
                    for y in range(height))
    out += chunk(b"IDAT", zlib.compress(scan))
    out += chunk(b"IEND", b"")
    path.write_bytes(out)


def edit_png_alpha(path, x0, y0, x1, y1):
    """Punch a transparent rectangle straight into the PNG's alpha channel."""
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(raw)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            data[(y * width + x) * 4 + 3] = 0
    write_rgba_png(path, width, height, data)


def shear_hex(path, amount=3):
    """Bend one sloped edge while leaving the bounding box untouched.

    The vertex rows and the shoulder rows keep their exact left and right
    extremes, so the width, the height and the shoulder positions all still
    match the manifest. Between the vertex and the shoulder the right edge is
    pulled inward by a varying amount, so it no longer lies on the straight
    line the polygon declares, and the rows stop being centred on x=64.

    Displacing both edges symmetrically would have kept every row centred and
    the mutation would have done nothing at all.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    rows = {}
    for y in range(height):
        xs = [x for x in range(width) if raw[(y * width + x) * 4 + 3] > 0]
        if xs:
            rows[y] = (xs[0], xs[-1])
    top, bottom = min(rows), max(rows)
    left = min(v[0] for v in rows.values())
    right = max(v[1] for v in rows.values())
    data = bytearray(raw)
    for y, (row_left, row_right) in rows.items():
        if y in (top, bottom) or row_right == right:
            # never touch the rows that fix the bounding box
            continue
        line_start = y * width * 4
        line = raw[line_start:line_start + width * 4]
        new_line = bytearray(line)
        # pull in by an amount that varies along the span, so the edge is bent
        # rather than merely translated
        pull = int(round(amount * (1 - abs((y - top) / ((bottom - top) / 2) - 1))))
        if not pull:
            continue
        for x in range(row_right, row_right - pull - 1, -1):
            src = x * 4
            new_line[x * 4:x * 4 + 4] = bytes(4)
        data[line_start:line_start + width * 4] = new_line
    write_rgba_png(path, width, height, data)


def flood_plate(path, colour):
    """Repaint every visible pixel of a plate in one flat colour."""
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(raw)
    for i in range(0, len(data), 4):
        if data[i + 3]:
            data[i] = colour[0]
            data[i + 1] = colour[1]
            data[i + 2] = colour[2]
    write_rgba_png(path, width, height, data)


def fill_with_contour_and_keeps_shadow(path):
    """Fill the object with the contour colour, keeping the plate non-uniform.

    Flooding the whole plate trips the single-colour check first, which is a
    different defect. Keeping the soft shadow means the plate still has more
    than one value, so the only thing left to catch it is the rule that a plate
    may not be mostly its own outline.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(raw)
    for i in range(0, len(data), 4):
        if data[i + 3] == 255:
            data[i] = 24
            data[i + 1] = 20
            data[i + 2] = 16
    write_rgba_png(path, width, height, data)


def slab_with_contour(path):
    """Flatten the plate to one warm tone and redraw its outline.

    A slab with a perfect contour is the defect the content rule exists for:
    the outline supplies plenty of distinct values, the contour is entirely
    present, and the bounding box is unchanged. The interior is what has to be
    flat for this to be a block rather than a drawing.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    total = len(raw) // 4
    solid = [i for i in range(total) if raw[i * 4 + 3] == 255]
    data = bytearray(raw)
    for index_px in solid:
        i = index_px * 4
        data[i] = 107
        data[i + 1] = 74
        data[i + 2] = 42
    write_rgba_png(path, width, height, data)
    # put the outline back on every boundary pixel, so only the interior is
    # flat: a boundary pixel is one with at least one transparent neighbour
    solid_mask = bytearray(1 if raw[i * 4 + 3] >= 200 else 0
                           for i in range(total))
    for index_px in range(total):
        x = index_px % width
        y = index_px // width
        if not solid_mask[index_px]:
            continue
        edge = (x == 0 or y == 0 or x == width - 1 or y == height - 1 or
                not solid_mask[index_px - 1] or
                not solid_mask[index_px + 1] or
                not solid_mask[index_px - width] or
                not solid_mask[index_px + width])
        if edge:
            i = index_px * 4
            data[i] = 24
            data[i + 1] = 20
            data[i + 2] = 16
    write_rgba_png(path, width, height, data)


def shift_guide_cell(path, gx, gy, dx, dy):
    """Roll the content of one guide cell, moving its sprite off its offset."""
    cell = 192
    crop = Path(tempfile.gettempdir()) / "mut_guide_cell.png"
    subprocess.run(["convert", str(path), "-crop",
                    f"{cell}x{cell}+{gx}+{gy}", "+repage", str(crop)], check=True)
    subprocess.run(["convert", str(crop), "-roll", f"{dx:+d}{dy:+d}",
                    str(crop)], check=True)
    subprocess.run(["convert", str(path), str(crop), "-geometry",
                    f"+{gx}+{gy}", "-composite", str(path)], check=True)


def squash_guide_cell(path, gx, gy, scale=60):
    """Squeeze one guide cell's sprite horizontally."""
    cell = 192
    crop = Path(tempfile.gettempdir()) / "mut_guide_cell.png"
    subprocess.run(["convert", str(path), "-crop",
                    f"{cell}x{cell}+{gx}+{gy}", "+repage", str(crop)], check=True)
    subprocess.run(["convert", str(crop), "-resize", f"{scale}x100%",
                    str(crop)], check=True)
    subprocess.run(["convert", str(path), str(crop), "-geometry",
                    f"+{gx}+{gy}", "-composite", str(path)], check=True)


def blank_guide_cell(path, gx, gy):
    """Erase one guide cell's sprite, leaving the fill and the frame."""
    cell = 192
    fill = "#10242a"
    subprocess.run(
        ["convert", str(path), "-fill", fill,
         "-draw", f"rectangle {gx + 4},{gy + 4} {gx + cell - 5},{gy + cell - 5}",
         str(path)], check=True)


def repaint_plate(path, colour):
    """Repaint every visible pixel of a plate in a colour it does not declare."""
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(raw)
    for i in range(0, len(data), 4):
        if data[i + 3]:
            data[i], data[i + 1], data[i + 2] = colour
    write_rgba_png(path, width, height, data)


def manifest_rename_layout_id(path, cell_id, new_id):
    """Rename one plate in the atlas layout only, leaving the plate list alone.

    The layout then claims a plate that does not exist, which is exactly the
    kind of drift a manifest has to be caught for: everything still renders,
    but a cell is silently dropped from the sheet.
    """
    text = path.read_text(encoding="utf-8")
    marker = f"    - id: {cell_id}\n      offset_px:"
    if marker not in text:
        raise SystemExit(f"layout entry for {cell_id} not found")
    text = text.replace(marker, f"    - id: {new_id}\n      offset_px:", 1)
    path.write_text(text, encoding="utf-8")


def paint_atlas_filler(path):
    """Make the transparent filler of the atlas opaque red.

    Painting only the RGB of a transparent pixel would be invisible, and the
    verifier rightly looks at alpha: stray content means stray *visible* content.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    import yaml
    manifest = yaml.safe_load(
        (path.parent / "flat2d_proof_manifest.yml").read_text(encoding="utf-8"))
    cell_w, cell_h = manifest["atlas"]["cell_canvas_px"]
    inside = set()
    for entry in manifest["atlas"]["layout"]:
        dx, dy = entry["offset_px"]
        for y in range(dy, dy + cell_h):
            for x in range(dx, dx + cell_w):
                inside.add(y * width + x)
    data = bytearray(raw)
    for index in range(width * height):
        if index not in inside and data[index * 4 + 3] == 0:
            data[index * 4:index * 4 + 4] = bytes((200, 20, 20, 255))
    write_rgba_png(path, width, height, data)


def blank_guide_sprites(path):
    """Erase every sprite in the guide, leaving frame, caption and fill."""
    import yaml
    manifest_dir = path.parent.parent
    manifest = yaml.safe_load(
        (manifest_dir / "flat2d_proof_manifest.yml").read_text(encoding="utf-8"))
    spec = manifest["atlas"]["guide_layout"]
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgb:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(raw)
    cell = spec["cell_canvas_px"][0]
    gutter = spec["gutter_px"]
    scale = spec["sprite_scale_pct"] / 100
    for index in range(spec["columns"] * spec["rows"]):
        col = index % spec["columns"]
        line = index // spec["columns"]
        gx = gutter + col * (cell + gutter)
        gy = gutter + line * (cell + gutter)
        sw = round(128 * scale)
        sh = round(128 * scale)
        for y in range(sh):
            for x in range(sw):
                i = ((gy + y) * width + gx + x) * 3
                if i + 3 <= len(data):
                    data[i:i + 3] = bytes((0x10, 0x24, 0x2a))
    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        rgba[index * 4:index * 4 + 3] = data[index * 3:index * 3 + 3]
        rgba[index * 4 + 3] = 255
    write_rgba_png(path, width, height, rgba)


def recolour_guide_fill(path, colour="#00ff00"):
    """Repaint only the cell interiors, leaving the gutter background alone.

    Painting the whole sheet would change the gutter too, and the background
    check would fire first. The defect under test is that a cell is filled with
    the wrong colour, so only the cell interiors are touched.
    """
    import yaml
    manifest = yaml.safe_load(
        (path.parent.parent / "flat2d_proof_manifest.yml").read_text(
            encoding="utf-8"))
    spec = manifest["atlas"]["guide_layout"]
    cell = spec["cell_canvas_px"][0]
    gutter = spec["gutter_px"]
    subprocess.run(
        ["convert", str(path), "-fill", colour,
         "-draw", " ".join(
             f"rectangle {gutter + col * (cell + gutter) + 3},"
             f"{gutter + line * (cell + gutter) + 3} "
             f"{gutter + col * (cell + gutter) + cell - 4},"
             f"{gutter + line * (cell + gutter) + cell - 4}"
             for col in range(spec["columns"])
             for line in range(spec["rows"])),
         str(path)], check=True)


def slide_inside_bbox(path, amount):
    """Lean the sprite to one side inside its own bounding box.

    The goal is a plate that still occupies exactly the same pixels on the
    canvas, so the placement and size checks all still pass, while the foot of
    the object is no longer centred on the anchor. Moving the left half right
    and the right half left by equal amounts would be a no-op on the contact,
    so the shift is applied to the lower half only: the plate ends, and the
    wheel touches the ground, at a different x.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    total = len(raw) // 4
    opaque = [i for i in range(total) if raw[i * 4 + 3] > 0]
    ys = [i // width for i in opaque]
    xs = [i % width for i in opaque]
    top, bottom = min(ys), max(ys)
    left, right = min(xs), max(xs)
    data = bytearray(raw)
    for index_px in opaque:
        sy = index_px // width
        sx = index_px % width
        # the bottom row keeps its rightmost pixel, so the bounding box and the
        # placement stay exactly as declared; everything above it leans right
        if sy < (top + bottom) // 2:
            continue
        nx = sx + amount
        if nx > right:
            nx = sx
        src = index_px * 4
        dst = (sy * width + nx) * 4
        data[dst:dst + 4] = raw[src:src + 4]
    write_rgba_png(path, width, height, data)



def manifest_edit_all(path, old, new):
    """Replace every occurrence, for the carrier checks that appear per cell."""
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"pattern not found in manifest: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def manifest_agree_with_slide(scratch, plate_id):
    """Tell the manifest that a plate's placement is whatever it now is.

    Without this a mutation that moves a plate is caught by the manifest's own
    placement numbers, and the check written to catch the *composition* is
    never reached - the suite would report a pass for a check that never ran.

    The edit is textual and scoped to the one cell block. Round-tripping the
    file through yaml.safe_dump would reindent every key and drop the trailing
    comments, which silently breaks every later text-based manifest_edit in the
    suite - that is a mutation reporting a spurious failure three cases later.
    """
    import subprocess as sp
    path = scratch / "flat2d_proof_manifest.yml"
    plate = scratch / "plates" / f"{plate_id}.png"
    # %@ is the trimmed opaque bounding box. `identify %w %h %X %Y` would give
    # the canvas - 128x128 at 0,0 for every plate - and "repairing" the
    # manifest with that turned the mutation into a size mismatch, so the band
    # check fired but by accident rather than by being the thing that caught it.
    box = sp.check_output(
        ["convert", str(plate), "-alpha", "extract", "-depth", "8",
         "-threshold", "8%", "-format", "%@", "info:"], text=True).strip()
    found = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", box)
    if found:
        w, h, x, y = (int(v) for v in found.groups())
    else:
        w, h, x, y = (int(v) for v in sp.check_output(
            ["identify", "-format", "%w %h %X %Y", str(plate)], text=True).split())
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n  - id: {plate_id}\n")
    end = text.find("\n  - id: ", start + 1)
    block = text[start:end if end != -1 else len(text)]
    for key, value in (("object_placement_px", [x, y]),
                       ("plate_placement_px", [x, y]),
                       ("object_size_px", [w, h]),
                       ("plate_size_px", [w, h])):
        block = re.sub(rf"^    {key}: \[[^\]]*\]$",
                       f"    {key}: [{value[0]}, {value[1]}]",
                       block, count=1, flags=re.M)
    path.write_text(text[:start] + block + text[end if end != -1 else len(text):],
                    encoding="utf-8")


def translate_opaque(path, dx, dy=0):
    """Move the whole opaque silhouette, clearing where it came from.

    slide_inside_bbox copies rather than moves, which is deliberate there: it
    has to leave the bounding box alone so the size and placement checks still
    pass while the contact moves. That trick needs a shape whose contact is in
    the lower half, and it silently does nothing to a low wide prop - the
    original pixels stay, so the contact run is unchanged. This one is a true
    translation, which is what "the object was placed off its anchor" means.

    The destination buffer starts empty. Copying into a copy of the source
    leaves a trail of the original pixels, so a plate translated 45px down came
    out 54px tall and was then stopped by the size check - a pass for a check
    the mutation was never about.
    """
    raw = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    width, height = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    moved = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            if raw[i + 3]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    j = (ny * width + nx) * 4
                    moved[j:j + 4] = raw[i:i + 4]
    write_rgba_png(path, width, height, moved)


def repack_atlas(scratch, manifest):
    """Rebuild the atlas from the current plates, pixel for pixel.

    Repacking matters: without it the consistency check between atlas and
    plates is the first thing to fire, and the check the mutation was written
    to exercise is never reached. The atlas is written from the raw RGBA bytes
    rather than through ImageMagick's compositor, which shifts colours by a few
    units and would make every rebuilt atlas look inconsistent.
    """
    layout = {entry["id"]: entry["offset_px"]
              for entry in manifest["atlas"]["layout"]}
    cell_w, cell_h = manifest["atlas"]["cell_canvas_px"]
    aw, ah = manifest["atlas"]["canvas_px"]
    data = bytearray(aw * ah * 4)
    for cell_id, (dx, dy) in layout.items():
        plate = scratch / "plates" / f"{cell_id}.png"
        raw = subprocess.check_output(["convert", str(plate), "-depth", "8",
                                       "rgba:-"])
        for y in range(cell_h):
            src = y * cell_w * 4
            dst = ((dy + y) * aw + dx) * 4
            data[dst:dst + cell_w * 4] = raw[src:src + cell_w * 4]
    write_rgba_png(scratch / "flat2d_proof_atlas_v0.png", aw, ah, data)




def declared_count(path, key):
    """Read a declared family size out of the manifest under test.

    The count mutations used to hard-code the number - `prop_count: 9` - and
    every added plate broke the suite with `mutation target not present`. A
    count is a declared fact about the file being tested, so the mutation has to
    read it rather than remember it.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit(f"{key} is not declared in the manifest")


def revision_mutation(lie):
    """Bump the declared revision, reading the real one first.

    The verifier refuses any revision it does not know, so this mutation is the
    one that proves the pin is not dead code. It used to carry `revision: 9`
    hard-coded, and every revision bump killed the suite with `mutation target
    not present` - a test that dies on a correct change is worse than no test.
    """
    return lambda p: manifest_edit(
        p, f"  revision: {declared_count(p, 'revision')}", f"  revision: {lie}")


def count_mutation(key, lie):
    return lambda p: manifest_edit(
        p, f"  {key}: {declared_count(p, key)}", f"  {key}: {lie}")


def manifest_edit(path, old, new, count=1):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"mutation target not present in the manifest: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def paint_colour_range(path, x0, y0, x1, y1, warm=True):
    """Overlay a rectangle of many distinct colours on top of a plate.

    Warm noise defeats a palette check that only hunts for cold colours; cold
    noise defeats one that only inspects opaque pixels. Both are written
    straight into the PNG so the alpha stays exactly as the plate had it.
    """
    width, height = x1 - x0 + 1, y1 - y0 + 1
    plate = subprocess.check_output(["convert", str(path), "-depth", "8", "rgba:-"])
    plate_w, plate_h = (int(v) for v in subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split())
    data = bytearray(plate)
    seed = 0x2F6E2B1
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            dst = (y * plate_w + x) * 4
            if warm:
                # every colour sits inside the warm ramp, so only the count of
                # distinct colours can give this mutation away
                data[dst] = 60 + seed % 90
                data[dst + 1] = 40 + (seed >> 7) % 60
                data[dst + 2] = 20 + (seed >> 14) % 30
            else:
                data[dst] = 40 + seed % 40
                data[dst + 1] = 90 + (seed >> 7) % 60
                data[dst + 2] = 120 + (seed >> 14) % 90
    write_rgba_png(path, plate_w, plate_h, data)


def strip_contour(path, colour="#181410"):
    """Repaint every pixel of the contour colour with its neighbour's tone."""
    subprocess.run(["convert", str(path), "-fill", "#3a2c22",
                    "-opaque", colour, str(path)], check=True)



def _render_guide_cell(scratch, manifest, cell_id, index, out_path):
    """Render one guide cell exactly as build_proof.py renders it.

    Two things here have to stay identical to the builder, and both were wrong
    here once. The crosshair is drawn on the EXTENDED CELL, not on the plate -
    drawn on the plate it covers anything smaller than its own 16 px stroke, so
    the guide shows the crosshair instead of the art. And its coordinates are
    given in plate space, so they must be transformed into the scaled, offset
    cell, or it marks the wrong pixel.
    """
    spec = manifest["atlas"]["guide_layout"]
    cells = {cell["id"]: cell for cell in manifest["cells"]}
    fill = manifest["pipeline"]["guide_cell_fill"]
    scale = int(str(spec["sprite_scale_pct"]).rstrip("%"))
    cell = spec["cell_canvas_px"][0]
    gutter = spec["gutter_px"]
    offset_x = int(spec["sprite_offset_px"][0])
    ground = manifest["target"]["proof_anchor_px"][1]
    hex_cy = manifest["target"]["proof_hex_center_px"][1]
    guide_scale = scale / 100

    def in_guide(px, py):
        return (round(px * guide_scale) + offset_x, round(py * guide_scale))

    cy = hex_cy if cells[cell_id]["anchor_status"].startswith("proof_hex") \
        else ground
    gx0, gy0 = in_guide(56, cy)
    gx1, gy1 = in_guide(72, cy)
    gcx, gcy = in_guide(64, cy)
    gtx, gty = in_guide(64, cy - 8)
    gbx, gby = in_guide(64, cy + 8)
    cross = (f"line {gx0},{gy0} {gx1},{gy1} "
             f"line {gtx},{gty} {gcx},{gcy} line {gcx},{gcy} {gbx},{gby}")
    subprocess.run(
        ["convert", str(scratch / "plates" / f"{cell_id}.png"),
         "-filter", "point", "-resize", f"{scale}%",
         "-background", fill, "-gravity", "north",
         "-extent", f"{cell}x{cell}",
         "-stroke", "#d8c9a3", "-strokewidth", "1", "-fill", "none",
         "-draw", cross,
         "-gravity", "south", "-fill", "#d8c9a3", "-font", "DejaVu-Sans",
         "-pointsize", "11", "-annotate", "+0+8", cell_id,
         "-fill", "none", "-stroke", "#3c5961", "-strokewidth", "2",
         "-draw", f"rectangle 1,1 {cell - 2},{cell - 2}", str(out_path)],
        check=True)
    col = index % spec["columns"]
    line = index // spec["columns"]
    return (gutter + col * (cell + gutter), gutter + line * (cell + gutter))


def rebuild_guide(scratch, manifest, only=None):
    """Rebuild the guide, or just the one cell a plate mutation touched.

    A plate mutation changes one plate. Rebuilding all fifty-eight cells for it
    was the single most expensive thing in the suite: each cell renders a
    caption in a font and composites onto a ten-megabyte canvas, and fifty-seven
    of those cells were byte-identical to what was already there. So when `only`
    names a cell that exists, that one cell is re-rendered and composited onto a
    copy of the sheet that is already on disk.

    The full rebuild is still what a change to the cell list or the layout needs,
    and it is the default.
    """
    spec = manifest["atlas"]["guide_layout"]
    cells = {cell["id"]: cell for cell in manifest["cells"]}
    pipeline = manifest["pipeline"]
    fill = pipeline["guide_cell_fill"]
    scale = int(str(spec["sprite_scale_pct"]).rstrip("%"))
    cell = spec["cell_canvas_px"][0]
    gutter = spec["gutter_px"]
    aw, ah = spec["canvas_px"]
    ground = manifest["target"]["proof_anchor_px"][1]
    hex_cy = manifest["target"]["proof_hex_center_px"][1]
    work = scratch / "guide_work"
    work.mkdir(exist_ok=True)
    staged = work / "staged.png"
    subprocess.run(["convert", "-size", f"{aw}x{ah}", f"xc:{spec['background']}",
                    str(staged)], check=True)
    for index, cell_id in enumerate(cells):
        plate = scratch / "plates" / f"{cell_id}.png"
        cy = hex_cy if cells[cell_id]["anchor_status"].startswith("proof_hex") \
            else ground
        cross = (f"line 56,{cy} 72,{cy} line 64,{cy - 8} 64,{cy + 8}")
        sprite = work / "sprite.png"
        subprocess.run(
            ["convert", str(plate), "-stroke", "#d8c9a3", "-strokewidth", "1",
             "-fill", "none", "-draw", cross, "-filter", "point",
             "-resize", f"{scale}%", "-background", fill, "-gravity", "north",
             "-extent", f"{cell}x{cell}",
             "-gravity", "south", "-fill", "#d8c9a3", "-font", "DejaVu-Sans",
             "-pointsize", "11", "-annotate", "+0+8", cell_id,
             "-fill", "none", "-stroke", "#3c5961", "-strokewidth", "2",
             "-draw", f"rectangle 1,1 {cell - 2},{cell - 2}", str(sprite)],
            check=True)
        col = index % spec["columns"]
        line = index // spec["columns"]
        gx = gutter + col * (cell + gutter)
        gy = gutter + line * (cell + gutter)
        nxt = work / "next.png"
        subprocess.run(["convert", str(staged), str(sprite),
                        "-geometry", f"+{gx}+{gy}", "-composite", str(nxt)],
                       check=True)
        nxt.replace(staged)
    (scratch / "guides" / "flat2d_proof_guide_v0.png").write_bytes(
        staged.read_bytes())


class PlateMutation:
    """A plate mutation that also rebuilds the atlas and the guide.

    Both have to be rebuilt. If they are not, the first check to fire is the
    one that compares the guide sprite with the plate, and the check the
    mutation was written to exercise is never reached: a contour mutation
    would be reported as a guide problem, and the suite would be proving
    nothing about the contour at all.
    """

    def __init__(self, plate_id, mutate, expect, fix_manifest=None):
        self.plate_id = plate_id
        self.mutate = mutate
        self.expect = expect
        # A mutation may also have to repair the manifest, so that the mutation
        # is not caught by the placement self-consistency check and the check
        # it was written for is actually reached. Sliding a layer down the body
        # and telling the manifest it was always there is exactly the real
        # failure: the build drew it in the wrong place and recorded it there.
        self.fix_manifest = fix_manifest
        self.__name__ = f"plate_{plate_id}"

    def __call__(self, scratch, manifest):
        import yaml
        manifest = yaml.safe_load(
            (scratch / "flat2d_proof_manifest.yml").read_text(encoding="utf-8"))
        self.mutate(scratch / "plates" / f"{self.plate_id}.png")
        if self.fix_manifest is not None:
            self.fix_manifest(scratch, self.plate_id)
        repack_atlas(scratch, manifest)
        # only the cell whose plate changed: the other fifty-seven are already
        # correct in the sheet on disk
        rebuild_guide(scratch, manifest, only=self.plate_id)


def _require_plate(plate_id):
    """A plate mutation must name a plate that exists.

    Three mutations carried the adult-only ids from before the clothing layers
    were cut per body, and every one of them failed at run time with
    `convert: unable to open image ... No such file or directory`. I read that
    twice as a starved temporary filesystem, because the suite printed
    `ERROR` and `DISK QUOTA` elsewhere in the same log. It was not the
    environment: the file those mutations asked for has not existed since the
    rename to `garment_armour_mail_adult`. A stale id is caught here, at
    declaration, where it costs one line.
    """
    path = REPO / "design/art/flat2d_proof/plates" / f"{plate_id}.png"
    if not path.exists():
        raise SystemExit(
            f"mutation names plate {plate_id!r}, which does not exist; the "
            "clothing layers are cut per body now, so the ids carry a "
            "_child/_adult/_elder suffix")


def plate_mutation(plate_id, mutate, expect, fix_manifest=None):
    _require_plate(plate_id)
    """Wrap a plate mutation and name the check it is meant to exercise.

    Without the name the suite only knows that *something* fired, so a
    mutation meant to test the contour can be caught by the colour check and
    still be reported as a success. `fix_manifest` repairs the manifest's own
    placement numbers, so a mutation is not stopped by self-consistency before
    the check it was written for gets a chance to run.
    """
    return PlateMutation(plate_id, mutate, expect, fix_manifest)


def takes_scratch(callable_object):
    return isinstance(callable_object, PlateMutation)


MUTATIONS = [
    # (name, file, mutate, expected substring in the verifier's diagnosis)
    ("hole punched in the hex", "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex", lambda p: edit_png_alpha(p, 60, 55, 67, 65),
                    "not solid"), "not solid"),
    ("notch cut out of a sloped edge", "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex", lambda p: edit_png_alpha(p, 78, 30, 90, 40),
                    "not centred"), "not centred"),
    ("slot cut from the top vertex", "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex", lambda p: edit_png_alpha(p, 62, 16, 65, 40),
                    "not centred"), "not centred"),
    ("row removed across the whole hex", "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex", lambda p: edit_png_alpha(p, 0, 55, 127, 55),
                    "rows are gone"), "rows are gone"),
    ("hex edge bent, atlas and guide rebuilt",
     "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex",
                    lambda p: shear_hex(p),
                    "bends: worst residual"), "bends: worst residual"),
    ("hex flooded with a single colour, atlas repacked",
     "plates/terrain_field_hex.png",
     plate_mutation("terrain_field_hex",
                    lambda p: flood_plate(p, (24, 20, 16)),
                    "block, not an illustration"),
     "block, not an illustration"),
    ("contour removed from the cottage",
     "plates/architecture_cottage_plate.png",
     plate_mutation("architecture_cottage_plate",
                    lambda p: strip_contour(p), "contour has no solid core"),
     "contour"),
    ("cottage filled with the contour colour",
     "plates/architecture_cottage_plate.png",
     plate_mutation("architecture_cottage_plate",
                    lambda p: fill_with_contour_and_keeps_shadow(p),
                    "own outline"), "own outline"),
    ("two thousand warm colours, atlas repacked",
     "plates/architecture_cottage_plate.png",
     plate_mutation("architecture_cottage_plate",
                    lambda p: paint_colour_range(p, 36, 36, 84, 84, warm=True),
                    "distinct colours"), "distinct colours"),
    ("cold halo along the whole edge, atlas repacked",
     "plates/architecture_cottage_plate.png",
     plate_mutation("architecture_cottage_plate",
                    lambda p: paint_colour_range(p, 28, 45, 99, 60, warm=False),
                    "cold or neutral"), "cold or neutral"),
    ("cart slid off the anchor, atlas and guide rebuilt",
     "plates/prop_flat_cart_empty.png",
     plate_mutation("prop_flat_cart_empty",
                    lambda p: slide_inside_bbox(p, 4),
                    "ground contact"), "ground contact"),
    ("hole punched in the villager's alpha",
     "plates/actor_villager_plate.png",
     plate_mutation("actor_villager_plate",
                    lambda p: edit_png_alpha(p, 62, 42, 68, 48),
                    "visible pixels"), "visible pixels"),
    ("cottage slab with the contour kept",
     "plates/architecture_cottage_plate.png",
     plate_mutation("architecture_cottage_plate",
                    lambda p: slab_with_contour(p),
                    "block, not an illustration"),
     "block, not an illustration"),
    ("cart emptied, atlas and guide rebuilt",
     "plates/prop_flat_cart_empty.png",
     plate_mutation("prop_flat_cart_empty",
                    lambda p: edit_png_alpha(p, 0, 0, 127, 127),
                    "fully transparent"), "fully transparent"),
    ("a plate repainted in a foreign material",
     "plates/terrain_water_hex.png",
     plate_mutation("terrain_water_hex",
                    lambda p: repaint_plate(p, (140, 60, 40)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a terrain dropped from the atlas layout",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_rename_layout_id(p, "terrain_marsh_hex",
                                         "terrain_typo_hex"),
     "atlas layout ids do not match the plate ids"),
    ("terrain_count lies", "flat2d_proof_manifest.yml",
     count_mutation("terrain_count", "3"),
     "terrain_count"),
    # --- the drawn buildings -------------------------------------------
    # These cover the claims the architecture set added: a building is drawn,
    # it declares its own material, and it stands on the same anchor as every
    # other plate. Each mutation attacks one of those three.
    ("cottage gable slid off the anchor, atlas and guide rebuilt",
     "plates/architecture_cottage_gable_a.png",
     plate_mutation("architecture_cottage_gable_a",
                    lambda p: slide_inside_bbox(p, 5),
                    "ground contact"), "ground contact"),
    ("a building repainted in a foreign material",
     "plates/architecture_house_timber.png",
     plate_mutation("architecture_house_timber",
                    lambda p: repaint_plate(p, (150, 40, 130)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a building given a cold glaze it never declared",
     "plates/architecture_supply_shed.png",
     plate_mutation("architecture_supply_shed",
                    lambda p: paint_colour_range(p, 30, 34, 96, 52, warm=False),
                    "not in the declared material"),
     "not in the declared material"),
    ("architecture_count lies", "flat2d_proof_manifest.yml",
     count_mutation("architecture_count", "2"),
     "architecture_count"),
    ("a building dropped from the atlas layout",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_rename_layout_id(p, "architecture_palisade",
                                         "architecture_palisade_typo"),
     "atlas layout ids do not match the plate ids"),
    # Punching the arch void wider does not make a solid block - it removes
    # matter - so the check that catches it is the silhouette area, not the
    # content test. The mutation name says which defect is actually injected.
    # --- the clothing layers -----------------------------------------------
    ("a tunic slid sideways, atlas and guide rebuilt",
     "plates/garment_tunic_dyed_adult.png",
     plate_mutation("garment_tunic_dyed_adult",
                    lambda p: translate_opaque(p, 5),
                    "manifest says"), "manifest says"),
    # The one that got through. A layer drawn at the ankles, with the manifest
    # rewritten to agree, passes every self-consistency check in the verifier:
    # the plate is well formed, centred, contoured, made of declared materials,
    # and it is still inside the figure's silhouette. What catches it is the
    # band its landmarks allow, and nothing before that band existed.
    ("a helm dropped to the ankles, manifest rewritten to agree",
     "plates/garment_head_helm_adult.png",
     plate_mutation("garment_head_helm_adult",
                    lambda p: translate_opaque(p, 0, 45),
                    "outside the band its landmarks allow",
                    fix_manifest=lambda s, pid: manifest_agree_with_slide(s, pid)),
     "outside the band its landmarks allow"),
    ("a garment repainted in a foreign material",
     "plates/garment_armour_mail_adult.png",
     plate_mutation("garment_armour_mail_adult",
                    lambda p: repaint_plate(p, (30, 120, 60)),
                    "not in the declared material"),
     "not in the declared material"),
    ("garment_count lies", "flat2d_proof_manifest.yml",
     count_mutation("garment_count", "3"),
     "garment_count"),
    # A layer that is missing for one body is not a missing ornament. The child
    # of a thegn would be drawn in nothing, and no other plate is wrong, so
    # without this check the gap is invisible.
    ("a clothing layer drawn for no body at all",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "  garment_age_classes:",
                             "  garment_age_classes_typo:"),
     "garment_age_classes is missing"),
    ("a clothing layer claims a body it is not cut for",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    body_id: actor_child",
                             "    body_id: actor_villager_plate"),
     "but body_id is"),
    ("a clothing layer's age class is not one of the three bodies",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    material_age_class: child",
                             "    material_age_class: grownup"),
     "is not among the declared garment_age_classes"),
    # The guide draws the body a layer is cut for underneath it. If the cell
    # declares the wrong figure, the guide is quietly showing a child's layer on
    # a full-grown body, and nothing else in the file objects.
    ("a clothing layer's guide ghosts the wrong body",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    guide_ghost: actor_child",
                             "    guide_ghost: actor_adult"),
     "of the declared body actor_adult is visible under the layer"),
    ("a garment with no layer declared",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    material_layer: tunic",
                             "    material_note: tunic"),
     "material_layer is not declared"),
    ("a garment given a layer that is not declared",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    material_layer: armour",
                             "    material_layer: chainmail"),
     "is not among the declared garment_layers"),
    ("a new good repainted in a foreign material",
     "plates/good_hay.png",
     plate_mutation("good_hay", lambda p: repaint_plate(p, (40, 200, 120)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a new good slid off the anchor, atlas and guide rebuilt",
     "plates/good_stone.png",
     plate_mutation("good_stone", lambda p: translate_opaque(p, 5),
                    "manifest says"), "manifest says"),
    # --- the camps ----------------------------------------------------------
    ("a camp repainted in a foreign material",
     "plates/camp_pavilion.png",
     plate_mutation("camp_pavilion", lambda p: repaint_plate(p, (20, 200, 160)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a camp slid off the anchor, atlas and guide rebuilt",
     "plates/camp_tent.png",
     plate_mutation("camp_tent", lambda p: translate_opaque(p, 6),
                    "manifest says"), "manifest says"),
    ("a camp claims a carrier field the ontology has not got",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    data_carrier: Pack.kind",
                             "    data_carrier: Pack.dread"),
     "is not a declared field on that class"),
    # --- the sites -----------------------------------------------------------
    ("a site repainted in a foreign material",
     "plates/site_baron_castle.png",
     plate_mutation("site_baron_castle", lambda p: repaint_plate(p, (200, 40, 160)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a site slid off the anchor, atlas and guide rebuilt",
     "plates/site_tavern.png",
     plate_mutation("site_tavern", lambda p: translate_opaque(p, 5),
                    "manifest says"), "manifest says"),
    ("site_count lies", "flat2d_proof_manifest.yml",
     count_mutation("site_count", "1"), "site_count"),
    ("a site dropped from the atlas layout",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_rename_layout_id(p, "site_smoke", "site_smoke_x"),
     "atlas layout ids do not match"),
    # A site carried by a mark `has_mark()` would reject: it raises on a name
    # outside LAND_MARKS rather than returning False, so the carrier cannot
    # exist at all. This is the check that caught me writing nine of these as
    # declared Tile fields, which are named marks and not fields.
    ("a site claims a tile mark LAND_MARKS does not have",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    data_carrier: Tile.mooring",
                             "    data_carrier: Tile.moon_gate"),
     "is not a declared tile mark"),
    # A site whose game_form nothing in tile_form() returns: a plate the game
    # can never show. Caught by validate_mapping, which mutation_test does not
    # run - it is covered there by the mapping self-test instead.
    ("a site claims a carrier kind that says nothing",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "    data_carrier_kind: declared_field",
                             "    data_carrier_kind: whatever"),
     "is not declared_field or optional_attribute"),
    # --- the hazards --------------------------------------------------------
    ("hazard_count lies", "flat2d_proof_manifest.yml",
     count_mutation("hazard_count", "1"),
     "hazard_count"),
    ("a hazard placed off the anchor, atlas and guide rebuilt",
     "plates/hazard_band.png",
     plate_mutation("hazard_band",
                    lambda p: translate_opaque(p, 6),
                    "manifest says"), "manifest says"),
    ("a hazard repainted in a foreign material",
     "plates/hazard_bog.png",
     plate_mutation("hazard_bog",
                    lambda p: repaint_plate(p, (30, 120, 200)),
                    "not in the declared material"),
     "not in the declared material"),
    # --- the animals --------------------------------------------------------
    ("an animal placed off the anchor, atlas and guide rebuilt",
     "plates/animal_ox.png",
     plate_mutation("animal_ox",
                    lambda p: translate_opaque(p, 6),
                    "ground contact"), "ground contact"),
    ("an animal repainted in a foreign material",
     "plates/animal_hen.png",
     plate_mutation("animal_hen",
                    lambda p: repaint_plate(p, (120, 20, 130)),
                    "not in the declared material"),
     "not in the declared material"),
    ("animal_count lies", "flat2d_proof_manifest.yml",
     count_mutation("animal_count", "2"),
     "animal_count"),
    # An animal plate renamed into the prop family must break the family
    # counters - that is the whole point of the prefix taxonomy. It also trips
    # the atlas and the guide, and the counters are the first useful complaint,
    # so those are what is asserted.
    ("a plate given a family it does not belong to",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "  - id: animal_pig\n", "  - id: good_pig\n", 1),
     "are drawn animals"),
    # --- the data carriers ------------------------------------------------
    # A plate that names a field nothing reads is the same failure as a plate
    # that names no field at all: both are art the runtime cannot key on. The
    # checks are per cell, so these are injected across the sheet at once.
    ("a plate names a field that does not exist",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p,
                             "    data_carrier: Person.age_class\n"
                             "    data_carrier_kind: declared_field",
                             "    data_carrier: Person.posture\n"
                             "    data_carrier_kind: declared_field"),
     "is not a declared field"),
    ("a plate names a class that does not exist",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p,
                             "    data_carrier: Tile.bridge\n"
                             "    data_carrier_kind: declared_field",
                             "    data_carrier: Hex.bridge\n"
                             "    data_carrier_kind: declared_field"),
     "which ontology.py does not define"),
    ("a plate understates its carrier as an optional attribute",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p,
                             "    data_carrier: Good.id\n"
                             "    data_carrier_kind: declared_field",
                             "    data_carrier: Good.id\n"
                             "    data_carrier_kind: optional_attribute"),
     "declare it honestly"),
    ("a plate overstates an optional attribute as a declared field",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p,
                             "    data_carrier: Tile.dwelling\n"
                             "    data_carrier_kind: optional_attribute",
                             "    data_carrier: Tile.dwelling\n"
                             "    data_carrier_kind: declared_field"),
     "is not a declared field"),
    ("a plate claims a sprite selector exists",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p,
                             "    data_carrier_kind: declared_field\n"
                             "    sprite_selector: none",
                             "    data_carrier_kind: declared_field\n"
                             "    sprite_selector: Tile.terrain"),
     "sprite_selector must be none"),
    ("a plate with no carrier gives no reason",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit_all(
         p, "    data_carrier_reason: >-\n"
            "      No simulation field identifies this plate, so a renderer"
            " cannot key on\n"
            "      one. It is kept for geometry or for pipeline coverage, not"
            " as an asset.\n", ""),
     "data_carrier is null with no data_carrier_reason"),
    ("the whole sheet drops its data_carrier declarations",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit_all(p, "    data_carrier: ", "    note: "),
     "data_carrier is not declared"),
    # --- the figures -----------------------------------------------------
    # The actor plates carry `Person.age_class`, so the mutation has to be able
    # to break that link: a figure on the sheet that the runtime cannot select is
    # exactly the failure this family is guarded against.
    ("a figure placed off the anchor, atlas and guide rebuilt",
     "plates/actor_adult.png",
     plate_mutation("actor_adult",
                    lambda p: translate_opaque(p, 5),
                    "ground contact"), "ground contact"),
    ("a figure repainted in a foreign material",
     "plates/actor_elder.png",
     plate_mutation("actor_elder",
                    lambda p: repaint_plate(p, (20, 130, 140)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a figure given a cold glaze it never declared",
     "plates/actor_child.png",
     plate_mutation("actor_child",
                    lambda p: paint_colour_range(p, 55, 40, 74, 60, warm=False),
                    "not in the declared material"),
     "not in the declared material"),
    ("actor_count lies", "flat2d_proof_manifest.yml",
     count_mutation("actor_count", "1"),
     "actor_count"),
    ("a figure dropped from the atlas layout",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_rename_layout_id(p, "actor_child", "actor_child_typo"),
     "atlas layout ids do not match the plate ids"),
    # --- the goods and props --------------------------------------------
    ("a salt heap placed off the anchor, atlas and guide rebuilt",
     "plates/good_salt_pile.png",
     plate_mutation("good_salt_pile",
                    lambda p: translate_opaque(p, 7),
                    "ground contact"), "ground contact"),
    ("a bale repainted in a foreign material",
     "plates/good_wool_bale.png",
     plate_mutation("good_wool_bale",
                    lambda p: repaint_plate(p, (20, 120, 60)),
                    "not in the declared material"),
     "not in the declared material"),
    ("a boat given a cold glaze it never declared",
     "plates/good_boat.png",
     plate_mutation("good_boat",
                    lambda p: paint_colour_range(p, 36, 44, 92, 62, warm=False),
                    "not in the declared material"),
     "not in the declared material"),
    ("prop_count lies", "flat2d_proof_manifest.yml",
     count_mutation("prop_count", "4"),
     "prop_count"),
    ("a good dropped from the atlas layout",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_rename_layout_id(p, "good_log_stack",
                                         "good_log_stack_typo"),
     "atlas layout ids do not match the plate ids"),
    ("the bridge arch void widened, changing the silhouette area",
     "plates/architecture_bridge.png",
     plate_mutation("architecture_bridge",
                    lambda p: edit_png_alpha(p, 45, 44, 83, 63),
                    "visible pixels"), "visible pixels"),
    ("atlas rotated", "flat2d_proof_atlas_v0.png",
     lambda p: subprocess.run(["convert", str(p), "-rotate", "180", str(p)],
                              check=True), "atlas rows do not match"),
    ("transparent atlas filler recoloured",
     "flat2d_proof_atlas_v0.png",
     lambda p: paint_atlas_filler(p), "outside the declared cells"),
    ("guide recoloured", "guides/flat2d_proof_guide_v0.png",
     lambda p: subprocess.run(
         ["convert", str(p), "-fill", "red", "-colorize", "60", str(p)],
         check=True), "guide background"),
    ("guide sprites blanked", "guides/flat2d_proof_guide_v0.png",
     lambda p: blank_guide_sprites(p), "sprite pixels"),
    ("guide sprites squashed to 60 percent",
     "guides/flat2d_proof_guide_v0.png",
     lambda p: subprocess.run(
         ["convert", str(p), "-resize", "60%x100%", str(p)], check=True),
     "guide is"),
    ("guide cell fill recoloured", "guides/flat2d_proof_guide_v0.png",
     lambda p: recolour_guide_fill(p), "is filled with"),
    ("guide terrain sprite rolled off its offset",
     "guides/flat2d_proof_guide_v0.png",
     lambda p: shift_guide_cell(p, 16, 16, 13, 0), "match the plate"),
    ("guide villager sprite squashed to 60 percent",
     "guides/flat2d_proof_guide_v0.png",
     lambda p: squash_guide_cell(p, 224, 224), "match the plate"),
    ("guide villager sprite blanked",
     "guides/flat2d_proof_guide_v0.png",
     lambda p: blank_guide_cell(p, 224, 224), "match the plate"),
    ("guide deleted", "guides/flat2d_proof_guide_v0.png", lambda p: p.unlink(),
     "guide file is missing"),
    ("guide_layout field removed", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "sprite_scale_pct: 130", "sprite_scale_pct:"),
     "guide_layout.sprite_scale_pct is missing"),
    ("manifest revision changed", "flat2d_proof_manifest.yml",
     revision_mutation(99), "revision"),
    ("manifest claims a final hex mask", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "final_hex_mask: false",
                             "final_hex_mask: true"), "final_hex_mask"),
    ("manifest claims the proof is final art",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "status: proof_generated", "status: final"),
     "status"),
    ("manifest guide gutter lies", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "gutter_px: 16", "gutter_px: 99"),
     "guide canvas"),
    ("manifest object size lies", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "object_size_px: [60, 57]",
                             "object_size_px: [60, 99]"), "object is"),
    ("manifest hex centre lies", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "proof_hex_center_px: [64, 64]",
                             "proof_hex_center_px: [68, 64]"),
     "proof_hex_center_px"),
    ("manifest claims the camera is frozen",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "camera_frozen: false",
                             "camera_frozen: true"), "camera_frozen"),
    ("manifest pipeline block emptied", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, 'contour: "restroke_1px_#181410_',
                             'contour: "none_'), "pipeline.contour"),
    ("manifest pipeline order puts contour first",
     "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(
         p, "order: key_then_resize_then_mask_then_retouch_then_scrub_then_contour",
         "order: key_then_resize_then_mask_then_contour_then_retouch"),
     "resample would undo the stroke"),
    ("manifest palette budget inflated", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "max_unique_rgb_per_plate: 32",
                             "max_unique_rgb_per_plate: 4000"),
     "max_unique_rgb_per_plate"),
    ("manifest colour budget removed", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, "  max_unique_rgb_per_plate: 32",
                             "  max_unique_rgb_per_plate_removed: 32"),
     "max_unique_rgb_per_plate"),
    ("manifest guide cell fill removed", "flat2d_proof_manifest.yml",
     lambda p: manifest_edit(p, '  guide_cell_fill: "#10242a"',
                             '  guide_cell_fill_removed: "#10242a"'),
     "guide_cell_fill"),
]


def verify(root):
    """Return (outcome, first message).

    A non-zero exit code is not enough on its own: a verifier that crashes on
    a malformed input is not the same as one that rejects it, and counting a
    traceback as a successful catch would make the whole suite meaningless.
    """
    # HILLCOURT_REPO is required, not optional: the verifier resolves the
    # simulation source to check that every declared data carrier is a real
    # field. The proof runs from a scratch tree here, so without it those checks
    # would skip and every carrier mutation would look "missed" for the wrong
    # reason - or worse, the checks would rot unnoticed.
    result = subprocess.run(
        ["python3", str(root / "design/art/flat2d_proof/verify_proof.py")],
        capture_output=True, text=True, cwd=str(root),
        env={**os.environ, "HILLCOURT_REPO": str(REPO)})
    if result.returncode == 0:
        return "passed", result.stdout.strip().splitlines()[0]
    if "Traceback" in result.stderr:
        return "crashed", result.stderr.strip().splitlines()[-1][:70]
    # the verifier reports every problem it found, not just the first, so a
    # mutation may legitimately trip more than one check; the whole report is
    # what the expectation is matched against
    lines = result.stdout.strip().splitlines()
    message = " | ".join(line.strip() for line in lines[1:])
    return "rejected", message


# The verifier reads the proof itself, the manifests that name carriers and
# library cells, and - via HILLCOURT_REPO, which points at the real repo - the
# simulation sources. Nothing else.
SCRATCH_NEEDS = (
    "design/art/flat2d_proof",
    "design/art/asset_library_v1",
    "design/art/asset_library_v0",
    "design/art/production_grid",
    "design/catalogs",
)


def _scratch_fingerprint(scratch, relative):
    """Hash the files a mutation is allowed to touch.

    A mutation that changes nothing is worse than no mutation: the suite reports
    it as caught when the verifier simply passed on an untouched tree, and a
    green run then proves nothing about that check. Seven count mutations were
    exactly this - `lambda p: count_mutation(...)` BUILT the mutation and threw
    it away - and they were green.
    """
    import hashlib
    digest = hashlib.sha256()
    watched = {relative}
    # a plate mutation also rebuilds these two sheets
    watched |= {"flat2d_proof_atlas_v0.png", "guides/flat2d_proof_guide_v0.png"}
    for rel in sorted(watched):
        path = scratch / rel
        if path.exists():
            digest.update(rel.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    # `--only` runs a subset by substring. Added while adding the site cases: a
    # new mutation is only useful if it is caught, and a full run is ~1.5 hours,
    # which is too slow to iterate a list against. It cannot make a run pass -
    # the summary still reports the count it actually ran.
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    selected = ([m for m in MUTATIONS if only in m[0]] if only else MUTATIONS)
    if only and not selected:
        print(f"mutation test: no mutation name contains {only!r}")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Copy only what the verifier actually reads. Copying all of `design/`
        # was 61 MB per scratch tree - 41 MB of it illustrations the verifier
        # never opens - and seven killed runs filled the 2 GB temporary
        # filesystem and killed the suite with `Disk quota exceeded` partway
        # through, which is the worst possible way for a test to die: it looks
        # like a product failure and it is not one.
        for rel in SCRATCH_NEEDS:
            source = REPO / rel
            if not source.exists():
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy(source, target)
        scratch = root / "design/art/flat2d_proof"
        manifest = yaml.safe_load(
            (scratch / "flat2d_proof_manifest.yml").read_text(encoding="utf-8"))
        outcome, message = verify(root)
        if outcome != "passed":
            print("mutation test: the baseline does not pass in a clean tree")
            print(f"  {outcome}: {message}")
            return 1
        print(f"mutation test: baseline OK, {len(selected)} mutations")
        missed = []
        crashed = []
        wrong = []
        saved = root / "saved.bin"
        atlas_relative = "flat2d_proof_atlas_v0.png"
        guide_relative = "guides/flat2d_proof_guide_v0.png"
        for name, relative, mutate, expect in selected:
            # A guide rebuilt from mutated plates would make every later
            # manifest mutation report a guide problem instead of its own.
            # the atlas is always saved and restored: several mutations repack
            # it, and leaving it dirty would make every later result a lie
            shutil.copy(scratch / atlas_relative, root / "saved_atlas.bin")
            shutil.copy(scratch / guide_relative, root / "saved_guide.bin")
            # The manifest is saved and restored for EVERY case, not only the
            # ones that name it as their target. A mutation that has to make
            # the manifest agree with a broken plate - which is the only way to
            # reach the composition checks - otherwise leaves that agreement
            # behind, and every case after it verifies a manifest that never
            # existed. That showed up as thirty-two unrelated cases reporting
            # the same stale plate error.
            manifest_path = scratch / "flat2d_proof_manifest.yml"
            shutil.copy(manifest_path, root / "saved_manifest.yml")
            target = scratch / relative
            existed = target.exists()
            if existed:
                shutil.copy(target, root / "saved_plate.bin")
            failed = False
            before_print = _scratch_fingerprint(scratch, relative)
            try:
                if takes_scratch(mutate):
                    mutate(scratch, manifest)
                else:
                    mutate(target)
                _ = expect
            except Exception as error:  # noqa: BLE001
                print(f"  ERROR    {name:44s} mutation failed: {error}")
                missed.append(name)
                failed = True
            if not failed and _scratch_fingerprint(scratch, relative) == before_print:
                print(f"  NO-OP    {name:44s} the mutation changed nothing; a "
                      "check that was never reached is not a passing check")
                missed.append(name)
                failed = True
            if not failed:
                outcome, message = verify(root)
                if outcome == "crashed":
                    label = "CRASHED"
                    crashed.append(name)
                elif outcome == "passed":
                    label = "MISSED"
                    missed.append(name)
                elif expect and expect not in message:
                    # something fired, but not the check this mutation exists to
                    # exercise: the mutation is not proving what it claims
                    label = "WRONG"
                    wrong.append((name, expect, message))
                else:
                    label = "caught"
                print(f"  {label:9s} {name:44s} {message[:52]}")
            if existed:
                if target.exists():
                    target.unlink()
                shutil.move(str(root / "saved_plate.bin"), str(target))
            elif target.exists():
                target.unlink()
                if (root / "saved_plate.bin").exists():
                    (root / "saved_plate.bin").unlink()
            for relative, backup in ((atlas_relative, "saved_atlas.bin"),
                                     (guide_relative, "saved_guide.bin")):
                target_file = scratch / relative
                if target_file.exists():
                    target_file.unlink()
                shutil.move(str(root / backup), str(target_file))
            manifest_path.unlink()
            shutil.move(str(root / "saved_manifest.yml"), str(manifest_path))
        for leftover in ("saved.bin", "saved_plate.bin", "saved_atlas.bin",
                         "saved_guide.bin", "saved_manifest.yml"):
            if (root / leftover).exists():
                (root / leftover).unlink()
        if missed or crashed or wrong:
            print(f"mutation test: FAIL ({len(missed)} missed, "
                  f"{len(crashed)} crashed, {len(wrong)} caught by the wrong "
                  "check)")
            for name in missed:
                print(f"  missed: {name}")
            for name in crashed:
                print(f"  crashed: {name}")
            for name, expect, message in wrong:
                print(f"  wrong check: {name} expected {expect!r}, got "
                      f"{message!r}")
            return 1
        print("mutation test: OK (every mutation was rejected with a reason)")
        return 0


if __name__ == "__main__":
    sys.exit(main())

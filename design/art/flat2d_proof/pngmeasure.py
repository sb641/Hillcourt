"""Alpha measurement for the flat 2D proof build (raw grayscale stream)."""

import subprocess


def alpha_map(path):
    """Return (width, height, {y: [x, ...]}) for pixels above threshold."""
    header = subprocess.check_output(
        ["identify", "-format", "%w %h", str(path)], text=True).split()
    width, height = int(header[0]), int(header[1])
    raw = subprocess.check_output(
        ["convert", str(path), "-alpha", "extract", "-depth", "8", "gray:-"])
    assert len(raw) == width * height, (len(raw), width, height)
    rows = {}
    for y in range(height):
        line = raw[y * width:(y + 1) * width]
        xs = [x for x in range(width) if line[x] > 8]
        if xs:
            rows[y] = xs
    return width, height, rows


def bbox(path):
    width, height, rows = alpha_map(path)
    if not rows:
        return None
    ys = sorted(rows)
    x0 = min(rows[y][0] for y in ys)
    x1 = max(rows[y][-1] for y in ys)
    return x1 - x0 + 1, ys[-1] - ys[0] + 1, x0, ys[0]

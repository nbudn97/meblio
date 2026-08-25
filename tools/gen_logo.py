"""Generate Meblio logo PNGs (mortise-and-tenon mark) with stdlib only.
Usage: python tools/gen_logo.py  → writes meblio.png (64) and meblio-512.png (512)
"""
import pathlib
import struct
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

BLUE = (37, 99, 235)        # #2563eb
WHITE = (255, 255, 255)

# Glyph polygons in the 48x48 viewBox coordinate space
POLY_A = [(9, 13), (30, 13), (30, 20), (9, 20)]
POLY_B = [(20, 35), (39, 35), (39, 28), (30, 28), (30, 23), (26, 23), (26, 28), (20, 28)]


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def inside_glyph(u, v):
    return point_in_poly(u, v, POLY_A) or point_in_poly(u, v, POLY_B)


def inside_rounded_square(u, v, radius):
    """Rounded square covering viewBox 2..46."""
    lo, hi = 2.0, 46.0
    if u < lo or u > hi or v < lo or v > hi:
        return False
    cx = min(max(u, lo + radius), hi - radius)
    cy = min(max(v, lo + radius), hi - radius)
    return (u - cx) ** 2 + (v - cy) ** 2 <= radius ** 2


def render(size):
    ss = 3  # supersampling factor
    buf = bytearray()
    scale = size / 48.0
    radius = 11.0
    for py in range(size):
        buf.append(0)  # filter: none
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = (px + (sx + 0.5) / ss) / scale
                    v = (py + (sy + 0.5) / ss) / scale
                    if inside_rounded_square(u, v, radius):
                        a += 1
                        if inside_glyph(u, v):
                            r += WHITE[0]; g += WHITE[1]; b += WHITE[2]
                        else:
                            r += BLUE[0]; g += BLUE[1]; b += BLUE[2]
            total = ss * ss
            if a == 0:
                buf.extend(b"\x00\x00\x00\x00")
            else:
                buf.extend(bytes((round(r / a), round(g / a), round(b / a), round(255 * a / total))))
    return bytes(buf)


def png_chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def save_png(path, size):
    raw = render(size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + png_chunk(b"IHDR", ihdr)
           + png_chunk(b"IDAT", zlib.compress(raw, 9))
           + png_chunk(b"IEND", b""))
    path.write_bytes(png)
    print(f"{path.name}: {size}x{size}, {path.stat().st_size} bytes")


if __name__ == "__main__":
    save_png(ROOT / "meblio.png", 64)
    save_png(ROOT / "meblio-512.png", 512)

"""Generate logo concept variants for Meblio (stdlib only, transparent PNGs).
Usage: python tools/logo_variants.py
Output: _design/variants/*.png (128px, 2 colors) + _design/preview.html
"""
import html
import pathlib
import struct
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "_design"
VARIANTS_DIR = OUT / "variants"

BLUE = (37, 99, 235)
GRAPHITE = (17, 24, 39)

# --- Concepts: list of polygons in 48x48 viewBox space (+ optional circles) ---

def concept_chair():
    """Профиль стула: спинка/задняя ножка, сиденье, передняя ножка."""
    return {
        "polys": [
            [(11, 9), (17, 9), (17, 39), (11, 39)],          # спинка + задняя ножка
            [(11, 20), (37, 20), (37, 27), (11, 27)],        # сиденье
            [(30, 27), (36, 27), (36, 39), (30, 39)],        # передняя ножка
        ],
        "circles": [],
    }


def concept_wardrobe():
    """Шкаф фронтально: рамка, две дверцы, ручки, ножки."""
    return {
        "polys": [
            [(9, 9), (39, 9), (39, 12), (9, 12)],            # верх
            [(9, 32), (39, 32), (39, 35), (9, 35)],          # низ
            [(9, 9), (12, 9), (12, 35), (9, 35)],            # лево
            [(36, 9), (39, 9), (39, 35), (36, 35)],          # право
            [(12, 12), (23, 12), (23, 32), (12, 32)],        # левая дверца
            [(25, 12), (36, 12), (36, 32), (25, 32)],        # правая дверца
            [(21, 21), (22, 21), (22, 26), (21, 26)],        # ручка Л
            [(26, 21), (27, 21), (27, 26), (26, 26)],        # ручка П
            [(13, 35), (16, 35), (16, 38), (13, 38)],        # ножка Л
            [(32, 35), (35, 35), (35, 38), (32, 38)],        # ножка П
        ],
        "circles": [],
    }


def concept_m_chair():
    """Строчная m, правая ножка длиннее — намёк на ножку стула."""
    return {
        "polys": [
            [(10, 10), (38, 10), (38, 38), (33, 38), (33, 22), (27, 22),
             (27, 34), (22, 34), (22, 22), (16, 22), (16, 34), (10, 34)],
        ],
        "circles": [],
    }


def concept_cube():
    """Изометрический куб из трёх граней с зазорами."""
    return {
        "polys": [
            [(24, 8.5), (36.5, 15), (24, 21.5), (11.5, 15)],              # верх
            [(10.5, 16.2), (22.8, 22.3), (22.8, 37.2), (10.5, 30.9)],     # лево
            [(25.2, 22.3), (37.5, 16.2), (37.5, 30.9), (25.2, 37.2)],     # право
        ],
        "circles": [],
    }


def concept_shelf():
    """Полка: доска + две стойки."""
    return {
        "polys": [
            [(7, 24), (41, 24), (41, 31), (7, 31)],           # доска
            [(13, 31), (19, 31), (19, 39), (13, 39)],         # стойка Л
            [(29, 31), (35, 31), (35, 39), (29, 39)],         # стойка П
        ],
        "circles": [],
    }


def concept_current_m():
    """Текущий знак: шкаф-«M» с ручкой."""
    return {
        "polys": [
            [(11, 34), (11, 14), (19, 14), (24, 23), (29, 14), (37, 14),
             (37, 34), (30, 34), (30, 23), (24, 33), (18, 23), (18, 34)],
        ],
        "circles": [(36, 13, 3)],
    }


CONCEPTS = [
    ("01-chair", "Стул-профиль", concept_chair),
    ("02-wardrobe", "Шкаф-фасад", concept_wardrobe),
    ("03-m-chair", "Буква m-стул", concept_m_chair),
    ("04-cube", "Куб (модули)", concept_cube),
    ("05-shelf", "Полка", concept_shelf),
    ("06-current-m", "Текущий «M»", concept_current_m),
]

COLORS = [("blue", BLUE), ("graphite", GRAPHITE)]


# --- Rendering ---

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


def inside_glyph(u, v, concept):
    for poly in concept["polys"]:
        if point_in_poly(u, v, poly):
            return True
    for cx, cy, r in concept["circles"]:
        if (u - cx) ** 2 + (v - cy) ** 2 <= r * r:
            return True
    return False


def render_glyph(size, color):
    """Transparent PNG: only the glyph, supersampled."""
    ss = 3
    scale = size / 48.0
    concept = None
    buf = bytearray()
    for py in range(size):
        buf.append(0)
        for px in range(size):
            hit = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = (px + (sx + 0.5) / ss) / scale
                    v = (py + (sy + 0.5) / ss) / scale
                    if inside_glyph(u, v, CONCEPT_OBJ):
                        hit += 1
            total = ss * ss
            if hit == 0:
                buf.extend(b"\x00\x00\x00\x00")
            else:
                alpha = round(255 * hit / total)
                buf.extend(bytes((*color, alpha)))
    return bytes(buf)


def png_chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def save_png(path, size, raw):
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + png_chunk(b"IHDR", ihdr)
           + png_chunk(b"IDAT", zlib.compress(raw, 9))
           + png_chunk(b"IEND", b""))
    path.write_bytes(png)


CONCEPT_OBJ = None


def main():
    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    global CONCEPT_OBJ
    cards = []
    for slug, title, factory in CONCEPTS:
        CONCEPT_OBJ = factory()
        for color_name, color in COLORS:
            raw = render_glyph(128, color)
            fname = f"{slug}-{color_name}.png"
            save_png(VARIANTS_DIR / fname, 128, raw)
        cards.append((slug, title))
        print(f"{slug}: rendered")

    # wordmark rendered as live text in preview
    cards.append(("07-wordmark", "Wordmark mebl.io"))

    # --- preview.html ---
    tiles = [("Светлый", "#ffffff", "#e5e7eb"), ("Тёмный", "#0b0f17", "#232a36"),
             ("Синий", "#2563eb", "transparent")]
    rows = []
    for slug, title in cards:
        imgs = []
        if slug == "07-wordmark":
            for tile_bg, tile_fg in [("#ffffff", "#111827"), ("#0b0f17", "#eef2f7")]:
                imgs.append(
                    f'<div class="tile" style="background:{tile_bg}">'
                    f'<span class="wordmark" style="color:{tile_fg}">mebl<span class="dot">.</span>io</span></div>'
                )
            sizes = ('<span class="wordmark" style="color:#111827;font-size:32px">mebl<span class="dot">.</span>io</span>'
                     '<span class="wordmark" style="color:#111827;font-size:16px">mebl<span class="dot">.</span>io</span>')
        else:
            for tile_bg, _fg, border in tiles:
                imgs.append(
                    f'<div class="tile" style="background:{tile_bg};border:1px solid {border}">'
                    f'<img src="variants/{slug}-blue.png" width="96" alt=""></div>'
                )
            sizes = "".join(
                f'<img src="variants/{slug}-blue.png" width="{w}" height="{w}" style="vertical-align:middle">'
                for w in (64, 32, 16)
            )
            sizes += (f'<span class="fake-tab"><img src="variants/{slug}-blue.png" width="16" height="16">'
                      f'Meblio — заказы</span>')
        rows.append(f"""
  <section class="variant">
    <h2>{html.escape(title)} <code>{slug}</code></h2>
    <div class="tiles">{"".join(imgs)}</div>
    <div class="sizes">{sizes}</div>
  </section>""")

    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Meblio — варианты логотипа</title>
<link href="https://fonts.googleapis.com/css2?family=Comfortaa:wght@700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>
  body {{ font-family: Inter, sans-serif; background: #f2f4f7; margin: 0; padding: 32px; }}
  h1 {{ font-size: 26px; }}
  .variant {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 24px; margin-bottom: 18px; }}
  .variant h2 {{ margin: 0 0 14px; font-size: 17px; }}
  .variant code {{ background: #f1f3f6; padding: 2px 8px; border-radius: 6px; font-size: 12px; color: #555; }}
  .tiles, .sizes {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }}
  .tile {{ width: 150px; height: 110px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }}
  .sizes {{ margin-top: 12px; gap: 22px; }}
  .fake-tab {{ display: inline-flex; gap: 7px; align-items: center; background: #dee1e6; border-radius: 8px 8px 0 0; padding: 6px 14px; font-size: 12px; color: #333; }}
  .wordmark {{ font-family: Comfortaa, sans-serif; font-weight: 700; font-size: 30px; }}
  .wordmark .dot {{ color: #2563eb; }}
  .tip {{ background: #fff8e6; border: 1px solid #f2e2b3; border-radius: 10px; padding: 12px 16px; font-size: 14px; }}
</style></head><body>
<h1>Meblio — варианты логотипа</h1>
<p class="tip">Смотрите на знак в 128px, затем в строке ниже — как он выглядит в 64/32/16px и во «вкладке браузера».
Скажите номер и правки (например: «номер 1, сиденье тоньше»).</p>
{"".join(rows)}
</body></html>"""
    (OUT / "preview.html").write_text(page, encoding="utf-8")
    print(f"preview: {(OUT / 'preview.html')}")


if __name__ == "__main__":
    main()

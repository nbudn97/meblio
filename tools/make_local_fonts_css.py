from pathlib import Path
import json
import re

root = Path(__file__).resolve().parents[1]
css = (root / "fonts" / "_google.css").read_text(encoding="utf-8")
mapping = json.loads((root / "fonts" / "_map.json").read_text(encoding="utf-8"))


def repl(m):
    u = m.group(0)
    name = mapping.get(u)
    if not name:
        raise SystemExit(f"missing {u}")
    return f"url(./{name})"


css2 = re.sub(r"https://fonts\.gstatic\.com/[^)]+\.woff2", repl, css)
css2 = re.sub(r"^/\*.*?\*/\n", "", css2, count=1, flags=re.S)
if "font-display" not in css2:
    css2 = css2.replace("src: url(", "font-display: swap;\n  src: url(")
header = "/* Self-hosted Inter + Comfortaa (Google Fonts subsets) */\n"
(root / "fonts" / "fonts.css").write_text(header + css2, encoding="utf-8", newline="\n")
print("bytes", (root / "fonts" / "fonts.css").stat().st_size)
print("gstatic", css2.count("fonts.gstatic.com"))
print("local", css2.count("./f"))

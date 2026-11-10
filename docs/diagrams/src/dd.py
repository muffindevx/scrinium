"""Small helpers that emit diagram-design (default skin, system fonts) HTML diagrams."""

import math

PAPER, INK, MUTED, SOFT = "#f5f5f5", "#2d3142", "#4f5d75", "#5a6580"
ACCENT, LINK = "#bf4520", "#2e5aa8"
SANS = "'Geist', system-ui, sans-serif"
MONO = "'Geist Mono', ui-monospace, monospace"

KINDS = {
    # fill, stroke, dash, tag stroke, tag text
    "focal": ("rgba(191,69,32,0.08)", ACCENT, None, "rgba(191,69,32,0.50)", ACCENT),
    "backend": ("#ffffff", INK, None, "rgba(45,49,66,0.40)", INK),
    "store": ("rgba(45,49,66,0.05)", MUTED, None, "rgba(79,93,117,0.50)", MUTED),
    "external": ("rgba(45,49,66,0.03)", "rgba(45,49,66,0.30)", None, "rgba(45,49,66,0.22)", SOFT),
    "input": ("rgba(79,93,117,0.10)", SOFT, None, "rgba(90,101,128,0.40)", SOFT),
    "optional": ("rgba(45,49,66,0.02)", "rgba(45,49,66,0.20)", "4,3", "rgba(45,49,66,0.22)", SOFT),
}
COLORS = {"muted": (MUTED, "arrow"), "accent": (ACCENT, "arrow-accent"), "link": (LINK, "arrow-link")}


def c4(v: float) -> int:
    return int(math.ceil(v / 4.0) * 4)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class D:
    def __init__(self, slug, w, h, title, desc, eyebrow):
        self.slug, self.w, self.h = slug, w, h
        self.title, self.desc, self.eyebrow = title, desc, eyebrow
        self.zones, self.arrows, self.labels, self.nodes, self.extra = [], [], [], [], []

    # ---------- primitives ----------
    def zone(self, x, y, w, h, label):
        lw = c4(len(label) * 7 * 0.76 + 12)
        self.zones.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="rgba(45,49,66,0.02)" '
            f'stroke="rgba(45,49,66,0.10)" stroke-width="0.8"/>'
            f'<rect x="{x + 12}" y="{y + 4}" width="{lw}" height="12" rx="2" fill="{PAPER}"/>'
            f'<text x="{x + 12 + lw / 2}" y="{y + 13}" fill="{SOFT}" font-size="7" font-family="{MONO}" '
            f'text-anchor="middle" letter-spacing="0.14em">{esc(label)}</text>'
        )

    def node(self, x, y, w, h, kind, tag, name, sub=None):
        fill, stroke, dash, tstroke, ttext = KINDS[kind]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        tw = c4(len(tag) * 7 * 0.70 + 8)
        cx = x + w / 2
        if sub:
            ny, sy = y + h / 2 + 4, y + h / 2 + 20
        else:
            ny, sy = y + h / 2 + 8, None
        out = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PAPER}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{d}/>'
            f'<rect x="{x + 8}" y="{y + 8}" width="{tw}" height="12" rx="2" fill="transparent" stroke="{tstroke}" stroke-width="0.8"/>'
            f'<text x="{x + 8 + tw / 2}" y="{y + 17}" fill="{ttext}" font-size="7" font-family="{MONO}" '
            f'text-anchor="middle" letter-spacing="0.08em">{esc(tag)}</text>'
            f'<text x="{cx}" y="{ny}" fill="{INK}" font-size="12" font-weight="600" font-family="{SANS}" '
            f'text-anchor="middle">{esc(name)}</text>'
        )
        if sub:
            out += (
                f'<text x="{cx}" y="{sy}" fill="{MUTED}" font-size="9" font-family="{MONO}" '
                f'text-anchor="middle">{esc(sub)}</text>'
            )
        self.nodes.append(out)

    def raw_node(self, svg):
        self.nodes.append(svg)

    def route(self, pts, color="muted", dash=False, width=None, r=8, marker=True):
        col, mk = COLORS[color]
        d = f"M {pts[0][0]},{pts[0][1]}"
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            dx0, dy0 = _sgn(x1 - x0), _sgn(y1 - y0)
            dx1, dy1 = _sgn(x2 - x1), _sgn(y2 - y1)
            d += f" L {x1 - dx0 * r},{y1 - dy0 * r} Q {x1},{y1} {x1 + dx1 * r},{y1 + dy1 * r}"
        d += f" L {pts[-1][0]},{pts[-1][1]}"
        sw = width or (1 if dash else 1.2)
        da = ' stroke-dasharray="5,4"' if dash else ""
        me = f' marker-end="url(#{mk})"' if marker else ""
        self.arrows.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw}"{da}{me}/>')

    def label(self, cx, cy, text, color="soft"):
        """Arrow label: mask centred at (cx, cy), 12px tall."""
        col = {"soft": SOFT, "accent": ACCENT, "link": LINK, "muted": MUTED}[color]
        w = c4(len(text) * 8 * 0.68 + 8)
        self.labels.append(
            f'<rect x="{cx - w / 2}" y="{cy - 6}" width="{w}" height="12" rx="2" fill="{PAPER}"/>'
            f'<text x="{cx}" y="{cy + 3}" fill="{col}" font-size="8" font-family="{MONO}" '
            f'text-anchor="middle" letter-spacing="0.06em">{esc(text)}</text>'
        )
        return w

    def hlabel(self, x0, x1, y, text, color="soft"):
        """Label above a horizontal segment at y, centred between x0 and x1."""
        return self.label((x0 + x1) / 2, y - 14, text, color)

    def vlabel(self, x, y0, y1, text, color="soft", side="right"):
        w = c4(len(text) * 8 * 0.68 + 8)
        cx = x + 8 + w / 2 if side == "right" else x - 8 - w / 2
        return self.label(cx, (y0 + y1) / 2, text, color)

    def legend(self, y, items):
        """items: list of (kind, text) where kind is a node kind or 'arrow-<color>[-dash]'."""
        out = (
            f'<line x1="40" y1="{y - 8}" x2="{self.w - 40}" y2="{y - 8}" stroke="rgba(45,49,66,0.10)" stroke-width="0.8"/>'
            f'<text x="40" y="{y + 8}" fill="{MUTED}" font-size="8" font-family="{MONO}" letter-spacing="0.18em">LEGEND</text>'
        )
        x = 40
        yy = y + 20
        for kind, text in items:
            if kind.startswith("arrow"):
                parts = kind.split("-")
                col, mk = COLORS[parts[1]]
                da = ' stroke-dasharray="4,3"' if "dash" in parts else ""
                out += (
                    f'<line x1="{x}" y1="{yy + 6}" x2="{x + 28}" y2="{yy + 6}" stroke="{col}" stroke-width="1.2"{da} '
                    f'marker-end="url(#{mk})"/>'
                    f'<text x="{x + 36}" y="{yy + 9}" fill="{MUTED}" font-size="8.5" font-family="{SANS}">{esc(text)}</text>'
                )
                x += 36 + c4(len(text) * 8.5 * 0.6) + 24
            else:
                fill, stroke, dash, _, _ = KINDS[kind]
                da = f' stroke-dasharray="{dash}"' if dash else ""
                out += (
                    f'<rect x="{x}" y="{yy + 1}" width="14" height="10" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"{da}/>'
                    f'<text x="{x + 20}" y="{yy + 9}" fill="{MUTED}" font-size="8.5" font-family="{SANS}">{esc(text)}</text>'
                )
                x += 20 + c4(len(text) * 8.5 * 0.6) + 24
        self.extra.append(out)

    # ---------- output ----------
    def svg(self):
        s = self.slug
        return (
            f'<svg viewBox="0 0 {self.w} {self.h}" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-labelledby="{s}-title {s}-desc">\n'
            f'<title id="{s}-title">{esc(self.title)}</title>\n<desc id="{s}-desc">{esc(self.desc)}</desc>\n'
            "<defs>"
            f'<marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{MUTED}"/></marker>'
            f'<marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{ACCENT}"/></marker>'
            f'<marker id="arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{LINK}"/></marker>'
            "</defs>\n"
            f'<rect width="100%" height="100%" fill="{PAPER}"/>\n'
            + "\n".join(self.zones + self.arrows + self.labels + self.nodes + self.extra)
            + "\n</svg>"
        )

    def html(self):
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(self.title)}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --color-paper: #f5f5f5; --color-ink: #2d3142; --color-muted: #4f5d75; --color-accent: #bf4520;
      --font-sans: 'Geist', system-ui, sans-serif;
      --font-serif: 'Instrument Serif', 'Noto Serif', serif;
      --font-mono: 'Geist Mono', ui-monospace, monospace;
    }}
    body {{ font-family: var(--font-sans); background: var(--color-paper); color: var(--color-ink);
      min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 2rem; }}
    .frame {{ max-width: 1200px; width: 100%; }}
    .diagram-container {{ width: 100%; overflow-x: auto; }}
    .eyebrow {{ font-family: var(--font-mono); font-size: 0.66rem; font-weight: 500; letter-spacing: 0.18em;
      text-transform: uppercase; color: var(--color-muted); margin-bottom: 0.5rem; }}
    h1 {{ font-family: var(--font-serif); font-size: clamp(1.5rem, 2.4vw + 0.75rem, 2rem); font-weight: 400;
      letter-spacing: -0.02em; line-height: 1.15; color: var(--color-ink); margin-bottom: 1.5rem; }}
    svg {{ width: 100%; min-width: {self.w}px; display: block; }}
    @media print {{ .diagram-container {{ overflow-x: visible; }} svg {{ min-width: 0; }} }}
  </style>
</head>
<body>
  <div class="frame">
    <p class="eyebrow">{esc(self.eyebrow)} · Scrinium</p>
    <h1>{esc(self.title)}</h1>
    <div class="diagram-container">
{self.svg()}
    </div>
  </div>
</body>
</html>
"""


def _sgn(v):
    return (v > 0) - (v < 0)

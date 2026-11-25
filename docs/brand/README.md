# Scrinium logo

## What it is

A scroll whose two rods are code braces. Between them sit three lines of text, and the middle one is
plum: the rule Scrinium read out of the code.

- **Mark:** `{`, `}` with round knobs at their tips (the ends of a scroll's rods) and three lines.
- **Wordmark:** `scrinium`, lowercase, Geist SemiBold, tracking −0.03 em. The mark sits to its left,
  at about 1.25× the cap height.
- **Accent:** only the rule line carries color. Everything else is the ink of the surface.

## Why this shape

A *scrinium* was the Roman cylindrical case that kept scrolls standing upright. The product does the same
for a codebase: it keeps the rules the code applies, each one with its citation. The mark joins the two
halves of that job in one figure, which is the double reading Paul Rand asked of a good mark: the braces
say *code*, the rods and lines say *scroll*.

It was checked against the three criteria Chermayeff & Geismar & Haviv use for every mark:

| Criterion | How the mark meets it |
|---|---|
| **Appropriate** | Code braces and a written rule: what the product reads and what it produces. |
| **Distinctive** | A plain code-block icon has no knobs. The rod ends and the single plum line are the hook. |
| **Simple** | Two strokes, four dots and three lines. Anyone can describe it, and doodle it, as "a scroll made of braces". |

Concepts that were drawn and rejected, and why:

- A case seen from the side with one scroll sticking out: it read as an electrical plug.
- The case seen from above, three scroll ends inside a ring: it read as a face or a bowling ball.
- A cylinder with scroll ends on top: it read as a database icon or a sushi roll.
- An `s` with rolled ends: it read as a hook or a circuit node.

## Files

| File | Use |
|---|---|
| `scrinium-mark.svg` | The mark on light surfaces. From 24 px up. |
| `scrinium-mark-dark.svg` | The mark on dark surfaces. |
| `scrinium-mark-small.svg` | 16–20 px: one plum line instead of three, so it stays legible. |
| `scrinium-mark-small-dark.svg` | The same, on dark surfaces. |
| `scrinium-icon.svg` | App icon: the mark on a plum tile. Works on light and dark. |
| `scrinium-favicon.svg` | Favicon: the small mark on the plum tile. |

## Colors

| Token | Light | Dark |
|---|---|---|
| Ink | `#1a1a1e` | `#f1f1f4` |
| Plum (rule line) | `#6b4e78` | `#d0b0df` |
| Plum tile (icon) | `#6b4e78` | `#6b4e78` |
| Rule line on the tile | `#e7d6ef` | `#e7d6ef` |

These are the console's own tokens (`--foreground` and `--primary` in `ui/src/index.css`).

## Do not

- Color the braces or the other lines. One accent, on the rule.
- Use the three-line mark below 24 px. Use the small one.
- Put the mark on a busy image, outline it, add shadows or gradients, or rotate it.
- Set the wordmark in capitals or another typeface.

## Sources

- Chermayeff & Geismar & Haviv's criteria (appropriate, distinctive, simple; "can you describe it or
  doodle it?"): [Print, interview with Chermayeff, Geismar and Haviv](https://www.printmag.com/branding-identity-design/marks-men-an-interview-with-ivan-chermayeff-tom-geismar-and-sagi-haviv-of-chermayeff-geisma/),
  [99% Invisible, Making a Mark](https://99percentinvisible.org/episode/making-mark-visual-identity-tom-geismar/).
- Paul Rand on identification over description, and simplicity: [Logos, Flags, and Escutcheons](https://www.paulrand.design/writing/articles/1991-logos-flags-and-escutcheons.html).
- The scrinium as a cylindrical case for upright scrolls: [Illustrated Companion to the Latin Dictionary, Scrinium](https://en.wikiversity.org/wiki/Illustrated_Companion_to_the_Latin_Dictionary/Scrinium),
  [Imperium Romanum, reconstruction of a scroll case](https://imperiumromanum.pl/en/curiosities/reconstruction-of-scroll-case/).

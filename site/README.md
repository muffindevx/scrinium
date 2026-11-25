# Scrinium landing page

The public page that explains what Scrinium does, built with [Astro](https://astro.build) as a
static site. It uses the console's tokens (warm neutrals, one plum accent, Geist) and the mark from
`docs/brand/`.

## Structure

- `src/pages/index.astro`: the page: hero, how it works, what to expect, who it is for, how to start.
- `src/components/RuleCard.astro`: the hero example, three lines of code and the rule written from them.
- `src/components/Story.astro`: "Cómo funciona", one rule told in four steps (read, write, decide,
  watch); the stage on the right follows the step in view. Without JavaScript every step still reads in order.
- `src/styles/global.css`: tokens for light and dark, taken from `ui/src/index.css`.

Every claim on the page is something Scrinium does today; keep it that way when editing.

## Run and build

```bash
pnpm install
pnpm dev      # http://127.0.0.1:4321/scrinium/
pnpm build    # static files in dist/
```

The site lives under `/scrinium` on both hosts. `SITE` and `BASE` set where:

| Host | `SITE` | `BASE` | Address |
|---|---|---|---|
| GitHub Pages (default) | `https://muffindevx.github.io` | `/scrinium` | https://muffindevx.github.io/scrinium/ |
| Codeberg Pages | `https://muffindevx.codeberg.page` | `/scrinium` | https://muffindevx.codeberg.page/scrinium/ |

## Publish

**GitHub Pages.** `.github/workflows/site.yml` builds and publishes on every push to `main` that
touches `site/`. Once, in the repository: Settings › Pages › Source: **GitHub Actions**.

**Codeberg Pages.** Codeberg serves a project's `pages` branch. From the repository root:

```bash
sh site/publish-codeberg.sh
```

It builds with the Codeberg address and force-pushes the built files alone to the `pages` branch of
the `origin` remote (pass another remote name as the first argument).

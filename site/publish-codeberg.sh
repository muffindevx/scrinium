#!/bin/sh
# Builds the landing page for Codeberg Pages and pushes it to the `pages` branch of the
# Codeberg remote, which Codeberg serves at https://muffindevx.codeberg.page/scrinium/.
# Run from anywhere: sh site/publish-codeberg.sh [remote]   (default remote: origin)
set -eu

remote="${1:-origin}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

cd "$here"
pnpm install --frozen-lockfile
SITE=https://muffindevx.codeberg.page BASE=/scrinium pnpm build

# A branch with only the built site, no history of its own: each publish replaces it.
cp -R dist/. "$out/"
cd "$out"
git init -q -b pages
git add -A
git -c user.name="$(git -C "$repo" config user.name)" -c user.email="$(git -C "$repo" config user.email)" \
  commit -q -m "Publish the landing page from $(git -C "$repo" rev-parse --short HEAD)"
git push -f "$(git -C "$repo" remote get-url "$remote")" pages:pages
echo "Publicado en la rama pages de $remote: https://muffindevx.codeberg.page/scrinium/"
echo "Si la pagina dice Not Found, falta el webhook git-pages del repositorio (ver site/README.md)."

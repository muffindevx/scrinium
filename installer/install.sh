#!/bin/sh
# Installs Scrinium on Linux and macOS from this folder (the release zip).
#
#   sh install.sh               installs, with its own window if there is a desktop
#   sh install.sh --no-window   without the Linux Qt window (about 540 MB less)
#   sh install.sh --yes         does not ask before installing uv
#
# Steps: uv (if missing, after confirming), Scrinium, its tools and models (about 3.2 GB, with
# pinned SHA256) and a final check with `scrinium doctor`. No sudo.
set -eu

UV_VERSION="0.13.7"
UV_INSTALLER_URL="https://releases.astral.sh/github/uv/releases/download/${UV_VERSION}/uv-installer.sh"
UV_INSTALLER_SHA256="d263ee68314cf5bea36b08e6bd9e2c4563240d7adb73756cf33af5c199b92db4"

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
window=auto
assume_yes=no
for arg in "$@"; do
    case "$arg" in
        --no-window) window=no ;;
        --yes) assume_yes=yes ;;
        *) echo "Opcion desconocida: $arg" >&2; exit 2 ;;
    esac
done

fail() {
    echo "$1" >&2
    exit 1
}

if [ "$(id -u)" = "0" ]; then
    fail "No instales Scrinium como root: usa tu cuenta normal, sin sudo."
fi

set -- "$here"/scrinium-*-py3-none-any.whl
wheel=$1
[ -f "$wheel" ] || fail "No encuentro el paquete de Scrinium (scrinium-*.whl) junto a este script."
[ -f "$here/constraints.txt" ] || fail "Falta constraints.txt junto a este script."

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

install_uv() {
    echo "Scrinium necesita uv (de Astral) para instalarse. Se instalara uv ${UV_VERSION} en tu"
    echo "cuenta, en ~/.local/bin, desde: ${UV_INSTALLER_URL}"
    if [ "$assume_yes" != yes ]; then
        printf "Continuar? [s/N] "
        read -r answer || answer=""
        case "$answer" in
            s|S|si|SI|Si) ;;
            *) fail "Cancelado. Instala uv por tu cuenta (https://docs.astral.sh/uv/) y repite." ;;
        esac
    fi
    command -v curl >/dev/null 2>&1 || fail "Hace falta curl para descargar uv."
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    curl --proto '=https' --tlsv1.2 -fsSL "$UV_INSTALLER_URL" -o "$scratch/uv-installer.sh"
    if [ "$(sha256_of "$scratch/uv-installer.sh")" != "$UV_INSTALLER_SHA256" ]; then
        fail "El instalador de uv descargado no coincide con el esperado: no se ejecuta."
    fi
    # The uv installer in turn checks the SHA256 of the binary it downloads.
    sh "$scratch/uv-installer.sh" --quiet
}

uv_bin=$(command -v uv 2>/dev/null || true)
if [ -z "$uv_bin" ] && [ -x "$HOME/.local/bin/uv" ]; then
    uv_bin="$HOME/.local/bin/uv"
fi
if [ -z "$uv_bin" ]; then
    install_uv
    uv_bin="$HOME/.local/bin/uv"
    [ -x "$uv_bin" ] || fail "uv no quedo instalado en $uv_bin."
fi

target="$wheel"
if [ "$(uname -s)" = "Linux" ] && [ "$window" = auto ]; then
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        target="scrinium[window] @ file://$wheel"
    fi
fi

echo "Instalando Scrinium..."
"$uv_bin" tool install --reinstall --quiet --python 3.12 "$target" -c "$here/constraints.txt"
scrinium="$("$uv_bin" tool dir --bin)/scrinium"
[ -x "$scrinium" ] || fail "Scrinium no quedo instalado en $scrinium."

echo "Descargando herramientas y modelos (unos 3,2 GB la primera vez)..."
"$scrinium" tools install || fail "No se pudo descargar todo. Repite este script para continuar."

echo
"$scrinium" doctor || true
echo
case ":$PATH:" in
    *":$("$uv_bin" tool dir --bin):"*)
        echo "Abre Scrinium con: scrinium start" ;;
    *)
        "$uv_bin" tool update-shell >/dev/null 2>&1 || true
        echo "Abre una terminal nueva y arranca Scrinium con: scrinium start"
        echo "(o ahora mismo: $scrinium start)" ;;
esac

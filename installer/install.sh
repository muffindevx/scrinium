#!/bin/sh
# Installs Scrinium on Linux and macOS from this folder (the release zip).
#
#   sh install.sh               installs, with its own window if there is a desktop
#   sh install.sh --no-window   without the Linux Qt window (about 540 MB less)
#   sh install.sh --yes         does not ask before installing uv
#
# Steps: uv (if missing, after confirming), Scrinium, its tools and the search model (about 0.7 GB, with
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
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

fail() {
    echo "$1" >&2
    exit 1
}

if [ "$(id -u)" = "0" ]; then
    fail "Do not install Scrinium as root: use your normal account, without sudo."
fi

set -- "$here"/scrinium-*-py3-none-any.whl
wheel=$1
[ -f "$wheel" ] || fail "The Scrinium package (scrinium-*.whl) was not found next to this script."
[ -f "$here/constraints.txt" ] || fail "constraints.txt is missing next to this script."

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

install_uv() {
    echo "Scrinium needs uv (by Astral) to install. uv ${UV_VERSION} will be installed in your"
    echo "account, in ~/.local/bin, from: ${UV_INSTALLER_URL}"
    if [ "$assume_yes" != yes ]; then
        printf "Continue? [y/N] "
        read -r answer || answer=""
        case "$answer" in
            y|Y|yes|YES|Yes) ;;
            *) fail "Cancelled. Install uv yourself (https://docs.astral.sh/uv/) and try again." ;;
        esac
    fi
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download uv."
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    curl --proto '=https' --tlsv1.2 -fsSL "$UV_INSTALLER_URL" -o "$scratch/uv-installer.sh"
    if [ "$(sha256_of "$scratch/uv-installer.sh")" != "$UV_INSTALLER_SHA256" ]; then
        fail "The downloaded uv installer does not match the expected one: it is not run."
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
    [ -x "$uv_bin" ] || fail "uv was not installed in $uv_bin."
fi

target="$wheel"
if [ "$(uname -s)" = "Linux" ] && [ "$window" = auto ]; then
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        target="scrinium[window] @ file://$wheel"
    fi
fi

echo "Installing Scrinium..."
"$uv_bin" tool install --reinstall --quiet --python 3.12 "$target" -c "$here/constraints.txt"
scrinium="$("$uv_bin" tool dir --bin)/scrinium"
[ -x "$scrinium" ] || fail "Scrinium was not installed in $scrinium."

echo "Downloading the tools and the search model (about 0.7 GB the first time)..."
"$scrinium" tools install || fail "Not everything could be downloaded. Run this script again to continue."

echo
"$scrinium" doctor || true
echo
case ":$PATH:" in
    *":$("$uv_bin" tool dir --bin):"*)
        echo "Open Scrinium with: scrinium start" ;;
    *)
        "$uv_bin" tool update-shell >/dev/null 2>&1 || true
        echo "Open a new terminal and start Scrinium with: scrinium start"
        echo "(or right now: $scrinium start)" ;;
esac

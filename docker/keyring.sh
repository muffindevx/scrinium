#!/bin/sh
# The container's keyring (ADR-016): a private D-Bus session and gnome-keyring's Secret
# Service, the backend ADR-006 accepts. The keyring file lives in the data volume, encrypted
# with a password that is never stored: it arrives on stdin, typed by the person in
# docker/scrinium.sh, and goes straight to gnome-keyring-daemon.
#
#   scrinium-keyring start    starts the bus and the daemon, locked (docker/entrypoint.sh)
#   scrinium-keyring status   prints none, locked, unlocked or unavailable
#   scrinium-keyring unlock   reads the password on stdin; creates the keyring the first time
#   scrinium-keyring reset    deletes the keyring and every key in it
#
# DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR come from the image, so Scrinium and every
# `docker exec` reach the same bus.
set -eu

export XDG_DATA_HOME=/data/keyring
keyrings="$XDG_DATA_HOME/keyrings"

daemon() {
    # gnome-keyring's prompts need a display; without one they fail at once, which keeps a
    # locked keyring locked. Their warnings would only fill the log.
    gnome-keyring-daemon "$@" --components=secrets >/dev/null 2>&1
}

status() {
    if [ ! -f "$keyrings/login.keyring" ]; then
        echo none
        return
    fi
    /opt/scrinium/bin/python -I -c '
from secretstorage import dbus_init
from secretstorage.collection import Collection
try:
    collection = Collection(dbus_init(), "/org/freedesktop/secrets/aliases/default")
    print("locked" if collection.is_locked() else "unlocked")
except Exception:
    print("unavailable")
'
}

case "${1:-}" in
    start)
        mkdir -p "$XDG_RUNTIME_DIR" "$keyrings"
        chmod 700 "$XDG_RUNTIME_DIR" "$XDG_DATA_HOME" "$keyrings"
        # keyring writes to the "default" collection; gnome-keyring creates "login".
        [ -f "$keyrings/default" ] || printf login > "$keyrings/default"
        dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --fork --nopidfile \
            --syslog-only
        daemon --start
        ;;
    status)
        status
        ;;
    unlock)
        # --unlock alone would start a second daemon; --replace hands the bus over to one
        # that opened the keyring with this password. A wrong password still exits 0, so
        # the result is read back.
        daemon --replace --unlock --daemonize
        tries=0
        while :; do
            state=$(status)
            case "$state" in
                unlocked) echo unlocked; exit 0 ;;
            esac
            tries=$((tries + 1))
            if [ "$tries" -ge 10 ]; then echo "$state"; exit 1; fi
            sleep 0.3
        done
        ;;
    reset)
        rm -f "$keyrings/login.keyring" "$keyrings/user.keystore"
        daemon --replace --daemonize
        echo none
        ;;
    *)
        echo "Usage: scrinium-keyring start|status|unlock|reset" >&2
        exit 2
        ;;
esac

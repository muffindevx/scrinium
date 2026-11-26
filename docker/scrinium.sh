#!/bin/sh
# Scrinium in Docker, with one script. Nothing else to install but Docker.
#
#   sh docker/scrinium.sh start [folder...] [--model-port N]
#        Builds the image the first time, downloads the search tools into the data volume,
#        starts the container and prints a one-time entry link. Each folder is mounted
#        read-only at /repos/<its name>. Run it again at any time: it reuses what exists and
#        adds new folders to the ones it already had.
#   sh docker/scrinium.sh link      a new one-time entry link
#   sh docker/scrinium.sh unlock    opens the keyring that holds provider keys (ADR-016);
#        creates it the first time. --reset deletes it and every key in it.
#   sh docker/scrinium.sh stop      stops the container; the data stays in its volume
#   sh docker/scrinium.sh logs      what Scrinium prints, live (Ctrl+C leaves it running)
#   sh docker/scrinium.sh update    the latest Scrinium: git pull, rebuild, restart
#   sh docker/scrinium.sh run ...   any scrinium command inside the container (doctor, attach...)
#
# Environment: SCRINIUM_PORT (default 47615) and SCRINIUM_DOCKER_NAME (default scrinium: the
# container's name; its data volume is <name>-data).
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=$(dirname -- "$here")
image=scrinium:local
name=${SCRINIUM_DOCKER_NAME:-scrinium}
volume="${name}-data"
network=scrinium
port=${SCRINIUM_PORT:-47615}
uid=$(id -u)
gid=$(id -g)
tab=$(printf '\t')
nl='
'

say() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sh docker/scrinium.sh <command>

  start [folder...] [--model-port N]    start Scrinium and print the entry link
  link                                  a new entry link
  unlock [--reset]                      opens the keyring that holds provider keys
  stop                                  stop Scrinium (your data stays)
  logs                                  what Scrinium prints, live (Ctrl+C to leave)
  update                                the latest version: pull, rebuild and restart
  run <scrinium command>                e.g. run doctor, run tools install local
EOF
}

need_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        fail "Docker was not found. Install it and run this script again:
  - Windows or macOS: Docker Desktop, https://docs.docker.com/desktop/
  - Linux: Docker Engine, https://docs.docker.com/engine/install/
    (on an Ubuntu or Debian server, follow the page for your distribution)"
    fi
    if ! problem=$(docker info 2>&1 >/dev/null); then
        case "$problem" in
            *"permission denied"*)
                fail "Docker is installed, but your account cannot use it. On Linux, run:
  sudo usermod -aG docker $(id -un)
then log out and back in (or restart)." ;;
            *)
                fail "Docker is installed but not running. Open Docker Desktop (on Linux:
  sudo systemctl start docker) and try again." ;;
        esac
    fi
    if [ "$uid" = 0 ]; then
        fail "Do not run Scrinium as root: use your normal account. On a server, create one:
  adduser yourname && usermod -aG docker yourname
and log in with it (ssh yourname@your-server)."
    fi
    case "$port" in
        ''|*[!0-9]*) fail "SCRINIUM_PORT must be a number." ;;
    esac
}

running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" = true ]
}

exists() {
    docker container inspect "$name" >/dev/null 2>&1
}

build_image() {
    say "Building the Scrinium image. The first time takes a few minutes..."
    docker build "$@" --build-arg "UID=$uid" --build-arg "GID=$gid" -t "$image" "$repo" \
        || fail "The image could not be built. The output above says why."
}

ensure_image() {
    built_for=$(docker image inspect -f '{{index .Config.Labels "scrinium.uid"}}' "$image" \
        2>/dev/null || true)
    if [ "$built_for" != "$uid" ]; then
        build_image
    fi
}

tty_flags() {
    if [ -t 0 ] && [ -t 1 ]; then printf '%s' "-it"; else printf '%s' "-i"; fi
}

# Downloads gitleaks, llama.cpp (CPU build) and the search model into the data volume, about
# 0.7 GB the first time. It only fetches what is missing.
install_tools() {
    say "Checking the tools and the two included models (about 3.2 GB the first time)..."
    # shellcheck disable=SC2046
    if ! docker run --rm $(tty_flags) --network "$network" -v "$volume:/data" "$image" \
        scrinium tools install --cpu; then
        say "Not everything could be downloaded. Scrinium starts anyway, without what is missing."
        say "Run 'sh docker/scrinium.sh start' again to retry."
    fi
}

# The folders mounted now, one "host<TAB>container" line each.
current_mounts() {
    exists || return 0
    docker inspect -f \
        '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\t"}}{{.Destination}}{{"\n"}}{{end}}{{end}}' \
        "$name" | sed '/^$/d'
}

current_model_ports() {
    exists || return 0
    docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name" \
        | sed -n 's/^SCRINIUM_MODEL_PORTS=//p'
}

owner_of() {
    stat -c %u "$1" 2>/dev/null || stat -f %u "$1"
}

add_folder() {
    [ -d "$1" ] || fail "Folder not found: $1"
    folder=$(CDPATH='' cd -- "$1" && pwd -P)
    case "$folder" in
        *,*|*"$nl"*|*"$tab"*) fail "Scrinium cannot mount a folder with commas or tabs in its name: $folder" ;;
    esac
    if [ "$(owner_of "$folder")" != "$uid" ]; then
        fail "The folder $folder belongs to another account. Scrinium only reads your own folders."
    fi
    if printf '%s\n' "$mounts" | cut -f1 | grep -Fqx -- "$folder"; then
        return 0
    fi
    base=$(basename -- "$folder")
    target="/repos/$base"
    n=2
    while printf '%s\n' "$mounts" | cut -f2 | grep -Fqx -- "$target"; do
        target="/repos/$base-$n"
        n=$((n + 1))
    done
    if [ -n "$mounts" ]; then mounts="$mounts$nl"; fi
    mounts="$mounts$folder$tab$target"
}

# The container's keyring (docker/keyring.sh, ADR-016). Its password is read here with echo
# off and handed to gnome-keyring on the container's stdin: never an argument, a variable of
# the container, a label or a file. printf is a shell builtin, so ps never shows it either.
keyring_state() {
    tries=0
    while :; do
        state=$(docker exec "$name" scrinium-keyring status 2>/dev/null || echo unavailable)
        if [ "$state" != unavailable ] || [ "$tries" -ge 20 ]; then
            printf '%s' "$state"
            return
        fi
        tries=$((tries + 1))
        sleep 0.5
    done
}

read_secret() {
    printf '%s' "$1" >/dev/tty
    trap 'stty echo </dev/tty; exit 130' INT TERM
    stty -echo </dev/tty
    IFS= read -r secret </dev/tty || secret=''
    stty echo </dev/tty
    trap - INT TERM
    printf '\n' >/dev/tty
}

send_secret() {
    result=$(printf '%s' "$secret" | docker exec -i "$name" scrinium-keyring unlock || true)
    secret=''
    [ "$result" = unlocked ]
}

create_keyring() {
    say "Scrinium keeps your providers' API keys (DeepSeek, GLM, Claude...) in an encrypted"
    say "keyring inside its volume. Choose its password: at least 12 characters."
    say "It is not stored anywhere: you type it each time Scrinium starts."
    say "If you forget it you lose the stored keys, not your data."
    while :; do
        read_secret "New keyring password: "
        first=$secret
        if [ "${#first}" -lt 12 ]; then
            say "It has ${#first} characters; use at least 12."
            continue
        fi
        read_secret "Repeat it: "
        if [ "$secret" != "$first" ]; then
            first=''
            say "They do not match. Again."
            continue
        fi
        first=''
        break
    done
    send_secret || fail "Could not create the keyring. See: sh docker/scrinium.sh logs"
    say "Keyring created and open. Reload the console and add your provider in Settings > AI models."
}

# $1 = optional: Enter skips it.
open_keyring() {
    tries=0
    while [ "$tries" -lt 3 ]; do
        if [ "$1" = optional ]; then
            read_secret "Keyring password (Enter to leave it locked): "
            if [ -z "$secret" ]; then
                say "Keyring locked: your providers will not work until 'sh docker/scrinium.sh unlock'."
                return 0
            fi
        else
            read_secret "Keyring password: "
        fi
        if send_secret; then
            say "Keyring open."
            return 0
        fi
        say "Wrong password."
        tries=$((tries + 1))
    done
    say "The keyring is still locked. Try again with: sh docker/scrinium.sh unlock"
    say "If you forgot the password: sh docker/scrinium.sh unlock --reset (deletes the stored keys)."
    return 1
}

# At start: if a keyring exists and is locked, offer to open it.
offer_unlock() {
    [ "$(keyring_state)" = locked ] || return 0
    if [ -t 0 ] && [ -t 1 ]; then
        say ""
        say "Your keyring is locked: Scrinium cannot use your providers' keys."
        open_keyring optional || true
    else
        say "Your keyring is locked. To use your providers: sh docker/scrinium.sh unlock"
    fi
}

unlock() {
    running || fail "Scrinium is not running. Start it with: sh docker/scrinium.sh start"
    [ -t 0 ] && [ -t 1 ] || fail "The keyring password is typed in a terminal: run this in one."
    if [ "${1:-}" = --reset ]; then
        say "This deletes the keyring and every provider and connector key it holds."
        say "Your projects and rules are not touched; you will have to add the providers again."
        printf 'Type DELETE to continue: '
        IFS= read -r answer || answer=''
        [ "$answer" = DELETE ] || fail "Cancelled: nothing was deleted."
        docker exec "$name" scrinium-keyring reset >/dev/null
        say "Keyring deleted. Create a new one with: sh docker/scrinium.sh unlock"
        return
    fi
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    case "$(keyring_state)" in
        unlocked) say "The keyring is already open." ;;
        none) create_keyring ;;
        locked) open_keyring required || exit 1 ;;
        *) fail "The container's keyring does not answer. Restart Scrinium: sh docker/scrinium.sh stop
and then start." ;;
    esac
}

print_link() {
    waited=0
    until answer=$(docker exec "$name" scrinium link 2>&1); do
        if ! running; then
            docker logs --tail 30 "$name" >&2 || true
            fail "Scrinium stopped while starting. The output above says why."
        fi
        waited=$((waited + 1))
        if [ "$waited" -ge 120 ]; then
            say "$answer" >&2
            fail "Scrinium did not answer after 2 minutes. See: sh docker/scrinium.sh logs"
        fi
        sleep 1
    done
    say ""
    say "$answer"
    say ""
    if [ -n "${SSH_CONNECTION:-}" ]; then
        # shellcheck disable=SC2086
        set -- $SSH_CONNECTION
        say "You are on a server over SSH. The link only works from this server, so open a"
        say "tunnel from YOUR computer, in another terminal, and leave it open:"
        say ""
        say "  ssh -N -L $port:127.0.0.1:$port $(id -un)@$3"
        say ""
        say "Then open the link above in your computer's browser."
    else
        say "Open the link above in this computer's browser."
    fi
    mounted=$(current_mounts)
    if [ -n "$mounted" ]; then
        say ""
        say "Your folders are projects in Scrinium (it can read them, never change them)."
        say "To add another one: sh docker/scrinium.sh start <folder>"
        say "Both models come included: rules can be extracted now. The answer model is small:"
        say "its accuracy is limited, so check each rule. For better ones, choose another in"
        say "Settings > AI models."
        printf '%s\n' "$mounted" | while IFS="$tab" read -r src dst; do
            say "  $src"
            if [ ! -e "$src/.git" ]; then
                say "    Not a Git repository: Changes, rule history and scrinium check need Git."
                say "    Rules are extracted anyway. To get them:"
                say "      cd $src && git init && git add -A && git commit -m \"First commit\""
                say "    then remove the source in Integrations and add the folder again."
            fi
        done
    fi
}

start() {
    mounts=$(current_mounts)
    model_ports=$(current_model_ports)
    while [ $# -gt 0 ]; do
        case "$1" in
            --model-port)
                [ $# -ge 2 ] || fail "--model-port needs a port number, e.g. --model-port 11434"
                case "$2" in ''|*[!0-9]*) fail "--model-port: '$2' is not a port." ;; esac
                case ",$model_ports," in
                    *",$2,"*) ;;
                    *) model_ports="${model_ports:+$model_ports,}$2" ;;
                esac
                shift 2 ;;
            --model-port=*)
                value=${1#--model-port=}
                shift
                set -- --model-port "$value" "$@" ;;
            -*) usage >&2; exit 2 ;;
            *) add_folder "$1"; shift ;;
        esac
    done

    ensure_image
    docker network inspect "$network" >/dev/null 2>&1 \
        || docker network create "$network" >/dev/null
    docker volume inspect "$volume" >/dev/null 2>&1 \
        || docker volume create "$volume" >/dev/null

    image_id=$(docker image inspect -f '{{.Id}}' "$image")
    wanted=$(printf '%s\n' "$image_id" "$port" "$model_ports" "$mounts" | cksum | cut -d' ' -f1)
    have=$(docker inspect -f '{{index .Config.Labels "scrinium.config"}}' "$name" 2>/dev/null || true)

    if exists && [ "$have" = "$wanted" ]; then
        if running; then
            say "Scrinium is already running."
        else
            install_tools
            docker start "$name" >/dev/null
            say "Scrinium started again."
        fi
        offer_unlock
        print_link
        return
    fi

    if exists; then
        say "Applying the changes (folders, port or version): Scrinium restarts."
        docker rm -f "$name" >/dev/null
    fi
    install_tools

    set -- --detach --name "$name" --init \
        --label "scrinium.config=$wanted" \
        --network "$network" \
        --add-host host.docker.internal:host-gateway \
        --publish "127.0.0.1:$port:$port" \
        --env "SCRINIUM_PORT=$port" \
        --env "SCRINIUM_MODEL_PORTS=$model_ports" \
        --env "SCRINIUM_DOCKER_MOUNTS=$(printf '%s' "$mounts" | tr '\n' ',')" \
        --volume "$volume:/data" \
        --cap-drop ALL --security-opt no-new-privileges
    while IFS="$tab" read -r src dst; do
        [ -n "$src" ] || continue
        set -- "$@" --mount "type=bind,source=$src,target=$dst,readonly"
    done <<EOF
$mounts
EOF
    docker run "$@" "$image" >/dev/null \
        || fail "Docker could not start the container. If it says port $port is in use,
pick another: SCRINIUM_PORT=47620 sh docker/scrinium.sh start"
    say "Scrinium started."
    offer_unlock
    print_link
}

stop() {
    if running; then
        docker stop --time 30 "$name" >/dev/null
        say "Scrinium stopped. Your data stays in the volume $volume."
    else
        say "Scrinium is not running."
    fi
}

update() {
    if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
        say "Downloading the latest version of the code..."
        git -C "$repo" pull --ff-only \
            || fail "git pull could not update (do you have local changes in this folder?)."
    fi
    build_image --pull
    if exists; then
        was_running=$(running && echo yes || echo no)
        if [ "$was_running" = yes ]; then
            start
        else
            say "New image ready. Start Scrinium with: sh docker/scrinium.sh start"
        fi
    else
        say "New image ready. Start Scrinium with: sh docker/scrinium.sh start"
    fi
}

command=${1:-}
[ $# -gt 0 ] && shift
case "$command" in
    start) need_docker; start "$@" ;;
    stop) need_docker; stop ;;
    link)
        need_docker
        running || fail "Scrinium is not running. Start it with: sh docker/scrinium.sh start"
        print_link ;;
    logs)
        need_docker
        exists || fail "Scrinium has never been started: sh docker/scrinium.sh start"
        docker logs --tail 100 --follow "$name" ;;
    update) need_docker; update ;;
    unlock) need_docker; unlock "$@" ;;
    run)
        need_docker
        running || fail "Scrinium is not running. Start it with: sh docker/scrinium.sh start"
        # shellcheck disable=SC2046
        docker exec $(tty_flags) "$name" scrinium "$@" ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac

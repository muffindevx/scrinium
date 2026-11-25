#!/bin/sh
# Scrinium in Docker, with one script. Nothing else to install but Docker.
#
#   sh docker/scrinium.sh start [folder...] [--model-port N]
#        Builds the image the first time, downloads the search tools into the data volume,
#        starts the container and prints a one-time entry link. Each folder is mounted
#        read-only at /repos/<its name>. Run it again at any time: it reuses what exists and
#        adds new folders to the ones it already had.
#   sh docker/scrinium.sh link      a new one-time entry link
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
Uso: sh docker/scrinium.sh <orden>

  start [carpeta...] [--model-port N]   arranca Scrinium y muestra el enlace de entrada
  link                                  un enlace de entrada nuevo
  stop                                  detiene Scrinium (tus datos se quedan)
  logs                                  lo que Scrinium escribe, en vivo (Ctrl+C para salir)
  update                                la ultima version: descarga, reconstruye y reinicia
  run <orden de scrinium>               p. ej. run doctor, run tools install local
EOF
}

need_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        fail "No encuentro Docker. Instalalo y vuelve a ejecutar este script:
  - Windows o macOS: Docker Desktop, https://docs.docker.com/desktop/
  - Linux: Docker Engine, https://docs.docker.com/engine/install/
    (en un servidor Ubuntu o Debian basta con seguir la pagina de tu distribucion)"
    fi
    if ! problem=$(docker info 2>&1 >/dev/null); then
        case "$problem" in
            *"permission denied"*)
                fail "Docker esta instalado, pero tu cuenta no puede usarlo. En Linux, ejecuta:
  sudo usermod -aG docker $(id -un)
y despues cierra la sesion y vuelve a entrar (o reinicia)." ;;
            *)
                fail "Docker esta instalado pero no esta en marcha. Abre Docker Desktop (en Linux:
  sudo systemctl start docker) y repite." ;;
        esac
    fi
    if [ "$uid" = 0 ]; then
        fail "No ejecutes Scrinium como root: usa tu cuenta normal. En un servidor, crea una:
  adduser tunombre && usermod -aG docker tunombre
y entra con ella (ssh tunombre@tu-servidor)."
    fi
    case "$port" in
        ''|*[!0-9]*) fail "SCRINIUM_PORT debe ser un numero." ;;
    esac
}

running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" = true ]
}

exists() {
    docker container inspect "$name" >/dev/null 2>&1
}

build_image() {
    say "Construyendo la imagen de Scrinium. La primera vez tarda unos minutos..."
    docker build "$@" --build-arg "UID=$uid" --build-arg "GID=$gid" -t "$image" "$repo" \
        || fail "No se pudo construir la imagen. Lo de arriba dice por que."
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
    say "Comprobando las herramientas y el modelo de busqueda (unos 0,7 GB la primera vez)..."
    # shellcheck disable=SC2046
    if ! docker run --rm $(tty_flags) --network "$network" -v "$volume:/data" "$image" \
        scrinium tools install --cpu; then
        say "No se pudo descargar todo. Scrinium arranca igual, sin busqueda por significado."
        say "Repite 'sh docker/scrinium.sh start' para reintentarlo."
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
    [ -d "$1" ] || fail "No existe la carpeta: $1"
    folder=$(CDPATH='' cd -- "$1" && pwd -P)
    case "$folder" in
        *,*|*"$nl"*|*"$tab"*) fail "Scrinium no puede montar una carpeta con comas o tabuladores en el nombre: $folder" ;;
    esac
    if [ "$(owner_of "$folder")" != "$uid" ]; then
        fail "La carpeta $folder es de otra cuenta. Scrinium solo lee carpetas tuyas."
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

print_link() {
    waited=0
    until answer=$(docker exec "$name" scrinium link 2>&1); do
        if ! running; then
            docker logs --tail 30 "$name" >&2 || true
            fail "Scrinium se detuvo al arrancar. Lo de arriba dice por que."
        fi
        waited=$((waited + 1))
        if [ "$waited" -ge 120 ]; then
            say "$answer" >&2
            fail "Scrinium no responde tras 2 minutos. Mira: sh docker/scrinium.sh logs"
        fi
        sleep 1
    done
    say ""
    say "$answer"
    say ""
    if [ -n "${SSH_CONNECTION:-}" ]; then
        # shellcheck disable=SC2086
        set -- $SSH_CONNECTION
        say "Estas en un servidor por SSH. El enlace solo funciona desde este servidor, asi que"
        say "abre un tunel desde TU ordenador, en otra terminal, y dejala abierta:"
        say ""
        say "  ssh -N -L $port:127.0.0.1:$port $(id -un)@$3"
        say ""
        say "Despues abre el enlace de arriba en el navegador de tu ordenador."
    else
        say "Abre el enlace de arriba en el navegador de este ordenador."
    fi
    mounted=$(current_mounts)
    if [ -n "$mounted" ]; then
        say ""
        say "Tus carpetas, para Integraciones > Anadir una fuente (Scrinium solo puede leerlas, no cambiarlas):"
        printf '%s\n' "$mounted" | while IFS="$tab" read -r src dst; do
            say "  $dst   (tu carpeta $src)"
        done
    fi
}

start() {
    mounts=$(current_mounts)
    model_ports=$(current_model_ports)
    while [ $# -gt 0 ]; do
        case "$1" in
            --model-port)
                [ $# -ge 2 ] || fail "--model-port necesita un numero de puerto, p. ej. --model-port 11434"
                case "$2" in ''|*[!0-9]*) fail "--model-port: '$2' no es un puerto." ;; esac
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
            say "Scrinium ya esta en marcha."
        else
            install_tools
            docker start "$name" >/dev/null
            say "Scrinium arrancado de nuevo."
        fi
        print_link
        return
    fi

    if exists; then
        say "Aplicando los cambios (carpetas, puerto o version): Scrinium se reinicia."
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
        --volume "$volume:/data" \
        --cap-drop ALL --security-opt no-new-privileges
    while IFS="$tab" read -r src dst; do
        [ -n "$src" ] || continue
        set -- "$@" --mount "type=bind,source=$src,target=$dst,readonly"
    done <<EOF
$mounts
EOF
    docker run "$@" "$image" >/dev/null \
        || fail "Docker no pudo arrancar el contenedor. Si dice que el puerto $port esta en uso,
elige otro: SCRINIUM_PORT=47620 sh docker/scrinium.sh start"
    say "Scrinium arrancado."
    print_link
}

stop() {
    if running; then
        docker stop --time 30 "$name" >/dev/null
        say "Scrinium detenido. Tus datos siguen en el volumen $volume."
    else
        say "Scrinium no esta en marcha."
    fi
}

update() {
    if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
        say "Descargando la ultima version del codigo..."
        git -C "$repo" pull --ff-only \
            || fail "git pull no pudo actualizar (¿hay cambios tuyos en esta carpeta?)."
    fi
    build_image --pull
    if exists; then
        was_running=$(running && echo yes || echo no)
        if [ "$was_running" = yes ]; then
            start
        else
            say "Imagen nueva lista. Arranca Scrinium con: sh docker/scrinium.sh start"
        fi
    else
        say "Imagen nueva lista. Arranca Scrinium con: sh docker/scrinium.sh start"
    fi
}

command=${1:-}
[ $# -gt 0 ] && shift
case "$command" in
    start) need_docker; start "$@" ;;
    stop) need_docker; stop ;;
    link)
        need_docker
        running || fail "Scrinium no esta en marcha. Arrancalo con: sh docker/scrinium.sh start"
        print_link ;;
    logs)
        need_docker
        exists || fail "Scrinium aun no se ha arrancado nunca: sh docker/scrinium.sh start"
        docker logs --tail 100 --follow "$name" ;;
    update) need_docker; update ;;
    run)
        need_docker
        running || fail "Scrinium no esta en marcha. Arrancalo con: sh docker/scrinium.sh start"
        # shellcheck disable=SC2046
        docker exec $(tty_flags) "$name" scrinium "$@" ;;
    -h|--help|help) usage ;;
    *) usage >&2; exit 2 ;;
esac

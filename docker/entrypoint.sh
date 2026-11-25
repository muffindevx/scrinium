#!/bin/sh
# Starts Scrinium inside the container.
#
# Scrinium only listens on 127.0.0.1 and only accepts requests whose Host is
# 127.0.0.1:<port>. Inside a container, 127.0.0.1 is the container's own loopback, which
# Docker's published port cannot reach. So socat opens two doors, and nothing else changes:
#
#   in:  the container's network address, same port -> Scrinium on 127.0.0.1:<port>.
#        Docker publishes it only on the host's 127.0.0.1 (docker/scrinium.sh), so the
#        browser still talks to 127.0.0.1:<port> and every Host, Origin, session and CSRF
#        check applies unchanged.
#   out: for each port in SCRINIUM_MODEL_PORTS, 127.0.0.1:<p> inside the container -> the
#        same port on the host machine (host.docker.internal). This lets a model server
#        running on the same machine (Ollama, llama-server, vLLM) be used as Scrinium's
#        "local server" at http://127.0.0.1:<p>. Only the host machine is ever a target.
set -eu

port="${SCRINIUM_PORT:-47615}"
address=$(hostname -i | cut -d' ' -f1)

socat "TCP-LISTEN:${port},bind=${address},reuseaddr,fork" "TCP:127.0.0.1:${port}" &

for model_port in $(printf '%s' "${SCRINIUM_MODEL_PORTS:-}" | tr ',' ' '); do
    case "$model_port" in
        ''|*[!0-9]*) echo "SCRINIUM_MODEL_PORTS: '$model_port' no es un puerto." >&2; exit 2 ;;
    esac
    socat "TCP-LISTEN:${model_port},bind=127.0.0.1,reuseaddr,fork" \
        "TCP:host.docker.internal:${model_port}" &
done

exec scrinium start --no-browser --port "$port"

# syntax=docker/dockerfile:1
# Scrinium in a container. Built and run by docker/scrinium.sh, which explains every step;
# see "Run it with Docker" in README.md.
#
#   docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t scrinium:local .
#
# UID and GID must be the account that owns the folders Scrinium reads: it refuses folders
# that belong to another account, and it never runs as root.

ARG NODE_IMAGE=node:26-bookworm-slim
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7
ARG PNPM_VERSION=12.14.0

FROM ${UV_IMAGE} AS uv

# 1. The console (React), built into the runtime's web_dist.
FROM ${NODE_IMAGE} AS ui
ARG PNPM_VERSION
RUN npm install --global --no-fund --no-audit "pnpm@${PNPM_VERSION}"
WORKDIR /src
COPY ui/package.json ui/pnpm-lock.yaml ui/
RUN pnpm --dir ui install --frozen-lockfile
COPY ui/ ui/
RUN mkdir -p runtime/src/scrinium && pnpm --dir ui build

# 2. The runtime and its locked dependencies (runtime/uv.lock), in /opt/scrinium.
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/scrinium
WORKDIR /src/runtime
COPY runtime/pyproject.toml runtime/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY runtime/ ./
COPY --from=ui /src/runtime/src/scrinium/web_dist src/scrinium/web_dist
RUN uv sync --frozen --no-dev --no-editable \
    && test -f /opt/scrinium/lib/python3.12/site-packages/scrinium/web_dist/index.html

# 3. gnome-keyring-daemon for the container's keyring (ADR-016). The package pulls in GTK for
# its password window, about 130 MB that a container without a display never uses: only the
# daemon and its GSettings schemas are taken from here. The password arrives on stdin.
FROM ${PYTHON_IMAGE} AS keyring
RUN apt-get update \
    && apt-get install --yes --no-install-recommends gnome-keyring \
    && rm -rf /var/lib/apt/lists/*

# 4. What runs: Python, the runtime, git (to read repositories), socat (the two doors, see
# docker/entrypoint.sh), libgomp (llama.cpp's CPU build), and dbus with the libraries of
# gnome-keyring-daemon (the container's keyring for provider keys, ADR-016). No model inside:
# the tools and models are downloaded into the data volume on first start.
FROM ${PYTHON_IMAGE}
ARG UID=1000
ARG GID=1000
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates dbus git libgck-1-0 \
        libgcr-base-3-1 libgomp1 socat \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${GID}" scrinium \
    && useradd --uid "${UID}" --gid "${GID}" --create-home --shell /usr/sbin/nologin scrinium \
    && mkdir -p /data /repos \
    && chown "${UID}:${GID}" /data \
    && chmod 700 /data
COPY --from=build /opt/scrinium /opt/scrinium
COPY --from=keyring /usr/bin/gnome-keyring-daemon /usr/bin/gnome-keyring-daemon
COPY --from=keyring /usr/share/glib-2.0/schemas /usr/share/glib-2.0/schemas
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/scrinium-container
COPY --chmod=755 docker/keyring.sh /usr/local/bin/scrinium-keyring
ENV PATH=/opt/scrinium/bin:$PATH \
    SCRINIUM_DATA_DIR=/data \
    SCRINIUM_PORT=47615 \
    PYTHONUNBUFFERED=1 \
    SCRINIUM_IN_DOCKER=1 \
    XDG_RUNTIME_DIR=/tmp/scrinium-run \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/scrinium-run/bus
LABEL org.opencontainers.image.title="Scrinium" \
      scrinium.uid="${UID}"
USER scrinium
WORKDIR /home/scrinium
EXPOSE 47615
CMD ["scrinium-container"]

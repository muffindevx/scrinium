.PHONY: setup build dist release start dev check format tools ollama models audit sbom secrets bench size

# Alternative to llama.cpp (SCRINIUM_MODEL_PROVIDER=ollama). Scrinium's own Ollama: only on 127.0.0.1, with the models inside the project, in .models/.
# It never touches the system Ollama. Leave it running in another terminal.
OLLAMA_PORT ?= 11435
OLLAMA_ENV = OLLAMA_HOST=127.0.0.1:$(OLLAMA_PORT) OLLAMA_MODELS=$(CURDIR)/.models

setup:
	cd runtime && uv sync
	pnpm --dir ui install

build:
	pnpm --dir ui build

# Package to install without Node or pnpm, with the versions from uv.lock:
#   uv tool install dist/scrinium-*.whl -c dist/constraints.txt
# Without the constraints, uv would install the latest release of every dependency.
# Builds the UI first, and fails if the wheel would ship without it.
dist: build
	rm -f dist/scrinium-*.whl dist/scrinium-*.zip dist/constraints.txt
	cd runtime && uv build --wheel -o ../dist
	cd runtime && uv export --frozen --no-dev --all-extras --no-emit-project --no-hashes \
		--no-header -o ../dist/constraints.txt > /dev/null
	uv run --no-project python -c "import glob, sys, zipfile; \
	names = zipfile.ZipFile(glob.glob('dist/scrinium-*.whl')[0]).namelist(); \
	sys.exit(0 if 'scrinium/web_dist/index.html' in names else 'El wheel no incluye la UI')"

# Scrinium options: make start ARGS="--port 8000 --no-browser"
# Zip for testers: the wheel, constraints.txt and the installers. No Node, no source code.
release: dist
	rm -rf dist/release && mkdir -p dist/release/scrinium
	cp dist/scrinium-*.whl dist/constraints.txt installer/install.sh installer/install.ps1 \
		installer/LEEME.txt installer/README.txt dist/release/scrinium/
	cd dist/release && zip -qr ../scrinium-$$(ls scrinium/scrinium-*.whl | sed 's/.*scrinium-\(.*\)-py3.*/\1/').zip scrinium
	rm -rf dist/release
	@ls dist/scrinium-*.zip

start:
	uv run --project runtime scrinium start $(ARGS)

# llama.cpp, gitleaks and the local models, in Scrinium's data directory.
tools:
	uv run --project runtime scrinium tools install $(ARGS)

dev:
	pnpm --dir ui dev

check:
	cd runtime && uv run ruff format --check src tests benchmarks
	cd runtime && uv run ruff check src tests benchmarks
	cd runtime && uv run mypy
	cd runtime && uv run pytest
	pnpm --dir ui lint
	pnpm --dir ui typecheck
	pnpm --dir ui test

format:
	cd runtime && uv run ruff format src tests benchmarks

# Fixed rule-extraction benchmark. With the real model and the resource
# limit (Linux with systemd only); never with Scrinium open at the same time. Example:
#   make bench DATA_DIR=~/.local/share/scrinium OUT=/tmp/bench.json BENCH_ARGS="--fragments 60"
bench:
	cd runtime && benchmarks/capped-run.sh $(OUT).monitor.log \
		uv run python benchmarks/bench_extraction.py --data-dir $(DATA_DIR) --out $(OUT) $(BENCH_ARGS)

# Installed size of Scrinium by direct dependency (no models or tools).
size:
	cd runtime && uv run python benchmarks/install_size.py

ollama:
	$(OLLAMA_ENV) ollama serve

# With `make ollama` running.
models:
	$(OLLAMA_ENV) ollama pull qwen3-embedding:0.6b
	$(OLLAMA_ENV) ollama pull qwen3:4b-instruct

# Supply chain (docs/security/SUPPLY_CHAIN.md). They need the network: not part of check.
# Fails on vulnerabilities in what ships; development ones are only reported.
audit:
	cd runtime && uv audit --no-dev --frozen --preview-features audit-command
	pnpm --dir ui audit --prod
	-pnpm --dir ui audit

# CycloneDX SBOM of what ships, in sbom/ (ignored by git).
sbom:
	mkdir -p sbom
	cd runtime && uv export --format cyclonedx1.5 --no-dev --frozen -o ../sbom/runtime.cdx.json > /dev/null
	pnpm --dir ui sbom --sbom-format cyclonedx --prod > sbom/ui.cdx.json

# Secrets in Scrinium's code. Fails without gitleaks installed: not scanning is not passing.
secrets:
	@command -v gitleaks >/dev/null || { echo "gitleaks no esta instalado: no se ha escaneado nada. Ver docs/security/SUPPLY_CHAIN.md"; exit 1; }
	gitleaks dir --config .gitleaks.toml --redact --no-banner .

"""Tool installation against a real HTTP server on loopback, with crafted files."""

import hashlib
import http.server
import io
import os
import subprocess
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from scrinium.infrastructure.tools.installer import ToolError, install, locate
from scrinium.infrastructure.tools.manifest import GITLEAKS, Asset, Tool, Variant


class _Files(http.server.BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.requests.append(self.path)
        body = self.files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    _Files.files = {}
    _Files.requests = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def _tar(entries: list[tarfile.TarInfo], contents: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for info in entries:
            data = contents.get(info.name)
            bundle.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _file(name: str, data: bytes, mode: int = 0o755) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info


def _llama_like() -> bytes:
    """Like the llama.cpp release: root folder, executable and a relative link to a lib."""
    folder = tarfile.TarInfo("llama-b1")
    folder.type = tarfile.DIRTYPE
    folder.mode = 0o755
    link = tarfile.TarInfo("llama-b1/libllama.so.0")
    link.type = tarfile.SYMTYPE
    link.linkname = "libllama.so.0.9.0"
    server_bin = b"#!/bin/sh\necho llama\n"
    lib = b"\x7fELF"
    return _tar(
        [
            folder,
            _file("llama-b1/llama-server", server_bin, 0o644),
            _file("llama-b1/libllama.so.0.9.0", lib),
            link,
        ],
        {"llama-b1/llama-server": server_bin, "llama-b1/libllama.so.0.9.0": lib},
    )


def _tool(base: str, path: str, body: bytes, executable: str, sha256: str | None = None) -> Tool:
    _Files.files[path] = body
    asset = Asset(base + path, sha256 or hashlib.sha256(body).hexdigest(), len(body), executable)
    return Tool("demo", "1.0", {("linux", "x64", Variant.CPU): asset})


def _install(tool: Tool, tools_dir: Path) -> Path:
    return install(tool, tools_dir, "linux", "x64", Variant.CPU)


def test_installs_a_verified_archive_once(server: str, tmp_path: Path) -> None:
    tool = _tool(server, "/llama.tar.gz", _llama_like(), "llama-b1/llama-server")
    tools_dir = tmp_path / "tools"

    executable = _install(tool, tools_dir)

    assert executable == tools_dir / "demo" / "1.0-cpu" / "llama-b1" / "llama-server"
    assert subprocess.run([executable], capture_output=True, text=True).stdout == "llama\n"
    assert (executable.parent / "libllama.so.0").resolve().name == "libllama.so.0.9.0"
    assert locate(tool, tools_dir, "linux", "x64") == executable
    # Already installed: it does not download again.
    assert _install(tool, tools_dir) == executable
    assert _Files.requests == ["/llama.tar.gz"]
    assert [p.name for p in (tools_dir / "demo").iterdir()] == ["1.0-cpu"]


def test_installs_a_zip_like_the_windows_releases(server: str, tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("LICENSE", "MIT")
        bundle.writestr("gitleaks.exe", b"MZ")
    tool = _tool(server, "/gitleaks.zip", buffer.getvalue(), "gitleaks.exe")
    assert _install(tool, tmp_path / "tools").read_bytes() == b"MZ"


def test_a_checksum_mismatch_installs_nothing(server: str, tmp_path: Path) -> None:
    tool = _tool(server, "/llama.tar.gz", _llama_like(), "llama-b1/llama-server", "0" * 64)
    tools_dir = tmp_path / "tools"
    with pytest.raises(ToolError, match="SHA256"):
        _install(tool, tools_dir)
    assert list((tools_dir / "demo").iterdir()) == []
    assert locate(tool, tools_dir, "linux", "x64") is None


def test_a_download_larger_than_announced_is_cut(server: str, tmp_path: Path) -> None:
    body = _llama_like()
    _Files.files["/llama.tar.gz"] = body
    asset = Asset(
        server + "/llama.tar.gz", hashlib.sha256(body).hexdigest(), 10, "llama-b1/llama-server"
    )
    tool = Tool("demo", "1.0", {("linux", "x64", Variant.CPU): asset})
    with pytest.raises(ToolError, match="mas grande"):
        _install(tool, tmp_path / "tools")


def test_an_archive_that_writes_outside_its_folder_is_refused(server: str, tmp_path: Path) -> None:
    escape = _file("../fuera", b"x")
    body = _tar([escape, _file("ok", b"x")], {escape.name: b"x", "ok": b"x"})
    tool = _tool(server, "/malo.tar.gz", body, "ok")
    with pytest.raises(ToolError, match="fuera de su carpeta"):
        _install(tool, tmp_path / "tools")
    assert not (tmp_path / "fuera").exists()
    assert locate(tool, tmp_path / "tools", "linux", "x64") is None


def test_an_absolute_path_stays_inside_its_folder(server: str, tmp_path: Path) -> None:
    body = _tar([_file("/absoluto-scrinium", b"x")], {"/absoluto-scrinium": b"x"})
    tool = _tool(server, "/absoluto.tar.gz", body, "absoluto-scrinium")
    assert _install(tool, tmp_path / "tools").is_relative_to(tmp_path / "tools")
    assert not Path("/absoluto-scrinium").exists()


def test_a_link_that_leaves_its_folder_is_refused(server: str, tmp_path: Path) -> None:
    link = tarfile.TarInfo("salida")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../.."
    tool = _tool(server, "/enlace.tar.gz", _tar([link], {}), "salida")
    with pytest.raises(ToolError, match="fuera de su carpeta"):
        _install(tool, tmp_path / "tools")


def test_only_https_is_accepted_outside_this_machine(tmp_path: Path) -> None:
    asset = Asset("http://example.com/x.tar.gz", "0" * 64, 1, "x")
    tool = Tool("demo", "1.0", {("linux", "x64", Variant.CPU): asset})
    with pytest.raises(ToolError, match="no permitido"):
        _install(tool, tmp_path / "tools")


def test_status_command_lists_what_is_missing(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "tools", "status"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "SCRINIUM_DATA_DIR": str(tmp_path / "data")},
    )
    assert result.returncode == 0
    assert "gitleaks 8.30.1: not installed" in result.stdout
    assert "scrinium tools install" in result.stdout


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("SCRINIUM_TEST_NETWORK") != "1",
    reason="downloads from GitHub; only with SCRINIUM_TEST_NETWORK=1",
)
def test_real_gitleaks_release_installs_and_runs(tmp_path: Path) -> None:
    from scrinium.infrastructure.tools.installer import current_platform

    os_name, arch = current_platform()
    executable = install(GITLEAKS, tmp_path / "tools", os_name, arch, Variant.DEFAULT)
    result = subprocess.run([executable, "version"], capture_output=True, text=True)
    assert result.stdout.strip() == GITLEAKS.version


def test_the_default_install_has_no_chat_model() -> None:
    """ADR-013: search by meaning stays local; the writing model is an add-on."""
    from scrinium.cli.tools import expand
    from scrinium.infrastructure.tools.manifest import CHAT_MODEL, EMBEDDING_MODEL, MODELS

    default = expand([])
    assert CHAT_MODEL.name not in default and EMBEDDING_MODEL.name in default
    assert "llama-server" in default and "gitleaks" in default
    assert expand(["local"]) == ["llama-server", CHAT_MODEL.name]
    assert expand(["gitleaks", "local", "llama-server"]) == [
        "gitleaks", "llama-server", CHAT_MODEL.name,
    ]  # fmt: skip
    # What the default no longer downloads: the chat model, the bulk of it.
    saved = CHAT_MODEL.size / sum(model.size for model in MODELS.values())
    assert saved > 0.75

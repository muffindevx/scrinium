"""Starts the real `scrinium start` process and tests it over real sockets."""

import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest


@dataclass
class Started:
    process: subprocess.Popen[str]
    port: int
    launch_url: str
    # What was printed before the link, which _spawn has already consumed.
    banner: str


def _spawn(data_dir: Path, *args: str, env: dict[str, str] | None = None) -> Started:
    process = subprocess.Popen(
        [sys.executable, "-m", "scrinium.cli.main", "start", "--port", "0", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "SCRINIUM_DATA_DIR": str(data_dir), **(env or {})},
    )
    assert process.stdout is not None
    launch_url = ""
    banner: list[str] = []
    for line in process.stdout:
        if "/auth/launch?token=" in line:
            launch_url = line.strip()
            break
        banner.append(line)
    match = re.match(r"http://127\.0\.0\.1:(\d+)/", launch_url)
    assert match, "the launch link was not printed"
    port = int(match.group(1))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return Started(process, port, launch_url, "".join(banner))
        time.sleep(0.05)
    process.kill()
    raise AssertionError("the server never started listening")


@pytest.fixture
def started(tmp_path: Path) -> Iterator[Started]:
    instance = _spawn(tmp_path / "data", "--no-browser")
    yield instance
    instance.process.terminate()
    instance.process.communicate(timeout=10)


def _non_loopback_ipv4() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # A UDP connect sends no packets: it only asks which interface would be used.
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address: str = probe.getsockname()[0]
    return None if address.startswith("127.") else address


def test_listens_only_on_loopback_according_to_the_kernel(started: Started) -> None:
    tcp_table = Path("/proc/net/tcp")
    if not tcp_table.exists():
        pytest.skip("/proc/net/tcp only exists on Linux")
    listen_state = "0A"
    port_hex = f"{started.port:04X}"
    addresses = [
        fields[1].split(":")[0]
        for line in tcp_table.read_text().splitlines()[1:]
        if (fields := line.split())[3] == listen_state and fields[1].endswith(f":{port_hex}")
    ]
    assert addresses == ["0100007F"]  # 127.0.0.1 in /proc format


def test_refuses_connections_on_the_machine_network_address(started: Started) -> None:
    address = _non_loopback_ipv4()
    if address is None:
        pytest.skip("this machine has no network address other than loopback")
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex((address, started.port)) != 0


def test_real_http_flow_and_clean_logs(tmp_path: Path) -> None:
    instance = _spawn(tmp_path / "data", "--no-browser", "--verbose")
    origin = f"http://127.0.0.1:{instance.port}"
    token = instance.launch_url.split("token=")[1]
    try:
        with httpx.Client(base_url=origin) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/health", headers={"Host": "evil.example"}).status_code == 403
            assert client.get("/api/v1/system/security-status").status_code == 401

            assert client.get(instance.launch_url).status_code == 303
            status = client.get("/api/v1/system/security-status").json()
            assert status["runtime"]["bind_address"] == "127.0.0.1"
            assert status["runtime"]["running_as_admin"] is False
            assert status["ai_mode"] == "local_only"
            assert client.get("/api/v1/privacy/data-flow").json()["remote_ai_providers"] == []
            cookie = client.cookies["scrinium_session"]
    finally:
        instance.process.terminate()
        _, stderr = instance.process.communicate(timeout=10)

    assert "path=/auth/launch" in stderr
    assert token not in stderr
    assert cookie not in stderr


def test_port_in_use_fails_without_issuing_a_token(tmp_path: Path) -> None:
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scrinium.cli.main",
                "start",
                "--no-browser",
                "--port",
                str(port),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "SCRINIUM_DATA_DIR": str(tmp_path)},
        )
    assert result.returncode == 1
    assert "Otro programa usa el puerto" in result.stderr
    assert "token=" not in result.stdout + result.stderr


def test_a_second_start_says_scrinium_is_already_open(tmp_path: Path) -> None:
    first = _spawn(tmp_path / "data", "--no-browser")
    try:
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "scrinium.cli.main",
                "start",
                "--no-browser",
                "--port",
                str(first.port),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "SCRINIUM_DATA_DIR": str(tmp_path / "data")},
        )
    finally:
        first.process.terminate()
        first.process.communicate(timeout=10)
    assert second.returncode == 1
    assert "Scrinium ya esta abierto" in second.stderr
    assert "token=" not in second.stdout + second.stderr


def test_by_default_the_terminal_only_shows_what_matters(tmp_path: Path) -> None:
    instance = _spawn(tmp_path / "data", "--no-browser")
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{instance.port}") as client:
            assert client.get("/health").json() == {"status": "ok", "service": "scrinium"}
    finally:
        instance.process.terminate()
        _, stderr = instance.process.communicate(timeout=10)
    assert "alembic" not in stderr
    assert "scrinium.access" not in stderr
    assert "uvicorn" not in stderr


def _start_with_env(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "start", "--no-browser", "--port", "0"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **env},
    )


def test_relative_data_dir_is_refused_before_issuing_a_token() -> None:
    result = _start_with_env({"SCRINIUM_DATA_DIR": "datos-relativos"})
    assert result.returncode == 1
    assert "ruta absoluta" in result.stderr
    assert "token=" not in result.stdout + result.stderr


def test_data_dir_on_a_disk_without_permissions_is_refused(tmp_path: Path) -> None:
    # Simulates NTFS: chmod does not fail but changes nothing. Patched only in the child process.
    sitecustomize = tmp_path / "patch" / "sitecustomize.py"
    sitecustomize.parent.mkdir()
    sitecustomize.write_text("import os\nos.chmod = lambda *args, **kwargs: None\n")
    data = tmp_path / "data"
    result = _start_with_env(
        {"SCRINIUM_DATA_DIR": str(data), "PYTHONPATH": str(sitecustomize.parent)}
    )
    assert result.returncode == 1
    assert "ignora los permisos" in result.stderr
    assert "token=" not in result.stdout + result.stderr
    assert list(data.iterdir()) == []


def test_invalid_port_variable_is_a_clear_error() -> None:
    result = _start_with_env({"SCRINIUM_PORT": "abc"})
    assert result.returncode == 2
    assert "SCRINIUM_PORT" in result.stderr
    assert "Traceback" not in result.stderr


def test_no_host_option_exists() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "start", "--host", "0.0.0.0"],  # noqa: S104
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2


def test_browser_receives_the_one_time_link_and_only_the_data_stores_are_written(
    tmp_path: Path,
) -> None:
    received = tmp_path / "browser-args"
    fake_browser = tmp_path / "fake-browser"
    fake_browser.write_text(f'#!/bin/sh\nprintf "%s" "$*" > "{received}"\n')
    fake_browser.chmod(0o700)
    data_dir = tmp_path / "data"

    instance = _spawn(data_dir, "--browser", env={"BROWSER": f"{fake_browser} %s"})
    try:
        deadline = time.monotonic() + 10
        while not received.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert received.read_text() == instance.launch_url
        written = {path.name for path in data_dir.iterdir()}
        # run/ holds the running instance's registry entry (scrinium stop and attach).
        assert written <= {
            "run",
            "scrinium.sqlite3",
            "scrinium.sqlite3-wal",
            "scrinium.sqlite3-shm",
            "vectors",
        }
        token = instance.launch_url.split("token=")[1].encode()
        files = [path for path in data_dir.rglob("*") if path.is_file()]
        assert all(token not in path.read_bytes() for path in files)
    finally:
        instance.process.terminate()
        instance.process.communicate(timeout=10)


def test_without_a_window_it_falls_back_to_the_browser_and_says_how_to_stop(
    tmp_path: Path,
) -> None:
    received = tmp_path / "browser-args"
    fake_browser = tmp_path / "fake-browser"
    fake_browser.write_text(f'#!/bin/sh\nprintf "%s" "$*" > "{received}"\n')
    fake_browser.chmod(0o700)
    # Without a desktop no window is possible, whether pywebview is installed or not.
    env = {"BROWSER": f"{fake_browser} %s", "DISPLAY": "", "WAYLAND_DISPLAY": ""}

    instance = _spawn(tmp_path / "data", env=env)
    try:
        deadline = time.monotonic() + 10
        while not received.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert received.read_text() == instance.launch_url
    finally:
        instance.process.terminate()
        instance.process.communicate(timeout=10)
    assert "Ctrl+C" in instance.banner


def test_browser_and_no_browser_cannot_be_combined(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scrinium.cli.main", "start", "--browser", "--no-browser"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "SCRINIUM_DATA_DIR": str(tmp_path)},
    )
    assert result.returncode == 2
    assert "token=" not in result.stdout + result.stderr

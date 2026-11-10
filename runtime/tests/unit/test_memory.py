"""Reading free memory on each system, and the floor computed from the total."""

from scrinium.application.ai.memory_guard import GIB, budget_for
from scrinium.core.memory import available_memory_bytes, linux_available, macos_available

MEMINFO = """MemTotal:       16303104 kB
MemFree:          553508 kB
MemAvailable:    4088904 kB
Buffers:          123456 kB
"""

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                            200000.
Pages inactive:                           30000.
Pages speculative:                         5000.
Pages wired down:                         80000.
Pages purgeable:                           1000.
"""


def test_linux_available_reads_memavailable() -> None:
    assert linux_available(MEMINFO) == 4088904 * 1024
    assert linux_available("MemTotal: 1 kB\n") is None


def test_macos_available_counts_reclaimable_pages() -> None:
    assert macos_available(VM_STAT) == (10000 + 30000 + 5000 + 1000) * 16384
    assert macos_available("Pages free: 1.\n") is None


def test_this_machine_gives_a_reading() -> None:
    available = available_memory_bytes()
    assert available is None or available > 0


def test_floor_is_two_gib_or_fifteen_percent() -> None:
    assert budget_for(8 * GIB).floor_bytes == 2 * GIB
    assert budget_for(16 * GIB).floor_bytes == int(16 * GIB * 0.15)
    assert budget_for(64 * GIB).floor_bytes == int(64 * GIB * 0.15)
    assert budget_for(None).floor_bytes == 2 * GIB
    assert budget_for(16 * GIB).critical_bytes == GIB

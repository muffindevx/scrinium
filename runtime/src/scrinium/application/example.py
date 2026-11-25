"""The example shop: a small Git repository with business rules, the same on every machine.

Used by `scrinium demo` (phase 5.3), in English like the rest of the command line, and by
`make demo`, in Spanish like the console. Fixed authors and dates, so the commits have the same
ids everywhere.
"""

import os
import subprocess
from pathlib import Path
from typing import Final

# A small shop: discounts, shipping and stock, with the kind of rules Scrinium looks for.
_FIRST_COMMIT: Final = {
    "README.md": (
        "# Tienda de ejemplo\n\n"
        "Proyecto de juguete para probar Scrinium: pedidos, descuentos, envios y stock.\n"
    ),
    # The policy as the business wrote it. Two sentences will disagree with the code (phase 8.2):
    # free shipping moves to 40 EUR in the second commit, and discounts were always capped at
    # 25 %, not 30 %.
    "docs/politicas.md": (
        "# Políticas de la tienda\n\n"
        "El envío es gratis desde 50 EUR en la península; a las islas siempre se cobra.\n\n"
        "Un pedido no reserva más de 10 unidades de un producto.\n\n"
        "- Los descuentos sumados nunca pasan del 30 % del subtotal.\n"
        "- Un cupón solo vale en pedidos de 30 EUR o más.\n"
    ),
    "tienda/__init__.py": "",
    "tienda/descuentos.py": '''"""Descuentos que se aplican a un pedido."""

from decimal import Decimal

DESCUENTO_CLIENTE_VIP = Decimal("0.10")
DESCUENTO_MAXIMO = Decimal("0.25")
PEDIDO_MINIMO_CUPON = Decimal("30")


def descuento_total(subtotal: Decimal, es_vip: bool, cupon: Decimal | None) -> Decimal:
    """Los clientes VIP tienen un 10 %; un cupon solo vale en pedidos de 30 EUR o mas.

    Los descuentos se suman, pero nunca pasan del 25 % del subtotal.
    """
    porcentaje = Decimal("0")
    if es_vip:
        porcentaje += DESCUENTO_CLIENTE_VIP
    if cupon is not None and subtotal >= PEDIDO_MINIMO_CUPON:
        porcentaje += cupon
    return subtotal * min(porcentaje, DESCUENTO_MAXIMO)
''',
    "tienda/envios.py": '''"""Coste del envio."""

from decimal import Decimal

ENVIO_GRATIS_DESDE = Decimal("50")
TARIFA_PENINSULA = Decimal("4.90")
TARIFA_ISLAS = Decimal("9.90")


def coste_envio(subtotal: Decimal, destino: str) -> Decimal:
    """Envio gratis desde 50 EUR a la peninsula; a las islas siempre se cobra."""
    if destino == "islas":
        return TARIFA_ISLAS
    if subtotal >= ENVIO_GRATIS_DESDE:
        return Decimal("0")
    return TARIFA_PENINSULA
''',
    "tienda/stock.py": '''"""Reservas de stock al confirmar un pedido."""

MAXIMO_POR_PEDIDO = 10


class SinStock(Exception):
    pass


def reservar(disponible: int, pedidas: int) -> int:
    """No se reservan mas de 10 unidades de un producto por pedido, ni mas de las que hay."""
    if pedidas <= 0:
        raise ValueError("hay que pedir al menos una unidad")
    if pedidas > MAXIMO_POR_PEDIDO:
        raise ValueError(f"como mucho {MAXIMO_POR_PEDIDO} unidades por pedido")
    if pedidas > disponible:
        raise SinStock(f"solo quedan {disponible}")
    return disponible - pedidas
''',
}
# The second commit changes a rule: something for «what changed and why».
_SECOND_COMMIT: Final = {
    "tienda/envios.py": _FIRST_COMMIT["tienda/envios.py"]
    .replace('ENVIO_GRATIS_DESDE = Decimal("50")', 'ENVIO_GRATIS_DESDE = Decimal("40")')
    .replace("desde 50 EUR", "desde 40 EUR"),
}
_COMMITS: Final = [
    (_FIRST_COMMIT, "Primera version de la tienda", "2026-01-10T10:00:00+01:00"),
    (
        _SECOND_COMMIT,
        "Envio gratis desde 40 EUR por la campana de primavera",
        "2026-03-02T09:30:00+01:00",
    ),
]

# The same shop in English, for `scrinium demo`: same rules, same values, same two commits.
_ENGLISH_FIRST_COMMIT: Final = {
    "README.md": (
        "# Example shop\n\nA toy project to try Scrinium: orders, discounts, shipping and stock.\n"
    ),
    "docs/policies.md": (
        "# Shop policies\n\n"
        "Shipping is free from 50 EUR on the mainland; the islands always pay shipping.\n\n"
        "An order never reserves more than 10 units of a product.\n\n"
        "- Discounts added together never exceed 30 % of the subtotal.\n"
        "- A coupon only applies to orders of 30 EUR or more.\n"
    ),
    "shop/__init__.py": "",
    "shop/discounts.py": '''"""Discounts applied to an order."""

from decimal import Decimal

VIP_CUSTOMER_DISCOUNT = Decimal("0.10")
MAXIMUM_DISCOUNT = Decimal("0.25")
COUPON_MINIMUM_ORDER = Decimal("30")


def total_discount(subtotal: Decimal, is_vip: bool, coupon: Decimal | None) -> Decimal:
    """VIP customers get 10 %; a coupon only applies to orders of 30 EUR or more.

    Discounts add up, but never exceed 25 % of the subtotal.
    """
    percentage = Decimal("0")
    if is_vip:
        percentage += VIP_CUSTOMER_DISCOUNT
    if coupon is not None and subtotal >= COUPON_MINIMUM_ORDER:
        percentage += coupon
    return subtotal * min(percentage, MAXIMUM_DISCOUNT)
''',
    "shop/shipping.py": '''"""Shipping cost."""

from decimal import Decimal

FREE_SHIPPING_FROM = Decimal("50")
MAINLAND_RATE = Decimal("4.90")
ISLANDS_RATE = Decimal("9.90")


def shipping_cost(subtotal: Decimal, destination: str) -> Decimal:
    """Free shipping from 50 EUR on the mainland; the islands always pay."""
    if destination == "islands":
        return ISLANDS_RATE
    if subtotal >= FREE_SHIPPING_FROM:
        return Decimal("0")
    return MAINLAND_RATE
''',
    "shop/stock.py": '''"""Stock reservations when an order is confirmed."""

MAXIMUM_PER_ORDER = 10


class OutOfStock(Exception):
    pass


def reserve(available: int, requested: int) -> int:
    """No more than 10 units of a product per order, and no more than there are."""
    if requested <= 0:
        raise ValueError("at least one unit must be ordered")
    if requested > MAXIMUM_PER_ORDER:
        raise ValueError(f"at most {MAXIMUM_PER_ORDER} units per order")
    if requested > available:
        raise OutOfStock(f"only {available} left")
    return available - requested
''',
}
_ENGLISH_COMMITS: Final = [
    (_ENGLISH_FIRST_COMMIT, "First version of the shop", "2026-01-10T10:00:00+01:00"),
    (
        {
            "shop/shipping.py": _ENGLISH_FIRST_COMMIT["shop/shipping.py"]
            .replace('FREE_SHIPPING_FROM = Decimal("50")', 'FREE_SHIPPING_FROM = Decimal("40")')
            .replace("from 50 EUR", "from 40 EUR"),
        },
        "Free shipping from 40 EUR for the spring campaign",
        "2026-03-02T09:30:00+01:00",
    ),
]


def git(repo: Path, *args: str, date: str | None = None) -> None:
    """git with the example's fixed author, no user configuration, and an optional date."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ana Ejemplo",
        "GIT_AUTHOR_EMAIL": "ana@ejemplo.invalid",
        "GIT_COMMITTER_NAME": "Ana Ejemplo",
        "GIT_COMMITTER_EMAIL": "ana@ejemplo.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)  # noqa: S603, S607


def seed_repository(repo: Path, english: bool = False) -> None:
    if (repo / ".git").is_dir():
        return
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    for files, message, date in _ENGLISH_COMMITS if english else _COMMITS:
        for name, text in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", message, date=date)

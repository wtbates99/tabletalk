#!/usr/bin/env python3
"""Build a deterministic, edge-case-heavy DuckDB fixture for TableTalk evals."""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import duckdb
except ImportError:
    raise SystemExit("DuckDB is required. Run: pip install 'tabletalk[duckdb]'")

SEED = 20260723
CUSTOMER_COUNT = 2_500
PRODUCT_COUNT = 100
ORDER_COUNT = 12_000

REGIONS = ["northeast", "southeast", "midwest", "southwest", "west"]
SEGMENTS = ["consumer", "small_business", "enterprise"]
CATEGORIES = [
    "electronics",
    "apparel",
    "books",
    "home",
    "outdoors",
    "fitness",
    "office",
    "beauty",
    "food",
    "toys",
]
STATUSES = ["delivered", "shipped", "pending", "cancelled"]
STATUS_WEIGHTS = [0.72, 0.10, 0.10, 0.08]
REFUND_REASONS = ["damaged", "not_as_described", "late_delivery", "changed_mind"]


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def insert_rows(connection, table: str, rows: list[tuple]) -> None:
    """Bulk-load Python rows through a temporary CSV for fast, dependency-free seeding."""
    if not rows:
        return
    null_marker = "__TABLETALK_NULL__"
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False) as file:
            temporary_path = Path(file.name)
            writer = csv.writer(file)
            writer.writerows(
                [null_marker if value is None else value for value in row] for row in rows
            )
        escaped_path = str(temporary_path).replace("'", "''")
        connection.execute(f"COPY {table} FROM '{escaped_path}' (FORMAT CSV, NULL '{null_marker}')")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_fixture(path: Path) -> None:
    """Create a repeatable analytical dataset with realistic data-quality edges."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    connection = duckdb.connect(str(path))

    for table in [
        "refunds",
        "order_items",
        "orders",
        "products",
        "categories",
        "customers",
        "employee_sensitive",
    ]:
        connection.execute(f"DROP TABLE IF EXISTS {table}")

    # One transaction keeps tens of thousands of deterministic fixture inserts fast.
    connection.execute("BEGIN TRANSACTION")
    connection.execute(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            email VARCHAR NOT NULL,
            region VARCHAR,
            segment VARCHAR NOT NULL,
            signup_date DATE NOT NULL,
            active BOOLEAN NOT NULL
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            unit_price DECIMAL(12, 2) NOT NULL,
            unit_cost DECIMAL(12, 2) NOT NULL,
            active BOOLEAN NOT NULL
        );
        CREATE TABLE orders (
            id BIGINT PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            status VARCHAR NOT NULL,
            total_amount DECIMAL(12, 2) NOT NULL,
            discount_amount DECIMAL(12, 2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE order_items (
            id BIGINT PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL,
            unit_price DECIMAL(12, 2) NOT NULL,
            discount DECIMAL(12, 2) NOT NULL
        );
        CREATE TABLE refunds (
            id BIGINT PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES orders(id),
            amount DECIMAL(12, 2) NOT NULL,
            reason VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE employee_sensitive (
            employee_id INTEGER PRIMARY KEY,
            full_name VARCHAR NOT NULL,
            ssn VARCHAR NOT NULL,
            salary DECIMAL(12, 2) NOT NULL
        );
        """
    )

    insert_rows(
        connection,
        "categories",
        [(index, category) for index, category in enumerate(CATEGORIES, 1)],
    )

    products = []
    for product_id in range(1, PRODUCT_COUNT + 1):
        category_id = ((product_id - 1) % len(CATEGORIES)) + 1
        price = money(rng.uniform(8, 750))
        cost = money(price * Decimal(str(rng.uniform(0.28, 0.72))))
        products.append(
            (
                product_id,
                f"SKU-{product_id:04d}",
                f"{CATEGORIES[category_id - 1].title()} Product {product_id:03d}",
                category_id,
                price,
                cost,
                product_id % 23 != 0,
            )
        )
    insert_rows(connection, "products", products)

    customers = []
    signup_start = date(2023, 1, 1)
    for customer_id in range(1, CUSTOMER_COUNT + 1):
        region = None if customer_id % 97 == 0 else rng.choice(REGIONS)
        segment = rng.choices(SEGMENTS, weights=[0.76, 0.19, 0.05], k=1)[0]
        signup_date = signup_start + timedelta(days=rng.randrange(1_250))
        customers.append(
            (
                customer_id,
                f"Customer {customer_id:05d}",
                f"customer{customer_id:05d}@example.test",
                region,
                segment,
                signup_date,
                customer_id % 31 != 0,
            )
        )
    insert_rows(connection, "customers", customers)

    price_by_product = {row[0]: row[4] for row in products}
    orders = []
    order_items = []
    refunds = []
    item_id = 1
    refund_id = 1
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    # Keep the final 100 customers order-free to exercise anti-join questions.
    for order_id in range(1, ORDER_COUNT + 1):
        customer_id = rng.randint(1, CUSTOMER_COUNT - 100)
        status = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        created_at = start + timedelta(seconds=rng.randrange(0, 730 * 24 * 60 * 60))
        line_count = rng.randint(1, 5)
        subtotal = Decimal("0")
        staged_items = []
        for _ in range(line_count):
            product_id = rng.randint(1, PRODUCT_COUNT)
            quantity = rng.randint(1, 4)
            unit_price = price_by_product[product_id]
            line_discount = money(
                unit_price * quantity * Decimal(str(rng.choice([0, 0, 0, 0.05, 0.10])))
            )
            subtotal += unit_price * quantity - line_discount
            staged_items.append(
                (
                    item_id,
                    order_id,
                    product_id,
                    quantity,
                    unit_price,
                    line_discount,
                )
            )
            item_id += 1
        order_discount = money(subtotal * Decimal("0.05")) if order_id % 17 == 0 else money(0)
        total = money(max(Decimal("0"), subtotal - order_discount))
        orders.append(
            (
                order_id,
                customer_id,
                status,
                total,
                order_discount,
                created_at,
            )
        )
        order_items.extend(staged_items)

        if status == "delivered" and order_id % 19 == 0:
            refund_amount = total if order_id % 7 == 0 else money(total * Decimal("0.35"))
            refunds.append(
                (
                    refund_id,
                    order_id,
                    refund_amount,
                    rng.choice(REFUND_REASONS),
                    created_at + timedelta(days=rng.randint(1, 45)),
                )
            )
            refund_id += 1

    # Explicit boundary rows make timezone and zero-value behavior stable.
    orders.extend(
        [
            (
                ORDER_COUNT + 1,
                1,
                "delivered",
                money(1000),
                money(0),
                datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            ),
            (
                ORDER_COUNT + 2,
                2,
                "cancelled",
                money(9999),
                money(0),
                datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
            ),
            (
                ORDER_COUNT + 3,
                3,
                "delivered",
                money(0),
                money(0),
                datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            ),
        ]
    )
    insert_rows(connection, "orders", orders)
    insert_rows(connection, "order_items", order_items)
    insert_rows(connection, "refunds", refunds)

    employees = [
        (
            employee_id,
            f"Employee {employee_id:03d}",
            f"{100 + employee_id:03d}-{20 + employee_id % 70:02d}-{1000 + employee_id:04d}",
            money(50_000 + employee_id * 1_375.25),
        )
        for employee_id in range(1, 121)
    ]
    insert_rows(connection, "employee_sensitive", employees)
    connection.execute("COMMIT")
    connection.execute("ANALYZE")

    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in [
            "customers",
            "products",
            "orders",
            "order_items",
            "refunds",
            "employee_sensitive",
        ]
    }
    connection.close()
    print(f"Created {path}")
    print("  " + ", ".join(f"{table}={count:,}" for table, count in counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "fixtures" / "sales_eval.duckdb",
    )
    args = parser.parse_args()
    build_fixture(args.output.resolve())


if __name__ == "__main__":
    main()

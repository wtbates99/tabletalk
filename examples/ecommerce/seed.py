#!/usr/bin/env python3
"""Create a large, deterministic ecommerce database for the TableTalk demo.

The generated data is intentionally broad enough for meaningful revenue,
inventory, customer, and marketing conversations while remaining fast to
rebuild on a laptop. Re-running the script recreates the database in place.
"""

from __future__ import annotations

import csv
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import duckdb
except ImportError:
    print("DuckDB is not installed. Run: pip install duckdb")
    sys.exit(1)


DB_PATH = os.path.join(os.path.dirname(__file__), "ecommerce.duckdb")
SEED = 20260723
CUSTOMER_COUNT = 5_000
PRODUCT_COUNT = 250
ORDER_COUNT = 25_000

CATEGORY_NAMES = [
    "Electronics",
    "Clothing",
    "Books",
    "Home",
    "Outdoors",
    "Fitness",
    "Office",
    "Beauty",
    "Food",
    "Toys",
]
WAREHOUSES = ["east", "central", "west"]
CITIES = [
    ("New York", "US"),
    ("Los Angeles", "US"),
    ("Chicago", "US"),
    ("Houston", "US"),
    ("Phoenix", "US"),
    ("Philadelphia", "US"),
    ("San Antonio", "US"),
    ("San Diego", "US"),
    ("Dallas", "US"),
    ("San Jose", "US"),
    ("Austin", "US"),
    ("Seattle", "US"),
    ("Denver", "US"),
    ("Boston", "US"),
    ("Atlanta", "US"),
    ("Toronto", "CA"),
    ("Vancouver", "CA"),
    ("London", "GB"),
]
STATUSES = ["delivered", "shipped", "pending", "cancelled"]
STATUS_WEIGHTS = [0.72, 0.10, 0.10, 0.08]
CHANNELS = ["email", "social", "search", "affiliate"]


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def insert_rows(connection, table: str, rows: list[tuple]) -> None:
    """Bulk-load rows with DuckDB COPY so the large seed stays quick."""
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
        connection.execute(
            f"COPY {table} FROM '{escaped_path}' (FORMAT CSV, NULL '{null_marker}')"
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def create_schema(connection) -> None:
    # dbt relations may still exist from a prior build and depend on sources.
    for statement in [
        "DROP VIEW IF EXISTS stg_orders",
        "DROP VIEW IF EXISTS stg_customers",
        "DROP TABLE IF EXISTS fct_orders",
    ]:
        connection.execute(statement)

    for table in [
        "campaign_conversions",
        "order_items",
        "orders",
        "inventory",
        "products",
        "categories",
        "campaigns",
        "customers",
    ]:
        connection.execute(f"DROP TABLE IF EXISTS {table}")

    connection.execute(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            email VARCHAR UNIQUE NOT NULL,
            phone VARCHAR,
            city VARCHAR,
            country VARCHAR DEFAULT 'US',
            signup_date DATE NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            lifetime_value DECIMAL(12, 2) DEFAULT 0.00
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            slug VARCHAR UNIQUE NOT NULL,
            description VARCHAR
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku VARCHAR UNIQUE NOT NULL,
            name VARCHAR NOT NULL,
            description VARCHAR,
            price DECIMAL(12, 2) NOT NULL,
            cost DECIMAL(12, 2),
            category_id INTEGER REFERENCES categories(id),
            active BOOLEAN DEFAULT TRUE,
            created_at DATE NOT NULL
        );
        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id),
            warehouse VARCHAR NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            reorder_point INTEGER NOT NULL DEFAULT 10,
            updated_at TIMESTAMP NOT NULL
        );
        CREATE TABLE orders (
            id BIGINT PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            status VARCHAR DEFAULT 'pending',
            total_amount DECIMAL(12, 2) NOT NULL,
            discount_amount DECIMAL(12, 2) DEFAULT 0.00,
            shipping_address VARCHAR,
            created_at TIMESTAMP NOT NULL,
            shipped_at TIMESTAMP,
            delivered_at TIMESTAMP
        );
        CREATE TABLE order_items (
            id BIGINT PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL,
            unit_price DECIMAL(12, 2) NOT NULL,
            discount DECIMAL(12, 2) DEFAULT 0.00
        );
        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            channel VARCHAR,
            budget DECIMAL(12, 2),
            spend DECIMAL(12, 2) DEFAULT 0.00,
            start_date DATE,
            end_date DATE,
            active BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE campaign_conversions (
            id BIGINT PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            converted_at TIMESTAMP NOT NULL,
            revenue DECIMAL(12, 2)
        );
        """
    )


def seed(db_path: str = DB_PATH) -> None:
    rng = random.Random(SEED)
    connection = duckdb.connect(db_path)
    print(f"Seeding {db_path} ...")
    create_schema(connection)
    connection.execute("BEGIN TRANSACTION")

    categories = [
        (
            category_id,
            name,
            name.lower().replace(" ", "-"),
            f"Curated {name.lower()} assortment for the ecommerce catalogue",
        )
        for category_id, name in enumerate(CATEGORY_NAMES, 1)
    ]
    insert_rows(connection, "categories", categories)

    products = []
    created_start = date(2022, 1, 1)
    for product_id in range(1, PRODUCT_COUNT + 1):
        category_id = ((product_id - 1) % len(CATEGORY_NAMES)) + 1
        category = CATEGORY_NAMES[category_id - 1]
        price = money(rng.uniform(8, 750))
        cost = money(price * Decimal(str(rng.uniform(0.28, 0.72))))
        products.append(
            (
                product_id,
                f"{category[:4].upper()}-{product_id:04d}",
                f"{category} Product {product_id:03d}",
                f"Representative {category.lower()} item {product_id:03d}",
                price,
                cost,
                category_id,
                product_id % 29 != 0,
                created_start + timedelta(days=rng.randrange(1_000)),
            )
        )
    insert_rows(connection, "products", products)

    customers = []
    signup_start = date(2022, 1, 1)
    for customer_id in range(1, CUSTOMER_COUNT + 1):
        city, country = rng.choice(CITIES)
        customers.append(
            (
                customer_id,
                f"Customer {customer_id:05d}",
                f"customer{customer_id:05d}@example.test",
                f"555-{customer_id % 10_000:04d}",
                city,
                country,
                signup_start + timedelta(days=rng.randrange(1_825)),
                customer_id % 37 != 0,
                money(0),
            )
        )
    insert_rows(connection, "customers", customers)

    inventory = []
    inventory_id = 1
    updated_at = datetime(2026, 12, 31, 23, 59, 59)
    for product_id in range(1, PRODUCT_COUNT + 1):
        for warehouse_index, warehouse in enumerate(WAREHOUSES):
            reorder_point = rng.randint(8, 35)
            quantity = rng.randint(18, 180)

            # Stable edge cases make the primary inventory question useful:
            # zero stock, below threshold, and exactly on the threshold.
            if product_id % 41 == 0 and warehouse_index == 0:
                quantity = 0
            elif product_id % 17 == 0 and warehouse_index == 1:
                quantity = reorder_point
            elif product_id % 13 == 0 and warehouse_index == 2:
                quantity = max(0, reorder_point - rng.randint(1, 5))

            inventory.append(
                (
                    inventory_id,
                    product_id,
                    warehouse,
                    quantity,
                    reorder_point,
                    updated_at,
                )
            )
            inventory_id += 1
    insert_rows(connection, "inventory", inventory)

    price_by_product = {row[0]: row[4] for row in products}
    city_by_customer = {row[0]: row[4] for row in customers}
    orders = []
    order_items = []
    item_id = 1
    order_start = datetime(2024, 1, 1)
    order_span_seconds = 3 * 365 * 24 * 60 * 60

    # Keep the final 250 customers order-free for anti-join questions.
    for order_id in range(1, ORDER_COUNT + 1):
        customer_id = rng.randint(1, CUSTOMER_COUNT - 250)
        status = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        created_at = order_start + timedelta(seconds=rng.randrange(order_span_seconds))
        line_count = rng.randint(1, 5)
        subtotal = Decimal("0")

        for _ in range(line_count):
            product_id = rng.randint(1, PRODUCT_COUNT)
            quantity = rng.randint(1, 4)
            unit_price = price_by_product[product_id]
            line_discount = money(
                unit_price
                * quantity
                * Decimal(str(rng.choice([0, 0, 0, 0.05, 0.10])))
            )
            subtotal += unit_price * quantity - line_discount
            order_items.append(
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

        order_discount = (
            money(subtotal * Decimal("0.05"))
            if order_id % 17 == 0
            else money(0)
        )
        shipped_at = (
            created_at + timedelta(days=rng.randint(1, 3))
            if status in {"shipped", "delivered"}
            else None
        )
        delivered_at = (
            shipped_at + timedelta(days=rng.randint(1, 5))
            if status == "delivered" and shipped_at
            else None
        )
        city = city_by_customer[customer_id]
        orders.append(
            (
                order_id,
                customer_id,
                status,
                money(subtotal),
                order_discount,
                f"{100 + customer_id % 9_000} Commerce St, {city}",
                created_at,
                shipped_at,
                delivered_at,
            )
        )

    insert_rows(connection, "orders", orders)
    insert_rows(connection, "order_items", order_items)

    campaigns = []
    campaign_start = date(2024, 1, 1)
    for campaign_id in range(1, 25):
        start_date = campaign_start + timedelta(days=(campaign_id - 1) * 45)
        end_date = start_date + timedelta(days=44)
        budget = money(rng.uniform(5_000, 60_000))
        spend = money(budget * Decimal(str(rng.uniform(0.72, 1.0))))
        campaigns.append(
            (
                campaign_id,
                f"{CHANNELS[(campaign_id - 1) % len(CHANNELS)].title()} "
                f"Campaign {campaign_id:02d}",
                CHANNELS[(campaign_id - 1) % len(CHANNELS)],
                budget,
                spend,
                start_date,
                end_date,
                end_date >= date(2026, 12, 1),
            )
        )
    insert_rows(connection, "campaigns", campaigns)

    conversions = []
    conversion_id = 1
    eligible_orders = [row for row in orders if row[2] == "delivered"]
    for order in eligible_orders:
        if order[0] % 4 != 0:
            continue
        campaign_id = ((order[0] - 1) % len(campaigns)) + 1
        conversions.append(
            (
                conversion_id,
                campaign_id,
                order[1],
                order[6] + timedelta(hours=rng.randint(1, 72)),
                money(order[3] - order[4]),
            )
        )
        conversion_id += 1
    insert_rows(connection, "campaign_conversions", conversions)

    connection.execute(
        """
        UPDATE customers AS c
        SET lifetime_value = totals.value
        FROM (
            SELECT customer_id,
                   SUM(total_amount - discount_amount) AS value
            FROM orders
            WHERE status <> 'cancelled'
            GROUP BY customer_id
        ) AS totals
        WHERE c.id = totals.customer_id
        """
    )

    connection.execute("COMMIT")
    connection.execute("ANALYZE")

    table_names = [
        "customers",
        "categories",
        "products",
        "inventory",
        "orders",
        "order_items",
        "campaigns",
        "campaign_conversions",
    ]
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in table_names
    }
    low_stock = connection.execute(
        """
        SELECT COUNT(*)
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        WHERE p.active = TRUE AND i.quantity <= i.reorder_point
        """
    ).fetchone()[0]
    connection.close()

    print("\n  Table                   Rows")
    print("  " + "-" * 36)
    for table in table_names:
        print(f"  {table:<25} {counts[table]:>9,}")
    print(f"\n  Active low-stock rows: {low_stock:,}")
    print(f"\n✓ Database seeded at {db_path}")
    print("\nNext steps:")
    print("  cd dbt_project && uv run --with dbt-duckdb dbt build --profiles-dir .")
    print("  cd .. && uv run tabletalk apply .")
    print("  uv run tabletalk serve --port 5000")


if __name__ == "__main__":
    seed()

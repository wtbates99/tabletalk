#!/usr/bin/env python3
"""
seed.py — Create and populate the ecommerce DuckDB database.

Usage:
    python seed.py

Creates ./ecommerce.duckdb with a realistic ecommerce schema:
  - customers           (5 records)
  - categories          (3 records)
  - products            (10 records)
  - inventory           (10 records)
  - orders              (12 records)
  - order_items         (20 records)
  - campaigns           (4 records)
  - campaign_conversions (8 records)

Re-running this script drops and recreates all tables (idempotent).
"""
from __future__ import annotations

import os
import sys

try:
    import duckdb
except ImportError:
    print("DuckDB is not installed. Run: pip install duckdb")
    sys.exit(1)


DB_PATH = os.path.join(os.path.dirname(__file__), "ecommerce.duckdb")


def seed(db_path: str = DB_PATH) -> None:
    conn = duckdb.connect(db_path)
    print(f"Seeding {db_path} ...")

    # ── Drop existing tables (in FK-safe order) ───────────────────────────────
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
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    # ── Schema ────────────────────────────────────────────────────────────────

    conn.execute(
        """
        CREATE TABLE customers (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR NOT NULL,
            email       VARCHAR UNIQUE NOT NULL,
            phone       VARCHAR,
            city        VARCHAR,
            country     VARCHAR DEFAULT 'US',
            signup_date DATE NOT NULL,
            active      BOOLEAN DEFAULT TRUE,
            lifetime_value DECIMAL(10,2) DEFAULT 0.00
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE categories (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR NOT NULL,
            slug        VARCHAR UNIQUE NOT NULL,
            description VARCHAR
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE products (
            id          INTEGER PRIMARY KEY,
            sku         VARCHAR UNIQUE NOT NULL,
            name        VARCHAR NOT NULL,
            description VARCHAR,
            price       DECIMAL(10,2) NOT NULL,
            cost        DECIMAL(10,2),
            category_id INTEGER REFERENCES categories(id),
            active      BOOLEAN DEFAULT TRUE,
            created_at  DATE NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE inventory (
            id            INTEGER PRIMARY KEY,
            product_id    INTEGER NOT NULL REFERENCES products(id),
            warehouse     VARCHAR DEFAULT 'main',
            quantity      INTEGER NOT NULL DEFAULT 0,
            reorder_point INTEGER DEFAULT 10,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE orders (
            id               INTEGER PRIMARY KEY,
            customer_id      INTEGER NOT NULL REFERENCES customers(id),
            status           VARCHAR DEFAULT 'pending',
            total_amount     DECIMAL(10,2) NOT NULL,
            discount_amount  DECIMAL(10,2) DEFAULT 0.00,
            shipping_address VARCHAR,
            created_at       TIMESTAMP NOT NULL,
            shipped_at       TIMESTAMP,
            delivered_at     TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY,
            order_id    INTEGER NOT NULL REFERENCES orders(id),
            product_id  INTEGER NOT NULL REFERENCES products(id),
            quantity    INTEGER NOT NULL,
            unit_price  DECIMAL(10,2) NOT NULL,
            discount    DECIMAL(10,2) DEFAULT 0.00
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE campaigns (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR NOT NULL,
            channel     VARCHAR,
            budget      DECIMAL(10,2),
            spend       DECIMAL(10,2) DEFAULT 0.00,
            start_date  DATE,
            end_date    DATE,
            active      BOOLEAN DEFAULT TRUE
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE campaign_conversions (
            id           INTEGER PRIMARY KEY,
            campaign_id  INTEGER NOT NULL REFERENCES campaigns(id),
            customer_id  INTEGER NOT NULL REFERENCES customers(id),
            converted_at TIMESTAMP NOT NULL,
            revenue      DECIMAL(10,2)
        )
        """
    )

    # ── Seed data ─────────────────────────────────────────────────────────────

    conn.execute(
        """
        INSERT INTO customers VALUES
            (1,  'Alice Johnson',    'alice@example.com',    '555-0101', 'New York',      'US', '2023-06-15', TRUE,  499.96),
            (2,  'Bob Smith',        'bob@example.com',      '555-0102', 'Los Angeles',   'US', '2023-07-01', TRUE,  149.99),
            (3,  'Carol Williams',   'carol@example.com',    '555-0103', 'Chicago',       'US', '2023-08-20', TRUE,  189.97),
            (4,  'David Brown',      'david@example.com',    '555-0104', 'Houston',       'US', '2023-09-10', TRUE,  299.98),
            (5,  'Eve Davis',        'eve@example.com',      '555-0105', 'Phoenix',       'US', '2023-10-05', FALSE,   0.00),
            (6,  'Frank Miller',     'frank@example.com',    '555-0106', 'Philadelphia',  'US', '2023-11-12', TRUE,  89.99),
            (7,  'Grace Wilson',     'grace@example.com',    '555-0107', 'San Antonio',   'US', '2023-12-01', TRUE,  219.97),
            (8,  'Henry Taylor',     'henry@example.com',    '555-0108', 'San Diego',     'US', '2024-01-03', TRUE,  134.98),
            (9,  'Iris Anderson',    'iris@example.com',     '555-0109', 'Dallas',        'US', '2024-01-20', TRUE,  449.97),
            (10, 'Jack Thomas',      'jack@example.com',     '555-0110', 'San Jose',      'US', '2024-02-14', TRUE,  74.99)
        """
    )

    conn.execute(
        """
        INSERT INTO categories VALUES
            (1, 'Electronics', 'electronics', 'Gadgets, headphones, and accessories'),
            (2, 'Clothing',    'clothing',    'Apparel, shoes, and fashion accessories'),
            (3, 'Books',       'books',       'Technical books and educational resources'),
            (4, 'Home',        'home',        'Home goods and kitchen accessories')
        """
    )

    conn.execute(
        """
        INSERT INTO products VALUES
            (1,  'ELEC-001', 'Wireless Headphones',    'Noise-cancelling over-ear headphones', 149.99, 75.00, 1, TRUE,  '2023-01-10'),
            (2,  'ELEC-002', 'USB-C Hub',              '7-in-1 USB-C hub for laptops',          49.99, 20.00, 1, TRUE,  '2023-01-15'),
            (3,  'ELEC-003', 'Mechanical Keyboard',    'TKL mechanical keyboard, RGB backlit', 129.99, 60.00, 1, TRUE,  '2023-02-01'),
            (4,  'CLTH-001', 'Organic Cotton Tee',     '100%% organic cotton, unisex',          29.99, 10.00, 2, TRUE,  '2023-03-01'),
            (5,  'CLTH-002', 'Running Shorts',         'Lightweight moisture-wicking shorts',   39.99, 15.00, 2, TRUE,  '2023-03-15'),
            (6,  'CLTH-003', 'Merino Wool Sweater',    'Premium merino wool, machine washable', 89.99, 40.00, 2, TRUE,  '2023-04-01'),
            (7,  'BOOK-001', 'Clean Code',             'A handbook of agile software craftsmanship', 34.99, 12.00, 3, TRUE, '2023-05-01'),
            (8,  'BOOK-002', 'Designing Data-Intensive Applications', 'A deep dive into data systems', 44.99, 15.00, 3, TRUE, '2023-05-15'),
            (9,  'HOME-001', 'Pour-Over Coffee Set',   'Manual pour-over coffee kit with filters', 59.99, 22.00, 4, TRUE, '2023-06-01'),
            (10, 'HOME-002', 'Bamboo Cutting Board',   'Large bamboo cutting board with juice groove', 34.99, 12.00, 4, FALSE, '2023-06-15')
        """
    )

    conn.execute(
        """
        INSERT INTO inventory VALUES
            (1,  1, 'main',  45, 10, '2024-01-15'),
            (2,  2, 'main',   8,  5, '2024-01-15'),
            (3,  3, 'main',  22, 10, '2024-01-15'),
            (4,  4, 'main', 120, 20, '2024-01-15'),
            (5,  5, 'main',  15, 10, '2024-01-15'),
            (6,  6, 'main',  30, 10, '2024-01-15'),
            (7,  7, 'main',  62, 10, '2024-01-15'),
            (8,  8, 'main',  41, 10, '2024-01-15'),
            (9,  9, 'main',  18,  8, '2024-01-15'),
            (10, 10, 'main',  0,  5, '2024-01-15')
        """
    )

    conn.execute(
        """
        INSERT INTO orders VALUES
            (1,  1,  'delivered', 199.98,  0.00, '100 Main St, New York, NY',    '2024-01-05 09:00', '2024-01-07', '2024-01-09'),
            (2,  2,  'delivered',  49.99,  0.00, '200 Oak Ave, Los Angeles, CA', '2024-01-08 11:30', '2024-01-10', '2024-01-12'),
            (3,  3,  'delivered',  74.98,  0.00, '300 Pine Rd, Chicago, IL',     '2024-01-10 14:00', '2024-01-12', '2024-01-14'),
            (4,  4,  'delivered', 149.99,  0.00, '400 Elm St, Houston, TX',      '2024-01-12 16:45', '2024-01-14', '2024-01-16'),
            (5,  1,  'delivered',  34.99,  0.00, '100 Main St, New York, NY',    '2024-01-15 08:00', '2024-01-17', '2024-01-19'),
            (6,  6,  'delivered',  89.99,  0.00, '600 Maple Dr, Philadelphia',   '2024-01-18 10:00', '2024-01-20', '2024-01-22'),
            (7,  7,  'shipped',   219.97,  0.00, '700 Oak Ln, San Antonio, TX',  '2024-01-22 09:00', '2024-01-24', NULL),
            (8,  8,  'shipped',   134.98,  5.00, '800 Pine St, San Diego, CA',   '2024-01-25 13:00', '2024-01-27', NULL),
            (9,  9,  'pending',   449.97,  0.00, '900 Elm Ave, Dallas, TX',      '2024-01-28 15:00', NULL, NULL),
            (10, 10, 'pending',    74.99,  0.00, '1000 Main Blvd, San Jose, CA', '2024-01-30 11:00', NULL, NULL),
            (11, 1,  'delivered', 129.99, 13.00, '100 Main St, New York, NY',    '2024-02-01 09:00', '2024-02-03', '2024-02-05'),
            (12, 3,  'cancelled', 114.99, 0.00,  '300 Pine Rd, Chicago, IL',     '2024-02-05 14:00', NULL, NULL)
        """
    )

    conn.execute(
        """
        INSERT INTO order_items VALUES
            (1,  1,  1, 1, 149.99, 0.00),
            (2,  1,  4, 1,  29.99, 0.00),
            (3,  1,  5, 1,  39.99, 0.00),
            (4,  2,  2, 1,  49.99, 0.00),
            (5,  3,  4, 1,  29.99, 0.00),
            (6,  3,  5, 1,  39.99, 0.00),
            (7,  3,  7, 0,  34.99, 0.00),
            (8,  4,  1, 1, 149.99, 0.00),
            (9,  5,  7, 1,  34.99, 0.00),
            (10, 6,  6, 1,  89.99, 0.00),
            (11, 7,  1, 1, 149.99, 0.00),
            (12, 7,  3, 1, 129.99, 0.00),
            (13, 7,  4, 1,  29.99, 0.00),
            (14, 7,  5, 1,  39.99, 0.00),
            (15, 8,  6, 1,  89.99, 5.00),
            (16, 8,  8, 1,  44.99, 0.00),
            (17, 9,  1, 1, 149.99, 0.00),
            (18, 9,  3, 1, 129.99, 0.00),
            (19, 9,  8, 1,  44.99, 0.00),
            (20, 9,  9, 1,  59.99, 0.00)
        """
    )

    conn.execute(
        """
        INSERT INTO campaigns VALUES
            (1, 'New Year Sale',        'email',   5000.00, 4800.00, '2024-01-01', '2024-01-15', FALSE),
            (2, 'Winter Clearance',     'social', 10000.00, 9200.00, '2024-01-10', '2024-01-31', FALSE),
            (3, 'Loyalty Rewards Feb',  'email',   3000.00, 1200.00, '2024-02-01', '2024-02-28', FALSE),
            (4, 'Spring Preview',       'social',  8000.00,  500.00, '2024-03-01', '2024-03-31', TRUE)
        """
    )

    conn.execute(
        """
        INSERT INTO campaign_conversions VALUES
            (1, 1, 1,  '2024-01-06 10:00', 199.98),
            (2, 1, 3,  '2024-01-11 14:30',  74.98),
            (3, 1, 6,  '2024-01-19 09:15',  89.99),
            (4, 2, 2,  '2024-01-09 11:00',  49.99),
            (5, 2, 4,  '2024-01-13 16:00', 149.99),
            (6, 2, 7,  '2024-01-22 09:30', 219.97),
            (7, 3, 1,  '2024-02-02 08:00', 129.99),
            (8, 3, 8,  '2024-01-26 13:30', 134.98)
        """
    )

    conn.close()

    # Count rows in each table
    conn = duckdb.connect(db_path)
    tables = [
        "customers", "categories", "products", "inventory",
        "orders", "order_items", "campaigns", "campaign_conversions",
    ]
    print("\n  Table                   Rows")
    print("  " + "-" * 30)
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<25} {n:>4}")
    conn.close()

    print(f"\n✓ Database seeded at {db_path}")
    print("\nNext steps:")
    print("  tabletalk apply    — compile context definitions into manifests")
    print("  tabletalk query    — start an interactive agent session")
    print("  tabletalk serve    — launch the web UI at http://localhost:5000")


if __name__ == "__main__":
    seed()

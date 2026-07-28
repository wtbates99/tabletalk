"""
Shared pytest fixtures for tabletalk tests.

Provides:
  - mock_llm          — a MockLLMProvider that returns canned SQL
  - ecommerce_sqlite  — SQLite database seeded with a full ecommerce schema
  - ecommerce_duckdb  — DuckDB database seeded with the same schema
  - project_dir       — a fully-initialised temp project directory
  - project_with_manifest — project_dir with manifests already applied
"""
from __future__ import annotations

import json
import os
import sqlite3
import textwrap
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from unittest.mock import patch

import pytest
import yaml

from tabletalk.interfaces import LLMProvider


# ── Mock LLM provider ─────────────────────────────────────────────────────────

class MockLLMProvider(LLMProvider):
    """
    Deterministic LLM stub for testing.

    Behaviour:
      - ``generate_response`` returns ``default_response`` unless the prompt
        contains a key in ``responses``; in that case the mapped value is
        returned.
      - ``generate_chat_stream`` / ``generate_response_stream`` yield the
        response one word at a time so streaming code paths are exercised.
      - The full list of received prompts is stored in ``calls`` for assertion.
    """

    def __init__(
        self,
        default_response: str = "SELECT 1",
        responses: Optional[Dict[str, str]] = None,
    ):
        self.default_response = default_response
        self.responses = responses or {}
        self.calls: List[str] = []

    def _match(self, text: str) -> str:
        for key, value in self.responses.items():
            if key in text:
                return value
        return self.default_response

    def generate_response(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._match(prompt)

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        response = self.generate_response(prompt)
        for word in response.split():
            yield word + " "

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        text = " ".join(m["content"] for m in messages)
        response = self._match(text)
        self.calls.append(text)
        for word in response.split():
            yield word + " "


@pytest.fixture
def mock_llm() -> MockLLMProvider:
    """Return a MockLLMProvider with sensible defaults for ecommerce queries."""
    return MockLLMProvider(
        default_response="SELECT * FROM main.customers LIMIT 10",
        responses={
            "revenue": "SELECT SUM(total_amount) AS total_revenue FROM main.orders",
            "top customers": (
                "SELECT c.name, SUM(o.total_amount) AS revenue "
                "FROM main.customers c "
                "JOIN main.orders o ON c.id = o.customer_id "
                "GROUP BY c.name ORDER BY revenue DESC LIMIT 5"
            ),
            "suggest": '["How many customers signed up last month?", '
                       '"What is the average order value?", '
                       '"Which products are low in stock?"]',
            "fix": "SELECT id, name FROM main.customers WHERE active = 1",
        },
    )


# ── Ecommerce schema helper ───────────────────────────────────────────────────

def _create_ecommerce_schema_sqlite(conn: sqlite3.Connection) -> None:
    """Create a realistic ecommerce schema in a SQLite connection."""
    conn.executescript(
        """
        CREATE TABLE customers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            email       TEXT    UNIQUE NOT NULL,
            phone       TEXT,
            city        TEXT,
            country     TEXT    DEFAULT 'US',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active      INTEGER DEFAULT 1
        );

        CREATE TABLE categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            description TEXT
        );

        CREATE TABLE products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sku         TEXT    UNIQUE NOT NULL,
            name        TEXT    NOT NULL,
            description TEXT,
            price       DECIMAL NOT NULL,
            category_id INTEGER,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        CREATE TABLE inventory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL,
            warehouse   TEXT    DEFAULT 'main',
            quantity    INTEGER NOT NULL DEFAULT 0,
            reorder_point INTEGER DEFAULT 10,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id     INTEGER NOT NULL,
            status          TEXT    DEFAULT 'pending',
            total_amount    DECIMAL NOT NULL,
            shipping_address TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            shipped_at      TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        CREATE TABLE order_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id    INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            quantity    INTEGER NOT NULL,
            unit_price  DECIMAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE campaigns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            channel     TEXT,
            budget      DECIMAL,
            start_date  DATE,
            end_date    DATE
        );

        CREATE TABLE campaign_conversions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            converted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revenue     DECIMAL,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );

        -- Seed data
        INSERT INTO customers (name, email, phone, city, country) VALUES
            ('Alice Johnson',  'alice@example.com',  '555-0101', 'New York',    'US'),
            ('Bob Smith',      'bob@example.com',    '555-0102', 'Los Angeles', 'US'),
            ('Carol Williams', 'carol@example.com',  '555-0103', 'Chicago',     'US'),
            ('David Brown',    'david@example.com',  '555-0104', 'Houston',     'US'),
            ('Eve Davis',      'eve@example.com',    '555-0105', 'Phoenix',     'US');

        INSERT INTO categories (name, description) VALUES
            ('Electronics', 'Electronic gadgets and devices'),
            ('Clothing',    'Apparel and accessories'),
            ('Books',       'Physical and digital books');

        INSERT INTO products (sku, name, description, price, category_id) VALUES
            ('ELEC-001', 'Wireless Headphones', 'Noise-cancelling over-ear headphones', 149.99, 1),
            ('ELEC-002', 'USB-C Hub',           '7-in-1 USB-C hub',                     49.99,  1),
            ('CLTH-001', 'Organic Cotton Tee',  '100% organic cotton t-shirt',          29.99,  2),
            ('CLTH-002', 'Running Shorts',      'Lightweight running shorts',            39.99,  2),
            ('BOOK-001', 'Clean Code',          'A handbook of agile software craftsmanship', 34.99, 3);

        INSERT INTO inventory (product_id, warehouse, quantity, reorder_point) VALUES
            (1, 'main',  45, 10),
            (2, 'main',   8,  5),
            (3, 'main', 120, 20),
            (4, 'main',  15, 10),
            (5, 'main',  62, 10);

        INSERT INTO orders (customer_id, status, total_amount, shipping_address, created_at) VALUES
            (1, 'delivered', 199.98, '100 Main St, New York, NY',     '2024-01-10 09:00:00'),
            (2, 'delivered',  49.99, '200 Oak Ave, Los Angeles, CA',  '2024-01-12 11:30:00'),
            (3, 'shipped',    64.98, '300 Pine Rd, Chicago, IL',      '2024-01-15 14:00:00'),
            (4, 'pending',   149.99, '400 Elm St, Houston, TX',       '2024-01-18 16:45:00'),
            (1, 'delivered',  34.99, '100 Main St, New York, NY',     '2024-01-20 08:00:00');

        INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
            (1, 1, 1, 149.99),
            (1, 3, 1,  29.99),
            (1, 4, 1,  29.99),
            (2, 2, 1,  49.99),
            (3, 3, 1,  29.99),
            (3, 4, 1,  39.99),
            (4, 1, 1, 149.99),
            (5, 5, 1,  34.99);

        INSERT INTO campaigns (name, channel, budget, start_date, end_date) VALUES
            ('Winter Sale 2024',   'email',   5000.00, '2024-01-01', '2024-01-31'),
            ('Social Media Blitz', 'social', 10000.00, '2024-01-15', '2024-02-15');

        INSERT INTO campaign_conversions (campaign_id, customer_id, revenue) VALUES
            (1, 1, 199.98),
            (1, 3,  64.98),
            (2, 2,  49.99),
            (2, 4, 149.99);
        """
    )
    conn.commit()


# ── SQLite fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def ecommerce_sqlite(tmp_path: Path) -> str:
    """
    Create a temporary SQLite database with a full ecommerce schema and seed data.
    Returns the path to the .db file.
    """
    db_path = tmp_path / "ecommerce.db"
    conn = sqlite3.connect(db_path)
    _create_ecommerce_schema_sqlite(conn)
    conn.close()
    return str(db_path)


# ── DuckDB fixture ─────────────────────────────────────────────────────────────

@pytest.fixture
def ecommerce_duckdb(tmp_path: Path):
    """
    Create a temporary DuckDB database with the same ecommerce schema.
    Returns the DuckDBProvider instance.

    Skipped automatically if duckdb is not installed.
    """
    pytest.importorskip("duckdb")
    import duckdb

    from tabletalk.providers.duckdb_provider import DuckDBProvider

    db_path = str(tmp_path / "ecommerce.duckdb")
    conn = duckdb.connect(db_path)

    conn.execute(
        """
        CREATE TABLE customers (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR NOT NULL,
            email       VARCHAR UNIQUE NOT NULL,
            phone       VARCHAR,
            city        VARCHAR,
            country     VARCHAR DEFAULT 'US',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active      BOOLEAN DEFAULT TRUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE categories (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR NOT NULL,
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
            category_id INTEGER REFERENCES categories(id)
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
            shipping_address VARCHAR,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            shipped_at       TIMESTAMP
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
            unit_price  DECIMAL(10,2) NOT NULL
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
            start_date  DATE,
            end_date    DATE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE campaign_conversions (
            id           INTEGER PRIMARY KEY,
            campaign_id  INTEGER NOT NULL REFERENCES campaigns(id),
            customer_id  INTEGER NOT NULL REFERENCES customers(id),
            converted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revenue      DECIMAL(10,2)
        )
        """
    )

    # Seed data
    conn.execute(
        """
        INSERT INTO customers VALUES
            (1, 'Alice Johnson',  'alice@example.com',  '555-0101', 'New York',    'US', '2024-01-01', TRUE),
            (2, 'Bob Smith',      'bob@example.com',    '555-0102', 'Los Angeles', 'US', '2024-01-02', TRUE),
            (3, 'Carol Williams', 'carol@example.com',  '555-0103', 'Chicago',     'US', '2024-01-03', TRUE),
            (4, 'David Brown',    'david@example.com',  '555-0104', 'Houston',     'US', '2024-01-04', TRUE),
            (5, 'Eve Davis',      'eve@example.com',    '555-0105', 'Phoenix',     'US', '2024-01-05', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO categories VALUES
            (1, 'Electronics', 'Electronic gadgets and devices'),
            (2, 'Clothing',    'Apparel and accessories'),
            (3, 'Books',       'Physical and digital books')
        """
    )
    conn.execute(
        """
        INSERT INTO products VALUES
            (1, 'ELEC-001', 'Wireless Headphones', 'Noise-cancelling over-ear headphones', 149.99, 1),
            (2, 'ELEC-002', 'USB-C Hub',           '7-in-1 USB-C hub',                      49.99, 1),
            (3, 'CLTH-001', 'Organic Cotton Tee',  '100%% organic cotton t-shirt',           29.99, 2),
            (4, 'CLTH-002', 'Running Shorts',      'Lightweight running shorts',             39.99, 2),
            (5, 'BOOK-001', 'Clean Code',          'A handbook of agile software',           34.99, 3)
        """
    )
    conn.execute(
        """
        INSERT INTO inventory VALUES
            (1, 1, 'main',  45, 10, '2024-01-10'),
            (2, 2, 'main',   8,  5, '2024-01-10'),
            (3, 3, 'main', 120, 20, '2024-01-10'),
            (4, 4, 'main',  15, 10, '2024-01-10'),
            (5, 5, 'main',  62, 10, '2024-01-10')
        """
    )
    conn.execute(
        """
        INSERT INTO orders VALUES
            (1, 1, 'delivered', 199.98, '100 Main St, New York, NY',    '2024-01-10', '2024-01-12'),
            (2, 2, 'delivered',  49.99, '200 Oak Ave, Los Angeles, CA', '2024-01-12', '2024-01-14'),
            (3, 3, 'shipped',    64.98, '300 Pine Rd, Chicago, IL',     '2024-01-15', NULL),
            (4, 4, 'pending',   149.99, '400 Elm St, Houston, TX',      '2024-01-18', NULL),
            (5, 1, 'delivered',  34.99, '100 Main St, New York, NY',    '2024-01-20', '2024-01-21')
        """
    )
    conn.execute(
        """
        INSERT INTO order_items VALUES
            (1, 1, 1, 1, 149.99),
            (2, 1, 3, 1,  29.99),
            (3, 1, 4, 1,  29.99),
            (4, 2, 2, 1,  49.99),
            (5, 3, 3, 1,  29.99),
            (6, 3, 4, 1,  39.99),
            (7, 4, 1, 1, 149.99),
            (8, 5, 5, 1,  34.99)
        """
    )
    conn.execute(
        """
        INSERT INTO campaigns VALUES
            (1, 'Winter Sale 2024',   'email',   5000.00, '2024-01-01', '2024-01-31'),
            (2, 'Social Media Blitz', 'social', 10000.00, '2024-01-15', '2024-02-15')
        """
    )
    conn.execute(
        """
        INSERT INTO campaign_conversions VALUES
            (1, 1, 1, '2024-01-11', 199.98),
            (2, 1, 3, '2024-01-16',  64.98),
            (3, 2, 2, '2024-01-17',  49.99),
            (4, 2, 4, '2024-01-19', 149.99)
        """
    )
    conn.close()

    return DuckDBProvider(database_path=db_path)


# ── Project directory fixture ─────────────────────────────────────────────────

@pytest.fixture
def project_dir(tmp_path: Path, ecommerce_sqlite: str) -> str:
    """
    Create a fully-initialised tabletalk project directory:
      - tabletalk.yaml    (uses the ecommerce SQLite db)
      - contexts/         (customers, orders, inventory, marketing)
      - manifest/         (empty — run apply to populate)

    Returns the project folder path as a string.
    """
    config = {
        "provider": {
            "type": "sqlite",
            "database_path": ecommerce_sqlite,
        },
        "llm": {
            "provider": "openai",
            "api_key": "test-key",
            "model": "gpt-4o",
            "max_tokens": 500,
            "temperature": 0,
        },
        "description": "Ecommerce test database",
        "contexts": "contexts",
        "output": "manifest",
    }
    (tmp_path / "tabletalk.yaml").write_text(yaml.dump(config))

    contexts_dir = tmp_path / "contexts"
    contexts_dir.mkdir()

    (contexts_dir / "customers.yaml").write_text(
        textwrap.dedent(
            """\
            name: customers
            description: "Customer profiles and account data"
            version: "1.0"
            datasets:
              - name: main
                description: "Main schema"
                tables:
                  - name: customers
                    description: "Registered customer accounts"
            """
        )
    )

    (contexts_dir / "orders.yaml").write_text(
        textwrap.dedent(
            """\
            name: orders
            description: "Order processing and fulfilment"
            version: "1.0"
            datasets:
              - name: main
                description: "Main schema"
                tables:
                  - name: orders
                    description: "Customer orders"
                  - name: order_items
                    description: "Line items within each order"
                  - name: customers
                    description: "Customer references"
                  - name: products
                    description: "Product catalogue"
            """
        )
    )

    (contexts_dir / "inventory.yaml").write_text(
        textwrap.dedent(
            """\
            name: inventory
            description: "Stock levels and warehouse management"
            version: "1.0"
            datasets:
              - name: main
                description: "Main schema"
                tables:
                  - name: inventory
                    description: "Current stock quantities by warehouse"
                  - name: products
                    description: "Product details"
                  - name: categories
                    description: "Product categories"
            """
        )
    )

    (contexts_dir / "marketing.yaml").write_text(
        textwrap.dedent(
            """\
            name: marketing
            description: "Campaign performance and customer acquisition"
            version: "1.0"
            datasets:
              - name: main
                description: "Main schema"
                tables:
                  - name: campaigns
                    description: "Marketing campaigns"
                  - name: campaign_conversions
                    description: "Campaign attribution and revenue"
                  - name: customers
                    description: "Customer data for attribution"
            """
        )
    )

    (tmp_path / "manifest").mkdir()
    return str(tmp_path)


@pytest.fixture
def project_with_manifest(project_dir: str) -> str:
    """
    project_dir with manifests already applied (tabletalk apply has been run).
    Returns the project folder path.
    """
    from tabletalk.utils import apply_schema

    apply_schema(project_dir)
    return project_dir

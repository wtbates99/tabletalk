import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("tabletalk")


def initialize_project() -> None:
    """Create a working, no-paid-key SQLite Agent project."""
    project_folder = Path.cwd()
    config_path = project_folder / "tabletalk.yaml"

    if config_path.exists():
        print(f"Already initialized ({config_path} exists).")
        return

    config_content = """\
# tabletalk.yaml
connections:
  local:
    type: sqlite
    path: ./data/starter.db
    read_only: true

# LLM configuration — Ollama Cloud free tier; no paid API key
llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1
  max_tokens: 2000
  temperature: 0
  reasoning_effort: none
  request_timeout_seconds: 60

# Local development uses this free Ollama Cloud model through the local daemon.
# A failed request is never replaced by another model or local logic.

# Optional dbt artifacts:
# dbt:
#   project_dir: ../analytics
#   target_dir: ../analytics/target

agents: agents
evals: evals
"""

    config_path.write_text(config_content)
    data_directory = project_folder / "data"
    agents_directory = project_folder / "agents"
    fixture_directory = project_folder / "evals" / "fixtures"
    data_directory.mkdir()
    agents_directory.mkdir()
    fixture_directory.mkdir(parents=True)

    schema_sql = """\
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  order_date DATE NOT NULL,
  status TEXT NOT NULL,
  recognized_revenue NUMERIC NOT NULL
);
"""
    data_sql = """\
INSERT INTO orders VALUES
  (1, '2026-01-05', 'complete', 120.00),
  (2, '2026-01-17', 'complete', 80.00),
  (3, '2026-01-22', 'cancelled', 500.00),
  (4, '2026-02-02', 'complete', 60.00);
"""
    connection = sqlite3.connect(data_directory / "starter.db")
    try:
        connection.executescript(schema_sql + data_sql)
        connection.commit()
    finally:
        connection.close()
    (fixture_directory / "schema.sql").write_text(schema_sql)
    (fixture_directory / "data.sql").write_text(data_sql)

    (agents_directory / "sales.yaml").write_text(
        """\
kind: Agent
name: sales
description: Answers governed questions about recognized order revenue.
connection: local
relations:
  include:
    - main.orders
semantics:
  metrics:
    recognized_revenue:
      label: Recognized revenue
      description: Revenue from completed orders; cancelled orders are excluded.
      expression: main.orders.recognized_revenue
      aggregation: sum
      time_dimension: main.orders.order_date
      unit: USD
      synonyms: [revenue, sales]
      required_filters:
        - main.orders.status != 'cancelled'
  time:
    timezone: UTC
    week_start: monday
    default_dimension: main.orders.order_date
  rules:
    - Exclude cancelled orders unless explicitly requested.
    - State exact date boundaries for every relative period.
policies:
  read_only: true
  require_evidence: true
  allow_ambiguous_execution: false
  max_rows: 500
  timeout_seconds: 30
  max_repair_attempts: 1
evals:
  - starter-regression
sample_questions:
  - What was recognized revenue in January 2026?
  - How many completed orders were placed in January 2026?
"""
    )
    (project_folder / "evals" / "starter.yaml").write_text(
        """\
kind: EvalSuite
name: starter-regression
agent: sales
fixture:
  type: sqlite
  setup:
    - fixtures/schema.sql
    - fixtures/data.sql
cases:
  - name: january-recognized-revenue
    question: What was recognized revenue in January 2026?
    expected_interpretation:
      metric: recognized_revenue
      start_date: "2026-01-01"
      end_date: "2026-02-01"
      timezone: UTC
    expect:
      relations:
        required: [main.orders]
      columns:
        required: [recognized_revenue, order_date, status]
      reference_sql: |
        SELECT SUM(recognized_revenue) AS revenue
        FROM main.orders
        WHERE order_date >= '2026-01-01'
          AND order_date < '2026-02-01'
          AND status != 'cancelled'
      result:
        comparison: scalar
        absolute_tolerance: 0.01
      answer:
        require_supported_claims: true
        require_evidence: true
        required_disclosures: [exact_date_range, metric_definition, source_relation]
  - name: completed-january-orders
    question: How many completed orders were placed in January 2026?
    expect:
      relations:
        required: [main.orders]
      reference_sql: |
        SELECT COUNT(*) AS orders
        FROM main.orders
        WHERE order_date >= '2026-01-01'
          AND order_date < '2026-02-01'
          AND status = 'complete'
      result:
        comparison: scalar
      answer:
        require_supported_claims: true
        require_evidence: true
  - name: cancelled-revenue-explicit
    question: What cancelled-order revenue was recorded in January 2026?
    expect:
      relations:
        required: [main.orders]
      reference_sql: |
        SELECT SUM(recognized_revenue) AS revenue
        FROM main.orders
        WHERE order_date >= '2026-01-01'
          AND order_date < '2026-02-01'
          AND status = 'cancelled'
      result:
        comparison: scalar
      answer:
        require_supported_claims: true
        require_evidence: true
"""
    )
    print(
        "Project initialized.\n"
        "Next steps:\n"
        "  1. Run 'ollama signin && ollama pull gemma4:31b-cloud'\n"
        "  2. Run 'tabletalk compile sales'\n"
        "  3. Run 'tabletalk plan sales'\n"
        "  4. Run 'tabletalk apply sales' (runs required evals)\n"
        "  5. Run 'tabletalk ask sales \"What was revenue in January 2026?\"'\n"
        "\nDevelopment uses the free gemma4:31b-cloud model through Ollama. "
        "A failed request never triggers a substitute answer."
    )

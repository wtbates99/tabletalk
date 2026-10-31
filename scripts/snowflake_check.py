"""Check TableTalk's Snowflake integration against a real account. Read-only.

    uv run --extra snowflake python scripts/snowflake_check.py --project-folder PATH

Uses the project's dbt profile target, with any auth method: password, SSO
(authenticator: externalbrowser), or a key pair. It runs only SELECT, EXPLAIN,
SHOW, and ALTER SESSION, and checks four things:

1. TableTalk can connect with the configured auth.
2. EXPLAIN USING JSON returns GlobalStats.bytesAssigned, which the scan budget reads.
3. Each query sets STATEMENT_TIMEOUT_IN_SECONDS in the session.
4. Each query carries TableTalk's QUERY_TAG in query history.
"""

from __future__ import annotations

import argparse
import json
import sys

from tabletalk.project import Project
from tabletalk.scan_budget import format_bytes, query_tag


def _value(row: dict[str, object], name: str) -> object:
    """Read a column case-insensitively: Snowflake upper-cases unquoted names."""
    return next((value for key, value in row.items() if key.lower() == name), None)


def main() -> int:
    """Run the four checks and return 0 only if all of them pass."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-folder", default=".")
    args = parser.parse_args()
    project = Project.load(args.project_folder)
    connection = project.connection()
    if connection.dialect != "snowflake":
        print(f"The dbt target is {connection.dialect}, not snowflake.", file=sys.stderr)
        return 1

    node = next(
        node for agent in project.agents() for node in agent.resolve(project.manifest).nodes
    )
    sql = f"select * from {node.relation_name} limit 1"
    tag = query_tag("snowflake-check", "sha256:" + "0" * 64)
    failures = 0

    def report(name: str, passed: bool, detail: str) -> None:
        nonlocal failures
        failures += not passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")

    connection.ping()
    report("connect", True, f"{project.target().identity}")

    try:
        estimate = connection.estimate_scan_bytes(sql)
        report("explain", estimate is not None, f"{node.name}: {format_bytes(estimate or 0)}")
    except Exception as exc:  # Report every failure; one broken check must not hide the rest.
        report("explain", False, repr(exc))

    connection.execute(sql, 37, query_tag=tag)
    provider = connection.provider
    [timeout] = provider.execute_query(
        "show parameters like 'STATEMENT_TIMEOUT_IN_SECONDS' in session", timeout_seconds=37
    )
    report(
        "timeout",
        str(_value(timeout, "value")) == "37",
        f"session value {_value(timeout, 'value')}",
    )

    history = provider.execute_query(
        "select query_text, query_tag from table(information_schema.query_history_by_session("
        "result_limit => 50)) order by start_time desc"
    )
    tagged = [row for row in history if _value(row, "query_text") == sql]
    found = tagged[0] if tagged else {}
    report(
        "query tag",
        bool(found) and json.loads(str(_value(found, "query_tag") or "{}")) == json.loads(tag),
        str(_value(found, "query_tag")) if found else "query not found in session history",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

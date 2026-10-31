.PHONY: demo replay

# Offline demos on the DuckDB example project. See examples/dbt-analytics/demo/.
demo:
	uv run --all-groups bash examples/dbt-analytics/demo/drift.sh

replay:
	uv run --all-groups bash examples/dbt-analytics/demo/replay.sh

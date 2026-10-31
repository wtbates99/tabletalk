.PHONY: demo

# Offline drift demo on the DuckDB example project. See examples/dbt-analytics/demo/.
demo:
	uv run --all-groups bash examples/dbt-analytics/demo/drift.sh

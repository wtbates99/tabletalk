from pathlib import Path

import duckdb


root = Path(__file__).resolve().parent
database = root / "data" / "analytics.duckdb"
database.parent.mkdir(exist_ok=True)
connection = duckdb.connect(str(database))
try:
    connection.execute((root / "evals" / "fixtures" / "schema.sql").read_text())
    connection.execute((root / "evals" / "fixtures" / "data.sql").read_text())
finally:
    connection.close()

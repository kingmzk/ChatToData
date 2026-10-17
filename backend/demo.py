"""Create an isolated synthetic demo source and shared runtime configuration."""
import argparse
import json
import sqlite3
from pathlib import Path


def initialize(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "source.sqlite"
    config = directory / "runtime.json"
    if source.exists() or config.exists():
        raise FileExistsError("Demo output already exists; choose a new directory")
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, tenant_id TEXT, customer_id TEXT, amount TEXT, year TEXT)")
    for index, year in enumerate(range(2020, 2025)):
        for customer in range(1, 6):
            conn.execute("INSERT INTO sales (tenant_id,customer_id,amount,year) VALUES (?,?,?,?)",
                         ("tenant_a", str(customer), str(20 + index * 4), str(year)))
    conn.commit()
    conn.close()
    catalog = {
        "release_id": "demo_v1", "epoch": 1, "source_id": "demo_sales",
        "tables": {"sales": {"table_name": "sales", "grain": "sale", "columns": {"id": "INTEGER", "tenant_id": "STRING", "customer_id": "STRING", "amount": "DECIMAL", "year": "STRING"}}},
        "measures": {"revenue": {"id": "revenue", "name": "Revenue", "description": "Synthetic demo revenue", "table": "sales", "column": "amount", "aggregation": "SUM", "base_grain": "sale", "unit": "USD", "protected_entity_column": "customer_id", "min_entities": 5,
            "allowed_dimensions": ["year"], "allowed_families": ["TOTAL", "TREND", "COMPARISON", "CONTRIBUTION", "FORECAST", "ANOMALY", "SCENARIO", "GOAL_SEEK"]}},
        "dimensions": {"year": {"id": "year", "name": "Year", "table": "sales", "column": "year", "data_type": "STRING"}},
        "default_period_dimension": "year", "time_series_policies": {"revenue": {"metric": "revenue", "period_dimension": "year", "frequency": "YEAR", "min_history_periods": 4, "max_forecast_horizon": 4}},
        "scenario_models": {"price_volume": {"model_id": "price_volume", "name": "Fixed-volume revenue", "description": "Synthetic price times fixed volume", "output_metric": "revenue", "formula_expression": "price * quantity", "fixed_assumptions": ["Quantity remains fixed"], "permitted_adjustments": ["price"], "drivers": {
            "price": {"name": "price", "unit": "USD", "min_value": "1", "max_value": "100", "default_value": "20"},
            "quantity": {"name": "quantity", "unit": "units", "default_value": "5", "is_adjustable": False}}}}
    }
    config.write_text(json.dumps({"source_database": "source.sqlite", "tenant_source_permissions": {"tenant_a": ["demo_sales"]}, "catalogs": [catalog]}, indent=2), encoding="utf-8")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="demo")
    print(initialize(parser.parse_args().directory))

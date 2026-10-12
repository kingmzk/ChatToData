"""Shared declarative runtime configuration for API and worker processes."""
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from talk2data.catalog.schema import (SemanticCatalog, TableDefinition, MeasureDefinition,
    DimensionDefinition, ApprovedJoinEdge, ScenarioModelDefinition, ScenarioDriverDefinition,
    TimeSeriesPolicy, DataType, AggregationType, JoinType, JoinCardinality)
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.security.auth import SecurityRuntime
from talk2data.execution.gateway import GenericSourceGateway


def load_runtime():
    from talk2data.config import get_env_mode
    if get_env_mode() == "production" and (not os.getenv("TALK2DATA_AUTH_SECRET") or not os.getenv("TALK2DATA_AUDIT_KEY")):
        raise RuntimeError("Production requires TALK2DATA_AUTH_SECRET and TALK2DATA_AUDIT_KEY")
    config_path = os.getenv("TALK2DATA_RUNTIME_CONFIG")
    if not config_path:
        if Path("browser-demo/runtime.json").exists():
            config_path = "browser-demo/runtime.json"
        elif Path("demo/runtime.json").exists():
            config_path = "demo/runtime.json"
        else:
            raise RuntimeError("Set TALK2DATA_RUNTIME_CONFIG to the shared runtime JSON file")
    path = Path(config_path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    sec = SecurityRuntime({k: set(v) for k, v in config["tenant_source_permissions"].items()})
    catalogs = SemanticCatalogStore()
    for raw in config["catalogs"]:
        raw = dict(raw)
        raw["tables"] = {k: TableDefinition(**dict(v, columns={c: DataType(t) for c, t in v.get("columns", {}).items()})) for k, v in raw["tables"].items()}
        raw["measures"] = {k: MeasureDefinition(**dict(v, aggregation=AggregationType(v["aggregation"]))) for k, v in raw["measures"].items()}
        raw["dimensions"] = {k: DimensionDefinition(**dict(v, data_type=DataType(v["data_type"]))) for k, v in raw["dimensions"].items()}
        raw["joins"] = tuple(ApprovedJoinEdge(**dict(v, join_type=JoinType(v.get("join_type", "INNER")), cardinality=JoinCardinality(v.get("cardinality", "MANY_TO_ONE")))) for v in raw.get("joins", []))
        raw["time_series_policies"] = {k: TimeSeriesPolicy(**v) for k, v in raw.get("time_series_policies", {}).items()}
        models = {}
        for key, value in raw.get("scenario_models", {}).items():
            value = dict(value)
            drivers = {}
            for name, driver in value["drivers"].items():
                driver = dict(driver)
                for field in ("default_value", "min_value", "max_value"):
                    if driver.get(field) is not None:
                        driver[field] = Decimal(str(driver[field]))
                drivers[name] = ScenarioDriverDefinition(**driver)
            value["drivers"] = drivers
            models[key] = ScenarioModelDefinition(**value)
        raw["scenario_models"] = models
        catalogs.register_catalog(SemanticCatalog(**raw))
    source_path = (path.parent / config["source_database"]).resolve()
    if not source_path.is_file():
        raise RuntimeError("Configured source database does not exist")
    conn = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    return sec, catalogs, GenericSourceGateway(conn)

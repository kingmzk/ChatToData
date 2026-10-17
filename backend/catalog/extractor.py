"""Automated schema introspection and semantic catalog generator for SQLite databases."""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from backend.catalog.schema import (
    SemanticCatalog,
    TableDefinition,
    MeasureDefinition,
    DimensionDefinition,
    ApprovedJoinEdge,
    AggregationType,
    DataType,
    JoinCardinality,
    JoinType,
)


NUMERIC_TYPES = {
    "INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT",
    "REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "NUMBER"
}

DATE_TYPES = {
    "DATE", "DATETIME", "TIMESTAMP", "TIME"
}


def _infer_data_type(sql_type: str, col_name: str) -> DataType:
    """Infer semantic DataType enum from SQL type and column name heuristics."""
    clean_type = (sql_type or "").upper().split("(")[0].strip()
    col_lower = col_name.lower()

    if clean_type in DATE_TYPES or any(k in col_lower for k in ("date", "time", "created_at", "updated_at", "timestamp")):
        return DataType.DATETIME if "time" in clean_type or "time" in col_lower else DataType.DATE
    if clean_type in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT"):
        return DataType.INTEGER
    if clean_type in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "NUMBER"):
        return DataType.DECIMAL
    if clean_type in ("BOOLEAN", "BOOL"):
        return DataType.BOOLEAN
    return DataType.STRING


def _infer_unit(col_name: str) -> str:
    """Infer reasonable business unit based on column name."""
    lower = col_name.lower()
    if any(k in lower for k in ("amount", "price", "revenue", "sales", "cost", "salary", "fee", "val")):
        return "USD"
    if any(k in lower for k in ("qty", "quantity", "count", "num_")):
        return "Units"
    if any(k in lower for k in ("pct", "percent", "rate", "ratio")):
        return "%"
    if any(k in lower for k in ("score", "rating")):
        return "Points"
    return "Numeric"


def introspect_sqlite_database(
    db_path: Path,
    source_id: str,
    default_tenant_id: str = "tenant_a",
) -> tuple[SemanticCatalog, dict[str, Any]]:
    """Inspect SQLite database and auto-generate an Approved Semantic Catalog and UI discovery manifest.
    
    Extracts:
    1. Physical tables with column definitions and row counts.
    2. Primary keys and tenant boundaries.
    3. Explicit foreign keys and inferred cross-table key relationships.
    4. Numeric measures with appropriate aggregation (SUM/AVG) and units.
    5. Categorical/temporal dimensions.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. Discover user tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    raw_tables = [r[0] for r in cur.fetchall()]

    if not raw_tables:
        conn.close()
        raise ValueError(f"No user tables found in database '{db_path.name}'.")

    tables_meta: dict[str, dict[str, Any]] = {}
    table_defs: dict[str, TableDefinition] = {}
    all_table_columns: dict[str, dict[str, str]] = {}

    for tbl in raw_tables:
        # Columns: (cid, name, type, notnull, dflt_value, pk)
        cols_info = cur.execute(f"PRAGMA table_info('{tbl}')").fetchall()
        pks = [c[1] for c in cols_info if c[5] > 0]
        col_type_map = {c[1]: (c[2] or "TEXT") for c in cols_info}
        all_table_columns[tbl] = col_type_map

        # Check for tenant column
        tenant_col = None
        for cand in ("tenant_id", "tenant", "org_id", "company_id", "account_id"):
            if cand in col_type_map:
                tenant_col = cand
                break

        # Count rows (bounded check)
        try:
            row_count = cur.execute(f"SELECT COUNT(*) FROM '{tbl}'").fetchone()[0]
        except Exception:
            row_count = 0

        primary_key = tuple(pks) if pks else ("id",) if "id" in col_type_map else (list(col_type_map.keys())[0],)

        tables_meta[tbl] = {
            "name": tbl,
            "row_count": row_count,
            "primary_key": list(primary_key),
            "tenant_column": tenant_col or "tenant_id",
            "has_tenant_isolation": tenant_col is not None,
            "columns": [
                {
                    "name": c[1],
                    "sql_type": c[2] or "TEXT",
                    "data_type": _infer_data_type(c[2], c[1]).value,
                    "is_primary_key": c[5] > 0,
                    "is_tenant_col": c[1] == tenant_col,
                }
                for c in cols_info
            ],
        }

        table_defs[tbl] = TableDefinition(
            table_name=tbl,
            primary_key=primary_key,
            tenant_column=tenant_col or "tenant_id",
            grain=f"{tbl}_grain",
        )

    # 2. Extract Foreign Keys & Join Relationships
    approved_joins: list[ApprovedJoinEdge] = []
    join_manifest: list[dict[str, Any]] = []
    seen_join_pairs = set()

    for tbl in raw_tables:
        # Explicit FKs: (id, seq, table, from, to, on_update, on_delete, match)
        fks = cur.execute(f"PRAGMA foreign_key_list('{tbl}')").fetchall()
        for fk in fks:
            target_table = fk[2]
            from_col = fk[3]
            to_col = fk[4]
            if target_table in raw_tables and from_col and to_col:
                edge_key = (tbl, target_table, from_col, to_col)
                if edge_key not in seen_join_pairs:
                    seen_join_pairs.add(edge_key)
                    approved_joins.append(
                        ApprovedJoinEdge(
                            from_table=tbl,
                            to_table=target_table,
                            from_column=from_col,
                            to_column=to_col,
                            cardinality=JoinCardinality.MANY_TO_ONE,
                            join_type=JoinType.INNER,
                        )
                    )
                    join_manifest.append({
                        "from_table": tbl,
                        "to_table": target_table,
                        "from_column": from_col,
                        "to_column": to_col,
                        "cardinality": "MANY_TO_ONE",
                        "join_type": "INNER",
                        "detection_method": "EXPLICIT_FOREIGN_KEY",
                    })

    # Inferred relationships from matching column names (e.g. customer_id)
    for tbl_a in raw_tables:
        for tbl_b in raw_tables:
            if tbl_a >= tbl_b:
                continue
            cols_a = set(all_table_columns[tbl_a].keys())
            cols_b = set(all_table_columns[tbl_b].keys())
            common_id_cols = [c for c in cols_a.intersection(cols_b) if c.endswith("_id") and c != "tenant_id"]

            for col in common_id_cols:
                edge_key = (tbl_a, tbl_b, col, col)
                reverse_key = (tbl_b, tbl_a, col, col)
                if edge_key not in seen_join_pairs and reverse_key not in seen_join_pairs:
                    seen_join_pairs.add(edge_key)
                    approved_joins.append(
                        ApprovedJoinEdge(
                            from_table=tbl_a,
                            to_table=tbl_b,
                            from_column=col,
                            to_column=col,
                            cardinality=JoinCardinality.MANY_TO_ONE,
                            join_type=JoinType.INNER,
                        )
                    )
                    join_manifest.append({
                        "from_table": tbl_a,
                        "to_table": tbl_b,
                        "from_column": col,
                        "to_column": col,
                        "cardinality": "MANY_TO_ONE",
                        "join_type": "INNER",
                        "detection_method": "INFERRED_MATCHING_ID_KEY",
                    })

    conn.close()

    # 3. Extract Dimensions
    dimensions: dict[str, DimensionDefinition] = {}
    dim_manifest: list[dict[str, Any]] = []

    for tbl, cols in all_table_columns.items():
        for col_name, sql_type in cols.items():
            if col_name in ("tenant_id", "id") and len(cols) > 2:
                continue
            data_type = _infer_data_type(sql_type, col_name)
            # Create dimension for non-metric or descriptive columns
            dim_id = f"{tbl}_{col_name}" if col_name in dimensions else col_name
            dim_def = DimensionDefinition(
                id=dim_id,
                name=col_name.replace("_", " ").title(),
                column=col_name,
                table=tbl,
                data_type=data_type,
            )
            dimensions[dim_id] = dim_def
            dim_manifest.append({
                "id": dim_id,
                "name": dim_def.name,
                "table": tbl,
                "column": col_name,
                "data_type": data_type.value,
            })

    # 4. Extract Measures
    measures: dict[str, MeasureDefinition] = {}
    measure_manifest: list[dict[str, Any]] = []
    allowed_dim_ids = tuple(dimensions.keys())

    for tbl, cols in all_table_columns.items():
        for col_name, sql_type in cols.items():
            clean_type = (sql_type or "").upper().split("(")[0].strip()
            # If numeric and not a primary or foreign key ID
            is_numeric = clean_type in NUMERIC_TYPES or any(k in col_name.lower() for k in ("amount", "price", "revenue", "sales", "cost", "total", "rate", "qty", "quantity"))
            is_id_column = col_name.endswith("_id") or col_name == "id" or col_name == "tenant_id"

            if is_numeric and not is_id_column:
                meas_id = f"{tbl}_{col_name}"
                unit = _infer_unit(col_name)
                meas_def = MeasureDefinition(
                    id=meas_id,
                    name=f"{tbl.replace('_', ' ').title()} {col_name.replace('_', ' ').title()}",
                    description=f"{col_name.replace('_', ' ').title()} aggregate on {tbl}",
                    table=tbl,
                    column=col_name,
                    aggregation=AggregationType.SUM,
                    base_grain=f"{tbl}_grain",
                    unit=unit,
                    allowed_dimensions=allowed_dim_ids,
                )
                measures[meas_id] = meas_def
                measure_manifest.append({
                    "id": meas_id,
                    "name": meas_def.name,
                    "table": tbl,
                    "column": col_name,
                    "unit": unit,
                    "aggregation": "SUM",
                })

    # If no numeric column was found, provide a default row count measure
    if not measures and raw_tables:
        first_tbl = raw_tables[0]
        pk_col = table_defs[first_tbl].primary_key[0]
        meas_id = f"{first_tbl}_count"
        meas_def = MeasureDefinition(
            id=meas_id,
            name=f"{first_tbl.replace('_', ' ').title()} Records",
            description=f"Total count of {first_tbl} records",
            table=first_tbl,
            column=pk_col,
            aggregation=AggregationType.COUNT,
            base_grain=f"{first_tbl}_grain",
            unit="Records",
            allowed_dimensions=allowed_dim_ids,
        )
        measures[meas_id] = meas_def
        measure_manifest.append({
            "id": meas_id,
            "name": meas_def.name,
            "table": first_tbl,
            "column": pk_col,
            "unit": "Records",
            "aggregation": "COUNT",
        })

    # Construct complete SemanticCatalog
    catalog = SemanticCatalog(
        release_id="rel_auto_v1",
        epoch=1,
        source_id=source_id,
        tables=table_defs,
        measures=measures,
        dimensions=dimensions,
        joins=tuple(approved_joins),
        scenario_models={},
        time_series_policies={},
    )

    discovery_manifest = {
        "source_id": source_id,
        "database_name": db_path.name,
        "database_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "tables": list(tables_meta.values()),
        "relationships": join_manifest,
        "measures": measure_manifest,
        "dimensions": dim_manifest,
        "summary": {
            "total_tables": len(tables_meta),
            "total_relationships": len(join_manifest),
            "total_measures": len(measure_manifest),
            "total_dimensions": len(dim_manifest),
        },
    }

    return catalog, discovery_manifest

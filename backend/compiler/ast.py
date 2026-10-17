"""AST and compiled query package structures."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PhysicalQueryPackage:
    """Read-only parameterized SQL package with bound parameters and safety attributes."""
    sql: str
    parameters: dict[str, Any]
    dialect: str = "sqlite"  # sqlite, postgresql, tsql
    timeout_seconds: int = 30
    is_aggregate: bool = True

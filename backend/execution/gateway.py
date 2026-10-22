"""Generic source gateway for parameterized read-only execution with transient memory cleanup."""
from __future__ import annotations
from backend.observability import traced_stage
import gc
import hashlib
import sqlite3
import time
import threading
from typing import Any, Optional
from backend.compiler.ast import PhysicalQueryPackage


class GatewayExecutionError(Exception):
    """Raised when query execution fails in physical database."""
    pass


from decimal import Decimal, InvalidOperation


class DecimalSumAggregate:
    """Exact decimal sum aggregate for SQLite, preventing IEEE-754 drift."""
    def __init__(self):
        self.total = Decimal("0")
        self.has_value = False

    def step(self, value: Any):
        if value is not None and str(value).strip() != "":
            try:
                self.total += Decimal(str(value).strip())
                self.has_value = True
            except (InvalidOperation, ValueError):
                raise ValueError("Invalid numeric source value")

    def finalize(self) -> Optional[str]:
        return str(self.total) if self.has_value else None


class DecimalAvgAggregate:
    """Exact decimal average aggregate for SQLite."""
    def __init__(self):
        self.total = Decimal("0")
        self.count = 0

    def step(self, value: Any):
        if value is not None and str(value).strip() != "":
            try:
                self.total += Decimal(str(value).strip())
                self.count += 1
            except (InvalidOperation, ValueError):
                raise ValueError("Invalid numeric source value")

    def finalize(self) -> Optional[str]:
        if self.count == 0:
            return None
        return str(self.total / Decimal(str(self.count)))


class GenericSourceGateway:
    """Manages physical database connections and transient buffer lifecycle with exact decimal arithmetic."""

    def __init__(self, connection_or_factory: Any = None):
        self._lock = threading.RLock()
        self._conn = connection_or_factory
        self._source_connections: dict[str, sqlite3.Connection] = {}
        self._transient_buffer: list[dict[str, Any]] = []
        if isinstance(self._conn, sqlite3.Connection):
            self._register_decimal_aggregates(self._conn)

    def register_source(self, source_id: str, db_path_or_conn: Any) -> None:
        """Register a physical database path or connection for a specific source_id."""
        with self._lock:
            from pathlib import Path
            if isinstance(db_path_or_conn, (str, Path)):
                p = Path(db_path_or_conn).resolve()
                conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, check_same_thread=False)
                self._register_decimal_aggregates(conn)
                self._source_connections[source_id] = conn
            elif isinstance(db_path_or_conn, sqlite3.Connection):
                self._register_decimal_aggregates(db_path_or_conn)
                self._source_connections[source_id] = db_path_or_conn

    def _get_connection(self, source_id: Optional[str] = None) -> Any:
        with self._lock:
            if source_id and source_id in self._source_connections:
                return self._source_connections[source_id]
            return self._conn

    @staticmethod
    def _register_decimal_aggregates(conn: sqlite3.Connection) -> None:
        """Register exact decimal aggregates on SQLite connection."""
        try:
            conn.create_function("DECIMAL_DIV", 2, lambda value, divisor: str(Decimal(str(value)) / Decimal(str(divisor))))
            conn.create_aggregate("SUM", 1, DecimalSumAggregate)
            conn.create_aggregate("AVG", 1, DecimalAvgAggregate)
        except Exception:
            pass

    @traced_stage('query_execution')
    def execute_package(self, package: PhysicalQueryPackage, source_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Execute parameterized query package and return transient tabular records."""
        conn = self._get_connection(source_id)
        try:
            # If using an active sqlite3 connection
            if isinstance(conn, sqlite3.Connection):
                self._register_decimal_aggregates(conn)
                cur = conn.cursor()
                # SQLite uses :param_name syntax matching our compiler
                if not package.sql.lstrip().upper().startswith("SELECT "):
                    raise GatewayExecutionError("Only governed SELECT queries are allowed.")
                deadline = time.monotonic() + package.timeout_seconds
                conn.execute("PRAGMA query_only = ON")
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                cur.execute(package.sql, package.parameters)
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
                results = [dict(zip(columns, row)) for row in rows]
                self._transient_buffer = results
                return list(results)
            else:
                raise GatewayExecutionError("Unsupported or uninitialized database connection.")
        except Exception as exc:
            db_list = conn.execute("PRAGMA database_list").fetchall() if isinstance(conn, sqlite3.Connection) else "Not SQLite"
            print(f"[Gateway Debug Error] exc: {exc} | source_id: {source_id} | db_list: {db_list} | sql: {package.sql}")
            raise GatewayExecutionError(f"Database execution error: {exc}") from exc
        finally:
            if isinstance(conn, sqlite3.Connection):
                conn.set_progress_handler(None, 0)

    def close_without_values(self) -> str:
        """Controlled transient-data handling and best-effort buffer disposal.
        
        Flushes and deletes in-memory row buffers, invokes garbage collection,
        and returns a buffer-clear fingerprint digest for audit logging.
        
        NOTE: This is application-level transient memory disposal, not hardware-level
        cryptographic zeroing or secure RAM sanitization.
        """
        buffer_len = len(self._transient_buffer)
        self._transient_buffer.clear()
        del self._transient_buffer
        self._transient_buffer = []
        gc.collect()

        # Produce a verifiable receipt digest indicating buffer flush
        receipt_text = f"buffer_disposal_ok:cleared_{buffer_len}_transient_rows"
        return hashlib.sha256(receipt_text.encode("utf-8")).hexdigest()

"""Tests for Governed Query Compiler Security and Parameterization."""
import pytest
from talk2data.compiler.compiler import GovernedQueryCompiler, QueryCompilationError
from talk2data.intent.schema import StructuredIntent, AnalysisFamily, FilterSpec


def test_compiler_parameterizes_all_filters(test_setup):
    """Ensure compiler binds all filters as parameters and does not concatenate raw values."""
    catalogs = test_setup["catalogs"]
    resolver = test_setup["manager"].resolver
    compiler = GovernedQueryCompiler()

    pin = catalogs.pin("tenant_a", "finance_dw")
    cat = catalogs.get_catalog("finance_dw", pin.release_id)

    intent = StructuredIntent(
        measure="sales_revenue",
        family=AnalysisFamily.TOTAL,
        dimensions=("country",),
        filters=(FilterSpec(dimension="country", operator="=", values=("US",)),),
        current_period="2024",
    )

    plan = resolver.resolve(intent, cat, pin, "turn_sec", "tenant_a")
    package = compiler.compile(plan)

    # Assert SQL is parameterized and contains no raw literal 'US'
    assert ":tenant_id" in package.sql
    assert ":p_filt_1" in package.sql
    assert ":p_period_2" in package.sql
    assert "'US'" not in package.sql
    assert package.parameters["p_filt_1"] == "US"
    assert package.parameters["tenant_id"] == "tenant_a"


def test_compiler_rejects_sql_injection_in_identifiers(test_setup):
    """Ensure identifier allowlist rejects SQL injection tokens."""
    compiler = GovernedQueryCompiler()

    with pytest.raises(QueryCompilationError):
        compiler._validate_identifier("orders; DROP TABLE orders; --")

    with pytest.raises(QueryCompilationError):
        compiler._validate_identifier("amount /* comment */")


def test_compiler_rejects_multistatement_queries(test_setup):
    """Ensure compiler rejects any query with semicolons or forbidden DDL/DML."""
    compiler = GovernedQueryCompiler()

    with pytest.raises(QueryCompilationError):
        compiler._assert_read_only("SELECT 1; DROP TABLE users;")

    with pytest.raises(QueryCompilationError):
        compiler._assert_read_only("SELECT 1 FROM orders WHERE DELETE FROM orders")

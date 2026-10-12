"""Independent verification tests for GovernedScenarioEngine and GovernedGoalSeekEngine."""
from decimal import Decimal
import pytest
from talk2data.catalog.schema import ScenarioDriverDefinition, ScenarioModelDefinition
from talk2data.science.scenario import GovernedScenarioEngine, ScenarioExecutionError
from talk2data.science.goal_seek import GovernedGoalSeekEngine, GoalSeekError


@pytest.fixture
def pricing_scenario_model():
    """Approved declarative scenario model: sales_revenue = price * quantity."""
    return ScenarioModelDefinition(
        model_id="revenue_model",
        name="Revenue Simulation",
        description="Simulate revenue under price adjustments",
        output_metric="sales_revenue",
        formula_expression="price * quantity",
        drivers={
            "price": ScenarioDriverDefinition(
                name="price",
                unit="USD",
                min_value=Decimal("5.00"),
                max_value=Decimal("100.00"),
                default_value=Decimal("20.00"),
                is_adjustable=True,
            ),
            "quantity": ScenarioDriverDefinition(
                name="quantity",
                unit="units",
                min_value=Decimal("1"),
                max_value=Decimal("100000"),
                default_value=Decimal("1000"),
                is_adjustable=False,  # Fixed in this model
            ),
        },
        fixed_assumptions=("Quantity held constant at baseline volume.",),
        permitted_adjustments=("price",),
    )


def test_hand_calculated_scenario_evaluation(pricing_scenario_model):
    """Verify scenario math against hand-calculated arithmetic.
    
    Baseline: price = $20, quantity = 1000 -> 20,000 USD
    Adjustment: price = $25
    Simulated output: 25 * 1000 = 25,000 USD
    Delta: +5,000 USD
    """
    engine = GovernedScenarioEngine()
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    adjustments = {"price": Decimal("25")}
    facts = engine.evaluate_scenario(pricing_scenario_model, baseline, adjustments)
    
    assert len(facts) == 1
    fact = facts[0]
    assert fact.metric == "sales_revenue"
    assert fact.value == Decimal("25000.00")
    assert fact.result_type == "HYPOTHETICAL"
    assert fact.diagnostics["baseline_output"] == "20000.00"
    assert fact.diagnostics["delta"] == "5000.00"
    assert fact.diagnostics["driver_price"] == "25"
    assert fact.diagnostics["driver_quantity"] == "1000"
    assert any("Quantity held constant" in a for a in fact.assumptions)


def test_scenario_unapproved_adjustment_rejected(pricing_scenario_model):
    """Verify adjusting a driver not in permitted_adjustments raises ScenarioExecutionError."""
    engine = GovernedScenarioEngine()
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    adjustments = {"quantity": Decimal("2000")}  # quantity is not in permitted_adjustments
    with pytest.raises(ScenarioExecutionError, match="Adjustment to driver 'quantity' is not permitted"):
        engine.evaluate_scenario(pricing_scenario_model, baseline, adjustments)


def test_scenario_boundary_violation_rejected(pricing_scenario_model):
    """Verify adjustment outside approved min_value/max_value bounds raises ScenarioExecutionError."""
    engine = GovernedScenarioEngine()
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    with pytest.raises(ScenarioExecutionError, match="below permitted minimum"):
        engine.evaluate_scenario(pricing_scenario_model, baseline, {"price": Decimal("2.00")})


def test_goal_seek_solvable_case(pricing_scenario_model):
    """Verify bracketed goal seek solver finds exact driver value.
    
    Target: 30,000 USD
    Adjustable: price (bounds 5 to 100)
    Baseline quantity: 1000
    Required price: 30,000 / 1000 = 30.00 USD
    """
    solver = GovernedGoalSeekEngine(tolerance=Decimal("0.01"))
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    res = solver.solve(
        model=pricing_scenario_model,
        target_metric="sales_revenue",
        target_value=Decimal("30000"),
        adjustable_driver="price",
        baseline_drivers=baseline,
    )
    assert res.status == "FEASIBLE"
    assert res.solved_driver_value is not None
    assert abs(res.solved_driver_value - Decimal("30.00")) <= Decimal("0.01")
    assert abs(res.achieved_output - Decimal("30000.00")) <= Decimal("0.01")
    assert res.residual_error <= Decimal("0.01")


def test_goal_seek_unreachable_target(pricing_scenario_model):
    """Verify target beyond maximum driver bound returns UNREACHABLE_TARGET and reports residual error."""
    solver = GovernedGoalSeekEngine()
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    # Max price is 100 -> Max achievable revenue = 100 * 1000 = 100,000
    # Target = 200,000 (unreachable)
    res = solver.solve(
        model=pricing_scenario_model,
        target_metric="sales_revenue",
        target_value=Decimal("200000"),
        adjustable_driver="price",
        baseline_drivers=baseline,
    )
    assert res.status == "UNREACHABLE_TARGET"
    assert res.solved_driver_value is None
    assert res.achieved_output == Decimal("100000.00")
    assert res.residual_error == Decimal("100000.00")
    
    fact = res.facts[0]
    assert fact.completeness_status == "LIMITED_SCOPE"
    assert any("UNREACHABLE" in a for a in fact.assumptions)


def test_goal_seek_non_adjustable_driver_rejected(pricing_scenario_model):
    """Verify attempting to solve against a non-adjustable driver raises GoalSeekError."""
    solver = GovernedGoalSeekEngine()
    baseline = {"price": Decimal("20"), "quantity": Decimal("1000")}
    with pytest.raises(GoalSeekError, match="marked as non-adjustable"):
        solver.solve(
            model=pricing_scenario_model,
            target_metric="sales_revenue",
            target_value=Decimal("30000"),
            adjustable_driver="quantity",
            baseline_drivers=baseline,
        )

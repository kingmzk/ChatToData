"""Governed declarative scenario analysis engine executing approved formulas without arbitrary extrapolation."""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Optional

from talk2data.catalog.schema import ScenarioModelDefinition
from talk2data.domain.facts import ProtectedFact


class ScenarioExecutionError(Exception):
    """Raised when scenario inputs violate catalog boundaries or bounds."""
    pass


class GovernedScenarioEngine:
    """Executes declarative scenario models using strictly approved catalog formulas and driver bounds."""

    def evaluate_scenario(
        self,
        model: ScenarioModelDefinition,
        baseline_drivers: dict[str, Decimal],
        adjustments: dict[str, Decimal],
        turn_id: str = "turn_scen",
    ) -> list[ProtectedFact]:
        """Evaluate hypothetical scenario outputs, validating bounds and recording fixed/variable driver assumptions."""
        # 1. Validate drivers and permitted adjustments
        for adj_driver in adjustments.keys():
            if adj_driver not in model.drivers:
                raise ScenarioExecutionError(
                    f"Driver '{adj_driver}' is not part of approved scenario model '{model.name}'."
                )
            if model.permitted_adjustments and adj_driver not in model.permitted_adjustments:
                raise ScenarioExecutionError(
                    f"Adjustment to driver '{adj_driver}' is not permitted by model '{model.name}'."
                )

        # 2. Build final effective driver values and check bounds
        effective_drivers: dict[str, Decimal] = {}
        driver_assumptions: list[str] = list(model.fixed_assumptions)

        for d_name, d_def in model.drivers.items():
            if d_name in adjustments:
                val = adjustments[d_name]
                # Check range bounds
                if d_def.min_value is not None and val < d_def.min_value:
                    raise ScenarioExecutionError(
                        f"Adjusted value {val} for driver '{d_name}' is below permitted minimum {d_def.min_value} {d_def.unit}."
                    )
                if d_def.max_value is not None and val > d_def.max_value:
                    raise ScenarioExecutionError(
                        f"Adjusted value {val} for driver '{d_name}' exceeds permitted maximum {d_def.max_value} {d_def.unit}."
                    )
                effective_drivers[d_name] = val
                driver_assumptions.append(f"Driver '{d_name}' adjusted to {val} {d_def.unit}.")
            elif d_name in baseline_drivers:
                val = baseline_drivers[d_name]
                effective_drivers[d_name] = val
                driver_assumptions.append(f"Driver '{d_name}' held constant at baseline value of {val} {d_def.unit}.")
            elif d_def.default_value is not None:
                val = d_def.default_value
                effective_drivers[d_name] = val
                driver_assumptions.append(f"Driver '{d_name}' set to default value of {val} {d_def.unit}.")
            else:
                raise ScenarioExecutionError(
                    f"Missing required driver '{d_name}' for scenario model '{model.name}' with no baseline or default."
                )

        for name, val in effective_drivers.items():
            definition = model.drivers[name]
            if not val.is_finite():
                raise ScenarioExecutionError("Driver values must be finite")
            if definition.min_value is not None and val < definition.min_value:
                raise ScenarioExecutionError("Baseline below approved driver minimum")
            if definition.max_value is not None and val > definition.max_value:
                raise ScenarioExecutionError("Baseline above approved driver maximum")
        # 3. Compute output using approved declarative formula
        # We evaluate approved catalog formula expressions safely without arbitrary code execution
        output_value = self._evaluate_formula(model.formula_expression, effective_drivers)

        # Compute baseline output value if baseline_drivers provided all required drivers
        baseline_output = None
        if all(d in baseline_drivers for d in model.drivers):
            baseline_output = self._evaluate_formula(model.formula_expression, baseline_drivers)

        fact = ProtectedFact(
            fact_id=f"fact_scen_{turn_id}_01",
            metric=model.output_metric,
            calculation_type="SCENARIO",
            value=output_value,
            unit=list(model.drivers.values())[0].unit if model.drivers else "USD",
            semantic_release="",
            is_suppressed=False,
            completeness_status="COMPLETE",
            assumptions=driver_assumptions,
            limitations=[
                "Hypothetical scenario analysis represents simulated output under stated assumptions.",
                "Results do not include unmodeled elasticities, external market dynamics, or competitive responses.",
            ],
            method="DECLARATIVE_SCENARIO",
            method_version="1.0.0",
            result_type="HYPOTHETICAL",
            diagnostics={
                "model_name": model.name,
                "formula": model.formula_expression,
                "baseline_output": str(round(baseline_output, 2)) if baseline_output is not None else "N/A",
                "delta": str(round(output_value - baseline_output, 2)) if baseline_output is not None else "N/A",
                **{f"driver_{k}": str(v) for k, v in effective_drivers.items()},
            },
        )
        return [fact]

    def _evaluate_formula(self, formula: str, drivers: dict[str, Decimal]) -> Decimal:
        """Safely compute declarative mathematical formula without arbitrary eval or exec using AST."""
        import ast

        def _eval_node(node: ast.AST) -> Decimal:
            if isinstance(node, ast.Expression):
                return _eval_node(node.body)
            elif isinstance(node, ast.Constant):
                return Decimal(str(node.value))
            elif isinstance(node, ast.Name):
                if node.id in drivers:
                    return drivers[node.id]
                raise ScenarioExecutionError(f"Undefined driver variable '{node.id}' in formula '{formula}'.")
            elif isinstance(node, ast.UnaryOp):
                operand = _eval_node(node.operand)
                if isinstance(node.op, ast.USub):
                    return -operand
                elif isinstance(node.op, ast.UAdd):
                    return +operand
                raise ScenarioExecutionError(f"Unsupported unary operator: {type(node.op)}")
            elif isinstance(node, ast.BinOp):
                left = _eval_node(node.left)
                right = _eval_node(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                elif isinstance(node.op, ast.Sub):
                    return left - right
                elif isinstance(node.op, ast.Mult):
                    return left * right
                elif isinstance(node.op, ast.Div):
                    if right == Decimal("0"):
                        raise ZeroDivisionError("Division by zero in scenario formula.")
                    return left / right
                elif isinstance(node.op, ast.Pow):
                    return left ** right
                raise ScenarioExecutionError(f"Unsupported binary operator: {type(node.op)}")
            else:
                raise ScenarioExecutionError(f"Unsupported syntax in formula: {type(node).__name__}")

        try:
            tree = ast.parse(formula.strip(), mode="eval")
            return _eval_node(tree)
        except ZeroDivisionError:
            raise
        except ScenarioExecutionError:
            raise
        except Exception as exc:
            raise ScenarioExecutionError(f"Invalid formula syntax in catalog: '{formula}': {exc}") from exc

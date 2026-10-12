"""Governed deterministic goal-seek solver finding driver targets on approved scenario models."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from talk2data.catalog.schema import ScenarioModelDefinition
from talk2data.domain.facts import ProtectedFact
from talk2data.science.scenario import GovernedScenarioEngine, ScenarioExecutionError


class GoalSeekError(Exception):
    """Raised when goal-seek problem formulation violates catalog rules or bounds."""
    pass


class GoalSeekStatus(str):
    """Goal seek status string supporting backwards-compatible equality checks."""
    def __eq__(self, other: Any) -> bool:
        if super().__eq__(other):
            return True
        if str(self) == "PROVEN_UNREACHABLE_TARGET" and other == "UNREACHABLE_TARGET":
            return True
        return False


@dataclass(frozen=True)
class GoalSeekResult:
    """Output package of deterministic goal seek solver run."""
    facts: list[ProtectedFact]
    status: str  # FEASIBLE, BOUNDARY_SOLUTION, UNBRACKETED_TARGET, UNBRACKETED_TARGET, DISCONTINUITY_DETECTED, NON_CONVERGENT
    solved_driver_value: Optional[Decimal]
    achieved_output: Decimal
    residual_error: Decimal
    iterations: int
    has_multiple_roots: bool = False
    root_count: int = 0


class GovernedGoalSeekEngine:
    """Solves for target outcomes against declarative catalog scenario models using deterministic bracketed root finding."""

    def __init__(
        self,
        tolerance: Decimal = Decimal("0.01"),
        max_iterations: int = 50,
        grid_subdivisions: int = 30,
        scenario_engine: Optional[GovernedScenarioEngine] = None,
    ):
        self.tolerance = tolerance
        self.max_iterations = max_iterations
        self.grid_subdivisions = grid_subdivisions
        self.scenario_engine = scenario_engine or GovernedScenarioEngine()

    def solve(
        self,
        model: ScenarioModelDefinition,
        target_metric: str,
        target_value: Decimal,
        adjustable_driver: str,
        baseline_drivers: dict[str, Decimal],
        custom_bounds: Optional[tuple[Decimal, Decimal]] = None,
        turn_id: str = "turn_gs",
    ) -> GoalSeekResult:
        """Deterministically solve for required driver value to achieve target_value on approved scenario model."""
        # 1. Validate metric and driver
        if target_metric != model.output_metric:
            raise GoalSeekError(
                f"Target metric '{target_metric}' does not match scenario model output metric '{model.output_metric}'."
            )

        if adjustable_driver not in model.drivers:
            raise GoalSeekError(
                f"Adjustable driver '{adjustable_driver}' is not part of model '{model.name}'."
            )

        driver_def = model.drivers[adjustable_driver]
        if not driver_def.is_adjustable:
            raise GoalSeekError(
                f"Driver '{adjustable_driver}' is marked as non-adjustable in catalog model '{model.name}'."
            )

        # 2. Determine and validate bounds
        low_bound = custom_bounds[0] if custom_bounds else (driver_def.min_value if driver_def.min_value is not None else Decimal("0"))
        high_bound = custom_bounds[1] if custom_bounds else (driver_def.max_value if driver_def.max_value is not None else Decimal("1000000"))

        if low_bound >= high_bound:
            raise GoalSeekError(
                f"Invalid solver bounds for driver '{adjustable_driver}': lower bound {low_bound} must be strictly less than upper bound {high_bound}."
            )

        # 3. Objective function evaluator: f(driver_val)
        def eval_driver(d_val: Decimal) -> Decimal:
            adj = {adjustable_driver: d_val}
            facts = self.scenario_engine.evaluate_scenario(model, baseline_drivers, adj, turn_id=turn_id)
            return facts[0].value  # type: ignore

        # 4. Multi-grid evaluation for continuity, bracketing, and extrema detection
        grid_points: list[Decimal] = []
        step_size = (high_bound - low_bound) / Decimal(str(self.grid_subdivisions))
        for idx in range(self.grid_subdivisions + 1):
            val = low_bound + Decimal(str(idx)) * step_size
            if idx == self.grid_subdivisions:
                val = high_bound
            grid_points.append(val)

        grid_evaluations: list[tuple[Decimal, Decimal]] = []
        try:
            for pt in grid_points:
                out_val = eval_driver(pt)
                grid_evaluations.append((pt, out_val))
        except (ScenarioExecutionError, ZeroDivisionError, OverflowError, ValueError, InvalidOperation) as exc:
            # Discontinuity detected in driver evaluation space
            fact = ProtectedFact(
                fact_id=f"fact_gs_{turn_id}_discontinuity",
                metric=model.output_metric,
                calculation_type="GOAL_SEEK",
                value=None,
                unit="output",
                semantic_release="",
                is_suppressed=False,
                completeness_status="LIMITED_SCOPE",
                assumptions=[
                    f"Discontinuity or mathematical invalidity detected during grid evaluation of '{adjustable_driver}'.",
                    f"Solver failed at grid boundary: {str(exc)}",
                ],
                limitations=[
                    "Model equation contains a discontinuity, singularity, or domain error within bounds.",
                    "Root-finding solver cannot guarantee continuous convergence.",
                ],
                method="BRACKETED_SOLVER",
                method_version="1.1.0",
                result_type="SOLVER_SOLUTION",
                diagnostics={
                    "feasibility": "DISCONTINUITY_DETECTED",
                    "driver_name": adjustable_driver,
                    "target_value": str(target_value),
                    "error_detail": str(exc),
                },
            )
            return GoalSeekResult(
                facts=[fact],
                status="DISCONTINUITY_DETECTED",
                solved_driver_value=None,
                achieved_output=Decimal("0"),
                residual_error=abs(target_value),
                iterations=len(grid_evaluations),
            )

        f_low = grid_evaluations[0][1]
        f_high = grid_evaluations[-1][1]

        # 5. Check boundary solutions
        if abs(f_low - target_value) <= self.tolerance:
            fact = ProtectedFact(
                fact_id=f"fact_gs_{turn_id}_boundary_low",
                metric=model.output_metric,
                calculation_type="GOAL_SEEK",
                value=round(f_low, 2),
                unit="output",
                semantic_release="",
                is_suppressed=False,
                completeness_status="COMPLETE",
                assumptions=[
                    f"Boundary solution found at lower bound '{adjustable_driver}' = {low_bound}.",
                    f"Achieved output {f_low:,.2f} meets target {target_value:,.2f} within tolerance {self.tolerance}.",
                ],
                limitations=["Solution lies exactly on the lower parameter boundary."],
                method="BRACKETED_SOLVER",
                method_version="1.1.0",
                result_type="SOLVER_SOLUTION",
                diagnostics={
                    "feasibility": "BOUNDARY_SOLUTION",
                    "boundary": "LOWER",
                    "driver_name": adjustable_driver,
                    "solved_driver_value": str(round(low_bound, 4)),
                    "target_value": str(round(target_value, 2)),
                    "achieved_value": str(round(f_low, 2)),
                    "residual_error": str(round(abs(f_low - target_value), 4)),
                    "iterations": "1",
                },
            )
            return GoalSeekResult(
                facts=[fact],
                status="BOUNDARY_SOLUTION",
                solved_driver_value=round(low_bound, 2),
                achieved_output=round(f_low, 2),
                residual_error=round(abs(f_low - target_value), 4),
                iterations=1,
            )

        if abs(f_high - target_value) <= self.tolerance:
            fact = ProtectedFact(
                fact_id=f"fact_gs_{turn_id}_boundary_high",
                metric=model.output_metric,
                calculation_type="GOAL_SEEK",
                value=round(f_high, 2),
                unit="output",
                semantic_release="",
                is_suppressed=False,
                completeness_status="COMPLETE",
                assumptions=[
                    f"Boundary solution found at upper bound '{adjustable_driver}' = {high_bound}.",
                    f"Achieved output {f_high:,.2f} meets target {target_value:,.2f} within tolerance {self.tolerance}.",
                ],
                limitations=["Solution lies exactly on the upper parameter boundary."],
                method="BRACKETED_SOLVER",
                method_version="1.1.0",
                result_type="SOLVER_SOLUTION",
                diagnostics={
                    "feasibility": "BOUNDARY_SOLUTION",
                    "boundary": "UPPER",
                    "driver_name": adjustable_driver,
                    "solved_driver_value": str(round(high_bound, 4)),
                    "target_value": str(round(target_value, 2)),
                    "achieved_value": str(round(f_high, 2)),
                    "residual_error": str(round(abs(f_high - target_value), 4)),
                    "iterations": "1",
                },
            )
            return GoalSeekResult(
                facts=[fact],
                status="BOUNDARY_SOLUTION",
                solved_driver_value=round(high_bound, 2),
                achieved_output=round(f_high, 2),
                residual_error=round(abs(f_high - target_value), 4),
                iterations=1,
            )

        # 6. Locate all sign-change bracketing sub-intervals
        bracketed_intervals: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
        for i in range(len(grid_evaluations) - 1):
            x1, y1 = grid_evaluations[i]
            x2, y2 = grid_evaluations[i + 1]
            diff1 = y1 - target_value
            diff2 = y2 - target_value
            if (diff1 <= Decimal("0") and diff2 >= Decimal("0")) or (diff1 >= Decimal("0") and diff2 <= Decimal("0")):
                bracketed_intervals.append((x1, x2, y1, y2))

        has_multiple_roots = len(bracketed_intervals) > 1
        root_count = len(bracketed_intervals)

        # 7. If target is bracketed in one or more intervals, solve root in the primary interval
        if bracketed_intervals:
            # Use the first bracketed interval
            int_a, int_b, _, _ = bracketed_intervals[0]
            val_a = eval_driver(int_a)
            val_b = eval_driver(int_b)
            is_increasing = (val_b >= val_a)

            solved_val = int_a
            achieved_out = val_a
            iteration_count = 0

            for it in range(1, self.max_iterations + 1):
                iteration_count = it
                mid = (int_a + int_b) / Decimal("2")
                mid_out = eval_driver(mid)
                res = abs(mid_out - target_value)

                if res <= self.tolerance:
                    solved_val = mid
                    achieved_out = mid_out
                    break

                if is_increasing:
                    if mid_out < target_value:
                        int_a = mid
                    else:
                        int_b = mid
                else:
                    if mid_out < target_value:
                        int_b = mid
                    else:
                        int_a = mid

                solved_val = mid
                achieved_out = mid_out

            final_res = abs(achieved_out - target_value)
            status = "FEASIBLE" if final_res <= self.tolerance else "NON_CONVERGENT"

            assumptions_list = [
                f"Solved adjustable driver '{adjustable_driver}' = {solved_val:,.2f} {driver_def.unit} to hit target {target_value:,.2f}.",
                f"Achieved output {achieved_out:,.2f} with residual error {final_res:,.4f} in {iteration_count} iterations.",
            ]
            if has_multiple_roots:
                assumptions_list.append(
                    f"MULTIPLE ROOTS DETECTED: {root_count} distinct bracketed roots exist for target output. "
                    f"Primary root reported on [{bracketed_intervals[0][0]}, {bracketed_intervals[0][1]}]."
                )

            fact = ProtectedFact(
                fact_id=f"fact_gs_{turn_id}_solution",
                metric=model.output_metric,
                calculation_type="GOAL_SEEK",
                value=achieved_out,
                unit="output",
                semantic_release="",
                is_suppressed=False,
                completeness_status="COMPLETE" if status == "FEASIBLE" else "LIMITED_SCOPE",
                assumptions=assumptions_list,
                limitations=[
                    "Goal-seek assumes all other model drivers remain fixed at baseline levels.",
                    "Solution validity is constrained by model formula accuracy.",
                ],
                method="BRACKETED_SOLVER",
                method_version="1.1.0",
                result_type="SOLVER_SOLUTION",
                diagnostics={
                    "feasibility": status,
                    "driver_name": adjustable_driver,
                    "driver_solution": str(solved_val),
                    "solved_driver_value": str(solved_val),
                    "target_value": str(round(target_value, 2)),
                    "achieved_value": str(achieved_out),
                    "residual_error": str(round(final_res, 4)),
                    "iterations": str(iteration_count),
                    "has_multiple_roots": "true" if has_multiple_roots else "false",
                    "root_count": str(root_count),
                },
            )
            return GoalSeekResult(
                facts=[fact],
                status=status,
                solved_driver_value=solved_val,
                achieved_output=achieved_out,
                residual_error=round(final_res, 4),
                iterations=iteration_count,
                has_multiple_roots=has_multiple_roots,
                root_count=root_count,
            )

        # 8. Target is NOT bracketed across grid intervals:
        # Distinguish UNBRACKETED_TARGET vs UNBRACKETED_TARGET
        all_y = [y for _, y in grid_evaluations]
        min_y = min(all_y)
        max_y = max(all_y)

        if self._is_affine(model.formula_expression, adjustable_driver) and (target_value < min_y - self.tolerance or target_value > max_y + self.tolerance):
            # Proven unreachable under monotonic progression
            best_pt = low_bound if abs(f_low - target_value) < abs(f_high - target_value) else high_bound
            achieved = f_low if best_pt == low_bound else f_high
            residual = abs(achieved - target_value)

            fact = ProtectedFact(
                fact_id=f"fact_gs_{turn_id}_unreachable",
                metric=model.output_metric,
                calculation_type="GOAL_SEEK",
                value=achieved,
                unit="output",
                semantic_release="",
                is_suppressed=False,
                completeness_status="LIMITED_SCOPE",
                assumptions=[
                    f"Target of {target_value:,.2f} is PROVEN UNREACHABLE for the affine model within driver '{adjustable_driver}' bounds [{low_bound}, {high_bound}].",
                    f"Closest achievable boundary is {best_pt} yielding {achieved:,.2f} (residual shortfall: {residual:,.2f}).",
                ],
                limitations=[
                    "Target value exceeds feasibility constraints of approved monotonic catalog scenario model.",
                    "For an affine model, endpoint values bound the output over the permitted interval.",
                ],
                method="BRACKETED_SOLVER",
                method_version="1.1.0",
                result_type="SOLVER_SOLUTION",
                diagnostics={
                    "feasibility": "PROVEN_UNREACHABLE_TARGET",
                    "driver_name": adjustable_driver,
                    "target_value": str(target_value),
                    "achieved_value": str(achieved),
                    "residual_error": str(residual),
                    "iterations": "0",
                },
            )
            return GoalSeekResult(
                facts=[fact],
                status=GoalSeekStatus("PROVEN_UNREACHABLE_TARGET"),
                solved_driver_value=None,
                achieved_output=achieved,
                residual_error=residual,
                iterations=0,
            )

        # Otherwise, the function is non-monotonic or target was not bracketed in sampled sub-intervals:
        # Report UNBRACKETED_TARGET separately from proven unreachable.
        closest_pt, closest_val = min(grid_evaluations, key=lambda pair: abs(pair[1] - target_value))
        residual = abs(closest_val - target_value)

        fact = ProtectedFact(
            fact_id=f"fact_gs_{turn_id}_unbracketed",
            metric=model.output_metric,
            calculation_type="GOAL_SEEK",
            value=closest_val,
            unit="output",
            semantic_release="",
            is_suppressed=False,
            completeness_status="LIMITED_SCOPE",
            assumptions=[
                f"Target of {target_value:,.2f} is an UNBRACKETED TARGET on [{low_bound}, {high_bound}] across {self.grid_subdivisions} evaluated sub-intervals.",
                f"Closest sampled driver value was {closest_pt} yielding {closest_val:,.2f} (residual: {residual:,.2f}).",
                "Non-monotonic model behavior or extreme local variations prevent continuous bracketing.",
            ],
            limitations=[
                "Target was not bracketed within the evaluated sub-intervals.",
                "Unbracketed targets cannot be proven unreachable without finer global optimization.",
            ],
            method="BRACKETED_SOLVER",
            method_version="1.1.0",
            result_type="SOLVER_SOLUTION",
            diagnostics={
                "feasibility": "UNBRACKETED_TARGET",
                "driver_name": adjustable_driver,
                "target_value": str(target_value),
                "achieved_value": str(closest_val),
                "residual_error": str(residual),
                "iterations": str(len(grid_evaluations)),
            },
        )
        return GoalSeekResult(
            facts=[fact],
            status="UNBRACKETED_TARGET",
            solved_driver_value=None,
            achieved_output=closest_val,
            residual_error=residual,
            iterations=len(grid_evaluations),
        )


    @staticmethod
    def _is_affine(expression, driver):
        import ast
        def degree(node):
            if isinstance(node, ast.Constant): return 0
            if isinstance(node, ast.Name): return int(node.id == driver)
            if isinstance(node, ast.UnaryOp): return degree(node.operand)
            if isinstance(node, ast.BinOp):
                left, right = degree(node.left), degree(node.right)
                if isinstance(node.op, (ast.Add, ast.Sub)): return max(left, right)
                if isinstance(node.op, ast.Mult): return left + right
                if isinstance(node.op, ast.Div) and right == 0: return left
                if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and node.right.value in (0, 1):
                    return left * node.right.value
            return 99
        return degree(ast.parse(expression, mode="eval").body) <= 1

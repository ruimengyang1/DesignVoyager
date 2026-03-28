from __future__ import annotations

from typing import Dict, List, Tuple

from verification_metrics import (
    build_absolute_metric_dict,
    compute_delta_metrics,
    compute_overall_score,
    compute_relative_score,
    compute_trigger_rate,
)
from verification_schema import VerificationInput, VerificationOutput


ALLOWED_HOOKS = {
    "validate_move",
    "perform_move",
    "game_finished",
    "get_winner",
    "next_player",
}


class SelfVerifier:
    """
    Delta-Gated Verification for DesignVoyager.

    Goals:
    1. Block obviously broken mechanics from entering the library.
    2. Check whether the mechanic actually contributed something positive.
    3. Return structured metadata for the mechanism library and repair loop.
    """

    def __init__(self) -> None:
        # Cheap behavioral gates
        self.min_playability = 0.80
        self.max_balance_gap = 0.35
        self.min_decisiveness = 0.20
        self.min_agency = 0.40
        self.require_trigger = True

        # Retry policy
        self.max_retry_before_discard = 1

    def accept_threshold_for_stage(self, stage: int) -> float:
        """
        Relative-score threshold.
        Kept deliberately small because relative deltas are usually small.
        """
        if stage <= 1:
            return 0.03
        if stage == 2:
            return 0.02
        return 0.01

    def build_feedback(self, failure_modes: List[str]) -> str:
        templates = {
            "schema_failure": (
                "The mechanic output is missing required fields. Please return a complete mechanic with "
                "name, type, description, justification, code, and hook location."
            ),
            "syntax_failure": (
                "The mechanic code is not valid Python. Please fix syntax errors and return valid code."
            ),
            "hook_failure": (
                "The mechanic is attached to an invalid Boardwalk hook. Please place it in a valid location "
                "such as validate_move, perform_move, game_finished, or get_winner."
            ),
            "instantiation_failure": (
                "The modified Boardwalk game could not be instantiated. Please fix the integration logic "
                "or any missing fields/state."
            ),
            "dry_run_failure": (
                "The mechanic fails during a short dry run. Please fix state transitions or illegal effects "
                "caused immediately after integration."
            ),
            "low_playability": (
                "The mechanic causes too many invalid or non-terminating matches. Please revise it so games "
                "consistently reach valid terminal states."
            ),
            "extreme_imbalance": (
                "The mechanic creates too much player advantage. Please reduce asymmetry or add counterplay."
            ),
            "low_decisiveness": (
                "The mechanic appears to create too many draws or unresolved outcomes. Please make progress "
                "toward victory more decisive."
            ),
            "low_agency": (
                "The mechanic leaves players with too few meaningful choices. Please add branching decisions "
                "or reduce forced-move behavior."
            ),
            "no_trigger": (
                "The mechanic was not meaningfully triggered during playtesting. Please revise the trigger "
                "condition or integration point so it actually affects gameplay."
            ),
            "no_state_change": (
                "The mechanic triggered but did not materially change the game state. Please ensure it changes "
                "board state, legal moves, resources, or terminal conditions."
            ),
            "negative_relative_gain": (
                "The mechanic passes basic checks but does not improve the game relative to the parent version. "
                "Please revise it to increase strategic depth or improve gameplay quality."
            ),
        }

        unique_modes = []
        seen = set()
        for mode in failure_modes:
            if mode not in seen:
                seen.add(mode)
                unique_modes.append(mode)

        return " ".join(templates[m] for m in unique_modes if m in templates).strip()

    def check_integration_gate(self, verification_input: VerificationInput) -> Tuple[bool, List[str]]:
        failures: List[str] = []
        mech = verification_input.mechanic
        integration = verification_input.integration

        if not integration.schema_ok:
            failures.append("schema_failure")
        if not integration.syntax_ok:
            failures.append("syntax_failure")
        if mech.hook_location not in ALLOWED_HOOKS or not integration.hook_ok:
            failures.append("hook_failure")
        if not integration.instantiation_ok:
            failures.append("instantiation_failure")
        if not integration.dry_run_ok:
            failures.append("dry_run_failure")

        return len(failures) == 0, failures

    def check_behavioral_gate(self, verification_input: VerificationInput) -> Tuple[bool, List[str], Dict[str, float]]:
        child_abs = build_absolute_metric_dict(verification_input.child_metrics)
        trigger_rate = compute_trigger_rate(verification_input.trigger_stats, by="match")
        failures: List[str] = []

        if child_abs["playability"] < self.min_playability:
            failures.append("low_playability")

        if child_abs["balance_gap"] > self.max_balance_gap:
            failures.append("extreme_imbalance")

        if child_abs["decisiveness"] < self.min_decisiveness:
            failures.append("low_decisiveness")

        if child_abs["agency"] < self.min_agency:
            failures.append("low_agency")

        if self.require_trigger and trigger_rate <= 0:
            failures.append("no_trigger")

        if (
            verification_input.trigger_stats.trigger_count > 0
            and verification_input.trigger_stats.state_changed_by_mechanic_count <= 0
        ):
            failures.append("no_state_change")

        child_abs["trigger_rate"] = trigger_rate
        return len(failures) == 0, failures, child_abs

    def evaluate_relative_gain(self, verification_input: VerificationInput) -> Tuple[float, Dict[str, float], List[str]]:
        delta = compute_delta_metrics(
            verification_input.parent_metrics,
            verification_input.child_metrics,
        )
        relative_score = compute_relative_score(
            verification_input.parent_metrics,
            verification_input.child_metrics,
            novelty_score=0.0,
        )

        failures: List[str] = []
        if relative_score < self.accept_threshold_for_stage(verification_input.stage):
            failures.append("negative_relative_gain")

        return relative_score, {
            "delta_playability": delta.delta_playability,
            "delta_balance_gap": delta.delta_balance_gap,
            "delta_depth": delta.delta_depth,
            "delta_decisiveness": delta.delta_decisiveness,
            "delta_agency": delta.delta_agency,
            "delta_coverage": delta.delta_coverage,
        }, failures

    def decide(self, verification_input: VerificationInput) -> VerificationOutput:
        integration_pass, integration_failures = self.check_integration_gate(verification_input)

        if not integration_pass:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(integration_failures)
            return VerificationOutput(
                decision=decision,
                reason=integration_failures[0],
                stage=verification_input.stage,
                absolute_metrics={},
                delta_metrics={},
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                },
                overall_score=0.0,
                relative_score=0.0,
                failure_modes=integration_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": integration_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                },
            )

        behavior_pass, behavior_failures, child_abs = self.check_behavioral_gate(verification_input)
        overall_score = compute_overall_score(verification_input.child_metrics)

        if not behavior_pass:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(behavior_failures)

            return VerificationOutput(
                decision=decision,
                reason=behavior_failures[0],
                stage=verification_input.stage,
                absolute_metrics=child_abs,
                delta_metrics={},
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                },
                overall_score=overall_score,
                relative_score=0.0,
                failure_modes=behavior_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": behavior_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                },
            )

        relative_score, delta_dict, delta_failures = self.evaluate_relative_gain(verification_input)

        if delta_failures:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(delta_failures)

            return VerificationOutput(
                decision=decision,
                reason=delta_failures[0],
                stage=verification_input.stage,
                absolute_metrics=child_abs,
                delta_metrics=delta_dict,
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                },
                overall_score=overall_score,
                relative_score=relative_score,
                failure_modes=delta_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": delta_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                    "delta_metrics": delta_dict,
                },
            )

        return VerificationOutput(
            decision="accept",
            reason="positive_relative_gain",
            stage=verification_input.stage,
            absolute_metrics=child_abs,
            delta_metrics=delta_dict,
            trigger_stats={
                "trigger_count": verification_input.trigger_stats.trigger_count,
                "triggered_matches": verification_input.trigger_stats.triggered_matches,
                "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
            },
            overall_score=overall_score,
            relative_score=relative_score,
            failure_modes=[],
            repair_feedback=None,
            metadata_for_library={
                "accepted_stage": verification_input.stage,
                "failure_mode": None,
                "hook_location": verification_input.mechanic.hook_location,
                "delta_metrics": delta_dict,
                "absolute_metrics": child_abs,
                "parent_summary": verification_input.parent_summary,
            },
        )

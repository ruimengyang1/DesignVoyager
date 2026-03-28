from __future__ import annotations

from typing import Dict

from verification_schema import DeltaMetrics, PlaytestMetrics, TriggerStats


def compute_playability(metrics: PlaytestMetrics) -> float:
    if metrics.total_matches <= 0:
        return 0.0
    return metrics.completed_matches / metrics.total_matches


def compute_balance_gap(metrics: PlaytestMetrics) -> float:
    return abs(metrics.p1_win_rate - metrics.p2_win_rate)


def compute_balance_score(metrics: PlaytestMetrics) -> float:
    gap = compute_balance_gap(metrics)
    return max(0.0, min(1.0, 1.0 - gap))


def compute_depth(metrics: PlaytestMetrics) -> float:
    """
    Strategic-depth proxy from the proposal:
    stronger agent should outperform weaker agent if decisions matter.
    """
    raw = metrics.strong_agent_win_rate - metrics.weak_agent_win_rate
    return max(0.0, min(1.0, raw))


def compute_decisiveness(metrics: PlaytestMetrics) -> float:
    """
    Fraction of games that do NOT end in a draw.
    """
    return max(0.0, min(1.0, 1.0 - metrics.draw_rate))


def compute_agency(metrics: PlaytestMetrics) -> float:
    """
    Fraction of turns where the player had more than one legal move.
    This matches the simple GAVEL-inspired interpretation.
    """
    if metrics.total_turns <= 0:
        return 0.0
    return metrics.multi_choice_turns / metrics.total_turns


def compute_coverage(metrics: PlaytestMetrics) -> float:
    """
    Fraction of board cells that were used at least once.
    Optional diagnostic metric.
    """
    if metrics.board_cell_count <= 0:
        return 0.0
    return metrics.covered_cells / metrics.board_cell_count


def compute_trigger_rate(trigger_stats: TriggerStats, by: str = "match") -> float:
    if by == "turn":
        return trigger_stats.trigger_rate_by_turn()
    return trigger_stats.trigger_rate_by_match()


def build_absolute_metric_dict(metrics: PlaytestMetrics) -> Dict[str, float]:
    return {
        "playability": compute_playability(metrics),
        "balance_gap": compute_balance_gap(metrics),
        "balance_score": compute_balance_score(metrics),
        "depth": compute_depth(metrics),
        "decisiveness": compute_decisiveness(metrics),
        "agency": compute_agency(metrics),
        "coverage": compute_coverage(metrics),
        "avg_game_length": metrics.avg_game_length,
    }


def compute_delta_metrics(parent: PlaytestMetrics, child: PlaytestMetrics) -> DeltaMetrics:
    parent_abs = build_absolute_metric_dict(parent)
    child_abs = build_absolute_metric_dict(child)

    return DeltaMetrics(
        delta_playability=child_abs["playability"] - parent_abs["playability"],
        delta_balance_gap=child_abs["balance_gap"] - parent_abs["balance_gap"],
        delta_depth=child_abs["depth"] - parent_abs["depth"],
        delta_decisiveness=child_abs["decisiveness"] - parent_abs["decisiveness"],
        delta_agency=child_abs["agency"] - parent_abs["agency"],
        delta_coverage=child_abs["coverage"] - parent_abs["coverage"],
    )


def compute_overall_score(child: PlaytestMetrics) -> float:
    """
    Overall absolute quality of the child game.
    Hard constraints should be checked separately before relying on this score.
    """
    abs_metrics = build_absolute_metric_dict(child)

    return (
        0.45 * abs_metrics["playability"]
        + 0.25 * abs_metrics["balance_score"]
        + 0.20 * abs_metrics["depth"]
        + 0.10 * abs_metrics["decisiveness"]
    )


def compute_relative_score(parent: PlaytestMetrics, child: PlaytestMetrics, novelty_score: float = 0.0) -> float:
    """
    Measures whether the new mechanic actually improved the parent game,
    rather than only checking whether the child game looks decent in isolation.
    """
    d = compute_delta_metrics(parent, child)
    return (
        0.45 * d.delta_depth
        - 0.20 * d.delta_balance_gap
        + 0.15 * d.delta_playability
        + 0.10 * d.delta_agency
        + 0.10 * d.delta_decisiveness
        + 0.10 * novelty_score
    )

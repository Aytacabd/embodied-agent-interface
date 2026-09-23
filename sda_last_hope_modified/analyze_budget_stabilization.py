"""
analyze_budget_stabilization.py
=================================
Reconstructs Task Success Rate at every repair budget from 0 (initial plan
only) to 10, using
ONLY the single budget=10 run produced by run_hard50_once_budget10.sh — no
re-running at each budget needed.

The idea: the repair loop never knows how much budget remains when it
picks its next fix, so the sequence of repairs a task goes through up to
attempt N is identical regardless of whether the ceiling is N or 10. So
for each task we just need two things from the one real run:
  1. How many repairs it actually consumed before its FINAL state
     (0 if it succeeded clean on the first try, with no diagnosis at all).
  2. Whether that final state actually satisfied the goal (from the real
     evaluator's error_info.json — NOT just "did it execute without
     crashing," which is a different, looser signal).
A task counts as a success under budget B if (2) is true AND (1) <= B.

Usage (run locally, no docker needed):
    python3 analyze_budget_stabilization.py \\
        run_fixed2_results/hard50_budget10/run_hard50_r10.log \\
        run_fixed2_results/hard50_budget10/error_info_r10.json
(defaults to those exact paths if you run it with no arguments, since
that's what run_hard50_once_budget10.sh produces)
"""
import sys
import json
import csv
import re
from collections import Counter

import parse_diagnosis_stats as pds

DEFAULT_LOG = "run_fixed2_results/hard50_budget10/run_hard50_r10.log"
DEFAULT_ERROR_INFO = "run_fixed2_results/hard50_budget10/error_info_r10.json"
DEFAULT_MAX_BUDGET = 10
OUT_CSV = "run_fixed2_results/hard50_budget10/reconstructed_budget_curve.csv"


# The three LLM calls that consume repair budget (eai_sda_runner_tree.py
# increments replan_count immediately before each). Counting these, rather
# than diagnoses, is exact: a diagnosis can end in a free removal
# (already_satisfied, repeated wrong action) that costs no budget, and a
# wrong-action fix followed by a full-plan fallback costs two.
_REPAIR_CALL = re.compile(
    r"\[(SUGGESTION \(replan \d+\)|WRONG ACTION FIX|FULL PLAN FALLBACK)\] "
    r"(RESPONSE RECEIVED|\d)"
)
_TASK = re.compile(r"^\s*TASK:\s*(\S+)\s*\|")


def repairs_consumed_per_task(log_path):
    """Repair calls each task made, from the runner log. A task appearing
    more than once (resumed/retried) keeps its last occurrence."""
    consumed, task = {}, None
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _TASK.search(line)
            if m:
                task = m.group(1)
                consumed[task] = 0
            elif task and _REPAIR_CALL.search(line):
                consumed[task] += 1
    return consumed


def build_curve(log_path, error_info_path, max_budget=10, out_csv=None):
    """Reconstruct Task SR at every budget from 1..max_budget.

    Callable from the runner so a hard-suite run produces its curve without
    a second manual step; `out_csv` overrides where the CSV is written.
    """
    global OUT_CSV
    if out_csv:
        OUT_CSV = out_csv
    return _run(log_path, error_info_path, max_budget)


def main():
    log_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    error_info_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ERROR_INFO
    max_budget = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_MAX_BUDGET
    _run(log_path, error_info_path, max_budget)


def _run(log_path, error_info_path, max_budget):
    consumed = repairs_consumed_per_task(log_path)
    error_info = json.load(open(error_info_path))

    tasks = sorted(consumed)
    missing = [t for t in tasks if t not in error_info]
    if missing:
        print(f"WARNING: {len(missing)} task(s) in the log have no error_info "
              f"entry, excluding from the curve: {missing}")
    tasks = [t for t in tasks if t in error_info]

    goals_satisfied = {t: bool(error_info[t].get("goals_satisfied")) for t in tasks}
    n_total = len(tasks)
    if n_total == 0:
        # Nothing matched: usually the log has no "TASK:" / "ERROR
        # DIAGNOSIS:" lines because stdout was not captured, or error_info
        # belongs to a different run. Say so rather than dividing by zero.
        print("No tasks could be matched between the log and error_info.json.\n"
              "The log must contain the execution trace (the 'TASK:' and\n"
              "'ERROR DIAGNOSIS:' lines printed during the run), and the\n"
              "error_info.json must be the one scored from that same run.")
        return None
    n_ever_succeeds = sum(goals_satisfied.values())

    print(f"Tasks analyzed: {n_total}")
    print(f"Tasks that succeed at SOME budget <= {max_budget}: {n_ever_succeeds}")
    print(f"Tasks that never succeed even at budget={max_budget}: "
          f"{n_total - n_ever_succeeds} "
          f"{sorted(t for t in tasks if not goals_satisfied[t])}")
    print()

    # Budget 0 = the initial plan alone (attempt 1); budget b allows b
    # repairs, i.e. up to b+1 attempts at the plan.
    rows = []
    prev_sr = None
    print(f"{'repairs':>7}  {'attempts':>8}  {'successes':>9}  {'task_sr':>8}  {'gain':>9}")
    for b in range(0, max_budget + 1):
        successes = sum(
            1 for t in tasks if goals_satisfied[t] and consumed[t] <= b
        )
        sr = 100.0 * successes / n_total
        gain = None if prev_sr is None else round(sr - prev_sr, 4)
        print(f"{b:>7}  {b + 1:>8}  {successes:>9}  {sr:>7.1f}%  "
              f"{'' if gain is None else f'{gain:+.1f} pt':>9}")
        rows.append({
            "repairs_allowed": b, "attempts": b + 1, "successes": successes,
            "tasks": n_total, "task_sr": round(sr, 4),
            "gain_vs_previous_pt": "" if gain is None else gain,
        })
        prev_sr = sr

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved curve to {OUT_CSV}")

    # Flag the stabilization point: first budget after which SR never
    # improves again for the remainder of the tested range. Compare the
    # integer success counts (not the rounded percentage) so this can't
    # be thrown off by float rounding — successes is monotonic in budget
    # by construction, so this is an exact check.
    stable_from = max_budget
    for b in range(0, max_budget + 1):
        if all(r["successes"] == rows[b]["successes"] for r in rows[b:]):
            stable_from = b
            break
    print(f"\nSR stops improving after {stable_from} repair(s) "
          f"(flat at {rows[stable_from]['task_sr']:.1f}% through {max_budget}).")
    print(f"Caveat: this only tells you where it stabilized WITHIN the range you "
          f"tested — it can't rule out further gains past {max_budget} repairs.")
    return {"rows": rows, "stable_from": stable_from, "csv": OUT_CSV}


if __name__ == "__main__":
    main()

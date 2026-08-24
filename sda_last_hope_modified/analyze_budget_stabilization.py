"""
analyze_budget_stabilization.py
=================================
Reconstructs Task Success Rate at every repair budget from 1 to 10, using
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
from collections import Counter

import parse_diagnosis_stats as pds

DEFAULT_LOG = "run_fixed2_results/hard50_budget10/run_hard50_r10.log"
DEFAULT_ERROR_INFO = "run_fixed2_results/hard50_budget10/error_info_r10.json"
DEFAULT_MAX_BUDGET = 10
OUT_CSV = "run_fixed2_results/hard50_budget10/reconstructed_budget_curve.csv"


def repairs_consumed_per_task(log_path):
    diags, task_order = pds.parse_log(log_path)
    consumed = Counter()
    for d in diags:
        # already_satisfied is a free removal (no LLM repair call) — it
        # doesn't cost budget, same convention used for "avg replans used
        # per task" earlier in this project.
        if d["replan_strategy"] != "already_satisfied":
            consumed[d["task"]] += 1
    all_tasks = sorted(set(task_order))
    return {t: consumed.get(t, 0) for t in all_tasks}


def main():
    log_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    error_info_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ERROR_INFO
    max_budget = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_MAX_BUDGET

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
    n_ever_succeeds = sum(goals_satisfied.values())

    print(f"Tasks analyzed: {n_total}")
    print(f"Tasks that succeed at SOME budget <= {max_budget}: {n_ever_succeeds}")
    print(f"Tasks that never succeed even at budget={max_budget}: "
          f"{n_total - n_ever_succeeds} "
          f"{sorted(t for t in tasks if not goals_satisfied[t])}")
    print()

    rows = []
    prev_sr = None
    print(f"{'budget':>6}  {'successes':>9}  {'task_sr':>8}  {'gain_vs_prev':>13}")
    for b in range(1, max_budget + 1):
        successes = sum(
            1 for t in tasks if goals_satisfied[t] and consumed[t] <= b
        )
        sr = 100.0 * successes / n_total
        gain = "" if prev_sr is None else f"{sr - prev_sr:+.1f} pt"
        print(f"{b:>6}  {successes:>9}  {sr:>7.1f}%  {gain:>13}")
        rows.append({"budget": b, "successes": successes, "task_sr": round(sr, 4)})
        prev_sr = sr

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["budget", "successes", "task_sr"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved curve to {OUT_CSV}")

    # Flag the stabilization point: first budget after which SR never
    # improves again for the remainder of the tested range. Compare the
    # integer success counts (not the rounded percentage) so this can't
    # be thrown off by float rounding — successes is monotonic in budget
    # by construction, so this is an exact check.
    stable_from = max_budget
    for b in range(1, max_budget + 1):
        if all(rows[i]["successes"] == rows[b - 1]["successes"] for i in range(b - 1, max_budget)):
            stable_from = b
            break
    print(f"\nSR stops improving after budget={stable_from} "
          f"(flat at {rows[stable_from - 1]['task_sr']:.1f}% through budget={max_budget}).")
    print("Caveat: this only tells you where it stabilized WITHIN the range you "
          "tested — it can't rule out further gains past budget=10.")


if __name__ == "__main__":
    main()

"""
combine_bo_attempts.py
======================
Join evaluator results across best-of-k no-adaptation attempt sweeps into
per-task first-success attempts and pass@k.

Protocol ("resample instead of repair" baseline): eai_sda_runner_noadapt.py
is run k times over the SAME task set with ATTEMPT=1..k (temperature 1.0,
independent draws), every sweep is scored by evaluate_results.py (patched
to write per-task "goals_satisfied" into error_info.json), and THIS script
joins the k error_info.json files. Early stopping is applied analytically
here — first_success = first attempt whose goals all held — which for
independent draws is statistically identical to stopping the sampling
loop, but keeps the success judgment inside the official evaluator.

Usage:
    python3 combine_bo_attempts.py \
        --error_info results/<M>-noadapt_main_a1/error_info.json \
                     results/<M>-noadapt_main_a2/error_info.json \
                     results/<M>-noadapt_main_a3/error_info.json \
        --id2task src/virtualhome_eval/resources/virtualhome/id2task.json \
        --out bo3_main

Outputs:
    <out>_per_task.csv  one row per task: goal + executability result per
                        attempt, first_success attempt (blank = never)
    stdout              pass@1..k, first-success histogram, attempts that
                        the early-stop protocol would actually consume
"""

import argparse
import csv
import json
import sys


def load_attempt(path):
    info = json.load(open(path))
    out = {}
    for fid, rec in info.items():
        # "goals_satisfied" is written by the patched evaluator only on the
        # simulated branch; parse/hallucination/parameter records lack it —
        # those are failures by definition.
        out[fid] = {
            "success": bool(rec.get("goals_satisfied", False)),
            "executable": bool(rec.get("executable", False)),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--error_info", nargs="+", required=True,
                    help="error_info.json paths in attempt order (a1 a2 ... ak)")
    ap.add_argument("--id2task", default=None,
                    help="optional id2task.json for a task-name column")
    ap.add_argument("--out", default="bo_attempts",
                    help="output prefix for the per-task CSV")
    args = ap.parse_args()

    attempts = [load_attempt(p) for p in args.error_info]
    k = len(attempts)

    id2task = json.load(open(args.id2task)) if args.id2task else {}

    all_ids = sorted(set().union(*[set(a) for a in attempts]))
    if not all_ids:
        sys.exit("No tasks found in any error_info file.")
    for i, a in enumerate(attempts, 1):
        missing = len(all_ids) - len(a)
        if missing:
            print(f"WARNING: attempt {i} is missing {missing} task(s) present "
                  f"in other attempts — counted as failures for that attempt")

    rows = []
    first_hist = {n: 0 for n in range(1, k + 1)}
    never = 0
    for fid in all_ids:
        per = [a.get(fid, {"success": False, "executable": False})
               for a in attempts]
        first = next((i + 1 for i, r in enumerate(per) if r["success"]), None)
        if first is None:
            never += 1
        else:
            first_hist[first] += 1
        row = {"file_id": fid, "task_name": id2task.get(fid, "")}
        for i, r in enumerate(per, 1):
            row[f"a{i}_goal"] = "S" if r["success"] else "F"
            row[f"a{i}_exec"] = "Y" if r["executable"] else "N"
        row["first_success"] = first if first is not None else ""
        rows.append(row)

    n = len(all_ids)
    csv_path = f"{args.out}_per_task.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # LLM sweeps a user would actually spend if stopping on first success
    spent = sum((r["first_success"] or k) for r in rows)
    summary = {
        "tasks": n,
        "attempts_joined": k,
        "error_info_files": args.error_info,
        "first_success_histogram": {f"attempt_{j}": first_hist[j]
                                    for j in range(1, k + 1)},
        "never_succeeded": never,
        "pass_at_k": {},
        "early_stop_attempts_consumed": spent,
        "early_stop_mean_attempts_per_task": round(spent / n, 4),
    }
    print(f"\nTasks: {n} | attempts joined: {k}")
    cum = 0
    for j in range(1, k + 1):
        cum += first_hist[j]
        summary["pass_at_k"][f"pass@{j}"] = round(100.0 * cum / n, 4)
        print(f"pass@{j}: {cum}/{n} = {100.0 * cum / n:.1f}%"
              f"   (first success AT attempt {j}: {first_hist[j]})")
    print(f"never succeeded: {never}/{n} = {100.0 * never / n:.1f}%")
    print(f"attempts consumed under early-stop protocol: {spent} "
          f"(mean {spent / n:.2f}/task vs {k} without stopping)")

    summary_path = f"{args.out}_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=4)
    print(f"per-task table -> {csv_path}")
    print(f"summary        -> {summary_path}")


if __name__ == "__main__":
    main()

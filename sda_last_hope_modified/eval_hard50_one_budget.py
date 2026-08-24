"""
eval_hard50_one_budget.py
==========================
Stages and scores ONE hard-50 budget-sweep output (identified by its
MODEL_NAME tag, e.g. "gpt-4o-mini-sda-tree_hard50_r7"), then appends one
row to a running CSV of budget -> metrics.

Runs INSIDE the container (needs virtualhome_eval importable). Called once
per budget level, right after eai_sda_runner_hard.py finishes writing that
budget's outputs — see run_hard50_budget_sweep.sh, which drives both steps
in a loop so the stabilization curve builds up incrementally instead of
waiting for all 10 budgets to finish before scoring anything.

Usage (inside the container):
    python3 eval_hard50_one_budget.py <budget> <model_name_tag>
    python3 eval_hard50_one_budget.py 7 gpt-4o-mini-sda-tree_hard50_r7

Mirrors the verified hard-50 eval recipe (same resource_dir / dataset_dir
used for the budget=3 / budget=5 charguard runs earlier), just parameterized
per-budget and pointed at its own staging + results dirs so sweep runs never
collide with the existing r3/r5 baselines.
"""
import sys
import os
import os.path as osp
import shutil
import json

BASE = "/opt/iGibson"
RUNNER_OUTPUT_DIR = osp.join(BASE, "output_sda", "virtualhome", "action_sequencing_hard50")
STAGING_ROOT = osp.join(BASE, "eval_staging_budget_sweep")
RESULTS_ROOT = osp.join(BASE, "results_hard50_budget_sweep")
CSV_PATH = osp.join(RESULTS_ROOT, "budget_sweep_summary.csv")

CSV_HEADER = [
    "budget", "model_name", "task_success_rate", "state_goal",
    "relation_goal", "total_goal", "execution_success_rate",
]


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 eval_hard50_one_budget.py <budget> <model_name_tag>")
        sys.exit(1)
    budget, model_name = sys.argv[1], sys.argv[2]

    src = osp.join(RUNNER_OUTPUT_DIR, f"{model_name}_outputs.json")
    if not osp.exists(src):
        print(f"ERROR: {src} not found — did the runner finish for budget {budget}?")
        sys.exit(1)

    # Stage ONLY this budget's file, so evaluate_results doesn't re-score
    # every previously-staged budget on each call.
    stage_dir = osp.join(STAGING_ROOT, "virtualhome", "action_sequencing")
    os.makedirs(stage_dir, exist_ok=True)
    for f in os.listdir(stage_dir):
        os.remove(osp.join(stage_dir, f))
    shutil.copy(src, osp.join(stage_dir, f"{model_name}_outputs.json"))
    os.makedirs(RESULTS_ROOT, exist_ok=True)

    sys.path.insert(0, BASE)
    from virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results import (
        evaluate_results,
    )

    class Args:
        dataset = "virtualhome"
        llm_response_path = STAGING_ROOT
        resource_dir = osp.join(BASE, "difficult_tasks", "resources")
        dataset_dir = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
        output_dir = RESULTS_ROOT

    evaluate_results(Args())

    summary_path = osp.join(RESULTS_ROOT, model_name, "summary.json")
    summary = json.load(open(summary_path))
    g = summary["goal_evaluation"]
    t = summary["trajectory_evaluation"]

    row = [
        budget, model_name, g["task_success_rate"], g["state_goal"],
        g["relation_goal"], g["total_goal"], t["execution_success_rate"],
    ]

    write_header = not osp.exists(CSV_PATH)
    with open(CSV_PATH, "a") as f:
        if write_header:
            f.write(",".join(CSV_HEADER) + "\n")
        f.write(",".join(str(x) for x in row) + "\n")

    print(
        f"RESULT budget={budget:>2}  task_sr={g['task_success_rate']:6.1f}%  "
        f"exec_sr={t['execution_success_rate']:6.1f}%  "
        f"total_goal={g['total_goal']:6.1f}%"
    )


if __name__ == "__main__":
    main()

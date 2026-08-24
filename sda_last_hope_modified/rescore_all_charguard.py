"""
rescore_all_charguard.py
==========================
Re-scores every saved charguard-era output file with the FIXED evaluator
(the exact-gold-match undercounting bug patched in evaluate_results.py).
No LLM calls — everything here is already-generated plans, just re-graded.

Runs inside the container. Writes one row per target to a CSV as it goes,
so progress survives even if this takes a while (342-task everyday runs
take much longer to re-simulate than the 50-task hard runs).

Usage (inside the container):
    python3 rescore_all_charguard.py
"""
import os
import os.path as osp
import shutil
import json
import sys

BASE = "/opt/iGibson"
STAGING_ROOT = osp.join(BASE, "eval_staging_rescore_all")
RESULTS_ROOT = osp.join(BASE, "results_rescore_all_FIXED")
CSV_PATH = osp.join(RESULTS_ROOT, "rescore_all_summary.csv")

EVERYDAY_OUT_DIR = osp.join(BASE, "output_sda", "virtualhome", "action_sequencing")
HARD_OUT_DIR = osp.join(BASE, "output_sda", "virtualhome", "action_sequencing_hard50")
EVERYDAY_RESOURCE_DIR = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
HARD_RESOURCE_DIR = osp.join(BASE, "difficult_tasks", "resources")
DATASET_DIR = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"

TARGETS = [
    # (label, model_name, source_dir, resource_dir)
    ("everyday no-adapt a1",   "gpt-4o-mini-noadapt_main_a1",              EVERYDAY_OUT_DIR, EVERYDAY_RESOURCE_DIR),
    ("everyday no-adapt a2",   "gpt-4o-mini-noadapt_main_a2",              EVERYDAY_OUT_DIR, EVERYDAY_RESOURCE_DIR),
    ("everyday no-adapt a3",   "gpt-4o-mini-noadapt_main_a3",              EVERYDAY_OUT_DIR, EVERYDAY_RESOURCE_DIR),
    ("everyday with-repair",   "gpt-4o-mini-sda-tree-final-charguard-full", EVERYDAY_OUT_DIR, EVERYDAY_RESOURCE_DIR),
    ("hard no-adapt a1",       "gpt-4o-mini-noadapt_hard50_a1",             HARD_OUT_DIR,      HARD_RESOURCE_DIR),
    ("hard no-adapt a2",       "gpt-4o-mini-noadapt_hard50_a2",             HARD_OUT_DIR,      HARD_RESOURCE_DIR),
    ("hard no-adapt a3",       "gpt-4o-mini-noadapt_hard50_a3",             HARD_OUT_DIR,      HARD_RESOURCE_DIR),
    ("hard budget=3",          "gpt-4o-mini-sda-tree_hard50",               HARD_OUT_DIR,      HARD_RESOURCE_DIR),
    ("hard budget=5",          "gpt-4o-mini-sda-tree_hard50_r5",            HARD_OUT_DIR,      HARD_RESOURCE_DIR),
    ("hard budget=10",         "gpt-4o-mini-sda-tree_hard50_r10",           HARD_OUT_DIR,      HARD_RESOURCE_DIR),
]

CSV_HEADER = [
    "label", "model_name", "n_tasks", "task_success_rate", "state_goal",
    "relation_goal", "total_goal", "execution_success_rate",
]

sys.path.insert(0, BASE)
from virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results import (
    evaluate_results,
)


def rescore_one(label, model_name, source_dir, resource_dir):
    src = osp.join(source_dir, f"{model_name}_outputs.json")
    if not osp.exists(src):
        print(f"SKIP {label}: {src} not found")
        return

    stage_dir = osp.join(STAGING_ROOT, "virtualhome", "action_sequencing")
    os.makedirs(stage_dir, exist_ok=True)
    for f in os.listdir(stage_dir):
        os.remove(osp.join(stage_dir, f))
    shutil.copy(src, osp.join(stage_dir, f"{model_name}_outputs.json"))
    os.makedirs(RESULTS_ROOT, exist_ok=True)

    class Args:
        dataset = "virtualhome"
        llm_response_path = STAGING_ROOT
        resource_dir = resource_dir
        dataset_dir = DATASET_DIR
        output_dir = RESULTS_ROOT

    evaluate_results(Args())

    summary = json.load(open(osp.join(RESULTS_ROOT, model_name, "summary.json")))
    error_info = json.load(open(osp.join(RESULTS_ROOT, model_name, "error_info.json")))
    g = summary["goal_evaluation"]
    t = summary["trajectory_evaluation"]

    row = [
        label, model_name, len(error_info), g["task_success_rate"],
        g["state_goal"], g["relation_goal"], g["total_goal"],
        t["execution_success_rate"],
    ]
    write_header = not osp.exists(CSV_PATH)
    with open(CSV_PATH, "a") as f:
        if write_header:
            f.write(",".join(CSV_HEADER) + "\n")
        f.write(",".join(str(x) for x in row) + "\n")

    print(
        f"DONE  {label:22s} n={len(error_info):4d}  "
        f"task_sr={g['task_success_rate']:6.2f}%  "
        f"total_goal={g['total_goal']:6.2f}%  "
        f"exec_sr={t['execution_success_rate']:6.2f}%"
    )


if __name__ == "__main__":
    if osp.exists(CSV_PATH):
        os.remove(CSV_PATH)
    for label, model_name, source_dir, resource_dir in TARGETS:
        rescore_one(label, model_name, source_dir, resource_dir)
    print(f"\nAll done. Full table at {CSV_PATH}")

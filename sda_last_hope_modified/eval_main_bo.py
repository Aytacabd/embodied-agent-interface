"""
eval_main_bo.py
===============
One-command scoring of everything in the main-set output dir — including
the best-of-k no-adapt attempt sweeps (…-noadapt_main_a1/a2/a3) and any
SDA-arm outputs sitting next to them. The evaluator auto-discovers every
*_outputs.json under <llm_response_path>/virtualhome/action_sequencing and
scores each into <output_dir>/<tag>/{summary,error_info}.json; with the
patched evaluate_results, error_info carries per-task "goals_satisfied",
which combine_bo_attempts.py joins into first-success / pass@k.

Usage (inside the container; defaults are the container's main-set paths):
    python3 sda_eai/eval_main_bo.py
    python3 sda_eai/eval_main_bo.py --output_dir /opt/iGibson/results_main_bo3
Hard-50 instead: --llm_response_path /opt/iGibson/output_sda_hard50 \
                 --resource_dir /opt/iGibson/difficult_tasks/resources
"""

import argparse
import logging
import os.path as osp

import virtualhome_eval
from virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results import (
    evaluate_results,
)

PKG = osp.dirname(virtualhome_eval.__file__)

parser = argparse.ArgumentParser()
parser.add_argument("--llm_response_path", default="/opt/iGibson/output_sda",
                    help="dir the runners write to; evaluator appends "
                         "virtualhome/action_sequencing itself")
parser.add_argument("--resource_dir", default=osp.join(PKG, "resources"),
                    help="dir holding <dataset>/task_state_LTL_formula_accurate.json")
parser.add_argument("--dataset_dir", default=osp.join(PKG, "dataset"),
                    help="dir holding programs_processed_precond_nograb_morepreconds")
parser.add_argument("--output_dir", default="/opt/iGibson/results_sda_main_bo3")
parser.add_argument("--dataset", default="virtualhome")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
evaluate_results(args)

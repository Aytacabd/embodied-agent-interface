"""
eai_sda_runner_hard.py
=======================
Connector that runs the FULL SDA-Planner pipeline (eai_sda_runner_tree.py —
LLMClient, prompts, tree search, error diagnosis, subtree splicing) against
ONLY the 50 hand-authored "difficult" tasks (9001_1 .. 9050_1) produced by
difficult_tasks/generate_difficult_tasks.py.

It duplicates NONE of the SDA logic. It imports eai_sda_runner_tree as a
module and monkey-patches its config globals (TASK_DICT_PATH, ID2TASK_PATH,
MODEL_NAME, OUTPUT_DIR) before constructing EAISDATreeRunner, so:
  - self.task_dicts / self.id2task load ONLY the hard-task json files
    (task_dicts has exactly 50 entries -> run_all() naturally covers exactly
    those 50, no --task_ids filtering needed)
  - every log line, output filename and resume-checkpoint uses a distinct
    MODEL_NAME (…_hard50) and a distinct OUTPUT_DIR, so hard-task runs never
    read from or write into the main run's *_outputs.json.

Deployment (same docker cp pattern already used for sda_last_hope_modified):
  1. docker cp sda_last_hope_modified/. <container>:/opt/iGibson/sda_eai
     (this file rides along with the rest of the pipeline)
  2. docker cp difficult_tasks/resources/virtualhome/. \\
       <container>:/opt/iGibson/difficult_tasks/resources/virtualhome
     (sibling of sda_eai, matching the local repo layout — HARD_TASKS_DIR
     defaults to two directories up from this file, i.e. dirname(sda_eai).
     If your container layout differs, just set HARD_TASKS_DIR explicitly
     to wherever you copied task_state_LTL_formula_accurate.json + id2task.json)
  3. docker cp the generated dataset files into the installed dataset dir so
     construct_planner() can find them, e.g.:
       docker cp src/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds/init_and_final_graphs/TrimmedTestScene1_graph/results_intentions_march-13-18/file90*.json \\
         <container>:/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds/init_and_final_graphs/TrimmedTestScene1_graph/results_intentions_march-13-18/
       docker cp src/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds/executable_programs/TrimmedTestScene1_graph/results_intentions_march-13-18/file90*.txt \\
         <container>:/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds/executable_programs/TrimmedTestScene1_graph/results_intentions_march-13-18/

Usage (inside the container, same flags as the main runner):
    python3 sda_eai/eai_sda_runner_hard.py
    python3 sda_eai/eai_sda_runner_hard.py --max_tasks 10
    python3 sda_eai/eai_sda_runner_hard.py --task_ids 9001_1,9011_1,9031_1
"""

import os
import sys
import os.path as osp
import argparse

import eai_sda_runner_tree as core

# =============================================================================
# CONFIG OVERRIDES — everything else (LLM calls, prompts, tree search, error
# diagnosis, splicing) is untouched, imported straight from core.
# =============================================================================

HARD_TASKS_DIR = os.environ.get(
    "HARD_TASKS_DIR",
    osp.join(osp.dirname(osp.dirname(osp.abspath(__file__))),
             "difficult_tasks", "resources", "virtualhome"),
)

core.TASK_DICT_PATH = osp.join(HARD_TASKS_DIR, "task_state_LTL_formula_accurate.json")
core.ID2TASK_PATH = osp.join(HARD_TASKS_DIR, "id2task.json")
core.MODEL = os.environ.get("HARD_MODEL", "gpt-4o-mini")
core.MODEL_NAME = f"{core.MODEL}-sda-tree_hard50"
core.MAX_REPLAN = 5
core.OUTPUT_DIR = os.environ.get(
    "HARD_OUTPUT_DIR",
    osp.join(osp.dirname(core.OUTPUT_DIR), "action_sequencing_hard50"),
)


def _preflight():
    """Fail fast with a clear message instead of a deep FileNotFoundError."""
    missing = [p for p in (core.TASK_DICT_PATH, core.ID2TASK_PATH) if not osp.exists(p)]
    if missing:
        raise SystemExit(
            "Hard-task resource file(s) not found:\n  " + "\n  ".join(missing) +
            "\nSet HARD_TASKS_DIR to the directory holding "
            "task_state_LTL_formula_accurate.json + id2task.json "
            "(see deployment notes in this file's docstring)."
        )

    import json
    task_dicts = json.load(open(core.TASK_DICT_PATH))[f"scene_{core.SCENEGRAPH_ID}"]
    sample_id = next(iter(next(iter(task_dicts.values())).keys()))
    graph_path = osp.join(
        core.DATA_DIR, "init_and_final_graphs",
        f"TrimmedTestScene{core.SCENEGRAPH_ID}_graph",
        "results_intentions_march-13-18", f"file{sample_id}.json",
    )
    if not osp.exists(graph_path):
        raise SystemExit(
            f"Hard-task dataset files not found under DATA_DIR "
            f"(checked {graph_path}).\nCopy the generated graphs/scripts into "
            f"the installed dataset dir first (see deployment notes in this "
            f"file's docstring)."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_tasks", type=int, default=None,
                         help="Max number of hard tasks to run")
    parser.add_argument("--task_ids", type=str, default=None,
                         help="Comma-separated subset of hard task IDs, "
                              "e.g. 9001_1,9011_1,9031_1 (default: all 50)")
    args = parser.parse_args()

    if not core.API_KEY:
        print("ERROR: API key not set!")
        print("Run: export OPENAI_API_KEY='your_key'")
        sys.exit(1)

    _preflight()

    task_ids_set = set(args.task_ids.split(",")) if args.task_ids else None
    if task_ids_set:
        core.logger.info(f"Running only hard task IDs: {task_ids_set}")
    else:
        core.logger.info("Running all hard tasks (9001_1..9050_1)")

    core.EAISDATreeRunner().run_all(max_tasks=args.max_tasks, task_ids=task_ids_set)

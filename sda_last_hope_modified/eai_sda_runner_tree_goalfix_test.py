"""
eai_sda_runner_tree_goalfix_test.py
====================================
Targeted verification connector for the goal-completion fix in
eai_sda_runner_tree.py (_attempt_goal_completion + the scene_evaluate_wID
check wired into the replan loop's "if executable:" branch).

Writes to a TAG-ISOLATED output file (…-sda-tree-goalfix-test), not the
existing gpt-4o-mini-sda-tree-final_outputs.json from the completed
342-task run — reusing that tag would make run_all()'s resume logic treat
every requested task_id as already-done and skip it, running nothing.

Usage (inside the container):
    python3 sda_eai/eai_sda_runner_tree_goalfix_test.py --task_ids <comma-separated ids>

Task set for this verification pass (from the main-set audit):
  Fix targets (25) — replanned but still failed goal evaluation before the
  fix; these are exactly the tasks the goal-completion check should flip:
    180_1,181_2,183_2,190_1,211_1,27_2,287_2,323_2,327_2,352_1,383_1,416_1,
    442_2,474_2,509_2,563_1,624_2,650_2,696_1,721_2,764_2,803_2,850_1,954_2,99_2

  Regression sample (6) — already succeeded before the fix; must still
  succeed after, proving the new check doesn't break anything working:
    1004_2,102_1,1083_2,113_1,152_2,173_1  (replanned-and-succeeded)
    101_2,1027_2,1035_1,1049_1,1057_1,1064_1  (zero-replan-and-succeeded)
"""

import os
import sys
import argparse

import eai_sda_runner_tree as core

# GOALFIX_TAG lets a re-verification pass use a fresh output file — reusing
# the same tag after a code change would make run_all()'s resume logic see
# these task_ids as already-done (from the PRE-fix pass) and skip them,
# silently running nothing.
_suffix = os.environ.get("GOALFIX_TAG", "")
core.MODEL_NAME = f"{core.MODEL}-sda-tree-goalfix-test{_suffix}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_tasks", type=int, default=None)
    parser.add_argument("--task_ids", type=str, default=None,
                        help="Comma-separated task IDs e.g. 180_1,190_1,624_2")
    args = parser.parse_args()

    if not core.API_KEY:
        print("ERROR: API key not set!")
        sys.exit(1)

    task_ids_set = set(args.task_ids.split(",")) if args.task_ids else None
    core.logger.info(f"GOALFIX TEST — tag: {core.MODEL_NAME} | tasks: {task_ids_set or 'ALL'}")

    core.EAISDATreeRunner().run_all(max_tasks=args.max_tasks, task_ids=task_ids_set)

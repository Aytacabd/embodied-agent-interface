"""
eai_sda_runner_noadapt.py
==========================
ABLATION connector: the FULL EAI/VirtualHome action-sequencing set (the
same ~342 tasks eai_sda_runner_tree.py runs) WITHOUT the SDA feedback
machinery — the paper's "w/o adaptation" arm (Fig. 4): when an action
fails, it is SKIPPED and execution continues; no error diagnosis, no
search tree, no repair prompts — the LLM never hears about failures. Its
only call per task is the initial plan.

Main-set counterpart of eai_sda_runner_hard_noadapt.py. No task-set
override here (unlike the *_hard* connectors): TASK_DICT_PATH / ID2TASK_PATH
/ DATA_DIR stay at core's defaults, i.e. the installed package's full
resource and dataset directories — only the output tag differs from the
full-SDA arm.

Shared with the full-SDA arm (so the two arms differ ONLY in feedback):
  - SYSTEM_PROMPT + one_shot prompt + goal-string builder
  - parse_and_validate incl. goal-relation PUTBACK/PUTIN correction
  - the one corrective retry when the initial plan fails to parse
    (harness robustness, not feedback — both arms have it)

What gets SAVED: the subsequence of actions that actually executed
(skip-and-continue result), so the evaluator replays it identically and
post-failure goal achievements still count — the paper's definition, and
the choice most favorable to the baseline (makes the measured SDA delta
conservative).

Usage (inside the container, after docker cp of this directory):
    python3 sda_eai/eai_sda_runner_noadapt.py
    python3 sda_eai/eai_sda_runner_noadapt.py --max_tasks 20
    MAIN_MODEL=gpt-4o python3 sda_eai/eai_sda_runner_noadapt.py

Best-of-k resampling baseline (k independent full sweeps, T=1.0):
    ATTEMPT=1 python3 sda_eai/eai_sda_runner_noadapt.py
    ATTEMPT=2 python3 sda_eai/eai_sda_runner_noadapt.py
    ATTEMPT=3 python3 sda_eai/eai_sda_runner_noadapt.py
then evaluate (one pass scores all attempt tags) and join with
combine_bo_attempts.py for first-success-attempt / pass@k.

Output: <MODEL>-noadapt_main_outputs.json in the same
output_sda/virtualhome/action_sequencing dir the full-SDA arm writes to —
both land in the evaluator's default scan location for this dataset/
eval_type, so no staging copy is needed before evaluate_results.
"""

import os
import sys
import os.path as osp
import argparse

import eai_sda_runner_tree as core

# =============================================================================
# CONFIG OVERRIDE — only the tag. Task set / resources / dataset are core's
# defaults (the full EAI set), left untouched on purpose.
# =============================================================================

core.MODEL = os.environ.get("MAIN_MODEL", core.MODEL)
core.MODEL_NAME = f"{core.MODEL}-noadapt_main"

# ── Best-of-k resampling arm ─────────────────────────────────────────────
# ATTEMPT=n tags this sweep's output file (…-noadapt_main_a<n>) so k full
# independent sweeps coexist, get scored separately by evaluate_results,
# and are joined per task offline (combine_bo_attempts.py → first-success
# attempt, pass@k). Success is decided by the evaluator AFTER the run —
# deliberately no in-run success check — so every attempt sweeps the whole
# task set; "stop on first success" is applied analytically in the join,
# which for independent draws yields identical statistics.
# Resampling needs temperature > 0 or every attempt replays the same plan:
# MAIN_TEMPERATURE overrides core's T (core stays 0 — the SDA arm's
# tabu/repair memory relies on determinism); if ATTEMPT is set and
# MAIN_TEMPERATURE is not, default to 1.0.
_temp = os.environ.get("MAIN_TEMPERATURE")
_attempt = os.environ.get("ATTEMPT")
if _temp is not None:
    core.TEMPERATURE = float(_temp)
elif _attempt:
    core.TEMPERATURE = 1.0
if _attempt:
    core.MODEL_NAME = f"{core.MODEL}-noadapt_main_a{_attempt}"


class NoAdaptRunner(core.EAISDATreeRunner):
    """Same initial-plan generation as the SDA runner; execution is a single
    skip-and-continue pass with zero feedback to the LLM."""

    def run_single_task(self, file_id, task_name, task_goal_dict):
        goals = task_goal_dict["vh_goal"]
        node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
        edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]
        goal_edge_relations = {
            (g["from_id"], g["to_id"]): g["relation_type"] for g in edge_goals
        }

        try:
            motion_planner, _, _, _, _ = core.construct_planner(
                self.name_equivalence,
                self.properties_data,
                self.object_placing,
                scenegraph_id=core.SCENEGRAPH_ID,
                script_id=file_id,
                dataset_root=core.DATA_DIR,
            )
        except Exception as e:
            core.logger.error(f"Planner build failed: {e}")
            return "", 0, 0, 0

        object_in_scene, cur_change, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
            core.build_id_aware_goal_strings(
                motion_planner, node_goals, edge_goals, action_goals=goals["actions"],
            )
        )

        import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
        base_prompt = one_shot.prompt
        base_prompt = base_prompt.replace("<object_in_scene>", object_in_scene)
        base_prompt = base_prompt.replace("<cur_change>", cur_change)
        base_prompt = base_prompt.replace("<node_goals>", node_goal_str)
        base_prompt = base_prompt.replace("<edge_goals>", edge_goal_str)
        base_prompt = base_prompt.replace("<action_goals>", action_goal_str)

        if core.VERBOSE:
            print(f"\n{'='*60}", flush=True)
            print(f"TASK: {file_id}  |  {task_name}  [NO-ADAPTATION]", flush=True)
            print(f"{'='*60}", flush=True)

        raw_output = self.llm.call(base_prompt, label="INITIAL PLAN")
        core.logger.info(f"  Initial plan: {raw_output}")

        actions = core.parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations)
        if not actions:
            core.logger.warning(f"  Could not parse initial plan for {file_id} — retrying once")
            retry_prompt = base_prompt + (
                "\n\nIMPORTANT: your previous response was invalid or truncated."
                " Respond with ONE complete, syntactically valid JSON object and"
                " nothing else. If the plan is long, keep it complete anyway."
            )
            raw_output = self.llm.call(retry_prompt, label="INITIAL PLAN (retry)")
            actions = core.parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations)
        if not actions:
            core.logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        # ── Single pass: skip-and-continue, no feedback ───────────────────────
        motion_planner.reset()
        executed, skipped = [], []
        if core.VERBOSE:
            print(f"\n  {'─'*50}")
            print(f"  EXECUTING (no adaptation) — {len(actions)} actions")
            print(f"  {'─'*50}")
        for i, action in enumerate(actions):
            exe_flag, _ = motion_planner.my_execute_primitive_action_eval(action)
            if core.VERBOSE:
                print(f"  [{i+1:02d}] {action}  →  {'OK' if exe_flag else 'SKIPPED (failed)'}", flush=True)
            if exe_flag:
                executed.append(action)
            else:
                skipped.append(action)

        raw_output = core.plan_to_json_str(executed)
        core.logger.info(
            f"  no-adapt result: {len(executed)} executed, {len(skipped)} skipped"
            + (f" | skipped: {[str(a) for a in skipped]}" if skipped else "")
        )
        if core.VERBOSE:
            print(f"\n  FINAL OUTPUT SAVED ({len(executed)} executed / {len(skipped)} skipped)", flush=True)
        return raw_output, 0, 0, 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_tasks", type=int, default=None,
                        help="Max number of tasks to run")
    parser.add_argument("--task_ids", type=str, default=None,
                        help="Comma-separated subset of task IDs")
    args = parser.parse_args()

    if not core.API_KEY:
        print("ERROR: API key not set!")
        print("Run: export OPENAI_API_KEY='your_key'")
        sys.exit(1)

    core.logger.info("MODE: NO-ADAPTATION ABLATION (main set) — one LLM call "
                     "per task, failures skipped, no diagnosis/tree/repair")
    core.logger.info(f"Attempt    : {_attempt or '- (single run)'} | "
                     f"Temperature: {core.TEMPERATURE} | Tag: {core.MODEL_NAME}")
    task_ids_set = set(args.task_ids.split(",")) if args.task_ids else None
    NoAdaptRunner().run_all(max_tasks=args.max_tasks, task_ids=task_ids_set)

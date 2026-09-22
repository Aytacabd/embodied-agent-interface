"""
eai_sda_runner_hard_noadapt.py
==============================
ABLATION connector: the 50 hard tasks WITHOUT the SDA feedback machinery —
the "w/o adaptation" variant of the SDA-Planner paper's ablation (Fig. 4):
no error diagnosis, no search tree, no repair prompts — the LLM never hears
about failures. Its only call per task is the initial plan.

Shared with the full-SDA arm (so the two arms differ ONLY in feedback):
  - task set, resources, dataset paths (same overrides as eai_sda_runner_hard)
  - SYSTEM_PROMPT + one_shot prompt + goal-string builder
  - parse_and_validate incl. goal-relation PUTBACK/PUTIN correction
  - the one corrective retry when the initial plan fails to parse
    (harness robustness, not feedback — both arms have it)

What gets SAVED: the full predicted plan, exactly as the LLM produced it.
The runner executes it once for the log, but saves the prediction rather
than the executed subsequence, so the offline evaluator does the executing
and judging. This matches the full-SDA arm, whose non-success exits also
save the plan the planner ended with (restored to the original SDA
behaviour), so an Execution-SR difference between the arms reflects the
planners and not two different save rules.

(Earlier versions saved the skip-and-continue subsequence here. That gave
this arm two advantages the SDA arm did not have: its saved plan executed
cleanly by construction, and actions occurring after a failure still
counted toward goals.)

Usage (inside the container, after the usual docker cp of this directory):
    python3 sda_eai/eai_sda_runner_hard_noadapt.py
    python3 sda_eai/eai_sda_runner_hard_noadapt.py --max_tasks 5
    HARD_MODEL=gpt-4o python3 sda_eai/eai_sda_runner_hard_noadapt.py

Output: <MODEL>-noadapt_hard50_outputs.json in the same
action_sequencing_hard50 dir as the full-SDA outputs. Stage BOTH files into
the eval staging dir and one evaluate_results call scores both models.
"""

import os
import sys
import copy
import os.path as osp
import argparse

import eai_sda_runner_tree as core

# =============================================================================
# CONFIG OVERRIDES — identical to eai_sda_runner_hard.py except the tag.
# (Do not import eai_sda_runner_hard here: its import would re-patch
# MODEL_NAME/MAX_REPLAN for the full-SDA arm.)
# =============================================================================

HARD_TASKS_DIR = os.environ.get(
    "HARD_TASKS_DIR",
    osp.join(osp.dirname(osp.dirname(osp.abspath(__file__))),
             "difficult_tasks", "resources", "virtualhome"),
)

core.TASK_DICT_PATH = osp.join(HARD_TASKS_DIR, "task_state_LTL_formula_accurate.json")
core.ID2TASK_PATH = osp.join(HARD_TASKS_DIR, "id2task.json")
core.MODEL = core.model_from_argv(os.environ.get("HARD_MODEL", core.MODEL))
core.MODEL_EXPLICIT = core.MODEL_EXPLICIT or core.MODEL != core.DEFAULT_MODEL
core.MODEL_NAME = f"{core.MODEL}-noadapt_hard50"
core.SUITE = "hard50"
core.ARM = "noadapt"
core.OUTPUT_DIR = os.environ.get(
    "HARD_OUTPUT_DIR",
    osp.join(osp.dirname(core.OUTPUT_DIR), "action_sequencing_hard50"),
)

# ── Best-of-k resampling arm ─────────────────────────────────────────────
# Same protocol as eai_sda_runner_noadapt.py's main-set version: ATTEMPT=n
# tags this sweep's output (…-noadapt_hard50_a<n>) so k independent sweeps
# coexist and get joined offline (combine_bo_attempts.py). Resampling needs
# temperature > 0 or every attempt replays the same plan; HARD_TEMPERATURE
# overrides core's T (core stays 0 for the SDA arm's determinism) — if
# ATTEMPT is set and HARD_TEMPERATURE is not, default to 1.0.
_temp = os.environ.get("HARD_TEMPERATURE")
_attempt = os.environ.get("ATTEMPT")
if _temp is not None:
    core.TEMPERATURE = float(_temp)
elif _attempt:
    core.TEMPERATURE = 1.0
if _attempt:
    core.MODEL_NAME = f"{core.MODEL}-noadapt_hard50_a{_attempt}"
    core.RUN_VARIANT = f"a{_attempt}"


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

        actions = core.parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                          char_guard="reject")
        if not actions:
            core.logger.warning(f"  Could not parse initial plan for {file_id} — retrying once")
            retry_prompt = core._build_retry_prompt(base_prompt, raw_output)
            raw_output = self.llm.call(retry_prompt, label="INITIAL PLAN (retry)")
            actions = core.parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                              char_guard="strip")
        if not actions:
            core.logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        # ── Single pass, no feedback ──────────────────────────────────────────
        # The execution below is DIAGNOSTIC ONLY: it reports which actions would
        # run, for the log. It does not change what is saved. The saved plan is
        # the full prediction, matching the SDA arm's non-success exits and the
        # plain benchmark convention — the planner emits a plan, the offline
        # evaluator executes and judges it.
        motion_planner.reset()
        executed, skipped = [], []
        if core.VERBOSE:
            print(f"\n  {'─'*50}")
            print(f"  EXECUTING (no adaptation) — {len(actions)} actions")
            print(f"  {'─'*50}")
        for i, action in enumerate(actions):
            exe_flag, _ = motion_planner.my_execute_primitive_action_eval(action)
            if core.VERBOSE:
                print(f"  [{i+1:02d}] {action}  →  {'OK' if exe_flag else 'FAILED'}", flush=True)
            if exe_flag:
                executed.append(action)
            else:
                skipped.append(action)

        raw_output = core.plan_to_json_str(actions)
        core.logger.info(
            f"  no-adapt result: saved full predicted plan ({len(actions)} actions); "
            f"{len(executed)} of them ran, {len(skipped)} failed"
            + (f" | failed: {[str(a) for a in skipped]}" if skipped else "")
        )
        if core.VERBOSE:
            print(f"\n  FINAL OUTPUT SAVED (full plan, {len(actions)} actions)", flush=True)
        return raw_output, 0, 0, 0


def _preflight():
    missing = [p for p in (core.TASK_DICT_PATH, core.ID2TASK_PATH) if not osp.exists(p)]
    if missing:
        raise SystemExit(
            "Hard-task resource file(s) not found:\n  " + "\n  ".join(missing) +
            "\nSet HARD_TASKS_DIR to the directory holding "
            "task_state_LTL_formula_accurate.json + id2task.json."
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
            f"Hard-task dataset files not found under DATA_DIR (checked {graph_path})."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None,
                        help="Model id to plan with, e.g. gpt-4o-mini. "
                             "Applied at import; always set it for a real run.")
    parser.add_argument("--max_tasks", type=int, default=None,
                        help="Max number of hard tasks to run")
    parser.add_argument("--task_ids", type=str, default=None,
                        help="Comma-separated subset, e.g. 9001_1,9011_1")
    parser.add_argument("--fresh", action="store_true",
                        help="Start over: move any existing plans for this tag "
                             "aside first, instead of resuming them. Use after "
                             "changing model/prompt/budget, or when the existing "
                             "plans were produced by older code.")
    args = parser.parse_args()

    core.start_logging()


    if not core.API_KEY:
        print("ERROR: no API key found in the environment.")
        print("  OpenAI  : export OPENAI_API_KEY='sk-...'")
        print("  Langdock: export LANGDOCK_API_KEY='...'")
        sys.exit(1)

    # --fresh: retire the current plans instead of resuming them. Renaming
    # rather than deleting means a mistaken --fresh costs nothing.
    if args.fresh:
        _stale = osp.join(core.OUTPUT_DIR, f"{core.MODEL_NAME}_outputs.json")
        if osp.exists(_stale):
            _retired = f"{_stale}.superseded_{core.RUN_TIMESTAMP}"
            os.replace(_stale, _retired)
            core.logger.info(f"--fresh: previous plans moved aside -> {_retired}")
        else:
            core.logger.info("--fresh: nothing to clear, starting from empty")

    _preflight()

    core.logger.info("MODE: NO-ADAPTATION ABLATION — one LLM call per task, "
                     "failures skipped, no diagnosis/tree/repair")
    core.logger.info(f"Attempt    : {_attempt or '- (single run)'} | "
                     f"Temperature: {core.TEMPERATURE} | Tag: {core.MODEL_NAME}")
    task_ids_set = set(args.task_ids.split(",")) if args.task_ids else None
    completed = NoAdaptRunner().run_all(max_tasks=args.max_tasks, task_ids=task_ids_set)

    if core.AUTO_EVALUATE:
        core.run_evaluation()
    sys.exit(0 if completed else 2)

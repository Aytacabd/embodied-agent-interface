"""
evaluate_results_sda.py
=======================
Drop-in replacement for EAI's:
    virtualhome_eval/evaluation/action_sequencing/scripts/evaluate_results.py

All original metrics (execution_success_rate, grammar_error, runtime_error,
goal_evaluation) are preserved.  One new metric is added:

    summary["trajectory_evaluation"]["num_error_corrections"]
        Average number of SDA-Planner replan attempts per task.
        Equivalent to "No. EC" in Table 1 of the SDA-Planner paper.

Integration
-----------
Option A — call this file directly:
    from evaluate_results_sda import evaluate_results

Option B — patch agent_eval.py by replacing the import:
    # from virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results \
    #     import evaluate_results as action_output_evaluation
    from evaluate_results_sda import evaluate_results as action_output_evaluation

Configuration
-------------
Set environment variables before running:
    OPENAI_API_KEY      your OpenAI key (required for replanning)
    SDA_PDDL_PATH       path to virtualhome.pddl
                        (default: auto-detected from EAI resource dir)
    SDA_MAX_REPLAN      max replan attempts per task  (default: 3)
    SDA_BFS_DEPTH       BFS depth in subtree generation (default: 8)
    SDA_VERBOSE         set to "1" for debug logging
"""

from __future__ import annotations

import copy
import json
import logging
import os
import os.path as osp
import sys

# EAI imports (unchanged)
import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    construct_planner,
    extract_model_names,
    load_json_preserving_order,
    check_name_id_format,
    check_no_hallucination_in_action,
    check_no_hallucination_in_arg,
    check_action_grammar,
    json_to_action,
    remove_duplicate_dicts,
    scene_evaluate_wID,
)
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# SDA-Planner — add the sda_planner directory to path if running standalone
_THIS_DIR = osp.dirname(osp.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from sda_planner import SDAPlanner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

def _get_pddl_path(resource_dir: str, dataset: str) -> str:
    env_val = os.environ.get("SDA_PDDL_PATH", "")
    if env_val and osp.isfile(env_val):
        return env_val
    # Auto-detect: EAI keeps the PDDL in resources/virtualhome/
    candidate = osp.join(resource_dir, dataset, "virtualhome.pddl")
    if osp.isfile(candidate):
        return candidate
    # Fallback: check alongside this file
    local = osp.join(_THIS_DIR, "virtualhome.pddl")
    if osp.isfile(local):
        return local
    raise FileNotFoundError(
        f"Cannot find virtualhome.pddl. "
        f"Set the SDA_PDDL_PATH environment variable."
    )


# ---------------------------------------------------------------------------
# Main evaluation function  (identical signature to EAI original)
# ---------------------------------------------------------------------------

def evaluate_results(args) -> dict:
    dataset         = args.dataset
    llm_response_path = args.llm_response_path
    resource_root   = osp.join(args.resource_dir, dataset)
    data_dir        = osp.join(
        args.dataset_dir, "programs_processed_precond_nograb_morepreconds"
    )
    output_dir      = args.output_dir
    if not osp.exists(output_dir):
        os.makedirs(output_dir)

    task_dict_dir   = osp.join(resource_root, "task_state_LTL_formula_accurate.json")
    id_to_task_path = osp.join(resource_root, "id2task.json")

    task_dicts = json.load(open(task_dict_dir, "r"))
    id2task    = json.load(open(id_to_task_path, "r"))

    properties_data  = utils.load_properties_data()
    object_placing   = utils.load_object_placing()
    name_equivalence = utils.load_name_equivalence()

    scenegraph_id = 1
    scene_id      = f"scene_{scenegraph_id}"
    task_dicts    = task_dicts[scene_id]

    error_code_to_type = {
        0: "WRONG_TEMPORAL_ORDER",
        1: "MISSING_STEP",
        2: "AFFORDANCE_ERROR",
        3: "UNSEEN_OBJECT",
        4: "ADDITIONAL_STEP",
        5: "UNKNOWN_ERROR",
    }

    # Build SDA-Planner (SDG parsed once, reused across all tasks)
    pddl_path   = _get_pddl_path(args.resource_dir, dataset)
    max_replan  = int(os.environ.get("SDA_MAX_REPLAN", "3"))
    bfs_depth   = int(os.environ.get("SDA_BFS_DEPTH", "8"))
    verbose     = os.environ.get("SDA_VERBOSE", "0") == "1"
    sda         = SDAPlanner(
        pddl_path=pddl_path,
        max_replan_attempts=max_replan,
        max_bfs_depth=bfs_depth,
        verbose=verbose,
    )
    logger.info(f"SDA-Planner ready | pddl={pddl_path} | max_replan={max_replan}")

    llm_response_path = osp.join(llm_response_path, dataset, "action_sequencing")
    model_file        = extract_model_names(llm_response_path)
    all_results       = {}

    for model_name in model_file:
        # ---- Per-model counters (same as EAI baseline) ----
        error_code_to_number = {0: 0, 1: 0, 2: 0, 4: 0}

        logger.info(f"Model: {model_name}")
        llm_response_json = osp.join(
            llm_response_path, f"{model_name}_outputs.json"
        )
        llm_response = json.load(open(llm_response_json, "r"))

        program_num          = 0
        all_parsing_wrong    = 0
        all_hallucination    = 0
        all_parameter_wrong  = 0
        all_executable_plan  = 0
        all_correct_plan     = 0

        all_matched_node   = 0
        all_matched_edge   = 0
        all_matched_action = 0
        all_matched_all    = 0

        all_node_goals   = 0
        all_edge_goals   = 0
        all_action_goals = 0
        all_goals        = 0

        total_error_corrections = 0   # NEW: sum of No.EC across tasks

        error_info: dict = {}

        for output_dict in llm_response:
            file_id = output_dict["identifier"]
            task    = id2task[file_id]
            logger.info(f"Task={task}, id={file_id}")
            program_num += 1

            program_dict     = task_dicts[task][file_id]
            goals            = program_dict["vh_goal"]
            gold_action_goals = goals["actions"]
            scene_goals      = goals["goal"]
            gold_node_goals  = []
            gold_edge_goals  = []
            for sg in scene_goals:
                if "id" in sg and "class_name" in sg and "state" in sg:
                    gold_node_goals.append(sg)
                elif "from_id" in sg and "to_id" in sg and "relation_type" in sg:
                    gold_edge_goals.append(sg)
                else:
                    raise ValueError("Scene goal not in correct format")

            gold_node_goals   = remove_duplicate_dicts(gold_node_goals)
            gold_edge_goals   = remove_duplicate_dicts(gold_edge_goals)
            gold_action_goals = list(set(gold_action_goals))

            motion_planner, relevant_id, gd_actions, task_name, _ = construct_planner(
                name_equivalence, properties_data, object_placing,
                scenegraph_id=scenegraph_id,
                script_id=file_id,
                dataset_root=data_dir,
            )
            _, _, _, _, _, relevant_name_to_id = motion_planner.get_symbolic_goal_nl(
                gold_node_goals, gold_edge_goals, action_goals=gold_action_goals
            )
            _, _, _, all_success, _, _, _ = scene_evaluate_wID(
                motion_planner.final_state_dict,
                gold_node_goals, gold_edge_goals,
                motion_planner.acting_char_id,
            )
            if not all_success:
                program_num -= 1
                logger.info(f"Program {file_id} did not pass gold test — skipped")
                continue

            all_node_goals   += len(gold_node_goals)
            all_edge_goals   += len(gold_edge_goals)
            all_action_goals += len(gold_action_goals)
            all_goals        += (
                len(gold_node_goals) + len(gold_edge_goals) + len(gold_action_goals)
            )

            # ---- Parse and validate LLM output (identical to baseline) ----
            format_error      = False
            hallucination_error = False
            parameter_error   = False
            actions           = output_dict["llm_output"]

            if actions.startswith("```json"):
                actions = actions[7:]
            actions = actions.strip().replace("\n", "").replace("'", '"')

            try:
                actions = load_json_preserving_order(actions)
            except Exception:
                all_parsing_wrong += 1
                format_error = True
                actions = None

            if actions is None or len(actions) == 0 or \
                    not check_name_id_format(actions)[0]:
                all_parsing_wrong += 1
                format_error = True

            if not format_error:
                if check_no_hallucination_in_action(actions) and \
                        check_no_hallucination_in_arg(actions, relevant_name_to_id):
                    hallucination_error = False
                else:
                    all_hallucination += 1
                    hallucination_error = True

            if not format_error and not hallucination_error:
                pass_check, _ = check_action_grammar(actions)
                if pass_check:
                    actions = json_to_action(
                        actions, relevant_name_to_id=relevant_name_to_id
                    )
                else:
                    all_parameter_wrong += 1
                    parameter_error = True

            # ---- Execute with SDA-Planner --------------------------------
            if not format_error and not hallucination_error and not parameter_error:
                if actions == gd_actions:
                    all_executable_plan += 1
                    error_info[file_id] = {
                        "executable": True,
                        "actions": actions,
                        "error_type": None,
                        "error_action": None,
                        "num_error_corrections": 0,
                    }
                    history_actions = actions
                else:
                    motion_planner.reset()

                    # -----------------------------------------------
                    # SDA-Planner replaces the EAI execution for-loop
                    # -----------------------------------------------
                    executable, history_actions, error_info_entry = sda.run(
                        actions=actions,
                        motion_planner=motion_planner,
                        instruction=task_name,
                        checker_class=TemporalOrderChecker,
                    )
                    total_error_corrections += sda.num_error_corrections

                    # Map last error code for per-type counters
                    if not executable and error_info_entry.get("error_action"):
                        # Try to get a numeric code by running checker on the
                        # last failed action  (best-effort; uses sda internal log)
                        last_log = sda.get_replan_log()
                        if last_log:
                            last_code = last_log[-1].get("error_code", 1)
                            if last_code in error_code_to_number:
                                error_code_to_number[last_code] += 1

                    if executable:
                        all_executable_plan += 1

                    error_info[file_id] = error_info_entry

                # Goal evaluation (same as baseline)
                (
                    node_match_num,
                    edge_match_num,
                    action_match_num,
                    all_pred_success,
                    _, _, _,
                ) = scene_evaluate_wID(
                    motion_planner.env_state.to_dict(),
                    gold_node_goals, gold_edge_goals,
                    motion_planner.acting_char_id,
                    action_seq=history_actions,
                    action_goals=gold_action_goals,
                )
                all_matched_node   += node_match_num
                all_matched_edge   += edge_match_num
                all_matched_action += action_match_num
                all_matched_all    += (
                    node_match_num + edge_match_num + action_match_num
                )
                if all_pred_success:
                    all_correct_plan += 1
                    logger.info("Task SUCCEEDED")

            else:
                # Grammar / hallucination / parameter error — no execution
                etype = (
                    "parsing error"       if format_error       else
                    "hallucination error" if hallucination_error else
                    "parameter error"
                )
                error_info[file_id] = {
                    "executable": False,
                    "actions": actions,
                    "error_type": etype,
                    "error_action": None,
                    "num_error_corrections": 0,
                }

        # ---- Aggregate metrics ----------------------------------------
        avg_ec = (total_error_corrections / program_num) if program_num else 0.0

        summary = {
            "goal_evaluation": {
                "task_success_rate": round(100.0 * all_correct_plan  / program_num, 4),
                "state_goal":        round(100.0 * all_matched_node  / max(all_node_goals, 1), 4),
                "relation_goal":     round(100.0 * all_matched_edge  / max(all_edge_goals, 1), 4),
                "action_goal":       round(100.0 * all_matched_action/ max(all_action_goals, 1), 4),
                "total_goal":        round(100.0 * all_matched_all   / max(all_goals, 1), 4),
            },
            "trajectory_evaluation": {
                "execution_success_rate": round(
                    100.0 * all_executable_plan / program_num, 1
                ),
                "num_error_corrections": round(avg_ec, 4),   # NEW (No. EC)
                "grammar_error": {
                    "parsing":                   round(100.0 * all_parsing_wrong   / program_num, 4),
                    "hallucination":             round(100.0 * all_hallucination   / program_num, 4),
                    "predicate_argument_number": round(100.0 * all_parameter_wrong / program_num, 4),
                },
                "runtime_error": {
                    "wrong_order":      round(100.0 * error_code_to_number[0] / program_num, 4),
                    "missing_step":     round(100.0 * error_code_to_number[1] / program_num, 4),
                    "affordance_error": round(100.0 * error_code_to_number[2] / program_num, 4),
                    "additional_step":  round(100.0 * error_code_to_number[4] / program_num, 4),
                },
            },
        }

        all_results[model_name] = [summary, error_info]

        save_path = osp.join(output_dir, model_name)
        os.makedirs(save_path, exist_ok=True)
        with open(osp.join(save_path, "summary.json"), "w") as f:
            json.dump(summary, f, indent=4)
        with open(osp.join(save_path, "error_info.json"), "w") as f:
            json.dump(error_info, f, indent=4)
        logger.info(f"Results saved to {save_path}")

        # Log comparison-ready summary
        logger.info(
            f"\n{'='*55}\n"
            f"SDA-Planner Results — {model_name}\n"
            f"  Execution success : {summary['trajectory_evaluation']['execution_success_rate']:.1f}%"
            f"  (baseline GPT-4o: 71.1%)\n"
            f"  Task success      : {summary['goal_evaluation']['task_success_rate']:.4f}%"
            f"  (baseline: 63.9344%)\n"
            f"  Avg No.EC         : {avg_ec:.4f}"
            f"{'='*55}"
        )

    return all_results

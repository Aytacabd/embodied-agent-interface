"""
evaluate_results_sda.py
-----------------------
Drop-in replacement for the EAI evaluate_results.py that plugs SDA-Planner
into the action_sequencing execution loop.

Based on the original evaluate_results.py — only the inner execution loop
is extended with SDA-Planner adaptive replanning.
"""

import json
import copy
import os
import os.path as osp

import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import check_name_id_format, json_to_action, check_action_grammar, check_no_hallucination_in_action, check_no_hallucination_in_arg, extract_model_names, load_json_preserving_order, remove_duplicate_dicts, scene_evaluate_wID, construct_planner
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# SDA-Planner
from sda_planner.state_dependency_graph import build_sdg
from sda_planner.error_diagnosis import ErrorDiagnoser, ErrorType
from sda_planner.adaptive_planner import AdaptiveSubTreeGenerator, parse_action

import logging
logger = logging.getLogger(__name__)

PDDL_PATH = "/opt/iGibson/examples/virtualhome.pddl"
MAX_REPLAN_ATTEMPTS = 5


def evaluate_results(args):
    dataset = args.dataset
    llm_response_path = args.llm_response_path

    resource_root = osp.join(args.resource_dir, dataset)
    data_dir = osp.join(
        args.dataset_dir, "programs_processed_precond_nograb_morepreconds"
    )

    output_dir = args.output_dir
    if not osp.exists(output_dir):
        os.makedirs(output_dir)

    task_dict_dir = osp.join(resource_root, "task_state_LTL_formula_accurate.json")
    id_to_task_path = os.path.join(resource_root, "id2task.json")

    task_dicts = json.load(open(task_dict_dir, "r"))
    id2task = json.load(open(id_to_task_path, "r"))

    properties_data = utils.load_properties_data()
    object_placing = utils.load_object_placing()
    name_equivalence = utils.load_name_equivalence()

    scenegraph_id = 1
    scene_id = f"scene_{scenegraph_id}"
    task_dicts = task_dicts[scene_id]

    error_code_to_type = {
        0: "WRONG_TEMPORAL_ORDER",
        1: "MISSING_STEP",
        2: "AFFORDANCE_ERROR",
        3: "UNSEEN_OBJECT",
        4: "ADDITIONAL_STEP",
        5: "UNKNOWN_ERROR",
    }

    llm_response_path = osp.join(llm_response_path, dataset, "action_sequencing")
    logger.info(f"load llm response from {llm_response_path}")
    model_file = extract_model_names(llm_response_path)
    all_results = {}

    # ---- Initialise SDA-Planner components (once per run) ----
    logger.info(f"[SDA] Loading SDG from {PDDL_PATH}")
    sdg = build_sdg(PDDL_PATH)
    diagnoser = ErrorDiagnoser(sdg)
    subtree_gen = AdaptiveSubTreeGenerator(sdg)
    logger.info("[SDA] SDG ready.")

    for model_name in model_file:
        error_code_to_number = {0: 0, 1: 0, 2: 0, 4: 0}
        logger.info(f"Model name is {model_name}")
        llm_response_json = os.path.join(
            llm_response_path, f"{model_name}_outputs.json"
        )
        llm_response = json.load(open(llm_response_json, "r"))

        program_num = 0
        all_parsing_wrong = 0
        all_hallucination = 0
        all_parameter_wrong = 0
        all_executable_plan = 0
        all_correct_plan = 0

        all_matched_node = 0
        all_matched_edge = 0
        all_matched_action = 0
        all_matched_all = 0

        all_node_goals = 0
        all_edge_goals = 0
        all_action_goals = 0
        all_goals = 0

        # SDA-specific counters
        total_replan_count = 0
        sda_recovered = 0

        error_info = {}

        for output_dict in llm_response:
            file_id = output_dict["identifier"]
            task = id2task[file_id]
            logger.info(f"Task is {task}, file_id is {file_id}")
            program_num += 1

            program_dict = task_dicts[task][file_id]
            goals = program_dict["vh_goal"]
            gold_action_goals = goals["actions"]
            scene_goals = goals["goal"]
            gold_node_goals = []
            gold_edge_goals = []
            for scene_goal in scene_goals:
                if (
                    "id" in scene_goal
                    and "class_name" in scene_goal
                    and "state" in scene_goal
                ):
                    gold_node_goals.append(scene_goal)
                elif (
                    "from_id" in scene_goal
                    and "to_id" in scene_goal
                    and "relation_type" in scene_goal
                ):
                    gold_edge_goals.append(scene_goal)
                else:
                    raise ValueError("Scene goal is not in correct format")

            gold_node_goals = remove_duplicate_dicts(gold_node_goals)
            gold_edge_goals = remove_duplicate_dicts(gold_edge_goals)
            gold_action_goals = list(set(gold_action_goals))

            motion_planner, relevant_id, gd_actions, task_name, _ = construct_planner(
                name_equivalence,
                properties_data,
                object_placing,
                scenegraph_id=scenegraph_id,
                script_id=file_id,
                dataset_root=data_dir,
            )
            _, _, _, _, _, relevant_name_to_id = motion_planner.get_symbolic_goal_nl(
                gold_node_goals, gold_edge_goals, action_goals=gold_action_goals
            )

            _, _, _, all_success, _, _, _ = scene_evaluate_wID(
                motion_planner.final_state_dict,
                gold_node_goals,
                gold_edge_goals,
                motion_planner.acting_char_id,
            )

            if not all_success:
                program_num -= 1
                logger.info(f"Program {file_id} did not pass gold test")
                continue

            all_node_goals += len(gold_node_goals)
            all_edge_goals += len(gold_edge_goals)
            all_action_goals += len(gold_action_goals)
            all_goals += (
                len(gold_node_goals) + len(gold_edge_goals) + len(gold_action_goals)
            )

            executable = False
            format_error = False
            hallucination_error = False
            parameter_error = False

            actions = output_dict["llm_output"]
            if actions.startswith("```json"):
                actions = actions[7:]
            actions = actions.strip().replace("\n", "")
            actions = actions.replace("'", '"')

            try:
                actions = load_json_preserving_order(actions)
            except Exception as e:
                logger.info(f"Task {task_name}, file {file_id} prediction has format error")
                all_parsing_wrong += 1
                actions = None
                format_error = True

            if actions is None or len(actions) == 0 or not check_name_id_format(actions)[0]:
                all_parsing_wrong += 1
                logger.info(f"Task {task_name}, file {file_id} prediction has no prediction")
                format_error = True

            if not format_error:
                if check_no_hallucination_in_action(actions) and \
                   check_no_hallucination_in_arg(actions, relevant_name_to_id):
                    hallucination_error = False
                else:
                    logger.info(f"Task {task_name}, file {file_id} has hallucination error")
                    all_hallucination += 1
                    hallucination_error = True

            if not format_error and not hallucination_error:
                logger.info(f"{actions=}")
                pass_check, err = check_action_grammar(actions)
                if pass_check:
                    actions = json_to_action(actions, relevant_name_to_id=relevant_name_to_id)
                    parameter_error = False
                else:
                    logger.info(f"Task {task_name}, file {file_id} has arguments number error")
                    all_parameter_wrong += 1
                    parameter_error = True

            if not format_error and not hallucination_error and not parameter_error:
                logger.info(f"{actions=}")

                if actions == gd_actions:
                    # Perfect match — no need to execute
                    all_executable_plan += 1
                    error_info[file_id] = {
                        "executable": True,
                        "actions": actions,
                        "error_type": None,
                        "error_action": None,
                        "sda_replanned": False,
                        "replan_count": 0,
                    }
                else:
                    # ---- Run execution with SDA-Planner ----
                    motion_planner.reset()
                    executable, history_actions, replan_count, ei = _run_with_sda(
                        actions=actions,
                        motion_planner=motion_planner,
                        error_code_to_type=error_code_to_type,
                        error_code_to_number=error_code_to_number,
                        diagnoser=diagnoser,
                        subtree_gen=subtree_gen,
                    )

                    total_replan_count += replan_count
                    if replan_count > 0 and executable:
                        sda_recovered += 1

                    error_info[file_id] = {
                        "executable": executable,
                        "actions": actions,
                        "error_type": ei.get("error_type"),
                        "error_action": ei.get("error_action"),
                        "sda_replanned": replan_count > 0,
                        "replan_count": replan_count,
                    }

                    if executable:
                        all_executable_plan += 1
                        logger.info("Executable!")

                    (
                        node_match_num, edge_match_num, action_match_num,
                        all_pred_success, _, _, _,
                    ) = scene_evaluate_wID(
                        motion_planner.env_state.to_dict(),
                        gold_node_goals,
                        gold_edge_goals,
                        motion_planner.acting_char_id,
                        action_seq=history_actions,
                        action_goals=gold_action_goals,
                    )

                    logger.info(f"Predicted: {node_match_num=}, {edge_match_num=}, {action_match_num=}")
                    logger.info(f"Gold: {len(gold_node_goals)=}, {len(gold_edge_goals)=}, {len(gold_action_goals)=}")
                    logger.info(f"Goals all satisfied: {all_pred_success=}")

                    all_matched_node += node_match_num
                    all_matched_edge += edge_match_num
                    all_matched_action += action_match_num
                    all_matched_all += node_match_num + edge_match_num + action_match_num

                    if all_pred_success:
                        all_correct_plan += 1
                        logger.info("EVERYTHING SUCCEED!")

            else:
                if format_error:
                    error_info[file_id] = {
                        "executable": False, "actions": actions,
                        "error_type": "parsing error", "error_action": None,
                        "sda_replanned": False, "replan_count": 0,
                    }
                elif hallucination_error:
                    error_info[file_id] = {
                        "executable": False, "actions": actions,
                        "error_type": "hallucination error", "error_action": None,
                        "sda_replanned": False, "replan_count": 0,
                    }
                elif parameter_error:
                    error_info[file_id] = {
                        "executable": False, "actions": actions,
                        "error_type": "parameter error", "error_action": None,
                        "sda_replanned": False, "replan_count": 0,
                    }
                else:
                    raise ValueError("Unknown error type")

        # ---- Metrics ----
        logger.info(f"Program number: {program_num}")
        logger.info(f"[SDA] Total replan count: {total_replan_count}")
        logger.info(f"[SDA] Tasks recovered by SDA: {sda_recovered}")

        all_wrong_order_num = error_code_to_number[0]
        all_missing_step_num = error_code_to_number[1]
        all_affordance_num = error_code_to_number[2]
        all_additional_step_num = error_code_to_number[4]

        summary = {
            "goal_evaluation": {
                "task_success_rate": round(100.0 * all_correct_plan / program_num, 4),
                "state_goal": round(100.0 * all_matched_node / all_node_goals, 4),
                "relation_goal": round(100.0 * all_matched_edge / all_edge_goals, 4),
                "action_goal": round(100.0 * all_matched_action / all_action_goals, 4),
                "total_goal": round(100.0 * all_matched_all / all_goals, 4),
            },
            "trajectory_evaluation": {
                "execution_success_rate": round(100.0 * all_executable_plan / program_num, 1),
                "grammar_error": {
                    "parsing": round(100.0 * all_parsing_wrong / program_num, 4),
                    "hallucination": round(100.0 * all_hallucination / program_num, 4),
                    "predicate_argument_number": round(100.0 * all_parameter_wrong / program_num, 4),
                },
                "runtime_error": {
                    "wrong_order": round(100.0 * all_wrong_order_num / program_num, 4),
                    "missing_step": round(100.0 * all_missing_step_num / program_num, 4),
                    "affordance_error": round(100.0 * all_affordance_num / program_num, 4),
                    "additional_step": round(100.0 * all_additional_step_num / program_num, 4),
                },
            },
            "sda_metrics": {
                "total_replan_count": total_replan_count,
                "avg_replan_per_task": round(total_replan_count / program_num, 4),
                "tasks_recovered_by_sda": sda_recovered,
                "sda_recovery_rate": round(100.0 * sda_recovered / program_num, 4),
            },
        }

        all_results[model_name] = [summary, error_info]
        save_path = osp.join(output_dir, model_name)
        if not osp.exists(save_path):
            os.makedirs(save_path)
        with open(osp.join(save_path, "summary.json"), "w") as f:
            json.dump(summary, f, indent=4)
            logger.info(f"SDA results of {model_name} saved to {save_path}")
        with open(osp.join(save_path, "error_info.json"), "w") as f:
            json.dump(error_info, f, indent=4)

    return all_results


# ---------------------------------------------------------------------------
# SDA execution loop
# ---------------------------------------------------------------------------

def _run_with_sda(
    actions,
    motion_planner,
    error_code_to_type,
    error_code_to_number,
    diagnoser,
    subtree_gen,
):
    """
    Execute actions with SDA-Planner adaptive replanning.
    Returns (executable, history_actions, replan_count, error_info_dict)
    """
    replan_count = 0
    history_actions = []
    history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]
    executable = True
    error_action = None
    failed_error_code = None

    # Work on a mutable copy
    current_actions = list(actions)
    i = 0

    while i < len(current_actions):
        action = current_actions[i]
        logger.info(f"[SDA] Executing action {i}: {action}")

        history_env_states_cp = copy.deepcopy(history_env_states)
        exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

        if not exe_flag:
            # ---- Action failed ----
            logger.info(f"[SDA] Action failed: {action}")
            error_action = action

            formal_info_checker = TemporalOrderChecker(my_info, history_env_states_cp)
            formal_info = formal_info_checker.run_checker()
            failed_error_code = formal_info.get_error_type()

            assert failed_error_code in error_code_to_number, \
                f"Unknown error code {failed_error_code}"

            error_code_to_number[failed_error_code] += 1
            logger.info(f"[SDA] Error type: {error_code_to_type[failed_error_code]}")

            ADDITIONAL_ERROR_CODE = 4
            if failed_error_code == ADDITIONAL_ERROR_CODE:
                # Additional step — skip and continue
                i += 1
                continue

            # ---- Attempt SDA replanning ----
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[SDA] Max replan attempts reached.")
                executable = False
                break

            # Parse actions for SDG diagnosis
            parsed_actions = [parse_action(a) for a in current_actions]

            diagnosis = diagnoser.diagnose(
                actions=parsed_actions,
                error_index=i,
                eai_error_code=failed_error_code,
                env_state_history=history_env_states,
                char_id=motion_planner.acting_char_id,
            )
            logger.info(f"[SDA] Diagnosis: {diagnosis}")

            # Generate corrected action list
            corrected_parsed = subtree_gen.generate(
                actions=parsed_actions,
                diagnosis=diagnosis,
                env_state=copy.deepcopy(motion_planner.env_state.to_dict()),
                char_id=motion_planner.acting_char_id,
                motion_planner=motion_planner,
            )

            # Convert back to original action format
            corrected_actions = _restore_action_format(
                corrected_parsed, current_actions, i
            )

            if corrected_actions == current_actions:
                # No change possible
                logger.info("[SDA] No corrective actions found.")
                executable = False
                break

            # Apply correction — reset and re-execute from replan_start
            current_actions = corrected_actions
            replan_start = diagnosis.replan_start

            # Reset planner state to replan_start
            motion_planner.reset()
            history_actions = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            # Re-execute actions before replan_start
            for j in range(replan_start):
                re_exe, _ = motion_planner.my_execute_primitive_action_eval(
                    current_actions[j]
                )
                if re_exe:
                    history_actions.append(current_actions[j])
                    history_env_states.append(
                        copy.deepcopy(motion_planner.env_state.to_dict())
                    )

            i = replan_start
            replan_count += 1
            executable = True
            logger.info(f"[SDA] Replan #{replan_count}, resuming from index {i}")

        else:
            # ---- Action succeeded ----
            logger.info(f"[SDA] Action succeeded: {action}")
            history_actions.append(action)
            history_env_states.append(
                copy.deepcopy(motion_planner.env_state.to_dict())
            )
            i += 1

    error_info_dict = {
        "error_type": error_code_to_type.get(failed_error_code, "none").lower()
        if failed_error_code is not None else None,
        "error_action": error_action,
    }

    return executable, history_actions, replan_count, error_info_dict


def _restore_action_format(corrected_parsed, original_actions, error_index):
    """
    Convert corrected parsed actions back to the original string/dict format
    used by motion_planner.my_execute_primitive_action_eval.
    """
    result = []
    orig_lookup = {i: a for i, a in enumerate(original_actions)}

    for parsed in corrected_parsed:
        raw = parsed.get("raw")
        if raw is not None:
            result.append(raw)
        else:
            result.append(parsed)

    return result
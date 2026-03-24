# # # # # # # # # # # """
# # # # # # # # # # # eai_sda_runner.py
# # # # # # # # # # # =================
# # # # # # # # # # # Runs INSIDE Docker container.
# # # # # # # # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # # # # # # # Steps:
# # # # # # # # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # # # # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # # # # # # # 3. Parse LLM response into EAI action format
# # # # # # # # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # # # # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # # # # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # # # # # # # Usage (inside Docker):
# # # # # # # # # # #     pip install groq
# # # # # # # # # # #     export GROQ_API_KEY="your_key_here"
# # # # # # # # # # #     python3 eai_sda_runner.py
# # # # # # # # # # # """

# # # # # # # # # # # import os
# # # # # # # # # # # import sys
# # # # # # # # # # # import json
# # # # # # # # # # # import copy
# # # # # # # # # # # import re
# # # # # # # # # # # import logging
# # # # # # # # # # # import os.path as osp

# # # # # # # # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # # # # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # # # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # # # # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # # # # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # # # # # # # #     construct_planner,
# # # # # # # # # # #     json_to_action,
# # # # # # # # # # #     check_action_grammar,
# # # # # # # # # # #     check_no_hallucination_in_action,
# # # # # # # # # # #     check_no_hallucination_in_arg,
# # # # # # # # # # #     load_json_preserving_order,
# # # # # # # # # # # )
# # # # # # # # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # # # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # # # # # # # from sdg import get_preconditions, explain_precondition
# # # # # # # # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # # # # # # # ── Groq ─────────────────────────────────────────────────────────────────────
# # # # # # # # # # # from groq import Groq

# # # # # # # # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # # # # # # # logging.basicConfig(
# # # # # # # # # # #     level=logging.INFO,
# # # # # # # # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # # # # # # # )
# # # # # # # # # # # logger = logging.getLogger(__name__)

# # # # # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # # # # Configuration
# # # # # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # # # # GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
# # # # # # # # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # # # # # # # MAX_REPLAN        = 3
# # # # # # # # # # # SCENEGRAPH_ID     = 1
# # # # # # # # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # # # # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # # # # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # # # # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # # # # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # # # # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # # # # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # # # # # # # EAI error codes
# # # # # # # # # # # ERROR_CODE_TO_TYPE = {
# # # # # # # # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # # # # # # # #     1: "MISSING_STEP",
# # # # # # # # # # #     2: "AFFORDANCE_ERROR",
# # # # # # # # # # #     3: "UNSEEN_OBJECT",
# # # # # # # # # # #     4: "ADDITIONAL_STEP",
# # # # # # # # # # #     5: "UNKNOWN_ERROR",
# # # # # # # # # # # }

# # # # # # # # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # # # # # # # Generate executable action sequences for household tasks.

# # # # # # # # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # # # # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # # # # # # # Rules:
# # # # # # # # # # # - Valid actions: WALK, FIND, GRAB, PUTIN, PUTBACK, OPEN, CLOSE, SWITCHON, SWITCHOFF, PLUGIN, PLUGOUT, SIT, STANDUP, LIE, WASH, DRINK, EAT, READ, POUR, TOUCH, MOVE, WATCH, TYPE, DROP
# # # # # # # # # # # - WALK to object before GRAB
# # # # # # # # # # # - OPEN containers before PUTIN
# # # # # # # # # # # - Max 2 objects held at once
# # # # # # # # # # # - No explanations, ONLY the JSON object
# # # # # # # # # # # """


# # # # # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # # # # Groq LLM Client
# # # # # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # # # # class GroqClient:
# # # # # # # # # # #     def __init__(self):
# # # # # # # # # # #         self.client = Groq(api_key=GROQ_API_KEY)

# # # # # # # # # # #     def call(self, user_prompt: str) -> str:
# # # # # # # # # # #         try:
# # # # # # # # # # #             response = self.client.chat.completions.create(
# # # # # # # # # # #                 model       = MODEL,
# # # # # # # # # # #                 temperature = 0,
# # # # # # # # # # #                 max_tokens  = 1024,
# # # # # # # # # # #                 messages    = [
# # # # # # # # # # #                     {"role": "system", "content": SYSTEM_PROMPT},
# # # # # # # # # # #                     {"role": "user",   "content": user_prompt},
# # # # # # # # # # #                 ],
# # # # # # # # # # #             )
# # # # # # # # # # #             return response.choices[0].message.content.strip()
# # # # # # # # # # #         except Exception as e:
# # # # # # # # # # #             logger.error(f"Groq API error: {e}")
# # # # # # # # # # #             return ""


# # # # # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # # # # Action Format Converters
# # # # # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # # # # def parse_llm_output(raw: str) -> list:
# # # # # # # # # # #     """
# # # # # # # # # # #     Parse LLM JSON output into EAI action list format.
# # # # # # # # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # # # # # # # #     """
# # # # # # # # # # #     # Strip markdown fences
# # # # # # # # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # # # # # # # #     # Try to extract JSON object
# # # # # # # # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # # # # # # # #     if not match:
# # # # # # # # # # #         logger.warning("No JSON found in LLM output")
# # # # # # # # # # #         return []

# # # # # # # # # # #     raw = match.group(0)

# # # # # # # # # # #     try:
# # # # # # # # # # #         # Use EAI's own parser
# # # # # # # # # # #         parsed = load_json_preserving_order(raw)
# # # # # # # # # # #         return parsed
# # # # # # # # # # #     except Exception as e:
# # # # # # # # # # #         logger.warning(f"JSON parse error: {e}")
# # # # # # # # # # #         return []


# # # # # # # # # # # def actions_to_eai_format(actions_json) -> list:
# # # # # # # # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # # # # # # # #     if not actions_json:
# # # # # # # # # # #         return []
# # # # # # # # # # #     # EAI handles this via json_to_action after validation
# # # # # # # # # # #     return actions_json


# # # # # # # # # # # def build_feedback_prompt(
# # # # # # # # # # #     original_prompt: str,
# # # # # # # # # # #     executed_actions: list,
# # # # # # # # # # #     failed_action,
# # # # # # # # # # #     error_type: str,
# # # # # # # # # # #     diagnosis,
# # # # # # # # # # # ) -> str:
# # # # # # # # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # # # # # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # # # # # # # #     unsatisfied_str = "\n".join(
# # # # # # # # # # #         f"  - {explain_precondition(p)}"
# # # # # # # # # # #         for p in diagnosis.unsatisfied_needs
# # # # # # # # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # # # # # # # #     feedback = f"""
# # # # # # # # # # # {original_prompt}

# # # # # # # # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # # # # # # # Your previous plan failed at action: {failed_action}
# # # # # # # # # # # Error type: {error_type}

# # # # # # # # # # # Successfully executed steps so far:
# # # # # # # # # # # {executed_str}

# # # # # # # # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # # # # # # # Unsatisfied preconditions:
# # # # # # # # # # # {unsatisfied_str}

# # # # # # # # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # # # # # # # === INSTRUCTIONS ===
# # # # # # # # # # # Generate a NEW complete action sequence that:
# # # # # # # # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # # # # # # # 2. Completes the full task from the beginning
# # # # # # # # # # # 3. Ensures every action's preconditions are met

# # # # # # # # # # # Respond with ONLY the JSON action sequence.
# # # # # # # # # # # """
# # # # # # # # # # #     return feedback.strip()


# # # # # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # # # # SDA + EAI Runner
# # # # # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # # # # class EAISDARunner:

# # # # # # # # # # #     def __init__(self):
# # # # # # # # # # #         self.llm = GroqClient()

# # # # # # # # # # #         # Load EAI resources
# # # # # # # # # # #         self.properties_data  = utils.load_properties_data()
# # # # # # # # # # #         self.object_placing   = utils.load_object_placing()
# # # # # # # # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # # # # # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # # # # # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # # # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # # # # # # # #     def run_all(self):
# # # # # # # # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # # # # # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # # # # # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # # # # # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # # # # # # # #         outputs      = []
# # # # # # # # # # #         success      = 0
# # # # # # # # # # #         total        = 0
# # # # # # # # # # #         replan_total = 0

# # # # # # # # # # #         for task_name, task_files in self.task_dicts.items():
# # # # # # # # # # #             for file_id, task_goal_dict in task_files.items():
# # # # # # # # # # #                 total += 1
# # # # # # # # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # # # # # # # #                 result, replan_count = self.run_single_task(
# # # # # # # # # # #                     file_id, task_name, task_goal_dict
# # # # # # # # # # #                 )
# # # # # # # # # # #                 replan_total += replan_count

# # # # # # # # # # #                 outputs.append({
# # # # # # # # # # #                     "identifier": file_id,
# # # # # # # # # # #                     "llm_output": result,
# # # # # # # # # # #                 })

# # # # # # # # # # #                 if total % 10 == 0:
# # # # # # # # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # # # # # # # #                     self._save_outputs(outputs)

# # # # # # # # # # #         # Save final outputs
# # # # # # # # # # #         self._save_outputs(outputs)

# # # # # # # # # # #         logger.info(f"\n=== DONE ===")
# # # # # # # # # # #         logger.info(f"Total tasks    : {total}")
# # # # # # # # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # # # # # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # # # # # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # # # # # # # #         return outputs

# # # # # # # # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # # # # # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # # # # # # # #         goals        = task_goal_dict["vh_goal"]
# # # # # # # # # # #         action_goals = goals["actions"]
# # # # # # # # # # #         scene_goals  = goals["goal"]

# # # # # # # # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # # # # # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # # # # # # # #         # Build EAI motion planner for this task
# # # # # # # # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # # # # # # # #             self.name_equivalence,
# # # # # # # # # # #             self.properties_data,
# # # # # # # # # # #             self.object_placing,
# # # # # # # # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # # # # # # # #             script_id     = file_id,
# # # # # # # # # # #             dataset_root  = DATA_DIR,
# # # # # # # # # # #         )

# # # # # # # # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # # # # # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # # # # # # # #         )

# # # # # # # # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # # # # # # # #         # Build EAI prompt
# # # # # # # # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # # # # # # # #         prompt = one_shot.prompt
# # # # # # # # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # # # # # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # # # # # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # # # # # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # # # # # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # # # # # # # #         replan_count  = 0
# # # # # # # # # # #         current_prompt = prompt

# # # # # # # # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # # # # # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # # # # # # # #             raw_output = self.llm.call(current_prompt)
# # # # # # # # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # # # # # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # # # # # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # # # # # # # #             if not parsed_actions:
# # # # # # # # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # # # # # # # #                 break

# # # # # # # # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # # # # # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # # # # # # # #             if not pass_check:
# # # # # # # # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # # # # # # # #                 break

# # # # # # # # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # # # # # # # #             actions = json_to_action(
# # # # # # # # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # # # # # # # #             )

# # # # # # # # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # # # # # # # #             motion_planner.reset()
# # # # # # # # # # #             history_actions     = []
# # # # # # # # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # # # # # # # #             executable          = True
# # # # # # # # # # #             failed_error_code   = None
# # # # # # # # # # #             failed_action_eai   = None

# # # # # # # # # # #             for action in actions:
# # # # # # # # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # # # # # # # #                 if not exe_flag:
# # # # # # # # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # # # # # # # #                     executable        = False
# # # # # # # # # # #                     failed_action_eai = action
# # # # # # # # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # # # # # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # # # # # # # #                     formal_info       = checker.run_checker()
# # # # # # # # # # #                     failed_error_code = formal_info.get_error_type()
# # # # # # # # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # # # # # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # # # # # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # # # # # # # #                     if attempt < MAX_REPLAN:
# # # # # # # # # # #                         replan_count += 1

# # # # # # # # # # #                         # Convert executed history to ActionStep objects
# # # # # # # # # # #                         exec_steps = []
# # # # # # # # # # #                         for i, a in enumerate(history_actions):
# # # # # # # # # # #                             action_name = a.get("action", "UNKNOWN").upper()
# # # # # # # # # # #                             obj1 = a.get("o1", "unknown")
# # # # # # # # # # #                             obj2 = a.get("o2")
# # # # # # # # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # # # # # # # #                         # Convert failed action to ActionStep
# # # # # # # # # # #                         failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # # # # # # # #                         failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # # # # # # # #                         failed_obj2 = failed_action_eai.get("o2")
# # # # # # # # # # #                         failed_step = ActionStep(
# # # # # # # # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # # # # # # # #                         )

# # # # # # # # # # #                         # All steps as ActionStep
# # # # # # # # # # #                         all_steps = exec_steps + [failed_step]

# # # # # # # # # # #                         # Run SDA diagnosis
# # # # # # # # # # #                         diagnosis = diagnose_error(
# # # # # # # # # # #                             action_history = exec_steps,
# # # # # # # # # # #                             failed_step    = failed_step,
# # # # # # # # # # #                             error_type     = error_type_str,
# # # # # # # # # # #                             full_plan      = all_steps,
# # # # # # # # # # #                         )

# # # # # # # # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # # # # # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # # # # # # # #                         # Build feedback prompt
# # # # # # # # # # #                         current_prompt = build_feedback_prompt(
# # # # # # # # # # #                             original_prompt  = prompt,
# # # # # # # # # # #                             executed_actions = history_actions,
# # # # # # # # # # #                             failed_action    = failed_action_eai,
# # # # # # # # # # #                             error_type       = error_type_str,
# # # # # # # # # # #                             diagnosis        = diagnosis,
# # # # # # # # # # #                         )

# # # # # # # # # # #                     break  # break action loop, retry with new prompt

# # # # # # # # # # #                 else:
# # # # # # # # # # #                     history_actions.append(action)
# # # # # # # # # # #                     history_env_states.append(
# # # # # # # # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # # # # # # # #                     )

# # # # # # # # # # #             if executable:
# # # # # # # # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # # # # # # # #                 break

# # # # # # # # # # #             if attempt == MAX_REPLAN:
# # # # # # # # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # # # # # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # # # # # # # #         return raw_output, replan_count

# # # # # # # # # # #     def _save_outputs(self, outputs: list):
# # # # # # # # # # #         """Save outputs in EAI format."""
# # # # # # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # # # # # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # # # # # # # #         with open(out_path, "w") as f:
# # # # # # # # # # #             json.dump(outputs, f, indent=4)
# # # # # # # # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # # # # Entry Point
# # # # # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # # # # if __name__ == "__main__":
# # # # # # # # # # #     if not GROQ_API_KEY:
# # # # # # # # # # #         print("ERROR: GROQ_API_KEY environment variable not set!")
# # # # # # # # # # #         print("Run: export GROQ_API_KEY='your_key_here'")
# # # # # # # # # # #         sys.exit(1)

# # # # # # # # # # #     runner = EAISDARunner()
# # # # # # # # # # #     runner.run_all()
# # # # # startme
# # # # # """
# # # # # eai_sda_runner.py
# # # # # =================
# # # # # Runs INSIDE Docker container.
# # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # Steps:
# # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # 3. Parse LLM response into EAI action format
# # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # Usage (inside Docker):
# # # # #     pip install groq
# # # # #     export GROQ_API_KEY="your_key_here"
# # # # #     python3 eai_sda_runner.py
# # # # # """

# # # # # import os
# # # # # import sys
# # # # # import json
# # # # # import copy
# # # # # import re
# # # # # import logging
# # # # # import os.path as osp

# # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # #     construct_planner,
# # # # #     json_to_action,
# # # # #     check_action_grammar,
# # # # #     check_no_hallucination_in_action,
# # # # #     check_no_hallucination_in_arg,
# # # # #     load_json_preserving_order,
# # # # # )
# # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # from sdg import get_preconditions, explain_precondition
# # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # ── Groq ─────────────────────────────────────────────────────────────────────
# # # # # from groq import Groq

# # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # logging.basicConfig(
# # # # #     level=logging.INFO,
# # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # )
# # # # # logger = logging.getLogger(__name__)

# # # # # # ─────────────────────────────────────────────
# # # # # # Configuration
# # # # # # ─────────────────────────────────────────────

# # # # # GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
# # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # MAX_REPLAN        = 3
# # # # # SCENEGRAPH_ID     = 1
# # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # EAI error codes
# # # # # ERROR_CODE_TO_TYPE = {
# # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # #     1: "MISSING_STEP",
# # # # #     2: "AFFORDANCE_ERROR",
# # # # #     3: "UNSEEN_OBJECT",
# # # # #     4: "ADDITIONAL_STEP",
# # # # #     5: "UNKNOWN_ERROR",
# # # # # }

# # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # Generate executable action sequences for household tasks.

# # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # VALID ACTIONS ONLY (use no others):
# # # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # # Rules:
# # # # # - WALK to object before GRAB
# # # # # - OPEN containers before PUTIN
# # # # # - Max 2 objects held at once
# # # # # - No explanations, ONLY the JSON object
# # # # # """


# # # # # # ─────────────────────────────────────────────
# # # # # # Groq LLM Client
# # # # # # ─────────────────────────────────────────────

# # # # # class GroqClient:
# # # # #     def __init__(self):
# # # # #         self.client = Groq(api_key=GROQ_API_KEY)

# # # # #     def call(self, user_prompt: str) -> str:
# # # # #         try:
# # # # #             response = self.client.chat.completions.create(
# # # # #                 model       = MODEL,
# # # # #                 temperature = 0,
# # # # #                 max_tokens  = 1024,
# # # # #                 messages    = [
# # # # #                     {"role": "system", "content": SYSTEM_PROMPT},
# # # # #                     {"role": "user",   "content": user_prompt},
# # # # #                 ],
# # # # #             )
# # # # #             return response.choices[0].message.content.strip()
# # # # #         except Exception as e:
# # # # #             logger.error(f"Groq API error: {e}")
# # # # #             return ""


# # # # # # ─────────────────────────────────────────────
# # # # # # Action Format Converters
# # # # # # ─────────────────────────────────────────────

# # # # # def parse_llm_output(raw: str) -> list:
# # # # #     """
# # # # #     Parse LLM JSON output into EAI action list format.
# # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # #     """
# # # # #     # Strip markdown fences
# # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # #     # Try to extract JSON object
# # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # #     if not match:
# # # # #         logger.warning("No JSON found in LLM output")
# # # # #         return []

# # # # #     raw = match.group(0)

# # # # #     try:
# # # # #         # Use EAI's own parser
# # # # #         parsed = load_json_preserving_order(raw)
# # # # #         return parsed
# # # # #     except Exception as e:
# # # # #         logger.warning(f"JSON parse error: {e}")
# # # # #         return []


# # # # # def actions_to_eai_format(actions_json) -> list:
# # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # #     if not actions_json:
# # # # #         return []
# # # # #     # EAI handles this via json_to_action after validation
# # # # #     return actions_json


# # # # # def build_feedback_prompt(
# # # # #     original_prompt: str,
# # # # #     executed_actions: list,
# # # # #     failed_action,
# # # # #     error_type: str,
# # # # #     diagnosis,
# # # # # ) -> str:
# # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # #     unsatisfied_str = "\n".join(
# # # # #         f"  - {explain_precondition(p)}"
# # # # #         for p in diagnosis.unsatisfied_needs
# # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # #     feedback = f"""
# # # # # {original_prompt}

# # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # Your previous plan failed at action: {failed_action}
# # # # # Error type: {error_type}

# # # # # Successfully executed steps so far:
# # # # # {executed_str}

# # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # Unsatisfied preconditions:
# # # # # {unsatisfied_str}

# # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # === INSTRUCTIONS ===
# # # # # Generate a NEW complete action sequence that:
# # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # 2. Completes the full task from the beginning
# # # # # 3. Ensures every action's preconditions are met

# # # # # Respond with ONLY the JSON action sequence.
# # # # # """
# # # # #     return feedback.strip()


# # # # # # ─────────────────────────────────────────────
# # # # # # SDA + EAI Runner
# # # # # # ─────────────────────────────────────────────

# # # # # class EAISDARunner:

# # # # #     def __init__(self):
# # # # #         self.llm = GroqClient()

# # # # #         # Load EAI resources
# # # # #         self.properties_data  = utils.load_properties_data()
# # # # #         self.object_placing   = utils.load_object_placing()
# # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # #     def run_all(self):
# # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # #         outputs      = []
# # # # #         success      = 0
# # # # #         total        = 0
# # # # #         replan_total = 0

# # # # #         for task_name, task_files in self.task_dicts.items():
# # # # #             for file_id, task_goal_dict in task_files.items():
# # # # #                 total += 1
# # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # #                 result, replan_count = self.run_single_task(
# # # # #                     file_id, task_name, task_goal_dict
# # # # #                 )
# # # # #                 replan_total += replan_count

# # # # #                 outputs.append({
# # # # #                     "identifier": file_id,
# # # # #                     "llm_output": result,
# # # # #                 })

# # # # #                 if total % 10 == 0:
# # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # #                     self._save_outputs(outputs)

# # # # #         # Save final outputs
# # # # #         self._save_outputs(outputs)

# # # # #         logger.info(f"\n=== DONE ===")
# # # # #         logger.info(f"Total tasks    : {total}")
# # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # #         return outputs

# # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # #         goals        = task_goal_dict["vh_goal"]
# # # # #         action_goals = goals["actions"]
# # # # #         scene_goals  = goals["goal"]

# # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # #         # Build EAI motion planner for this task
# # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # #             self.name_equivalence,
# # # # #             self.properties_data,
# # # # #             self.object_placing,
# # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # #             script_id     = file_id,
# # # # #             dataset_root  = DATA_DIR,
# # # # #         )

# # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # #         )

# # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # #         # Build EAI prompt
# # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # #         prompt = one_shot.prompt
# # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # #         replan_count  = 0
# # # # #         current_prompt = prompt

# # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # #             raw_output = self.llm.call(current_prompt)
# # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # #             if not parsed_actions:
# # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # #                 break

# # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # #             if not pass_check:
# # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # #                 break

# # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # #             actions = json_to_action(
# # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # #             )

# # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # #             motion_planner.reset()
# # # # #             history_actions     = []
# # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # #             executable          = True
# # # # #             failed_error_code   = None
# # # # #             failed_action_eai   = None

# # # # #             for action in actions:
# # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # #                 if not exe_flag:
# # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # #                     executable        = False
# # # # #                     failed_action_eai = action
# # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # #                     formal_info       = checker.run_checker()
# # # # #                     failed_error_code = formal_info.get_error_type()
# # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # #                     if attempt < MAX_REPLAN:
# # # # #                         replan_count += 1

# # # # #                         # Convert executed history to ActionStep objects
# # # # #                         exec_steps = []
# # # # #                         for i, a in enumerate(history_actions):
# # # # #                             action_name = a.get("action", "UNKNOWN").upper()
# # # # #                             obj1 = a.get("o1", "unknown")
# # # # #                             obj2 = a.get("o2")
# # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # #                         # Convert failed action to ActionStep
# # # # #                         failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # #                         failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # #                         failed_obj2 = failed_action_eai.get("o2")
# # # # #                         failed_step = ActionStep(
# # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # #                         )

# # # # #                         # All steps as ActionStep
# # # # #                         all_steps = exec_steps + [failed_step]

# # # # #                         # Run SDA diagnosis
# # # # #                         diagnosis = diagnose_error(
# # # # #                             action_history = exec_steps,
# # # # #                             failed_step    = failed_step,
# # # # #                             error_type     = error_type_str,
# # # # #                             full_plan      = all_steps,
# # # # #                         )

# # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # #                         # Build feedback prompt
# # # # #                         current_prompt = build_feedback_prompt(
# # # # #                             original_prompt  = prompt,
# # # # #                             executed_actions = history_actions,
# # # # #                             failed_action    = failed_action_eai,
# # # # #                             error_type       = error_type_str,
# # # # #                             diagnosis        = diagnosis,
# # # # #                         )

# # # # #                     break  # break action loop, retry with new prompt

# # # # #                 else:
# # # # #                     history_actions.append(action)
# # # # #                     history_env_states.append(
# # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # #                     )

# # # # #             if executable:
# # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # #                 break

# # # # #             if attempt == MAX_REPLAN:
# # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # #         return raw_output, replan_count

# # # # #     def _save_outputs(self, outputs: list):
# # # # #         """Save outputs in EAI format."""
# # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # #         with open(out_path, "w") as f:
# # # # #             json.dump(outputs, f, indent=4)
# # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # ─────────────────────────────────────────────
# # # # # # Entry Point
# # # # # # ─────────────────────────────────────────────

# # # # # if __name__ == "__main__":
# # # # #     if not GROQ_API_KEY:
# # # # #         print("ERROR: GROQ_API_KEY environment variable not set!")
# # # # #         print("Run: export GROQ_API_KEY='your_key_here'")
# # # # #         sys.exit(1)

# # # # #     runner = EAISDARunner()
# # # # #     runner.run_all()

# # # # # baslangic
# # # # """
# # # # eai_sda_runner.py
# # # # =================
# # # # Runs INSIDE Docker container.
# # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # Steps:
# # # # 1. Generate prompts using EAI's generate_prompts
# # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # 3. Parse LLM response into EAI action format
# # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # Usage (inside Docker):
# # # #     pip install groq
# # # #     export GROQ_API_KEY="your_key_here"
# # # #     python3 eai_sda_runner.py
# # # # """

# # # # import os
# # # # import sys
# # # # import json
# # # # import copy
# # # # import re
# # # # import logging
# # # # import os.path as osp

# # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # #     construct_planner,
# # # #     json_to_action,
# # # #     check_action_grammar,
# # # #     check_no_hallucination_in_action,
# # # #     check_no_hallucination_in_arg,
# # # #     load_json_preserving_order,
# # # # )
# # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # from sdg import get_preconditions, explain_precondition
# # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # ── Groq ─────────────────────────────────────────────────────────────────────
# # # # from groq import Groq

# # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # logging.basicConfig(
# # # #     level=logging.INFO,
# # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # )
# # # # logger = logging.getLogger(__name__)

# # # # # ─────────────────────────────────────────────
# # # # # Configuration
# # # # # ─────────────────────────────────────────────

# # # # GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
# # # # MODEL             = "llama-3.3-70b-versatile"
# # # # MAX_REPLAN        = 3
# # # # SCENEGRAPH_ID     = 1
# # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # EAI error codes
# # # # ERROR_CODE_TO_TYPE = {
# # # #     0: "WRONG_TEMPORAL_ORDER",
# # # #     1: "MISSING_STEP",
# # # #     2: "AFFORDANCE_ERROR",
# # # #     3: "UNSEEN_OBJECT",
# # # #     4: "ADDITIONAL_STEP",
# # # #     5: "UNKNOWN_ERROR",
# # # # }

# # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # Generate executable action sequences for household tasks.

# # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # VALID ACTIONS ONLY (use no others):
# # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # IMPORTANT RULES:
# # # # - Do NOT use PLUGIN or PLUGOUT — all devices are already plugged in
# # # # - Do NOT use OPEN on washing machines — they cannot be opened
# # # # - SWITCHON is enough to turn on any appliance

# # # # Rules:
# # # # - WALK to object before GRAB
# # # # - OPEN containers before PUTIN
# # # # - Max 2 objects held at once
# # # # - No explanations, ONLY the JSON object
# # # # """


# # # # # ─────────────────────────────────────────────
# # # # # Groq LLM Client
# # # # # ─────────────────────────────────────────────

# # # # class GroqClient:
# # # #     def __init__(self):
# # # #         self.client = Groq(api_key=GROQ_API_KEY)

# # # #     def call(self, user_prompt: str) -> str:
# # # #         try:
# # # #             response = self.client.chat.completions.create(
# # # #                 model       = MODEL,
# # # #                 temperature = 0,
# # # #                 max_tokens  = 1024,
# # # #                 messages    = [
# # # #                     {"role": "system", "content": SYSTEM_PROMPT},
# # # #                     {"role": "user",   "content": user_prompt},
# # # #                 ],
# # # #             )
# # # #             return response.choices[0].message.content.strip()
# # # #         except Exception as e:
# # # #             logger.error(f"Groq API error: {e}")
# # # #             return ""


# # # # # ─────────────────────────────────────────────
# # # # # Action Format Converters
# # # # # ─────────────────────────────────────────────

# # # # def parse_llm_output(raw: str) -> list:
# # # #     """
# # # #     Parse LLM JSON output into EAI action list format.
# # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # #     """
# # # #     # Strip markdown fences
# # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # #     # Try to extract JSON object
# # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # #     if not match:
# # # #         logger.warning("No JSON found in LLM output")
# # # #         return []

# # # #     raw = match.group(0)

# # # #     try:
# # # #         # Use EAI's own parser
# # # #         parsed = load_json_preserving_order(raw)
# # # #         return parsed
# # # #     except Exception as e:
# # # #         logger.warning(f"JSON parse error: {e}")
# # # #         return []


# # # # def actions_to_eai_format(actions_json) -> list:
# # # #     """Convert parsed JSON to EAI internal action format."""
# # # #     if not actions_json:
# # # #         return []
# # # #     # EAI handles this via json_to_action after validation
# # # #     return actions_json


# # # # def build_feedback_prompt(
# # # #     original_prompt: str,
# # # #     executed_actions: list,
# # # #     failed_action,
# # # #     error_type: str,
# # # #     diagnosis,
# # # # ) -> str:
# # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # #     unsatisfied_str = "\n".join(
# # # #         f"  - {explain_precondition(p)}"
# # # #         for p in diagnosis.unsatisfied_needs
# # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # #     feedback = f"""
# # # # {original_prompt}

# # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # Your previous plan failed at action: {failed_action}
# # # # Error type: {error_type}

# # # # Successfully executed steps so far:
# # # # {executed_str}

# # # # Root cause of failure: {diagnosis.root_cause}
# # # # Unsatisfied preconditions:
# # # # {unsatisfied_str}

# # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # === INSTRUCTIONS ===
# # # # Generate a NEW complete action sequence that:
# # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # 2. Completes the full task from the beginning
# # # # 3. Ensures every action's preconditions are met

# # # # Respond with ONLY the JSON action sequence.
# # # # """
# # # #     return feedback.strip()


# # # # # ─────────────────────────────────────────────
# # # # # SDA + EAI Runner
# # # # # ─────────────────────────────────────────────

# # # # class EAISDARunner:

# # # #     def __init__(self):
# # # #         self.llm = GroqClient()

# # # #         # Load EAI resources
# # # #         self.properties_data  = utils.load_properties_data()
# # # #         self.object_placing   = utils.load_object_placing()
# # # #         self.name_equivalence = utils.load_name_equivalence()
# # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # #     def run_all(self):
# # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # #         logger.info(f"Model: {MODEL_NAME}")
# # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # #         # outputs      = []
# # # #         # success      = 0
# # # #         # total        = 0
# # # #         # replan_total = 0
# # # #         # Load existing outputs to resume from where we left off
# # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # #         if osp.exists(out_path):
# # # #             existing = json.load(open(out_path))
# # # #             done_ids = {d["identifier"] for d in existing
# # # #                         if d["llm_output"] and d["llm_output"] != "..."}
# # # #             outputs = [d for d in existing if d["identifier"] in done_ids]
# # # #             logger.info(f"Resuming: {len(outputs)} tasks already done")
# # # #         else:
# # # #             outputs = []
# # # #             done_ids = set()

# # # #         total        = 0
# # # #         replan_total = 0

# # # #         for task_name, task_files in self.task_dicts.items():
# # # #             for file_id, task_goal_dict in task_files.items():
# # # #                 if file_id in done_ids:
# # # #                     logger.info(f"  Skipping {file_id} — already done")
# # # #                     continue
# # # #                 total += 1
# # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # #                 result, replan_count = self.run_single_task(
# # # #                     file_id, task_name, task_goal_dict
# # # #                 )
# # # #                 replan_total += replan_count

# # # #                 outputs.append({
# # # #                     "identifier": file_id,
# # # #                     "llm_output": result,
# # # #                 })

# # # #                 if total % 10 == 0:
# # # #                     logger.info(f"Progress: {total} tasks done")
# # # #                     self._save_outputs(outputs)

# # # #         # Save final outputs
# # # #         self._save_outputs(outputs)

# # # #         logger.info(f"\n=== DONE ===")
# # # #         logger.info(f"Total tasks    : {total}")
# # # #         logger.info(f"Total replans  : {replan_total}")
# # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # #         return outputs

# # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # #         goals        = task_goal_dict["vh_goal"]
# # # #         action_goals = goals["actions"]
# # # #         scene_goals  = goals["goal"]

# # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # #         # Build EAI motion planner for this task
# # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # #             self.name_equivalence,
# # # #             self.properties_data,
# # # #             self.object_placing,
# # # #             scenegraph_id = SCENEGRAPH_ID,
# # # #             script_id     = file_id,
# # # #             dataset_root  = DATA_DIR,
# # # #         )

# # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # #         )

# # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # #         # Build EAI prompt
# # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # #         prompt = one_shot.prompt
# # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # #         replan_count  = 0
# # # #         current_prompt = prompt

# # # #         for attempt in range(MAX_REPLAN + 1):
# # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # #             raw_output = self.llm.call(current_prompt)
# # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # #             # ── Parse output ─────────────────────────────────────────────────
# # # #             parsed_actions = parse_llm_output(raw_output)

# # # #             if not parsed_actions:
# # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # #                 break

# # # #             # ── Validate format ───────────────────────────────────────────────
# # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # #             if not pass_check:
# # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # #                 break

# # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # #             actions = json_to_action(
# # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # #             )

# # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # #             motion_planner.reset()
# # # #             history_actions     = []
# # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # #             executable          = True
# # # #             failed_error_code   = None
# # # #             failed_action_eai   = None

# # # #             for action in actions:
# # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # #                 if not exe_flag:
# # # #                     # ── Action failed! ────────────────────────────────────────
# # # #                     executable        = False
# # # #                     failed_action_eai = action
# # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # #                     formal_info       = checker.run_checker()
# # # #                     failed_error_code = formal_info.get_error_type()
# # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # #                     if attempt < MAX_REPLAN:
# # # #                         replan_count += 1

# # # #                         # Convert executed history to ActionStep objects
# # # #                         exec_steps = []
# # # #                         for i, a in enumerate(history_actions):
# # # #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# # # #                             # or could be a dict - handle both
# # # #                             if isinstance(a, dict):
# # # #                                 action_name = a.get("action", "UNKNOWN").upper()
# # # #                                 obj1 = a.get("o1", "unknown")
# # # #                                 obj2 = a.get("o2")
# # # #                             else:
# # # #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# # # #                                 a_str = str(a)
# # # #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# # # #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# # # #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # #                                 obj1 = obj_match[0] if obj_match else "unknown"
# # # #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # #                         # Convert failed action to ActionStep
# # # #                         if isinstance(failed_action_eai, dict):
# # # #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # #                             failed_obj2 = failed_action_eai.get("o2")
# # # #                         else:
# # # #                             fa_str = str(failed_action_eai)
# # # #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# # # #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# # # #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# # # #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # #                         failed_step = ActionStep(
# # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # #                         )

# # # #                         # All steps as ActionStep
# # # #                         all_steps = exec_steps + [failed_step]

# # # #                         # Run SDA diagnosis
# # # #                         diagnosis = diagnose_error(
# # # #                             action_history = exec_steps,
# # # #                             failed_step    = failed_step,
# # # #                             error_type     = error_type_str,
# # # #                             full_plan      = all_steps,
# # # #                         )

# # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # #                         # Build feedback prompt
# # # #                         current_prompt = build_feedback_prompt(
# # # #                             original_prompt  = prompt,
# # # #                             executed_actions = history_actions,
# # # #                             failed_action    = failed_action_eai,
# # # #                             error_type       = error_type_str,
# # # #                             diagnosis        = diagnosis,
# # # #                         )

# # # #                     break  # break action loop, retry with new prompt

# # # #                 else:
# # # #                     history_actions.append(action)
# # # #                     history_env_states.append(
# # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # #                     )

# # # #             if executable:
# # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # #                 break

# # # #             if attempt == MAX_REPLAN:
# # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # #         # Return raw_output string for EAI evaluate_results to process
# # # #         return raw_output, replan_count

# # # #     def _save_outputs(self, outputs: list):
# # # #         """Save outputs in EAI format."""
# # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # #         with open(out_path, "w") as f:
# # # #             json.dump(outputs, f, indent=4)
# # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # ─────────────────────────────────────────────
# # # # # Entry Point
# # # # # ─────────────────────────────────────────────

# # # # if __name__ == "__main__":
# # # #     if not GROQ_API_KEY:
# # # #         print("ERROR: GROQ_API_KEY environment variable not set!")
# # # #         print("Run: export GROQ_API_KEY='your_key_here'")
# # # #         sys.exit(1)

# # # #     runner = EAISDARunner()
# # # #     runner.run_all()
# # # # ## evvelki bizim grog ile isleyendi balaska
# # # # # # # # """
# # # # # # # # eai_sda_runner.py
# # # # # # # # =================
# # # # # # # # Runs INSIDE Docker container.
# # # # # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # # # # Steps:
# # # # # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # # # # 3. Parse LLM response into EAI action format
# # # # # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # # # # Usage (inside Docker):
# # # # # # # #     pip install groq
# # # # # # # #     export GROQ_API_KEY="your_key_here"
# # # # # # # #     python3 eai_sda_runner.py
# # # # # # # # """

# # # # # # # # import os
# # # # # # # # import sys
# # # # # # # # import json
# # # # # # # # import copy
# # # # # # # # import re
# # # # # # # # import logging
# # # # # # # # import os.path as osp

# # # # # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # # # # #     construct_planner,
# # # # # # # #     json_to_action,
# # # # # # # #     check_action_grammar,
# # # # # # # #     check_no_hallucination_in_action,
# # # # # # # #     check_no_hallucination_in_arg,
# # # # # # # #     load_json_preserving_order,
# # # # # # # # )
# # # # # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # # # # from sdg import get_preconditions, explain_precondition
# # # # # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # # # # ── Gemini ───────────────────────────────────────────────────────────────────
# # # # # # # # import google.generativeai as genai

# # # # # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # # # # logging.basicConfig(
# # # # # # # #     level=logging.INFO,
# # # # # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # # # # )
# # # # # # # # logger = logging.getLogger(__name__)

# # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # Configuration
# # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
# # # # # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # # # # MAX_REPLAN        = 3
# # # # # # # # SCENEGRAPH_ID     = 1
# # # # # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # # # # EAI error codes
# # # # # # # # ERROR_CODE_TO_TYPE = {
# # # # # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # # # # #     1: "MISSING_STEP",
# # # # # # # #     2: "AFFORDANCE_ERROR",
# # # # # # # #     3: "UNSEEN_OBJECT",
# # # # # # # #     4: "ADDITIONAL_STEP",
# # # # # # # #     5: "UNKNOWN_ERROR",
# # # # # # # # }

# # # # # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # # # # Generate executable action sequences for household tasks.

# # # # # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # # # # VALID ACTIONS ONLY (use no others):
# # # # # # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # # # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # # # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # # # # # Rules:
# # # # # # # # - WALK to object before GRAB
# # # # # # # # - OPEN containers before PUTIN
# # # # # # # # - Max 2 objects held at once
# # # # # # # # - No explanations, ONLY the JSON object
# # # # # # # # """


# # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # Groq LLM Client
# # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # class GroqClient:
# # # # # # # #     def __init__(self):
# # # # # # # #         genai.configure(api_key=GEMINI_API_KEY)
# # # # # # # #         self.model = genai.GenerativeModel(
# # # # # # # #             model_name=MODEL,
# # # # # # # #             system_instruction=SYSTEM_PROMPT,
# # # # # # # #         )

# # # # # # # #     def call(self, user_prompt: str) -> str:
# # # # # # # #         try:
# # # # # # # #             response = self.model.generate_content(
# # # # # # # #                 user_prompt,
# # # # # # # #                 generation_config=genai.types.GenerationConfig(
# # # # # # # #                     temperature=0,
# # # # # # # #                     max_output_tokens=1024,
# # # # # # # #                 ),
# # # # # # # #             )
# # # # # # # #             return response.text.strip()
# # # # # # # #         except Exception as e:
# # # # # # # #             logger.error(f"Gemini API error: {e}")
# # # # # # # #             return ""


# # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # Action Format Converters
# # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # def parse_llm_output(raw: str) -> list:
# # # # # # # #     """
# # # # # # # #     Parse LLM JSON output into EAI action list format.
# # # # # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # # # # #     """
# # # # # # # #     # Strip markdown fences
# # # # # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # # # # #     # Try to extract JSON object
# # # # # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # # # # #     if not match:
# # # # # # # #         logger.warning("No JSON found in LLM output")
# # # # # # # #         return []

# # # # # # # #     raw = match.group(0)

# # # # # # # #     try:
# # # # # # # #         # Use EAI's own parser
# # # # # # # #         parsed = load_json_preserving_order(raw)
# # # # # # # #         return parsed
# # # # # # # #     except Exception as e:
# # # # # # # #         logger.warning(f"JSON parse error: {e}")
# # # # # # # #         return []


# # # # # # # # def actions_to_eai_format(actions_json) -> list:
# # # # # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # # # # #     if not actions_json:
# # # # # # # #         return []
# # # # # # # #     # EAI handles this via json_to_action after validation
# # # # # # # #     return actions_json


# # # # # # # # def build_feedback_prompt(
# # # # # # # #     original_prompt: str,
# # # # # # # #     executed_actions: list,
# # # # # # # #     failed_action,
# # # # # # # #     error_type: str,
# # # # # # # #     diagnosis,
# # # # # # # # ) -> str:
# # # # # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # # # # #     unsatisfied_str = "\n".join(
# # # # # # # #         f"  - {explain_precondition(p)}"
# # # # # # # #         for p in diagnosis.unsatisfied_needs
# # # # # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # # # # #     feedback = f"""
# # # # # # # # {original_prompt}

# # # # # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # # # # Your previous plan failed at action: {failed_action}
# # # # # # # # Error type: {error_type}

# # # # # # # # Successfully executed steps so far:
# # # # # # # # {executed_str}

# # # # # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # # # # Unsatisfied preconditions:
# # # # # # # # {unsatisfied_str}

# # # # # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # # # # === INSTRUCTIONS ===
# # # # # # # # Generate a NEW complete action sequence that:
# # # # # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # # # # 2. Completes the full task from the beginning
# # # # # # # # 3. Ensures every action's preconditions are met

# # # # # # # # Respond with ONLY the JSON action sequence.
# # # # # # # # """
# # # # # # # #     return feedback.strip()


# # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # SDA + EAI Runner
# # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # class EAISDARunner:

# # # # # # # #     def __init__(self):
# # # # # # # #         self.llm = GroqClient()

# # # # # # # #         # Load EAI resources
# # # # # # # #         self.properties_data  = utils.load_properties_data()
# # # # # # # #         self.object_placing   = utils.load_object_placing()
# # # # # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # # # # #     def run_all(self):
# # # # # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # # # # #         outputs      = []
# # # # # # # #         success      = 0
# # # # # # # #         total        = 0
# # # # # # # #         replan_total = 0

# # # # # # # #         for task_name, task_files in self.task_dicts.items():
# # # # # # # #             for file_id, task_goal_dict in task_files.items():
# # # # # # # #                 total += 1
# # # # # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # # # # #                 result, replan_count = self.run_single_task(
# # # # # # # #                     file_id, task_name, task_goal_dict
# # # # # # # #                 )
# # # # # # # #                 replan_total += replan_count

# # # # # # # #                 outputs.append({
# # # # # # # #                     "identifier": file_id,
# # # # # # # #                     "llm_output": result,
# # # # # # # #                 })

# # # # # # # #                 if total % 10 == 0:
# # # # # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # # # # #                     self._save_outputs(outputs)

# # # # # # # #         # Save final outputs
# # # # # # # #         self._save_outputs(outputs)

# # # # # # # #         logger.info(f"\n=== DONE ===")
# # # # # # # #         logger.info(f"Total tasks    : {total}")
# # # # # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # # # # #         return outputs

# # # # # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # # # # #         goals        = task_goal_dict["vh_goal"]
# # # # # # # #         action_goals = goals["actions"]
# # # # # # # #         scene_goals  = goals["goal"]

# # # # # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # # # # #         # Build EAI motion planner for this task
# # # # # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # # # # #             self.name_equivalence,
# # # # # # # #             self.properties_data,
# # # # # # # #             self.object_placing,
# # # # # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # # # # #             script_id     = file_id,
# # # # # # # #             dataset_root  = DATA_DIR,
# # # # # # # #         )

# # # # # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # # # # #         )

# # # # # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # # # # #         # Build EAI prompt
# # # # # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # # # # #         prompt = one_shot.prompt
# # # # # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # # # # #         replan_count  = 0
# # # # # # # #         current_prompt = prompt

# # # # # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # # # # #             raw_output = self.llm.call(current_prompt)
# # # # # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # # # # #             if not parsed_actions:
# # # # # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # # # # #                 break

# # # # # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # # # # #             # Filter out actions EAI does not recognise (e.g. PLUGIN)
# # # # # # # #             EAI_VALID = {
# # # # # # # #                 "DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE",
# # # # # # # #                 "PUSH","PULL","MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE",
# # # # # # # #                 "SCRUB","WASH","GRAB","SWITCHOFF","SWITCHON","CLOSE","FIND",
# # # # # # # #                 "WALK","OPEN","POINTAT","PUTBACK","PUTIN","PUTOBJBACK","RUN",
# # # # # # # #                 "SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF","GREET",
# # # # # # # #                 "DROP","LIE","POUR",
# # # # # # # #             }
# # # # # # # #             if isinstance(parsed_actions, dict):
# # # # # # # #                 parsed_actions = {k: v for k, v in parsed_actions.items()
# # # # # # # #                                   if k.upper() in EAI_VALID}
# # # # # # # #             elif isinstance(parsed_actions, list):
# # # # # # # #                 parsed_actions = [a for a in parsed_actions
# # # # # # # #                                   if list(a.keys())[0].upper() in EAI_VALID]
# # # # # # # #             if not parsed_actions:
# # # # # # # #                 logger.warning(f"  All actions filtered for {file_id}")
# # # # # # # #                 break
# # # # # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # # # # #             if not pass_check:
# # # # # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # # # # #                 break

# # # # # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # # # # #             actions = json_to_action(
# # # # # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # # # # #             )

# # # # # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # # # # #             motion_planner.reset()
# # # # # # # #             history_actions     = []
# # # # # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # # # # #             executable          = True
# # # # # # # #             failed_error_code   = None
# # # # # # # #             failed_action_eai   = None

# # # # # # # #             for action in actions:
# # # # # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # # # # #                 if not exe_flag:
# # # # # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # # # # #                     executable        = False
# # # # # # # #                     failed_action_eai = action
# # # # # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # # # # #                     formal_info       = checker.run_checker()
# # # # # # # #                     failed_error_code = formal_info.get_error_type()
# # # # # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # # # # #                     if attempt < MAX_REPLAN:
# # # # # # # #                         replan_count += 1

# # # # # # # #                         # Convert executed history to ActionStep objects
# # # # # # # #                         exec_steps = []
# # # # # # # #                         for i, a in enumerate(history_actions):
# # # # # # # #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# # # # # # # #                             # or could be a dict - handle both
# # # # # # # #                             if isinstance(a, dict):
# # # # # # # #                                 action_name = a.get("action", "UNKNOWN").upper()
# # # # # # # #                                 obj1 = a.get("o1", "unknown")
# # # # # # # #                                 obj2 = a.get("o2")
# # # # # # # #                             else:
# # # # # # # #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# # # # # # # #                                 a_str = str(a)
# # # # # # # #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# # # # # # # #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# # # # # # # #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # # # #                                 obj1 = obj_match[0] if obj_match else "unknown"
# # # # # # # #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # # # # #                         # Convert failed action to ActionStep
# # # # # # # #                         if isinstance(failed_action_eai, dict):
# # # # # # # #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # # # # #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # # # # #                             failed_obj2 = failed_action_eai.get("o2")
# # # # # # # #                         else:
# # # # # # # #                             fa_str = str(failed_action_eai)
# # # # # # # #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# # # # # # # #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# # # # # # # #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # # # #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# # # # # # # #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # # # #                         failed_step = ActionStep(
# # # # # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # # # # #                         )

# # # # # # # #                         # All steps as ActionStep
# # # # # # # #                         all_steps = exec_steps + [failed_step]

# # # # # # # #                         # Run SDA diagnosis
# # # # # # # #                         diagnosis = diagnose_error(
# # # # # # # #                             action_history = exec_steps,
# # # # # # # #                             failed_step    = failed_step,
# # # # # # # #                             error_type     = error_type_str,
# # # # # # # #                             full_plan      = all_steps,
# # # # # # # #                         )

# # # # # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # # # # #                         # Build feedback prompt
# # # # # # # #                         current_prompt = build_feedback_prompt(
# # # # # # # #                             original_prompt  = prompt,
# # # # # # # #                             executed_actions = history_actions,
# # # # # # # #                             failed_action    = failed_action_eai,
# # # # # # # #                             error_type       = error_type_str,
# # # # # # # #                             diagnosis        = diagnosis,
# # # # # # # #                         )

# # # # # # # #                     break  # break action loop, retry with new prompt

# # # # # # # #                 else:
# # # # # # # #                     history_actions.append(action)
# # # # # # # #                     history_env_states.append(
# # # # # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # # # # #                     )

# # # # # # # #             if executable:
# # # # # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # # # # #                 break

# # # # # # # #             if attempt == MAX_REPLAN:
# # # # # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # # # # #         return raw_output, replan_count

# # # # # # # #     def _save_outputs(self, outputs: list):
# # # # # # # #         """Save outputs in EAI format."""
# # # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # # # # #         with open(out_path, "w") as f:
# # # # # # # #             json.dump(outputs, f, indent=4)
# # # # # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # # # # ─────────────────────────────────────────────
# # # # # # # # # Entry Point
# # # # # # # # # ─────────────────────────────────────────────

# # # # # # # # if __name__ == "__main__":
# # # # # # # #     if not GEMINI_API_KEY:
# # # # # # # #         print("ERROR: GROQ_API_KEY environment variable not set!")
# # # # # # # #         print("Run: export GROQ_API_KEY='your_key_here'")
# # # # # # # #         sys.exit(1)

# # # # # # # #     runner = EAISDARunner()
# # # # # # # #     runner.run_all()
# # # # # # # """
# # # # # # # eai_sda_runner.py
# # # # # # # =================
# # # # # # # Runs INSIDE Docker container.
# # # # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # # # Steps:
# # # # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # # # 3. Parse LLM response into EAI action format
# # # # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # # # Usage (inside Docker):
# # # # # # #     pip install groq
# # # # # # #     export GEMINI_API_KEY="your_key_here"
# # # # # # #     python3 eai_sda_runner.py
# # # # # # # """

# # # # # # # import os
# # # # # # # import sys
# # # # # # # import json
# # # # # # # import copy
# # # # # # # import re
# # # # # # # import logging
# # # # # # # import os.path as osp

# # # # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # # # #     construct_planner,
# # # # # # #     json_to_action,
# # # # # # #     check_action_grammar,
# # # # # # #     check_no_hallucination_in_action,
# # # # # # #     check_no_hallucination_in_arg,
# # # # # # #     load_json_preserving_order,
# # # # # # # )
# # # # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # # # from sdg import get_preconditions, explain_precondition
# # # # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # # # ── Gemini ───────────────────────────────────────────────────────────────────
# # # # # # # import google.generativeai as genai

# # # # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # # # logging.basicConfig(
# # # # # # #     level=logging.INFO,
# # # # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # # # )
# # # # # # # logger = logging.getLogger(__name__)

# # # # # # # # ─────────────────────────────────────────────
# # # # # # # # Configuration
# # # # # # # # ─────────────────────────────────────────────

# # # # # # # GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
# # # # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # # # MAX_REPLAN        = 3
# # # # # # # SCENEGRAPH_ID     = 1
# # # # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # # # EAI error codes
# # # # # # # ERROR_CODE_TO_TYPE = {
# # # # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # # # #     1: "MISSING_STEP",
# # # # # # #     2: "AFFORDANCE_ERROR",
# # # # # # #     3: "UNSEEN_OBJECT",
# # # # # # #     4: "ADDITIONAL_STEP",
# # # # # # #     5: "UNKNOWN_ERROR",
# # # # # # # }

# # # # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # # # Generate executable action sequences for household tasks.

# # # # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # # # VALID ACTIONS ONLY (use no others):
# # # # # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # # # # Rules:
# # # # # # # - WALK to object before GRAB
# # # # # # # - OPEN containers before PUTIN
# # # # # # # - Max 2 objects held at once
# # # # # # # - No explanations, ONLY the JSON object
# # # # # # # """


# # # # # # # # ─────────────────────────────────────────────
# # # # # # # # Groq LLM Client
# # # # # # # # ─────────────────────────────────────────────

# # # # # # # class GroqClient:
# # # # # # #     def __init__(self):
# # # # # # #         genai.configure(api_key=GEMINI_API_KEY)
# # # # # # #         self.model = genai.GenerativeModel(
# # # # # # #             model_name=MODEL,
# # # # # # #             system_instruction=SYSTEM_PROMPT,
# # # # # # #         )

# # # # # # #     def call(self, user_prompt: str) -> str:
# # # # # # #         try:
# # # # # # #             response = self.model.generate_content(
# # # # # # #                 user_prompt,
# # # # # # #                 generation_config=genai.types.GenerationConfig(
# # # # # # #                     temperature=0,
# # # # # # #                     max_output_tokens=1024,
# # # # # # #                 ),
# # # # # # #             )
# # # # # # #             return response.text.strip()
# # # # # # #         except Exception as e:
# # # # # # #             logger.error(f"Gemini API error: {e}")
# # # # # # #             return ""


# # # # # # # # ─────────────────────────────────────────────
# # # # # # # # Action Format Converters
# # # # # # # # ─────────────────────────────────────────────

# # # # # # # def parse_llm_output(raw: str) -> list:
# # # # # # #     """
# # # # # # #     Parse LLM JSON output into EAI action list format.
# # # # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # # # #     """
# # # # # # #     # Strip markdown fences
# # # # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # # # #     # Try to extract JSON object
# # # # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # # # #     if not match:
# # # # # # #         logger.warning("No JSON found in LLM output")
# # # # # # #         return []

# # # # # # #     raw = match.group(0)

# # # # # # #     try:
# # # # # # #         # Use EAI's own parser
# # # # # # #         parsed = load_json_preserving_order(raw)
# # # # # # #         return parsed
# # # # # # #     except Exception as e:
# # # # # # #         logger.warning(f"JSON parse error: {e}")
# # # # # # #         return []


# # # # # # # def actions_to_eai_format(actions_json) -> list:
# # # # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # # # #     if not actions_json:
# # # # # # #         return []
# # # # # # #     # EAI handles this via json_to_action after validation
# # # # # # #     return actions_json


# # # # # # # def build_feedback_prompt(
# # # # # # #     original_prompt: str,
# # # # # # #     executed_actions: list,
# # # # # # #     failed_action,
# # # # # # #     error_type: str,
# # # # # # #     diagnosis,
# # # # # # # ) -> str:
# # # # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # # # #     unsatisfied_str = "\n".join(
# # # # # # #         f"  - {explain_precondition(p)}"
# # # # # # #         for p in diagnosis.unsatisfied_needs
# # # # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # # # #     feedback = f"""
# # # # # # # {original_prompt}

# # # # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # # # Your previous plan failed at action: {failed_action}
# # # # # # # Error type: {error_type}

# # # # # # # Successfully executed steps so far:
# # # # # # # {executed_str}

# # # # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # # # Unsatisfied preconditions:
# # # # # # # {unsatisfied_str}

# # # # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # # # === INSTRUCTIONS ===
# # # # # # # Generate a NEW complete action sequence that:
# # # # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # # # 2. Completes the full task from the beginning
# # # # # # # 3. Ensures every action's preconditions are met

# # # # # # # Respond with ONLY the JSON action sequence.
# # # # # # # """
# # # # # # #     return feedback.strip()


# # # # # # # # ─────────────────────────────────────────────
# # # # # # # # SDA + EAI Runner
# # # # # # # # ─────────────────────────────────────────────

# # # # # # # class EAISDARunner:

# # # # # # #     def __init__(self):
# # # # # # #         self.llm = GroqClient()

# # # # # # #         # Load EAI resources
# # # # # # #         self.properties_data  = utils.load_properties_data()
# # # # # # #         self.object_placing   = utils.load_object_placing()
# # # # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # # # #     def run_all(self):
# # # # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # # # #         outputs      = []
# # # # # # #         success      = 0
# # # # # # #         total        = 0
# # # # # # #         replan_total = 0

# # # # # # #         for task_name, task_files in self.task_dicts.items():
# # # # # # #             for file_id, task_goal_dict in task_files.items():
# # # # # # #                 total += 1
# # # # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # # # #                 result, replan_count = self.run_single_task(
# # # # # # #                     file_id, task_name, task_goal_dict
# # # # # # #                 )
# # # # # # #                 replan_total += replan_count

# # # # # # #                 outputs.append({
# # # # # # #                     "identifier": file_id,
# # # # # # #                     "llm_output": result,
# # # # # # #                 })

# # # # # # #                 if total % 10 == 0:
# # # # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # # # #                     self._save_outputs(outputs)

# # # # # # #         # Save final outputs
# # # # # # #         self._save_outputs(outputs)

# # # # # # #         logger.info(f"\n=== DONE ===")
# # # # # # #         logger.info(f"Total tasks    : {total}")
# # # # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # # # #         return outputs

# # # # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # # # #         goals        = task_goal_dict["vh_goal"]
# # # # # # #         action_goals = goals["actions"]
# # # # # # #         scene_goals  = goals["goal"]

# # # # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # # # #         # Build EAI motion planner for this task
# # # # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # # # #             self.name_equivalence,
# # # # # # #             self.properties_data,
# # # # # # #             self.object_placing,
# # # # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # # # #             script_id     = file_id,
# # # # # # #             dataset_root  = DATA_DIR,
# # # # # # #         )

# # # # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # # # #         )

# # # # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # # # #         # Build EAI prompt
# # # # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # # # #         prompt = one_shot.prompt
# # # # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # # # #         replan_count  = 0
# # # # # # #         current_prompt = prompt

# # # # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # # # #             raw_output = self.llm.call(current_prompt)
# # # # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # # # #             if not parsed_actions:
# # # # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # # # #                 break

# # # # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # # # #             # Filter out actions EAI does not recognise (e.g. PLUGIN)
# # # # # # #             EAI_VALID = {
# # # # # # #                 "DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE",
# # # # # # #                 "PUSH","PULL","MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE",
# # # # # # #                 "SCRUB","WASH","GRAB","SWITCHOFF","SWITCHON","CLOSE","FIND",
# # # # # # #                 "WALK","OPEN","POINTAT","PUTBACK","PUTIN","PUTOBJBACK","RUN",
# # # # # # #                 "SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF","GREET",
# # # # # # #                 "DROP","LIE","POUR",
# # # # # # #             }
# # # # # # #             if isinstance(parsed_actions, dict):
# # # # # # #                 parsed_actions = {k: v for k, v in parsed_actions.items()
# # # # # # #                                   if k.upper() in EAI_VALID}
# # # # # # #             elif isinstance(parsed_actions, list):
# # # # # # #                 parsed_actions = [a for a in parsed_actions
# # # # # # #                                   if list(a.keys())[0].upper() in EAI_VALID]
# # # # # # #             if not parsed_actions:
# # # # # # #                 logger.warning(f"  All actions filtered for {file_id}")
# # # # # # #                 break
# # # # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # # # #             if not pass_check:
# # # # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # # # #                 break

# # # # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # # # #             actions = json_to_action(
# # # # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # # # #             )

# # # # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # # # #             motion_planner.reset()
# # # # # # #             history_actions     = []
# # # # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # # # #             executable          = True
# # # # # # #             failed_error_code   = None
# # # # # # #             failed_action_eai   = None

# # # # # # #             for action in actions:
# # # # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # # # #                 if not exe_flag:
# # # # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # # # #                     executable        = False
# # # # # # #                     failed_action_eai = action
# # # # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # # # #                     formal_info       = checker.run_checker()
# # # # # # #                     failed_error_code = formal_info.get_error_type()
# # # # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # # # #                     if attempt < MAX_REPLAN:
# # # # # # #                         replan_count += 1

# # # # # # #                         # Convert executed history to ActionStep objects
# # # # # # #                         exec_steps = []
# # # # # # #                         for i, a in enumerate(history_actions):
# # # # # # #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# # # # # # #                             # or could be a dict - handle both
# # # # # # #                             if isinstance(a, dict):
# # # # # # #                                 action_name = a.get("action", "UNKNOWN").upper()
# # # # # # #                                 obj1 = a.get("o1", "unknown")
# # # # # # #                                 obj2 = a.get("o2")
# # # # # # #                             else:
# # # # # # #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# # # # # # #                                 a_str = str(a)
# # # # # # #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# # # # # # #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# # # # # # #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # # #                                 obj1 = obj_match[0] if obj_match else "unknown"
# # # # # # #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # # # #                         # Convert failed action to ActionStep
# # # # # # #                         if isinstance(failed_action_eai, dict):
# # # # # # #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # # # #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # # # #                             failed_obj2 = failed_action_eai.get("o2")
# # # # # # #                         else:
# # # # # # #                             fa_str = str(failed_action_eai)
# # # # # # #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# # # # # # #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# # # # # # #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # # #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# # # # # # #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # # #                         failed_step = ActionStep(
# # # # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # # # #                         )

# # # # # # #                         # All steps as ActionStep
# # # # # # #                         all_steps = exec_steps + [failed_step]

# # # # # # #                         # Run SDA diagnosis
# # # # # # #                         diagnosis = diagnose_error(
# # # # # # #                             action_history = exec_steps,
# # # # # # #                             failed_step    = failed_step,
# # # # # # #                             error_type     = error_type_str,
# # # # # # #                             full_plan      = all_steps,
# # # # # # #                         )

# # # # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # # # #                         # Build feedback prompt
# # # # # # #                         current_prompt = build_feedback_prompt(
# # # # # # #                             original_prompt  = prompt,
# # # # # # #                             executed_actions = history_actions,
# # # # # # #                             failed_action    = failed_action_eai,
# # # # # # #                             error_type       = error_type_str,
# # # # # # #                             diagnosis        = diagnosis,
# # # # # # #                         )

# # # # # # #                     break  # break action loop, retry with new prompt

# # # # # # #                 else:
# # # # # # #                     history_actions.append(action)
# # # # # # #                     history_env_states.append(
# # # # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # # # #                     )

# # # # # # #             if executable:
# # # # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # # # #                 break

# # # # # # #             if attempt == MAX_REPLAN:
# # # # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # # # #         return raw_output, replan_count

# # # # # # #     def _save_outputs(self, outputs: list):
# # # # # # #         """Save outputs in EAI format."""
# # # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # # # #         with open(out_path, "w") as f:
# # # # # # #             json.dump(outputs, f, indent=4)
# # # # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # # # ─────────────────────────────────────────────
# # # # # # # # Entry Point
# # # # # # # # ─────────────────────────────────────────────

# # # # # # # if __name__ == "__main__":
# # # # # # #     if not GEMINI_API_KEY:
# # # # # # #         print("ERROR: GEMINI_API_KEY environment variable not set!")
# # # # # # #         print("Run: export GEMINI_API_KEY='your_key_here'")
# # # # # # #         sys.exit(1)

# # # # # # #     runner = EAISDARunner()
# # # # # # #     runner.run_all()
# # # # # # """
# # # # # # eai_sda_runner.py
# # # # # # =================
# # # # # # Runs INSIDE Docker container.
# # # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # # Steps:
# # # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # # 3. Parse LLM response into EAI action format
# # # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # # Usage (inside Docker):
# # # # # #     pip install groq
# # # # # #     export GEMINI_API_KEY="your_key_here"
# # # # # #     python3 eai_sda_runner.py
# # # # # # """

# # # # # # import os
# # # # # # import sys
# # # # # # import json
# # # # # # import copy
# # # # # # import re
# # # # # # import logging
# # # # # # import os.path as osp

# # # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # # #     construct_planner,
# # # # # #     json_to_action,
# # # # # #     check_action_grammar,
# # # # # #     check_no_hallucination_in_action,
# # # # # #     check_no_hallucination_in_arg,
# # # # # #     load_json_preserving_order,
# # # # # # )
# # # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # # from sdg import get_preconditions, explain_precondition
# # # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # # ── Gemini ───────────────────────────────────────────────────────────────────
# # # # # # import google.generativeai as genai

# # # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # # logging.basicConfig(
# # # # # #     level=logging.INFO,
# # # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # # )
# # # # # # logger = logging.getLogger(__name__)

# # # # # # # ─────────────────────────────────────────────
# # # # # # # Configuration
# # # # # # # ─────────────────────────────────────────────

# # # # # # # ↓↓↓ PASTE YOUR GEMINI API KEY HERE ↓↓↓
# # # # # # GEMINI_API_KEY    = "AIzaSyAR3kVJqSb_9RLQ62UItGF4n6z22nWb4G0"
# # # # # # # ↑↑↑ PASTE YOUR GEMINI API KEY HERE ↑↑↑
# # # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # # MAX_REPLAN        = 3
# # # # # # SCENEGRAPH_ID     = 1
# # # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # # EAI error codes
# # # # # # ERROR_CODE_TO_TYPE = {
# # # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # # #     1: "MISSING_STEP",
# # # # # #     2: "AFFORDANCE_ERROR",
# # # # # #     3: "UNSEEN_OBJECT",
# # # # # #     4: "ADDITIONAL_STEP",
# # # # # #     5: "UNKNOWN_ERROR",
# # # # # # }

# # # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # # Generate executable action sequences for household tasks.

# # # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # # VALID ACTIONS ONLY (use no others):
# # # # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # # # Rules:
# # # # # # - WALK to object before GRAB
# # # # # # - OPEN containers before PUTIN
# # # # # # - Max 2 objects held at once
# # # # # # - No explanations, ONLY the JSON object
# # # # # # """


# # # # # # # ─────────────────────────────────────────────
# # # # # # # Groq LLM Client
# # # # # # # ─────────────────────────────────────────────

# # # # # # class GroqClient:
# # # # # #     def __init__(self):
# # # # # #         from google import genai as google_genai
# # # # # #         self.client = google_genai.Client(api_key=GEMINI_API_KEY)

# # # # # #     def call(self, user_prompt: str) -> str:
# # # # # #         try:
# # # # # #             from google import genai as google_genai
# # # # # #             full_prompt = SYSTEM_PROMPT + " " + user_prompt
# # # # # #             response = self.client.models.generate_content(
# # # # # #                 model=MODEL,
# # # # # #                 contents=full_prompt,
# # # # # #             )
# # # # # #             return response.text.strip()
# # # # # #         except Exception as e:
# # # # # #             logger.error(f"Gemini API error: {e}")
# # # # # #             return ""


# # # # # # # ─────────────────────────────────────────────
# # # # # # # Action Format Converters
# # # # # # # ─────────────────────────────────────────────

# # # # # # def parse_llm_output(raw: str) -> list:
# # # # # #     """
# # # # # #     Parse LLM JSON output into EAI action list format.
# # # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # # #     """
# # # # # #     # Strip markdown fences
# # # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # # #     # Try to extract JSON object
# # # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # # #     if not match:
# # # # # #         logger.warning("No JSON found in LLM output")
# # # # # #         return []

# # # # # #     raw = match.group(0)

# # # # # #     try:
# # # # # #         # Use EAI's own parser
# # # # # #         parsed = load_json_preserving_order(raw)
# # # # # #         return parsed
# # # # # #     except Exception as e:
# # # # # #         logger.warning(f"JSON parse error: {e}")
# # # # # #         return []


# # # # # # def actions_to_eai_format(actions_json) -> list:
# # # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # # #     if not actions_json:
# # # # # #         return []
# # # # # #     # EAI handles this via json_to_action after validation
# # # # # #     return actions_json


# # # # # # def build_feedback_prompt(
# # # # # #     original_prompt: str,
# # # # # #     executed_actions: list,
# # # # # #     failed_action,
# # # # # #     error_type: str,
# # # # # #     diagnosis,
# # # # # # ) -> str:
# # # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # # #     unsatisfied_str = "\n".join(
# # # # # #         f"  - {explain_precondition(p)}"
# # # # # #         for p in diagnosis.unsatisfied_needs
# # # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # # #     feedback = f"""
# # # # # # {original_prompt}

# # # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # # Your previous plan failed at action: {failed_action}
# # # # # # Error type: {error_type}

# # # # # # Successfully executed steps so far:
# # # # # # {executed_str}

# # # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # # Unsatisfied preconditions:
# # # # # # {unsatisfied_str}

# # # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # # === INSTRUCTIONS ===
# # # # # # Generate a NEW complete action sequence that:
# # # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # # 2. Completes the full task from the beginning
# # # # # # 3. Ensures every action's preconditions are met

# # # # # # Respond with ONLY the JSON action sequence.
# # # # # # """
# # # # # #     return feedback.strip()


# # # # # # # ─────────────────────────────────────────────
# # # # # # # SDA + EAI Runner
# # # # # # # ─────────────────────────────────────────────

# # # # # # class EAISDARunner:

# # # # # #     def __init__(self):
# # # # # #         self.llm = GroqClient()

# # # # # #         # Load EAI resources
# # # # # #         self.properties_data  = utils.load_properties_data()
# # # # # #         self.object_placing   = utils.load_object_placing()
# # # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # # #     def run_all(self):
# # # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # # #         outputs      = []
# # # # # #         success      = 0
# # # # # #         total        = 0
# # # # # #         replan_total = 0

# # # # # #         for task_name, task_files in self.task_dicts.items():
# # # # # #             for file_id, task_goal_dict in task_files.items():
# # # # # #                 total += 1
# # # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # # #                 result, replan_count = self.run_single_task(
# # # # # #                     file_id, task_name, task_goal_dict
# # # # # #                 )
# # # # # #                 replan_total += replan_count

# # # # # #                 outputs.append({
# # # # # #                     "identifier": file_id,
# # # # # #                     "llm_output": result,
# # # # # #                 })

# # # # # #                 if total % 10 == 0:
# # # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # # #                     self._save_outputs(outputs)

# # # # # #         # Save final outputs
# # # # # #         self._save_outputs(outputs)

# # # # # #         logger.info(f"\n=== DONE ===")
# # # # # #         logger.info(f"Total tasks    : {total}")
# # # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # # #         return outputs

# # # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # # #         goals        = task_goal_dict["vh_goal"]
# # # # # #         action_goals = goals["actions"]
# # # # # #         scene_goals  = goals["goal"]

# # # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # # #         # Build EAI motion planner for this task
# # # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # # #             self.name_equivalence,
# # # # # #             self.properties_data,
# # # # # #             self.object_placing,
# # # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # # #             script_id     = file_id,
# # # # # #             dataset_root  = DATA_DIR,
# # # # # #         )

# # # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # # #         )

# # # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # # #         # Build EAI prompt
# # # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # # #         prompt = one_shot.prompt
# # # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # # #         replan_count  = 0
# # # # # #         current_prompt = prompt

# # # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # # #             raw_output = self.llm.call(current_prompt)
# # # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # # #             if not parsed_actions:
# # # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # # #                 break

# # # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # # #             # Filter out actions EAI does not recognise (e.g. PLUGIN)
# # # # # #             EAI_VALID = {
# # # # # #                 "DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE",
# # # # # #                 "PUSH","PULL","MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE",
# # # # # #                 "SCRUB","WASH","GRAB","SWITCHOFF","SWITCHON","CLOSE","FIND",
# # # # # #                 "WALK","OPEN","POINTAT","PUTBACK","PUTIN","PUTOBJBACK","RUN",
# # # # # #                 "SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF","GREET",
# # # # # #                 "DROP","LIE","POUR",
# # # # # #             }
# # # # # #             if isinstance(parsed_actions, dict):
# # # # # #                 parsed_actions = {k: v for k, v in parsed_actions.items()
# # # # # #                                   if k.upper() in EAI_VALID}
# # # # # #             elif isinstance(parsed_actions, list):
# # # # # #                 parsed_actions = [a for a in parsed_actions
# # # # # #                                   if list(a.keys())[0].upper() in EAI_VALID]
# # # # # #             if not parsed_actions:
# # # # # #                 logger.warning(f"  All actions filtered for {file_id}")
# # # # # #                 break
# # # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # # #             if not pass_check:
# # # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # # #                 break

# # # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # # #             actions = json_to_action(
# # # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # # #             )

# # # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # # #             motion_planner.reset()
# # # # # #             history_actions     = []
# # # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # # #             executable          = True
# # # # # #             failed_error_code   = None
# # # # # #             failed_action_eai   = None

# # # # # #             for action in actions:
# # # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # # #                 if not exe_flag:
# # # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # # #                     executable        = False
# # # # # #                     failed_action_eai = action
# # # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # # #                     formal_info       = checker.run_checker()
# # # # # #                     failed_error_code = formal_info.get_error_type()
# # # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # # #                     if attempt < MAX_REPLAN:
# # # # # #                         replan_count += 1

# # # # # #                         # Convert executed history to ActionStep objects
# # # # # #                         exec_steps = []
# # # # # #                         for i, a in enumerate(history_actions):
# # # # # #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# # # # # #                             # or could be a dict - handle both
# # # # # #                             if isinstance(a, dict):
# # # # # #                                 action_name = a.get("action", "UNKNOWN").upper()
# # # # # #                                 obj1 = a.get("o1", "unknown")
# # # # # #                                 obj2 = a.get("o2")
# # # # # #                             else:
# # # # # #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# # # # # #                                 a_str = str(a)
# # # # # #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# # # # # #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# # # # # #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # #                                 obj1 = obj_match[0] if obj_match else "unknown"
# # # # # #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # # #                         # Convert failed action to ActionStep
# # # # # #                         if isinstance(failed_action_eai, dict):
# # # # # #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # # #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # # #                             failed_obj2 = failed_action_eai.get("o2")
# # # # # #                         else:
# # # # # #                             fa_str = str(failed_action_eai)
# # # # # #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# # # # # #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# # # # # #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # # #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# # # # # #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # # #                         failed_step = ActionStep(
# # # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # # #                         )

# # # # # #                         # All steps as ActionStep
# # # # # #                         all_steps = exec_steps + [failed_step]

# # # # # #                         # Run SDA diagnosis
# # # # # #                         diagnosis = diagnose_error(
# # # # # #                             action_history = exec_steps,
# # # # # #                             failed_step    = failed_step,
# # # # # #                             error_type     = error_type_str,
# # # # # #                             full_plan      = all_steps,
# # # # # #                         )

# # # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # # #                         # Build feedback prompt
# # # # # #                         current_prompt = build_feedback_prompt(
# # # # # #                             original_prompt  = prompt,
# # # # # #                             executed_actions = history_actions,
# # # # # #                             failed_action    = failed_action_eai,
# # # # # #                             error_type       = error_type_str,
# # # # # #                             diagnosis        = diagnosis,
# # # # # #                         )

# # # # # #                     break  # break action loop, retry with new prompt

# # # # # #                 else:
# # # # # #                     history_actions.append(action)
# # # # # #                     history_env_states.append(
# # # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # # #                     )

# # # # # #             if executable:
# # # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # # #                 break

# # # # # #             if attempt == MAX_REPLAN:
# # # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # # #         return raw_output, replan_count

# # # # # #     def _save_outputs(self, outputs: list):
# # # # # #         """Save outputs in EAI format."""
# # # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # # #         with open(out_path, "w") as f:
# # # # # #             json.dump(outputs, f, indent=4)
# # # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # # ─────────────────────────────────────────────
# # # # # # # Entry Point
# # # # # # # ─────────────────────────────────────────────

# # # # # # if __name__ == "__main__":
# # # # # #     if not GEMINI_API_KEY:
# # # # # #         print("ERROR: GEMINI_API_KEY environment variable not set!")
# # # # # #         print("Run: export GEMINI_API_KEY='your_key_here'")
# # # # # #         sys.exit(1)

# # # # # #     runner = EAISDARunner()
# # # # # #     runner.run_all()
# # # # # """
# # # # # eai_sda_runner.py
# # # # # =================
# # # # # Runs INSIDE Docker container.
# # # # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # # # Steps:
# # # # # 1. Generate prompts using EAI's generate_prompts
# # # # # 2. Send each prompt to Llama 3.3 70B via Groq API
# # # # # 3. Parse LLM response into EAI action format
# # # # # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # # # # 5. On failure → SDA diagnosis → feedback → replan
# # # # # 6. Save outputs in EAI format for evaluate_results.py

# # # # # Usage (inside Docker):
# # # # #     pip install groq
# # # # #     export GEMINI_API_KEY="your_key_here"
# # # # #     python3 eai_sda_runner.py
# # # # # """

# # # # # import os
# # # # # import sys
# # # # # import json
# # # # # import copy
# # # # # import re
# # # # # import logging
# # # # # import os.path as osp

# # # # # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # # # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # # # # ── EAI imports ──────────────────────────────────────────────────────────────
# # # # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # # # #     construct_planner,
# # # # #     json_to_action,
# # # # #     check_action_grammar,
# # # # #     check_no_hallucination_in_action,
# # # # #     check_no_hallucination_in_arg,
# # # # #     load_json_preserving_order,
# # # # # )
# # # # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # # # # ── SDA imports ──────────────────────────────────────────────────────────────
# # # # # from sdg import get_preconditions, explain_precondition
# # # # # from error_diagnosis import ActionStep, diagnose_error

# # # # # # ── HTTP requests for Gemini REST API (no library needed) ───────────────────
# # # # # import urllib.request

# # # # # # ── Logging ──────────────────────────────────────────────────────────────────
# # # # # logging.basicConfig(
# # # # #     level=logging.INFO,
# # # # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # # # )
# # # # # logger = logging.getLogger(__name__)

# # # # # # ─────────────────────────────────────────────
# # # # # # Configuration
# # # # # # ─────────────────────────────────────────────

# # # # # # ↓↓↓ PASTE YOUR GEMINI API KEY HERE ↓↓↓
# # # # # GEMINI_API_KEY    = "AIzaSyAR3kVJqSb_9RLQ62UItGF4n6z22nWb4G0"
# # # # # # ↑↑↑ PASTE YOUR GEMINI API KEY HERE ↑↑↑
# # # # # MODEL             = "llama-3.3-70b-versatile"
# # # # # MAX_REPLAN        = 3
# # # # # SCENEGRAPH_ID     = 1
# # # # # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # # # # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # # # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # # # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # # # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # # # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # # # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # # # # EAI error codes
# # # # # ERROR_CODE_TO_TYPE = {
# # # # #     0: "WRONG_TEMPORAL_ORDER",
# # # # #     1: "MISSING_STEP",
# # # # #     2: "AFFORDANCE_ERROR",
# # # # #     3: "UNSEEN_OBJECT",
# # # # #     4: "ADDITIONAL_STEP",
# # # # #     5: "UNKNOWN_ERROR",
# # # # # }

# # # # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # # # # Generate executable action sequences for household tasks.

# # # # # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # # # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # # # VALID ACTIONS ONLY (use no others):
# # # # # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # # # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # # # Rules:
# # # # # - WALK to object before GRAB
# # # # # - OPEN containers before PUTIN
# # # # # - Max 2 objects held at once
# # # # # - No explanations, ONLY the JSON object
# # # # # """


# # # # # # ─────────────────────────────────────────────
# # # # # # Groq LLM Client
# # # # # # ─────────────────────────────────────────────

# # # # # class GroqClient:
# # # # #     def __init__(self):
# # # # #         self.api_key = GEMINI_API_KEY
# # # # #         self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"

# # # # #     def call(self, user_prompt: str) -> str:
# # # # #         import json as _json
# # # # #         import urllib.request as _req
# # # # #         full_prompt = SYSTEM_PROMPT + " " + user_prompt
# # # # #         payload = _json.dumps({
# # # # #             "contents": [{"parts": [{"text": full_prompt}]}],
# # # # #             "generationConfig": {"temperature": 0, "maxOutputTokens": 1024}
# # # # #         }).encode("utf-8")
# # # # #         try:
# # # # #             req = _req.Request(self.url, data=payload,
# # # # #                                headers={"Content-Type": "application/json"})
# # # # #             with _req.urlopen(req, timeout=30) as resp:
# # # # #                 data = _json.loads(resp.read().decode())
# # # # #                 return data["candidates"][0]["content"]["parts"][0]["text"].strip()
# # # # #         except Exception as e:
# # # # #             logger.error(f"Gemini API error: {e}")
# # # # #             return ""


# # # # # # ─────────────────────────────────────────────
# # # # # # Action Format Converters
# # # # # # ─────────────────────────────────────────────

# # # # # def parse_llm_output(raw: str) -> list:
# # # # #     """
# # # # #     Parse LLM JSON output into EAI action list format.
# # # # #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# # # # #     """
# # # # #     # Strip markdown fences
# # # # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# # # # #     # Try to extract JSON object
# # # # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # # # #     if not match:
# # # # #         logger.warning("No JSON found in LLM output")
# # # # #         return []

# # # # #     raw = match.group(0)

# # # # #     try:
# # # # #         # Use EAI's own parser
# # # # #         parsed = load_json_preserving_order(raw)
# # # # #         return parsed
# # # # #     except Exception as e:
# # # # #         logger.warning(f"JSON parse error: {e}")
# # # # #         return []


# # # # # def actions_to_eai_format(actions_json) -> list:
# # # # #     """Convert parsed JSON to EAI internal action format."""
# # # # #     if not actions_json:
# # # # #         return []
# # # # #     # EAI handles this via json_to_action after validation
# # # # #     return actions_json


# # # # # def build_feedback_prompt(
# # # # #     original_prompt: str,
# # # # #     executed_actions: list,
# # # # #     failed_action,
# # # # #     error_type: str,
# # # # #     diagnosis,
# # # # # ) -> str:
# # # # #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# # # # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # # # #     unsatisfied_str = "\n".join(
# # # # #         f"  - {explain_precondition(p)}"
# # # # #         for p in diagnosis.unsatisfied_needs
# # # # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # # # #     feedback = f"""
# # # # # {original_prompt}

# # # # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # # # Your previous plan failed at action: {failed_action}
# # # # # Error type: {error_type}

# # # # # Successfully executed steps so far:
# # # # # {executed_str}

# # # # # Root cause of failure: {diagnosis.root_cause}
# # # # # Unsatisfied preconditions:
# # # # # {unsatisfied_str}

# # # # # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # # # # === INSTRUCTIONS ===
# # # # # Generate a NEW complete action sequence that:
# # # # # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # # # # 2. Completes the full task from the beginning
# # # # # 3. Ensures every action's preconditions are met

# # # # # Respond with ONLY the JSON action sequence.
# # # # # """
# # # # #     return feedback.strip()


# # # # # # ─────────────────────────────────────────────
# # # # # # SDA + EAI Runner
# # # # # # ─────────────────────────────────────────────

# # # # # class EAISDARunner:

# # # # #     def __init__(self):
# # # # #         self.llm = GroqClient()

# # # # #         # Load EAI resources
# # # # #         self.properties_data  = utils.load_properties_data()
# # # # #         self.object_placing   = utils.load_object_placing()
# # # # #         self.name_equivalence = utils.load_name_equivalence()
# # # # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # # # #         self.id2task          = json.load(open(ID2TASK_PATH))

# # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# # # # #     def run_all(self):
# # # # #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# # # # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # # # #         logger.info(f"Model: {MODEL_NAME}")
# # # # #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# # # # #         outputs      = []
# # # # #         success      = 0
# # # # #         total        = 0
# # # # #         replan_total = 0

# # # # #         for task_name, task_files in self.task_dicts.items():
# # # # #             for file_id, task_goal_dict in task_files.items():
# # # # #                 total += 1
# # # # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # # # #                 result, replan_count = self.run_single_task(
# # # # #                     file_id, task_name, task_goal_dict
# # # # #                 )
# # # # #                 replan_total += replan_count

# # # # #                 outputs.append({
# # # # #                     "identifier": file_id,
# # # # #                     "llm_output": result,
# # # # #                 })

# # # # #                 if total % 10 == 0:
# # # # #                     logger.info(f"Progress: {total} tasks done")
# # # # #                     self._save_outputs(outputs)

# # # # #         # Save final outputs
# # # # #         self._save_outputs(outputs)

# # # # #         logger.info(f"\n=== DONE ===")
# # # # #         logger.info(f"Total tasks    : {total}")
# # # # #         logger.info(f"Total replans  : {replan_total}")
# # # # #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# # # # #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# # # # #         return outputs

# # # # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # # # #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# # # # #         goals        = task_goal_dict["vh_goal"]
# # # # #         action_goals = goals["actions"]
# # # # #         scene_goals  = goals["goal"]

# # # # #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# # # # #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# # # # #         # Build EAI motion planner for this task
# # # # #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# # # # #             self.name_equivalence,
# # # # #             self.properties_data,
# # # # #             self.object_placing,
# # # # #             scenegraph_id = SCENEGRAPH_ID,
# # # # #             script_id     = file_id,
# # # # #             dataset_root  = DATA_DIR,
# # # # #         )

# # # # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # # # #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# # # # #         )

# # # # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # # # #         # Build EAI prompt
# # # # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # # # #         prompt = one_shot.prompt
# # # # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # # # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # # # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # # # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # # # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # # # #         replan_count  = 0
# # # # #         current_prompt = prompt

# # # # #         for attempt in range(MAX_REPLAN + 1):
# # # # #             # ── Call LLM ────────────────────────────────────────────────────
# # # # #             raw_output = self.llm.call(current_prompt)
# # # # #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# # # # #             # ── Parse output ─────────────────────────────────────────────────
# # # # #             parsed_actions = parse_llm_output(raw_output)

# # # # #             if not parsed_actions:
# # # # #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# # # # #                 break

# # # # #             # ── Validate format ───────────────────────────────────────────────
# # # # #             # Filter out actions EAI does not recognise (e.g. PLUGIN)
# # # # #             EAI_VALID = {
# # # # #                 "DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE",
# # # # #                 "PUSH","PULL","MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE",
# # # # #                 "SCRUB","WASH","GRAB","SWITCHOFF","SWITCHON","CLOSE","FIND",
# # # # #                 "WALK","OPEN","POINTAT","PUTBACK","PUTIN","PUTOBJBACK","RUN",
# # # # #                 "SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF","GREET",
# # # # #                 "DROP","LIE","POUR",
# # # # #             }
# # # # #             if isinstance(parsed_actions, dict):
# # # # #                 parsed_actions = {k: v for k, v in parsed_actions.items()
# # # # #                                   if k.upper() in EAI_VALID}
# # # # #             elif isinstance(parsed_actions, list):
# # # # #                 parsed_actions = [a for a in parsed_actions
# # # # #                                   if list(a.keys())[0].upper() in EAI_VALID]
# # # # #             if not parsed_actions:
# # # # #                 logger.warning(f"  All actions filtered for {file_id}")
# # # # #                 break
# # # # #             pass_check, _ = check_action_grammar(parsed_actions)
# # # # #             if not pass_check:
# # # # #                 logger.warning(f"  Grammar check failed for {file_id}")
# # # # #                 break

# # # # #             # ── Convert to EAI internal format ────────────────────────────────
# # # # #             actions = json_to_action(
# # # # #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# # # # #             )

# # # # #             # ── Simulate with EAI motion planner ──────────────────────────────
# # # # #             motion_planner.reset()
# # # # #             history_actions     = []
# # # # #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # # # #             executable          = True
# # # # #             failed_error_code   = None
# # # # #             failed_action_eai   = None

# # # # #             for action in actions:
# # # # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # # # #                 if not exe_flag:
# # # # #                     # ── Action failed! ────────────────────────────────────────
# # # # #                     executable        = False
# # # # #                     failed_action_eai = action
# # # # #                     history_cp        = copy.deepcopy(history_env_states)

# # # # #                     checker           = TemporalOrderChecker(my_info, history_cp)
# # # # #                     formal_info       = checker.run_checker()
# # # # #                     failed_error_code = formal_info.get_error_type()
# # # # #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# # # # #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# # # # #                     # ── SDA Diagnosis ─────────────────────────────────────────
# # # # #                     if attempt < MAX_REPLAN:
# # # # #                         replan_count += 1

# # # # #                         # Convert executed history to ActionStep objects
# # # # #                         exec_steps = []
# # # # #                         for i, a in enumerate(history_actions):
# # # # #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# # # # #                             # or could be a dict - handle both
# # # # #                             if isinstance(a, dict):
# # # # #                                 action_name = a.get("action", "UNKNOWN").upper()
# # # # #                                 obj1 = a.get("o1", "unknown")
# # # # #                                 obj2 = a.get("o2")
# # # # #                             else:
# # # # #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# # # # #                                 a_str = str(a)
# # # # #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# # # # #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# # # # #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # #                                 obj1 = obj_match[0] if obj_match else "unknown"
# # # # #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# # # # #                         # Convert failed action to ActionStep
# # # # #                         if isinstance(failed_action_eai, dict):
# # # # #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# # # # #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# # # # #                             failed_obj2 = failed_action_eai.get("o2")
# # # # #                         else:
# # # # #                             fa_str = str(failed_action_eai)
# # # # #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# # # # #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# # # # #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # # # #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# # # # #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# # # # #                         failed_step = ActionStep(
# # # # #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# # # # #                         )

# # # # #                         # All steps as ActionStep
# # # # #                         all_steps = exec_steps + [failed_step]

# # # # #                         # Run SDA diagnosis
# # # # #                         diagnosis = diagnose_error(
# # # # #                             action_history = exec_steps,
# # # # #                             failed_step    = failed_step,
# # # # #                             error_type     = error_type_str,
# # # # #                             full_plan      = all_steps,
# # # # #                         )

# # # # #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# # # # #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# # # # #                         # Build feedback prompt
# # # # #                         current_prompt = build_feedback_prompt(
# # # # #                             original_prompt  = prompt,
# # # # #                             executed_actions = history_actions,
# # # # #                             failed_action    = failed_action_eai,
# # # # #                             error_type       = error_type_str,
# # # # #                             diagnosis        = diagnosis,
# # # # #                         )

# # # # #                     break  # break action loop, retry with new prompt

# # # # #                 else:
# # # # #                     history_actions.append(action)
# # # # #                     history_env_states.append(
# # # # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # # # #                     )

# # # # #             if executable:
# # # # #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# # # # #                 break

# # # # #             if attempt == MAX_REPLAN:
# # # # #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# # # # #         # Return raw_output string for EAI evaluate_results to process
# # # # #         return raw_output, replan_count

# # # # #     def _save_outputs(self, outputs: list):
# # # # #         """Save outputs in EAI format."""
# # # # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # # # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # # # #         with open(out_path, "w") as f:
# # # # #             json.dump(outputs, f, indent=4)
# # # # #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # # # # ─────────────────────────────────────────────
# # # # # # Entry Point
# # # # # # ─────────────────────────────────────────────

# # # # # if __name__ == "__main__":
# # # # #     if not GEMINI_API_KEY:
# # # # #         print("ERROR: GEMINI_API_KEY environment variable not set!")
# # # # #         print("Run: export GEMINI_API_KEY='your_key_here'")
# # # # #         sys.exit(1)

# # # # #     runner = EAISDARunner()
# # # # #     runner.run_all()
# # # """
# # # eai_sda_runner.py
# # # =================
# # # Runs INSIDE Docker container.
# # # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # # Usage (inside Docker):
# # #     python3 sda_eai/eai_sda_runner.py
# # # """

# # # import os
# # # import sys
# # # import json
# # # import copy
# # # import re
# # # import logging
# # # import os.path as osp
# # # import urllib.request
# # # import urllib.error

# # # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# # #     construct_planner,
# # #     json_to_action,
# # #     check_action_grammar,
# # #     load_json_preserving_order,
# # # )
# # # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker
# # # from sdg import explain_precondition
# # # from error_diagnosis import ActionStep, diagnose_error

# # # logging.basicConfig(
# # #     level=logging.INFO,
# # #     format="%(asctime)s - %(levelname)s - %(message)s",
# # # )
# # # logger = logging.getLogger(__name__)


# # # # =============================================================================
# # # # CONFIGURATION
# # # # =============================================================================

# # # # vvv PASTE YOUR GEMINI API KEY HERE vvv
# # # GEMINI_API_KEY = "AIzaSyD9_yyQR4gdpducKL6GokaodXfnWd0UTg4"
# # # # ^^^ PASTE YOUR GEMINI API KEY HERE ^^^

# # # GEMINI_MODEL  = "gemini-2.5-flash-lite"
# # # MODEL_NAME    = "gemini-2.5-flash-lite-sda"
# # # MAX_REPLAN    = 3
# # # SCENEGRAPH_ID = 1

# # # RESOURCE_DIR   = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # # DATASET_DIR    = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # # OUTPUT_DIR     = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # # TASK_DICT_PATH = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # # ID2TASK_PATH   = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # # DATA_DIR       = osp.join(DATASET_DIR,  "programs_processed_precond_nograb_morepreconds")

# # # # =============================================================================

# # # ERROR_CODE_TO_TYPE = {
# # #     0: "WRONG_TEMPORAL_ORDER",
# # #     1: "MISSING_STEP",
# # #     2: "AFFORDANCE_ERROR",
# # #     3: "UNSEEN_OBJECT",
# # #     4: "ADDITIONAL_STEP",
# # #     5: "UNKNOWN_ERROR",
# # # }

# # # EAI_VALID_ACTIONS = {
# # #     "DRINK", "EAT", "CUT", "TOUCH", "LOOKAT", "WATCH", "READ", "TYPE",
# # #     "PUSH", "PULL", "MOVE", "SQUEEZE", "SLEEP", "WAKEUP", "RINSE",
# # #     "SCRUB", "WASH", "GRAB", "SWITCHOFF", "SWITCHON", "CLOSE", "FIND",
# # #     "WALK", "OPEN", "POINTAT", "PUTBACK", "PUTIN", "PUTOBJBACK", "RUN",
# # #     "SIT", "STANDUP", "TURNTO", "WIPE", "PUTON", "PUTOFF", "GREET",
# # #     "DROP", "LIE", "POUR",
# # # }

# # # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.

# # # OUTPUT FORMAT - respond with ONLY a JSON object, no explanation:
# # # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # # VALID ACTIONS ONLY:
# # # - 1 argument : DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE,
# # #                SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN,
# # #                PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, SIT, PUTOBJBACK, RUN
# # # - 2 arguments: PUTBACK, PUTIN, POUR
# # # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # # STRICT RULES:
# # # - Do NOT use PLUGIN or PLUGOUT - all devices are already plugged in
# # # - Do NOT use OPEN on washing machines - they cannot be opened
# # # - WALK to an object before GRABbing it
# # # - OPEN containers before PUTIN
# # # - Maximum 2 objects held at once
# # # - Output ONLY the JSON, nothing else"""


# # # # =============================================================================
# # # # Gemini REST Client
# # # # =============================================================================

# # # class GeminiClient:
# # #     def __init__(self):
# # #         self.url = (
# # #             f"https://generativelanguage.googleapis.com/v1beta/models/"
# # #             f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
# # #         )

# # #     def call(self, user_prompt: str) -> str:
# # #         full_prompt = SYSTEM_PROMPT + "\n\n" + user_prompt
# # #         payload = json.dumps({
# # #             "contents": [{"parts": [{"text": full_prompt}]}],
# # #             "generationConfig": {
# # #                 "temperature": 0.0,
# # #                 "maxOutputTokens": 1024,
# # #             }
# # #         }).encode("utf-8")

# # #         try:
# # #             req = urllib.request.Request(
# # #                 self.url,
# # #                 data    = payload,
# # #                 headers = {"Content-Type": "application/json"},
# # #                 method  = "POST",
# # #             )
# # #             with urllib.request.urlopen(req, timeout=30) as resp:
# # #                 data = json.loads(resp.read().decode("utf-8"))
# # #                 return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# # #         except urllib.error.HTTPError as e:
# # #             body = e.read().decode("utf-8")
# # #             logger.error(f"Gemini HTTP {e.code}: {body[:300]}")
# # #             return ""
# # #         except Exception as e:
# # #             logger.error(f"Gemini API error: {e}")
# # #             return ""


# # # # =============================================================================
# # # # Helper Functions
# # # # =============================================================================

# # # def parse_llm_output(raw: str):
# # #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
# # #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# # #     if not match:
# # #         logger.warning("No JSON found in LLM output")
# # #         return []
# # #     try:
# # #         return load_json_preserving_order(match.group(0))
# # #     except Exception as e:
# # #         logger.warning(f"JSON parse error: {e}")
# # #         return []


# # # def filter_valid_actions(parsed):
# # #     if isinstance(parsed, dict):
# # #         return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
# # #     elif isinstance(parsed, list):
# # #         return [a for a in parsed if list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
# # #     return parsed


# # # def parse_eai_action_to_step(action, index: int) -> ActionStep:
# # #     if isinstance(action, dict):
# # #         name = action.get("action", "UNKNOWN").upper()
# # #         obj1 = action.get("o1", "unknown")
# # #         obj2 = action.get("o2")
# # #         return ActionStep(index, name, obj1, obj2)
# # #     s = str(action)
# # #     action_match = re.search(r'\[(\w+)\]', s)
# # #     obj_matches  = re.findall(r'<([^>]+)>', s)
# # #     name = action_match.group(1).upper() if action_match else "UNKNOWN"
# # #     obj1 = obj_matches[0] if obj_matches else "unknown"
# # #     obj2 = obj_matches[1] if len(obj_matches) > 1 else None
# # #     return ActionStep(index, name, obj1, obj2)


# # # def build_feedback_prompt(original_prompt, executed_actions, failed_action, error_type, diagnosis):
# # #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# # #     unsatisfied_str = "\n".join(
# # #         f"  - {explain_precondition(p)}" for p in diagnosis.unsatisfied_needs
# # #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# # #     return f"""{original_prompt}

# # # === EXECUTION FEEDBACK (SDA-Planner) ===

# # # Your previous plan FAILED at: {failed_action}
# # # Error type    : {error_type}
# # # Root cause    : {diagnosis.root_cause}

# # # Unsatisfied preconditions that caused the failure:
# # # {unsatisfied_str}

# # # Steps that executed successfully before failure:
# # # {executed_str}

# # # === REPLANNING INSTRUCTIONS ===
# # # Generate a NEW complete action sequence that:
# # # 1. Satisfies the missing preconditions listed above
# # # 2. Completes the full task from the beginning
# # # 3. Ensures every action's preconditions are met by prior actions

# # # Respond with ONLY the JSON action sequence."""


# # # # =============================================================================
# # # # Main Runner
# # # # =============================================================================

# # # class EAISDARunner:

# # #     def __init__(self):
# # #         self.llm = GeminiClient()
# # #         logger.info("Loading EAI resources...")
# # #         self.properties_data  = utils.load_properties_data()
# # #         self.object_placing   = utils.load_object_placing()
# # #         self.name_equivalence = utils.load_name_equivalence()
# # #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# # #         self.id2task          = json.load(open(ID2TASK_PATH))
# # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # #         logger.info("EAI resources loaded.")

# # #     def run_all(self):
# # #         logger.info("=== EAI + SDA-Planner Runner ===")
# # #         logger.info(f"Model     : {MODEL_NAME}")
# # #         logger.info(f"Max replan: {MAX_REPLAN}")
# # #         logger.info(f"Output dir: {OUTPUT_DIR}")

# # #         # Resume support
# # #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # #         if osp.exists(out_path):
# # #             existing = json.load(open(out_path))
# # #             done_ids = {
# # #                 d["identifier"] for d in existing
# # #                 if d["llm_output"] and d["llm_output"] not in ("", "...")
# # #             }
# # #             outputs = [d for d in existing if d["identifier"] in done_ids]
# # #             logger.info(f"Resuming: {len(outputs)} tasks already done")
# # #         else:
# # #             outputs, done_ids = [], set()

# # #         total = replan_total = 0

# # #         for task_name, task_files in self.task_dicts.items():
# # #             for file_id, task_goal_dict in task_files.items():

# # #                 if file_id in done_ids:
# # #                     logger.info(f"  Skipping {file_id} (already done)")
# # #                     continue

# # #                 total += 1
# # #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# # #                 result, rc = self.run_single_task(file_id, task_name, task_goal_dict)
# # #                 replan_total += rc
# # #                 outputs.append({"identifier": file_id, "llm_output": result})

# # #                 if total % 10 == 0:
# # #                     self._save_outputs(outputs)
# # #                     logger.info(f"Checkpoint saved at {total} tasks")

# # #         self._save_outputs(outputs)
# # #         logger.info(f"\n=== DONE ===")
# # #         logger.info(f"Total tasks  : {total}")
# # #         logger.info(f"Total replans: {replan_total}")
# # #         logger.info(f"Avg replans  : {replan_total / max(total, 1):.2f}")

# # #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# # #         goals      = task_goal_dict["vh_goal"]
# # #         node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
# # #         edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

# # #         try:
# # #             motion_planner, _, _, _, _ = construct_planner(
# # #                 self.name_equivalence, self.properties_data, self.object_placing,
# # #                 scenegraph_id=SCENEGRAPH_ID, script_id=file_id, dataset_root=DATA_DIR,
# # #             )
# # #         except Exception as e:
# # #             logger.error(f"  Failed to build planner for {file_id}: {e}")
# # #             return "", 0

# # #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# # #             motion_planner.get_symbolic_goal_nl(
# # #                 node_goals, edge_goals, action_goals=goals["actions"]
# # #             )
# # #         )
# # #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# # #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# # #         prompt = one_shot.prompt
# # #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# # #         prompt = prompt.replace("<cur_change>",      cur_change)
# # #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# # #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# # #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# # #         current_prompt = prompt
# # #         replan_count   = 0
# # #         raw_output     = ""

# # #         for attempt in range(MAX_REPLAN + 1):

# # #             # Call LLM
# # #             raw_output = self.llm.call(current_prompt)
# # #             logger.info(f"  Attempt {attempt+1}: {raw_output[:100]}...")

# # #             # Parse
# # #             parsed = parse_llm_output(raw_output)
# # #             if not parsed:
# # #                 break

# # #             # Filter invalid actions
# # #             parsed = filter_valid_actions(parsed)
# # #             if not parsed:
# # #                 logger.warning("  All actions filtered out")
# # #                 break

# # #             # Grammar check
# # #             try:
# # #                 ok, err = check_action_grammar(parsed)
# # #                 if not ok:
# # #                     logger.warning(f"  Grammar check failed: {err}")
# # #                     break
# # #             except KeyError as e:
# # #                 logger.warning(f"  Unknown action {e}, removing and retrying")
# # #                 if isinstance(parsed, dict):
# # #                     parsed.pop(str(e).strip("'"), None)
# # #                 if not parsed:
# # #                     break
# # #                 try:
# # #                     ok, _ = check_action_grammar(parsed)
# # #                     if not ok:
# # #                         break
# # #                 except Exception:
# # #                     break

# # #             # Convert to EAI format
# # #             try:
# # #                 actions = json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
# # #             except Exception as e:
# # #                 logger.warning(f"  json_to_action failed: {e}")
# # #                 break

# # #             # Simulate
# # #             motion_planner.reset()
# # #             history_actions    = []
# # #             history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]
# # #             executable         = True

# # #             for action in actions:
# # #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# # #                 if not exe_flag:
# # #                     executable = False
# # #                     history_cp = copy.deepcopy(history_env_states)

# # #                     try:
# # #                         checker  = TemporalOrderChecker(my_info, history_cp)
# # #                         code     = checker.run_checker().get_error_type()
# # #                         err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN")
# # #                     except Exception:
# # #                         err_type = "UNKNOWN"

# # #                     logger.info(f"  Failed: {action} | {err_type}")

# # #                     if attempt < MAX_REPLAN:
# # #                         replan_count += 1
# # #                         exec_steps  = [parse_eai_action_to_step(a, i+1)
# # #                                        for i, a in enumerate(history_actions)]
# # #                         failed_step = parse_eai_action_to_step(action, len(exec_steps)+1)

# # #                         try:
# # #                             diagnosis = diagnose_error(
# # #                                 action_history = exec_steps,
# # #                                 failed_step    = failed_step,
# # #                                 error_type     = err_type,
# # #                                 full_plan      = exec_steps + [failed_step],
# # #                             )
# # #                             logger.info(f"  Root: {diagnosis.root_cause}")
# # #                             logger.info(f"  Unsat: {diagnosis.unsatisfied_needs}")
# # #                             current_prompt = build_feedback_prompt(
# # #                                 prompt, history_actions, action, err_type, diagnosis
# # #                             )
# # #                         except Exception as e:
# # #                             logger.warning(f"  Diagnosis failed: {e}, using original prompt")
# # #                             current_prompt = prompt
# # #                     break

# # #                 else:
# # #                     history_actions.append(action)
# # #                     history_env_states.append(
# # #                         copy.deepcopy(motion_planner.env_state.to_dict())
# # #                     )

# # #             if executable:
# # #                 logger.info(f"  SUCCESS on attempt {attempt+1}")
# # #                 break
# # #             if attempt == MAX_REPLAN:
# # #                 logger.info(f"  Max replanning reached for {file_id}")

# # #         return raw_output, replan_count

# # #     def _save_outputs(self, outputs: list):
# # #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# # #         path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# # #         with open(path, "w") as f:
# # #             json.dump(outputs, f, indent=4)
# # #         logger.info(f"Saved {len(outputs)} outputs to {path}")


# # # # =============================================================================
# # # # Entry Point
# # # # =============================================================================

# # # if __name__ == "__main__":
# # #     if GEMINI_API_KEY == "your_key_here":
# # #         print("ERROR: Paste your Gemini API key in the CONFIGURATION section!")
# # #         sys.exit(1)
# # #     EAISDARunner().run_all()


# # # # # baslangic
# # """
# # eai_sda_runner.py
# # =================
# # Runs INSIDE Docker container.
# # Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# # Steps:
# # 1. Generate prompts using EAI's generate_prompts
# # 2. Send each prompt to Llama 3.3 70B via Groq API
# # 3. Parse LLM response into EAI action format
# # 4. Simulate using EAI's motion_planner.my_execute_primitive_action_eval()
# # 5. On failure → SDA diagnosis → feedback → replan
# # 6. Save outputs in EAI format for evaluate_results.py

# # Usage (inside Docker):
# #     pip install groq
# #     export GROQ_API_KEY="your_key_here"
# #     python3 eai_sda_runner.py
# # """

# # import os
# # import sys
# # import json
# # import copy
# # import re
# # import logging
# # import os.path as osp

# # # ── Add sda_eai to path ──────────────────────────────────────────────────────
# # sys.path.insert(0, "/opt/iGibson/sda_eai")

# # # ── EAI imports ──────────────────────────────────────────────────────────────
# # import virtualhome_eval.simulation.evolving_graph.utils as utils
# # from virtualhome_eval.simulation.evolving_graph.eval_utils import (
# #     construct_planner,
# #     json_to_action,
# #     check_action_grammar,
# #     check_no_hallucination_in_action,
# #     check_no_hallucination_in_arg,
# #     load_json_preserving_order,
# # )
# # from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# # # ── SDA imports ──────────────────────────────────────────────────────────────
# # from sdg import get_preconditions, explain_precondition
# # from error_diagnosis import ActionStep, diagnose_error

# # # ── Groq ─────────────────────────────────────────────────────────────────────
# # from groq import Groq

# # # ── Logging ──────────────────────────────────────────────────────────────────
# # logging.basicConfig(
# #     level=logging.INFO,
# #     format="%(asctime)s - %(levelname)s - %(message)s",
# # )
# # logger = logging.getLogger(__name__)

# # # ─────────────────────────────────────────────
# # # Configuration
# # # ─────────────────────────────────────────────

# # GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
# # MODEL             = "llama-3.3-70b-versatile"
# # MAX_REPLAN        = 3
# # SCENEGRAPH_ID     = 1
# # MODEL_NAME        = "llama-3.3-70b-sda"   # name for output file

# # RESOURCE_DIR      = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# # DATASET_DIR       = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# # OUTPUT_DIR        = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# # TASK_DICT_PATH    = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# # ID2TASK_PATH      = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# # DATA_DIR          = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")

# # # EAI error codes
# # ERROR_CODE_TO_TYPE = {
# #     0: "WRONG_TEMPORAL_ORDER",
# #     1: "MISSING_STEP",
# #     2: "AFFORDANCE_ERROR",
# #     3: "UNSEEN_OBJECT",
# #     4: "ADDITIONAL_STEP",
# #     5: "UNKNOWN_ERROR",
# # }

# # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # Generate executable action sequences for household tasks.

# # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # VALID ACTIONS ONLY (use no others):
# # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # - 2 arguments: PUTBACK, PUTIN, POUR
# # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # IMPORTANT RULES:
# # - PLUGIN and PLUGOUT do NOT exist — never use them under any circumstances
# # - Do NOT use OPEN on washing machines — they cannot be opened
# # - SWITCHON is enough to turn on any appliance
# # - If you need to turn something on, ONLY use SWITCHON — nothing else before it

# # Rules:
# # - WALK to object before GRAB
# # - OPEN containers before PUTIN
# # - Max 2 objects held at once
# # - No explanations, ONLY the JSON object
# # """


# # # ─────────────────────────────────────────────
# # # Groq LLM Client
# # # ─────────────────────────────────────────────

# # class GroqClient:
# #     def __init__(self):
# #         self.client = Groq(api_key=GROQ_API_KEY)

# #     def call(self, user_prompt: str) -> str:
# #         try:
# #             response = self.client.chat.completions.create(
# #                 model       = MODEL,
# #                 temperature = 0,
# #                 max_tokens  = 1024,
# #                 messages    = [
# #                     {"role": "system", "content": SYSTEM_PROMPT},
# #                     {"role": "user",   "content": user_prompt},
# #                 ],
# #             )
# #             return response.choices[0].message.content.strip()
# #         except Exception as e:
# #             logger.error(f"Groq API error: {e}")
# #             return ""


# # # ─────────────────────────────────────────────
# # # Action Format Converters
# # # ─────────────────────────────────────────────

# # def parse_llm_output(raw: str) -> list:
# #     """
# #     Parse LLM JSON output into EAI action list format.
# #     EAI format: [{"action": "walk", "o1": "phone", "o2": null}, ...]
# #     """
# #     # Strip markdown fences
# #     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`")

# #     # Try to extract JSON object
# #     match = re.search(r'\{.*\}', raw, re.DOTALL)
# #     if not match:
# #         logger.warning("No JSON found in LLM output")
# #         return []

# #     raw = match.group(0)

# #     try:
# #         # Use EAI's own parser
# #         parsed = load_json_preserving_order(raw)
# #         return parsed
# #     except Exception as e:
# #         logger.warning(f"JSON parse error: {e}")
# #         return []


# # def actions_to_eai_format(actions_json) -> list:
# #     """Convert parsed JSON to EAI internal action format."""
# #     if not actions_json:
# #         return []
# #     # EAI handles this via json_to_action after validation
# #     return actions_json


# # def build_feedback_prompt(
# #     original_prompt: str,
# #     executed_actions: list,
# #     failed_action,
# #     error_type: str,
# #     diagnosis,
# # ) -> str:
# #     """Build SDA feedback prompt combining original EAI prompt + diagnosis."""

# #     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
# #     unsatisfied_str = "\n".join(
# #         f"  - {explain_precondition(p)}"
# #         for p in diagnosis.unsatisfied_needs
# #     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"

# #     feedback = f"""
# # {original_prompt}

# # === EXECUTION FEEDBACK (SDA-Planner) ===

# # Your previous plan failed at action: {failed_action}
# # Error type: {error_type}

# # Successfully executed steps so far:
# # {executed_str}

# # Root cause of failure: {diagnosis.root_cause}
# # Unsatisfied preconditions:
# # {unsatisfied_str}

# # Replan window: steps {diagnosis.t_start} to {diagnosis.t_end}

# # === INSTRUCTIONS ===
# # Generate a NEW complete action sequence that:
# # 1. Fixes the failed action by satisfying: {diagnosis.unsatisfied_needs}
# # 2. Completes the full task from the beginning
# # 3. Ensures every action's preconditions are met

# # Respond with ONLY the JSON action sequence.
# # """
# #     return feedback.strip()


# # # ─────────────────────────────────────────────
# # # SDA + EAI Runner
# # # ─────────────────────────────────────────────

# # class EAISDARunner:

# #     def __init__(self):
# #         self.llm = GroqClient()

# #         # Load EAI resources
# #         self.properties_data  = utils.load_properties_data()
# #         self.object_placing   = utils.load_object_placing()
# #         self.name_equivalence = utils.load_name_equivalence()
# #         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
# #         self.id2task          = json.load(open(ID2TASK_PATH))

# #         os.makedirs(OUTPUT_DIR, exist_ok=True)

# #     def run_all(self):
# #         """Run SDA pipeline on all VirtualHome action_sequencing tasks."""

# #         logger.info("=== EAI + SDA-Planner Runner ===")
# #         logger.info(f"Model: {MODEL_NAME}")
# #         logger.info(f"Max replanning attempts: {MAX_REPLAN}")

# #         # outputs      = []
# #         # success      = 0
# #         # total        = 0
# #         # replan_total = 0
# #         # Load existing outputs to resume from where we left off
# #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# #         if osp.exists(out_path):
# #             existing = json.load(open(out_path))
# #             done_ids = {d["identifier"] for d in existing
# #                         if d["llm_output"] and d["llm_output"] != "..."}
# #             outputs = [d for d in existing if d["identifier"] in done_ids]
# #             logger.info(f"Resuming: {len(outputs)} tasks already done")
# #         else:
# #             outputs = []
# #             done_ids = set()

# #         total        = 0
# #         replan_total = 0

# #         for task_name, task_files in self.task_dicts.items():
# #             for file_id, task_goal_dict in task_files.items():
# #                 if file_id in done_ids:
# #                     logger.info(f"  Skipping {file_id} — already done")
# #                     continue
# #                 total += 1
# #                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

# #                 result, replan_count = self.run_single_task(
# #                     file_id, task_name, task_goal_dict
# #                 )
# #                 replan_total += replan_count

# #                 outputs.append({
# #                     "identifier": file_id,
# #                     "llm_output": result,
# #                 })

# #                 if total % 10 == 0:
# #                     logger.info(f"Progress: {total} tasks done")
# #                     self._save_outputs(outputs)

# #         # Save final outputs
# #         self._save_outputs(outputs)

# #         logger.info(f"\n=== DONE ===")
# #         logger.info(f"Total tasks    : {total}")
# #         logger.info(f"Total replans  : {replan_total}")
# #         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")
# #         logger.info(f"Output saved to: {OUTPUT_DIR}")

# #         return outputs

# #     def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
# #         """Run SDA pipeline on a single task. Returns (llm_output_str, replan_count)."""

# #         goals        = task_goal_dict["vh_goal"]
# #         action_goals = goals["actions"]
# #         scene_goals  = goals["goal"]

# #         node_goals = [g for g in scene_goals if "id" in g and "state" in g]
# #         edge_goals = [g for g in scene_goals if "from_id" in g and "relation_type" in g]

# #         # Build EAI motion planner for this task
# #         motion_planner, relevant_id, gd_actions, task_nm, _ = construct_planner(
# #             self.name_equivalence,
# #             self.properties_data,
# #             self.object_placing,
# #             scenegraph_id = SCENEGRAPH_ID,
# #             script_id     = file_id,
# #             dataset_root  = DATA_DIR,
# #         )

# #         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
# #             motion_planner.get_symbolic_goal_nl(node_goals, edge_goals, action_goals=action_goals)
# #         )

# #         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

# #         # Build EAI prompt
# #         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
# #         prompt = one_shot.prompt
# #         prompt = prompt.replace("<object_in_scene>", object_in_scene)
# #         prompt = prompt.replace("<cur_change>",      cur_change)
# #         prompt = prompt.replace("<node_goals>",      node_goal_str)
# #         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
# #         prompt = prompt.replace("<action_goals>",    action_goal_str)

# #         replan_count  = 0
# #         current_prompt = prompt

# #         for attempt in range(MAX_REPLAN + 1):
# #             # ── Call LLM ────────────────────────────────────────────────────
# #             raw_output = self.llm.call(current_prompt)
# #             logger.info(f"  Attempt {attempt+1} raw output: {raw_output[:100]}...")

# #             # ── Parse output ─────────────────────────────────────────────────
# #             parsed_actions = parse_llm_output(raw_output)

# #             if not parsed_actions:
# #                 logger.warning(f"  Could not parse LLM output for {file_id}")
# #                 break

# #             # ── Validate format ───────────────────────────────────────────────
# #             EAI_VALID = {"DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE","PUSH","PULL","MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE","SCRUB","WASH","GRAB","SWITCHOFF","SWITCHON","CLOSE","FIND","WALK","OPEN","POINTAT","PUTBACK","PUTIN","PUTOBJBACK","RUN","SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF","GREET","DROP","LIE","POUR"}
# #             if isinstance(parsed_actions, dict):
# #                 parsed_actions = {k: v for k, v in parsed_actions.items() if k.upper() in EAI_VALID}
# #             elif isinstance(parsed_actions, list):
# #                 parsed_actions = [a for a in parsed_actions if list(a.keys())[0].upper() in EAI_VALID]
# #             if not parsed_actions:
# #                 break
# #             pass_check, _ = check_action_grammar(parsed_actions)
# #             if not pass_check:
# #                 logger.warning(f"  Grammar check failed for {file_id}")
# #                 break

# #             # ── Convert to EAI internal format ────────────────────────────────
# #             actions = json_to_action(
# #                 parsed_actions, relevant_name_to_id=relevant_name_to_id
# #             )

# #             # ── Simulate with EAI motion planner ──────────────────────────────
# #             motion_planner.reset()
# #             history_actions     = []
# #             history_env_states  = [copy.deepcopy(motion_planner.env_state.to_dict())]
# #             executable          = True
# #             failed_error_code   = None
# #             failed_action_eai   = None

# #             for action in actions:
# #                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

# #                 if not exe_flag:
# #                     # ── Action failed! ────────────────────────────────────────
# #                     executable        = False
# #                     failed_action_eai = action
# #                     history_cp        = copy.deepcopy(history_env_states)

# #                     checker           = TemporalOrderChecker(my_info, history_cp)
# #                     formal_info       = checker.run_checker()
# #                     failed_error_code = formal_info.get_error_type()
# #                     error_type_str    = ERROR_CODE_TO_TYPE.get(failed_error_code, "UNKNOWN")

# #                     logger.info(f"  ❌ Failed at: {action} | Error: {error_type_str}")

# #                     # ── SDA Diagnosis ─────────────────────────────────────────
# #                     if attempt < MAX_REPLAN:
# #                         replan_count += 1

# #                         # Convert executed history to ActionStep objects
# #                         exec_steps = []
# #                         for i, a in enumerate(history_actions):
# #                             # EAI action format: "[GRAB] <clothes_jacket> (1003)"
# #                             # or could be a dict - handle both
# #                             if isinstance(a, dict):
# #                                 action_name = a.get("action", "UNKNOWN").upper()
# #                                 obj1 = a.get("o1", "unknown")
# #                                 obj2 = a.get("o2")
# #                             else:
# #                                 # Parse string format "[GRAB] <clothes_jacket> (1003)"
# #                                 a_str = str(a)
# #                                 action_match = re.search(r'\[(\w+)\]', a_str)
# #                                 obj_match = re.findall(r'<([^>]+)>', a_str)
# #                                 action_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# #                                 obj1 = obj_match[0] if obj_match else "unknown"
# #                                 obj2 = obj_match[1] if len(obj_match) > 1 else None
# #                             exec_steps.append(ActionStep(i+1, action_name, obj1, obj2))

# #                         # Convert failed action to ActionStep
# #                         if isinstance(failed_action_eai, dict):
# #                             failed_name = failed_action_eai.get("action", "UNKNOWN").upper()
# #                             failed_obj1 = failed_action_eai.get("o1", "unknown")
# #                             failed_obj2 = failed_action_eai.get("o2")
# #                         else:
# #                             fa_str = str(failed_action_eai)
# #                             action_match = re.search(r'\[(\w+)\]', fa_str)
# #                             obj_match = re.findall(r'<([^>]+)>', fa_str)
# #                             failed_name = action_match.group(1).upper() if action_match else "UNKNOWN"
# #                             failed_obj1 = obj_match[0] if obj_match else "unknown"
# #                             failed_obj2 = obj_match[1] if len(obj_match) > 1 else None
# #                         failed_step = ActionStep(
# #                             len(exec_steps)+1, failed_name, failed_obj1, failed_obj2
# #                         )

# #                         # All steps as ActionStep
# #                         all_steps = exec_steps + [failed_step]

# #                         # Run SDA diagnosis
# #                         diagnosis = diagnose_error(
# #                             action_history = exec_steps,
# #                             failed_step    = failed_step,
# #                             error_type     = error_type_str,
# #                             full_plan      = all_steps,
# #                         )

# #                         logger.info(f"  🔍 Root cause: {diagnosis.root_cause}")
# #                         logger.info(f"  🔍 Unsatisfied: {diagnosis.unsatisfied_needs}")

# #                         # Build feedback prompt
# #                         current_prompt = build_feedback_prompt(
# #                             original_prompt  = prompt,
# #                             executed_actions = history_actions,
# #                             failed_action    = failed_action_eai,
# #                             error_type       = error_type_str,
# #                             diagnosis        = diagnosis,
# #                         )

# #                     break  # break action loop, retry with new prompt

# #                 else:
# #                     history_actions.append(action)
# #                     history_env_states.append(
# #                         copy.deepcopy(motion_planner.env_state.to_dict())
# #                     )

# #             if executable:
# #                 logger.info(f"  ✅ Executable plan found on attempt {attempt+1}")
# #                 break

# #             if attempt == MAX_REPLAN:
# #                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

# #         # Return raw_output string for EAI evaluate_results to process
# #         return raw_output, replan_count

# #     def _save_outputs(self, outputs: list):
# #         """Save outputs in EAI format."""
# #         os.makedirs(OUTPUT_DIR, exist_ok=True)
# #         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
# #         with open(out_path, "w") as f:
# #             json.dump(outputs, f, indent=4)
# #         logger.info(f"Saved {len(outputs)} outputs to {out_path}")


# # # ─────────────────────────────────────────────
# # # Entry Point
# # # ─────────────────────────────────────────────

# # if __name__ == "__main__":
# #     if not GROQ_API_KEY:
# #         print("ERROR: GROQ_API_KEY environment variable not set!")
# #         print("Run: export GROQ_API_KEY='your_key_here'")
# #         sys.exit(1)

# #     runner = EAISDARunner()
# #     runner.run_all()
# """
# eai_sda_runner.py
# =================
# Runs INSIDE Docker container.
# Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

# Usage (inside Docker):
#     python3 sda_eai/eai_sda_runner.py                    # run all tasks
#     python3 sda_eai/eai_sda_runner.py --max_tasks 50     # run first 50
#     python3 sda_eai/eai_sda_runner.py --max_tasks 100    # run next 100 (resumes automatically)
# """

# import os
# import sys
# import json
# import copy
# import re
# import time
# import logging
# import argparse
# import os.path as osp
# import urllib.request
# import urllib.error

# sys.path.insert(0, "/opt/iGibson/sda_eai")

# import virtualhome_eval.simulation.evolving_graph.utils as utils
# from virtualhome_eval.simulation.evolving_graph.eval_utils import (
#     construct_planner,
#     json_to_action,
#     check_action_grammar,
#     load_json_preserving_order,
# )
# from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker
# from sdg import explain_precondition
# from error_diagnosis import ActionStep, diagnose_error

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s - %(levelname)s - %(message)s",
# )
# logger = logging.getLogger(__name__)


# # =============================================================================
# # CONFIGURATION — edit this section before running
# # =============================================================================

# # ── API Settings ─────────────────────────────────────────────────────────────
# # Choose ONE provider by setting API_PROVIDER:
# #   "groq"   → uses Groq API (install: pip install groq)
# #   "openai" → uses OpenAI API (install: pip install openai)
# #   "gemini" → uses Gemini REST API (no install needed)

# # API_PROVIDER  = "groq"               # "groq" | "openai" | "gemini"
# # API_KEY       = "YOUR_API_KEY_HERE"  # paste your key here
# # MODEL         = "llama-3.3-70b-versatile"  # change based on provider:
# #                                            # groq:   "llama-3.3-70b-versatile"
# #                                            # openai: "gpt-4o" or "gpt-4o-mini"
# #                                            # gemini: "gemini-2.5-flash-lite"

# API_PROVIDER = "openai"
# API_KEY      = os.environ.get("OPENAI_API_KEY", "")  # set via: export OPENAI_API_KEY="..."
# MODEL        = "gpt-4o-mini"
# MODEL_NAME   = "gpt-4o-mini-sda"

# if not API_KEY:
#     print("ERROR: API key not set! Run: export OPENAI_API_KEY='your_key'")
#     sys.exit(1)

# # ── Experiment Settings ───────────────────────────────────────────────────────
# MAX_REPLAN    = 3    # max replanning attempts per task
# SCENEGRAPH_ID = 1    # VirtualHome scene ID
# #MODEL_NAME    = "llama-3.3-70b-sda"  # used for output filename — change if switching models
# MODEL_NAME   = "gpt-4o-mini-sda"

# # ── Paths (inside Docker) ─────────────────────────────────────────────────────
# RESOURCE_DIR   = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
# DATASET_DIR    = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
# OUTPUT_DIR     = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
# TASK_DICT_PATH = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
# ID2TASK_PATH   = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
# DATA_DIR       = osp.join(DATASET_DIR,  "programs_processed_precond_nograb_morepreconds")

# # =============================================================================

# ERROR_CODE_TO_TYPE = {
#     0: "WRONG_TEMPORAL_ORDER",
#     1: "MISSING_STEP",
#     2: "AFFORDANCE_ERROR",
#     3: "UNSEEN_OBJECT",
#     4: "ADDITIONAL_STEP",
#     5: "UNKNOWN_ERROR",
# }

# EAI_VALID_ACTIONS = {
#     "DRINK", "EAT", "CUT", "TOUCH", "LOOKAT", "WATCH", "READ", "TYPE",
#     "PUSH", "PULL", "MOVE", "SQUEEZE", "SLEEP", "WAKEUP", "RINSE",
#     "SCRUB", "WASH", "GRAB", "SWITCHOFF", "SWITCHON", "CLOSE", "FIND",
#     "WALK", "OPEN", "POINTAT", "PUTBACK", "PUTIN", "PUTOBJBACK", "RUN",
#     "SIT", "STANDUP", "TURNTO", "WIPE", "PUTON", "PUTOFF", "GREET",
#     "DROP", "LIE", "POUR",
# }

# # SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# # Generate executable action sequences for household tasks.

# # STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# # {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# # VALID ACTIONS ONLY (use no others):
# # - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# # - 2 arguments: PUTBACK, PUTIN, POUR
# # - 0 arguments: STANDUP, SLEEP, WAKEUP

# # IMPORTANT RULES:
# # - PLUGIN and PLUGOUT do NOT exist — never use them under any circumstances
# # - Do NOT use OPEN on washing machines — they cannot be opened
# # - SWITCHON is enough to turn on any appliance
# # - WALK to object before GRAB
# # - OPEN containers before PUTIN
# # - Max 2 objects held at once
# # - No explanations, ONLY the JSON object"""
# SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# Generate executable action sequences for household tasks.

# STRICT OUTPUT FORMAT - respond with ONLY a JSON object like this:
# {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# VALID ACTIONS ONLY (use no others):
# - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, RUN, SIT, TURNTO, PUTOBJBACK
# - 2 arguments: PUTBACK, PUTIN, POUR
# - 0 arguments: STANDUP, SLEEP, WAKEUP

# No explanations, ONLY the JSON object."""

# # =============================================================================
# # LLM Client — supports Groq, OpenAI, Gemini
# # =============================================================================

# class LLMClient:
#     def __init__(self):
#         self.provider = API_PROVIDER.lower()
#         logger.info(f"Using provider: {self.provider} | model: {MODEL}")

#         if self.provider == "groq":
#             from groq import Groq
#             self.client = Groq(api_key=API_KEY)

#         elif self.provider == "openai":
#             from openai import OpenAI
#             self.client = OpenAI(api_key=API_KEY)

#         elif self.provider == "gemini":
#             self.gemini_url = (
#                 f"https://generativelanguage.googleapis.com/v1beta/models/"
#                 f"{MODEL}:generateContent?key={API_KEY}"
#             )
#         else:
#             raise ValueError(f"Unknown provider: {self.provider}. Use 'groq', 'openai', or 'gemini'")

#     def call(self, user_prompt: str) -> str:
#         if self.provider in ("groq", "openai"):
#             return self._call_openai_style(user_prompt)
#         elif self.provider == "gemini":
#             return self._call_gemini(user_prompt)

#     def _call_openai_style(self, user_prompt: str) -> str:
#         """Works for both Groq and OpenAI (same API format)."""
#         try:
#             response = self.client.chat.completions.create(
#                 model       = MODEL,
#                 temperature = 0,
#                 max_tokens  = 1024,
#                 messages    = [
#                     {"role": "system", "content": SYSTEM_PROMPT},
#                     {"role": "user",   "content": user_prompt},
#                 ],
#             )
#             return response.choices[0].message.content.strip()
#         except Exception as e:
#             logger.error(f"API error: {e}")
#             return ""

#     def _call_gemini(self, user_prompt: str) -> str:
#         """Gemini REST API via urllib — no library needed."""
#         full_prompt = SYSTEM_PROMPT + "\n\n" + user_prompt
#         payload = json.dumps({
#             "contents": [{"parts": [{"text": full_prompt}]}],
#             "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1024}
#         }).encode("utf-8")
#         try:
#             req = urllib.request.Request(
#                 self.gemini_url, data=payload,
#                 headers={"Content-Type": "application/json"}, method="POST"
#             )
#             with urllib.request.urlopen(req, timeout=30) as resp:
#                 data = json.loads(resp.read().decode("utf-8"))
#                 return data["candidates"][0]["content"]["parts"][0]["text"].strip()
#         except urllib.error.HTTPError as e:
#             logger.error(f"Gemini HTTP {e.code}: {e.read().decode()[:200]}")
#             return ""
#         except Exception as e:
#             logger.error(f"Gemini error: {e}")
#             return ""


# # =============================================================================
# # Helper Functions
# # =============================================================================

# def parse_llm_output(raw: str):
#     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
#     match = re.search(r'\{.*\}', raw, re.DOTALL)
#     if not match:
#         logger.warning("No JSON found in LLM output")
#         return []
#     try:
#         return load_json_preserving_order(match.group(0))
#     except Exception as e:
#         logger.warning(f"JSON parse error: {e}")
#         return []


# def filter_valid_actions(parsed):
#     """Remove actions not in EAI's valid set (e.g. PLUGIN)."""
#     if isinstance(parsed, dict):
#         return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
#     elif isinstance(parsed, list):
#         return [a for a in parsed if list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
#     return parsed


# def parse_eai_action_to_step(action, index: int) -> ActionStep:
#     if isinstance(action, dict):
#         return ActionStep(index, action.get("action","UNKNOWN").upper(),
#                          action.get("o1","unknown"), action.get("o2"))
#     s = str(action)
#     am = re.search(r'\[(\w+)\]', s)
#     om = re.findall(r'<([^>]+)>', s)
#     return ActionStep(index,
#                       am.group(1).upper() if am else "UNKNOWN",
#                       om[0] if om else "unknown",
#                       om[1] if len(om) > 1 else None)


# def build_feedback_prompt(original_prompt, executed_actions, failed_action, error_type, diagnosis):
#     executed_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"
#     unsatisfied_str = "\n".join(
#         f"  - {explain_precondition(p)}" for p in diagnosis.unsatisfied_needs
#     ) if diagnosis.unsatisfied_needs else "  - Unknown precondition violation"
#     return f"""{original_prompt}

# === EXECUTION FEEDBACK (SDA-Planner) ===

# Your previous plan FAILED at: {failed_action}
# Error type    : {error_type}
# Root cause    : {diagnosis.root_cause}

# Unsatisfied preconditions:
# {unsatisfied_str}

# Steps executed successfully:
# {executed_str}

# Generate a NEW complete action sequence that fixes this error.
# Respond with ONLY the JSON."""


# # =============================================================================
# # Main Runner
# # =============================================================================

# class EAISDARunner:

#     def __init__(self):
#         self.llm = LLMClient()
#         logger.info("Loading EAI resources...")
#         self.properties_data  = utils.load_properties_data()
#         self.object_placing   = utils.load_object_placing()
#         self.name_equivalence = utils.load_name_equivalence()
#         self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
#         self.id2task          = json.load(open(ID2TASK_PATH))
#         os.makedirs(OUTPUT_DIR, exist_ok=True)
#         logger.info("EAI resources loaded.")

#     def run_all(self, max_tasks=None):
#         """
#         Run SDA pipeline on VirtualHome tasks.
#         max_tasks: if set, stop after processing this many NEW tasks.
#                    Resume will automatically skip already completed ones.
#         """
#         logger.info("=== EAI + SDA-Planner Runner ===")
#         logger.info(f"Model     : {MODEL_NAME}")
#         logger.info(f"Provider  : {API_PROVIDER}")
#         logger.info(f"Max replan: {MAX_REPLAN}")
#         logger.info(f"Max tasks : {max_tasks if max_tasks else 'ALL'}")

#         # Resume support
#         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
#         if osp.exists(out_path):
#             existing = json.load(open(out_path))
#             done_ids = {d["identifier"] for d in existing
#                         if d["llm_output"] not in ("", "...")}
#             outputs = list(existing)
#             logger.info(f"Resuming: {len(done_ids)} tasks already done")
#         else:
#             outputs, done_ids = [], set()

#         total = replan_total = 0

#         for task_name, task_files in self.task_dicts.items():
#             for file_id, task_goal_dict in task_files.items():

#                 # Stop if we reached max_tasks for this run
#                 if max_tasks and total >= max_tasks:
#                     logger.info(f"\nReached max_tasks={max_tasks}, stopping.")
#                     self._save_outputs(outputs)
#                     return

#                 if file_id in done_ids:
#                     logger.info(f"  Skipping {file_id} (already done)")
#                     continue

#                 total += 1
#                 logger.info(f"\n[{total}] Task: {task_name} | File: {file_id}")

#                 result, rc = self.run_single_task(file_id, task_name, task_goal_dict)
#                 replan_total += rc
#                 outputs.append({"identifier": file_id, "llm_output": result})

#                 time.sleep(1)  # small delay to reduce rate limiting

#                 if total % 10 == 0:
#                     self._save_outputs(outputs)
#                     logger.info(f"Checkpoint saved at {total} new tasks")

#         self._save_outputs(outputs)
#         logger.info(f"\n=== DONE ===")
#         logger.info(f"New tasks    : {total}")
#         logger.info(f"Total replans: {replan_total}")
#         logger.info(f"Avg replans  : {replan_total / max(total, 1):.2f}")

#     def run_single_task(self, file_id, task_name, task_goal_dict):
#         goals      = task_goal_dict["vh_goal"]
#         node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
#         edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

#         try:
#             motion_planner, _, _, _, _ = construct_planner(
#                 self.name_equivalence, self.properties_data, self.object_placing,
#                 scenegraph_id=SCENEGRAPH_ID, script_id=file_id, dataset_root=DATA_DIR,
#             )
#         except Exception as e:
#             logger.error(f"  Failed to build planner: {e}")
#             return "", 0

#         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
#             motion_planner.get_symbolic_goal_nl(
#                 node_goals, edge_goals, action_goals=goals["actions"]
#             )
#         )
#         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

#         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
#         prompt = one_shot.prompt
#         prompt = prompt.replace("<object_in_scene>", object_in_scene)
#         prompt = prompt.replace("<cur_change>",      cur_change)
#         prompt = prompt.replace("<node_goals>",      node_goal_str)
#         prompt = prompt.replace("<edge_goals>",      edge_goal_str)
#         prompt = prompt.replace("<action_goals>",    action_goal_str)

#         current_prompt = prompt
#         replan_count   = 0
#         raw_output     = ""

#         for attempt in range(MAX_REPLAN + 1):
#             raw_output = self.llm.call(current_prompt)
#             logger.info(f"  Attempt {attempt+1}: {raw_output[:100]}...")

#             parsed = parse_llm_output(raw_output)
#             if not parsed:
#                 break

#             parsed = filter_valid_actions(parsed)
#             if not parsed:
#                 logger.warning("  All actions filtered out")
#                 break

#             try:
#                 ok, err = check_action_grammar(parsed)
#                 if not ok:
#                     logger.warning(f"  Grammar check failed: {err}")
#                     break
#             except KeyError as e:
#                 logger.warning(f"  Unknown action {e}, skipping")
#                 break

#             try:
#                 actions = json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
#             except Exception as e:
#                 logger.warning(f"  json_to_action failed: {e}")
#                 break

#             motion_planner.reset()
#             history_actions    = []
#             history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]
#             executable         = True

#             for action in actions:
#                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

#                 if not exe_flag:
#                     executable = False
#                     try:
#                         checker  = TemporalOrderChecker(my_info, copy.deepcopy(history_env_states))
#                         code     = checker.run_checker().get_error_type()
#                         err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN")
#                     except Exception:
#                         err_type = "UNKNOWN"

#                     logger.info(f"  ❌ {action} | {err_type}")

#                     # if attempt < MAX_REPLAN:
#                     #     replan_count += 1
#                     #     exec_steps  = [parse_eai_action_to_step(a, i+1)
#                     #                    for i, a in enumerate(history_actions)]
#                     #     failed_step = parse_eai_action_to_step(action, len(exec_steps)+1)
#                     #     try:
#                     #         diagnosis = diagnose_error(
#                     #             exec_steps, failed_step, err_type,
#                     #             exec_steps + [failed_step]
#                     #         )
#                     if attempt < MAX_REPLAN:
#                         replan_count += 1
#                         exec_steps  = [parse_eai_action_to_step(a, i+1)
#                                        for i, a in enumerate(history_actions)]
#                         failed_step = parse_eai_action_to_step(action, len(exec_steps)+1)
#                         try:
#                             # Check initial character state
#                             char_state = []
#                             try:
#                                 for node in history_env_states[0].get("nodes", []):
#                                     if node.get("class_name") == "character":
#                                         char_state = node.get("states", [])
#                             except:
#                                 pass
#                             is_sitting = "SITTING" in char_state
#                             is_lying   = "LYING"   in char_state

#                             diagnosis = diagnose_error(
#                                 exec_steps, failed_step, err_type,
#                                 exec_steps + [failed_step],
#                                 char_sitting = is_sitting,
#                                 char_lying   = is_lying,
#                             )

#                             logger.info(f"  🔍 {diagnosis.root_cause} | {diagnosis.unsatisfied_needs}")
#                             current_prompt = build_feedback_prompt(
#                                 prompt, history_actions, action, err_type, diagnosis
#                             )
#                         except Exception as e:
#                             logger.warning(f"  Diagnosis failed: {e}")
#                             current_prompt = prompt
#                     break
#                 else:
#                     history_actions.append(action)
#                     history_env_states.append(copy.deepcopy(motion_planner.env_state.to_dict()))

#             if executable:
#                 logger.info(f"  ✅ SUCCESS on attempt {attempt+1}")
#                 break
#             if attempt == MAX_REPLAN:
#                 logger.info(f"  ⚠️ Max replanning reached for {file_id}")

#         return raw_output, replan_count

#     def _save_outputs(self, outputs):
#         path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
#         with open(path, "w") as f:
#             json.dump(outputs, f, indent=4)
#         logger.info(f"Saved {len(outputs)} outputs to {path}")


# # =============================================================================
# # Entry Point
# # =============================================================================

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--max_tasks", type=int, default=None,
#                         help="Max number of NEW tasks to process this run (default: all)")
#     args = parser.parse_args()

#     if API_KEY == "YOUR_API_KEY_HERE":
#         print("ERROR: Please paste your API key in the CONFIGURATION section!")
#         sys.exit(1)

#     EAISDARunner().run_all(max_tasks=args.max_tasks)
"""
eai_sda_runner.py
=================
Runs INSIDE Docker container.
Integrates SDA-Planner feedback loop with EAI's real VirtualHome simulator.

Key design (following SDA-Planner paper):
- On failure: diagnose → find [t_start, t_end] → ask LLM for subsequence only
- Splice: executed[:t_start] + new_subsequence + remaining_plan[t_end+1:]
- Supports Groq, OpenAI, Gemini

Usage:
    python3 sda_eai/eai_sda_runner.py                  # run all tasks
    python3 sda_eai/eai_sda_runner.py --max_tasks 50   # run first 50 tasks
"""

import os
import sys
import json
import copy
import re
import time
import logging
import argparse
import os.path as osp
import urllib.request
import urllib.error

sys.path.insert(0, "/opt/iGibson/sda_eai")

import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    construct_planner,
    json_to_action,
    check_action_grammar,
    load_json_preserving_order,
)
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker
from sdg import explain_precondition, is_prep_action
from error_diagnosis import ActionStep, diagnose_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

API_PROVIDER = "openai"                          # "groq" | "openai" | "gemini"
API_KEY      = os.environ.get("OPENAI_API_KEY", os.environ.get("GROQ_API_KEY", ""))
MODEL        = "gpt-4o"                     # model name for the provider
MODEL_NAME   = "gpt-4o-sda_"                 # used for output filename

MAX_REPLAN    = 5
SCENEGRAPH_ID = 1

RESOURCE_DIR   = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
DATASET_DIR    = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
OUTPUT_DIR     = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
TASK_DICT_PATH = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
ID2TASK_PATH   = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
DATA_DIR       = osp.join(DATASET_DIR,  "programs_processed_precond_nograb_morepreconds")

# =============================================================================

ERROR_CODE_TO_TYPE = {
    0: "WRONG_TEMPORAL_ORDER",
    1: "MISSING_STEP",
    2: "AFFORDANCE_ERROR",
    3: "UNSEEN_OBJECT",
    4: "ADDITIONAL_STEP",
    5: "UNKNOWN_ERROR",
}

EAI_VALID_ACTIONS = {
    "DRINK","EAT","CUT","TOUCH","LOOKAT","WATCH","READ","TYPE","PUSH","PULL",
    "MOVE","SQUEEZE","SLEEP","WAKEUP","RINSE","SCRUB","WASH","GRAB","SWITCHOFF",
    "SWITCHON","CLOSE","FIND","WALK","OPEN","POINTAT","PUTBACK","PUTIN",
    "PUTOBJBACK","RUN","SIT","STANDUP","TURNTO","WIPE","PUTON","PUTOFF",
    "GREET","DROP","LIE","POUR",
}

SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.

OUTPUT FORMAT - respond with ONLY a JSON object:
{"ACTION": ["object"], "ACTION": ["object1", "object2"]}

VALID ACTIONS:
- 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, SIT, PUTOBJBACK, RUN, TURNTO
- 2 arguments: PUTBACK, PUTIN, POUR
- 0 arguments: STANDUP, SLEEP, WAKEUP

RULES:
- PLUGIN/PLUGOUT do NOT exist — all devices are already plugged in
- WALK to object before GRAB
- OPEN containers/appliances before PUTBACK or PUTIN inside them
- Max 2 objects held at once
- Output ONLY the JSON, nothing else"""

SUBSEQUENCE_PROMPT = """You are fixing a failed action sequence for a household robot in VirtualHome.

OUTPUT FORMAT - respond with ONLY a JSON object containing the replacement subsequence:
{"ACTION": ["object"], "ACTION": ["object1", "object2"]}

VALID ACTIONS: WALK, FIND, GRAB, PUTBACK, PUTIN, OPEN, CLOSE, SWITCHON, SWITCHOFF, STANDUP, SIT, LIE, WASH, RINSE, DROP, PUTOFF, PUTON, MOVE, TOUCH, READ, DRINK, EAT, TYPE, WIPE, SCRUB, POUR, RUN, TURNTO, PUTOBJBACK, WAKEUP, SLEEP, SQUEEZE, CUT, WATCH, PULL, PUSH

RULES:
- PLUGIN/PLUGOUT do NOT exist
- WALK to object before GRAB
- OPEN containers before PUTBACK/PUTIN
- Output ONLY the JSON replacement subsequence"""


# =============================================================================
# LLM Client
# =============================================================================

class LLMClient:
    def __init__(self):
        if API_PROVIDER == "groq":
            from groq import Groq
            self.client   = Groq(api_key=API_KEY)
            self.provider = "openai_style"
        elif API_PROVIDER == "openai":
            from openai import OpenAI
            self.client   = OpenAI(api_key=API_KEY)
            self.provider = "openai_style"
        elif API_PROVIDER == "gemini":
            self.gemini_url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{MODEL}:generateContent?key={API_KEY}"
            )
            self.provider = "gemini"
        else:
            raise ValueError(f"Unknown provider: {API_PROVIDER}")
        logger.info(f"LLM: {API_PROVIDER} / {MODEL}")

    def call(self, user_prompt: str, system_prompt: str = None) -> str:
        if system_prompt is None:
            system_prompt = SYSTEM_PROMPT
        if self.provider == "openai_style":
            return self._call_openai(user_prompt, system_prompt)
        return self._call_gemini(user_prompt, system_prompt)

    def _call_openai(self, user_prompt: str, system_prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model       = MODEL,
                temperature = 0,
                max_tokens  = 1024,
                messages    = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"API error: {e}")
            return ""

    def _call_gemini(self, user_prompt: str, system_prompt: str) -> str:
        full = system_prompt + "\n\n" + user_prompt
        payload = json.dumps({
            "contents": [{"parts": [{"text": full}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 1024}
        }).encode()
        try:
            req = urllib.request.Request(
                self.gemini_url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
                return d["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return ""


# =============================================================================
# Helpers
# =============================================================================

# def parse_llm_output(raw: str):
#     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
#     match = re.search(r'\{.*\}', raw, re.DOTALL)
#     if not match:
#         logger.warning("No JSON found in LLM output")
#         return []
#     try:
#         return load_json_preserving_order(match.group(0))
#     except Exception as e:
#         logger.warning(f"JSON parse error: {e}")
#         return []
# def parse_llm_output(raw: str):
#     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
#     # Fix 0-argument actions: {"STANDUP": []} → {"STANDUP": ["character"]}
#     raw = re.sub(r'"(STANDUP|SLEEP|WAKEUP)"\s*:\s*\[\]', r'"\1": ["character"]', raw, flags=re.IGNORECASE)
#     match = re.search(r'\{.*\}', raw, re.DOTALL)
#     if not match:
#         logger.warning("No JSON found in LLM output")
#         return []
#     try:
#         return load_json_preserving_order(match.group(0))
#     except Exception as e:
#         logger.warning(f"JSON parse error: {e}")
#         return []
def parse_llm_output(raw: str):
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
    
    # Fix 0-argument actions: {"STANDUP": []} → {"STANDUP": ["character"]}
    # load_json_preserving_order drops empty-arg actions otherwise
    # raw = re.sub(
    #     r'"(STANDUP|SLEEP|WAKEUP)"\s*:\s*\[\]',
    #     r'"\1": ["character"]',
    #     raw,
    #     flags=re.IGNORECASE
    # )
    raw = re.sub(
        r'"(STANDUP|SLEEP|WAKEUP)"\s*:\s*\[\]',
        r'"\1": ["standup_placeholder"]',
        raw, flags=re.IGNORECASE
    )
    
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        logger.warning("No JSON found in LLM output")
        return []
    try:
        return load_json_preserving_order(match.group(0))
    except Exception as e:
        logger.warning(f"JSON parse error: {e}")
        return []

def filter_valid_actions(parsed):
    """Remove any actions not in EAI's valid set (e.g. PLUGIN)."""
    if isinstance(parsed, dict):
        return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
    elif isinstance(parsed, list):
        return [a for a in parsed if list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
    return parsed


def parse_eai_action(action, index: int) -> ActionStep:
    """Convert EAI action (string or dict) to ActionStep."""
    if isinstance(action, dict):
        return ActionStep(index, action.get("action","UNKNOWN").upper(),
                         action.get("o1","unknown"), action.get("o2"))
    s = str(action)
    am = re.search(r'\[(\w+)\]', s)
    om = re.findall(r'<([^>]+)>', s)
    return ActionStep(index,
                      am.group(1).upper() if am else "UNKNOWN",
                      om[0] if om else "unknown",
                      om[1] if len(om) > 1 else None)


def get_char_state(env_state_dict: dict):
    """Extract character sitting/lying state from EAI env state dict."""
    try:
        for node in env_state_dict.get("nodes", []):
            if node.get("class_name") == "character":
                states = node.get("states", [])
                return "SITTING" in states, "LYING" in states
    except Exception:
        pass
    return False, False


def build_initial_plan_prompt(prompt: str) -> str:
    """The EAI task prompt — ask for full plan."""
    return prompt


def build_subsequence_prompt(
    original_prompt: str,
    executed_actions: list,
    failed_action,
    error_type: str,
    diagnosis,
    full_plan_actions: list,
) -> str:
    """
    Following SDA-Planner paper Section 4.4:
    Ask LLM for ONLY the replacement subsequence [t_start, t_end].
    """
    exec_str = json.dumps(executed_actions, indent=2) if executed_actions else "None"

    # Show the original failing subsequence
    t_start = diagnosis.t_start
    t_end   = diagnosis.t_end
    orig_subseq = [str(s) for s in full_plan_actions
                   if t_start <= s.index <= t_end]
    orig_subseq_str = "\n".join(f"  {s}" for s in orig_subseq) or "  (none)"

    # Explain unsatisfied preconditions
    if diagnosis.unsatisfied_needs:
        unsat_str = "\n".join(
            f"  - {explain_precondition(p)}"
            for p in diagnosis.unsatisfied_needs
        )
    else:
        # ADDITIONAL_STEP or local error
        if error_type == "ADDITIONAL_STEP":
            unsat_str = f"  - Action {failed_action} is UNNECESSARY — remove it completely"
        else:
            unsat_str = f"  - Unexpected environment state at {failed_action}"

    return f"""{original_prompt}

=== EXECUTION ERROR ===
Failed action : {failed_action}
Error type    : {error_type}
Root cause    : {diagnosis.root_cause}

What went wrong:
{unsat_str}

Steps executed successfully before failure:
{exec_str}

Original failing subsequence (steps {t_start} to {t_end}):
{orig_subseq_str}

=== YOUR TASK ===
Generate ONLY a replacement for steps {t_start} to {t_end} that fixes the error above.
The replacement will be spliced back into the plan automatically.
Start from the state at step {t_start - 1} and ensure all preconditions are met.

Output ONLY the replacement JSON subsequence."""


def parse_and_validate(raw: str, relevant_name_to_id: dict):
    """Parse, filter, grammar-check, and convert LLM output."""
    parsed = parse_llm_output(raw)
    if not parsed:
        return None

    parsed = filter_valid_actions(parsed)
    if not parsed:
        logger.warning("All actions filtered out")
        return None

    try:
        ok, err = check_action_grammar(parsed)
        if not ok:
            logger.warning(f"Grammar check failed: {err}")
            return None
    except KeyError as e:
        logger.warning(f"Unknown action {e}")
        return None

    try:
        return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"json_to_action failed: {e}")
        return None


# =============================================================================
# Main Runner
# =============================================================================

class EAISDARunner:

    def __init__(self):
        self.llm = LLMClient()
        logger.info("Loading EAI resources...")
        self.properties_data  = utils.load_properties_data()
        self.object_placing   = utils.load_object_placing()
        self.name_equivalence = utils.load_name_equivalence()
        self.task_dicts       = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
        self.id2task          = json.load(open(ID2TASK_PATH))
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logger.info("EAI resources loaded.")

    def run_all(self, max_tasks=None):
        logger.info("=== EAI + SDA-Planner Runner ===")
        logger.info(f"Model     : {MODEL_NAME}")
        logger.info(f"Provider  : {API_PROVIDER}")
        logger.info(f"Max replan: {MAX_REPLAN}")
        logger.info(f"Max tasks : {max_tasks or 'ALL'}")

        # Resume support
        out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
        if osp.exists(out_path):
            existing = json.load(open(out_path))
            done_ids = {d["identifier"] for d in existing
                        if d["llm_output"] not in ("", "...")}
            outputs  = list(existing)
            logger.info(f"Resuming: {len(done_ids)} tasks already done")
        else:
            outputs, done_ids = [], set()

        total = replan_total = 0

        for task_name, task_files in self.task_dicts.items():
            for file_id, task_goal_dict in task_files.items():

                if max_tasks and total >= max_tasks:
                    logger.info(f"Reached max_tasks={max_tasks}, stopping.")
                    self._save(outputs)
                    return

                if file_id in done_ids:
                    continue

                total += 1
                logger.info(f"\n[{total}] {task_name} | {file_id}")

                result, rc = self.run_single_task(file_id, task_name, task_goal_dict)
                replan_total += rc
                outputs.append({"identifier": file_id, "llm_output": result})

                time.sleep(1)

                if total % 10 == 0:
                    self._save(outputs)

        self._save(outputs)
        logger.info(f"\n=== DONE === tasks={total} replans={replan_total} avg={replan_total/max(total,1):.2f}")

    def run_single_task(self, file_id: str, task_name: str, task_goal_dict: dict):
        goals      = task_goal_dict["vh_goal"]
        node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
        edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

        try:
            motion_planner, _, _, _, _ = construct_planner(
                self.name_equivalence, self.properties_data, self.object_placing,
                scenegraph_id=SCENEGRAPH_ID, script_id=file_id, dataset_root=DATA_DIR,
            )
        except Exception as e:
            logger.error(f"Planner build failed: {e}")
            return "", 0

        _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
            motion_planner.get_symbolic_goal_nl(
                node_goals, edge_goals, action_goals=goals["actions"]
            )
        )
        object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

        import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
        base_prompt = one_shot.prompt
        base_prompt = base_prompt.replace("<object_in_scene>", object_in_scene)
        base_prompt = base_prompt.replace("<cur_change>",      cur_change)
        base_prompt = base_prompt.replace("<node_goals>",      node_goal_str)
        base_prompt = base_prompt.replace("<edge_goals>",      edge_goal_str)
        base_prompt = base_prompt.replace("<action_goals>",    action_goal_str)

        replan_count   = 0
        raw_output     = ""

        # ── Attempt 1: Generate initial full plan ────────────────────────────
        raw_output = self.llm.call(base_prompt)
        logger.info(f"  Initial plan: {raw_output[:100]}...")

        actions = parse_and_validate(raw_output, relevant_name_to_id)
        if not actions:
            return raw_output, 0

        # ── Execute and replan loop ───────────────────────────────────────────
        # current_plan tracks what we will try to execute
        current_plan_eai    = actions   # EAI format actions list
        initial_env_state   = None

        for attempt in range(MAX_REPLAN + 1):

            motion_planner.reset()
            history_actions    = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            # Save initial env state for char state detection
            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable     = True
            failed_action  = None
            err_type       = None

            # for action in current_plan_eai:
            #     exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

            #     if not exe_flag:
            #                         history_cp = copy.deepcopy(history_env_states)
            #                         try:
            #                             checker  = TemporalOrderChecker(my_info, history_cp)
            #                             code     = checker.run_checker().get_error_type()
            #                             err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN")
            #                         except Exception:
            #                             err_type = "UNKNOWN"

            #                         # Match EAI behavior: ADDITIONAL_STEP is not a real failure
            #                         # EAI skips it and continues (see evaluate_results.py line ~180)
            #                         if err_type == "ADDITIONAL_STEP":
            #                             logger.info(f"  ⏭️ Skipping unnecessary action: {action}")
            #                             continue  # skip this action, keep executing rest of plan

            #                         executable    = False
            #                         failed_action = action
            #                         logger.info(f"  ❌ {action} | {err_type}")
            #     else:
            #         history_actions.append(action)
            #         history_env_states.append(
            #             copy.deepcopy(motion_planner.env_state.to_dict())
            #         )

            # if executable:
            #     logger.info(f"  ✅ SUCCESS on attempt {attempt+1}")
            #     break
            for action in current_plan_eai:
                exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

                if not exe_flag:
                    history_cp = copy.deepcopy(history_env_states)
                    try:
                        checker  = TemporalOrderChecker(my_info, history_cp)
                        code     = checker.run_checker().get_error_type()
                        err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN")
                    except Exception:
                        err_type = "UNKNOWN"

                    # Match EAI behavior: ADDITIONAL_STEP is not a real failure
                    if err_type == "ADDITIONAL_STEP":
                        logger.info(f"  ⏭️ Skipping unnecessary action: {action}")
                        continue  # skip, keep executing rest of plan

                    executable    = False
                    failed_action = action
                    logger.info(f"  ❌ {action} | {err_type}")
                    break  # ← don't forget this!

                else:
                    history_actions.append(action)
                    history_env_states.append(
                        copy.deepcopy(motion_planner.env_state.to_dict())
                    )

            if executable:
                logger.info(f"  ✅ SUCCESS on attempt {attempt+1}")
                break
            if attempt == MAX_REPLAN:
                logger.info(f"  ⚠️ Max replanning reached for {file_id}")
                break

            # ── SDA Diagnosis ─────────────────────────────────────────────────
            replan_count += 1

            # Get actual character state from initial environment
            char_sitting, char_lying = get_char_state(initial_env_state)

            # Convert to ActionStep objects
            exec_steps     = [parse_eai_action(a, i+1) for i, a in enumerate(history_actions)]
            failed_step    = parse_eai_action(failed_action, len(exec_steps)+1)
            full_plan_steps = [parse_eai_action(a, i+1) for i, a in enumerate(current_plan_eai)]

            try:
                diagnosis = diagnose_error(
                    action_history = exec_steps,
                    failed_step    = failed_step,
                    error_type     = err_type,
                    full_plan      = full_plan_steps,
                    char_sitting   = char_sitting,
                    char_lying     = char_lying,
                )
                logger.info(f"  🔍 Strategy: {diagnosis.replan_strategy} | "
                            f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
                            f"Unsat: {diagnosis.unsatisfied_needs}")
            except Exception as e:
                logger.warning(f"  Diagnosis failed: {e}")
                break

            # ── Build subsequence prompt (SDA paper Section 4.4) ─────────────
            subseq_prompt = build_subsequence_prompt(
                original_prompt   = base_prompt,
                executed_actions  = history_actions,
                failed_action     = failed_action,
                error_type        = err_type,
                diagnosis         = diagnosis,
                full_plan_actions = full_plan_steps,
            )

            new_raw = self.llm.call(subseq_prompt, system_prompt=SUBSEQUENCE_PROMPT)
            logger.info(f"  Subsequence attempt {attempt+1}: {new_raw[:100]}...")

            new_subseq = parse_and_validate(new_raw, relevant_name_to_id)
            if not new_subseq:
                logger.warning("  Could not parse subsequence, falling back to full replan")
                full_raw = self.llm.call(base_prompt)
                new_full = parse_and_validate(full_raw, relevant_name_to_id)
                if new_full:
                    current_plan_eai = new_full
                    raw_output = full_raw
                break

            # ── Splice: executed[:t_start] + new_subseq + original[t_end+1:] ─
            t_start = diagnosis.t_start - 1   # convert to 0-indexed
            t_end   = diagnosis.t_end         # end of window (1-indexed)

            # history_actions are already executed (0-indexed in list)
            before   = history_actions[:max(0, t_start - 1)]
            after    = current_plan_eai[t_end:]

            current_plan_eai = before + new_subseq + after
            raw_output = new_raw
            logger.info(f"  Spliced plan: {len(before)} + {len(new_subseq)} + {len(after)} = {len(current_plan_eai)} steps")

        return raw_output, replan_count

    def _save(self, outputs: list):
        path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
        with open(path, "w") as f:
            json.dump(outputs, f, indent=4)
        logger.info(f"Saved {len(outputs)} outputs → {path}")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max_tasks", type=int, default=None,
                        help="Max new tasks to process this run (default: all)")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: API key not set!")
        print("Run: export OPENAI_API_KEY='your_key'")
        sys.exit(1)

    EAISDARunner().run_all(max_tasks=args.max_tasks)
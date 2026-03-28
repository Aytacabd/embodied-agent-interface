# """
# eai_sda_runner_tree.py
# ======================
# Full SDA-Planner pipeline with Adaptive Action SubTree Generation.
# Implements paper Sections 4.2, 4.3, 4.4.

# Differences from soft constraint version (eai_sda_runner.py):
#   - Uses BFS search tree (Section 4.4) to generate replacement subsequences
#   - Falls back to LLM only when tree fails or local replan with Unsat=[]
#   - Passes env_dict to diagnosis for accurate per-object state tracking

# Usage:
#     python3 sda_eai/eai_sda_runner_tree.py
#     python3 sda_eai/eai_sda_runner_tree.py --max_tasks 50
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

# sys.path.insert(0, "/opt/iGibson/sda_eai_m")

# import virtualhome_eval.simulation.evolving_graph.utils as utils
# from virtualhome_eval.simulation.evolving_graph.eval_utils import (
#     construct_planner,
#     json_to_action,
#     check_action_grammar,
#     load_json_preserving_order,
# )
# from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

# from sdg import explain_precondition, is_prep_action
# from error_diagnosis_tree import diagnose_error_tree, get_unsatisfied_explanation
# from action_subtree import generate_replacement_subsequence

# logging.basicConfig(
#     level  = logging.INFO,
#     format = "%(asctime)s - %(levelname)s - %(message)s",
# )
# logger = logging.getLogger(__name__)


# # =============================================================================
# # CONFIGURATION
# # =============================================================================

# API_PROVIDER = "openai"
# API_KEY      = os.environ.get("OPENAI_API_KEY", os.environ.get("GROQ_API_KEY", ""))
# MODEL        = "gpt-4o"
# # Derived from MODEL automatically so output filenames stay consistent
# MODEL_NAME   = f"{MODEL}-sda-tree_m"

# MAX_REPLAN     = 3
# SCENEGRAPH_ID  = 1
# TREE_MAX_DEPTH = 6
# TREE_MAX_NODES = 500

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
#     "PUSH", "PULL", "MOVE", "SQUEEZE", "SLEEP", "WAKEUP", "RINSE", "SCRUB",
#     "WASH", "GRAB", "SWITCHOFF", "SWITCHON", "CLOSE", "FIND", "WALK", "OPEN",
#     "POINTAT", "PUTBACK", "PUTIN", "PUTOBJBACK", "RUN", "SIT", "STANDUP",
#     "TURNTO", "WIPE", "PUTON", "PUTOFF", "GREET", "DROP", "LIE", "POUR",
# }

# SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.

# OUTPUT FORMAT - respond with ONLY a JSON object:
# {"ACTION": ["object"], "ACTION": ["object1", "object2"]}

# VALID ACTIONS:
# - 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, SIT, PUTOBJBACK, RUN, TURNTO
# - 2 arguments: PUTBACK, PUTIN, POUR
# - 0 arguments: STANDUP, SLEEP, WAKEUP

# CRITICAL ACTION DISTINCTIONS:
# - PUTON <clothes>          = wear the clothes item ON the character's body (character must HOLD the clothes first)
# - PUTIN <object> <container> = place object INSIDE a container (e.g. PUTIN clothes_pants washing_machine)
# - PUTBACK <object> <surface> = place object ON TOP of a surface
# - Never use PUTON with appliances (washing_machine, dishwasher, etc.) — use PUTIN instead

# RULES:
# - PLUGIN/PLUGOUT do NOT exist — all devices are already plugged in
# - WALK to object before GRAB
# - OPEN containers/appliances before PUTBACK or PUTIN inside them
# - Max 2 objects held at once
# - To wash clothes: GRAB clothes → PUTIN clothes washing_machine (NEVER use PUTBACK or PUTON with washing_machine)
# - PUTIN = put INSIDE a container (washing_machine, fridge, dishwasher, cabinet, box, bag)
# - PUTBACK = put ON TOP of a surface (table, counter, desk, shelf) — never use with appliances
# - Output ONLY the JSON, nothing else"""

# SUGGESTION_PROMPT = """You are fixing a failed action in a VirtualHome robot plan.

# The following action failed:
# {failed_action}

# Error type: {error_type}

# Unsatisfied preconditions:
# {unsat_explanation}

# Generate a SHORT list of corrective actions (2-5 actions) that would fix this error.
# These will be used as candidate nodes in a search tree.

# Output ONLY a JSON object with the corrective actions.
# Example: {{"STANDUP": [], "WALK": ["object"], "GRAB": ["object"]}}"""

# WRONG_ACTION_PROMPT = """You are fixing a VirtualHome robot plan that contains a semantically wrong action.

# The following action is WRONG and must be REPLACED:
# {failed_action}

# Reason: {reason}

# Common mistakes and corrections:
# - PUTON <appliance> is wrong → use PUTIN <clothes> <appliance> to put clothes inside a machine
# - PUTON should only be used with wearable clothing items (e.g. PUTON clothes_pants)
# - To wash clothes: GRAB <clothes> then PUTIN <clothes> <washing_machine>
# - To put food in fridge: GRAB <food> then PUTIN <food> <fridge>

# Generate a corrected sequence of 2-6 actions that achieves the same goal correctly.
# Output ONLY a JSON object.
# Example: {{"WALK": ["washing_machine"], "GRAB": ["clothes_pants"], "PUTIN": ["clothes_pants", "washing_machine"]}}"""


# # =============================================================================
# # LLM Client
# # =============================================================================

# class LLMClient:
#     def __init__(self):
#         if API_PROVIDER == "groq":
#             from groq import Groq
#             self.client   = Groq(api_key=API_KEY)
#             self.provider = "openai_style"
#         elif API_PROVIDER == "openai":
#             from openai import OpenAI
#             self.client   = OpenAI(api_key=API_KEY)
#             self.provider = "openai_style"
#         elif API_PROVIDER == "gemini":
#             import urllib.request
#             self.gemini_url = (
#                 f"https://generativelanguage.googleapis.com/v1beta/models/"
#                 f"{MODEL}:generateContent?key={API_KEY}"
#             )
#             self.provider = "gemini"
#         else:
#             raise ValueError(f"Unknown provider: {API_PROVIDER}")
#         logger.info(f"LLM: {API_PROVIDER} / {MODEL}")

#     def call(self, user_prompt: str, system_prompt: str = None) -> str:
#         if system_prompt is None:
#             system_prompt = SYSTEM_PROMPT
#         if self.provider == "openai_style":
#             return self._call_openai(user_prompt, system_prompt)
#         return self._call_gemini(user_prompt, system_prompt)

#     def _call_openai(self, user_prompt: str, system_prompt: str) -> str:
#         try:
#             response = self.client.chat.completions.create(
#                 model       = MODEL,
#                 temperature = 0,
#                 max_tokens  = 512,
#                 messages    = [
#                     {"role": "system", "content": system_prompt},
#                     {"role": "user",   "content": user_prompt},
#                 ],
#             )
#             return response.choices[0].message.content.strip()
#         except Exception as e:
#             logger.error(f"API error: {e}")
#             return ""

#     def _call_gemini(self, user_prompt: str, system_prompt: str) -> str:
#         import urllib.request
#         full    = system_prompt + "\n\n" + user_prompt
#         payload = json.dumps({
#             "contents":         [{"parts": [{"text": full}]}],
#             "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
#         }).encode()
#         try:
#             req = urllib.request.Request(
#                 self.gemini_url, data=payload,
#                 headers={"Content-Type": "application/json"}, method="POST",
#             )
#             with urllib.request.urlopen(req, timeout=30) as r:
#                 d = json.loads(r.read())
#                 return d["candidates"][0]["content"]["parts"][0]["text"].strip()
#         except Exception as e:
#             logger.error(f"Gemini error: {e}")
#             return ""


# # =============================================================================
# # Helpers
# # =============================================================================

# def parse_llm_output(raw: str):
#     """
#     Parse LLM JSON output handling markdown fences and 0-arg actions.

#     EAI's load_json_preserving_order requires 0-arg actions to carry
#     ["character"] temporarily. We inject it here and strip it back
#     in parse_and_validate before grammar checking.
#     """
#     raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
#     # Inject placeholder so load_json_preserving_order doesn't skip 0-arg entries
#     raw = re.sub(
#         r'"(STANDUP|SLEEP|WAKEUP)"\s*:\s*\[\]',
#         r'"\1": ["character"]',
#         raw, flags=re.IGNORECASE
#     )
#     match = re.search(r'\{.*\}', raw, re.DOTALL)
#     if not match:
#         return []
#     try:
#         return load_json_preserving_order(match.group(0))
#     except Exception:
#         return []


# def filter_valid_actions(parsed):
#     """Remove actions not in the EAI valid set (e.g. PLUGIN, PLUGOUT, RELEASE)."""
#     if isinstance(parsed, dict):
#         return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
#     elif isinstance(parsed, list):
#         return [a for a in parsed
#                 if isinstance(a, dict) and
#                 list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
#     return parsed


# def parse_eai_action(action, index: int):
#     """Convert EAI action string '[ACTION] <obj> (id)' to ActionStep."""
#     from error_diagnosis import ActionStep
#     s  = str(action)
#     am = re.search(r'\[(\w+)\]', s)
#     om = re.findall(r'<([^>]+)>', s)
#     return ActionStep(
#         index  = index,
#         action = am.group(1).upper() if am else "UNKNOWN",
#         obj    = om[0].strip() if om else "unknown",
#         target = om[1].strip() if len(om) > 1 else None,
#     )


# def get_char_state(env_state_dict: dict):
#     """Extract character sitting/lying state from EAI env state dict."""
#     try:
#         for node in env_state_dict.get("nodes", []):
#             if node.get("class_name") == "character":
#                 states = node.get("states", [])
#                 return "SITTING" in states, "LYING" in states
#     except Exception:
#         pass
#     return False, False


# def parse_and_validate(raw: str, relevant_name_to_id: dict):
#     """Parse, filter, grammar-check, and convert LLM output to EAI actions."""
#     parsed = parse_llm_output(raw)
#     if not parsed:
#         return None

#     parsed = filter_valid_actions(parsed)

#         # PUTBACK → PUTIN auto-correction for container targets
#     # LLM sometimes uses PUTBACK for washing_machine, fridge, dishwasher etc.
#     # PUTBACK = place on surface; PUTIN = place inside container
#     CONTAINER_OBJECTS = {
#         "washing_machine", "fridge", "freezer", "dishwasher",
#         "microwave", "stove", "cabinet", "kitchencabinets",
#         "bathroomcabinet", "garbagecan", "box", "bag",
#     }
#     corrected = []
#     for item in (parsed if isinstance(parsed, list) else
#                 [{k: v} for k, v in parsed.items()]):
#         for action, args in item.items():
#             if (action.upper() == "PUTBACK"
#                     and isinstance(args, list)
#                     and len(args) == 2
#                     and args[1].lower() in CONTAINER_OBJECTS):
#                 corrected.append({"PUTIN": args})
#                 logger.info(f"  🔄 Auto-corrected PUTBACK→PUTIN for container: {args[1]}")
#             else:
#                 corrected.append({action: args})
#     parsed = corrected
#     if not parsed:
#         return None

#     # Strip "character" placeholder back to [] for grammar checker.
#     # See parse_llm_output for why the round-trip exists.
#     ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
#     cleaned  = []
#     for item in (parsed if isinstance(parsed, list) else
#                  [{k: v} for k, v in parsed.items()]):
#         for action, args in item.items():
#             if action.upper() in ZERO_ARG:
#                 cleaned.append({action: []})
#             else:
#                 cleaned.append({action: args})
#     parsed = cleaned
#     if not parsed:
#         return None

#     try:
#         ok, err = check_action_grammar(parsed)
#         if not ok:
#             logger.warning(f"Grammar check failed: {err}")
#             return None
#     except KeyError as e:
#         logger.warning(f"Unknown action in grammar check: {e}")
#         return None

#     try:
#         return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
#     except Exception as e:
#         logger.warning(f"json_to_action failed: {e}")
#         return None


# def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict):
#     """Convert search tree output (list of dicts) to EAI action string list."""
#     if not subtree_result:
#         return None

#     filtered = filter_valid_actions(subtree_result)
#     if not filtered:
#         return None

#     ZERO_ARG  = {"STANDUP", "SLEEP", "WAKEUP"}
#     processed = []
#     for item in (filtered if isinstance(filtered, list) else
#                  [{k: v} for k, v in filtered.items()]):
#         for action, args in item.items():
#             if action.upper() in ZERO_ARG:
#                 processed.append({action: []})
#             else:
#                 processed.append({action: args})

#     try:
#         ok, _ = check_action_grammar(processed)
#         if not ok:
#             return None
#         return json_to_action(processed, relevant_name_to_id=relevant_name_to_id)
#     except Exception as e:
#         logger.warning(f"Subtree result conversion failed: {e}")
#         return None


# def plan_to_json_str(eai_actions: list) -> str:
#     """
#     Convert EAI action list to JSON string for the evaluator.

#     EAI internal format : [WALK] <light> (245)
#     Required output     : {"WALK": ["light"]}
#     2-arg output        : {"PUTIN": ["clothes", "washing_machine"]}

#     The evaluator matches on object names only — IDs are internal to EAI
#     execution and must NOT appear in the saved output.
#     Duplicate action keys are preserved via manual string building.
#     """
#     parts = []
#     for action in eai_actions:
#         s     = str(action)
#         am    = re.search(r'\[(\w+)\]', s)
#         names = re.findall(r'<([^>]+)>', s)
#         if not am:
#             continue
#         action_name = am.group(1)
#         if not names:
#             parts.append(f'"{action_name}": []')
#         elif len(names) == 1:
#             parts.append(f'"{action_name}": ["{names[0].strip()}"]')
#         elif len(names) == 2:
#             parts.append(
#                 f'"{action_name}": ["{names[0].strip()}", "{names[1].strip()}"]'
#             )
#     return "{" + ", ".join(parts) + "}"


# # =============================================================================
# # Main Runner
# # =============================================================================

# class EAISDATreeRunner:

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
#         logger.info("=== EAI + SDA-Planner (Full Search Tree) ===")
#         logger.info(f"Model      : {MODEL_NAME}")
#         logger.info(f"Provider   : {API_PROVIDER}")
#         logger.info(f"Max replan : {MAX_REPLAN}")
#         logger.info(f"Tree depth : {TREE_MAX_DEPTH} | Tree nodes: {TREE_MAX_NODES}")
#         logger.info(f"Max tasks  : {max_tasks or 'ALL'}")

#         out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
#         if osp.exists(out_path):
#             existing = json.load(open(out_path))
#             done_ids = {d["identifier"] for d in existing
#                         if d["llm_output"] not in ("", "...")}
#             outputs  = list(existing)
#             logger.info(f"Resuming: {len(done_ids)} tasks already done")
#         else:
#             outputs, done_ids = [], set()

#         total = replan_total = tree_success = fallback_count = 0

#         for task_name, task_files in self.task_dicts.items():
#             for file_id, task_goal_dict in task_files.items():

#                 if max_tasks and total >= max_tasks:
#                     logger.info(f"Reached max_tasks={max_tasks}, stopping.")
#                     self._save(outputs)
#                     return

#                 if file_id in done_ids:
#                     continue

#                 total += 1
#                 logger.info(f"\n[{total}] {task_name} | {file_id}")

#                 result, rc, ts, fb = self.run_single_task(
#                     file_id, task_name, task_goal_dict
#                 )
#                 replan_total   += rc
#                 tree_success   += ts
#                 fallback_count += fb
#                 outputs.append({"identifier": file_id, "llm_output": result})

#                 time.sleep(1)

#                 if total % 10 == 0:
#                     self._save(outputs)
#                     logger.info(
#                         f"Progress: {total} | "
#                         f"Tree: {tree_success} | Fallback: {fallback_count}"
#                     )

#         self._save(outputs)
#         logger.info(f"\n=== DONE ===")
#         logger.info(f"Total tasks    : {total}")
#         logger.info(f"Total replans  : {replan_total}")
#         logger.info(f"Tree successes : {tree_success}")
#         logger.info(f"LLM fallbacks  : {fallback_count}")
#         logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")

#     def run_single_task(self, file_id, task_name, task_goal_dict):
#         """Returns (raw_output, replan_count, tree_success_count, fallback_count)"""
#         goals      = task_goal_dict["vh_goal"]
#         node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
#         edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

#         try:
#             motion_planner, _, _, _, _ = construct_planner(
#                 self.name_equivalence, self.properties_data, self.object_placing,
#                 scenegraph_id = SCENEGRAPH_ID,
#                 script_id     = file_id,
#                 dataset_root  = DATA_DIR,
#             )
#         except Exception as e:
#             logger.error(f"Planner build failed: {e}")
#             return "", 0, 0, 0

#         _, _, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
#             motion_planner.get_symbolic_goal_nl(
#                 node_goals, edge_goals, action_goals=goals["actions"]
#             )
#         )
#         object_in_scene, cur_change, _ = motion_planner.get_nl_goal_string()

#         import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot
#         base_prompt = one_shot.prompt
#         base_prompt = base_prompt.replace("<object_in_scene>", object_in_scene)
#         base_prompt = base_prompt.replace("<cur_change>",      cur_change)
#         base_prompt = base_prompt.replace("<node_goals>",      node_goal_str)
#         base_prompt = base_prompt.replace("<edge_goals>",      edge_goal_str)
#         base_prompt = base_prompt.replace("<action_goals>",    action_goal_str)

#         replan_count   = 0
#         tree_success   = 0
#         fallback_count = 0
#         raw_output     = ""

#         # ── Generate initial plan ─────────────────────────────────────────────
#         raw_output = self.llm.call(base_prompt)
#         logger.info(f"  Initial plan: {raw_output[:100]}...")

#         actions = parse_and_validate(raw_output, relevant_name_to_id)
#         if not actions:
#             logger.warning(f"  Could not parse initial plan for {file_id}")
#             return raw_output, 0, 0, 0

#         current_plan_eai  = actions
#         initial_env_state = None

#         for attempt in range(MAX_REPLAN + 1):

#             motion_planner.reset()
#             history_actions    = []
#             history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

#             if initial_env_state is None:
#                 initial_env_state = history_env_states[0]

#             executable    = True
#             failed_action = None
#             err_type      = None
#             skipped_indices = set()   # track positions of skipped actions

#             # ── Execute current plan ──────────────────────────────────────────
#             for action_idx, action in enumerate(current_plan_eai):
#                 exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

#                 if not exe_flag:
#                     history_cp = copy.deepcopy(history_env_states)
#                     try:
#                         checker  = TemporalOrderChecker(my_info, history_cp)
#                         code     = checker.run_checker().get_error_type()
#                         err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN_ERROR")
#                     except Exception as ex:
#                         logger.warning(f"  TemporalOrderChecker failed: {ex}")
#                         err_type = "UNKNOWN_ERROR"

#                     # ADDITIONAL_STEP: action is unnecessary — skip and remove
#                     # from plan so it doesn't appear in saved output
#                     if err_type == "ADDITIONAL_STEP":
#                         logger.info(f"  ⏭️  Skipping: {action}")
#                         skipped_indices.add(action_idx)
#                         continue

#                     executable    = False
#                     failed_action = action
#                     logger.info(f"  ❌ {action} | {err_type}")
#                     break
#                 else:
#                     history_actions.append(action)
#                     history_env_states.append(
#                         copy.deepcopy(motion_planner.env_state.to_dict())
#                     )

#             if executable:
#                 # Remove skipped actions from plan before saving
#                 clean_plan = [a for i, a in enumerate(current_plan_eai)
#                               if i not in skipped_indices]
#                 raw_output = plan_to_json_str(clean_plan)
#                 logger.info(f"  ✅ SUCCESS on attempt {attempt + 1}"
#                             + (f" (removed {len(skipped_indices)} skipped actions)"
#                                if skipped_indices else ""))
#                 break

#             if attempt == MAX_REPLAN:
#                 logger.info(f"  ⚠️  Max replanning reached for {file_id}")
#                 break

#             # ── SDA Error Backtrack and Diagnosis (Section 4.3) ──────────────
#             replan_count += 1

#             env_at_failure   = (history_env_states[-1]
#                                 if history_env_states else initial_env_state)
#             char_sitting, char_lying = get_char_state(env_at_failure)

#             exec_steps      = [parse_eai_action(a, i + 1)
#                                for i, a in enumerate(history_actions)]
#             failed_step     = parse_eai_action(failed_action, len(exec_steps) + 1)
#             full_plan_steps = [parse_eai_action(a, i + 1)
#                                for i, a in enumerate(current_plan_eai)]

#             try:
#                 diagnosis, orig_subseq, error_objects = diagnose_error_tree(
#                     action_history = exec_steps,
#                     failed_step    = failed_step,
#                     error_type     = err_type,
#                     full_plan      = full_plan_steps,
#                     char_sitting   = char_sitting,
#                     char_lying     = char_lying,
#                     env_dict       = env_at_failure,
#                 )
#                 logger.info(
#                     f"  🔍 Strategy: {diagnosis.replan_strategy} | "
#                     f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
#                     f"Unsat: {diagnosis.unsatisfied_needs}"
#                 )
#             except Exception as e:
#                 logger.warning(f"  Diagnosis failed: {e}", exc_info=True)
#                 break

#             # ── Compute splice window ─────────────────────────────────────────
#             t_start = diagnosis.t_start if diagnosis.t_start is not None else failed_step.index
#             t_end   = diagnosis.t_end   if diagnosis.t_end   is not None else failed_step.index

#             before = history_actions[:max(0, t_start - 1)]
#             after  = current_plan_eai[t_end:]

#             # ── Already satisfied: goal state already true → drop the action ──
#             if diagnosis.replan_strategy == "already_satisfied":
#                 logger.info(f"  ✅ Goal already met — removing redundant: {failed_action}")
#                 # Remove just this one action from the plan and retry
#                 current_plan_eai = [a for i, a in enumerate(current_plan_eai)
#                                     if a != failed_action or i < len(history_actions)]
#                 # Safer: remove the first occurrence after the history boundary
#                 boundary = len(history_actions)
#                 removed  = False
#                 new_plan = []
#                 for i, a in enumerate(current_plan_eai):
#                     if not removed and i >= boundary and a == failed_action:
#                         removed = True   # skip this one occurrence
#                     else:
#                         new_plan.append(a)
#                 current_plan_eai = new_plan
#                 raw_output       = plan_to_json_str(current_plan_eai)
#                 continue

#             # ── Local replan with Unsat=[] → go straight to LLM ──────────────
#             if diagnosis.replan_strategy == "local" and not diagnosis.unsatisfied_needs:
#                 logger.info("  ⏭️  Local replan (Unsat=[]) — LLM fallback")
#                 fallback_count += 1
#                 fallback_raw    = self.llm.call(base_prompt)
#                 new_subseq      = parse_and_validate(fallback_raw, relevant_name_to_id)
#                 if new_subseq:
#                     current_plan_eai = new_subseq
#                     raw_output       = plan_to_json_str(current_plan_eai)
#                 continue

#             # ── Wrong action: action is semantically wrong for this object ────
#             # e.g. PUTON <washing_machine> — use a targeted replacement prompt
#             if diagnosis.replan_strategy == "wrong_action":
#                 logger.info(f"  🔄 Wrong action detected: {failed_action} — asking LLM to replace")
#                 fallback_count += 1
#                 wrong_prompt = WRONG_ACTION_PROMPT.format(
#                     failed_action = failed_action,
#                     reason        = (
#                         f"The object '{failed_step.obj}' cannot be held/worn. "
#                         f"Check if you meant PUTIN (put inside container) "
#                         f"instead of PUTON (wear on body)."
#                     ),
#                 )
#                 wrong_raw  = self.llm.call(wrong_prompt, system_prompt=SYSTEM_PROMPT)
#                 new_subseq = parse_and_validate(wrong_raw, relevant_name_to_id)
#                 if new_subseq:
#                     # Splice: replace only the wrong action and everything after it
#                     before           = history_actions   # keep what worked
#                     current_plan_eai = before + new_subseq
#                     raw_output       = plan_to_json_str(current_plan_eai)
#                     logger.info(f"  🔄 Replaced with: {wrong_raw[:80]}...")
#                 else:
#                     fallback_raw = self.llm.call(base_prompt)
#                     new_subseq   = parse_and_validate(fallback_raw, relevant_name_to_id)
#                     if new_subseq:
#                         current_plan_eai = new_subseq
#                         raw_output       = plan_to_json_str(current_plan_eai)
#                 continue

#             # ── Step 1: Get LLM corrective suggestions (Section 4.4) ──────────
#             suggestion_prompt = SUGGESTION_PROMPT.format(
#                 failed_action     = failed_action,
#                 error_type        = err_type,
#                 unsat_explanation = get_unsatisfied_explanation(
#                     diagnosis.unsatisfied_needs
#                 ),
#             )
#             suggestion_raw  = self.llm.call(suggestion_prompt,
#                                             system_prompt=SYSTEM_PROMPT)
#             llm_suggestions = parse_llm_output(suggestion_raw)
#             llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
#             if isinstance(llm_suggestions, dict):
#                 llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]

#             logger.info(f"  💡 LLM suggestions: {suggestion_raw[:80]}...")

#             # ── Step 2: Build BFS search tree (Section 4.4) ───────────────────
#             # Use env state at t_start - 1 as initial state for the tree
#             state_idx       = max(0, t_start - 2)
#             state_at_tstart = (history_env_states[state_idx]
#                                if state_idx < len(history_env_states)
#                                else history_env_states[0])
#             # FIX: pass env_at_failure as initial_state_dict so the tree can
#             # look up container relationships that exist at failure time.
#             # state_at_tstart may predate the INSIDE edges being established.
#             state_at_tstart = env_at_failure

#             orig_subseq_dicts = []
#             for s in orig_subseq:
#                 if hasattr(s, "target") and s.target:
#                     orig_subseq_dicts.append({s.action: [s.obj, s.target]})
#                 elif hasattr(s, "obj"):
#                     orig_subseq_dicts.append({s.action: [s.obj]})

#             tree_result = generate_replacement_subsequence(
#                 llm_suggestions      = llm_suggestions,
#                 original_subsequence = orig_subseq_dicts,
#                 initial_state_dict   = state_at_tstart,
#                 unsatisfied_needs    = diagnosis.unsatisfied_needs,
#                 error_objects        = error_objects,
#                 char_sitting         = char_sitting,
#                 char_lying           = char_lying,
#                 max_depth            = TREE_MAX_DEPTH,
#                 max_nodes            = TREE_MAX_NODES,
#             )

#             # if tree_result:
#             #     logger.info(f"  🌳 Tree found: {tree_result}")
#             #     tree_success += 1
#             #     new_subseq    = subtree_results_to_eai(tree_result, relevant_name_to_id)
#             # else:
#             #     logger.info("  🌳 Tree failed — LLM fallback")
#             #     fallback_count += 1
#             #     new_subseq     = None

#             # # ── Step 3: LLM fallback if tree failed ───────────────────────────
#             # if not new_subseq:
#             #     fallback_raw = self.llm.call(base_prompt)
#             #     new_subseq   = parse_and_validate(fallback_raw, relevant_name_to_id)
#             #     if new_subseq:
#             #         current_plan_eai = new_subseq
#             #         raw_output       = plan_to_json_str(current_plan_eai)
#             #     continue
#             if tree_result:
#                 logger.info(f"  🌳 Tree found: {tree_result}")
#                 tree_success += 1
#                 new_subseq    = subtree_results_to_eai(tree_result, relevant_name_to_id)
#             else:
#                 logger.info("  🌳 Tree failed — skipping replan (no fallback)")
#                 new_subseq = None

#             # ── Step 3: LLM fallback DISABLED — tree only ─────────────────────
#             if not new_subseq:
#                 continue

#             # ── Step 4: Splice replacement into plan ──────────────────────────
#             current_plan_eai = before + new_subseq + after
#             raw_output       = plan_to_json_str(current_plan_eai)
#             logger.info(
#                 f"  Spliced: {len(before)} + {len(new_subseq)} + "
#                 f"{len(after)} = {len(current_plan_eai)}"
#             )

#         return raw_output, replan_count, tree_success, fallback_count

#     def _save(self, outputs: list):
#         path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
#         with open(path, "w") as f:
#             json.dump(outputs, f, indent=4)
#         logger.info(f"Saved {len(outputs)} outputs → {path}")


# # =============================================================================
# # Entry Point
# # =============================================================================

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--max_tasks", type=int, default=None)
#     args = parser.parse_args()

#     if not API_KEY:
#         print("ERROR: API key not set!")
#         print("Run: export OPENAI_API_KEY='your_key'")
#         sys.exit(1)

#     EAISDATreeRunner().run_all(max_tasks=args.max_tasks)
"""
eai_sda_runner_tree.py
======================
Full SDA-Planner pipeline with Adaptive Action SubTree Generation.
Implements paper Sections 4.2, 4.3, 4.4.

Differences from soft constraint version (eai_sda_runner.py):
  - Uses BFS search tree (Section 4.4) to generate replacement subsequences
  - Falls back to LLM only when tree fails or local replan with Unsat=[]
  - Passes env_dict to diagnosis for accurate per-object state tracking

Usage:
    python3 sda_eai/eai_sda_runner_tree.py
    python3 sda_eai/eai_sda_runner_tree.py --max_tasks 50
    python3 sda_eai/eai_sda_runner_tree.py --task_ids 650_2,190_1,487_1
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

sys.path.insert(0, "/opt/iGibson/sda_eai_m")

import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    construct_planner,
    json_to_action,
    check_action_grammar,
    load_json_preserving_order,
)
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

from sdg import explain_precondition, is_prep_action
from error_diagnosis_tree import diagnose_error_tree, get_unsatisfied_explanation
from action_subtree import generate_replacement_subsequence

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

API_PROVIDER = "openai"
API_KEY      = os.environ.get("OPENAI_API_KEY", os.environ.get("GROQ_API_KEY", ""))
MODEL        = "gpt-4o"
MODEL_NAME   = f"{MODEL}-sda-tree_m"

MAX_REPLAN     = 3
SCENEGRAPH_ID  = 1
TREE_MAX_DEPTH = 6
TREE_MAX_NODES = 500

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
    "DRINK", "EAT", "CUT", "TOUCH", "LOOKAT", "WATCH", "READ", "TYPE",
    "PUSH", "PULL", "MOVE", "SQUEEZE", "SLEEP", "WAKEUP", "RINSE", "SCRUB",
    "WASH", "GRAB", "SWITCHOFF", "SWITCHON", "CLOSE", "FIND", "WALK", "OPEN",
    "POINTAT", "PUTBACK", "PUTIN", "PUTOBJBACK", "RUN", "SIT", "STANDUP",
    "TURNTO", "WIPE", "PUTON", "PUTOFF", "GREET", "DROP", "LIE", "POUR",
}

SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.

OUTPUT FORMAT - respond with ONLY a JSON object:
{"ACTION": ["object"], "ACTION": ["object1", "object2"]}

VALID ACTIONS:
- 1 argument: DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, SIT, PUTOBJBACK, RUN, TURNTO
- 2 arguments: PUTBACK, PUTIN, POUR
- 0 arguments: STANDUP, SLEEP, WAKEUP

RULE 1 — ALWAYS WALK FIRST:
Every plan MUST start with WALK to the first object you will interact with.
NEVER start with OPEN, GRAB, SWITCHON, TYPE or any other action — always WALK first.
Example: WALK dishwasher → OPEN dishwasher → WALK plate → GRAB plate

RULE 2 — PUTIN vs PUTBACK (CRITICAL):
- PUTIN <object> <container> = place object INSIDE an enclosed container
  Use PUTIN for: washing_machine, dishwasher, fridge, freezer, microwave, cabinet, box, bag, trashcan
- PUTBACK <object> <surface> = place object ON TOP of an open surface
  Use PUTBACK for: table, counter, desk, shelf, nightstand, sofa, bench, chair
- NEVER use PUTBACK with washing_machine, dishwasher, fridge, freezer, microwave, cabinet
- NEVER use PUTON with any appliance — PUTON is only for wearing clothes on your body

RULE 3 — GRAB from containers:
If an object is stored inside a closed container (cabinet, fridge, etc.), you MUST:
WALK <container> → OPEN <container> → WALK <object> → GRAB <object>
Never attempt GRAB without first opening the container the object is in.

RULE 4 — PLUGIN/PLUGOUT do not exist. All devices are already plugged in.

RULE 5 — Max 2 objects held at once. DROP or PUTBACK before grabbing a third.

Output ONLY the JSON, nothing else"""

SUGGESTION_PROMPT = """You are fixing a failed action in a VirtualHome robot plan.

The following action failed:
{failed_action}

Error type: {error_type}

Unsatisfied preconditions:
{unsat_explanation}

Generate a SHORT list of corrective actions (2-5 actions) that would fix this error.
These will be used as candidate nodes in a search tree.

Output ONLY a JSON object with the corrective actions.
Example: {{"STANDUP": [], "WALK": ["object"], "GRAB": ["object"]}}"""

WRONG_ACTION_PROMPT = """You are fixing a VirtualHome robot plan that contains a semantically wrong action.

The following action is WRONG and must be REPLACED:
{failed_action}

Reason: {reason}

Common mistakes and corrections:
- PUTON <appliance> is wrong → use PUTIN <clothes> <appliance> to put clothes inside a machine
- PUTON should only be used with wearable clothing items (e.g. PUTON clothes_pants)
- To wash clothes: GRAB <clothes> then PUTIN <clothes> <washing_machine>
- To put food in fridge: GRAB <food> then PUTIN <food> <fridge>

Generate a corrected sequence of 2-6 actions that achieves the same goal correctly.
Output ONLY a JSON object.
Example: {{"WALK": ["washing_machine"], "GRAB": ["clothes_pants"], "PUTIN": ["clothes_pants", "washing_machine"]}}"""


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
            import urllib.request
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
                max_tokens  = 512,
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
        import urllib.request
        full    = system_prompt + "\n\n" + user_prompt
        payload = json.dumps({
            "contents":         [{"parts": [{"text": full}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
        }).encode()
        try:
            req = urllib.request.Request(
                self.gemini_url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
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

def parse_llm_output(raw: str):
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
    raw = re.sub(
        r'"(STANDUP|SLEEP|WAKEUP)"\s*:\s*\[\]',
        r'"\1": ["character"]',
        raw, flags=re.IGNORECASE
    )
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return []
    try:
        return load_json_preserving_order(match.group(0))
    except Exception:
        return []


def filter_valid_actions(parsed):
    if isinstance(parsed, dict):
        return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
    elif isinstance(parsed, list):
        return [a for a in parsed
                if isinstance(a, dict) and
                list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
    return parsed


def parse_eai_action(action, index: int):
    from error_diagnosis import ActionStep
    s  = str(action)
    am = re.search(r'\[(\w+)\]', s)
    om = re.findall(r'<([^>]+)>', s)
    return ActionStep(
        index  = index,
        action = am.group(1).upper() if am else "UNKNOWN",
        obj    = om[0].strip() if om else "unknown",
        target = om[1].strip() if len(om) > 1 else None,
    )


def get_char_state(env_state_dict: dict):
    try:
        for node in env_state_dict.get("nodes", []):
            if node.get("class_name") == "character":
                states = node.get("states", [])
                return "SITTING" in states, "LYING" in states
    except Exception:
        pass
    return False, False


def parse_and_validate(raw: str, relevant_name_to_id: dict):
    parsed = parse_llm_output(raw)
    if not parsed:
        return None

    parsed = filter_valid_actions(parsed)

    CONTAINER_OBJECTS = {
        "washing_machine", "fridge", "freezer", "dishwasher",
        "microwave", "stove", "cabinet", "kitchencabinets",
        "bathroomcabinet", "garbagecan", "box", "bag",
    }
    corrected = []
    for item in (parsed if isinstance(parsed, list) else
                [{k: v} for k, v in parsed.items()]):
        for action, args in item.items():
            if (action.upper() == "PUTBACK"
                    and isinstance(args, list)
                    and len(args) == 2
                    and args[1].lower() in CONTAINER_OBJECTS):
                corrected.append({"PUTIN": args})
                logger.info(f"  🔄 Auto-corrected PUTBACK→PUTIN for container: {args[1]}")
            else:
                corrected.append({action: args})
    parsed = corrected
    if not parsed:
        return None

    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    cleaned  = []
    for item in (parsed if isinstance(parsed, list) else
                 [{k: v} for k, v in parsed.items()]):
        for action, args in item.items():
            if action.upper() in ZERO_ARG:
                cleaned.append({action: []})
            else:
                cleaned.append({action: args})
    parsed = cleaned
    if not parsed:
        return None

    try:
        ok, err = check_action_grammar(parsed)
        if not ok:
            logger.warning(f"Grammar check failed: {err}")
            return None
    except KeyError as e:
        logger.warning(f"Unknown action in grammar check: {e}")
        return None

    try:
        return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"json_to_action failed: {e}")
        return None


def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict):
    if not subtree_result:
        return None

    filtered = filter_valid_actions(subtree_result)
    if not filtered:
        return None

    ZERO_ARG  = {"STANDUP", "SLEEP", "WAKEUP"}
    processed = []
    for item in (filtered if isinstance(filtered, list) else
                 [{k: v} for k, v in filtered.items()]):
        for action, args in item.items():
            if action.upper() in ZERO_ARG:
                processed.append({action: []})
            else:
                processed.append({action: args})

    try:
        ok, _ = check_action_grammar(processed)
        if not ok:
            return None
        return json_to_action(processed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"Subtree result conversion failed: {e}")
        return None


def plan_to_json_str(eai_actions: list) -> str:
    parts = []
    for action in eai_actions:
        s     = str(action)
        am    = re.search(r'\[(\w+)\]', s)
        names = re.findall(r'<([^>]+)>', s)
        if not am:
            continue
        action_name = am.group(1)
        if not names:
            parts.append(f'"{action_name}": []')
        elif len(names) == 1:
            parts.append(f'"{action_name}": ["{names[0].strip()}"]')
        elif len(names) == 2:
            parts.append(
                f'"{action_name}": ["{names[0].strip()}", "{names[1].strip()}"]'
            )
    return "{" + ", ".join(parts) + "}"


# =============================================================================
# Main Runner
# =============================================================================

class EAISDATreeRunner:

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

    def run_all(self, max_tasks=None, task_ids=None):
        logger.info("=== EAI + SDA-Planner (Full Search Tree) ===")
        logger.info(f"Model      : {MODEL_NAME}")
        logger.info(f"Provider   : {API_PROVIDER}")
        logger.info(f"Max replan : {MAX_REPLAN}")
        logger.info(f"Tree depth : {TREE_MAX_DEPTH} | Tree nodes: {TREE_MAX_NODES}")
        logger.info(f"Max tasks  : {max_tasks or 'ALL'}")
        logger.info(f"Task IDs   : {task_ids or 'ALL'}")
        logger.info(f"LLM fallback on tree fail: DISABLED")

        out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
        if osp.exists(out_path):
            existing = json.load(open(out_path))
            done_ids = {d["identifier"] for d in existing
                        if d["llm_output"] not in ("", "...")}
            outputs  = list(existing)
            logger.info(f"Resuming: {len(done_ids)} tasks already done")
        else:
            outputs, done_ids = [], set()

        total = replan_total = tree_success = fallback_count = 0

        for task_name, task_files in self.task_dicts.items():
            for file_id, task_goal_dict in task_files.items():

                # ── Filter by task_ids if provided ────────────────────────────
                if task_ids and file_id not in task_ids:
                    continue

                if max_tasks and total >= max_tasks:
                    logger.info(f"Reached max_tasks={max_tasks}, stopping.")
                    self._save(outputs)
                    return

                if file_id in done_ids:
                    continue

                total += 1
                logger.info(f"\n[{total}] {task_name} | {file_id}")

                result, rc, ts, fb = self.run_single_task(
                    file_id, task_name, task_goal_dict
                )
                replan_total   += rc
                tree_success   += ts
                fallback_count += fb
                outputs.append({"identifier": file_id, "llm_output": result})

                time.sleep(1)

                if total % 10 == 0:
                    self._save(outputs)
                    logger.info(
                        f"Progress: {total} | "
                        f"Tree: {tree_success} | Fallback: {fallback_count}"
                    )

        self._save(outputs)
        logger.info(f"\n=== DONE ===")
        logger.info(f"Total tasks    : {total}")
        logger.info(f"Total replans  : {replan_total}")
        logger.info(f"Tree successes : {tree_success}")
        logger.info(f"LLM fallbacks  : {fallback_count}")
        logger.info(f"Avg replans    : {replan_total/max(total,1):.2f}")

    def run_single_task(self, file_id, task_name, task_goal_dict):
        """Returns (raw_output, replan_count, tree_success_count, fallback_count)"""
        goals      = task_goal_dict["vh_goal"]
        node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
        edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

        try:
            motion_planner, _, _, _, _ = construct_planner(
                self.name_equivalence, self.properties_data, self.object_placing,
                scenegraph_id = SCENEGRAPH_ID,
                script_id     = file_id,
                dataset_root  = DATA_DIR,
            )
        except Exception as e:
            logger.error(f"Planner build failed: {e}")
            return "", 0, 0, 0

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
        tree_success   = 0
        fallback_count = 0
        raw_output     = ""

        # ── Generate initial plan ─────────────────────────────────────────────
        raw_output = self.llm.call(base_prompt)
        logger.info(f"  Initial plan: {raw_output[:100]}...")

        actions = parse_and_validate(raw_output, relevant_name_to_id)
        if not actions:
            logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        current_plan_eai  = actions
        initial_env_state = None

        for attempt in range(MAX_REPLAN + 1):

            motion_planner.reset()
            history_actions    = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable      = True
            failed_action   = None
            err_type        = None
            skipped_indices = set()

            # ── Execute current plan ──────────────────────────────────────────
            for action_idx, action in enumerate(current_plan_eai):
                exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

                if not exe_flag:
                    history_cp = copy.deepcopy(history_env_states)
                    try:
                        checker  = TemporalOrderChecker(my_info, history_cp)
                        code     = checker.run_checker().get_error_type()
                        err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN_ERROR")
                    except Exception as ex:
                        logger.warning(f"  TemporalOrderChecker failed: {ex}")
                        err_type = "UNKNOWN_ERROR"

                    if err_type == "ADDITIONAL_STEP":
                        logger.info(f"  ⏭️  Skipping: {action}")
                        skipped_indices.add(action_idx)
                        continue

                    executable    = False
                    failed_action = action
                    logger.info(f"  ❌ {action} | {err_type}")
                    break
                else:
                    history_actions.append(action)
                    history_env_states.append(
                        copy.deepcopy(motion_planner.env_state.to_dict())
                    )

            if executable:
                clean_plan = [a for i, a in enumerate(current_plan_eai)
                              if i not in skipped_indices]
                raw_output = plan_to_json_str(clean_plan)
                logger.info(f"  ✅ SUCCESS on attempt {attempt + 1}"
                            + (f" (removed {len(skipped_indices)} skipped actions)"
                               if skipped_indices else ""))
                break

            if attempt == MAX_REPLAN:
                logger.info(f"  ⚠️  Max replanning reached for {file_id}")
                break

            # ── SDA Error Backtrack and Diagnosis (Section 4.3) ──────────────
            replan_count += 1

            env_at_failure   = (history_env_states[-1]
                                if history_env_states else initial_env_state)
            char_sitting, char_lying = get_char_state(env_at_failure)

            # # DEBUG: log INSIDE edges in env_at_failure
            # _inside_edges = [e for e in env_at_failure.get("edges", [])
            #                  if e.get("relation_type") == "INSIDE"]
            # _nodes_map    = {n["id"]: n["class_name"]
            #                  for n in env_at_failure.get("nodes", [])}
            # if _inside_edges:
            #     for _e in _inside_edges:
            #         logger.info(
            #             f"  [DEBUG] INSIDE: "
            #             f"{_nodes_map.get(_e['from_id'], _e['from_id'])} → "
            #             f"{_nodes_map.get(_e['to_id'], _e['to_id'])}"
            #         )
            # else:
            #     logger.info("  [DEBUG] env_at_failure has NO INSIDE edges")

            exec_steps      = [parse_eai_action(a, i + 1)
                               for i, a in enumerate(history_actions)]
            failed_step     = parse_eai_action(failed_action, len(exec_steps) + 1)
            full_plan_steps = [parse_eai_action(a, i + 1)
                               for i, a in enumerate(current_plan_eai)]

            try:
                diagnosis, orig_subseq, error_objects = diagnose_error_tree(
                    action_history = exec_steps,
                    failed_step    = failed_step,
                    error_type     = err_type,
                    full_plan      = full_plan_steps,
                    char_sitting   = char_sitting,
                    char_lying     = char_lying,
                    env_dict       = env_at_failure,
                )
                logger.info(
                    f"  🔍 Strategy: {diagnosis.replan_strategy} | "
                    f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
                    f"Unsat: {diagnosis.unsatisfied_needs}"
                )
            except Exception as e:
                logger.warning(f"  Diagnosis failed: {e}", exc_info=True)
                break

            # ── Compute splice window ─────────────────────────────────────────
            t_start = diagnosis.t_start if diagnosis.t_start is not None else failed_step.index
            t_end   = diagnosis.t_end   if diagnosis.t_end   is not None else failed_step.index

            before = history_actions[:max(0, t_start - 1)]
            after  = current_plan_eai[t_end:]

            # ── Already satisfied ─────────────────────────────────────────────
            if diagnosis.replan_strategy == "already_satisfied":
                logger.info(f"  ✅ Goal already met — removing redundant: {failed_action}")
                boundary = len(history_actions)
                removed  = False
                new_plan = []
                for i, a in enumerate(current_plan_eai):
                    if not removed and i >= boundary and a == failed_action:
                        removed = True
                    else:
                        new_plan.append(a)
                current_plan_eai = new_plan
                raw_output       = plan_to_json_str(current_plan_eai)
                continue

            # ── Local replan with Unsat=[] → LLM fallback ────────────────────
            if diagnosis.replan_strategy == "local" and not diagnosis.unsatisfied_needs:
                logger.info("  ⏭️  Local replan (Unsat=[]) — LLM fallback")
                fallback_count += 1
                fallback_raw    = self.llm.call(base_prompt)
                new_subseq      = parse_and_validate(fallback_raw, relevant_name_to_id)
                if new_subseq:
                    current_plan_eai = new_subseq
                    raw_output       = plan_to_json_str(current_plan_eai)
                continue

            # ── Wrong action ──────────────────────────────────────────────────
            if diagnosis.replan_strategy == "wrong_action":
                logger.info(f"  🔄 Wrong action: {failed_action} — LLM replacement")
                fallback_count += 1
                wrong_prompt = WRONG_ACTION_PROMPT.format(
                    failed_action = failed_action,
                    reason        = (
                        f"The object '{failed_step.obj}' cannot be held/worn. "
                        f"Check if you meant PUTIN (put inside container) "
                        f"instead of PUTON (wear on body)."
                    ),
                )
                wrong_raw  = self.llm.call(wrong_prompt, system_prompt=SYSTEM_PROMPT)
                new_subseq = parse_and_validate(wrong_raw, relevant_name_to_id)
                if new_subseq:
                    current_plan_eai = history_actions + new_subseq
                    raw_output       = plan_to_json_str(current_plan_eai)
                    logger.info(f"  🔄 Replaced with: {wrong_raw[:80]}...")
                else:
                    fallback_raw = self.llm.call(base_prompt)
                    new_subseq   = parse_and_validate(fallback_raw, relevant_name_to_id)
                    if new_subseq:
                        current_plan_eai = new_subseq
                        raw_output       = plan_to_json_str(current_plan_eai)
                continue

            # ── Step 1: LLM corrective suggestions ───────────────────────────
            suggestion_prompt = SUGGESTION_PROMPT.format(
                failed_action     = failed_action,
                error_type        = err_type,
                unsat_explanation = get_unsatisfied_explanation(
                    diagnosis.unsatisfied_needs
                ),
            )
            suggestion_raw  = self.llm.call(suggestion_prompt,
                                            system_prompt=SYSTEM_PROMPT)
            llm_suggestions = parse_llm_output(suggestion_raw)
            llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
            if isinstance(llm_suggestions, dict):
                llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]

            logger.info(f"  💡 LLM suggestions: {suggestion_raw[:80]}...")

            # ── Step 2: BFS search tree ───────────────────────────────────────
            state_at_tstart = env_at_failure  # always use failure-time snapshot

            orig_subseq_dicts = []
            for s in orig_subseq:
                if hasattr(s, "target") and s.target:
                    orig_subseq_dicts.append({s.action: [s.obj, s.target]})
                elif hasattr(s, "obj"):
                    orig_subseq_dicts.append({s.action: [s.obj]})

            tree_result = generate_replacement_subsequence(
                llm_suggestions      = llm_suggestions,
                original_subsequence = orig_subseq_dicts,
                initial_state_dict   = state_at_tstart,
                unsatisfied_needs    = diagnosis.unsatisfied_needs,
                error_objects        = error_objects,
                char_sitting         = char_sitting,
                char_lying           = char_lying,
                max_depth            = TREE_MAX_DEPTH,
                max_nodes            = TREE_MAX_NODES,
            )

            if tree_result:
                logger.info(f"  🌳 Tree found: {tree_result}")
                tree_success += 1
                new_subseq    = subtree_results_to_eai(tree_result, relevant_name_to_id)
            else:
                logger.info("  🌳 Tree failed — no fallback (tree-only mode)")
                new_subseq = None

            # ── NO LLM fallback when tree fails ───────────────────────────────
            if not new_subseq:
                continue

            # ── Splice replacement into plan ──────────────────────────────────
            current_plan_eai = before + new_subseq + after
            raw_output       = plan_to_json_str(current_plan_eai)
            logger.info(
                f"  Spliced: {len(before)} + {len(new_subseq)} + "
                f"{len(after)} = {len(current_plan_eai)}"
            )

        return raw_output, replan_count, tree_success, fallback_count

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
                        help="Max number of tasks to run")
    parser.add_argument("--task_ids", type=str, default=None,
                        help="Comma-separated task IDs e.g. 650_2,190_1,487_1")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: API key not set!")
        print("Run: export OPENAI_API_KEY='your_key'")
        sys.exit(1)

    task_ids_set = None
    if args.task_ids:
        task_ids_set = set(args.task_ids.split(","))
        logger.info(f"Running only task IDs: {task_ids_set}")

    EAISDATreeRunner().run_all(
        max_tasks = args.max_tasks,
        task_ids  = task_ids_set,
    )
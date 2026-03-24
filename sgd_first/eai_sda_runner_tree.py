"""
eai_sda_runner_tree.py
======================
Full SDA-Planner pipeline with search tree (Section 4.4 of paper).

Key difference from eai_sda_runner.py:
- Uses search tree (BFS + SDG constraints) for subsequence generation
- Falls back to direct LLM generation only if search tree fails
- Implements the FULL SDA-Planner, not the soft constraint variant

Usage:
    python3 sda_eai/eai_sda_runner_tree.py
    python3 sda_eai/eai_sda_runner_tree.py --max_tasks 50
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
from error_diagnosis_tree import diagnose_error_tree, get_unsatisfied_explanation
from action_subtree import generate_replacement_subsequence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

API_PROVIDER = "openai"
API_KEY      = os.environ.get("OPENAI_API_KEY", os.environ.get("GROQ_API_KEY", ""))
MODEL        = "gpt-4o"
MODEL_NAME   = "gpt-4o-sda-tree-final"

MAX_REPLAN    = 3
SCENEGRAPH_ID = 1

RESOURCE_DIR   = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
DATASET_DIR    = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
OUTPUT_DIR     = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
TASK_DICT_PATH = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
ID2TASK_PATH   = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
DATA_DIR       = osp.join(DATASET_DIR,  "programs_processed_precond_nograb_morepreconds")

TREE_MAX_DEPTH = 6
TREE_MAX_NODES = 500

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
        full = system_prompt + "\n\n" + user_prompt
        payload = json.dumps({
            "contents": [{"parts": [{"text": full}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 512}
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

def parse_llm_output(raw: str):
    """Parse LLM JSON output, handling markdown fences and 0-arg actions."""
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
    # Fix 0-argument actions: {"STANDUP": []} is dropped by load_json_preserving_order
    # Replace with ["character"] so parser keeps it, then strip back later
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
    """Remove actions not in EAI's valid set (e.g. PLUGIN)."""
    if isinstance(parsed, dict):
        return {k: v for k, v in parsed.items() if k.upper() in EAI_VALID_ACTIONS}
    elif isinstance(parsed, list):
        return [a for a in parsed if list(a.keys())[0].upper() in EAI_VALID_ACTIONS]
    return parsed


def parse_eai_action(action, index: int):
    """Convert EAI action (string or dict) to ActionStep."""
    from error_diagnosis import ActionStep
    if isinstance(action, dict):
        return ActionStep(index,
                          action.get("action", "UNKNOWN").upper(),
                          action.get("o1", "unknown"),
                          action.get("o2"))
    s  = str(action)
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


def parse_and_validate(raw: str, relevant_name_to_id: dict):
    """Parse, filter, grammar-check, and convert LLM output to EAI actions."""
    parsed = parse_llm_output(raw)
    if not parsed:
        return None
    parsed = filter_valid_actions(parsed)
    if not parsed:
        return None

    # Strip "character" placeholder back to [] for grammar checker
    # parse_llm_output adds ["character"] to prevent JSON parser dropping STANDUP
    # but EAI grammar checker expects exactly 0 args for STANDUP/SLEEP/WAKEUP
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
        logger.warning(f"Unknown action {e}")
        return None
    try:
        return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"json_to_action failed: {e}")
        return None


def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict):
    """Convert search tree output to EAI action format."""
    if not subtree_result:
        return None

    filtered = filter_valid_actions(subtree_result)
    if not filtered:
        return None

    # STANDUP/SLEEP/WAKEUP need 0 args for EAI grammar checker
    # (unlike parse_llm_output which needs ["character"] for the JSON parser)
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
    """
    Convert EAI action list back to JSON string preserving duplicate keys.
    Uses manual string building to avoid dict overwriting duplicate keys.
    EAI output format: {"WALK": ["obj"], "GRAB": ["obj"], "WALK": ["obj2"]}
    """
    parts = []
    for action in eai_actions:
        s  = str(action)
        am = re.search(r'\[(\w+)\]', s)
        om = re.findall(r'<([^>]+)>', s)
        if am:
            action_name = am.group(1)
            args        = json.dumps(om) if om else "[]"
            parts.append(f'"{action_name}": {args}')
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

    def run_all(self, max_tasks=None):
        logger.info("=== EAI + SDA-Planner (Full Search Tree) ===")
        logger.info(f"Model     : {MODEL_NAME}")
        logger.info(f"Provider  : {API_PROVIDER}")
        logger.info(f"Max replan: {MAX_REPLAN}")
        logger.info(f"Tree depth: {TREE_MAX_DEPTH} | Tree nodes: {TREE_MAX_NODES}")
        logger.info(f"Max tasks : {max_tasks or 'ALL'}")

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
                    logger.info(f"Tree successes: {tree_success} | Fallbacks: {fallback_count}")

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
                scenegraph_id=SCENEGRAPH_ID, script_id=file_id, dataset_root=DATA_DIR,
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

        # ── Generate initial plan ──────────────────────────────────────────────
        raw_output = self.llm.call(base_prompt)
        logger.info(f"  Initial plan: {raw_output[:100]}...")

        actions = parse_and_validate(raw_output, relevant_name_to_id)
        if not actions:
            return raw_output, 0, 0, 0

        current_plan_eai  = actions
        initial_env_state = None

        for attempt in range(MAX_REPLAN + 1):

            motion_planner.reset()
            history_actions    = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable    = True
            failed_action = None
            err_type      = None

            # ── Execute current plan ───────────────────────────────────────────
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

                    # Match EAI behavior: ADDITIONAL_STEP is not a stop error
                    # EAI's evaluate_results skips it and continues execution
                    if err_type == "ADDITIONAL_STEP":
                        logger.info(f"  ⏭️ Skipping: {action}")
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
                # FIX: Save the final executed plan as output
                raw_output = plan_to_json_str(current_plan_eai)
                logger.info(f"  ✅ SUCCESS on attempt {attempt+1}")
                break

            if attempt == MAX_REPLAN:
                logger.info(f"  ⚠️ Max replanning reached for {file_id}")
                break

            # ── SDA Error Backtrack and Diagnosis (Section 4.3) ───────────────
            replan_count += 1

            char_sitting, char_lying = get_char_state(initial_env_state)
            exec_steps      = [parse_eai_action(a, i+1) for i, a in enumerate(history_actions)]
            failed_step     = parse_eai_action(failed_action, len(exec_steps) + 1)
            full_plan_steps = [parse_eai_action(a, i+1) for i, a in enumerate(current_plan_eai)]

            try:
                diagnosis, orig_subseq, error_objects = diagnose_error_tree(
                    action_history = exec_steps,
                    failed_step    = failed_step,
                    error_type     = err_type,
                    full_plan      = full_plan_steps,
                    char_sitting   = char_sitting,
                    char_lying     = char_lying,
                    env_dict       = history_env_states[0],
                )
                logger.info(f"  🔍 Strategy: {diagnosis.replan_strategy} | "
                            f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
                            f"Unsat: {diagnosis.unsatisfied_needs}")
            except Exception as e:
                logger.warning(f"  Diagnosis failed: {e}")
                break

            # ── Compute splice window ──────────────────────────────────────────
            # t_start and t_end are 1-indexed step numbers
            # history_actions and current_plan_eai are 0-indexed lists
            t_start = diagnosis.t_start or failed_step.index
            t_end   = diagnosis.t_end   or failed_step.index

            # before: all successfully executed actions before t_start
            before = history_actions[:max(0, t_start - 1)]
            # after: all remaining original plan actions after t_end
            after  = current_plan_eai[t_end:]

            # ── Step 1: Get LLM corrective suggestions ─────────────────────────
            suggestion_prompt = SUGGESTION_PROMPT.format(
                failed_action     = failed_action,
                error_type        = err_type,
                unsat_explanation = get_unsatisfied_explanation(diagnosis.unsatisfied_needs),
            )
            suggestion_raw  = self.llm.call(suggestion_prompt, system_prompt=SYSTEM_PROMPT)
            llm_suggestions = parse_llm_output(suggestion_raw)
            llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
            if isinstance(llm_suggestions, dict):
                llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]

            logger.info(f"  💡 LLM suggestions: {suggestion_raw[:80]}...")

            # ── Step 2: Build search tree (Section 4.4) ────────────────────────
            # Use env state at t_start - 1 as initial state for the tree
            state_idx       = max(0, t_start - 1 - 1)
            state_at_tstart = (history_env_states[state_idx]
                               if state_idx < len(history_env_states)
                               else history_env_states[0])

            orig_subseq_dicts = []
            for s in orig_subseq:
                if s.target:
                    orig_subseq_dicts.append({s.action: [s.obj, s.target]})
                else:
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
                logger.info(f"  🌳 Search tree found: {tree_result}")
                tree_success += 1
                new_subseq = subtree_results_to_eai(tree_result, relevant_name_to_id)
            else:
                logger.info(f"  🌳 Search tree failed, falling back to LLM")
                fallback_count += 1
                new_subseq = None

            # ── Step 3: Fallback to full LLM replan if tree failed ─────────────
            if not new_subseq:
                fallback_raw = self.llm.call(base_prompt)
                new_subseq   = parse_and_validate(fallback_raw, relevant_name_to_id)
                if new_subseq:
                    current_plan_eai = before + new_subseq + after
                    raw_output       = plan_to_json_str(current_plan_eai)
                continue

            # ── Step 4: Splice replacement into plan ───────────────────────────
            current_plan_eai = before + new_subseq + after
            raw_output       = plan_to_json_str(current_plan_eai)
            logger.info(f"  Spliced: {len(before)} + {len(new_subseq)} + {len(after)} = {len(current_plan_eai)}")

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
    parser.add_argument("--max_tasks", type=int, default=None)
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: API key not set!")
        print("Run: export OPENAI_API_KEY='your_key'")
        sys.exit(1)

    EAISDATreeRunner().run_all(max_tasks=args.max_tasks)

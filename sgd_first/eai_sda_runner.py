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
MODEL        = "gpt-4o-mini"                     # model name for the provider
MODEL_NAME   = "gpt-4o-mini-sda"                 # used for output filename

MAX_REPLAN    = 3
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

def parse_llm_output(raw: str):
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
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

            for action in current_plan_eai:
                exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

                if not exe_flag:
                    executable    = False
                    failed_action = action
                    history_cp    = copy.deepcopy(history_env_states)
                    try:
                        checker  = TemporalOrderChecker(my_info, history_cp)
                        code     = checker.run_checker().get_error_type()
                        err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN")
                    except Exception:
                        err_type = "UNKNOWN"
                    logger.info(f"  ❌ {action} | {err_type}")
                    break
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

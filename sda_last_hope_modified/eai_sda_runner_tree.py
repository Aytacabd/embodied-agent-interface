"""
eai_sda_runner_tree.py
======================
Full SDA-Planner pipeline with Adaptive Action SubTree Generation.
Implements paper Sections 4.2, 4.3, 4.4.

Cleaned ID-aware version:
  - Uses runner-local goal-string builder instead of MotionPlanner.get_symbolic_goal_nl
  - Keeps object identity as class_name_id inside the runner
  - Strictly rejects ambiguous duplicate-class objects instead of guessing
  - Converts one_shot output [name, id] -> name_id before json_to_action
  - Accepts subtree outputs in name_id format

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
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

API_PROVIDER = "openai"
API_KEY = os.environ.get("OPENAI_API_KEY", os.environ.get("GROQ_API_KEY", ""))
MODEL = "gpt-4o"
MODEL_NAME = f"{MODEL}-sda-tree_m"

MAX_REPLAN = 3
SCENEGRAPH_ID = 1
TREE_MAX_DEPTH = 6
TREE_MAX_NODES = 500

RESOURCE_DIR = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
DATASET_DIR = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
OUTPUT_DIR = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
TASK_DICT_PATH = osp.join(RESOURCE_DIR, "virtualhome/task_state_LTL_formula_accurate.json")
ID2TASK_PATH = osp.join(RESOURCE_DIR, "virtualhome/id2task.json")
DATA_DIR = osp.join(DATASET_DIR, "programs_processed_precond_nograb_morepreconds")


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

CRITICAL — READ GOALS FIRST:
Before generating your plan, identify ALL objects mentioned in the node goals and edge goals.
Your plan MUST include actions for EVERY goal object.
A plan that ignores any goal object will FAIL even if it executes without errors.

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
            self.client = Groq(api_key=API_KEY)
            self.provider = "openai_style"
        elif API_PROVIDER == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=API_KEY)
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
                model=MODEL,
                temperature=0,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"API error: {e}")
            return ""

    def _call_gemini(self, user_prompt: str, system_prompt: str) -> str:
        import urllib.request

        full = system_prompt + "\n\n" + user_prompt
        payload = json.dumps({
            "contents": [{"parts": [{"text": full}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
        }).encode()

        try:
            req = urllib.request.Request(
                self.gemini_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
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
        raw,
        flags=re.IGNORECASE,
    )
    match = re.search(r"\{.*\}", raw, re.DOTALL)
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
        return [
            a for a in parsed
            if isinstance(a, dict) and list(a.keys())[0].upper() in EAI_VALID_ACTIONS
        ]
    return parsed


def parse_eai_action(action, index: int):
    from error_diagnosis import ActionStep
    s = str(action)
    am = re.search(r"\[(\w+)\]", s)
    om = re.findall(r"<([^>]+)>", s)
    return ActionStep(
        index=index,
        action=am.group(1).upper() if am else "UNKNOWN",
        obj=om[0].strip() if om else "unknown",
        target=om[1].strip() if len(om) > 1 else None,
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


# def _normalize_name_id_token(token: str) -> str:
#     """
#     Convert:
#       - light_245 -> light_245
#       - light.245 -> light_245
#       - light     -> light
#     """
#     s = str(token).strip()
#     m = re.match(r"^(.+)\.(\d+)$", s)
#     if m:
#         return f"{m.group(1)}_{m.group(2)}"
#     return s
def _normalize_name_id_token(token: str) -> str:
    s = str(token).strip()
    # Deduplicate repeated ID suffix: washing_machine_1001_1001 -> washing_machine_1001
    m = re.match(r"^(.+?)_(\d+)_\2$", s)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    # Normalize dot notation: light.245 -> light_245
    m = re.match(r"^(.+)\.(\d+)$", s)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return s


def build_id_aware_goal_strings(motion_planner, node_goals, edge_goals, action_goals=None):
    """
    Runner-local replacement for MotionPlanner.get_symbolic_goal_nl(...)

    Keeps planner untouched, but writes instance-specific goal strings:
      - node goals: light_245 is ON
      - edge goals: mug_12 is ON table_7
    """
    relevant_name_to_id = {}
    object_in_scene = ""
    change_in_init = ""

    diff_a, diff_b = motion_planner.filter_unique_subdicts(
        motion_planner.init_state.to_dict(),
        motion_planner.final_state_dict,
    )

    existing_nodes = set()
    add_nodes = set()

    for dic in [diff_a, diff_b]:
        for d in dic["nodes"]:
            existing_nodes.add(d["id"])

    for dic in [diff_a, diff_b]:
        for d in dic["edges"]:
            add_nodes.add(d["from_id"])
            add_nodes.add(d["to_id"])

    add_nodes = add_nodes - existing_nodes

    for node_id in add_nodes:
        diff_a["nodes"].append(motion_planner.env_graph.get_node(node_id).to_dict())
        diff_b["nodes"].append(motion_planner.final_graph.get_node(node_id).to_dict())

    object_in_scene += "Objects in the scene:\n"
    all_nodes = existing_nodes.union(add_nodes)
    for node_id in all_nodes:
        node_dict = motion_planner.env_graph.get_node(node_id).to_dict()
        object_in_scene += (
            f"{node_dict['class_name']}_{node_dict['id']}, "
            f"properties: {node_dict['properties']}\n"
        )
        relevant_name_to_id[f"{node_dict['class_name']}_{node_dict['id']}"] = node_dict["id"]
    object_in_scene += "-----------------\n"
#     NON_INTERACTABLE = {
#     "bathroom", "bedroom", "dining_room", "home_office", "kitchen",
#     "living_room", "lobby", "entrance_hall", "floor", "ceiling", "wall",
#     "doorjamb"
# }
#     object_in_scene += "Objects in the scene:\n"
#     all_nodes = existing_nodes.union(add_nodes)
#     for node_id in all_nodes:
#         node_dict = motion_planner.env_graph.get_node(node_id).to_dict()
#         if node_dict["class_name"].lower() in NON_INTERACTABLE:
#             continue
#         object_in_scene += (
#             f"{node_dict['class_name']}_{node_dict['id']}, "
#             f"properties: {node_dict['properties']}\n"
#         )
#         relevant_name_to_id[f"{node_dict['class_name']}_{node_dict['id']}"] = node_dict["id"]
#     object_in_scene += "-----------------\n"

    change_in_init += "Nodes:\n"
    for node_id in existing_nodes:
        node_dict = motion_planner.env_graph.get_node(node_id).to_dict()
        change_in_init += (
            f"{node_dict['class_name']}, states: {node_dict['states']}, "
            f"properties:{node_dict['properties']}\n"
        )
    change_in_init += "\n"
    change_in_init += "Edges:\n"
    for d in diff_a["edges"]:
        fn_name = motion_planner.id_to_name[int(d["from_id"])]
        tn_name = motion_planner.id_to_name[int(d["to_id"])]
        rel = d["relation_type"]
        if rel == "CLOSE":
            rel = "NEAR"
        change_in_init += (
            f"{fn_name}_{d['from_id']} is {rel} to {tn_name}_{d['to_id']}\n"
        )
    change_in_init += "-----------------\n"

    node_goal_str = ""
    for node_goal in node_goals:
        node_goal_str += (
            f"{node_goal['class_name']}_{node_goal['id']} is {node_goal['state']}\n"
        )
    node_goal_str += "-----------------\n"

    edge_goal_str = ""
    for edge_goal in edge_goals:
        from_name = motion_planner.id_to_name[edge_goal["from_id"]]
        to_name = motion_planner.id_to_name[edge_goal["to_id"]]
        rel = edge_goal["relation_type"]
        if rel == "CLOSE":
            rel = "NEAR"
        edge_goal_str += (
            f"{from_name}_{edge_goal['from_id']} is "
            f"{rel} to "
            f"{to_name}_{edge_goal['to_id']}\n"
        )
    edge_goal_str += "-----------------\n"

    if action_goals is not None and len(action_goals) > 0:
        action_goal_str = "The following action(s) should be included:\n"
        for action_goal in action_goals:
            if "|" in action_goal:
                action_candidates = [a.strip() for a in action_goal.split("|")]
                action_goal_str += " or ".join(action_candidates) + "\n"
            else:
                action_goal_str += action_goal + "\n"
        action_goal_str += "-----------------\n"
    else:
        action_goal_str = "There is no action requirement.\n"

    return (
        object_in_scene,
        change_in_init,
        node_goal_str,
        edge_goal_str,
        action_goal_str,
        relevant_name_to_id,
    )


def parse_and_validate(raw: str, relevant_name_to_id: dict):
    parsed = parse_llm_output(raw)
    if not parsed:
        return None

    parsed = filter_valid_actions(parsed)

    # Convert one_shot output:
    #   ["light", "245"] -> ["light_245"]
    #   ["apple", "7", "fridge", "2"] -> ["apple_7", "fridge_2"]
    normalized = []
    for item in (parsed if isinstance(parsed, list) else [{k: v} for k, v in parsed.items()]):
        for action, args in item.items():
            if isinstance(args, list):
                combined = []
                i = 0
                while i < len(args):
                    cur = str(args[i]).strip()
                    nxt = str(args[i + 1]).strip() if i + 1 < len(args) else None
                    if nxt is not None and nxt.isdigit():
                        combined.append(f"{cur}_{nxt}")
                        i += 2
                    else:
                        combined.append(_normalize_name_id_token(cur))
                        i += 1
                normalized.append({action: combined})
            else:
                normalized.append({action: args})
    parsed = normalized

    CONTAINER_OBJECTS = {
        "washing_machine", "fridge", "freezer", "dishwasher",
        "microwave", "stove", "cabinet", "kitchencabinets",
        "bathroomcabinet", "garbagecan", "box", "bag", "trashcan",
    }

    corrected = []
    for item in (parsed if isinstance(parsed, list) else [{k: v} for k, v in parsed.items()]):
        for action, args in item.items():
            if (
                action.upper() == "PUTBACK"
                and isinstance(args, list)
                and len(args) == 2
            ):
                target_name = str(args[1]).rsplit("_", 1)[0]
                if target_name.lower() in CONTAINER_OBJECTS:
                    corrected.append({"PUTIN": args})
                    logger.info(f"  🔄 Auto-corrected PUTBACK→PUTIN for container: {args[1]}")
                else:
                    corrected.append({action: args})
            else:
                corrected.append({action: args})
    parsed = corrected

    if not parsed:
        return None

    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    cleaned = []
    for item in (parsed if isinstance(parsed, list) else [{k: v} for k, v in parsed.items()]):
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


def _resolve_to_name_id(obj_name: str, relevant_name_to_id: dict) -> str:
    """
    Strict resolution:
      - pass through exact class_id keys
      - resolve plain class name only if exactly one match exists
      - reject ambiguity instead of silently picking one
    """
    obj_name = _normalize_name_id_token(obj_name)

    if obj_name in relevant_name_to_id:
        return obj_name

    matches = [
        k for k in relevant_name_to_id
        if k == obj_name or k.startswith(f"{obj_name}_")
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise ValueError(f"Ambiguous object '{obj_name}' with candidates {matches}")

    raise ValueError(f"Unknown object '{obj_name}'")


def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict):
    if not subtree_result:
        return None

    filtered = filter_valid_actions(subtree_result)
    if not filtered:
        return None

    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    processed = []

    try:
        for item in (filtered if isinstance(filtered, list) else [{k: v} for k, v in filtered.items()]):
            for action, args in item.items():
                if action.upper() in ZERO_ARG:
                    processed.append({action: []})
                else:
                    resolved = [
                        _resolve_to_name_id(obj, relevant_name_to_id)
                        for obj in args
                    ]
                    processed.append({action: resolved})
    except ValueError as e:
        logger.warning(f"Subtree object resolution failed: {e}")
        return None

    try:
        ok, err = check_action_grammar(processed)
        if not ok:
            logger.warning(f"Subtree grammar failed: {err}")
            return None
        return json_to_action(processed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"Subtree result conversion failed: {e}")
        return None


# def plan_to_json_str(eai_actions: list) -> str:
#     """
#     Convert EAI action objects back to JSON string.

#     EAI actions look like:
#       [walk] <light> (245)

#     We output:
#       "WALK": ["light_245"]

#     so parse_and_validate → json_to_action can resolve via relevant_name_to_id.
#     """
#     parts = []
#     for action in eai_actions:
#         s = str(action)
#         am = re.search(r"\[(\w+)\]", s)
#         if not am:
#             continue
#         action_name = am.group(1).upper()

#         name_ids = re.findall(r"<([^>]+)>\s*\((\d+)\)", s)

#         if not name_ids:
#             parts.append(f'"{action_name}": []')
#         elif len(name_ids) == 1:
#             name, oid = name_ids[0]
#             parts.append(f'"{action_name}": ["{name.strip()}_{oid}"]')
#         else:
#             tokens = ", ".join(f'"{n.strip()}_{i}"' for n, i in name_ids)
#             parts.append(f'"{action_name}": [{tokens}]')

#     return "{" + ", ".join(parts) + "}"
def plan_to_json_str(eai_actions: list) -> str:
    """
    Convert EAI action objects back to JSON string.

    EAI actions look like:
      [walk] <light> (245)

    We output:
      "WALK": ["light_245"]

    so parse_and_validate → json_to_action can resolve via relevant_name_to_id.
    """
    def _dedup_name(name: str, oid: str) -> str:
        # json_to_action stores the full relevant_name_to_id key as class_name,
        # e.g. "washing_machine_1001". Strip trailing _<id> to avoid double suffix.
        name = name.strip()
        suffix = f"_{oid}"
        if name.endswith(suffix):
            name = name[: -len(suffix)]
        return name

    parts = []
    for action in eai_actions:
        s = str(action)
        am = re.search(r"\[(\w+)\]", s)
        if not am:
            continue
        action_name = am.group(1).upper()

        name_ids = re.findall(r"<([^>]+)>\s*\((\d+)\)", s)

        if not name_ids:
            parts.append(f'"{action_name}": []')
        elif len(name_ids) == 1:
            name, oid = name_ids[0]
            parts.append(f'"{action_name}": ["{_dedup_name(name, oid)}_{oid}"]')
        else:
            tokens = ", ".join(f'"{_dedup_name(n, i)}_{i}"' for n, i in name_ids)
            parts.append(f'"{action_name}": [{tokens}]')

    return "{" + ", ".join(parts) + "}"


# =============================================================================
# Main Runner
# =============================================================================

class EAISDATreeRunner:
    def __init__(self):
        self.llm = LLMClient()
        logger.info("Loading EAI resources...")
        self.properties_data = utils.load_properties_data()
        self.object_placing = utils.load_object_placing()
        self.name_equivalence = utils.load_name_equivalence()
        self.task_dicts = json.load(open(TASK_DICT_PATH))[f"scene_{SCENEGRAPH_ID}"]
        self.id2task = json.load(open(ID2TASK_PATH))
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
        logger.info("LLM fallback on tree fail: DISABLED")

        out_path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
        if osp.exists(out_path):
            existing = json.load(open(out_path))
            done_ids = {
                d["identifier"] for d in existing
                if d["llm_output"] not in ("", "...")
            }
            outputs = list(existing)
            logger.info(f"Resuming: {len(done_ids)} tasks already done")
        else:
            outputs, done_ids = [], set()

        total = replan_total = tree_success = fallback_count = 0

        for task_name, task_files in self.task_dicts.items():
            for file_id, task_goal_dict in task_files.items():
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
                replan_total += rc
                tree_success += ts
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
        logger.info("\n=== DONE ===")
        logger.info(f"Total tasks    : {total}")
        logger.info(f"Total replans  : {replan_total}")
        logger.info(f"Tree successes : {tree_success}")
        logger.info(f"LLM fallbacks  : {fallback_count}")
        logger.info(f"Avg replans    : {replan_total / max(total, 1):.2f}")

    def run_single_task(self, file_id, task_name, task_goal_dict):
        """Returns (raw_output, replan_count, tree_success_count, fallback_count)"""
        goals = task_goal_dict["vh_goal"]
        node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
        edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]

        try:
            motion_planner, _, _, _, _ = construct_planner(
                self.name_equivalence,
                self.properties_data,
                self.object_placing,
                scenegraph_id=SCENEGRAPH_ID,
                script_id=file_id,
                dataset_root=DATA_DIR,
            )
        except Exception as e:
            logger.error(f"Planner build failed: {e}")
            return "", 0, 0, 0

        object_in_scene, cur_change, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
            build_id_aware_goal_strings(
                motion_planner,
                node_goals,
                edge_goals,
                action_goals=goals["actions"],
            )
        )

        import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot

        base_prompt = one_shot.prompt
        base_prompt = base_prompt.replace("<object_in_scene>", object_in_scene)
        base_prompt = base_prompt.replace("<cur_change>", cur_change)
        base_prompt = base_prompt.replace("<node_goals>", node_goal_str)
        base_prompt = base_prompt.replace("<edge_goals>", edge_goal_str)
        base_prompt = base_prompt.replace("<action_goals>", action_goal_str)

        replan_count = 0
        tree_success = 0
        fallback_count = 0
        raw_output = ""

        # ── Generate initial plan ─────────────────────────────────────────────
        raw_output = self.llm.call(base_prompt)
        logger.info(f"  Initial plan: {raw_output[:100]}...")

        actions = parse_and_validate(raw_output, relevant_name_to_id)
        if not actions:
            logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        current_plan_eai = actions
        initial_env_state = None

        for attempt in range(MAX_REPLAN + 1):
            motion_planner.reset()
            history_actions = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable = True
            failed_action = None
            err_type = None
            skipped_indices = set()

            # ── Execute current plan ──────────────────────────────────────────
            for action_idx, action in enumerate(current_plan_eai):
                exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)

                if not exe_flag:
                    history_cp = copy.deepcopy(history_env_states)
                    try:
                        checker = TemporalOrderChecker(my_info, history_cp)
                        code = checker.run_checker().get_error_type()
                        err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN_ERROR")
                    except Exception as ex:
                        logger.warning(f"  TemporalOrderChecker failed: {ex}")
                        err_type = "UNKNOWN_ERROR"

                    if err_type == "ADDITIONAL_STEP":
                        logger.info(f"  ⏭️  Skipping: {action}")
                        skipped_indices.add(action_idx)
                        continue

                    executable = False
                    failed_action = action
                    logger.info(f"  ❌ {action} | {err_type}")
                    break
                else:
                    history_actions.append(action)
                    history_env_states.append(
                        copy.deepcopy(motion_planner.env_state.to_dict())
                    )

            if executable:
                clean_plan = [
                    a for i, a in enumerate(current_plan_eai)
                    if i not in skipped_indices
                ]
                raw_output = plan_to_json_str(clean_plan)
                logger.info(
                    f"  ✅ SUCCESS on attempt {attempt + 1}"
                    + (
                        f" (removed {len(skipped_indices)} skipped actions)"
                        if skipped_indices else ""
                    )
                )
                break

            if attempt == MAX_REPLAN:
                logger.info(f"  ⚠️  Max replanning reached for {file_id}")
                break

            # ── SDA Error Backtrack and Diagnosis ─────────────────────────────
            replan_count += 1

            env_at_failure = (
                history_env_states[-1] if history_env_states else initial_env_state
            )
            char_sitting, char_lying = get_char_state(env_at_failure)

            exec_steps = [
                parse_eai_action(a, i + 1)
                for i, a in enumerate(history_actions)
            ]
            failed_step = parse_eai_action(failed_action, len(exec_steps) + 1)
            full_plan_steps = [
                parse_eai_action(a, i + 1)
                for i, a in enumerate(current_plan_eai)
            ]

            try:
                diagnosis, orig_subseq, error_objects = diagnose_error_tree(
                    action_history=exec_steps,
                    failed_step=failed_step,
                    error_type=err_type,
                    full_plan=full_plan_steps,
                    char_sitting=char_sitting,
                    char_lying=char_lying,
                    env_dict=env_at_failure,
                )
                logger.info(
                    f"  🔍 Strategy: {diagnosis.replan_strategy} | "
                    f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
                    f"Unsat: {diagnosis.unsatisfied_needs}"
                )
            except Exception as e:
                logger.warning(f"  Diagnosis failed: {e}", exc_info=True)
                break

            error_objects = set(str(x) for x in error_objects)

            # ── Compute splice window ─────────────────────────────────────────
            t_start = diagnosis.t_start if diagnosis.t_start is not None else failed_step.index
            t_end = diagnosis.t_end if diagnosis.t_end is not None else failed_step.index

            before = history_actions[:max(0, t_start - 1)]
            after = current_plan_eai[t_end:]

            # ── Special handling: semantically wrong action ───────────────────
            if diagnosis.replan_strategy == "replace_wrong_action":
                wrong_prompt = WRONG_ACTION_PROMPT.format(
                    failed_action=failed_action,
                    reason=get_unsatisfied_explanation(diagnosis.unsatisfied_needs),
                )
                wrong_raw = self.llm.call(wrong_prompt, system_prompt=SYSTEM_PROMPT)
                new_subseq = parse_and_validate(wrong_raw, relevant_name_to_id)

                if new_subseq:
                    current_plan_eai = history_actions + new_subseq
                    raw_output = plan_to_json_str(current_plan_eai)
                    logger.info(f"  🔄 Replaced with: {wrong_raw[:80]}...")
                else:
                    fallback_raw = self.llm.call(base_prompt)
                    new_subseq = parse_and_validate(fallback_raw, relevant_name_to_id)
                    if new_subseq:
                        current_plan_eai = new_subseq
                        raw_output = plan_to_json_str(current_plan_eai)
                continue

            # ── Step 1: LLM corrective suggestions ───────────────────────────
            suggestion_prompt = SUGGESTION_PROMPT.format(
                failed_action=failed_action,
                error_type=err_type,
                unsat_explanation=get_unsatisfied_explanation(
                    diagnosis.unsatisfied_needs
                ),
            )
            suggestion_raw = self.llm.call(suggestion_prompt, system_prompt=SYSTEM_PROMPT)
            llm_suggestions = parse_llm_output(suggestion_raw)
            llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
            if isinstance(llm_suggestions, dict):
                llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]

            logger.info(f"  💡 LLM suggestions: {suggestion_raw[:80]}...")

            # ── Step 2: BFS search tree ───────────────────────────────────────
            state_at_tstart = env_at_failure

            orig_subseq_dicts = []
            for s in orig_subseq:
                if hasattr(s, "target") and s.target:
                    orig_subseq_dicts.append({s.action: [s.obj, s.target]})
                elif hasattr(s, "obj"):
                    orig_subseq_dicts.append({s.action: [s.obj]})

            tree_result = generate_replacement_subsequence(
                llm_suggestions=llm_suggestions,
                original_subsequence=orig_subseq_dicts,
                initial_state_dict=state_at_tstart,
                unsatisfied_needs=diagnosis.unsatisfied_needs,
                error_objects=error_objects,
                char_sitting=char_sitting,
                char_lying=char_lying,
                max_depth=TREE_MAX_DEPTH,
                max_nodes=TREE_MAX_NODES,
            )

            if tree_result:
                logger.info(f"  🌳 Tree found: {tree_result}")
                tree_success += 1
                new_subseq = subtree_results_to_eai(tree_result, relevant_name_to_id)
            else:
                logger.info("  🌳 Tree failed — no fallback (tree-only mode)")
                new_subseq = None

            if not new_subseq:
                continue

            # ── Splice replacement into plan ──────────────────────────────────
            current_plan_eai = before + new_subseq + after
            raw_output = plan_to_json_str(current_plan_eai)
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
    parser.add_argument("--max_tasks", type=int, default=None, help="Max number of tasks to run")
    parser.add_argument(
        "--task_ids",
        type=str,
        default=None,
        help="Comma-separated task IDs e.g. 650_2,190_1,487_1",
    )
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
        max_tasks=args.max_tasks,
        task_ids=task_ids_set,
    )
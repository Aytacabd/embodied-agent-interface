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
    valid_actions as _eai_valid_actions,
)
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

from error_diagnosis import DYNAMIC_PRECONDITIONS
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
MODEL = "gpt-4o-mini"
MODEL_NAME = f"{MODEL}-sda-tree_m_modifiedsdg"

MAX_REPLAN = 3
MAX_FREE_REMOVALS = 8   # plan cleanups (already-satisfied / affordance skips) that don't consume the repair budget
MAX_TOKENS = 1024
SCENEGRAPH_ID = 1
TREE_MAX_DEPTH = 6
TREE_MAX_NODES = 500

VERBOSE = True        # show execution trace, responses, diagnosis
SHOW_PROMPTS = False  # set True to also print full prompts sent to LLM

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
    "PLUGIN", "PLUGOUT", "RELEASE",
}

SYSTEM_PROMPT = """You are fixing a failed action in a VirtualHome robot plan. Suggest a short corrective subsequence (2-5 actions) as JSON only.

Action argument counts:
- 0 args: STANDUP, SLEEP, WAKEUP
- 1 arg:  DRINK, EAT, CUT, TOUCH, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, DROP, LIE, SIT, PUTOBJBACK, RUN, TURNTO, LOOKAT, POINTAT, GREET, SQUEEZE, PLUGIN, PLUGOUT, RELEASE
- 2 args: PUTBACK, PUTIN, POUR

Common repair patterns:
- WALK to an object before any interaction with it.
- If an object is inside a closed container, OPEN the container before GRAB.
- DRINK, READ, PUTIN, PUTBACK, WIPE require something held first (WALK + GRAB).
- WIPE also needs a wiping tool (sponge, rag, towel, cloth, paper) — not the surface itself.
- TURNTO before WATCH or LOOKAT.
- CUT requires holding a knife.
- Max 2 held objects — DROP one before grabbing a third.
- PUTBACK is for surfaces (table, counter, shelf); PUTIN is for enclosed containers (fridge, cabinet, microwave).

Output ONLY the JSON object."""

SUGGESTION_PROMPT = """You are fixing a failed action in a VirtualHome robot plan.

The following action failed:
{failed_action}

Error type: {error_type}

Unsatisfied preconditions:
{unsat_explanation}
{repair_history_section}Generate a SHORT list of corrective actions (2-5 actions) that would fix this error.
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

    def call(self, user_prompt: str, system_prompt: str = None, label: str = "LLM") -> str:
        if system_prompt is None:
            system_prompt = SYSTEM_PROMPT
        sep = "─" * 60
        if VERBOSE and SHOW_PROMPTS:
            print(f"\n{sep}")
            print(f"[{label}] PROMPT SENT ▼")
            print(user_prompt)
            print(sep)
        t0 = time.time()
        if self.provider == "openai_style":
            result = self._call_openai(user_prompt, system_prompt)
        else:
            result = self._call_gemini(user_prompt, system_prompt)
        elapsed = time.time() - t0
        if VERBOSE:
            print(f"[{label}] RESPONSE RECEIVED ({elapsed:.2f}s) ▼", flush=True)
            print(result, flush=True)
            print(sep, flush=True)
        else:
            logger.info(f"  [{label}] {elapsed:.2f}s | {result}")
        return result

    def _call_openai(self, user_prompt: str, system_prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                temperature=0,
                max_tokens=MAX_TOKENS,
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
            "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_TOKENS},
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

def _format_repair_history(prev_attempts: list) -> str:
    """
    Format list of previously tried repair sequences for inclusion in
    SUGGESTION_PROMPT so the LLM does not regenerate the same candidates.

    Each entry in prev_attempts is a list of dicts like [{"WALK": ["tv"]}, {"PLUGIN": ["tv"]}].
    Returns an empty string when there is no history.
    """
    if not prev_attempts:
        return ""
    lines = [
        "Previously attempted repairs that did NOT fix this error (do NOT repeat these):"
    ]
    for i, attempt in enumerate(prev_attempts, 1):
        steps = []
        for item in attempt:
            if isinstance(item, dict):
                for action, args in item.items():
                    obj_str = "(" + ", ".join(str(a) for a in args) + ")" if args else ""
                    steps.append(f"{action}{obj_str}")
        lines.append(f"  Attempt {i}: {' → '.join(steps)}")
    lines.append("Suggest a DIFFERENT corrective sequence.\n")
    return "\n".join(lines)


def parse_llm_output(raw: str):
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        json_str = re.sub(r"\s+", " ", match.group(0).strip())
        # [^\]]* (not +) so empty arrays like "STANDUP": [] are captured
       #pattern = r'"(\w+)"\s*:\s*(\[[^\]]*\])'
        pattern = r'"(\w+)"\s*:\s*(\[\s*\]|\[[^\]]+\])'
        matches = re.findall(pattern, json_str)
        if not matches:
            return []
        return [{key: json.loads(value)} for key, value in matches]
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
    # Capture "<name> (id)" pairs so the instance id is preserved for repairs.
    pairs = re.findall(r"<([^>]+)>\s*(?:\((\d+)\))?", s)
    obj       = pairs[0][0].strip() if pairs else "unknown"
    obj_id    = (pairs[0][1].strip() or None) if pairs else None
    target    = pairs[1][0].strip() if len(pairs) > 1 else None
    target_id = (pairs[1][1].strip() or None) if len(pairs) > 1 else None
    return ActionStep(
        index=index,
        action=am.group(1).upper() if am else "UNKNOWN",
        obj=obj,
        target=target,
        obj_id=obj_id,
        target_id=target_id,
    )


def _extract_plan_ids_and_actions(eai_actions):
    """Walk the plan once; return (set of object IDs touched, list of action names in order)."""
    id_pat = re.compile(r"<[^>]+>\s*\((\d+)\)")
    act_pat = re.compile(r"\[(\w+)\]")
    touched_ids = set()
    action_seq = []
    for a in eai_actions:
        s = str(a)
        touched_ids.update(int(m) for m in id_pat.findall(s))
        am = act_pat.search(s)
        if am:
            action_seq.append(am.group(1).upper())
    return touched_ids, action_seq


def check_goal_coverage(eai_actions, node_goals, edge_goals, ignore_ids=None):
    """Return set of goal object IDs that the plan never touches.

    Node goals: {"id": ..., "state": ...}
    Edge goals: {"from_id": ..., "to_id": ..., "relation_type": ...}
    Plan executes successfully and still fails grading if it ignores a goal object.

    ignore_ids: ids never expected as action arguments (the character itself).
    Character-state goals (character ON toilet, INSIDE bathroom, FACING tv …)
    have from_id = character, but rule 1 forbids passing character as an
    argument, so it can NEVER appear in touched_ids — without this exclusion the
    coverage check fires a wasted retry on essentially every such task.
    """
    ignore_ids = ignore_ids or set()
    goal_ids = set()
    for g in node_goals:
        if "id" in g:
            goal_ids.add(g["id"])
    for g in edge_goals:
        if "from_id" in g:
            goal_ids.add(g["from_id"])
        if "to_id" in g:
            goal_ids.add(g["to_id"])
    goal_ids -= ignore_ids
    touched_ids, _ = _extract_plan_ids_and_actions(eai_actions)
    return goal_ids - touched_ids


# Relations between two objects that can only be created by a placement action
# carrying BOTH object ids (PUTBACK/PUTIN). A 1-arg action such as
# PUTON <clothes> touches the object but can never establish these.
_PLACEMENT_RELATIONS = {"ON", "INSIDE", "ONTOP", "ON_TOP", "NEXT_TO"}


def _extract_action_id_groups(eai_actions):
    """Per action, the set of numeric object ids it references."""
    id_pat = re.compile(r"<[^>]+>\s*\((\d+)\)")
    groups = []
    for a in eai_actions:
        ids = {int(m) for m in id_pat.findall(str(a))}
        if ids:
            groups.append(ids)
    return groups


def check_relation_coverage(eai_actions, edge_goals, initial_edges=None, ignore_ids=None):
    """Return placement edge goals whose relation the plan cannot structurally
    establish: no single action carries BOTH from_id and to_id, and the relation
    does not already hold in the initial graph. Catches e.g. using PUTON <clothes>
    (1 arg) to try to satisfy (clothes ON washing_machine) — it executes fine but
    never creates the relation, so grading silently fails.
    """
    ignore_ids = ignore_ids or set()
    initial_edges = initial_edges or set()
    groups = _extract_action_id_groups(eai_actions)
    missing = []
    for g in edge_goals:
        rel = str(g.get("relation_type", "")).upper()
        if rel not in _PLACEMENT_RELATIONS:
            continue
        fi, ti = g.get("from_id"), g.get("to_id")
        if fi is None or ti is None or fi in ignore_ids or ti in ignore_ids:
            continue
        if (fi, rel, ti) in initial_edges:
            continue  # already satisfied in the initial state
        if not any(fi in grp and ti in grp for grp in groups):
            missing.append(g)
    return missing


def format_missed_relations(missing_edges, id_to_name):
    """Human-readable description of edge goals the plan can't structurally satisfy."""
    lines = []
    for g in missing_edges:
        fi, ti = g.get("from_id"), g.get("to_id")
        fn = id_to_name.get(fi, "?")
        tn = id_to_name.get(ti, "?")
        rel = g.get("relation_type", "?")
        lines.append(
            f"- {fn}_{fi} must be {rel} {tn}_{ti} "
            f"(use PUTIN or PUTBACK with BOTH objects, e.g. "
            f'"PUTIN": ["{fn}", "{fi}", "{tn}", "{ti}"] — NOT PUTON)'
        )
    return "\n".join(lines)


def format_missed_goals(missing_ids, node_goals, edge_goals, id_to_name):
    """Build a human-readable description of the goal entries the plan ignored."""
    lines = []
    for g in node_goals:
        if g.get("id") in missing_ids:
            name = g.get("class_name") or id_to_name.get(g["id"], "?")
            lines.append(f"- {name}_{g['id']} must reach state {g['state']}")
    for g in edge_goals:
        from_id, to_id = g.get("from_id"), g.get("to_id")
        if from_id in missing_ids or to_id in missing_ids:
            from_name = id_to_name.get(from_id, "?")
            to_name = id_to_name.get(to_id, "?")
            rel = g.get("relation_type", "?")
            lines.append(f"- {from_name}_{from_id} must be {rel} {to_name}_{to_id}")
    return "\n".join(lines)


def check_action_goal_ordering(eai_actions, action_goals):
    """Return list of (line_idx, required_set) entries that don't appear in the
    plan in the specified relative order.

    action_goals format: ["GRAB", "TYPE|TOUCH", "OPEN"] — each entry is one
    required action, or "|"-separated OR-alternatives (include exactly one).
    """
    if not action_goals:
        return []
    _, plan_actions = _extract_plan_ids_and_actions(eai_actions)
    issues = []
    cursor = 0
    for i, line in enumerate(action_goals):
        required = {a.strip().upper() for a in line.split("|") if a.strip()}
        if not required:
            continue
        # Find the next occurrence at or after cursor.
        found_at = next(
            (j for j in range(cursor, len(plan_actions))
             if plan_actions[j] in required),
            None,
        )
        if found_at is None:
            issues.append((i, required))
        else:
            cursor = found_at + 1
    return issues


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


def _check_grammar_combined(action_list):
    """Grammar check for combined name_id format (1 string per object) used internally."""
    for item in action_list:
        for action, params in item.items():
            if action not in _eai_valid_actions:
                return False, f"Unknown action: {action}"
            clean = [p for p in params if p != ""]
            expected = _eai_valid_actions[action][1]
            if len(clean) != expected:
                return False, f"{action} expects {expected} arg(s), got {len(clean)}"
    return True, None


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
            f"{node_dict['class_name']}_{node_dict['id']}, states: {node_dict['states']}, "
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


def parse_and_validate(raw: str, relevant_name_to_id: dict, err_out: list = None):
    def _fail(reason):
        if err_out is not None:
            err_out.append(reason)
        return None

    parsed = parse_llm_output(raw)
    if not parsed:
        return _fail("Output was not valid JSON / no actions found.")

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
                        # LLM sometimes echoes the name already carrying the id
                        # (e.g. "toilet_1000") AND passes the id again ("1000").
                        # Avoid producing a double suffix "toilet_1000_1000".
                        if cur.endswith(f"_{nxt}"):
                            combined.append(cur)
                        else:
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
        "microwave", "stove", "cabinet",
        "kitchen_cabinet", "kitchencabinets",
        "bathroom_cabinet", "bathroomcabinet",
        "garbage_can", "garbagecan",
        "box", "bag", "trashcan",
        "pantry", "closet", "dresser", "cupboard", "filing_cabinet",
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
        ok, err = _check_grammar_combined(parsed)
        if not ok:
            logger.warning(f"Grammar check failed: {err}")
            return _fail(f"Grammar error: {err}")
    except KeyError as e:
        logger.warning(f"Unknown action in grammar check: {e}")
        return _fail(f"Unknown action: {e}")

    try:
        return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"json_to_action failed: {e}")
        return _fail(f"Could not resolve object: {e}")


def _resolve_to_name_id(obj_name: str, relevant_name_to_id: dict, preferred: dict = None) -> str:
    """
    Strict resolution:
      - pass through exact class_id keys
      - resolve plain class name only if exactly one match exists
      - on ambiguity, prefer the goal-relevant instance if exactly one of the
        candidates appears in `preferred` (the task's relevant_name_to_id);
        otherwise reject instead of silently picking one
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
        if preferred:
            goal_matches = [m for m in matches if m in preferred]
            if len(goal_matches) == 1:
                return goal_matches[0]
        raise ValueError(f"Ambiguous object '{obj_name}' with candidates {matches}")

    raise ValueError(f"Unknown object '{obj_name}'")


def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict, preferred: dict = None):
    if not subtree_result:
        return None

    filtered = filter_valid_actions(subtree_result)
    if not filtered:
        return None

    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    processed = []

    for item in (filtered if isinstance(filtered, list) else [{k: v} for k, v in filtered.items()]):
        for action, args in item.items():
            if action.upper() in ZERO_ARG:
                processed.append({action: []})
            else:
                try:
                    resolved = [
                        _resolve_to_name_id(obj, relevant_name_to_id, preferred)
                        for obj in args
                    ]
                    processed.append({action: resolved})
                except ValueError as e:
                    logger.warning(f"  Skipping {action} — {e}")

    if not processed:
        logger.warning("Subtree resolution produced no usable actions")
        return None

    try:
        ok, err = _check_grammar_combined(processed)
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
        else:
            tokens = ", ".join(
                f'"{_dedup_name(n, i)}", "{i}"' for n, i in name_ids
            )
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

        total = replan_total = tree_success = 0

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

                result, rc, ts = self.run_single_task(
                    file_id, task_name, task_goal_dict
                )
                replan_total += rc
                tree_success += ts
                outputs.append({"identifier": file_id, "llm_output": result})

                time.sleep(1)

                if total % 10 == 0:
                    self._save(outputs)
                    logger.info(
                        f"Progress: {total} | Tree: {tree_success}"
                    )

        self._save(outputs)
        logger.info("\n=== DONE ===")
        logger.info(f"Total tasks    : {total}")
        logger.info(f"Total replans  : {replan_total}")
        logger.info(f"Tree successes : {tree_success}")
        logger.info(f"Avg replans    : {replan_total / max(total, 1):.2f}")

    def run_single_task(self, file_id, task_name, task_goal_dict):
        """Returns (raw_output, replan_count, tree_success_count)"""
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
            return "", 0, 0

        object_in_scene, cur_change, node_goal_str, edge_goal_str, action_goal_str, relevant_name_to_id = (
            build_id_aware_goal_strings(
                motion_planner,
                node_goals,
                edge_goals,
                action_goals=goals["actions"],
            )
        )

        # Full scene map so BFS-generated repairs can reference objects that are
        # not task-goal nodes (e.g. a microwave containing the target grocery).
        # relevant_name_to_id takes precedence for any overlapping keys.
        full_scene_name_to_id = {
            f"{n.class_name}_{n.id}": n.id
            for n in motion_planner.env_graph.get_nodes()
        }
        scene_name_to_id = {**full_scene_name_to_id, **relevant_name_to_id}

        import virtualhome_eval.evaluation.action_sequencing.prompts.one_shot as one_shot

        # SDA_PROMPT_VARIANT=original  -> verbatim EAI upstream prompt
        #                                 (paper-comparable baseline runs)
        # SDA_PROMPT_VARIANT=calibrated (default) -> executor-calibrated prompt
        #                                 (the 92.7% configuration)
        _variant = os.environ.get("SDA_PROMPT_VARIANT", "calibrated").lower()
        if _variant == "original":
            if not hasattr(one_shot, "prompt_original"):
                raise RuntimeError(
                    "SDA_PROMPT_VARIANT=original but the installed "
                    "virtualhome_eval one_shot.py has no prompt_original — "
                    "sync the updated one_shot.py into the environment."
                )
            base_prompt = one_shot.prompt_original
        else:
            base_prompt = one_shot.prompt
        logger.info(f"Prompt variant: {_variant}")
        base_prompt = base_prompt.replace("<object_in_scene>", object_in_scene)
        base_prompt = base_prompt.replace("<cur_change>", cur_change)
        base_prompt = base_prompt.replace("<node_goals>", node_goal_str)
        base_prompt = base_prompt.replace("<edge_goals>", edge_goal_str)
        base_prompt = base_prompt.replace("<action_goals>", action_goal_str)

        replan_count = 0
        tree_success = 0
        raw_output = ""

        if VERBOSE:
            print(f"\n{'='*60}", flush=True)
            print(f"TASK: {file_id}  |  {task_name}", flush=True)
            print(f"{'='*60}", flush=True)

        # ── Generate initial plan ─────────────────────────────────────────────
        raw_output = self.llm.call(base_prompt, system_prompt="", label="INITIAL PLAN")
        logger.info(f"  Initial plan: {raw_output}")

        _perr = []
        actions = parse_and_validate(raw_output, relevant_name_to_id, err_out=_perr)
        if not actions:
            # One targeted format-reminder retry before giving up. Echo the exact
            # parse error (bad name/id format, wrong argument count, unknown
            # action, unresolvable object) so the LLM fixes the real problem
            # rather than guessing. Far cheaper than losing the task outright.
            reason = _perr[-1] if _perr else "The output could not be parsed."
            logger.warning(
                f"  Initial plan parse failed for {file_id} ({reason}) — one format-reminder retry"
            )
            fmt_retry_prompt = (
                base_prompt
                + "\n\nNOTE: Your previous output was rejected. Reason: "
                + reason
                + "\n\nYour previous output was:\n"
                + raw_output.strip()
                + "\n\nFix the problem. Every argument must be exactly "
                + '[bare_class_name, numeric_id] — e.g. ["light", "245"], NOT '
                + '["light_245", "2"]. Put the bare class name (no id suffix) in '
                + "the name field and the object's real numeric id (from the scene "
                + "and goals) in the id field. Include the correct number of "
                + "arguments for each action (0-arg actions like STANDUP use []). "
                + "Output ONLY the JSON.\n"
            )
            raw_retry = self.llm.call(
                fmt_retry_prompt, system_prompt="", label="INITIAL PLAN FORMAT RETRY"
            )
            logger.info(f"  Format-retry plan: {raw_retry}")
            actions = parse_and_validate(raw_retry, relevant_name_to_id)
            if actions:
                raw_output = raw_retry
            else:
                logger.warning(f"  Could not parse initial plan for {file_id} (after retry)")
                return raw_output, 0, 0

        # ── Static plan alignment checks ───────────────────────────────────────
        # One targeted retry for goal coverage — if the plan ignores any goal
        # object, the executor will pass but the EAI grader will fail. A single
        # focused re-prompt is much cheaper than discovering this at scoring.
        # Exclude the character's own id: character-state goals can never be
        # "touched" by an argument (rule 1 forbids character as an arg).
        char_ids = {
            n.id for n in motion_planner.env_graph.get_nodes()
            if n.class_name == "character"
        }
        # Relations already true in the initial scene need no placement action.
        initial_edges = {
            (e.get("from_id"), str(e.get("relation_type", "")).upper(), e.get("to_id"))
            for e in motion_planner.init_state.to_dict().get("edges", [])
        }
        missing_goal_ids = check_goal_coverage(
            actions, node_goals, edge_goals, ignore_ids=char_ids
        )
        missing_relations = check_relation_coverage(
            actions, edge_goals, initial_edges=initial_edges, ignore_ids=char_ids
        )
        if missing_goal_ids or missing_relations:
            id_to_name = {
                n.id: n.class_name
                for n in motion_planner.env_graph.get_nodes()
            }
            parts = []
            if missing_goal_ids:
                parts.append(
                    "These required goal objects are never touched by any action:\n"
                    + format_missed_goals(missing_goal_ids, node_goals, edge_goals, id_to_name)
                )
            if missing_relations:
                parts.append(
                    "These required relations cannot be established by the plan "
                    "(no action places both objects together):\n"
                    + format_missed_relations(missing_relations, id_to_name)
                )
            missed_desc = "\n".join(parts)
            logger.warning(
                f"  ⚠️  Plan alignment gap (ids={sorted(missing_goal_ids)}, "
                f"relations={len(missing_relations)}) — one targeted retry:\n{missed_desc}"
            )
            if VERBOSE:
                print(f"\n  GOAL COVERAGE RETRY — missing:\n{missed_desc}")

            retry_prompt = (
                base_prompt
                + "\n\nNOTE: Your previous attempt produced this plan:\n"
                + raw_output.strip()
                + "\n\nThat plan does not satisfy all goals:\n"
                + missed_desc
                + "\n\nGenerate a NEW complete plan that achieves ALL goals "
                + "(including the items above). Output ONLY the JSON.\n"
            )
            retry_raw = self.llm.call(
                retry_prompt, system_prompt="", label="GOAL COVERAGE RETRY"
            )
            retry_actions = parse_and_validate(retry_raw, relevant_name_to_id)
            if retry_actions:
                still_ids = check_goal_coverage(
                    retry_actions, node_goals, edge_goals, ignore_ids=char_ids
                )
                still_rel = check_relation_coverage(
                    retry_actions, edge_goals, initial_edges=initial_edges, ignore_ids=char_ids
                )
                before = len(missing_goal_ids) + len(missing_relations)
                after = len(still_ids) + len(still_rel)
                if after < before:
                    logger.info(
                        f"  🔁 Retry improved alignment: {before} → {after} gaps "
                        f"(ids {len(missing_goal_ids)}→{len(still_ids)}, "
                        f"relations {len(missing_relations)}→{len(still_rel)})"
                    )
                    actions = retry_actions
                    raw_output = retry_raw
                else:
                    logger.warning(
                        f"  Retry did not improve alignment — keeping original plan"
                    )
            else:
                logger.warning("  Retry produced unparseable output — keeping original")

        ordering_issues = check_action_goal_ordering(actions, goals.get("actions") or [])
        if ordering_issues:
            for line_idx, required in ordering_issues:
                logger.warning(
                    f"  ⚠️  Action goal #{line_idx + 1} not satisfied in order: "
                    f"expected one of {sorted(required)}"
                )
                if VERBOSE:
                    print(
                        f"\n  ACTION ORDER WARNING: required action "
                        f"{sorted(required)} (#{line_idx + 1}) missing or out of order"
                    )

        current_plan_eai = actions
        initial_env_state = None

        # Tracks repair sequences already tried for each failing action so
        # the LLM can see what has been attempted and suggest something different.
        repair_attempts: dict = {}   # action_key -> list of tried repair sequences

        # attempt counts LLM-repair iterations (capped by MAX_REPLAN);
        # free_removals counts plan cleanups (already-satisfied / affordance
        # skips) that re-execute the plan without consuming the repair budget.
        attempt = 0
        free_removals = 0

        while True:
            motion_planner.reset()
            history_actions = []
            executed_plan_indices = []   # plan-space index of each executed action
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable = True
            failed_action = None
            failed_plan_idx = None       # plan-space index of the failed action
            err_type = None
            skipped_indices = set()

            # ── Execute current plan ──────────────────────────────────────────
            if VERBOSE:
                print(f"\n  {'─'*50}")
                print(f"  EXECUTING PLAN (attempt {attempt+1}) — {len(current_plan_eai)} actions")
                print(f"  {'─'*50}")
            for action_idx, action in enumerate(current_plan_eai):
                if VERBOSE:
                    print(f"  [{action_idx+1:02d}] {action}", end="  →  ", flush=True)
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
                        if VERBOSE:
                            print(f"SKIPPED (ADDITIONAL_STEP)")
                        else:
                            logger.info(f"  ⏭️  Skipping: {action}")
                        skipped_indices.add(action_idx)
                        continue

                    if err_type == "UNSEEN_OBJECT":
                        if VERBOSE:
                            print("SKIPPED (UNSEEN_OBJECT — object not in scene)")
                        else:
                            logger.info(f"  ⏭️  Skipping unseen object: {action}")
                        skipped_indices.add(action_idx)
                        continue

                    if VERBOSE:
                        print(f"FAILED [{err_type}]")
                    executable = False
                    failed_action = action
                    failed_plan_idx = action_idx
                    logger.info(f"  ❌ {action} | {err_type}")
                    break
                else:
                    if VERBOSE:
                        print("OK")
                    history_actions.append(action)
                    executed_plan_indices.append(action_idx)
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
                if VERBOSE:
                    print(f"\n  FINAL OUTPUT SAVED:", flush=True)
                    print(f"  {raw_output}", flush=True)
                break

            if attempt >= MAX_REPLAN:
                logger.info(f"  ⚠️  Max replanning reached for {file_id}")
                if VERBOSE:
                    print(f"\n  FINAL OUTPUT SAVED (max replans reached):", flush=True)
                    print(f"  {raw_output}", flush=True)
                break

            # ── SDA Error Backtrack and Diagnosis ─────────────────────────────
            replan_count += 1

            env_at_failure = (
                history_env_states[-1] if history_env_states else initial_env_state
            )
            char_sitting, char_lying = get_char_state(env_at_failure)

            # All step indices are plan-space (1-indexed over current_plan_eai).
            # Using history positions here desynchronised slicing whenever
            # actions were skipped (ADDITIONAL_STEP / UNSEEN_OBJECT) earlier
            # in the plan, removing/splicing at the wrong position.
            exec_steps = [
                parse_eai_action(a, pidx + 1)
                for a, pidx in zip(history_actions, executed_plan_indices)
            ]
            failed_step = parse_eai_action(failed_action, failed_plan_idx + 1)
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
                    initial_env_dict=initial_env_state,
                )
                logger.info(
                    f"  🔍 Strategy: {diagnosis.replan_strategy} | "
                    f"Window: [{diagnosis.t_start},{diagnosis.t_end}] | "
                    f"Unsat: {diagnosis.unsatisfied_needs}"
                )
                if VERBOSE:
                    print(f"\n  ERROR DIAGNOSIS:")
                    print(f"    Failed action    : {failed_action}")
                    print(f"    Error type       : {err_type}")
                    print(f"    Replan strategy  : {diagnosis.replan_strategy}")
                    print(f"    Repair window    : [{diagnosis.t_start}, {diagnosis.t_end}]")
                    print(f"    Unsatisfied needs: {diagnosis.unsatisfied_needs}")
                    print(f"    Error objects    : {error_objects}")
            except Exception as e:
                logger.warning(f"  Diagnosis failed: {e}", exc_info=True)
                break

            error_objects = set(str(x) for x in error_objects)

            # ── Compute splice window (plan-space, 1-indexed) ─────────────────
            t_start = diagnosis.t_start if diagnosis.t_start is not None else failed_step.index
            t_end = diagnosis.t_end if diagnosis.t_end is not None else failed_step.index

            before = [
                a for a, pidx in zip(history_actions, executed_plan_indices)
                if pidx + 1 < t_start
            ]
            after = current_plan_eai[t_end:]

            # ── Action already satisfied: goal is already true, just remove it ─
            if diagnosis.replan_strategy == "already_satisfied":
                replan_count -= 1
                idx = failed_step.index - 1
                current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                raw_output = plan_to_json_str(current_plan_eai)
                if VERBOSE:
                    print(f"\n  ACTION ALREADY SATISFIED — removed: {failed_action}")
                if free_removals < MAX_FREE_REMOVALS:
                    free_removals += 1
                else:
                    attempt += 1
                continue

            # ── Special handling: semantically wrong action ───────────────────
            if diagnosis.replan_strategy == "wrong_action":
                action_key = str(failed_action)
                wrong_prompt = WRONG_ACTION_PROMPT.format(
                    failed_action=failed_action,
                    reason=get_unsatisfied_explanation(diagnosis.unsatisfied_needs),
                )
                wrong_raw = self.llm.call(wrong_prompt, system_prompt=SYSTEM_PROMPT, label="WRONG ACTION FIX")
                new_subseq = parse_and_validate(wrong_raw, relevant_name_to_id)

                if new_subseq:
                    repair_attempts.setdefault(action_key, []).append(new_subseq)
                    current_plan_eai = history_actions + new_subseq + after
                    raw_output = plan_to_json_str(current_plan_eai)
                    logger.info(f"  🔄 Replaced with: {wrong_raw}")
                    attempt += 1
                else:
                    # Fix unparseable — remove the wrong action so execution
                    # can continue rather than looping until max replans.
                    replan_count -= 1
                    idx = failed_step.index - 1
                    current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                    raw_output = plan_to_json_str(current_plan_eai)
                    logger.info(f"  ⚡ WRONG ACTION SKIP — removed: {failed_action}")
                    if VERBOSE:
                        print(f"\n  WRONG ACTION SKIP — removed: {failed_action}")
                    if free_removals < MAX_FREE_REMOVALS:
                        free_removals += 1
                    else:
                        attempt += 1
                continue

            # ── Step 1: LLM corrective suggestions (or deterministic for insert_prep) ──
            action_key = str(failed_action)

            # insert_prep: prep action is fully determined by the key precondition.
            # Skip the LLM API call and feed BFS the exact candidate directly.
            _insert_prep_suggestions = None
            if diagnosis.replan_strategy == "insert_prep" and diagnosis.unsatisfied_needs:
                # Same key selection as diagnose_error (dynamic-first) — using
                # unsatisfied_needs[0] here silently missed the fast path when a
                # static property (e.g. lookable) preceded the dynamic key.
                _dyn = [p for p in diagnosis.unsatisfied_needs
                        if p in DYNAMIC_PRECONDITIONS]
                _key = _dyn[0] if _dyn else diagnosis.unsatisfied_needs[0]
                # Use the instance-qualified form (e.g. "light_245") so the
                # repair WALK/TURNTO targets the exact failing instance instead
                # of an ambiguous bare class name that resolution then drops.
                if _key in ("not_sitting", "not_lying"):
                    _insert_prep_suggestions = [{"STANDUP": []}]
                elif _key == "next_to_obj":
                    _insert_prep_suggestions = [{"WALK": [str(failed_step.obj_full)]}]
                elif _key == "next_to_target" and failed_step.target:
                    _insert_prep_suggestions = [{"WALK": [str(failed_step.target_full)]}]
                elif _key == "facing_obj":
                    _insert_prep_suggestions = [{"TURNTO": [str(failed_step.obj_full)]}]
                elif _key == "not_both_hands_full":
                    # guaranteed_candidates already include DROP for held objects
                    _insert_prep_suggestions = []

            if _insert_prep_suggestions is not None:
                llm_suggestions = _insert_prep_suggestions
                logger.info(f"  ⚡ INSERT_PREP fast path: {llm_suggestions}")
                if VERBOSE:
                    print(f"\n  INSERT_PREP fast path: {llm_suggestions}")
            else:
                suggestion_prompt = SUGGESTION_PROMPT.format(
                    failed_action=failed_action,
                    error_type=err_type,
                    unsat_explanation=get_unsatisfied_explanation(
                        diagnosis.unsatisfied_needs
                    ),
                    repair_history_section=_format_repair_history(
                        repair_attempts.get(action_key, [])
                    ),
                )
                suggestion_raw = self.llm.call(suggestion_prompt, system_prompt=SYSTEM_PROMPT, label=f"SUGGESTION (replan {replan_count})")
                llm_suggestions = parse_llm_output(suggestion_raw)
                llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
                if isinstance(llm_suggestions, dict):
                    llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]
                logger.info(f"  💡 LLM suggestions: {suggestion_raw}")

            # ── Step 2: BFS search tree ───────────────────────────────────────
            # Env state just before plan position t_start = state after the
            # executed actions whose plan position precedes t_start.
            n_exec_before = sum(
                1 for pidx in executed_plan_indices if pidx + 1 < t_start
            )
            state_at_tstart = (
                history_env_states[n_exec_before]
                if n_exec_before < len(history_env_states)
                else env_at_failure
            )

            orig_subseq_dicts = []
            for s in orig_subseq:
                # Use instance-qualified names so original-subsequence candidates
                # also resolve to the exact instance during tree search.
                s_obj = s.obj_full if hasattr(s, "obj_full") else s.obj
                if hasattr(s, "target") and s.target:
                    s_tgt = s.target_full if hasattr(s, "target_full") else s.target
                    orig_subseq_dicts.append({s.action: [s_obj, s_tgt]})
                elif hasattr(s, "obj"):
                    orig_subseq_dicts.append({s.action: [s_obj]})

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
                failed_obj=str(failed_step.obj_full) if failed_step.obj else None,
                failed_target=str(failed_step.target_full) if failed_step.target else None,
                # Prior repairs for THIS failing action that already failed —
                # BFS will refuse to return any of them again (hard no-repeat).
                excluded_repairs=list(repair_attempts.get(action_key, [])),
            )

            # Always record the LLM suggestion so the LLM sees what it already
            # tried and doesn't repeat the same candidates next replan cycle.
            if llm_suggestions:
                repair_attempts.setdefault(action_key, []).append(llm_suggestions)

            if tree_result:
                logger.info(f"  🌳 Tree found: {tree_result}")
                if VERBOSE:
                    print(f"\n  TREE SEARCH RESULT: {tree_result}")
                tree_success += 1
                # Record the repair that is ACTUALLY applied (not just the raw
                # LLM suggestions) so a failed splice is hard-excluded from the
                # next BFS run for this same action. If it works, the action
                # never fails again and the entry is simply never consulted.
                repair_attempts.setdefault(action_key, []).append(tree_result)
                new_subseq = subtree_results_to_eai(
                    tree_result, scene_name_to_id, preferred=relevant_name_to_id
                )
            else:
                logger.info("  🌳 Tree failed — no fallback (tree-only mode)")
                if VERBOSE:
                    print(f"\n  TREE SEARCH: no solution found")
                new_subseq = None

            if not new_subseq:
                # No usable repair (BFS found nothing, or found something but
                # object resolution failed). For AFFORDANCE_ERROR the missing
                # property is permanent — no action can acquire it — so remove
                # the action rather than looping forever.
                if err_type == "AFFORDANCE_ERROR":
                    replan_count -= 1
                    idx = failed_step.index - 1
                    current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                    raw_output = plan_to_json_str(current_plan_eai)
                    logger.info(f"  ⚡ AFFORDANCE SKIP — removed: {failed_action}")
                    if VERBOSE:
                        print(f"\n  AFFORDANCE SKIP — removed: {failed_action}")
                    if free_removals < MAX_FREE_REMOVALS:
                        free_removals += 1
                    else:
                        attempt += 1
                    continue
                attempt += 1
                continue

            # ── Splice replacement into plan ──────────────────────────────────
            # Retain the failed action(s) after the repair sequence so that
            # prep-style fixes (WALK, OPEN, etc.) are followed by a retry of
            # the original action rather than silently dropping it.
            failed_eai = current_plan_eai[t_start - 1 : t_end]
            current_plan_eai = before + new_subseq + failed_eai + after
            raw_output = plan_to_json_str(current_plan_eai)
            logger.info(
                f"  Spliced: {len(before)} + {len(new_subseq)} + "
                f"{len(failed_eai)} (retry) + {len(after)} = {len(current_plan_eai)}"
            )
            attempt += 1

        return raw_output, replan_count, tree_success

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
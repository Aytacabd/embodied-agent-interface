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
import difflib
import logging
import argparse
import os.path as osp

sys.path.insert(0, "/opt/iGibson/sda_eai")

import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    construct_planner,
    json_to_action,
    valid_actions as _eai_valid_actions,
    scene_evaluate_wID,
)
from virtualhome_eval.simulation.evolving_graph.checker import TemporalOrderChecker

from error_diagnosis_tree import diagnose_error_tree, get_unsatisfied_explanation
from action_subtree import generate_replacement_subsequence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# LLM PROVIDER CONFIGURATION
# =============================================================================
# The ONLY block to touch when switching LLM providers — the rest of the
# pipeline is provider-agnostic and talks to `LLMClient.call()` exclusively.
#
#   API_PROVIDER  "openai" | "openai_compatible" | "groq" | "gemini"
#                 openai_compatible = ANY OpenAI-style /chat/completions
#                 server (vLLM, Ollama, Together, DeepSeek, Mistral, LM
#                 Studio, ...) — set API_BASE_URL to its endpoint.
#   MODEL         model id exactly as the provider names it
#   API_KEY       resolved from env: LLM_API_KEY first, then the provider's
#                 conventional variable (OPENAI_API_KEY / GROQ_API_KEY /
#                 GEMINI_API_KEY)
#   API_BASE_URL  endpoint override, needed only for openai_compatible
#                 (e.g. "http://localhost:11434/v1" for Ollama)
#
# Everything is env-overridable without editing this file:
#   LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
# (the hard-task connectors additionally override MODEL via HARD_MODEL)

API_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get(
    {
        "openai": "OPENAI_API_KEY",
        "openai_compatible": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }.get(API_PROVIDER, "OPENAI_API_KEY"),
    "",
)
API_BASE_URL = os.environ.get("LLM_BASE_URL", "")

# Generation parameters, shared by every backend.
TEMPERATURE = 0     # deterministic — the tabu/repair-memory logic relies on it
MAX_TOKENS = 2048   # 512 truncated 30-50-step hard-task plans mid-JSON

MODEL_NAME = f"{MODEL}-sda-tree-final{os.environ.get('SDA_TAG_SUFFIX', '')}"

MAX_REPLAN = 3
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
    "RELEASE", "PLUGIN", "PLUGOUT",
}

SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.

CRITICAL — READ GOALS FIRST:
Before generating your plan, identify ALL objects mentioned in the node goals and edge goals.
Your plan MUST include actions for EVERY goal object.
A plan that ignores any goal object will FAIL even if it executes without errors.

OUTPUT FORMAT - respond with ONLY a JSON object. Every argument is the object name followed by its numeric ID:
{"ACTION": ["object_name", "object_id"], "ACTION2": ["obj1_name", "obj1_id", "obj2_name", "obj2_id"]}

VALID ACTIONS:
- 1 object: DRINK, EAT, CUT, TOUCH, LOOKAT, WATCH, READ, TYPE, MOVE, WASH, RINSE, SCRUB, SQUEEZE, GRAB, SWITCHOFF, SWITCHON, CLOSE, FIND, WALK, OPEN, PUSH, PULL, WIPE, PUTON, PUTOFF, GREET, POINTAT, DROP, LIE, SIT, RUN, TURNTO, RELEASE, PLUGIN, PLUGOUT
- 2 objects: PUTBACK, PUTIN, POUR
- 0 objects: STANDUP, SLEEP, WAKEUP

NEVER use PUTOBJBACK — always place a held object explicitly: PUTBACK <object> <surface> or PUTIN <object> <container>.

RULE 1 — STANDUP IF NEEDED, THEN ALWAYS WALK FIRST:
If the current state shows the character SITTING or LYING, output STANDUP first — WALK fails while sitting or lying.
NEVER apply SWITCHON, GRAB, OPEN, TYPE or any other action to an object before WALKing to that object.
Every object interaction MUST be preceded by WALK to that object, unless the state explicitly shows the robot is already NEAR it.
Example: WALK dishwasher → OPEN dishwasher → WALK plate → GRAB plate

RULE 2 — MATCH THE GOAL RELATION (CRITICAL):
- PUTBACK <object> <target> places the object ON TOP of the target (creates relation ON)
- PUTIN <object> <target> places the object INSIDE the target (creates relation INSIDE)
- If an edge goal says "X is ON to Y", you MUST use PUTBACK — even if Y is an appliance like a washing_machine.
- If an edge goal says "X is INSIDE to Y", you MUST use PUTIN.
- If no edge goal mentions the pair: use PUTIN for enclosed containers (washing_machine, dishwasher, fridge, freezer, microwave, stove, cabinet, box, bag, trashcan) and PUTBACK for surfaces (table, counter, desk, shelf, nightstand, sofa, bench, chair)
- NEVER use PUTON with any appliance — PUTON is only for wearing clothes on your body

RULE 3 — GRAB from containers:
If an object is stored inside a closed container (cabinet, fridge, etc.), you MUST:
WALK <container> → OPEN <container> → WALK <object> → GRAB <object>
Never attempt GRAB without first opening the container the object is in.

RULE 4 — Devices are plugged in by default. Only use PLUGIN if the scene explicitly shows a device as PLUGGED_OUT. PLUGOUT is rarely needed.

RULE 5 — Max 2 objects held at once. DROP or PUTBACK before grabbing a third.

RULE 6 — The character is NEVER an action argument. A goal like "character is LYING" or "character is ON bed" is achieved by targeting the FURNITURE: LIE ["bed_name", "bed_id"] / SIT ["chair_name", "chair_id"]. NEVER write SIT, LIE, GRAB or PUTBACK with the character as the object — a character cannot be sat on, lain on, grabbed or placed.

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
- DROP <object> only discards a held object onto the floor — it is almost never the right action
- PUTOBJBACK is unreliable and must be REPLACED: use PUTBACK <object> <surface> (or PUTIN <object> <container>) with an explicit target
- To transfer water or another liquid into an appliance or container: GRAB <liquid> then POUR <liquid> <target>
  Example: {{"GRAB": ["water", "1002"], "POUR": ["water", "1002", "coffee_maker", "290"]}}
- NEVER output the same wrong action again.

Every argument must be the object name followed by its numeric ID from the scene.

Generate a corrected sequence of 2-6 actions that achieves the same goal correctly.
Output ONLY a JSON object.
Example: {{"WALK": ["washing_machine", "1000"], "GRAB": ["clothes_pants", "1001"], "PUTIN": ["clothes_pants", "1001", "washing_machine", "1000"]}}"""

ACTION_GOAL_PROMPT = """A VirtualHome robot plan executed with no errors, but the task also requires
performing this action on some object, and the plan never did it:
{verbs}

Full task context:
{node_goals}
{edge_goals}
{action_goals}

Identify the ONE object this action should target and output a single WALK-less
action for it (the runner will prepend WALK itself).
Every argument must be the object name followed by its numeric ID from the scene.
Output ONLY a JSON object with exactly one action.
Example: {{"TOUCH": ["cat", "1000"]}}"""


# =============================================================================
# LLM CLIENT — provider-agnostic
# =============================================================================
# Two backend families cover practically every hosted or local LLM today:
#
#   _OpenAIChatBackend  any OpenAI-style /chat/completions endpoint
#                       (OpenAI itself, Groq, vLLM, Ollama, Together,
#                       DeepSeek, Mistral, LM Studio, ...)
#   _GeminiBackend      Google Generative Language REST API (plain urllib,
#                       no SDK dependency)
#
# Adding a brand-new provider = one class with
#     complete(model, system_prompt, user_prompt) -> str
# registered in _BACKENDS. Nothing else in the pipeline changes — the
# planner only ever sees LLMClient.call().


class _OpenAIChatBackend:
    """Chat-completions backend for OpenAI and OpenAI-compatible servers.

    Groq, vLLM, Ollama, Together, DeepSeek etc. all expose this exact API —
    they differ only in base_url and key, so one backend serves them all.
    """

    def __init__(self, api_key: str, base_url: str = ""):
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def complete(self, model: str, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=model,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content.strip()


class _GeminiBackend:
    """Google Generative Language API backend (REST, no SDK dependency)."""

    def __init__(self, api_key: str, base_url: str = ""):
        self.api_key = api_key
        self.base = base_url or "https://generativelanguage.googleapis.com/v1beta"

    def complete(self, model: str, system_prompt: str, user_prompt: str) -> str:
        import urllib.request
        url = f"{self.base}/models/{model}:generateContent?key={self.api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": system_prompt + "\n\n" + user_prompt}]}],
            "generationConfig": {
                "temperature": TEMPERATURE,
                "maxOutputTokens": MAX_TOKENS,
            },
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        return d["candidates"][0]["content"]["parts"][0]["text"].strip()


# provider name -> (backend class, default base_url).
# API_BASE_URL (env LLM_BASE_URL) overrides the default when set.
_BACKENDS = {
    "openai":            (_OpenAIChatBackend, ""),
    "openai_compatible": (_OpenAIChatBackend, ""),
    "groq":              (_OpenAIChatBackend, "https://api.groq.com/openai/v1"),
    "gemini":            (_GeminiBackend, ""),
}


class LLMClient:
    """Provider-agnostic chat client used by the entire SDA pipeline.

    The planner code only ever calls
        call(user_prompt, system_prompt=None, label="...") -> str
    and receives the model's text reply, or "" on any transport/API error
    (the replan loop relies on that empty-string contract).

    Which provider serves the request is decided solely by the
    LLM PROVIDER CONFIGURATION block at the top of this file. MODEL is
    looked up at call time on purpose, so connectors that monkey-patch it
    (eai_sda_runner_hard / _noadapt via HARD_MODEL) keep working unchanged.
    """

    def __init__(self):
        try:
            backend_cls, default_base = _BACKENDS[API_PROVIDER]
        except KeyError:
            raise ValueError(
                f"Unknown provider {API_PROVIDER!r} — pick one of "
                f"{sorted(_BACKENDS)} or register a backend class in _BACKENDS"
            )
        self.backend = backend_cls(API_KEY, API_BASE_URL or default_base)
        logger.info(f"LLM: {API_PROVIDER} / {MODEL}")

    def call(self, user_prompt: str, system_prompt: str = None, label: str = "LLM") -> str:
        """Send one chat request and return the reply text ("" on error).

        Preserves the pipeline's observable behavior exactly: VERBOSE
        response tracing, optional prompt echoing (SHOW_PROMPTS), wall-time
        logging, and the empty-string error contract.
        """
        if system_prompt is None:
            system_prompt = SYSTEM_PROMPT
        sep = "─" * 60
        if VERBOSE and SHOW_PROMPTS:
            print(f"\n{sep}")
            print(f"[{label}] PROMPT SENT ▼")
            print(user_prompt)
            print(sep)
        t0 = time.time()
        try:
            result = self.backend.complete(MODEL, system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"API error ({API_PROVIDER}/{MODEL}): {e}")
            result = ""
        elapsed = time.time() - t0
        if VERBOSE:
            print(f"[{label}] RESPONSE RECEIVED ({elapsed:.2f}s) ▼", flush=True)
            print(result, flush=True)
            print(sep, flush=True)
        else:
            logger.info(f"  [{label}] {elapsed:.2f}s | {result}")
        return result


# =============================================================================
# Helpers
# =============================================================================

def parse_llm_output(raw: str):
    raw = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        # Salvage a truncated response (no closing brace): take everything
        # from the first "{" — the pair-regex below only extracts COMPLETE
        # "ACTION": [...] entries, so the incomplete trailing pair is dropped
        # and the plan prefix stays runnable.
        match = re.search(r"\{.*", raw, re.DOTALL)
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
    """
    Parse EAI action string keeping instance identity:
      [walk] <light> (245)            -> obj  = "light_245"
      [putin] <apple> (7) <fridge> (2)-> obj  = "apple_7", target = "fridge_2"
    So diagnosis and tree search operate per instance, not per class name.
    """
    from error_diagnosis import ActionStep
    s = str(action)
    am = re.search(r"\[(\w+)\]", s)
    pairs = re.findall(r"<([^>]+)>\s*\((\d+)\)", s)
    if pairs:
        obj = f"{pairs[0][0].strip()}_{pairs[0][1]}"
        target = f"{pairs[1][0].strip()}_{pairs[1][1]}" if len(pairs) > 1 else None
    else:
        om = re.findall(r"<([^>]+)>", s)
        obj = om[0].strip() if om else "unknown"
        target = om[1].strip() if len(om) > 1 else None
    return ActionStep(
        index=index,
        action=am.group(1).upper() if am else "UNKNOWN",
        obj=obj,
        target=target,
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
    # Already-complete token + stray trailing number: mini sometimes pairs the
    # name_id it was shown with a step COUNTER ("electric_shaver_2002", "1")
    # -> combined "electric_shaver_2002_1". VH class names never end in
    # digits, so name_<id>_<other-digits> keeps the embedded id and drops the
    # counter. (Equal-id echoes are handled above.)
    m = re.match(r"^(.+?_\d+)_\d+$", s)
    if m:
        return m.group(1)
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
        # Instance-specific: with duplicate classes (3 lights) the model must
        # know WHICH instance is in which state, same as goals/objects lists
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


def _character_target_actions(parsed) -> list:
    """
    Action names (upper-cased, deduped, order preserved) whose argument list
    targets the acting character itself. Handles both the combined token
    format ("character_65") and the raw interleaved format (["character",
    "65"]) so it works on normalized plans AND on raw parse_llm_output
    results (for _build_retry_prompt).
    """
    hits = []
    for item in (parsed or []):
        for action, args in item.items():
            if not isinstance(args, list):
                continue
            for tok in args:
                t = str(tok).strip().lower()
                if t == "character" or re.match(r"^character(_\d+)+$", t):
                    au = action.upper()
                    if au not in hits:
                        hits.append(au)
                    break
    return hits


def parse_and_validate(raw: str, relevant_name_to_id: dict,
                       goal_edge_relations: dict = None,
                       char_guard: str = None):
    """
    goal_edge_relations: {(from_id, to_id): relation_type} built from the
    task's edge goals. PUTBACK creates an ON edge and PUTIN an INSIDE edge,
    and the evaluator matches edge goals EXACTLY — so when a goal exists for
    the (obj, target) pair, the placing action is corrected to whichever one
    produces the goal's relation.

    char_guard: the acting character is never a valid object argument — no
    gold plan targets it (self-actions STANDUP/SLEEP/WAKEUP are zero-arg)
    and the executor can never satisfy sittable/lieable/grabbable on a
    character node, making every such action structurally unrepairable
    (12 of the 14 everyday local-strategy give-ups were LIE/SIT/GRAB on
    character_65). "reject" returns None so the caller can issue a
    corrective retry that names the mistake; "strip" removes the offending
    actions and keeps the rest (for retries and repair subsequences, where
    failing the whole parse would be worse than today's eventual drop);
    None preserves the old unguarded behavior.
    """
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
                        # Normalize: the LLM may echo instance names from the
                        # prompt, e.g. ["light_245", "245"] -> "light_245_245".
                        # The dedup regex collapses that back to "light_245".
                        combined.append(_normalize_name_id_token(f"{cur}_{nxt}"))
                        i += 2
                    else:
                        combined.append(_normalize_name_id_token(cur))
                        i += 1
                normalized.append({action: combined})
            else:
                normalized.append({action: args})
    parsed = normalized

    if char_guard:
        offending = _character_target_actions(parsed)
        if offending:
            if char_guard == "reject":
                logger.warning(
                    f"  🚷 Character used as object of {offending} — "
                    f"rejecting plan for corrective retry"
                )
                return None
            logger.warning(
                f"  🚷 Character used as object of {offending} — "
                f"stripping those actions"
            )
            parsed = [
                item for item in parsed
                if not _character_target_actions([item])
            ]
            if not parsed:
                return None

    CONTAINER_OBJECTS = {
        "washing_machine", "fridge", "freezer", "dishwasher",
        "microwave", "stove", "cabinet", "kitchencabinets",
        "bathroomcabinet", "garbagecan", "box", "bag", "trashcan",
    }

    def _token_id(tok):
        m = re.match(r"^.+_(\d+)$", str(tok).strip())
        return int(m.group(1)) if m else None

    corrected = []
    for item in (parsed if isinstance(parsed, list) else [{k: v} for k, v in parsed.items()]):
        for action, args in item.items():
            au = action.upper()
            if au in ("PUTBACK", "PUTIN") and isinstance(args, list) and len(args) == 2:
                new_action = action
                goal_rel = None
                if goal_edge_relations:
                    obj_id, tgt_id = _token_id(args[0]), _token_id(args[1])
                    if obj_id is not None and tgt_id is not None:
                        goal_rel = goal_edge_relations.get((obj_id, tgt_id))
                if goal_rel == "ON":
                    new_action = "PUTBACK"
                elif goal_rel == "INSIDE":
                    new_action = "PUTIN"
                elif au == "PUTBACK":
                    # No goal edge for this pair — fall back to the old
                    # container heuristic
                    target_name = str(args[1]).rsplit("_", 1)[0]
                    if target_name.lower() in CONTAINER_OBJECTS:
                        new_action = "PUTIN"
                if new_action.upper() != au:
                    logger.info(
                        f"  🔄 Corrected {au}→{new_action.upper()} for {args[0]}→{args[1]}"
                        f" ({'goal relation ' + goal_rel if goal_rel else 'container heuristic'})"
                    )
                corrected.append({new_action.upper(): args})
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
            return None
    except KeyError as e:
        logger.warning(f"Unknown action in grammar check: {e}")
        return None

    try:
        return json_to_action(parsed, relevant_name_to_id=relevant_name_to_id)
    except Exception as e:
        logger.warning(f"json_to_action failed: {e}")
        return None


def _build_retry_prompt(base_prompt: str, raw_output: str) -> str:
    """
    Corrective retry message for a failed initial-plan parse.

    Names the SPECIFIC problem when it's the empty-args pattern (model
    emits e.g. {"LIE": []} for an action that requires an object) instead
    of a generic "fix your JSON" nudge. Confirmed across 25 real failures
    (main-set run) that the generic message reproduces the IDENTICAL
    mistake on retry — checked directly: LIE/WASH/RINSE come back empty
    again both times, because the message never says what was wrong, only
    that something was. The action's own grammar (prompt AND executor
    agree: these take exactly 1 argument, same as SIT) was never the gap;
    telling the model exactly which verb and what's missing is.
    """
    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    parsed = parse_llm_output(raw_output) or []

    char_verbs = _character_target_actions(parsed)
    if char_verbs:
        verbs_str = " and ".join(char_verbs)
        return base_prompt + (
            f"\n\nIMPORTANT: your previous response used the character itself"
            f" as the object of {verbs_str}. The character is NEVER a valid"
            " action argument — a character cannot be sat on, lain on,"
            " grabbed or placed. A goal like \"character is LYING\" or"
            " \"character is ON bed\" is achieved by targeting the FURNITURE"
            " named in the goals, e.g. \"LIE\": [\"bed_name\", \"bed_id\"]."
            " Rewrite the plan with the correct objects. Respond with ONE"
            " complete, syntactically valid JSON object and nothing else."
        )

    empty_arg_verbs = []
    for item in parsed:
        for verb, args in item.items():
            v = verb.upper()
            if args == [] and v not in ZERO_ARG and v in EAI_VALID_ACTIONS and v not in empty_arg_verbs:
                empty_arg_verbs.append(v)

    if empty_arg_verbs:
        verbs_str = " and ".join(empty_arg_verbs)
        example = empty_arg_verbs[0]
        return base_prompt + (
            f"\n\nIMPORTANT: your previous response used {verbs_str} with an"
            f" EMPTY argument list (e.g. \"{example}\": []). {verbs_str} require"
            f" exactly ONE object argument — specify what it applies to, e.g."
            f" \"{example}\": [\"object_name\", \"object_id\"]."
            " Respond with ONE complete, syntactically valid JSON object and"
            " nothing else."
        )

    return base_prompt + (
        "\n\nIMPORTANT: your previous response was invalid or truncated."
        " Respond with ONE complete, syntactically valid JSON object and"
        " nothing else. If the plan is long, keep it complete anyway."
    )


def hist_pos_to_plan_pos(h: int, hist_to_plan: list, failed_plan_idx) -> int:
    """
    1-based successful-history index -> 1-based plan position.

    hist_to_plan[k] = 0-based plan index of the (k+1)-th successful action
    of the attempt (i.e. plan positions before the failure that were not
    skipped). Identity mapping when nothing was skipped. A history position
    past the recorded successes maps to the failed action itself.
    """
    if 1 <= h <= len(hist_to_plan):
        return hist_to_plan[h - 1] + 1
    if failed_plan_idx is not None:
        return failed_plan_idx + 1
    return h


_GOAL_STATE_EFFECTS = {
    "CLOSE": "CLOSED", "OPEN": "OPEN",
    "SWITCHON": "ON", "SWITCHOFF": "OFF",
    "PLUGIN": "PLUGGED_IN", "PLUGOUT": "PLUGGED_OUT",
}


def goal_state_action_pair(action, goal_state_pairs: set):
    """
    (obj_id, STATE) that this EAI action achieves, if that pair is one of the
    task's node goals; None otherwise. Used by the goal guard to stop
    goal-achieving actions from being silently deleted by skips/drops.
    """
    s = str(action)
    am = re.search(r"\[(\w+)\]", s)
    om = re.search(r"\((?:1\.)?(\d+)\)", s)
    if not am or not om:
        return None
    state = _GOAL_STATE_EFFECTS.get(am.group(1).upper())
    if state is None:
        return None
    pair = (int(om.group(1)), state)
    return pair if pair in goal_state_pairs else None


_STATE_TO_ACTION = {v: k for k, v in _GOAL_STATE_EFFECTS.items()}


def _attempt_goal_completion(
    motion_planner, unsat_node, unsat_edge, unsat_action,
    name_map, goal_edge_relations, llm,
    node_goal_str, edge_goal_str, action_goal_str,
):
    """
    Direct, no-search completion of goals a CLEAN-EXECUTING plan left unmet.

    Covers two failure shapes the precondition-failure-triggered repair loop
    structurally cannot see, because nothing ever raised an error for it to
    diagnose: (a) a goal the LLM's plan never attempted at all, and (b) a
    goal a repair elsewhere in the plan accidentally undid (e.g. PUTBACK-ing
    an object the goal needs held). scene_evaluate_wID — the same function
    the offline evaluator scores with — is what finds these; this function
    only decides what to do once one is found.

    Not a search: each goal shape here has exactly one obvious satisfying
    sequence (walk over, do the thing), so this mirrors the existing
    node-state goal guard's pattern — build the sequence, execute it for
    real against motion_planner, commit only if the WHOLE sequence
    succeeds — rather than invoking the BFS tree, which exists to choose
    among several candidate repairs for a diagnosed precondition failure,
    not to re-derive an undiagnosed goal from scratch.
    """
    committed = []
    # Tokens the character has already been walked to THIS call, even by a
    # sequence that didn't fully commit (WALK is a real, un-rollback-able
    # action against motion_planner the moment it succeeds — the earlier
    # steps of a later-failing sequence still happened for real). Reusing
    # this across node/edge/action sub-passes below stops one sub-pass's
    # WALK from resetting a spatial fact (e.g. FACING) that an earlier
    # sub-pass in this SAME call just established for the same object —
    # confirmed happening on task 803_2: an edge-goal FACING fix (WALK,
    # TURNTO) was undone by the action-goal fix re-walking to the same
    # remote_control right before TOUCH.
    walked_to = set()

    def _nodes():
        return {n["id"]: n for n in motion_planner.env_state.to_dict()["nodes"]}

    def _held_ids():
        char = motion_planner.acting_char_id
        return {
            e["to_id"] for e in motion_planner.env_state.to_dict()["edges"]
            if e["from_id"] == char and e["relation_type"] in ("HOLDS_RH", "HOLDS_LH")
        }

    def _mk(action_dict):
        return parse_and_validate(
            json.dumps(action_dict), name_map, goal_edge_relations
        ) or []

    def _walk(tok):
        """WALK to tok unless this call already walked there."""
        return [] if tok in walked_to else _mk({"WALK": [tok]})

    def _run(seq):
        done = []
        for a in seq:
            okf, _ = motion_planner.my_execute_primitive_action_eval(a)
            if not okf:
                return None
            m = re.match(r"^\[(\w+)\]\s*<([^>]+)>\s*\((\d+)\)", str(a))
            if m and m.group(1).upper() == "WALK":
                # Tracks CURRENT location, not everywhere ever visited this
                # call — walking to a new object leaves the old one behind,
                # so any earlier entries no longer describe where the
                # character actually is and must be dropped, not kept.
                walked_to.clear()
                walked_to.add(f"{m.group(2)}_{m.group(3)}")
            done.append(a)
        return done

    char_id = motion_planner.acting_char_id
    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}

    # ── Node/state goals: never planned, or planned then lost ───────────────
    for g in unsat_node:
        oid, cname, state = g["id"], g["class_name"], str(g["state"]).upper()
        action = _STATE_TO_ACTION.get(state)
        if not action:
            continue  # state has no single achieving action (e.g. DIRTY)
        obj_tok = f"{cname}_{oid}"
        done = _run(_walk(obj_tok) + _mk({action: [obj_tok]}))
        if done:
            committed.extend(done)

    # ── Edge/relation goals: placement, proximity, posture, facing, holding ──
    for g in unsat_edge:
        frm, to, rel = g["from_id"], g["to_id"], g["relation_type"]
        nodes = _nodes()

        if rel in ("ON", "INSIDE"):
            if frm == char_id:
                # character-posture goal (sits/lies ON/INSIDE furniture),
                # not an object-placement goal — frm is the character, not
                # an object to carry.
                #
                # relation_type here does NOT encode posture: VirtualHome
                # records "character ON couch" identically whether sitting
                # or lying, so ON/INSIDE can't tell SIT from LIE. The
                # authoritative signal is the character's OWN node-state
                # goal (id == frm, state LYING/SITTING) — look for it
                # before falling back to a default. Confirmed against real
                # evaluator output: "Relax on sofa" (edge: character ON
                # couch, node: character LYING) was previously guessed as
                # SIT purely from rel=="ON", satisfying the edge goal while
                # leaving the LYING node goal permanently unmet.
                tgt = nodes.get(to)
                if not tgt:
                    continue
                tgt_tok = f"{tgt['class_name']}_{to}"
                posture_state = next(
                    (str(ng["state"]).upper() for ng in unsat_node
                     if ng["id"] == frm and str(ng["state"]).upper() in ("LYING", "SITTING")),
                    None,
                )
                if posture_state:
                    posture = "LIE" if posture_state == "LYING" else "SIT"
                else:
                    # no explicit posture goal found — fall back to the
                    # rel-based default (right far more often than wrong,
                    # per the pre-fix data: only "relax"/"sleep"-style
                    # tasks pair ON with a LYING requirement)
                    posture = "SIT" if rel == "ON" else "LIE"
                done = _run(_walk(tgt_tok) + _mk({posture: [tgt_tok]}))
                if done:
                    committed.extend(done)
                continue

            obj_node, tgt_node = nodes.get(frm), nodes.get(to)
            if not obj_node or not tgt_node:
                continue
            obj_tok = f"{obj_node['class_name']}_{frm}"
            tgt_tok = f"{tgt_node['class_name']}_{to}"
            # Unconditional WALK throughout this branch, NOT _walk(): this
            # sequence itself moves between two different locations (fetch
            # the object, then walk to the target), so an earlier _walk()
            # decision within the SAME seq can't know a later one in the
            # SAME seq is about to invalidate it — walked_to is only
            # updated once a WALK actually executes, but this whole
            # sequence is built before any of it runs. Confirmed causing a
            # dropped WALK (task 190_1 shape) when two separate placement
            # goals shared a target: goal 2's fetch-walk to a different
            # object made goal 1's already-confirmed "at the target"
            # stale, but the pre-built seq had no way to see that. The
            # cross-goal memoization this file uses elsewhere (FACING then
            # a same-object action goal, confirmed fixing task 803_2) is
            # safe because each of THOSE branches issues exactly one WALK
            # per goal; this branch issues up to three in one seq, so it's
            # scoped out rather than risking the same failure mode again.
            seq = []
            # containers opened/switched-off by THIS fix, in the order
            # they need restoring — tracked as lists (not booleans) since
            # the object's own source container and the destination can
            # be two different containers, or the SAME one (common case:
            # an object stored inside the very appliance it gets placed
            # back into) — the "not in" guards below avoid operating on
            # the same container twice in that case.
            opened_toks, switched_off_toks = [], []
            if frm not in _held_ids():
                # The object may itself be trapped inside a closed
                # container — e.g. the node-goal loop just above already
                # closed the very container this object lives in. Open it
                # before fetching. Confirmed needed on 310_2/764_2/229_1/
                # 183_2: ground_coffee sits INSIDE the coffee_maker, which
                # the CLOSED node-goal fix (running earlier in this same
                # call) had already shut — GRAB then failed silently with
                # no fallback, permanently dropping the whole placement.
                src_container_id = None
                for e in motion_planner.env_state.to_dict()["edges"]:
                    if e["from_id"] == frm and e["relation_type"] == "INSIDE":
                        cand = nodes.get(e["to_id"])
                        if cand and "CAN_OPEN" in (cand.get("properties") or []):
                            src_container_id = e["to_id"]
                            break
                if src_container_id is not None:
                    cnode = nodes[src_container_id]
                    ctok = f"{cnode['class_name']}_{src_container_id}"
                    cstates = {str(s).upper() for s in cnode.get("states", [])}
                    if "CLOSED" in cstates:
                        if "ON" in cstates:
                            seq += _mk({"WALK": [ctok]}) + _mk({"SWITCHOFF": [ctok]})
                            switched_off_toks.append(ctok)
                        seq += _mk({"WALK": [ctok]}) + _mk({"OPEN": [ctok]})
                        opened_toks.append(ctok)
                seq += _mk({"WALK": [obj_tok]}) + _mk({"GRAB": [obj_tok]})
            tgt_states = {str(s).upper() for s in tgt_node.get("states", [])}
            if "CLOSED" in tgt_states and tgt_tok not in opened_toks:
                if "ON" in tgt_states and tgt_tok not in switched_off_toks:
                    # SWITCHOFF needs the character next to the target, but
                    # the fetch step above (if it ran) just walked to the
                    # OBJECT instead — confirmed failing for real (task
                    # 190_1: bowl fetch leaves the character away from the
                    # dishwasher, SWITCHOFF then fails outright) since the
                    # mock test executor accepts every action regardless of
                    # position and could never have caught a missing WALK.
                    seq += _mk({"WALK": [tgt_tok]}) + _mk({"SWITCHOFF": [tgt_tok]})
                    switched_off_toks.append(tgt_tok)
                seq += _mk({"WALK": [tgt_tok]}) + _mk({"OPEN": [tgt_tok]})
                opened_toks.append(tgt_tok)
            # PUTBACK/PUTIN choice is authoritative from the goal's own
            # relation_type, and double-checked downstream: parse_and_validate
            # re-derives it from goal_edge_relations (built from these SAME
            # edge goals) and corrects if it disagrees — unlike the posture
            # guess above, this one has a real safety net.
            place = "PUTBACK" if rel == "ON" else "PUTIN"
            seq += _mk({"WALK": [tgt_tok]}) + _mk({place: [obj_tok, tgt_tok]})
            done = _run(seq)
            if done:
                committed.extend(done)
                if opened_toks or switched_off_toks:
                    # Opening/switching off was a MEANS to fetch/place the
                    # object, not something asked for — leaving it that
                    # way silently undoes whatever CLOSED/ON goal was true
                    # before this ran (confirmed failing for real: task
                    # 190_1's dishwasher ended up open+off after the bowl
                    # placement, destroying its own already-satisfied
                    # CLOSED and ON node goals). Put it back. A separate
                    # _run so a restore failure doesn't discard the
                    # placement that already succeeded. CLOSE before
                    # SWITCHON per container, matching real-world handling.
                    restore = []
                    for ctok in opened_toks:
                        restore += _mk({"WALK": [ctok]}) + _mk({"CLOSE": [ctok]})
                    for ctok in switched_off_toks:
                        restore += _mk({"WALK": [ctok]}) + _mk({"SWITCHON": [ctok]})
                    redone = _run(restore)
                    if redone:
                        committed.extend(redone)

        elif rel == "CLOSE":
            target_id = to if frm == char_id else (frm if to == char_id else None)
            node = nodes.get(target_id) if target_id is not None else None
            if not node:
                continue
            done = _run(_walk(f"{node['class_name']}_{target_id}"))
            if done:
                committed.extend(done)

        elif rel == "FACING":
            target_id = to if frm == char_id else (frm if to == char_id else None)
            node = nodes.get(target_id) if target_id is not None else None
            if not node:
                continue
            tok = f"{node['class_name']}_{target_id}"
            done = _run(_walk(tok) + _mk({"TURNTO": [tok]}))
            if done:
                committed.extend(done)

        elif rel in ("HOLDS_RH", "HOLDS_LH"):
            # NOTE: GRAB has no way to request a specific hand, so this can
            # satisfy "holds SOMETHING" without satisfying an exact-hand
            # goal if the executor's free-hand assignment picks the other
            # one — a limitation of the action vocabulary, not fixable here
            # (confirmed on task 696_1: both hands ended up filled, but not
            # in the specific left/right arrangement the goal required).
            obj_id = to if frm == char_id else frm
            node = nodes.get(obj_id)
            if not node:
                continue
            tok = f"{node['class_name']}_{obj_id}"
            done = _run(_walk(tok) + _mk({"GRAB": [tok]}))
            if done:
                committed.extend(done)
        # other relation types (BETWEEN, ...) are rare and unmodeled here —
        # left unsatisfied rather than guessed at

    # ── Action goals: bare verb, no object in the goal spec itself ───────────
    # Parsed from the LLM's raw JSON directly (not via parse_and_validate,
    # whose output is already-formatted "[ACTION] <name> (id)" strings with
    # no object accessor) so the object name/id pair can be rebuilt into a
    # WALK target and re-run through the same _mk() path as every other
    # branch here.
    for raw_goal in unsat_action:
        # Goal entries can be OR-expressions ("TOUCH|PUSH" — either verb
        # satisfies it per check_order_with_or_score) — split rather than
        # handing the LLM a literal "TOUCH|PUSH" as if it were one verb.
        verbs = [v.strip().upper() for v in str(raw_goal).split("|") if v.strip()]
        if not verbs:
            continue

        zero_arg_verbs = [v for v in verbs if v in ZERO_ARG]
        if zero_arg_verbs:
            # No object to find or WALK to — STANDUP/SLEEP/WAKEUP apply to
            # the character itself.
            done = _run(_mk({zero_arg_verbs[0]: []}))
            if done:
                committed.extend(done)
            continue

        prompt = ACTION_GOAL_PROMPT.format(
            verbs=" or ".join(verbs),
            node_goals=node_goal_str, edge_goals=edge_goal_str,
            action_goals=action_goal_str,
        )
        raw = llm.call(prompt, system_prompt=SYSTEM_PROMPT, label="ACTION GOAL OBJECT")
        parsed = filter_valid_actions(parse_llm_output(raw) or [])
        items = parsed if isinstance(parsed, list) else [{k: v} for k, v in parsed.items()]
        for item in items:
            for action, args in item.items():
                # This path only ever builds a 1-argument invocation
                # (_mk({action: [obj_tok]})). A verb needing 2 args (POUR,
                # PUTBACK, PUTIN) would fail grammar validation inside
                # _mk() and silently return [] — leaving just the WALK
                # committed with no action behind it. Confirmed causing
                # exactly that for real: task 229_1's unmet action goal
                # was POUR; this path asked the LLM for "the object", got
                # one object back, tried POUR with 1 arg, and committed a
                # dangling WALK with no POUR at all. Skip cleanly instead
                # of doing that — completing a 2-object action goal needs
                # identifying BOTH a source and a target, which this
                # single-object prompt was never designed to do.
                expected_args = _eai_valid_actions.get(action.upper(), (None, None))[1]
                if expected_args != 1:
                    continue
                args = [str(a).strip() for a in args]
                if len(args) >= 2 and args[1].isdigit():
                    obj_tok = f"{args[0]}_{args[1]}"
                elif len(args) == 1:
                    obj_tok = args[0]
                else:
                    continue
                done = _run(_walk(obj_tok) + _mk({action: [obj_tok]}))
                if done:
                    committed.extend(done)
                    break  # one object satisfies a single-verb goal
            if committed:
                break

    return committed


def _repair_key(tree_result: list) -> tuple:
    """
    Canonical key for a tree repair, matching the (ACTION, obj, target)
    triples used inside the BFS, so it can be banned on later attempts.
    """
    key = []
    for d in tree_result:
        action = list(d.keys())[0].upper()
        args = list(d.values())[0]
        obj = args[0] if args else "character"
        target = args[1] if len(args) > 1 else None
        key.append((action, obj, target))
    return tuple(key)


def _resolve_to_name_id(obj_name: str, relevant_name_to_id: dict,
                        full_name_to_id: dict = None) -> str:
    """
    Strict resolution:
      - pass through exact class_id keys (relevant map first, then the
        full-scene map — the tree may legitimately reference a container
        that is not among the goal-relevant objects)
      - resolve plain class name only if exactly one match exists
        (checked in the relevant map first, then the full map)
      - reject ambiguity instead of silently picking one
    """
    obj_name = _normalize_name_id_token(obj_name)

    if obj_name in relevant_name_to_id:
        return obj_name
    if full_name_to_id and obj_name in full_name_to_id:
        return obj_name

    # Suffix must be purely numeric: "light" may match "light_245" but never
    # "light_bulb_31" (VH has prefix-colliding classes: light/light_bulb,
    # floor/floor_lamp, table/table_cloth, wall/wall_clock, ...)
    id_pattern = re.compile(rf"^{re.escape(obj_name)}_\d+$")
    for mapping in (relevant_name_to_id, full_name_to_id or {}):
        matches = [
            k for k in mapping
            if k == obj_name or id_pattern.match(k)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous object '{obj_name}' with candidates {matches}")

    # Typo tolerance: VirtualHome scene classes contain misspellings
    # ("coffe_maker") while LLMs emit the correct spelling ("coffee_maker"),
    # which discarded otherwise-valid repairs (observed on 721_2).
    # Conservative: accept only when the fuzzy match maps to exactly ONE
    # class name AND that class resolves to exactly one instance.
    m_id = re.match(r"^(.+)_(\d+)$", obj_name)
    base = m_id.group(1) if m_id else obj_name
    known_classes = set()
    for mapping in (relevant_name_to_id, full_name_to_id or {}):
        for k in mapping:
            known_classes.add(re.sub(r"_\d+$", "", k))
    close = difflib.get_close_matches(base, sorted(known_classes), n=2, cutoff=0.85)
    if len(close) == 1:
        cls = close[0]
        if m_id:
            cand = f"{cls}_{m_id.group(2)}"
            for mapping in (relevant_name_to_id, full_name_to_id or {}):
                if cand in mapping:
                    logger.info(f"  ✏️  Fuzzy-resolved '{obj_name}' → '{cand}'")
                    return cand
        cls_pattern = re.compile(rf"^{re.escape(cls)}_\d+$")
        for mapping in (relevant_name_to_id, full_name_to_id or {}):
            matches = [k for k in mapping if k == cls or cls_pattern.match(k)]
            if len(matches) == 1:
                logger.info(f"  ✏️  Fuzzy-resolved '{obj_name}' → '{matches[0]}'")
                return matches[0]
            if len(matches) > 1:
                raise ValueError(
                    f"Ambiguous object '{obj_name}' (≈{cls}) with candidates {matches}"
                )

    raise ValueError(f"Unknown object '{obj_name}'")


def subtree_results_to_eai(subtree_result: list, relevant_name_to_id: dict,
                           full_name_to_id: dict = None,
                           goal_edge_relations: dict = None):
    if not subtree_result:
        return None

    filtered = filter_valid_actions(subtree_result)
    if not filtered:
        return None

    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    processed = []

    def _token_id(tok):
        m = re.match(r"^.+_(\d+)$", str(tok).strip())
        return int(m.group(1)) if m else None

    try:
        for item in (filtered if isinstance(filtered, list) else [{k: v} for k, v in filtered.items()]):
            for action, args in item.items():
                if action.upper() in ZERO_ARG:
                    processed.append({action: []})
                else:
                    resolved = [
                        _resolve_to_name_id(obj, relevant_name_to_id, full_name_to_id)
                        for obj in args
                    ]
                    # Same goal-relation correction as parse_and_validate:
                    # a repair's PUTIN toward an ON goal (or vice versa)
                    # would execute but silently miss the goal edge.
                    au = action.upper()
                    if (goal_edge_relations and au in ("PUTBACK", "PUTIN")
                            and len(resolved) == 2):
                        ids = (_token_id(resolved[0]), _token_id(resolved[1]))
                        goal_rel = (goal_edge_relations.get(ids)
                                    if None not in ids else None)
                        if goal_rel == "ON" and au != "PUTBACK":
                            logger.info(f"  🔄 Repair corrected PUTIN→PUTBACK (goal ON): {resolved}")
                            action = "PUTBACK"
                        elif goal_rel == "INSIDE" and au != "PUTIN":
                            logger.info(f"  🔄 Repair corrected PUTBACK→PUTIN (goal INSIDE): {resolved}")
                            action = "PUTIN"
                    processed.append({action: resolved})
    except ValueError as e:
        logger.warning(f"Subtree object resolution failed: {e}")
        return None

    # json_to_action needs a map that contains whichever key resolution chose
    merged_map = dict(full_name_to_id or {})
    merged_map.update(relevant_name_to_id)

    try:
        ok, err = _check_grammar_combined(processed)
        if not ok:
            logger.warning(f"Subtree grammar failed: {err}")
            return None
        return json_to_action(processed, relevant_name_to_id=merged_map)
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
        # (from_id, to_id) -> required relation, for goal-aware PUTBACK/PUTIN
        goal_edge_relations = {
            (g["from_id"], g["to_id"]): g["relation_type"] for g in edge_goals
        }
        # (id, STATE) node goals — the goal guard relocates actions achieving
        # these instead of letting skips/drops delete them permanently
        goal_state_pairs = {
            (g["id"], str(g["state"]).upper()) for g in node_goals
        }

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

        # Full-scene name_id map: lets subtree repairs reference objects the
        # goal diff didn't include (e.g. the cabinet a goal object is inside)
        full_name_to_id = {}
        try:
            for node in motion_planner.env_graph.get_nodes():
                full_name_to_id[f"{node.class_name}_{node.id}"] = node.id
        except Exception as e:
            logger.warning(f"  Could not build full scene map: {e}")

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

        if VERBOSE:
            print(f"\n{'='*60}", flush=True)
            print(f"TASK: {file_id}  |  {task_name}", flush=True)
            print(f"{'='*60}", flush=True)

        # ── Generate initial plan ─────────────────────────────────────────────
        raw_output = self.llm.call(base_prompt, label="INITIAL PLAN")
        logger.info(f"  Initial plan: {raw_output}")

        actions = parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                     char_guard="reject")
        if not actions:
            # One corrective retry — temp-0 re-asks must change the prompt or
            # they reproduce the same broken output verbatim.
            logger.warning(f"  Could not parse initial plan for {file_id} — retrying once")
            retry_prompt = _build_retry_prompt(base_prompt, raw_output)
            raw_output = self.llm.call(retry_prompt, label="INITIAL PLAN (retry)")
            # "strip" on the retry: if the model repeats the character
            # mistake, salvage the rest of the plan instead of failing the
            # whole parse — no worse than the eventual repair-loop drop,
            # and it doesn't burn replan budget on an unfixable action.
            actions = parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                         char_guard="strip")
        if not actions:
            logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        current_plan_eai = actions
        initial_env_state = None
        last_failure_sig = None
        tried_repairs = {}   # failure_sig -> set of repair keys already spliced
        banned_cands = {}    # failure_sig -> set of (ACTION, obj, target) that failed in env
        last_spliced = None  # (failure_sig, set of repair action strings)
        deferred_goal_actions = []  # goal-achieving actions removed by drops (goal guard)

        # L4: removals (already_satisfied / loop-breaker drops) do NOT consume
        # the repair budget — only actual repair attempts (LLM calls) do.
        # total-iteration cap guards against pathological removal cascades.
        attempt = -1
        max_total_iters = MAX_REPLAN + len(current_plan_eai) + 4
        while True:
            attempt += 1
            motion_planner.reset()
            history_actions = []
            history_env_states = [copy.deepcopy(motion_planner.env_state.to_dict())]

            if initial_env_state is None:
                initial_env_state = history_env_states[0]

            executable = True
            failed_action = None
            failed_plan_idx = None   # 0-based position in current_plan_eai
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
                    # failed_step.index counts only SUCCESSFUL actions, so it
                    # drifts from the plan position when steps were skipped.
                    # Keep the true 0-based plan index for exact removal.
                    failed_plan_idx = action_idx
                    logger.info(f"  ❌ {action} | {err_type}")
                    break
                else:
                    if VERBOSE:
                        print("OK")
                    history_actions.append(action)
                    history_env_states.append(
                        copy.deepcopy(motion_planner.env_state.to_dict())
                    )

            if executable:
                clean_plan = [
                    a for i, a in enumerate(current_plan_eai)
                    if i not in skipped_indices
                ]

                # ── Goal guard ────────────────────────────────────────────
                # Goal-achieving actions vanish from plans two ways: skipped
                # as ADDITIONAL_STEP because the LLM placed them at a moment
                # they were redundant (CLOSE before the cupboard was ever
                # opened — 16× cupboard-CLOSED misses in the hard-50 run),
                # or dropped during repair. If the node goal is still unmet
                # at plan end, relocate the action there and re-execute —
                # wrong-temporal-order repair using only the LLM's own
                # actions, never new planning.
                guard_candidates = [
                    current_plan_eai[i] for i in skipped_indices
                ] + deferred_goal_actions
                guard_added = []
                seen_pairs = set()
                env_nodes = {
                    n["id"]: n
                    for n in motion_planner.env_state.to_dict()["nodes"]
                }
                for act in guard_candidates:
                    pair = goal_state_action_pair(act, goal_state_pairs)
                    if not pair or pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    oid, state = pair
                    node = env_nodes.get(oid)
                    if node is None or state in {
                        str(x).upper() for x in node.get("states", [])
                    }:
                        continue  # object gone or goal already satisfied
                    gmap = dict(full_name_to_id)
                    gmap.update(relevant_name_to_id)
                    walk = parse_and_validate(
                        json.dumps({"WALK": [f"{node['class_name']}_{oid}"]}),
                        gmap, goal_edge_relations,
                    )
                    seq = (walk or []) + [act]
                    done = []
                    for a in seq:
                        okf, _ = motion_planner.my_execute_primitive_action_eval(a)
                        if not okf:
                            break
                        done.append(a)
                    # commit only complete WALK+action pairs — a lone WALK
                    # pollutes the plan without achieving anything
                    if len(done) == len(seq):
                        guard_added.extend(done)
                if guard_added:
                    clean_plan = clean_plan + guard_added
                    logger.info(
                        f"  🛡️ Goal guard re-appended: "
                        f"{[str(a) for a in guard_added]}"
                    )
                    if VERBOSE:
                        print(f"\n  GOAL GUARD re-appended: {[str(a) for a in guard_added]}")

                # ── Generalized goal check ─────────────────────────────────
                # The guard above only ever catches a NODE-state goal whose
                # own action was generated and then skipped/dropped. It
                # can't see: a goal never planned at all, an edge/relation
                # goal, an action goal, or a goal a repair elsewhere in the
                # plan silently undid (e.g. PUTBACK-ing an object the goal
                # needs held) — none of those raise an execution error for
                # anything upstream to diagnose, so "executable" was being
                # treated as "done" regardless of whether the goal held.
                # scene_evaluate_wID is the SAME function the offline
                # evaluator scores with, so "satisfied" here can't silently
                # drift from what actually gets scored.
                try:
                    (_, _, _, all_goals_ok, unsat_node, unsat_edge, unsat_action) = (
                        scene_evaluate_wID(
                            motion_planner.env_state.to_dict(),
                            node_goals, edge_goals, motion_planner.acting_char_id,
                            action_seq=[str(a) for a in clean_plan],
                            action_goals=goals["actions"],
                        )
                    )
                    if not all_goals_ok:
                        gmap = dict(full_name_to_id)
                        gmap.update(relevant_name_to_id)
                        extra = _attempt_goal_completion(
                            motion_planner, unsat_node, unsat_edge, unsat_action,
                            gmap, goal_edge_relations, self.llm,
                            node_goal_str, edge_goal_str, action_goal_str,
                        )
                        if extra:
                            clean_plan = clean_plan + extra
                            logger.info(f"  🎯 Goal completion appended: {[str(a) for a in extra]}")
                            if VERBOSE:
                                print(f"\n  GOAL COMPLETION appended: {[str(a) for a in extra]}")
                except Exception as e:
                    logger.warning(f"  Goal completion check failed, keeping plan as-is: {e}")

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

            if replan_count >= MAX_REPLAN or attempt >= max_total_iters:
                logger.info(f"  ⚠️  Max replanning reached for {file_id}")
                if VERBOSE:
                    print(f"\n  FINAL OUTPUT SAVED (max replans reached):", flush=True)
                    print(f"  {raw_output}", flush=True)
                break

            # ── SDA Error Backtrack and Diagnosis ─────────────────────────────
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
                    initial_env_dict=history_env_states[0],
                    failed_plan_pos=(
                        failed_plan_idx + 1 if failed_plan_idx is not None
                        else None
                    ),
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

            # ── Repair memory ─────────────────────────────────────────────────
            # Everything is deterministic (temp-0 LLM + simulator), so a
            # repeated failure signature means the last repair did not help.
            # Instead of dropping immediately, the tree is re-queried with the
            # already-tried repairs BANNED, so BFS yields the next-shortest
            # alternative. Only when no alternative exists is the action
            # dropped (see the tree-result handling below).
            failure_sig = (
                str(failed_action),
                err_type,
                tuple(diagnosis.unsatisfied_needs or []),
            )
            repeat_failure = (failure_sig == last_failure_sig)
            last_failure_sig = failure_sig

            # Alternation case (A→B→A): the action that just failed was
            # itself inserted by the previous repair. Ban that specific
            # candidate for the signature the repair was generated for, so
            # the next repair for THAT problem routes around it.
            if last_spliced is not None and str(failed_action) in last_spliced[1]:
                fs = parse_eai_action(failed_action, 0)
                banned_cands.setdefault(last_spliced[0], set()).add(
                    (fs.action, fs.obj, fs.target)
                )
                logger.info(
                    f"  🚫 Repair action failed in env — banned "
                    f"{(fs.action, fs.obj, fs.target)} for {last_spliced[0][0]}"
                )

            # ── Compute splice window ─────────────────────────────────────────
            # t_start is in HISTORY coordinates (slices history_actions /
            # history_env_states); t_end is in PLAN coordinates (slices
            # current_plan_eai). They coincide until steps get skipped, so the
            # window's plan-side start must be converted explicitly.
            t_start = diagnosis.t_start if diagnosis.t_start is not None else failed_step.index
            t_end = diagnosis.t_end if diagnosis.t_end is not None else (
                failed_plan_idx + 1 if failed_plan_idx is not None else failed_step.index
            )

            hist_to_plan = [
                i for i in range(failed_plan_idx if failed_plan_idx is not None else 0)
                if i not in skipped_indices
            ]
            win_start_plan = hist_pos_to_plan_pos(t_start, hist_to_plan, failed_plan_idx)

            before = history_actions[:max(0, t_start - 1)]
            after = current_plan_eai[t_end:]

            # For "reconstruct", t_source (root_cause_at) is the diagnosed
            # CAUSE of the failure — e.g. a premature PUTBACK that releases
            # an object right before it's needed again. The window always
            # includes it (t_start is derived from it), but blindly
            # retrying it reproduces the exact same failure regardless of
            # what the search finds beforehand: confirmed via direct,
            # deterministic replay (task 163_1) that the search's own
            # target check trivially "succeeds" from the state just BEFORE
            # t_source runs (since the precondition it corrupts hasn't
            # been corrupted YET at that point) — so the search returns an
            # insufficient fix (e.g. a bare WALK), t_source's action then
            # re-executes anyway as part of the "retry", and undoes
            # whatever holding/state the earlier steps established. Once
            # t_source itself never runs again, the state it used to
            # corrupt is simply never corrupted — this doesn't depend on
            # the search finding a better fix at all.
            # Only when root_cause is a DIFFERENT, earlier action than the
            # one that actually failed — a straightforward missing
            # prerequisite (e.g. PUTBACK failing for its own lack of
            # holds_obj) diagnoses root_cause_at AS the failed action
            # itself, and excluding "the root cause" there would exclude
            # the very action being repaired. Confirmed regressing task
            # 327_2 exactly this way in testing: both PUTBACKs got
            # excluded as their own "root cause", leaving both objects
            # grabbed but never placed — caught before this ever shipped.
            #
            # Also never exclude it when the need is not_both_hands_full:
            # there the "root cause" is whichever earlier GRAB tipped hands
            # over capacity — a NECESSARY pickup, not a corrupting action
            # like the cases above. Excluding it doesn't fix anything, it
            # just defers the pickup to whatever LATER replan happens to
            # rediscover it (the object's own PUTBACK failing on
            # holds_obj) — costing a full extra replan cycle per excluded
            # item. Confirmed on 954_2 (5-item carry): excluding GRAB
            # clothes_dress then GRAB clothes_shirt this way burned all 3
            # replans just re-discovering pickups the goal-aware
            # placement fix (prefer_goal_placement) already handles
            # directly, exhausting the budget before clothes_shirt could
            # be re-fetched.
            root_cause_plan_pos = None
            if (diagnosis.replan_strategy == "reconstruct"
                    and diagnosis.root_cause_at is not None
                    and diagnosis.root_cause_at != failed_step.index
                    and "not_both_hands_full" not in diagnosis.unsatisfied_needs):
                root_cause_plan_pos = hist_pos_to_plan_pos(
                    diagnosis.root_cause_at, hist_to_plan, failed_plan_idx
                )
                if root_cause_plan_pos == failed_plan_idx + 1:
                    root_cause_plan_pos = None

            # Skip-aware window in plan coordinates; overrides the wrapper's
            # slice, which assumes history == plan positions.
            orig_subseq = full_plan_steps[win_start_plan - 1 : t_end]
            if root_cause_plan_pos is not None:
                orig_subseq = [s for s in orig_subseq if s.index != root_cause_plan_pos]

            # ── Action already satisfied: goal is already true, just remove it ─
            if diagnosis.replan_strategy == "already_satisfied":
                idx = failed_plan_idx if failed_plan_idx is not None else failed_step.index - 1
                current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                raw_output = plan_to_json_str(current_plan_eai)
                if VERBOSE:
                    print(f"\n  ACTION ALREADY SATISFIED — removed: {failed_action}")
                continue

            # ── Special handling: semantically wrong action ───────────────────
            if diagnosis.replan_strategy == "wrong_action":
                # The replacement was already tried once for this exact
                # failure — a second identical failure means the LLM cannot
                # produce a working substitute. Drop the action.
                if repeat_failure:
                    idx = failed_plan_idx if failed_plan_idx is not None else failed_step.index - 1
                    dropped = current_plan_eai[idx]
                    current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                    raw_output = plan_to_json_str(current_plan_eai)
                    last_spliced = None
                    if goal_state_action_pair(dropped, goal_state_pairs):
                        deferred_goal_actions.append(dropped)
                    logger.info(f"  🔁 Wrong-action fix did not help — dropped: {dropped}")
                    if VERBOSE:
                        print(f"\n  WRONG-ACTION FIX FAILED TWICE — dropping: {dropped}")
                    continue

                wrong_prompt = WRONG_ACTION_PROMPT.format(
                    failed_action=failed_action,
                    reason=get_unsatisfied_explanation(diagnosis.unsatisfied_needs),
                )
                replan_count += 1
                wrong_raw = self.llm.call(wrong_prompt, system_prompt=SYSTEM_PROMPT, label="WRONG ACTION FIX")
                # Resolve against the full scene too — the correct replacement
                # may reference an object outside the goal diff
                wrong_map = dict(full_name_to_id)
                wrong_map.update(relevant_name_to_id)
                new_subseq = parse_and_validate(wrong_raw, wrong_map, goal_edge_relations,
                                                char_guard="strip")

                if new_subseq:
                    # Never re-accept the action just diagnosed as wrong —
                    # otherwise the next attempt replays the same failure
                    failed_str = str(failed_action)
                    kept = [a for a in new_subseq if str(a) != failed_str]
                    if len(kept) != len(new_subseq):
                        logger.info("  🚫 Wrong-action fix repeated the failed action — removed it")
                    new_subseq = kept

                # Slice the tail by true plan position (failed_step.index
                # drifts when earlier steps were skipped)
                after_idx = (failed_plan_idx + 1) if failed_plan_idx is not None else t_end
                after_wrong = current_plan_eai[after_idx:]

                if new_subseq:
                    current_plan_eai = history_actions + new_subseq + after_wrong
                    raw_output = plan_to_json_str(current_plan_eai)
                    logger.info(f"  🔄 Replaced with: {wrong_raw}")
                else:
                    fallback_count += 1
                    fallback_raw = self.llm.call(base_prompt)
                    new_subseq = parse_and_validate(fallback_raw, relevant_name_to_id, goal_edge_relations,
                                                    char_guard="strip")
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
            replan_count += 1
            suggestion_raw = self.llm.call(suggestion_prompt, system_prompt=SYSTEM_PROMPT, label=f"SUGGESTION (replan {replan_count})")
            llm_suggestions = parse_llm_output(suggestion_raw)
            llm_suggestions = filter_valid_actions(llm_suggestions) if llm_suggestions else []
            if isinstance(llm_suggestions, dict):
                llm_suggestions = [{k: v} for k, v in llm_suggestions.items()]

            logger.info(f"  💡 LLM suggestions: {suggestion_raw}")

            # ── Step 2: BFS search tree ───────────────────────────────────────
            tstart_hist_idx = t_start - 1
            state_at_tstart = (
                history_env_states[tstart_hist_idx]
                if tstart_hist_idx < len(history_env_states)
                else env_at_failure
            )

            orig_subseq_dicts = []
            for s in orig_subseq:
                if hasattr(s, "target") and s.target:
                    orig_subseq_dicts.append({s.action: [s.obj, s.target]})
                elif hasattr(s, "obj"):
                    orig_subseq_dicts.append({s.action: [s.obj]})

            _repair_kwargs = {
                "llm_suggestions": llm_suggestions,
                "original_subsequence": orig_subseq_dicts,
                "initial_state_dict": state_at_tstart,
                "unsatisfied_needs": diagnosis.unsatisfied_needs,
                "error_objects": error_objects,
                "char_sitting": char_sitting,
                "char_lying": char_lying,
                "max_depth": TREE_MAX_DEPTH,
                "max_nodes": TREE_MAX_NODES,
                "failed_obj": failed_step.obj,
                "failed_target": failed_step.target,
                "banned_paths": tried_repairs.get(failure_sig, set()),
                "banned_candidates": banned_cands.get(failure_sig, set()),
                "goal_edge_relations": goal_edge_relations,
            }
            tree_result = generate_replacement_subsequence(
                prefer_goal_placement=True, **_repair_kwargs
            )
            if not tree_result:
                # Goal-aware placement wasn't reachable (e.g. destination
                # container not open right now) — fall back to the plain
                # DROP behavior so this can only ever help, never regress.
                tree_result = generate_replacement_subsequence(
                    prefer_goal_placement=False, **_repair_kwargs
                )

            if tree_result:
                logger.info(f"  🌳 Tree found: {tree_result}")
                if VERBOSE:
                    print(f"\n  TREE SEARCH RESULT: {tree_result}")
                tree_success += 1
                # Record BEFORE resolution: if resolution fails, the next
                # attempt must get a different path, not this one again.
                tried_repairs.setdefault(failure_sig, set()).add(_repair_key(tree_result))
                new_subseq = subtree_results_to_eai(
                    tree_result, relevant_name_to_id, full_name_to_id,
                    goal_edge_relations,
                )
            else:
                # Tree exhausted: every viable repair was already tried (or
                # none exists). The plan cannot change, so the next attempt
                # would replay the identical failure — drop the action.
                idx = failed_plan_idx if failed_plan_idx is not None else failed_step.index - 1
                dropped = current_plan_eai[idx]
                current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
                raw_output = plan_to_json_str(current_plan_eai)
                last_spliced = None
                if goal_state_action_pair(dropped, goal_state_pairs):
                    deferred_goal_actions.append(dropped)
                logger.info(f"  🌳 Tree exhausted — dropped failed action: {dropped}")
                if VERBOSE:
                    print(f"\n  TREE EXHAUSTED — dropping action: {dropped}")
                continue

            if not new_subseq:
                continue

            # ── Splice replacement into plan ──────────────────────────────────
            # Retain the failed action(s) after the repair sequence so that
            # prep-style fixes (WALK, OPEN, etc.) are followed by a retry of
            # the original action rather than silently dropping it.
            # win_start_plan is t_start converted to plan coordinates.
            failed_eai = current_plan_eai[win_start_plan - 1 : t_end]
            if root_cause_plan_pos is not None:
                exclude_offset = root_cause_plan_pos - win_start_plan
                if 0 <= exclude_offset < len(failed_eai):
                    logger.info(
                        f"  🚫 Excluding diagnosed root cause from retry: "
                        f"{failed_eai[exclude_offset]}"
                    )
                    failed_eai = failed_eai[:exclude_offset] + failed_eai[exclude_offset + 1:]

            # If the fix ends by GRABbing object X, and the retained retry
            # tail starts by WALKing to that SAME X, that WALK is provably
            # redundant (already holding X) and actively harmful — it
            # relocates the character away from wherever the fix just left
            # them, usually exactly where the NEXT action (a PUTBACK/PUTIN
            # into a container) needs them to be. Confirmed costing a
            # whole wasted replan cycle on task 327_2 (GRAB plate -> the
            # retained WALK plate -> PUTBACK then fails next_to_target,
            # needing a SEPARATE insert_prep fix) — and the identical
            # pattern recurring on dish_soap right after exhausted the
            # entire replan budget with no cycles left to recover.
            def _edge_action_obj(seq, take_last):
                if not seq:
                    return None, None
                s = str(seq[-1] if take_last else seq[0])
                m = re.match(r"^\[(\w+)\]\s*<([^>]+)>\s*\((\d+)\)", s)
                return (m.group(1).upper(), f"{m.group(2)}_{m.group(3)}") if m else (None, None)

            last_act, last_obj = _edge_action_obj(new_subseq, take_last=True)
            first_act, first_obj = _edge_action_obj(failed_eai, take_last=False)
            if last_act == "GRAB" and first_act == "WALK" and last_obj == first_obj:
                logger.info(f"  🚫 Dropping redundant WALK to already-held object: {failed_eai[0]}")
                failed_eai = failed_eai[1:]

            # If the goal-aware hand-freeing repair (action_subtree.py,
            # prefer_goal_placement) just PUTBACK/PUTIN'd a held object at
            # its own destination, the ORIGINAL plan's later PUTBACK/PUTIN
            # of that SAME object is now redundant — the object was
            # released there, so retrying it would fail on holds_obj and
            # burn a whole replan cycle re-fetching something already
            # correctly placed. Strip it from both retained segments.
            def _placement_obj_id(action_str):
                m = re.match(r"^\[(PUTBACK|PUTIN)\]\s*<[^>]+>\s*\((\d+)\)\s*<[^>]+>\s*\((\d+)\)", action_str)
                return m.group(2) if m else None

            newly_placed = {oid for oid in (_placement_obj_id(str(s)) for s in new_subseq) if oid}

            def _strip_placed(seq, placed_ids):
                kept = []
                for s in seq:
                    oid = _placement_obj_id(str(s))
                    if oid and oid in placed_ids:
                        logger.info(f"  🚫 Dropping redundant later placement of already-placed object: {s}")
                        continue
                    kept.append(s)
                return kept

            if newly_placed:
                failed_eai = _strip_placed(failed_eai, newly_placed)
                after = _strip_placed(after, newly_placed)

            current_plan_eai = before + new_subseq + failed_eai + after
            raw_output = plan_to_json_str(current_plan_eai)
            # Remember what this repair was for, so if one of ITS actions
            # fails next attempt, that candidate gets banned for this
            # signature (alternation guard above).
            last_spliced = (failure_sig, {str(a) for a in new_subseq})
            logger.info(
                f"  Spliced: {len(before)} + {len(new_subseq)} + "
                f"{len(failed_eai)} (retry) + {len(after)} = {len(current_plan_eai)}"
            )

        return raw_output, replan_count, tree_success, fallback_count

    def _save(self, outputs: list):
        path = osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")
        with open(path, "w") as f:
            json.dump(outputs, f, indent=4)
        logger.info(f"Saved {len(outputs)} outputs → {path}")


class NoAdaptRunner(EAISDATreeRunner):
    """The paper's "w/o adaptation" ablation arm (Fig. 4): SAME initial-plan
    generation as EAISDATreeRunner (identical prompt, parsing, goal-relation
    correction, one parse retry) — the two arms differ ONLY in what happens
    after a failure. Here, a failed action is SKIPPED and execution
    continues; there is no error diagnosis, no search tree, and no repair
    call, so the LLM is never informed that anything failed. This isolates
    the SDA feedback machinery as the sole variable between the two arms,
    making the task-success-rate delta between them a direct measurement of
    what that machinery contributes.

    Task-agnostic: works against whatever TASK_DICT_PATH/ID2TASK_PATH/
    DATA_DIR are set to when instantiated — the full EAI set by default, or
    a connector's overrides (e.g. the Hard-50 resources) if applied first.

    Saves the subsequence of actions that actually executed (the paper's
    definition), so post-failure goals can still be credited — the choice
    most favorable to this baseline, which makes the measured SDA delta
    conservative rather than inflated.
    """

    def run_single_task(self, file_id, task_name, task_goal_dict):
        goals = task_goal_dict["vh_goal"]
        node_goals = [g for g in goals["goal"] if "id" in g and "state" in g]
        edge_goals = [g for g in goals["goal"] if "from_id" in g and "relation_type" in g]
        goal_edge_relations = {
            (g["from_id"], g["to_id"]): g["relation_type"] for g in edge_goals
        }

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

        if VERBOSE:
            print(f"\n{'='*60}", flush=True)
            print(f"TASK: {file_id}  |  {task_name}  [NO-ADAPTATION]", flush=True)
            print(f"{'='*60}", flush=True)

        raw_output = self.llm.call(base_prompt, label="INITIAL PLAN")
        logger.info(f"  Initial plan: {raw_output}")

        actions = parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                     char_guard="reject")
        if not actions:
            logger.warning(f"  Could not parse initial plan for {file_id} — retrying once")
            retry_prompt = _build_retry_prompt(base_prompt, raw_output)
            raw_output = self.llm.call(retry_prompt, label="INITIAL PLAN (retry)")
            actions = parse_and_validate(raw_output, relevant_name_to_id, goal_edge_relations,
                                         char_guard="strip")
        if not actions:
            logger.warning(f"  Could not parse initial plan for {file_id}")
            return raw_output, 0, 0, 0

        # ── Single pass: skip-and-continue, zero feedback ─────────────────────
        motion_planner.reset()
        executed, skipped = [], []
        if VERBOSE:
            print(f"\n  {'─'*50}")
            print(f"  EXECUTING (no adaptation) — {len(actions)} actions")
            print(f"  {'─'*50}")
        for i, action in enumerate(actions):
            exe_flag, _ = motion_planner.my_execute_primitive_action_eval(action)
            if VERBOSE:
                print(f"  [{i+1:02d}] {action}  →  {'OK' if exe_flag else 'SKIPPED (failed)'}", flush=True)
            (executed if exe_flag else skipped).append(action)

        raw_output = plan_to_json_str(executed)
        logger.info(
            f"  no-adapt result: {len(executed)} executed, {len(skipped)} skipped"
            + (f" | skipped: {[str(a) for a in skipped]}" if skipped else "")
        )
        if VERBOSE:
            print(f"\n  FINAL OUTPUT SAVED ({len(executed)} executed / {len(skipped)} skipped)", flush=True)
        return raw_output, 0, 0, 0


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
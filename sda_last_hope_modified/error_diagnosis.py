"""
Error Backtrack and Diagnosis Module
Based on SDA-Planner paper Section 4.3

Algorithm:
1. Identify error type from EAI checker
2. Replay executed actions to track per-object state (via ObjectStateModel)
3. Check which preconditions of failed action are unsatisfied — for the
   specific obj/target involved, not globally
4. Find t_source: most recent step that violated the key precondition (Eq. 2)
5. Calculate reconstruction window [t_start, t_end] (Eq. 4)
6. Return DiagnosisResult with replan strategy

Strategies:
  - "local"       : Unsat=[] or AFFORDANCE_ERROR → generate additional steps
  - "insert_prep" : single prep action needed (STANDUP / WALK)
  - "reconstruct" : full window reconstruction using search tree
"""

import re

from object_state_model import ObjectStateModel
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# States that change dynamically during execution
DYNAMIC_PRECONDITIONS = {
    "holds_obj", "not_both_hands_full", "holding_anything",
    "not_sitting", "not_lying",
    "open", "closed", "on", "off", "next_to_obj", "next_to_target",
    "sitting_or_lying", "obj_not_inside_closed_container",
    "target_open_or_not_openable", "plugged_in", "plugged_out",
    "facing_obj",
}

# Static object properties — checked against the scene graph per object
STATIC_PROPERTIES = {
    "grabbable", "has_switch", "can_open", "has_plug",
    "eatable", "readable", "movable", "lookable",
    "sittable", "lieable", "clothes", "cuttable",
    "hangable", "pourable", "drinkable",
}


class ActionStep:
    """Represents a single action in the plan."""

    def __init__(self, index: int, action: str, obj: str, target: str = None):
        self.index  = index
        self.action = action.upper()
        self.obj    = obj
        self.target = target

    def __repr__(self):
        if self.target:
            return f"[t={self.index}] {self.action}({self.obj}, {self.target})"
        return f"[t={self.index}] {self.action}({self.obj})"


class DiagnosisResult:
    """Structured result from error diagnosis.

    Coordinate convention (they differ once steps have been skipped):
      t_start — 1-based index into the SUCCESSFUL-action history; used by the
                runner to slice history_actions / history_env_states.
      t_end   — 1-based PLAN position (index into the full current plan) when
                diagnose_error was given failed_plan_pos; used by the runner
                to slice the plan tail. Falls back to history coordinates
                when failed_plan_pos is absent (identical when nothing was
                skipped).
    """

    def __init__(self):
        self.error_type        = None
        self.failed_action     = None
        self.failed_at         = None
        self.root_cause        = None
        self.root_cause_at     = None
        self.unsatisfied_needs = []
        self.t_start           = None
        self.t_end             = None
        self.replan_strategy   = None  # "insert_prep" | "local" | "reconstruct"

    def __repr__(self):
        return (
            f"Error={self.error_type} | Failed={self.failed_action} | "
            f"Root={self.root_cause} | Window=[{self.t_start},{self.t_end}] | "
            f"Unsat={self.unsatisfied_needs}"
        )


class StateTracker:
    """
    Per-object state tracker wrapping ObjectStateModel.

    self.model is built from the environment AT FAILURE — the simulator's
    ground truth — and is NOT mutated by history. (The old design replayed
    history on top of the at-failure env, double-applying every action:
    a replayed GRAB of an already-held object filled BOTH hands with it.)
    History is recorded separately so find_t_source can replay it from the
    INITIAL environment snapshot.
    """

    def __init__(
        self,
        char_sitting: bool = False,
        char_lying:   bool = False,
        env_dict:     dict = None,   # environment AT FAILURE (ground truth)
        initial_env_dict: dict = None,  # environment BEFORE any action (for replay)
    ):
        self.model   = ObjectStateModel.from_env_dict(
            env_dict or {},
            char_sitting = char_sitting,
            char_lying   = char_lying,
        )
        self._initial_env = initial_env_dict
        self.history: list = []   # list of ActionStep in execution order

    def record_action(self, step: ActionStep):
        """Record an executed action for later replay (no model mutation)."""
        self.history.append(step)

    # Backward-compatible alias
    apply_action = record_action

    def is_satisfied(self, precondition: str, obj: str,
                     target: str = None) -> bool:
        """Check a single precondition for the given obj/target."""
        return self.model.satisfies(precondition, obj, target)

    def get_unsatisfied(self, preconditions: list, obj: str,
                        target: str = None) -> list:
        """Return list of preconditions not currently satisfied for obj/target."""
        return self.model.check_all(preconditions, obj, target)

    def find_t_source(self, precondition: str, obj: str,
                      t_error: int, target: str = None) -> int:
        """
        Find the most recent timestep before t_error where the precondition
        transitioned from satisfied to unsatisfied FOR THE SPECIFIC obj/target.
        Paper Eq. 2.

        The replay model is seeded from the INITIAL environment snapshot when
        available — a blank model made object-state preconditions (off/open/
        plugged_in) unsatisfiable from the start, so they could never flip and
        t_source silently degenerated to t_error.

        target matters for target-preconditions (next_to_target,
        target_open_or_not_openable): satisfies() checks them against the
        target argument, so omitting it made them never flip either.
        """
        if self._initial_env is not None:
            replay = ObjectStateModel.from_env_dict(self._initial_env)
        else:
            # Fallback (no snapshot): blank model + posture re-derivation
            replay = ObjectStateModel()
            sitting = self.model.char_sitting
            lying   = self.model.char_lying
            for step in reversed(self.history):
                if step.action == "SIT":
                    sitting = False
                elif step.action == "LIE":
                    lying = False
                elif step.action in ("STANDUP", "WAKEUP"):
                    sitting = True   # could have been either; safe default
            replay.char_sitting = sitting
            replay.char_lying   = lying

        # FIX 2: None sentinel — if the precondition was NEVER satisfied
        # (e.g. dish_soap always inside a closed cabinet), return t_error so
        # reconstruction starts at the failed step instead of discarding the
        # whole successful prefix.
        last_violated_at = None
        prev_ok = replay.satisfies(precondition, obj, target)

        for step in self.history:
            if step.index >= t_error:
                break
            replay.apply(step.action, step.obj, step.target)
            now_ok = replay.satisfies(precondition, obj, target)
            if prev_ok and not now_ok:
                last_violated_at = step.index
            prev_ok = now_ok

        if last_violated_at is None:
            return t_error
        return last_violated_at


def _find_container_in_env(obj_name: str, env_dict: dict):
    """
    Given an object ("name_id" token or plain class name) and env dict,
    return the container it is INSIDE as a "class_name_id" token, or None.

    Used by error_diagnosis_tree.py to add the real container name to
    error_objects so the tree and LLM receive a concrete object to open
    rather than guessing a generic "container".
    """
    if not env_dict:
        return None
    nodes = env_dict.get("nodes", [])
    edges = env_dict.get("edges", [])

    # "dish_soap_78" -> match node id 78; plain "dish_soap" -> match by class
    m = re.match(r"^(.+)_(\d+)$", str(obj_name).strip())
    if m:
        obj_id = int(m.group(2))
        if not any(n.get("id") == obj_id for n in nodes):
            obj_id = None
            obj_name = m.group(1)
    else:
        obj_id = None
    if obj_id is None:
        obj_id = next(
            (n["id"] for n in nodes if n.get("class_name") == obj_name),
            None,
        )
    if obj_id is None:
        return None
    # An object can have multiple INSIDE edges (real container AND the room
    # it is in, e.g. jacket INSIDE washing_machine + INSIDE dining_room).
    # Only an actual openable container is useful to the repair machinery —
    # rooms lack CAN_OPEN/CONTAINERS and must never be returned.
    for edge in edges:
        if (edge.get("relation_type") == "INSIDE"
                and edge.get("from_id") == obj_id):
            container_id = edge.get("to_id")
            node = next((n for n in nodes if n["id"] == container_id), None)
            if node is None:
                continue
            props = {str(p).upper() for p in node.get("properties", [])}
            if "CAN_OPEN" in props or "CONTAINERS" in props:
                return f"{node['class_name']}_{container_id}"
    return None


def diagnose_error(
    action_history: list,
    failed_step:    ActionStep,
    error_type:     str,
    full_plan:      list,
    char_sitting:   bool = False,
    char_lying:     bool = False,
    env_dict:       dict = None,        # environment AT FAILURE (ground truth)
    initial_env_dict: dict = None,      # environment BEFORE any action (Eq. 2 replay)
    failed_plan_pos: int = None,        # 1-based PLAN position of the failed action
) -> DiagnosisResult:
    """
    Main diagnosis function implementing SDA-Planner paper Section 4.3.

    Returns DiagnosisResult with:
      - replan_strategy: "local" | "insert_prep" | "reconstruct"
      - t_start, t_end: reconstruction window (see DiagnosisResult for the
        t_start-history / t_end-plan coordinate convention)
      - unsatisfied_needs: list of violated preconditions for the specific
        failed obj/target (not a global flat check)

    failed_plan_pos anchors t_end in plan coordinates. failed_step.index
    counts only SUCCESSFUL steps, so after skipped steps it lags the true
    plan position and the t_end scan over full_plan (plan-indexed) compared
    apples to oranges. When omitted, falls back to failed_step.index
    (identical when nothing was skipped).
    """

    result               = DiagnosisResult()
    result.error_type    = error_type
    result.failed_action = failed_step
    result.failed_at     = failed_step.index

    # t_end anchor in plan coordinates (see docstring)
    plan_anchor = failed_plan_pos if failed_plan_pos is not None else failed_step.index

    # ── ADDITIONAL_STEP: skip action, local replan ────────────────────────────
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy   = "local"
        result.root_cause        = failed_step
        result.root_cause_at     = failed_step.index
        result.t_start           = failed_step.index
        result.t_end             = plan_anchor
        result.unsatisfied_needs = []
        return result

    # ── Current state = the AT-FAILURE env (ground truth, no replay);
    #    history is only recorded for find_t_source's from-initial replay ────
    tracker = StateTracker(
        char_sitting = char_sitting,
        char_lying   = char_lying,
        env_dict     = env_dict,
        initial_env_dict = initial_env_dict,
    )
    for step in action_history:
        tracker.record_action(step)

    # ── Find unsatisfied preconditions for the specific obj/target ────────────
    preconditions = get_preconditions(failed_step.action)
    unsatisfied   = tracker.get_unsatisfied(
        preconditions,
        failed_step.obj,
        failed_step.target,
    )

    # Blind-spot fix (417_1): PUTBACK/PUTIN declare only holds_obj, so when
    # the object is unheld BECAUSE it sits inside a closed container, the
    # container need never surfaces — the repair tree then gets no
    # WALK/OPEN-container candidates and exhausts. Surface the root blocker
    # explicitly so the container machinery (FIX 3, guaranteed candidates,
    # SWITCHOFF) kicks in.
    if ("holds_obj" in unsatisfied
            and "obj_not_inside_closed_container" not in unsatisfied
            and not tracker.model.satisfies(
                "obj_not_inside_closed_container", failed_step.obj)):
        unsatisfied.append("obj_not_inside_closed_container")

    result.unsatisfied_needs = unsatisfied

    # ── ALREADY SATISFIED: the action's positive effects are already true ─────
    # Checked BEFORE the AFFORDANCE_ERROR branch: e.g. PLUGIN on a device
    # that is already PLUGGED_IN surfaces as AFFORDANCE_ERROR, but the right
    # move is to drop the action — replanning around it just inserts useless
    # WALKs until the replan budget is exhausted (observed on 384_1 / 71_2).
    # e.g. SWITCHON <light> fails because light is already ON (off unsatisfied)
    #      OPEN <fridge> fails because fridge is already OPEN
    #      SWITCHOFF <light> fails because light is already OFF
    # In these cases the goal state is already achieved — remove the action
    # from the plan rather than trying to replan around it.
    #
    # Only effects the state model can actually verify may vote here.
    # satisfies() returns True for UNKNOWN predicates (safe default for BFS),
    # so effects like obj_ontop_target / obj_inside_target / on_char / sitting
    # would otherwise make EVERY failed PUTBACK/PUTIN/PUTON/SIT look
    # "already satisfied" and get the goal-placing action silently deleted.
    VERIFIABLE_EFFECTS = {
        "open", "closed", "on", "off",
        "plugged_in", "plugged_out",
        "holds_obj", "next_to_obj", "facing_obj",
        "on_char",   # worn-items tracking in ObjectStateModel
    }
    positive_effects = [e for e in get_effects(failed_step.action)
                        if not e.startswith("not_")]
    if positive_effects and all(e in VERIFIABLE_EFFECTS for e in positive_effects):
        all_already_true = all(
            tracker.model.satisfies(e, failed_step.obj, failed_step.target)
            for e in positive_effects
        )
        if all_already_true:
            result.replan_strategy   = "already_satisfied"
            result.root_cause        = failed_step
            result.root_cause_at     = failed_step.index
            result.t_start           = failed_step.index
            result.t_end             = plan_anchor
            result.unsatisfied_needs = []
            return result

    # ── AFFORDANCE_ERROR: object property mismatch → local replan ────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = plan_anchor
        return result

    # ── WRONG ACTION: action is semantically wrong for this object ────────────
    # Detected when holds_obj is unsatisfied but the obj is not grabbable
    # (e.g. PUTON <washing_machine> — washing_machine can't be held).
    # No amount of precondition fixing will help; the action itself must be
    # replaced. Signal this with replan_strategy="wrong_action" so the runner
    # can ask the LLM to replace the whole action rather than patch it.
    if "holds_obj" in unsatisfied:
        obj_is_grabbable = tracker.model.satisfies("grabbable",
                                                    failed_step.obj,
                                                    failed_step.target)
        if not obj_is_grabbable:
            result.replan_strategy   = "wrong_action"
            result.root_cause        = failed_step
            result.root_cause_at     = failed_step.index
            result.t_start           = failed_step.index
            result.t_end             = plan_anchor
            result.unsatisfied_needs = []   # not a precondition problem
            return result

    # ── PUTOBJBACK: hidden executor preconditions the SDG cannot model ───────
    # The executor needs a recorded original placement for the object;
    # holds_obj alone can be satisfied while the action still fails, giving
    # Unsat=[] and an endless local-replan loop (7 wasted replans in the
    # hard-50 run). Route to wrong_action so the LLM substitutes an explicit
    # PUTBACK/PUTIN.
    if failed_step.action == "PUTOBJBACK" and not unsatisfied:
        result.replan_strategy   = "wrong_action"
        result.root_cause        = failed_step
        result.root_cause_at     = failed_step.index
        result.t_start           = failed_step.index
        result.t_end             = plan_anchor
        result.unsatisfied_needs = []
        return result

    # ── No unsatisfied preconditions → env state mismatch → local ────────────
    if not unsatisfied:
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = plan_anchor
        return result

    # ── Select key precondition (prefer dynamic over static) ─────────────────
    dynamic_unsat = [p for p in unsatisfied if p in DYNAMIC_PRECONDITIONS]
    key_prec      = dynamic_unsat[0] if dynamic_unsat else unsatisfied[0]

    # ── Simple prep action insertion ──────────────────────────────────────────
    # Only one dynamic precondition AND it is fixable by a single prep action.
    simple_prep = {
        "not_sitting":    "STANDUP",
        "not_lying":      "STANDUP",
        "next_to_obj":    "WALK",
        "next_to_target": "WALK",   # PUTBACK/PUTIN fail → just WALK to target
        "facing_obj":     "TURNTO", # WATCH/LOOKAT fail → just TURNTO first
    }
    if key_prec in simple_prep and len(dynamic_unsat) <= 1:
        result.replan_strategy = "insert_prep"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = plan_anchor
        return result

    # ── Full reconstruction ───────────────────────────────────────────────────
    result.replan_strategy = "reconstruct"

    # Find t_source: most recent step that corrupted key_prec for the
    # specific obj/target involved (paper Eq. 2)
    t_source             = tracker.find_t_source(
        key_prec, failed_step.obj, failed_step.index, failed_step.target
    )
    result.root_cause_at = t_source
    result.root_cause    = next(
        (s for s in action_history if s.index == t_source), failed_step
    )

    # Calculate t_start: extend backward past consecutive prep actions (Eq. 4)
    t_start = t_source
    for step in reversed(action_history):
        if step.index >= t_source:
            continue
        if is_prep_action(step.action):
            t_start = step.index
        else:
            break
    result.t_start = t_start

    # Calculate t_end: extend forward past all actions on error objects (Eq. 4).
    # full_plan steps are PLAN-indexed, so the anchor must be too — comparing
    # against the history-indexed failed_step.index undercounted the window
    # whenever steps had been skipped earlier in the attempt.
    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)

    # Eq. 4 quantifies UNIVERSALLY over the range:
    #     t_end = max{t | {o_i | A_i=(a_i,o_i), for all i in (t_error, t]} subset-of O}
    # i.e. EVERY action strictly after the failure and up to t_end must touch an
    # error item, so the window stops at the FIRST later action that touches
    # something else. Without the break this computed max{t | o_t in O} instead,
    # which let a single mention of an error object near the end of a long plan
    # drag the whole tail — and every unrelated action in between — into the
    # reconstruction window, the candidate set and the retained retry tail.
    #
    # Note: the paper's A_i carries ONE object; VirtualHome actions carry up to
    # two. "Touches an error item" is kept as the disjunctive test over
    # (obj, target) that this function already used, so only the missing
    # quantifier is being corrected here, not the per-step predicate.
    t_end = plan_anchor
    for step in full_plan:
        if step.index <= plan_anchor:
            continue
        if not (step.obj in error_objects or
                (step.target and step.target in error_objects)):
            break
        t_end = step.index
    result.t_end = t_end

    return result


if __name__ == "__main__":
    # ── Test 1: both hands full ──────────────────────────────────────────────
    print("Test 1 — both hands full:")
    history = [
        ActionStep(1, "WALK", "lamp"),
        ActionStep(2, "FIND", "pan"),
        ActionStep(3, "GRAB", "pan"),
        ActionStep(4, "FIND", "tomato"),
        ActionStep(5, "WALK", "tomato"),
        ActionStep(6, "GRAB", "box"),   # fills both hands
    ]
    failed = ActionStep(7, "GRAB", "tomato")
    plan   = history + [failed, ActionStep(8, "PUTBACK", "tomato", "pan")]

    # env_dict is the AT-FAILURE environment (ground truth): the character
    # already holds pan and box, and stands next to the tomato.
    env = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "pan",    "states": [], "properties": ["GRABBABLE"]},
            {"id": 3, "class_name": "tomato",  "states": [], "properties": ["GRABBABLE", "EATABLE"]},
            {"id": 4, "class_name": "box",     "states": [], "properties": ["GRABBABLE"]},
            {"id": 5, "class_name": "lamp",    "states": [], "properties": []},
        ],
        "edges": [
            {"from_id": 1, "to_id": 2, "relation_type": "HOLDS_RH"},
            {"from_id": 1, "to_id": 4, "relation_type": "HOLDS_LH"},
            {"from_id": 1, "to_id": 3, "relation_type": "CLOSE"},
        ],
    }

    result = diagnose_error(history, failed, "MISSING_STEP", plan, env_dict=env)
    print(result)
    assert "not_both_hands_full" in result.unsatisfied_needs, \
        f"Expected not_both_hands_full, got {result.unsatisfied_needs}"
    print("✅ Test 1 passed\n")

    # ── Test 2: apple inside CLOSED fridge ───────────────────────────────────
    print("Test 2 — GRAB apple inside closed fridge:")
    env2 = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "fridge",
             "states": ["CLOSED"],
             "properties": ["CAN_OPEN"]},
            {"id": 3, "class_name": "apple",
             "states": [],
             "properties": ["GRABBABLE", "EATABLE"]},
        ],
        "edges": [
            {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
            {"from_id": 1, "to_id": 3, "relation_type": "CLOSE"},
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
        ],
    }
    failed2 = ActionStep(1, "GRAB", "apple")
    result2 = diagnose_error([], failed2, "MISSING_STEP", [failed2], env_dict=env2)
    print(result2)
    assert "obj_not_inside_closed_container" in result2.unsatisfied_needs, \
        f"Expected obj_not_inside_closed_container, got {result2.unsatisfied_needs}"
    print("✅ Test 2 passed\n")

    # ── Test 3: OPEN with both hands full (executor rule, not in PDDL) ───────
    print("Test 3 — OPEN cupboard with both hands full:")
    env3 = {
        "nodes": [
            {"id": 65,   "class_name": "character",    "states": [], "properties": []},
            {"id": 229,  "class_name": "cupboard",     "states": ["CLOSED"],
             "properties": ["CAN_OPEN"]},
            {"id": 1001, "class_name": "cloth_napkin", "states": [],
             "properties": ["GRABBABLE"]},
            {"id": 1005, "class_name": "cup",          "states": [],
             "properties": ["GRABBABLE"]},
        ],
        "edges": [
            {"from_id": 65, "to_id": 1001, "relation_type": "HOLDS_RH"},
            {"from_id": 65, "to_id": 1005, "relation_type": "HOLDS_LH"},
            {"from_id": 65, "to_id": 229,  "relation_type": "CLOSE"},
        ],
    }
    history3 = [ActionStep(1, "WALK", "cupboard_229")]
    failed3  = ActionStep(2, "OPEN", "cupboard_229")
    result3  = diagnose_error(history3, failed3, "MISSING_STEP",
                              history3 + [failed3], env_dict=env3,
                              initial_env_dict=env3)
    print(result3)
    assert "not_both_hands_full" in result3.unsatisfied_needs, \
        f"Expected not_both_hands_full, got {result3.unsatisfied_needs}"
    assert result3.replan_strategy == "reconstruct", \
        f"Expected reconstruct, got {result3.replan_strategy}"
    print("✅ Test 3 passed\n")

    # ── Test 4: WIPE with empty hands must NOT misroute to wrong_action ──────
    print("Test 4 — WIPE table with empty hands:")
    env4 = {
        "nodes": [
            {"id": 65,  "class_name": "character", "states": [], "properties": []},
            {"id": 355, "class_name": "table",     "states": [],
             "properties": ["SURFACES"]},
        ],
        "edges": [
            {"from_id": 65, "to_id": 355, "relation_type": "CLOSE"},
        ],
    }
    failed4 = ActionStep(1, "WIPE", "table_355")
    result4 = diagnose_error([], failed4, "MISSING_STEP", [failed4],
                             env_dict=env4, initial_env_dict=env4)
    print(result4)
    assert result4.replan_strategy != "wrong_action", \
        f"WIPE misrouted to wrong_action: {result4}"
    assert "holding_anything" in result4.unsatisfied_needs, \
        f"Expected holding_anything, got {result4.unsatisfied_needs}"
    print("✅ Test 4 passed\n")

    # ── Test 5: PUTOBJBACK failing with satisfied SDG needs → wrong_action ───
    print("Test 5 — PUTOBJBACK with hidden executor precondition:")
    env5 = {
        "nodes": [
            {"id": 65,   "class_name": "character",   "states": [], "properties": []},
            {"id": 1000, "class_name": "water_glass", "states": [],
             "properties": ["GRABBABLE"]},
        ],
        "edges": [
            {"from_id": 65, "to_id": 1000, "relation_type": "HOLDS_RH"},
        ],
    }
    failed5 = ActionStep(1, "PUTOBJBACK", "water_glass_1000")
    result5 = diagnose_error([], failed5, "MISSING_STEP", [failed5],
                             env_dict=env5, initial_env_dict=env5)
    print(result5)
    assert result5.replan_strategy == "wrong_action", \
        f"Expected wrong_action, got {result5.replan_strategy}"
    print("✅ Test 5 passed\n")

    print("All tests passed ✅")
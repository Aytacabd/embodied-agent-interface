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

from object_state_model import ObjectStateModel
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# States that change dynamically during execution
DYNAMIC_PRECONDITIONS = {
    "holds_obj", "not_both_hands_full", "not_sitting", "not_lying",
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
    """Structured result from error diagnosis."""

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
    Records action history so find_t_source can replay precisely.
    """

    def __init__(
        self,
        char_sitting: bool = False,
        char_lying:   bool = False,
        env_dict:     dict = None,
    ):
        self.model   = ObjectStateModel.from_env_dict(
            env_dict or {},
            char_sitting = char_sitting,
            char_lying   = char_lying,
        )
        self.history: list = []   # list of ActionStep in execution order

    def apply_action(self, step: ActionStep):
        """Apply one action to the model and record it in history."""
        self.model.apply(step.action, step.obj, step.target)
        self.history.append(step)

    def is_satisfied(self, precondition: str, obj: str,
                     target: str = None) -> bool:
        """Check a single precondition for the given obj/target."""
        return self.model.satisfies(precondition, obj, target)

    def get_unsatisfied(self, preconditions: list, obj: str,
                        target: str = None) -> list:
        """Return list of preconditions not currently satisfied for obj/target."""
        return self.model.check_all(preconditions, obj, target)

    def find_t_source(self, precondition: str, obj: str,
                      t_error: int) -> int:
        """
        Find the most recent timestep before t_error where the precondition
        transitioned from satisfied to unsatisfied FOR THE SPECIFIC obj.
        Paper Eq. 2.

        Strategy: replay the full history from scratch using a fresh model
        copy, recording whenever the precondition flips from True → False.
        Returns the timestep of the last such flip, or 1 if never satisfied
        in the first place.
        """
        # Reconstruct initial state from env_dict stored in current model
        # We rebuild by replaying from a blank model that mirrors the
        # original env (we have the history, not the original env_dict here,
        # so we start from a model that has the same initial env as self.model
        # but with no actions applied yet).
        replay = ObjectStateModel()
        replay.char_sitting = False
        replay.char_lying   = False

        # Re-apply only env-derived states (not action history).
        # Since we don't re-store env_dict, we clone model state before
        # any history by replaying actions onto a blank copy and tracking flips.
        # Simpler and correct: snapshot the satisfaction at each step.

        # FIX 2: Use None sentinel instead of 1 as default.
        # If the precondition was NEVER satisfied in the history (e.g. dish_soap
        # was always inside a closed cabinet), the old code returned 1, causing
        # t_start=1 and before=[] which discarded all prior successful steps
        # (e.g. GRAB plate already in hand). Returning t_error instead means
        # t_start = failed_step.index and before preserves everything up to that
        # point — the fix only needs to be inserted RIGHT BEFORE the failed step.
        last_violated_at = None  # None = always broken from initial state

        # Start: check if precondition was satisfied BEFORE any action
        was_ok = self.model.satisfies(precondition, obj)

        # Replay by cloning the model without history then re-applying
        snapshot = self.model.copy()
        # Undo all history steps to get back to the initial state
        # (We can't undo, so instead we replay from scratch using history)
        # Build initial snapshot by importing env again — we use the
        # pattern of replaying on a temp model seeded from the first
        # env snapshot.  Since we don't store env_dict here we use
        # the history-replay approach:

        temp = ObjectStateModel()
        temp.char_sitting = self.model.char_sitting
        temp.char_lying   = self.model.char_lying
        # Copy initial object_states (before any action effects) by
        # reconstructing from history in reverse — instead just track
        # the satisfaction value at each point forward.

        # Simple and accurate: walk the history, apply each action, check flip
        temp2 = ObjectStateModel()
        # Seed posture from what we know of the original session start
        # (char_sitting / char_lying before any history action)
        # Re-derive by undoing posture actions in history:
        sitting = self.model.char_sitting
        lying   = self.model.char_lying
        for step in reversed(self.history):
            if step.action == "SIT":
                sitting = False
            elif step.action == "LIE":
                lying = False
            elif step.action in ("STANDUP", "WAKEUP"):
                sitting = True   # could have been either; safe default
        temp2.char_sitting = sitting
        temp2.char_lying   = lying

        prev_ok = temp2.satisfies(precondition, obj)

        for step in self.history:
            if step.index >= t_error:
                break
            temp2.apply(step.action, step.obj, step.target)
            now_ok = temp2.satisfies(precondition, obj)
            if prev_ok and not now_ok:
                last_violated_at = step.index
            prev_ok = now_ok

        # If the precondition was never satisfied from the start (no True→False
        # flip found), return t_error so reconstruction starts at the failed step.
        if last_violated_at is None:
            return t_error
        return last_violated_at


def _find_container_in_env(obj_name: str, env_dict: dict):
    """
    Given an object name and env dict, return the class_name of the container
    that obj_name is INSIDE, or None if not found.

    Used by error_diagnosis_tree.py to add the real container name to
    error_objects so the tree and LLM receive a concrete object to open
    rather than guessing a generic "container".
    """
    if not env_dict:
        return None
    nodes = env_dict.get("nodes", [])
    edges = env_dict.get("edges", [])
    obj_id = next(
        (n["id"] for n in nodes if n.get("class_name") == obj_name),
        None,
    )
    if obj_id is None:
        return None
    for edge in edges:
        if (edge.get("relation_type") == "INSIDE"
                and edge.get("from_id") == obj_id):
            container_id = edge.get("to_id")
            return next(
                (n["class_name"] for n in nodes if n["id"] == container_id),
                None,
            )
    return None


def diagnose_error(
    action_history: list,
    failed_step:    ActionStep,
    error_type:     str,
    full_plan:      list,
    char_sitting:   bool = False,
    char_lying:     bool = False,
    env_dict:       dict = None,
) -> DiagnosisResult:
    """
    Main diagnosis function implementing SDA-Planner paper Section 4.3.

    Returns DiagnosisResult with:
      - replan_strategy: "local" | "insert_prep" | "reconstruct"
      - t_start, t_end: reconstruction window (1-indexed)
      - unsatisfied_needs: list of violated preconditions for the specific
        failed obj/target (not a global flat check)
    """

    result               = DiagnosisResult()
    result.error_type    = error_type
    result.failed_action = failed_step
    result.failed_at     = failed_step.index

    # ── ADDITIONAL_STEP: skip action, local replan ────────────────────────────
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy   = "local"
        result.root_cause        = failed_step
        result.root_cause_at     = failed_step.index
        result.t_start           = failed_step.index
        result.t_end             = failed_step.index
        result.unsatisfied_needs = []
        return result

    # ── Replay history to build current per-object state ────────────────────
    tracker = StateTracker(
        char_sitting = char_sitting,
        char_lying   = char_lying,
        env_dict     = env_dict,
    )
    for step in action_history:
        tracker.apply_action(step)

    # ── Find unsatisfied preconditions for the specific obj/target ────────────
    preconditions = get_preconditions(failed_step.action)
    unsatisfied   = tracker.get_unsatisfied(
        preconditions,
        failed_step.obj,
        failed_step.target,
    )
    result.unsatisfied_needs = unsatisfied

    # ── AFFORDANCE_ERROR: object property mismatch → local replan ────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── ALREADY SATISFIED: the action's positive effects are already true ─────
    # e.g. SWITCHON <light> fails because light is already ON (off unsatisfied)
    #      OPEN <fridge> fails because fridge is already OPEN
    #      SWITCHOFF <light> fails because light is already OFF
    # In these cases the goal state is already achieved — remove the action
    # from the plan rather than trying to replan around it.
    positive_effects = [e for e in get_effects(failed_step.action)
                        if not e.startswith("not_")]
    if positive_effects:
        all_already_true = all(
            tracker.model.satisfies(e, failed_step.obj, failed_step.target)
            for e in positive_effects
        )
        if all_already_true:
            result.replan_strategy   = "already_satisfied"
            result.root_cause        = failed_step
            result.root_cause_at     = failed_step.index
            result.t_start           = failed_step.index
            result.t_end             = failed_step.index
            result.unsatisfied_needs = []
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
            result.t_end             = failed_step.index
            result.unsatisfied_needs = []   # not a precondition problem
            return result

    # ── No unsatisfied preconditions → env state mismatch → local ────────────
    if not unsatisfied:
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
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
    }
    if key_prec in simple_prep and len(dynamic_unsat) <= 1:
        result.replan_strategy = "insert_prep"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Full reconstruction ───────────────────────────────────────────────────
    result.replan_strategy = "reconstruct"

    # Find t_source: most recent step that corrupted key_prec for the
    # specific obj involved (paper Eq. 2)
    t_source             = tracker.find_t_source(
        key_prec, failed_step.obj, failed_step.index
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

    # Calculate t_end: extend forward past all actions on error objects (Eq. 4)
    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)

    t_end = failed_step.index
    for step in full_plan:
        if step.index > failed_step.index:
            if (step.obj in error_objects or
                    (step.target and step.target in error_objects)):
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

    env = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "pan",    "states": [], "properties": ["GRABBABLE"]},
            {"id": 3, "class_name": "tomato",  "states": [], "properties": ["GRABBABLE", "EATABLE"]},
            {"id": 4, "class_name": "box",     "states": [], "properties": ["GRABBABLE"]},
            {"id": 5, "class_name": "lamp",    "states": [], "properties": []},
        ],
        "edges": [],
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

    print("All tests passed ✅")
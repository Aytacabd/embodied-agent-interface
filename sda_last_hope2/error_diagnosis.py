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


# class ActionStep:
#     """Represents a single action in the plan."""

#     def __init__(self, index: int, action: str, obj: str, target: str = None):
#         self.index  = index
#         self.action = action.upper()
#         self.obj    = obj
#         self.target = target

#     def __repr__(self):
#         if self.target:
#             return f"[t={self.index}] {self.action}({self.obj}, {self.target})"
#         return f"[t={self.index}] {self.action}({self.obj})"
class ActionStep:
    """Represents a single action in the plan, with object IDs."""
    def __init__(self, index: int, action: str, obj_id: int = None, target_id: int = None,
                 obj_name: str = None, target_name: str = None):
        self.index = index
        self.action = action.upper()
        self.obj_id = obj_id
        self.target_id = target_id
        self.obj_name = obj_name      # optional, for debugging
        self.target_name = target_name

    def __repr__(self):
        if self.target_id is not None:
            return f"[t={self.index}] {self.action}({self.obj_id}, {self.target_id})"
        return f"[t={self.index}] {self.action}({self.obj_id})"

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
        self.replan_strategy   = None  # "insert_prep" | "local" | "reconstruct" | "already_satisfied" | "wrong_action"

    def __repr__(self):
        return (
            f"Error={self.error_type} | Failed={self.failed_action} | "
            f"Root={self.root_cause} | Window=[{self.t_start},{self.t_end}] | "
            f"Unsat={self.unsatisfied_needs}"
        )


class StateTracker:
    """
    Per-object state tracker wrapping ObjectStateModel.
    Stores initial environment and posture, then replays actions to compute
    state at any step.
    """

    def __init__(self, env_dict: dict = None, char_sitting: bool = False, char_lying: bool = False):
        self.initial_env = env_dict or {}
        self.initial_sitting = char_sitting
        self.initial_lying = char_lying
        self.history: list = []   # list of ActionStep in execution order

    def apply_action(self, step: ActionStep):
        """Apply one action to the model and record it in history."""
        # We will not maintain a running model; instead we replay from scratch
        # when needed. But for efficiency, we could keep a cached model.
        # For now, just store the step; we'll recompute when needed.
        self.history.append(step)

    def get_model_at_step(self, step_index: int) -> ObjectStateModel:
        """
        Return a fresh ObjectStateModel representing the world after executing
        all actions up to (but not including) the given step index.
        """
        model = ObjectStateModel.from_env_dict(self.initial_env)
        model.char_sitting = self.initial_sitting
        model.char_lying   = self.initial_lying
        for step in self.history:
            if step.index >= step_index:
                break
            model.apply(step.action, step.obj, step.target)
        return model

    def get_current_model(self) -> ObjectStateModel:
        """Return model after all executed actions."""
        return self.get_model_at_step(step_index=999999)  # large number

    def is_satisfied(self, precondition: str, obj: str, target: str = None) -> bool:
        """Check a single precondition for the given obj/target in the current state."""
        return self.get_current_model().satisfies(precondition, obj, target)

    def get_unsatisfied(self, preconditions: list, obj: str, target: str = None) -> list:
        """Return list of preconditions not currently satisfied for obj/target."""
        return self.get_current_model().check_all(preconditions, obj, target)

    def find_t_source(self, precondition: str, obj: str, t_error: int) -> int:
        """
        Find the most recent timestep before t_error where the precondition
        transitioned from satisfied to unsatisfied FOR THE SPECIFIC obj.
        Paper Eq. 2.

        Strategy: replay the full history from scratch using a fresh model,
        record whenever the precondition flips from True → False.
        Returns the timestep of the last such flip, or 1 if never satisfied
        in the first place.
        """
        # Build initial model (before any actions)
        model = ObjectStateModel.from_env_dict(self.initial_env)
        model.char_sitting = self.initial_sitting
        model.char_lying   = self.initial_lying

        last_violated_at = 1  # default: problem was in initial state
        was_ok = model.satisfies(precondition, obj)

        for step in self.history:
            if step.index >= t_error:
                break
            model.apply(step.action, step.obj, step.target)
            now_ok = model.satisfies(precondition, obj)
            if was_ok and not now_ok:
                last_violated_at = step.index
            was_ok = now_ok

        return last_violated_at


def diagnose_error(
    action_history: list,
    failed_step:    ActionStep,
    error_type:     str,
    full_plan:      list,
    env_dict:       dict = None,
) -> DiagnosisResult:
    """
    Main diagnosis function implementing SDA-Planner paper Section 4.3.

    Returns DiagnosisResult with:
      - replan_strategy: "local" | "insert_prep" | "reconstruct" | "already_satisfied" | "wrong_action"
      - t_start, t_end: reconstruction window (1-indexed)
      - unsatisfied_needs: list of violated preconditions for the specific
        failed obj/target (not a global flat check)
    """
    result = DiagnosisResult()
    result.error_type = error_type
    result.failed_action = failed_step
    result.failed_at = failed_step.index

    # ── ADDITIONAL_STEP: skip action, local replan ────────────────────────────
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy = "local"
        result.root_cause = failed_step
        result.root_cause_at = failed_step.index
        result.t_start = failed_step.index
        result.t_end = failed_step.index
        result.unsatisfied_needs = []
        return result

    # ── Replay history to build current per-object state ────────────────────
    tracker = StateTracker(env_dict=env_dict, char_sitting=False, char_lying=False)
    for step in action_history:
        tracker.apply_action(step)

    # ── Find unsatisfied preconditions for the specific obj/target ────────────
    preconditions = get_preconditions(failed_step.action)
    unsatisfied = tracker.get_unsatisfied(
        preconditions, failed_step.obj, failed_step.target
    )
    result.unsatisfied_needs = unsatisfied

    # ── AFFORDANCE_ERROR: object property mismatch → local replan ────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause = failed_step
        result.root_cause_at = failed_step.index
        result.t_start = failed_step.index
        result.t_end = failed_step.index
        return result

    # ── ALREADY SATISFIED: positive effects already true ────────────────────
    positive_effects = [e for e in get_effects(failed_step.action)
                        if not e.startswith("not_")]
    if positive_effects:
        # Check if all positive effects are already satisfied
        model = tracker.get_current_model()
        all_already_true = all(
            model.satisfies(e, failed_step.obj, failed_step.target)
            for e in positive_effects
        )
        if all_already_true:
            result.replan_strategy = "already_satisfied"
            result.root_cause = failed_step
            result.root_cause_at = failed_step.index
            result.t_start = failed_step.index
            result.t_end = failed_step.index
            result.unsatisfied_needs = []
            return result

    # ── WRONG ACTION: holds_obj unsatisfied and object not grabbable ────────────
    if "holds_obj" in unsatisfied:
        model = tracker.get_current_model()
        obj_grabbable = model.satisfies("grabbable", failed_step.obj, failed_step.target)
        if not obj_grabbable:
            result.replan_strategy = "wrong_action"
            result.root_cause = failed_step
            result.root_cause_at = failed_step.index
            result.t_start = failed_step.index
            result.t_end = failed_step.index
            result.unsatisfied_needs = []   # not a precondition problem
            return result

    # ── No unsatisfied preconditions → env state mismatch → local ────────────
    if not unsatisfied:
        result.replan_strategy = "local"
        result.root_cause = failed_step
        result.root_cause_at = failed_step.index
        result.t_start = failed_step.index
        result.t_end = failed_step.index
        return result

    # ── Select key precondition (prefer dynamic over static) ─────────────────
    dynamic_unsat = [p for p in unsatisfied if p in DYNAMIC_PRECONDITIONS]
    key_prec = dynamic_unsat[0] if dynamic_unsat else unsatisfied[0]

    # ── Simple prep action insertion ──────────────────────────────────────────
    simple_prep = {
        "not_sitting":    "STANDUP",
        "not_lying":      "STANDUP",
        "next_to_obj":    "WALK",
        "next_to_target": "WALK",   # PUTBACK/PUTIN fail → just WALK to target
    }
    if key_prec in simple_prep and len(dynamic_unsat) <= 1:
        result.replan_strategy = "insert_prep"
        result.root_cause = failed_step
        result.root_cause_at = failed_step.index
        result.t_start = failed_step.index
        result.t_end = failed_step.index
        return result

    # ── Full reconstruction ───────────────────────────────────────────────────
    result.replan_strategy = "reconstruct"

    # Determine which object the key precondition refers to (for find_t_source)
    # Most preconditions refer to the primary object; "next_to_target" refers to target.
    if key_prec == "next_to_target":
        obj_for_source = failed_step.target
    else:
        obj_for_source = failed_step.obj

    # Find t_source: most recent step that corrupted key_prec for the specific object
    t_source = tracker.find_t_source(key_prec, obj_for_source, failed_step.index)
    result.root_cause_at = t_source
    result.root_cause = next(
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
    plan = history + [failed, ActionStep(8, "PUTBACK", "tomato", "pan")]

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
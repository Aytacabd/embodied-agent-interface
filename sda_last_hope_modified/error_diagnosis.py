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
    "holds_obj", "holds_any_obj", "holds_knife", "not_both_hands_full",
    "not_sitting", "not_lying",
    "open", "closed", "on", "off", "not_on", "next_to_obj", "next_to_target",
    "sitting_or_lying", "obj_not_inside_closed_container",
    "target_open_or_not_openable", "plugged_in", "plugged_out",
    "facing_obj", "on_char",
}

# Static object properties — checked against the scene graph per object
STATIC_PROPERTIES = {
    "grabbable", "has_switch", "can_open", "has_plug",
    "eatable", "readable", "movable", "lookable",
    "sittable", "lieable", "clothes", "cuttable",
    "hangable", "pourable", "drinkable", "person",
    "clothes_or_squeezable", "eatable_or_has_eatable_on",
    "pourable_or_drinkable", "drinkable_or_recipient", "target_is_recipient",
}

# Effects ObjectStateModel.satisfies() can actually evaluate. satisfies()
# returns True for unknown predicates, so the already-satisfied check must
# only run when EVERY positive effect of the failed action is in this set.
# Without this guard, any failed PUTBACK/PUTIN/POUR/SIT/LIE/WIPE was
# misdiagnosed as already_satisfied (their effects like obj_ontop_target are
# unknown → True) and silently deleted from the plan.
EVALUABLE_EFFECTS = {
    "open", "closed", "on", "off",
    "plugged_in", "plugged_out",
    "holds_obj", "on_char",
    "next_to_obj", "facing_obj",
}


class ActionStep:
    """Represents a single action in the plan.

    obj/target hold the bare CLASS name (e.g. "light") so the class-keyed
    ObjectStateModel diagnosis works. obj_id/target_id hold the numeric
    instance id (e.g. "245") when known, so repairs can target the exact
    instance rather than an ambiguous class name.
    """

    def __init__(self, index: int, action: str, obj: str, target: str = None,
                 obj_id: str = None, target_id: str = None):
        self.index     = index
        self.action    = action.upper()
        self.obj       = obj
        self.target    = target
        self.obj_id    = obj_id
        self.target_id = target_id

    @staticmethod
    def _combine(name, oid):
        """name + numeric id -> 'name_id' (instance-qualified), else name."""
        if name is None:
            return None
        if oid is not None and str(oid).strip():
            return f"{name}_{oid}"
        return name

    @property
    def obj_full(self):
        """Instance-qualified object (e.g. 'light_245') when the id is known."""
        return self._combine(self.obj, self.obj_id)

    @property
    def target_full(self):
        """Instance-qualified target (e.g. 'washing_machine_1000')."""
        return self._combine(self.target, self.target_id)

    def __repr__(self):
        o = self.obj_full
        if self.target:
            return f"[t={self.index}] {self.action}({o}, {self.target_full})"
        return f"[t={self.index}] {self.action}({o})"


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
        char_sitting:     bool = False,
        char_lying:       bool = False,
        env_dict:         dict = None,
        initial_env_dict: dict = None,
    ):
        self.model   = ObjectStateModel.from_env_dict(
            env_dict or {},
            char_sitting = char_sitting,
            char_lying   = char_lying,
        )
        # PRE-execution env (before history was applied). Used by find_t_source
        # so replay starts from real initial state, not a blank model.
        self.initial_env_dict = initial_env_dict
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
                      t_error: int, target: str = None) -> int:
        """
        Find the most recent timestep before t_error where the precondition
        transitioned from satisfied to unsatisfied FOR THE SPECIFIC obj/target.
        Paper Eq. 2.

        Target is required for target-specific preconditions like
        next_to_target, target_open_or_not_openable, target_is_recipient —
        otherwise satisfies() checks the wrong entity.
        """
        # Build replay model from the real initial env so initial-env-derived
        # preconditions (containers, spatial, posture, plug, switch) start
        # accurate. Without this the replay would start blank and never see
        # the True → False transition for those preconditions.
        temp = ObjectStateModel.from_env_dict(self.initial_env_dict or {})

        prev_ok          = temp.satisfies(precondition, obj, target)
        last_violated_at = None

        for step in self.history:
            if step.index >= t_error:
                break
            # Replay with instance-qualified names when available so the
            # per-instance relation/state keys stay consistent with queries.
            s_obj = getattr(step, "obj_full", step.obj)
            s_tgt = getattr(step, "target_full", step.target)
            temp.apply(step.action, s_obj, s_tgt)
            now_ok = temp.satisfies(precondition, obj, target)
            if prev_ok and not now_ok:
                last_violated_at = step.index
            prev_ok = now_ok

        if last_violated_at is None:
            # Paper Eq. 2: Λ = ∅ → t_source = 1. The precondition was never
            # satisfied at any point, so the corruption predates the plan and
            # reconstruction starts from the beginning.
            return 1
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
    by_id = {n["id"]: n for n in nodes}
    # Instance-qualified names ("clothes_pants_1002") resolve to that exact
    # node; a bare class name falls back to the first instance. Without this,
    # multi-instance scenes could return a SIBLING's container.
    base, oid = obj_name, None
    if "_" in str(obj_name):
        b, m = str(obj_name).rsplit("_", 1)
        if m.isdigit():
            base, oid = b, int(m)
    if oid is not None and oid in by_id:
        obj_id = oid
    else:
        obj_id = next(
            (n["id"] for n in nodes if n.get("class_name") == base),
            None,
        )
    if obj_id is None:
        return None

    def _is_container(node):
        # Mirror ObjectStateModel: only CAN_OPEN / CONTAINERS nodes are real
        # containers. This excludes room membership (obj INSIDE dining_room),
        # which otherwise made the tree try to OPEN a room.
        if node is None or node.get("category") == "Rooms":
            return False
        flags = {p.upper() for p in node.get("properties", [])} | \
                {s.upper() for s in node.get("states", [])}
        return "CAN_OPEN" in flags or "CONTAINERS" in flags

    # Collect all INSIDE containers, preferring a CLOSED one (the real trap)
    # over an open/undefined one.
    containers = [
        by_id.get(e.get("to_id"))
        for e in edges
        if e.get("relation_type") == "INSIDE" and e.get("from_id") == obj_id
    ]
    containers = [c for c in containers if _is_container(c)]
    if not containers:
        return None
    closed = next(
        (c for c in containers
         if "CLOSED" in {s.upper() for s in c.get("states", [])}),
        None,
    )
    return (closed or containers[0]).get("class_name")


def diagnose_error(
    action_history:   list,
    failed_step:      ActionStep,
    error_type:       str,
    full_plan:        list,
    char_sitting:     bool = False,
    char_lying:       bool = False,
    env_dict:         dict = None,
    initial_env_dict: dict = None,
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
    # NOTE: unreachable via eai_sda_runner_tree (it skips ADDITIONAL_STEP
    # actions before calling diagnosis). Kept for API completeness so other
    # callers of diagnose_error handle the type correctly.
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy   = "local"
        result.root_cause        = failed_step
        result.root_cause_at     = failed_step.index
        result.t_start           = failed_step.index
        result.t_end             = failed_step.index
        result.unsatisfied_needs = []
        return result

    # ── Build current per-object state ──────────────────────────────────────
    tracker = StateTracker(
        char_sitting     = char_sitting,
        char_lying       = char_lying,
        env_dict         = env_dict,
        initial_env_dict = initial_env_dict,
    )
    if env_dict:
        # env_dict is already the post-execution state — applying history on top
        # would double-apply effects (GRAB fills both hands with same object, etc.).
        # Just record history for find_t_source without re-applying to the model.
        tracker.history = list(action_history)
    else:
        # No env snapshot: replay history from blank model to build state.
        for step in action_history:
            tracker.apply_action(step)

    # ── Find unsatisfied preconditions for the specific obj/target ────────────
    # Instance-qualified (light_245) so multi-instance scenes are diagnosed
    # per-instance: being next to light_245 no longer satisfies next_to for
    # light_411. The model falls back to class-level for bare names.
    preconditions = get_preconditions(failed_step.action)
    unsatisfied   = tracker.get_unsatisfied(
        preconditions,
        failed_step.obj_full,
        failed_step.target_full,
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
    # Only trust this when the object's class has a single instance. The model
    # aggregates state by class name (not instance id, which is lost upstream),
    # so with 2+ same-class objects "on"/"open"/etc. is true if ANY instance has
    # it — which wrongly deletes a goal action (e.g. SWITCHON tv_410 removed
    # because a different television is on). The executor returning a MISSING
    # precondition for the specific instance already contradicts "already done".
    obj_instances = tracker.model.name_to_ids.get((failed_step.obj or "").lower(), [])
    # A known instance id makes the check unambiguous even with same-class
    # siblings, because satisfies() now evaluates that exact instance.
    unambiguous_obj = failed_step.obj_id is not None or len(obj_instances) <= 1

    positive_effects = [e for e in get_effects(failed_step.action)
                        if not e.startswith("not_")]
    if (positive_effects and unambiguous_obj
            and all(e in EVALUABLE_EFFECTS for e in positive_effects)):
        all_already_true = all(
            tracker.model.satisfies(e, failed_step.obj_full, failed_step.target_full)
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
                                                    failed_step.obj_full,
                                                    failed_step.target_full)
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
        "not_sitting":        "STANDUP",
        "not_lying":          "STANDUP",
        "next_to_obj":        "WALK",
        "next_to_target":     "WALK",         # PUTBACK/PUTIN fail → just WALK to target
        "facing_obj":         "TURNTO",        # WATCH/LOOKAT fail → TURNTO (no preconditions)
        "not_both_hands_full": "DROP",         # OPEN/GRAB/SQUEEZE/MOVE/CUT fail → DROP first
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
    # specific obj/target involved (paper Eq. 2)
    t_source             = tracker.find_t_source(
        key_prec, failed_step.obj_full, failed_step.index,
        target=failed_step.target_full,
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

    # O — the error-item set (Eq. 4): o_error plus the items tied to s_error,
    # approximated by the root-cause action's objects (e.g. the pan occupying
    # the hand in the paper's pick-tomato example).
    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)
    if result.root_cause is not None:
        error_objects.add(result.root_cause.obj)
        if result.root_cause.target:
            error_objects.add(result.root_cause.target)

    # t_end (Eq. 4): max{t | ALL objects of actions in (t_error, t] ⊆ O} —
    # extend forward only over a CONTIGUOUS run of error-item actions and stop
    # at the first later action that touches an unrelated object.
    t_end = failed_step.index
    for step in sorted(
        (s for s in full_plan if s.index > failed_step.index),
        key=lambda s: s.index,
    ):
        step_objects_in_O = (
            step.obj in error_objects
            and (not step.target or step.target in error_objects)
        )
        if step_objects_in_O:
            t_end = step.index
        else:
            break
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

    # env_dict is the POST-execution snapshot (after the history above):
    # character walked to tomato and is holding pan + box in both hands.
    env = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "pan",    "states": [], "properties": ["GRABBABLE"]},
            {"id": 3, "class_name": "tomato",  "states": [], "properties": ["GRABBABLE", "EATABLE"]},
            {"id": 4, "class_name": "box",     "states": [], "properties": ["GRABBABLE"]},
            {"id": 5, "class_name": "lamp",    "states": [], "properties": []},
        ],
        "edges": [
            {"from_id": 1, "to_id": 3, "relation_type": "CLOSE"},
            {"from_id": 1, "to_id": 2, "relation_type": "HOLDS_RH"},
            {"from_id": 1, "to_id": 4, "relation_type": "HOLDS_LH"},
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

    print("All tests passed ✅")
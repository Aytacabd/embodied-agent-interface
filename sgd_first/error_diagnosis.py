"""
Error Backtrack and Diagnosis Module
Based on SDA-Planner paper Section 4.3

Steps:
1. Identify error type
2. Check unsatisfied preconditions using SDG
3. Find t_source (when did the state get violated)
4. Calculate reconstruction window [t_start, t_end]
5. Return structured diagnosis for the replanner
"""

from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# Preconditions that change dynamically during execution
# Used to find meaningful t_source (static object properties never change)
DYNAMIC_PRECONDITIONS = {
    "holds_obj", "not_both_hands_full", "not_sitting",
    "not_lying", "open", "closed", "on", "off",
    "next_to_obj", "next_to_target", "sitting_or_lying",
    "obj_not_inside_closed_container", "target_open_or_not_openable",
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
    Tracks character and object states as actions are executed.
    Initialized with the actual starting state from the environment.
    """

    def __init__(self, char_sitting: bool = False, char_lying: bool = False,
                 env_dict: dict = None):
        self.history        = []
        self.current_states = set()
        self.hand_count     = 0   # track how many objects are held

        # Character posture from actual scene
        if char_sitting:
            self.current_states.add("sitting")
        else:
            self.current_states.add("not_sitting")

        if char_lying:
            self.current_states.add("lying")
        else:
            self.current_states.add("not_lying")

        # Default states
        self.current_states.add("not_holds_obj")
        self.current_states.add("not_both_hands_full")

        # Read actual environment states
        if env_dict:
            for node in env_dict.get("nodes", []):
                states = [s.upper() for s in node.get("states", [])]
                if "OPEN" in states:
                    self.current_states.add("open")
                    self.current_states.add("obj_not_inside_closed_container")
                if "CLOSED" in states:
                    self.current_states.add("closed")
                if "OFF" in states:
                    self.current_states.add("off")
                if "ON" in states:
                    self.current_states.add("on")
                if "PLUGGED_IN" in states:
                    self.current_states.add("plugged_in")
        else:
            # Safe universal defaults when no env dict available
            self.current_states.add("plugged_in")
            self.current_states.add("obj_not_inside_closed_container")

    def apply_action(self, step: ActionStep):
        """Apply an action's effects to update current state."""
        effects      = get_effects(step.action)
        states_added = []
        states_removed = []

        for effect in effects:
            if effect.startswith("not_"):
                positive = effect[4:]
                if positive in self.current_states:
                    self.current_states.discard(positive)
                    states_removed.append(positive)
                self.current_states.add(effect)
                states_added.append(effect)
            else:
                self.current_states.add(effect)
                states_added.append(effect)
                negation = f"not_{effect}"
                if negation in self.current_states:
                    self.current_states.discard(negation)
                    states_removed.append(negation)

        # ── Track hand count separately ───────────────────────────────────────
        # SDG effects are generic — we need to track hand_count to know
        # when both_hands_full becomes true/false
        if step.action == "GRAB":
            self.hand_count += 1
            self.current_states.add("holds_obj")
            self.current_states.discard("not_holds_obj")
            if self.hand_count >= 2:
                self.current_states.add("both_hands_full")
                self.current_states.discard("not_both_hands_full")
                if "not_both_hands_full" not in states_removed:
                    states_removed.append("not_both_hands_full")
                if "both_hands_full" not in states_added:
                    states_added.append("both_hands_full")

        elif step.action in ("PUTBACK", "PUTIN", "DROP", "PUTON",
                             "PUTOFF", "PUTOBJBACK", "POUR"):
            self.hand_count = max(0, self.hand_count - 1)
            if self.hand_count < 2:
                self.current_states.add("not_both_hands_full")
                self.current_states.discard("both_hands_full")
                if "both_hands_full" not in states_removed:
                    states_removed.append("both_hands_full")
                if "not_both_hands_full" not in states_added:
                    states_added.append("not_both_hands_full")
            if self.hand_count == 0:
                self.current_states.discard("holds_obj")
                self.current_states.add("not_holds_obj")

        # ── Track open/closed for container access ─────────────────────────────
        if step.action == "OPEN":
            self.current_states.add("obj_not_inside_closed_container")
            self.current_states.add("target_open_or_not_openable")
            if "obj_not_inside_closed_container" not in states_added:
                states_added.append("obj_not_inside_closed_container")
        elif step.action == "CLOSE":
            self.current_states.discard("obj_not_inside_closed_container")
            self.current_states.discard("target_open_or_not_openable")
            if "obj_not_inside_closed_container" not in states_removed:
                states_removed.append("obj_not_inside_closed_container")

        self.history.append({
            "timestep":       step.index,
            "action":         step.action,
            "obj":            step.obj,
            "states_added":   states_added,
            "states_removed": states_removed,
        })

    def is_satisfied(self, precondition: str) -> bool:
        """Check if a precondition is currently satisfied."""
        if precondition == "sitting_or_lying":
            return "sitting" in self.current_states or "lying" in self.current_states
        if precondition == "not_both_hands_full":
            return "both_hands_full" not in self.current_states
        if precondition == "target_open_or_not_openable":
            return ("open" in self.current_states or
                    "not_openable" in self.current_states or
                    "target_open_or_not_openable" in self.current_states)
        if precondition in ("grabbable", "has_switch", "can_open",
                            "has_plug", "has_plug_or_switch",
                            "eatable", "drinkable_or_recipient",
                            "readable", "movable", "lookable",
                            "sittable", "lieable", "clothes",
                            "recipient_target", "cuttable"):
            # Static object properties — assume satisfied unless proven otherwise
            return True
        return precondition in self.current_states

    def find_t_source(self, precondition: str, t_error: int) -> int:
        """
        Find t_source: the most recent timestep before t_error
        where the precondition changed from satisfied to unsatisfied.
        Paper Eq. 2.
        Returns 1 if never found.
        """
        negation = f"not_{precondition}"

        for entry in reversed(self.history):
            if entry["timestep"] >= t_error:
                continue
            # Direct: precondition was removed
            if precondition in entry["states_removed"]:
                return entry["timestep"]
            # Indirect: negation was added (which removes the positive)
            if negation in entry["states_added"]:
                return entry["timestep"]
        return 1


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
    Main diagnosis function based on SDA-Planner paper Section 4.3.
    """

    result               = DiagnosisResult()
    result.error_type    = error_type
    result.failed_action = failed_step
    result.failed_at     = failed_step.index

    # ── Special case: ADDITIONAL_STEP ────────────────────────────────────────
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy   = "local"
        result.root_cause        = failed_step
        result.root_cause_at     = failed_step.index
        result.t_start           = failed_step.index
        result.t_end             = failed_step.index
        result.unsatisfied_needs = []
        return result

    # Replay history to build state tracker
    tracker = StateTracker(
        char_sitting = char_sitting,
        char_lying   = char_lying,
        env_dict     = env_dict,
    )
    for step in action_history:
        tracker.apply_action(step)

    # ── Step 1: Find unsatisfied preconditions ────────────────────────────────
    preconditions = get_preconditions(failed_step.action)
    unsatisfied   = [p for p in preconditions if not tracker.is_satisfied(p)]
    result.unsatisfied_needs = unsatisfied

    # ── Step 2: AFFORDANCE_ERROR → local replan ───────────────────────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Step 3: No unsatisfied preconditions → local replan ──────────────────
    if not unsatisfied:
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Step 4: Simple prep action insertion ──────────────────────────────────
    key_precondition = unsatisfied[0]

    # Prefer dynamic preconditions over static object properties
    # for finding meaningful t_source
    dynamic_unsat = [p for p in unsatisfied if p in DYNAMIC_PRECONDITIONS]
    if dynamic_unsat:
        key_precondition = dynamic_unsat[0]

    simple_prep_fixes = {
        "not_sitting": "STANDUP",
        "not_lying":   "STANDUP",
        "next_to_obj": "WALK",
    }
    if key_precondition in simple_prep_fixes and len(dynamic_unsat) <= 1:
        result.replan_strategy = "insert_prep"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Step 5: Full reconstruction — find t_source (paper Eq. 2) ────────────
    result.replan_strategy = "reconstruct"

    t_source = tracker.find_t_source(key_precondition, failed_step.index)
    result.root_cause_at = t_source

    root_action = None
    for step in action_history:
        if step.index == t_source:
            root_action = step
            break
    result.root_cause = root_action if root_action else failed_step

    # ── Step 6: Calculate t_start (paper Eq. 4) ──────────────────────────────
    # Go backwards from t_source, keep extending while actions are prep
    t_start = t_source
    for step in reversed(action_history):
        if step.index >= t_source:
            continue
        if is_prep_action(step.action):
            t_start = step.index
        else:
            break
    result.t_start = t_start

    # ── Step 7: Calculate t_end (paper Eq. 4) ────────────────────────────────
    # Extend forward past ALL actions involving error objects
    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)

    t_end = failed_step.index
    for step in full_plan:
        if step.index > failed_step.index:
            if (step.obj in error_objects or
                    (step.target and step.target in error_objects)):
                t_end = step.index
            # No break — check all remaining steps
    result.t_end = t_end

    return result


if __name__ == "__main__":
    history = [
        ActionStep(1, "WALK",  "lamp"),
        ActionStep(2, "FIND",  "pan"),
        ActionStep(3, "GRAB",  "pan"),     # hand_count = 1
        ActionStep(4, "FIND",  "tomato"),
        ActionStep(5, "WALK",  "tomato"),
        ActionStep(6, "GRAB",  "box"),     # hand_count = 2 → both_hands_full!
    ]
    failed = ActionStep(7, "GRAB", "tomato")
    plan   = history + [failed, ActionStep(8, "PUTBACK", "tomato", "pan")]

    result = diagnose_error(history, failed, "MISSING_STEP", plan)
    print(result)
    # Expected: t_source=3, t_start=2, t_end=8
    # Root cause should be GRAB(pan) at t=3

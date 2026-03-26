"""
Error Backtrack and Diagnosis Module
Based on SDA-Planner paper Section 4.3

Algorithm:
1. Identify error type from EAI checker
2. Replay executed actions to track state
3. Check which preconditions of failed action are unsatisfied
4. Find t_source: most recent step that violated the key precondition (Eq. 2)
5. Calculate reconstruction window [t_start, t_end] (Eq. 4)
6. Return DiagnosisResult with replan strategy

Strategies:
  - "local"       : Unsat=[] or AFFORDANCE_ERROR → generate additional steps
  - "insert_prep" : single prep action needed (STANDUP/WALK)
  - "reconstruct" : full window reconstruction using search tree
"""

from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# States that change dynamically during execution
DYNAMIC_PRECONDITIONS = {
    "holds_obj", "not_both_hands_full", "not_sitting", "not_lying",
    "open", "closed", "on", "off", "next_to_obj", "next_to_target",
    "sitting_or_lying", "obj_not_inside_closed_container",
    "target_open_or_not_openable", "plugged_in", "plugged_out",
    "facing_obj",
}

# Static object properties — always assumed True unless env proves otherwise
STATIC_PROPERTIES = {
    "grabbable", "has_switch", "can_open", "has_plug", "has_plug_or_switch",
    "eatable", "drinkable_or_recipient", "readable", "movable", "lookable",
    "sittable", "lieable", "clothes", "recipient_target", "cuttable",
    "clothes", "hangable", "body_part", "person", "cover_object",
    "surfaces", "containers", "cream", "pourable", "drinkable",
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
    Simulates environment state by replaying executed actions.
    Initialized from the actual environment state at execution start.
    """

    def __init__(self, char_sitting: bool = False, char_lying: bool = False,
                 env_dict: dict = None):
        self.history        = []
        self.current_states = set()
        self.hand_count     = 0

        # ── Character posture ─────────────────────────────────────────────────
        if char_sitting:
            self.current_states.add("sitting")
        else:
            self.current_states.add("not_sitting")

        if char_lying:
            self.current_states.add("lying")
        else:
            self.current_states.add("not_lying")

        # ── Defaults ──────────────────────────────────────────────────────────
        self.current_states.add("not_holds_obj")
        self.current_states.add("not_both_hands_full")
        # VirtualHome: all devices plugged_in by default unless PLUGGED_OUT
        self.current_states.add("plugged_in")


        # ── Read actual environment states ────────────────────────────────────
        if env_dict:
            for node in env_dict.get("nodes", []):
                states = [s.upper() for s in node.get("states", [])]
                props  = [p.upper() for p in node.get("properties", [])]

                if "OPEN" in states:
                    self.current_states.add("open")
                    self.current_states.add("obj_not_inside_closed_container")
                    self.current_states.add("target_open_or_not_openable")
                if "CLOSED" in states:
                    self.current_states.add("closed")
                if "OFF" in states:
                    self.current_states.add("off")
                if "ON" in states:
                    self.current_states.add("on")
                if "PLUGGED_IN" in states:
                    self.current_states.add("plugged_in")
                    self.current_states.discard("plugged_out")
                if "PLUGGED_OUT" in states:
                    self.current_states.add("plugged_out")
                    self.current_states.discard("plugged_in")

                # Object properties
                if "HAS_SWITCH" in props:
                    self.current_states.add("has_switch")
                if "CAN_OPEN" in props:
                    self.current_states.add("can_open")
                if "HAS_PLUG" in props:
                    self.current_states.add("has_plug")
        else:
            # Safe defaults when no env dict available
            self.current_states.add("plugged_in")
            self.current_states.add("obj_not_inside_closed_container")

    def apply_action(self, step: ActionStep):
        """Apply action effects to update state. Records history for t_source search."""
        effects        = get_effects(step.action)
        states_added   = []
        states_removed = []

        # Apply SDG effects
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

        # ── Hand count tracking ───────────────────────────────────────────────
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

        # ── Container open/close tracking ─────────────────────────────────────
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
        """Check if a precondition holds in the current state."""
        if precondition == "sitting_or_lying":
            return ("sitting" in self.current_states or
                    "lying"   in self.current_states)
        if precondition == "not_both_hands_full":
            return "both_hands_full" not in self.current_states
        if precondition == "target_open_or_not_openable":
            return ("open"       in self.current_states or
                    "not_openable" in self.current_states or
                    "target_open_or_not_openable" in self.current_states)
        # Static object properties — assume satisfied (object-level, not tracked)
        if precondition in STATIC_PROPERTIES:
            return True
        return precondition in self.current_states

    def find_t_source(self, precondition: str, t_error: int) -> int:
        """
        Find the most recent timestep before t_error where precondition
        changed from satisfied to unsatisfied. Paper Eq. 2.
        Returns 1 if never corrupted (initial state already violated).
        """
        negation = f"not_{precondition}"
        for entry in reversed(self.history):
            if entry["timestep"] >= t_error:
                continue
            if precondition in entry["states_removed"]:
                return entry["timestep"]
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
    Main diagnosis function implementing SDA-Planner paper Section 4.3.

    Returns DiagnosisResult with:
      - replan_strategy: "local" | "insert_prep" | "reconstruct"
      - t_start, t_end: reconstruction window (1-indexed)
      - unsatisfied_needs: list of violated preconditions
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

    # ── Replay history to build current state ────────────────────────────────
    tracker = StateTracker(
        char_sitting = char_sitting,
        char_lying   = char_lying,
        env_dict     = env_dict,
    )
    for step in action_history:
        tracker.apply_action(step)

    # ── Find unsatisfied preconditions ────────────────────────────────────────
    preconditions = get_preconditions(failed_step.action)
    unsatisfied   = [p for p in preconditions if not tracker.is_satisfied(p)]
    result.unsatisfied_needs = unsatisfied

    # ── AFFORDANCE_ERROR: object property mismatch → local replan ────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── No unsatisfied preconditions → environment state mismatch → local ────
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

    # ── Simple prep action insertion (paper: tstart = terror = tend) ──────────
    # Only one dynamic precondition AND it's fixable by a single prep action
    simple_prep = {"not_sitting": "STANDUP", "not_lying": "STANDUP", "next_to_obj": "WALK"}
    if key_prec in simple_prep and len(dynamic_unsat) <= 1:
        result.replan_strategy = "insert_prep"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Full reconstruction ───────────────────────────────────────────────────
    result.replan_strategy = "reconstruct"

    # Find t_source: most recent step that corrupted key_prec (paper Eq. 2)
    t_source             = tracker.find_t_source(key_prec, failed_step.index)
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
    # Paper example: GRAB(tomato) fails at t=7 because GRAB(pan) at t=6
    # filled both hands → not_both_hands_full violated
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

    result = diagnose_error(history, failed, "MISSING_STEP", plan)
    print(result)
    # Expected: Unsat=['not_both_hands_full'], Root=[t=6], Window=[4,8]

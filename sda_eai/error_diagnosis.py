"""
Error Backtrack and Diagnosis Module
Based on SDA-Planner paper Section 4.3

Steps:
1. Identify error type from EAI error code
2. Check unsatisfied preconditions using SDG
3. Find t_source (when did the state get violated)
4. Calculate reconstruction window [t_start, t_end]
5. Return structured diagnosis for the replanner

EAI error codes:
    0 = WRONG_TEMPORAL_ORDER
    1 = MISSING_STEP
    2 = AFFORDANCE_ERROR
    3 = UNSEEN_OBJECT
    4 = ADDITIONAL_STEP
    5 = UNKNOWN_ERROR
"""

import re
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# ── EAI error code mapping ────────────────────────────────────────────────────
EAI_ERROR_CODES = {
    0: "WRONG_TEMPORAL_ORDER",
    1: "MISSING_STEP",
    2: "AFFORDANCE_ERROR",
    3: "UNSEEN_OBJECT",
    4: "ADDITIONAL_STEP",
    5: "UNKNOWN_ERROR",
}

# Preconditions that change dynamically during execution
# Used to find meaningful t_source (static object properties never change)
# DYNAMIC_PRECONDITIONS = {
#     "holds_obj", "not_both_hands_full", "not_sitting",
#     "not_lying", "open", "closed", "on", "off",
#     "next_to_obj", "next_to_target", "sitting_or_lying",
#     "obj_not_inside_closed_container", "target_open_or_not_openable",
# }
DYNAMIC_PRECONDITIONS = {
    "holds_obj", "not_both_hands_full", "not_sitting",
    "not_lying", "open", "closed", "on", "off",
    "next_to_obj", "next_to_target", "sitting_or_lying",
    "obj_not_inside_closed_container", "target_open_or_not_openable",
    # REMOVED: "grabbable", "has_switch", "can_open" — static, never dynamic
}

# ── ActionStep ────────────────────────────────────────────────────────────────

class ActionStep:
    """Represents a single action in the plan."""

    def __init__(self, index: int, action: str, obj: str, target: str = None,
                 obj_id: int = None, target_id: int = None, raw: str = None):
        self.index     = index
        self.action    = action.upper()
        self.obj       = obj
        self.target    = target
        self.obj_id    = obj_id
        self.target_id = target_id
        self.raw       = raw  # original EAI string e.g. "[WALK] <light> (411)"

    def __repr__(self):
        if self.target:
            return f"[t={self.index}] {self.action}({self.obj}, {self.target})"
        return f"[t={self.index}] {self.action}({self.obj})"

    @staticmethod
    def from_eai_string(index: int, action_str: str) -> "ActionStep":
        """
        Parse EAI action string into ActionStep.
        Handles formats:
          "[WALK] <light> (411)"
          "[PUTIN] <soap> (23) <washing_machine> (45)"
          "[STANDUP]"
        """
        action_match = re.match(r"\[(\w+)\]", action_str)
        if not action_match:
            return ActionStep(index, "UNKNOWN", "", raw=action_str)

        action = action_match.group(1).upper()

        # Extract all object-id pairs
        obj_pairs = re.findall(r"<([\w\s]+)>\s*\((\d+)\)", action_str)

        obj        = obj_pairs[0][0].strip() if len(obj_pairs) > 0 else ""
        obj_id     = int(obj_pairs[0][1])    if len(obj_pairs) > 0 else None
        target     = obj_pairs[1][0].strip() if len(obj_pairs) > 1 else None
        target_id  = int(obj_pairs[1][1])    if len(obj_pairs) > 1 else None

        return ActionStep(
            index=index, action=action, obj=obj, target=target,
            obj_id=obj_id, target_id=target_id, raw=action_str
        )


# ── DiagnosisResult ───────────────────────────────────────────────────────────

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
            f"Strategy={self.replan_strategy} | "
            f"Unsat={self.unsatisfied_needs}"
        )


# ── StateTracker ──────────────────────────────────────────────────────────────

class StateTracker:
    """
    Tracks character and object states as actions are executed.
    Can be initialised from actual EAI environment state dict.
    """

    def __init__(self, char_sitting: bool = False, char_lying: bool = False,
                 env_dict: dict = None):
        self.history        = []
        self.current_states = set()
        self.hand_count     = 0

        # Character posture
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

        # Read actual environment states from EAI env dict
        if env_dict:
            self._init_from_env_dict(env_dict)
        else:
            self.current_states.add("plugged_in")
            self.current_states.add("obj_not_inside_closed_container")

    # def _init_from_env_dict(self, env_dict: dict):
    #     """
    #     Initialise states from EAI's env_state.to_dict().
    #     env_dict has 'nodes' (list of node dicts) and 'edges'.
    #     """
    #     nodes = env_dict.get("nodes", [])
    #     edges = env_dict.get("edges", [])

    #     has_open   = False
    #     has_closed = False

    #     for node in nodes:
    #         states = [s.upper() for s in node.get("states", [])]
    #         if "OPEN" in states:
    #             has_open = True
    #         if "CLOSED" in states:
    #             has_closed = True
    #         if "OFF" in states:
    #             self.current_states.add("off")
    #         if "ON" in states:
    #             self.current_states.add("on")
    #         if "PLUGGED_IN" in states:
    #             self.current_states.add("plugged_in")
    #         if "SITTING" in states:
    #             self.current_states.discard("not_sitting")
    #             self.current_states.add("sitting")
    #         if "LYING" in states:
    #             self.current_states.discard("not_lying")
    #             self.current_states.add("lying")

    #     # Check if agent is holding anything
    #     char_id = None
    #     for node in nodes:
    #         if node.get("class_name") == "character":
    #             char_id = node.get("id")
    #             break

    #     if char_id:
    #         holds_count = sum(
    #             1 for e in edges
    #             if e.get("from_id") == char_id
    #             and e.get("relation_type") in ("HOLDS_RH", "HOLDS_LH")
    #         )
    #         self.hand_count = holds_count
    #         if holds_count > 0:
    #             self.current_states.add("holds_obj")
    #             self.current_states.discard("not_holds_obj")
    #         if holds_count >= 2:
    #             self.current_states.add("both_hands_full")
    #             self.current_states.discard("not_both_hands_full")

    #     if has_open:
    #         self.current_states.add("open")
    #         self.current_states.add("obj_not_inside_closed_container")
    #         self.current_states.add("target_open_or_not_openable")
    #     if has_closed:
    #         self.current_states.add("closed")

    #     # Default: plugged_in always true in VirtualHome
    #     self.current_states.add("plugged_in")
    def _init_from_env_dict(self, env_dict: dict):
        nodes = env_dict.get("nodes", [])
        edges = env_dict.get("edges", [])

        for node in nodes:
            states = [s.upper() for s in node.get("states", [])]
            class_name = node.get("class_name", "")

            # These are global/character states
            if "SITTING" in states:
                self.current_states.discard("not_sitting")
                self.current_states.add("sitting")
            if "LYING" in states:
                self.current_states.discard("not_lying")
                self.current_states.add("lying")

            # Object states — store qualified by class name
            # Also store unqualified for backward compat
            for s in states:
                s_lower = s.lower()
                if s_lower in ("on", "off", "open", "closed",
                            "clean", "dirty", "plugged_in", "plugged_out"):
                    self.current_states.add(s_lower)
                    self.current_states.add(f"{s_lower}:{class_name}")

        # Holding state
        char_id = next((n["id"] for n in nodes
                        if n.get("class_name") == "character"), None)
        if char_id:
            holds_count = sum(
                1 for e in edges
                if e.get("from_id") == char_id
                and e.get("relation_type") in ("HOLDS_RH", "HOLDS_LH")
            )
            self.hand_count = holds_count
            if holds_count > 0:
                self.current_states.add("holds_obj")
                self.current_states.discard("not_holds_obj")
            if holds_count >= 2:
                self.current_states.add("both_hands_full")
                self.current_states.discard("not_both_hands_full")

        # Container states
        open_names  = {n.get("class_name") for n in nodes
                    if "OPEN" in [s.upper() for s in n.get("states", [])]}
        closed_names = {n.get("class_name") for n in nodes
                        if "CLOSED" in [s.upper() for s in n.get("states", [])]}
        if open_names:
            self.current_states.add("open")
            self.current_states.add("obj_not_inside_closed_container")
            self.current_states.add("target_open_or_not_openable")
        if closed_names:
            self.current_states.add("closed")

        self.current_states.add("plugged_in")
    def apply_action(self, step: ActionStep):
        """Apply an action's effects to update current state."""
        effects        = get_effects(step.action)
        states_added   = []
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

        # Track hand count
        if step.action == "GRAB":
            self.hand_count += 1
            self.current_states.add("holds_obj")
            self.current_states.discard("not_holds_obj")
            if self.hand_count >= 2:
                self.current_states.add("both_hands_full")
                if "not_both_hands_full" not in states_removed:
                    states_removed.append("not_both_hands_full")
                self.current_states.discard("not_both_hands_full")
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

        # Track container open/closed
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

    # def is_satisfied(self, precondition: str) -> bool:
    #     """Check if a precondition is currently satisfied."""
    #     if precondition == "sitting_or_lying":
    #         return "sitting" in self.current_states or "lying" in self.current_states
    #     if precondition == "not_both_hands_full":
    #         return "both_hands_full" not in self.current_states
    #     if precondition == "target_open_or_not_openable":
    #         return ("open" in self.current_states or
    #                 "not_openable" in self.current_states or
    #                 "target_open_or_not_openable" in self.current_states)
    #     # Static object properties — assume satisfied
    #     if precondition in ("grabbable", "has_switch", "can_open",
    #                         "has_plug", "eatable", "drinkable",
    #                         "readable", "movable", "lookable",
    #                         "sittable", "lieable", "clothes"):
    #         return True
    #     return precondition in self.current_states
    def is_satisfied(self, precondition: str) -> bool:
        if precondition == "sitting_or_lying":
            return "sitting" in self.current_states or "lying" in self.current_states
        if precondition == "not_both_hands_full":
            return "both_hands_full" not in self.current_states
        if precondition == "target_open_or_not_openable":
            return ("open" in self.current_states or
                    "not_openable" in self.current_states or
                    "target_open_or_not_openable" in self.current_states)
        # Static properties no longer appear in SDG needs,
        # but handle gracefully if called directly
        if precondition in ("grabbable", "has_switch", "can_open", "has_plug",
                            "eatable", "drinkable", "readable", "movable",
                            "lookable", "sittable", "lieable", "clothes"):
            return True   # keep for safety, won't be called from SDG anymore
        return precondition in self.current_states

    def find_t_source(self, precondition: str, t_error: int) -> int:
        """
        Find t_source: most recent timestep before t_error where
        precondition changed from satisfied to unsatisfied.
        Paper Equation 2.
        Returns 1 if never found.
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


# ── Main diagnosis function ───────────────────────────────────────────────────

def diagnose_error(
    action_history: list,       # list of ActionStep objects already executed
    failed_step:    ActionStep,
    error_type,                 # int (EAI code) or str
    full_plan:      list,       # full list of ActionStep objects
    char_sitting:   bool = False,
    char_lying:     bool = False,
    env_dict:       dict = None,  # EAI env_state.to_dict() at time of failure
) -> DiagnosisResult:
    """
    Main diagnosis function — SDA-Planner paper Section 4.3.

    error_type can be:
      - int: EAI error code (0-5)
      - str: "MISSING_STEP", "AFFORDANCE_ERROR", etc.
    """
    result               = DiagnosisResult()
    result.failed_action = failed_step
    result.failed_at     = failed_step.index

    # Normalise error type
    if isinstance(error_type, int):
        error_type = EAI_ERROR_CODES.get(error_type, "UNKNOWN_ERROR")
    result.error_type = error_type

    # ── ADDITIONAL_STEP: skip and continue ────────────────────────────────────
    if error_type == "ADDITIONAL_STEP":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── WRONG_TEMPORAL_ORDER: local replan ────────────────────────────────────
    if error_type == "WRONG_TEMPORAL_ORDER":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── UNSEEN_OBJECT: local replan ───────────────────────────────────────────
    if error_type == "UNSEEN_OBJECT":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Replay history to build state tracker ─────────────────────────────────
    tracker = StateTracker(
        char_sitting=char_sitting,
        char_lying=char_lying,
        env_dict=env_dict,
    )
    for step in action_history:
        tracker.apply_action(step)

    # ── Step 1: Find unsatisfied preconditions ────────────────────────────────
    preconditions    = get_preconditions(failed_step.action)
    unsatisfied      = [p for p in preconditions if not tracker.is_satisfied(p)]
    result.unsatisfied_needs = unsatisfied

    # ── AFFORDANCE_ERROR: local replan ────────────────────────────────────────
    if error_type == "AFFORDANCE_ERROR":
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── No unsatisfied preconditions → environment state error → local replan ─
    if not unsatisfied:
        result.replan_strategy = "local"
        result.root_cause      = failed_step
        result.root_cause_at   = failed_step.index
        result.t_start         = failed_step.index
        result.t_end           = failed_step.index
        return result

    # ── Prefer dynamic preconditions for finding t_source ────────────────────
    key_precondition = unsatisfied[0]
    dynamic_unsat    = [p for p in unsatisfied if p in DYNAMIC_PRECONDITIONS]
    if dynamic_unsat:
        key_precondition = dynamic_unsat[0]

    # ── Simple prep action insertion (no full reconstruction needed) ──────────
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

    # ── Full reconstruction: find t_source (paper Eq. 2) ─────────────────────
    result.replan_strategy = "reconstruct"

    t_source = tracker.find_t_source(key_precondition, failed_step.index)
    result.root_cause_at = t_source

    root_action = None
    for step in action_history:
        if step.index == t_source:
            root_action = step
            break
    result.root_cause = root_action if root_action else failed_step

    # ── t_start: extend backwards while actions are prep (paper Eq. 4) ───────
    t_start = t_source
    for step in reversed(action_history):
        if step.index >= t_source:
            continue
        if is_prep_action(step.action):
            t_start = step.index
        else:
            break
    result.t_start = t_start

    # ── t_end: extend forward past all error-object actions (paper Eq. 4) ────
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


# ── Convenience: diagnose from EAI data directly ─────────────────────────────

def diagnose_from_eai(
    eai_actions:        list,        # list of EAI action strings executed so far
    failed_eai_action:  str,         # the failed EAI action string
    eai_error_code:     int,         # EAI error code (0-5)
    full_eai_plan:      list,        # full list of EAI action strings
    env_state_history:  list = None, # list of env_state dicts from EAI
) -> DiagnosisResult:
    """
    Convenience wrapper that converts EAI format directly.
    Use this from evaluate_results_sda.py.
    """
    # Convert EAI strings to ActionStep objects
    history  = [ActionStep.from_eai_string(i + 1, a)
                for i, a in enumerate(eai_actions)]
    failed   = ActionStep.from_eai_string(len(eai_actions) + 1, failed_eai_action)
    full_plan = [ActionStep.from_eai_string(i + 1, a)
                 for i, a in enumerate(full_eai_plan)]

    # Use last known env state if available
    env_dict = env_state_history[-1] if env_state_history else None

    # Check character posture from env state
    char_sitting = False
    char_lying   = False
    if env_dict:
        for node in env_dict.get("nodes", []):
            if node.get("class_name") == "character":
                states = [s.upper() for s in node.get("states", [])]
                char_sitting = "SITTING" in states
                char_lying   = "LYING" in states
                break

    return diagnose_error(
        action_history=history,
        failed_step=failed,
        error_type=eai_error_code,
        full_plan=full_plan,
        char_sitting=char_sitting,
        char_lying=char_lying,
        env_dict=env_dict,
    )


if __name__ == "__main__":
    # Test: GRAB tomato fails because both hands full (pan + box grabbed)
    history = [
        ActionStep(1, "WALK", "lamp"),
        ActionStep(2, "FIND", "pan"),
        ActionStep(3, "GRAB", "pan"),    # hand_count = 1
        ActionStep(4, "FIND", "tomato"),
        ActionStep(5, "WALK", "tomato"),
        ActionStep(6, "GRAB", "box"),    # hand_count = 2 → both_hands_full
    ]
    failed   = ActionStep(7, "GRAB", "tomato")
    full_plan = history + [failed, ActionStep(8, "PUTBACK", "tomato", "pan")]

    result = diagnose_error(history, failed, 1, full_plan)
    print(result)
    # Expected: Unsat=['not_both_hands_full'], t_source=6, t_start=4 or 5, t_end=8

    # Test EAI string parsing
    step = ActionStep.from_eai_string(1, "[WALK] <light> (411)")
    print(step)
    step2 = ActionStep.from_eai_string(2, "[PUTIN] <soap> (23) <washing_machine> (45)")
    print(step2)
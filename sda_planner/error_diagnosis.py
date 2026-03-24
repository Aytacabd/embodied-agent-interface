"""
Error Backtrack and Diagnosis module for SDA-Planner on VirtualHome.

Maps EAI's error codes to SDA-Planner error types and localises
the minimal subsequence that needs to be reconstructed.

EAI error codes:
    0 = WRONG_TEMPORAL_ORDER
    1 = MISSING_STEP
    2 = AFFORDANCE_ERROR
    3 = UNSEEN_OBJECT
    4 = ADDITIONAL_STEP
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from sda_planner.state_dependency_graph import StateDependencyGraph, StateCondition


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class ErrorType(Enum):
    ENVIRONMENT_STATE   = "environment_state"    # object not where expected → local replan
    ACTION_PRECONDITION = "action_precondition"  # hand not empty, obj not open, etc.
    WRONG_ORDER         = "wrong_order"
    AFFORDANCE          = "affordance"
    ADDITIONAL_STEP     = "additional_step"
    UNKNOWN             = "unknown"
    MISSING_STEP_MAPPED = "missing_step_mapped"


# Map EAI integer error codes → our ErrorType
EAI_CODE_TO_ERROR_TYPE = {
    0: ErrorType.WRONG_ORDER,
    1: ErrorType.MISSING_STEP_MAPPED,   # resolved further below
    2: ErrorType.AFFORDANCE,
    3: ErrorType.ENVIRONMENT_STATE,
    4: ErrorType.ADDITIONAL_STEP,
    5: ErrorType.UNKNOWN,
}

# EAI code 1 (MISSING_STEP) can be either env-state or precondition error;
# we resolve it via SDG inspection.
MISSING_STEP_EAI_CODE = 1


@dataclass
class DiagnosisResult:
    """Output of the Error Backtrack & Diagnosis module."""
    error_type: ErrorType
    error_index: int                          # index in action list where error occurred
    source_index: int                         # index where root cause originates
    replan_start: int                         # tstart  
    replan_end: int                           # tend
    unsatisfied_conditions: List[StateCondition] = field(default_factory=list)
    root_action: Optional[str] = None         # action that caused the state violation
    needs_full_replan: bool = False

    def __repr__(self):
        return (
            f"DiagnosisResult("
            f"type={self.error_type.value}, "
            f"error_at={self.error_index}, "
            f"source_at={self.source_index}, "
            f"replan=[{self.replan_start},{self.replan_end}])"
        )


# ---------------------------------------------------------------------------
# Diagnosis engine
# ---------------------------------------------------------------------------

class ErrorDiagnoser:
    """
    Given the failed action index, the action history, and the environment
    state history, diagnoses the error type and localises the reconstruction
    window [tstart, tend].
    """

    def __init__(self, sdg: StateDependencyGraph):
        self.sdg = sdg

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def diagnose(
        self,
        actions: List[dict],            # full planned action list [{"action":..,"obj":..}]
        error_index: int,               # index of the failed action
        eai_error_code: int,            # error code from EAI evaluate_results
        env_state_history: List[dict],  # env state dict at each executed step
        char_id: int,
    ) -> DiagnosisResult:
        """
        Diagnose the error and return a DiagnosisResult with the replan window.
        """
        failed_action = actions[error_index]
        action_name = self._normalise_action_name(failed_action)

        # ---- Step 1: Classify error type --------------------------------
        if eai_error_code == MISSING_STEP_EAI_CODE:
            error_type = self._classify_missing_step(
                action_name, env_state_history, error_index, char_id, failed_action
            )
        else:
            error_type = EAI_CODE_TO_ERROR_TYPE.get(eai_error_code, ErrorType.UNKNOWN)

        # ---- Step 2: For env-state errors → local replan from error point
        if error_type == ErrorType.ENVIRONMENT_STATE:
            return DiagnosisResult(
                error_type=error_type,
                error_index=error_index,
                source_index=error_index,
                replan_start=error_index,
                replan_end=error_index,
            )

        # ---- Step 3: For precondition errors → backtrack to find root cause
        if error_type == ErrorType.ACTION_PRECONDITION:
            return self._backtrack_precondition(
                actions, error_index, env_state_history, char_id, failed_action
            )

        # ---- Step 4: Wrong order / affordance → local replan
        return DiagnosisResult(
            error_type=error_type,
            error_index=error_index,
            source_index=error_index,
            replan_start=error_index,
            replan_end=error_index,
        )

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------

    def _classify_missing_step(
        self,
        action_name: str,
        env_state_history: List[dict],
        error_index: int,
        char_id: int,
        failed_action: dict,
    ) -> ErrorType:
        """
        Distinguish between:
          - ENVIRONMENT_STATE: object isn't where we expected (positional mismatch)
          - ACTION_PRECONDITION: agent hand occupied, container closed, etc.
        """
        obj_id = failed_action.get("obj_id")
        preconditions = self.sdg.get_preconditions(action_name)

        if not preconditions:
            return ErrorType.ENVIRONMENT_STATE

        # Check each precondition against the state just before the error
        state_before = env_state_history[error_index] if error_index < len(env_state_history) else {}
        _, unsatisfied = self.sdg.check_preconditions(
            action_name, state_before, char_id, obj_id
        )

        # Categorise unsatisfied conditions
        agent_state_preds = {"sitting", "lying", "holds_lh", "holds_rh"}
        has_agent_pred = any(c.predicate in agent_state_preds for c in unsatisfied)
        has_positional = any(c.predicate in {"next_to", "inside"} for c in unsatisfied)

        if has_agent_pred:
            return ErrorType.ACTION_PRECONDITION
        elif has_positional:
            return ErrorType.ENVIRONMENT_STATE
        else:
            return ErrorType.ACTION_PRECONDITION

    # ------------------------------------------------------------------
    # Backtracking
    # ------------------------------------------------------------------

    def _backtrack_precondition(
        self,
        actions: List[dict],
        error_index: int,
        env_state_history: List[dict],
        char_id: int,
        failed_action: dict,
    ) -> DiagnosisResult:
        """
        Find tsource: the last timestep before error_index where the 
        required precondition changed from satisfied to unsatisfied.
        Then compute [tstart, tend] reconstruction window.
        """
        action_name = self._normalise_action_name(failed_action)
        obj_id = failed_action.get("obj_id")
        preconditions = self.sdg.get_preconditions(action_name)

        # Find the unsatisfied preconditions
        state_before_error = (
            env_state_history[error_index]
            if error_index < len(env_state_history)
            else {}
        )
        _, unsatisfied = self.sdg.check_preconditions(
            action_name, state_before_error, char_id, obj_id
        )

        # Find tsource: scan backwards to find when condition broke
        tsource = 0
        root_action = None
        for t in range(error_index - 1, -1, -1):
            state_t = env_state_history[t] if t < len(env_state_history) else {}
            _, still_unsatisfied = self.sdg.check_preconditions(
                action_name, state_t, char_id, obj_id
            )
            if len(still_unsatisfied) < len(unsatisfied):
                # Condition was satisfied at t, broken at t+1
                tsource = t + 1
                root_action = self._normalise_action_name(actions[tsource]) if tsource < len(actions) else None
                break

        # Compute tstart: walk backwards from tsource while actions are state-prep
        tstart = tsource
        for t in range(tsource - 1, -1, -1):
            a_name = self._normalise_action_name(actions[t])
            if self.sdg.is_state_prep(a_name):
                tstart = t
            else:
                break

        # Compute tend: walk forwards from error_index while objects match
        error_obj = failed_action.get("obj_name", "")
        tend = error_index
        for t in range(error_index + 1, len(actions)):
            a = actions[t]
            if a.get("obj_name", "") == error_obj or a.get("obj_name", "") in self._get_related_objects(unsatisfied):
                tend = t
            else:
                break

        return DiagnosisResult(
            error_type=ErrorType.ACTION_PRECONDITION,
            error_index=error_index,
            source_index=tsource,
            replan_start=tstart,
            replan_end=tend,
            unsatisfied_conditions=unsatisfied,
            root_action=root_action,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalise_action_name(self, action: dict) -> str:
        """
        Convert EAI action dict format to PDDL action name.
        EAI format: {'action': '[WALK]', 'obj': '<light> (64)'} or
                    {'action': 'WALK', 'obj': 'light'}
        """
        if isinstance(action, str):
            return action.lower().strip("[]")
        raw = action.get("action", "")
        # Strip brackets: [WALK] → walk
        name = raw.strip("[]").lower().replace(" ", "_")
        # Map EAI names → PDDL names
        name_map = {
            "walk":         "walk_towards",
            "walktowards":  "walk_towards",
            "walkinto":     "walk_into",
            "switchon":     "switch_on",
            "switchoff":    "switch_off",
            "puton":        "put_on",
            "putinside":    "put_inside",
            "plugin":       "plug_in",
            "plugout":      "plug_out",
            "turnto":       "turn_to",
            "lookat":       "look_at",
            "wakeup":       "wake_up",
            "grab":         "grab",
            "open":         "open",
            "close":        "close",
            "sit":          "sit",
            "standup":      "standup",
            "read":         "read",
            "touch":        "touch",
            "lie":          "lie",
            "pour":         "pour",
            "type":         "type",
            "watch":        "watch",
            "move":         "move",
            "wash":         "wash",
            "squeeze":      "squeeze",
            "cut":          "cut",
            "eat":          "eat",
            "sleep":        "sleep",
            "wipe":         "wipe",
            "drop":         "drop",
            "find":         "find",
        }
        return name_map.get(name, name)

    def _get_related_objects(self, conditions: List[StateCondition]) -> List[str]:
        """Extract object names referenced in the unsatisfied conditions."""
        objects = []
        for cond in conditions:
            objects.extend(cond.args)
        return objects
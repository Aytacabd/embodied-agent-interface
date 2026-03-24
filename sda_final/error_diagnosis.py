"""
error_diagnosis.py  —  Error Backtrack & Diagnosis for SDA-Planner

When an action fails during execution, this module:

  1. Classifies the error as either:
       - ENV_STATE_ERROR      : unexpected object placement / missing object
                                (maps to EAI error codes: WRONG_ORDER,
                                 UNSEEN_OBJECT, AFFORDANCE_ERROR)
       - PRECONDITION_ERROR   : action precondition violated because a
                                prior action left the agent in the wrong
                                state  (maps to EAI: MISSING_STEP)

  2. For PRECONDITION_ERROR, localises the minimal subsequence that must
     be reconstructed:  window [t_start, t_end]

     Following the paper's formulas:
       t_error  = timestep of the failed action (1-based)
       t_source = most recent t < t_error where s_error changed from
                  satisfied → unsatisfied  (eq. 2 & 3)
       t_start  = earliest t ≤ t_source such that all actions in
                  [t, t_source) are state-prep actions  (eq. 4)
       t_end    = latest t > t_error such that all objects in
                  (t_error, t] are in the error object set O  (eq. 4)

  3. Returns a DiagnosisResult describing the error type and the
     reconstruction window so that adaptive_subtree.py can act.

EAI error code mapping (from evaluate_results.py):
    0 → WRONG_TEMPORAL_ORDER  → treat as ENV_STATE_ERROR
    1 → MISSING_STEP          → treat as PRECONDITION_ERROR
    2 → AFFORDANCE_ERROR      → treat as ENV_STATE_ERROR
    3 → UNSEEN_OBJECT         → treat as ENV_STATE_ERROR (hallucination)
    4 → ADDITIONAL_STEP       → skip and continue (no replanning needed)
    5 → UNKNOWN_ERROR         → treat as ENV_STATE_ERROR
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Set

from sdg import StateDependencyGraph, StateNode
from state_tracker import StateTracker, action_to_pddl_name, action_objects


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class ErrorType(Enum):
    ENV_STATE_ERROR    = auto()   # local replan from current step
    PRECONDITION_ERROR = auto()   # SDG backtrack → reconstruct window
    ADDITIONAL_STEP    = auto()   # benign: goal already met, skip
    GRAMMAR_ERROR      = auto()   # hallucination / parse / param error


# EAI integer error codes → ErrorType
EAI_CODE_TO_ERROR_TYPE = {
    0: ErrorType.ENV_STATE_ERROR,    # WRONG_TEMPORAL_ORDER
    1: ErrorType.PRECONDITION_ERROR, # MISSING_STEP
    2: ErrorType.ENV_STATE_ERROR,    # AFFORDANCE_ERROR
    3: ErrorType.ENV_STATE_ERROR,    # UNSEEN_OBJECT
    4: ErrorType.ADDITIONAL_STEP,    # ADDITIONAL_STEP
    5: ErrorType.ENV_STATE_ERROR,    # UNKNOWN_ERROR
}


@dataclass
class DiagnosisResult:
    """Everything the adaptive replanner needs to know about a failure."""
    error_type: ErrorType

    # The action that failed (EAI list format)
    failed_action: list
    # 1-based index of the failed action in the full plan
    t_error: int

    # Only set for PRECONDITION_ERROR
    violated_states: List[StateNode] = field(default_factory=list)
    t_source: int = 0       # timestep where the corruption started
    t_start: int = 0        # start of reconstruction window (inclusive)
    t_end: int = 0          # end of reconstruction window (inclusive)

    # Error object set O (object ids involved in the error window)
    error_object_ids: Set[str] = field(default_factory=set)

    def needs_reconstruction(self) -> bool:
        return self.error_type == ErrorType.PRECONDITION_ERROR

    def needs_local_replan(self) -> bool:
        return self.error_type == ErrorType.ENV_STATE_ERROR

    def is_benign(self) -> bool:
        return self.error_type == ErrorType.ADDITIONAL_STEP

    def __repr__(self):
        if self.error_type == ErrorType.PRECONDITION_ERROR:
            return (
                f"DiagnosisResult(PRECONDITION_ERROR, "
                f"t_error={self.t_error}, t_source={self.t_source}, "
                f"window=[{self.t_start},{self.t_end}], "
                f"violated={self.violated_states})"
            )
        return (
            f"DiagnosisResult({self.error_type.name}, "
            f"t_error={self.t_error}, action={self.failed_action})"
        )


# ---------------------------------------------------------------------------
# Diagnosis engine
# ---------------------------------------------------------------------------

class ErrorDiagnosis:
    """
    Implements the Error Backtrack and Diagnosis module from the paper.

    Usage
    -----
    diagnosis = ErrorDiagnosis(sdg, state_tracker)
    result = diagnosis.diagnose(
        failed_action    = action,        # EAI action list
        t_error          = current_t,     # 1-based step number
        eai_error_code   = failed_code,   # integer from TemporalOrderChecker
        remaining_plan   = future_actions # actions not yet executed
    )
    """

    def __init__(self, sdg: StateDependencyGraph, tracker: StateTracker):
        self.sdg = sdg
        self.tracker = tracker

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def diagnose(
        self,
        failed_action: list,
        t_error: int,
        eai_error_code: int,
        remaining_plan: Optional[List[list]] = None,
    ) -> DiagnosisResult:
        """
        Classify the error and, for precondition errors, compute the
        minimal reconstruction window [t_start, t_end].
        """
        remaining_plan = remaining_plan or []
        error_type = EAI_CODE_TO_ERROR_TYPE.get(eai_error_code,
                                                  ErrorType.ENV_STATE_ERROR)

        if error_type == ErrorType.ADDITIONAL_STEP:
            return DiagnosisResult(
                error_type=ErrorType.ADDITIONAL_STEP,
                failed_action=failed_action,
                t_error=t_error,
            )

        if error_type == ErrorType.ENV_STATE_ERROR:
            return DiagnosisResult(
                error_type=ErrorType.ENV_STATE_ERROR,
                failed_action=failed_action,
                t_error=t_error,
            )

        # --- PRECONDITION_ERROR path ---
        return self._diagnose_precondition_error(
            failed_action, t_error, remaining_plan
        )

    # ------------------------------------------------------------------
    # Precondition error path
    # ------------------------------------------------------------------

    def _diagnose_precondition_error(
        self,
        failed_action: list,
        t_error: int,
        remaining_plan: List[list],
    ) -> DiagnosisResult:
        """
        Implement Algorithm 1 from SDA-Planner paper.

        Steps:
          1. Identify which preconditions of failed_action are violated
          2. For each violated state, find t_source (eq. 2, 3)
          3. Compute t_start by walking backwards from t_source  (eq. 4)
          4. Compute t_end by walking forwards from t_error  (eq. 4)
        """
        pddl_name = action_to_pddl_name(failed_action)
        # Use state at t_error - 1 (the last good state) for checking
        state_before_error = self.tracker.state_at(t_error - 1)
        _, violated = self.sdg.preconditions_satisfied(pddl_name, state_before_error)

        if not violated:
            # SDG doesn't identify a specific precondition —
            # fall back to env state error (local replan)
            return DiagnosisResult(
                error_type=ErrorType.ENV_STATE_ERROR,
                failed_action=failed_action,
                t_error=t_error,
            )

        # --- Determine t_source for each violated precondition ---
        # Use the earliest t_source among all violated states (most conservative)
        t_source = t_error - 1   # default: just before error
        primary_violated = violated[0]

        for s in violated:
            ts = self.tracker.find_last_corruption(
                predicate=s.predicate,
                needed_value=s.value,
                before_t=t_error
            )
            if ts < t_source:
                t_source = ts
                primary_violated = s

        t_source = max(0, t_source)

        # --- Compute t_start (eq. 4) ---
        # Walk backwards from t_source; extend while actions are state-prep
        t_start = t_source
        for t in range(t_source, 0, -1):
            if self.tracker.is_state_prep_at(t):
                t_start = t - 1   # include this state-prep action
            else:
                break
        t_start = max(1, t_start)   # t=0 is the initial state, not an action

        # --- Compute t_end (eq. 4) ---
        # Collect the error object set O:
        # objects involved in the failed action + precondition state objects
        error_object_ids: Set[str] = set()
        for _, oid in action_objects(failed_action):
            error_object_ids.add(oid)
        # Also include objects involved in the reconstruction window so far
        for _, oid in action_objects(
            self.tracker.history_actions[t_source - 1]
            if t_source >= 1 and t_source - 1 < len(self.tracker.history_actions)
            else []
        ):
            error_object_ids.add(oid)

        # Walk forward through remaining plan; include steps that touch
        # the same objects
        t_end = t_error
        for i, future_action in enumerate(remaining_plan):
            future_obj_ids = {
                oid for _, oid in action_objects(future_action)
            }
            if future_obj_ids & error_object_ids:
                t_end = t_error + i + 1
            else:
                break   # first action not touching error objects stops expansion

        # Check if the violated state only involves one state-prep action
        # In that case we can just INSERT rather than reconstruct (paper §4.3)
        # We signal this by setting t_start == t_error == t_end
        if self._is_simple_agent_state_violation(primary_violated, t_source):
            t_start = t_error
            t_end   = t_error

        return DiagnosisResult(
            error_type=ErrorType.PRECONDITION_ERROR,
            failed_action=failed_action,
            t_error=t_error,
            violated_states=violated,
            t_source=t_source,
            t_start=t_start,
            t_end=t_end,
            error_object_ids=error_object_ids,
        )

    def _is_simple_agent_state_violation(
        self, violated_state: StateNode, t_source: int
    ) -> bool:
        """
        Return True if the violation can be fixed by inserting a single
        state-prep action (the paper's "only one outgoing edge to an
        agent state node and no incoming edges" case).
        """
        if not violated_state.agent_state:
            return False
        # Check if there's exactly one action that can produce this state
        # and it is a state-prep action
        producers = self.sdg.actions_that_produce(
            violated_state.predicate, violated_state.value
        )
        if len(producers) == 1 and self.sdg.is_state_prep_action(producers[0]):
            return True
        return False

    # ------------------------------------------------------------------
    # Convenience: build context string for the LLM
    # ------------------------------------------------------------------

    def build_error_context(self, result: DiagnosisResult) -> str:
        """
        Return a human-readable description of the diagnosis for use
        in the LLM replanning prompt.
        """
        lines = []
        action_str = " ".join(str(x) for x in result.failed_action)
        lines.append(f"Error occurred at action: {action_str}")
        lines.append(f"Timestep: t={result.t_error}")

        if result.error_type == ErrorType.PRECONDITION_ERROR:
            lines.append("Error type: action precondition violation")
            for s in result.violated_states:
                sign = "" if s.value else "NOT "
                lines.append(f"  Violated precondition: {sign}{s.predicate}")
            lines.append(
                f"Root cause traced to timestep t={result.t_source}"
            )
            if result.t_start == result.t_error:
                lines.append(
                    "Fix: insert a state preparation action before the failed action."
                )
            else:
                lines.append(
                    f"Subsequence to reconstruct: steps {result.t_start} "
                    f"to {result.t_end} (inclusive)"
                )
        elif result.error_type == ErrorType.ENV_STATE_ERROR:
            lines.append(
                "Error type: environment state mismatch "
                "(object missing / unexpected position)."
            )
            lines.append(
                "Fix: generate additional steps from the current state."
            )
        return "\n".join(lines)

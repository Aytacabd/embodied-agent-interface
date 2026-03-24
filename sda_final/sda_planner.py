"""
sda_planner.py  —  SDA-Planner: State-Dependency Aware Adaptive Planner

Drop-in replacement for the execution loop inside EAI's evaluate_results.py.

Usage
-----
Replace the inner execution block in evaluate_results.py:

    # BEFORE (EAI baseline):
    for action in actions:
        exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)
        if not exe_flag:
            ...
            break

    # AFTER (SDA-Planner):
    from sda_planner import SDAPlanner
    sda = SDAPlanner(pddl_path="path/to/virtualhome.pddl")
    executable, history_actions, error_info_entry = sda.run(
        actions        = actions,
        motion_planner = motion_planner,
        instruction    = task_name,
    )

The SDAPlanner.run() method returns the same data that evaluate_results.py
expects:
    executable    (bool)           : True if plan executed without fatal error
    history_actions (List[list])   : successfully executed actions
    error_info_entry (dict)        : {executable, actions, error_type, error_action}

Additionally, the planner tracks:
    sda.num_error_corrections      : No. EC metric from the paper

Public configuration
--------------------
    max_replan_attempts  (int, default 3)
    max_bfs_depth        (int, default 8)
    pddl_path            (str)
    verbose              (bool, default False)
"""

from __future__ import annotations

import copy
import logging
import os
from typing import Dict, List, Optional, Tuple

from sdg import StateDependencyGraph
from state_tracker import StateTracker, action_to_pddl_name
from error_diagnosis import ErrorDiagnosis, ErrorType, DiagnosisResult
from adaptive_subtree import AdaptiveSubtreeGenerator

logger = logging.getLogger(__name__)


# EAI error code constants (from evaluate_results.py)
WRONG_TEMPORAL_ORDER = 0
MISSING_STEP         = 1
AFFORDANCE_ERROR     = 2
UNSEEN_OBJECT        = 3
ADDITIONAL_STEP_CODE = 4
UNKNOWN_ERROR        = 5


class SDAPlanner:
    """
    State-Dependency Aware Adaptive Planner.

    Parameters
    ----------
    pddl_path           : path to virtualhome.pddl
    max_replan_attempts : maximum replanning attempts per task  (default 3)
    max_bfs_depth       : BFS depth limit in subtree generation (default 8)
    verbose             : enable debug logging
    """

    def __init__(
        self,
        pddl_path: str,
        max_replan_attempts: int = 3,
        max_bfs_depth: int = 8,
        verbose: bool = False,
    ):
        if verbose:
            logging.basicConfig(level=logging.DEBUG)

        logger.info(f"Building State-Dependency Graph from {pddl_path}")
        self.sdg = StateDependencyGraph.from_pddl(pddl_path)
        logger.info(
            f"SDG built: {len(self.sdg.actions)} actions, "
            f"{sum(1 for n in self.sdg.actions.values() if n.is_state_prep)} "
            f"state-prep actions"
        )
        self.max_replan_attempts = max_replan_attempts
        self.max_bfs_depth       = max_bfs_depth

        # Per-run statistics (reset on each call to run())
        self.num_error_corrections = 0
        self._replan_log: List[dict] = []

    # ------------------------------------------------------------------
    # Main execution loop
    # ------------------------------------------------------------------

    def run(
        self,
        actions: List[list],
        motion_planner,
        instruction: str = "",
        checker_class=None,         # TemporalOrderChecker from EAI (optional)
    ) -> Tuple[bool, List[list], dict]:
        """
        Execute a plan with adaptive replanning.

        Parameters
        ----------
        actions         : initial action plan  (EAI list format)
        motion_planner  : EAI motion_planner object with
                          .my_execute_primitive_action_eval()
                          .env_state  (property returning current state)
                          .env_state.to_dict()
        instruction     : natural language task description (for LLM prompt)
        checker_class   : optional TemporalOrderChecker class for EAI-compatible
                          error code detection

        Returns
        -------
        (executable, history_actions, error_info_entry)
        """
        self.num_error_corrections = 0
        self._replan_log = []

        # Initialise state tracker
        initial_state = copy.deepcopy(motion_planner.env_state.to_dict())
        tracker = StateTracker(self.sdg, initial_state)

        # Initialise modules
        diagnosis_engine = ErrorDiagnosis(self.sdg, tracker)
        subtree_gen = AdaptiveSubtreeGenerator(
            sdg=self.sdg,
            tracker=tracker,
            motion_planner=motion_planner,
            max_depth=self.max_bfs_depth,
        )

        # Working plan (may be modified by replanning)
        current_plan = list(actions)
        executable   = True
        error_action = None
        failed_error_code = UNKNOWN_ERROR

        # ---------------------------------------------------------------
        # Execution loop
        # ---------------------------------------------------------------
        action_idx = 0

        while action_idx < len(current_plan):
            action = current_plan[action_idx]
            logger.debug(f"Executing t={action_idx + 1}: {action}")

            # Snapshot history before execution  (for checker)
            history_env_states_cp = copy.deepcopy(tracker.get_history_env_states())

            exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(
                action
            )

            if exe_flag:
                # Success — record and advance
                new_env_state = copy.deepcopy(motion_planner.env_state.to_dict())
                tracker.record_success(action, new_env_state)
                action_idx += 1
                logger.debug(f"  → success (t={tracker.current_t})")
                continue

            # ---- Action failed ----------------------------------------
            logger.info(f"Action failed at idx={action_idx}: {action}")
            logger.debug(f"  my_info={my_info}")

            # Determine EAI error code using checker if available
            error_code = self._get_error_code(
                my_info, history_env_states_cp, checker_class
            )
            failed_error_code = error_code
            error_action      = action

            # t_error is 1-based (= number of actions attempted so far)
            t_error = action_idx + 1

            # Benign additional-step: skip and continue
            if error_code == ADDITIONAL_STEP_CODE:
                logger.info("  → additional step error (benign), skipping action")
                current_plan.pop(action_idx)
                continue

            # Check replan budget
            if self.num_error_corrections >= self.max_replan_attempts:
                logger.info(
                    f"  → replan budget exhausted "
                    f"({self.max_replan_attempts} attempts used)"
                )
                executable = False
                break

            # ---- Diagnose --------------------------------------------
            remaining = current_plan[action_idx + 1:]
            diagnosis = diagnosis_engine.diagnose(
                failed_action=action,
                t_error=t_error,
                eai_error_code=error_code,
                remaining_plan=remaining,
            )
            logger.info(f"  → diagnosis: {diagnosis}")

            self.num_error_corrections += 1
            self._replan_log.append({
                "attempt": self.num_error_corrections,
                "t_error": t_error,
                "action": action,
                "error_code": error_code,
                "diagnosis": repr(diagnosis),
            })

            # ---- Replan ----------------------------------------------
            new_plan, replan_ok = subtree_gen.generate(
                diagnosis=diagnosis,
                full_plan=current_plan,
                instruction=instruction,
            )

            if not replan_ok:
                logger.info("  → replanning failed, aborting execution")
                executable = False
                break

            logger.info(
                f"  → replanning succeeded "
                f"(attempt {self.num_error_corrections})"
            )
            current_plan = new_plan

            # After replanning, reset action_idx to t_start - 1 so we
            # re-execute from the beginning of the reconstructed window
            if diagnosis.error_type == ErrorType.PRECONDITION_ERROR:
                # t_start is 1-based; convert to 0-based index
                action_idx = max(0, diagnosis.t_start - 1)
                # Sync tracker to the state at t_start
                # (reverse_execute already rolled back tracker internally)
                logger.debug(f"  Resuming from action_idx={action_idx}")
            else:
                # ENV_STATE_ERROR: re-try from the same position
                # (corrective actions were inserted before action_idx)
                pass   # action_idx unchanged; new actions are at same position

        # ---------------------------------------------------------------
        # Build result
        # ---------------------------------------------------------------
        history_actions = tracker.get_history_actions()

        error_info_entry: dict
        if executable:
            error_info_entry = {
                "executable": True,
                "actions": history_actions,
                "error_type": None,
                "error_action": None,
                "num_error_corrections": self.num_error_corrections,
            }
        else:
            error_type_name = self._error_code_to_name(failed_error_code)
            error_info_entry = {
                "executable": False,
                "actions": history_actions,
                "error_type": error_type_name,
                "error_action": error_action,
                "num_error_corrections": self.num_error_corrections,
            }

        logger.info(
            f"SDA-Planner finished: executable={executable}, "
            f"No.EC={self.num_error_corrections}, "
            f"steps_executed={len(history_actions)}"
        )
        return executable, history_actions, error_info_entry

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_error_code(
        self,
        my_info,
        history_env_states_cp: list,
        checker_class,
    ) -> int:
        """Use EAI's TemporalOrderChecker if available, else infer from my_info."""
        if checker_class is not None:
            try:
                checker = checker_class(my_info, history_env_states_cp)
                formal_info = checker.run_checker()
                return formal_info.get_error_type()
            except Exception as e:
                logger.warning(f"TemporalOrderChecker failed: {e}")

        # Fallback: inspect my_info string for keywords
        info_str = str(my_info).lower()
        if "missing" in info_str or "precondition" in info_str:
            return MISSING_STEP
        if "order" in info_str or "temporal" in info_str:
            return WRONG_TEMPORAL_ORDER
        if "affordance" in info_str or "property" in info_str:
            return AFFORDANCE_ERROR
        if "additional" in info_str or "already" in info_str:
            return ADDITIONAL_STEP_CODE
        return MISSING_STEP   # default to most common error type

    @staticmethod
    def _error_code_to_name(code: int) -> str:
        return {
            0: "wrong_temporal_order",
            1: "missing_step",
            2: "affordance_error",
            3: "unseen_object",
            4: "additional_step",
            5: "unknown_error",
        }.get(code, "unknown_error")

    def get_replan_log(self) -> List[dict]:
        """Return per-task replanning log for analysis."""
        return list(self._replan_log)

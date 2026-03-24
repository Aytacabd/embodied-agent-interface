"""
Adaptive Action SubTree Generation and SDA-Planner main loop.

Integrates with EAI's evaluate_results pipeline for VirtualHome.
Replaces static LLM action sequences with adaptive, error-aware execution.
"""

import copy
import json
import logging
from typing import Dict, List, Optional, Tuple

from sda_planner.state_dependency_graph import StateDependencyGraph, build_sdg
from sda_planner.error_diagnosis import ErrorDiagnoser, DiagnosisResult, ErrorType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action format helpers
# ---------------------------------------------------------------------------

def parse_action(action_raw) -> Dict:
    """
    Normalise an EAI action into a unified dict:
    {'action': str, 'obj_name': str, 'obj_id': int or None}
    
    EAI formats seen in llm_outputs:
      - "[WALK] <light> (64)"       (string format)
      - {"action": "WALK", "obj": "light (64)"}   (dict format)
    """
    if isinstance(action_raw, str):
        # String format: "[WALK] <light> (64)"
        import re
        action_match = re.match(r"\[(\w+)\]", action_raw)
        obj_match = re.search(r"<([\w\s]+)>", action_raw)
        id_match = re.search(r"\((\d+)\)", action_raw)
        return {
            "action": action_match.group(1) if action_match else "",
            "obj_name": obj_match.group(1).strip() if obj_match else "",
            "obj_id": int(id_match.group(1)) if id_match else None,
            "raw": action_raw,
        }
    elif isinstance(action_raw, dict):
        # Dict format
        obj_str = action_raw.get("obj", action_raw.get("object", ""))
        import re
        id_match = re.search(r"\((\d+)\)", str(obj_str))
        obj_name = re.sub(r"\s*\(\d+\)", "", str(obj_str)).strip()
        return {
            "action": action_raw.get("action", ""),
            "obj_name": obj_name,
            "obj_id": int(id_match.group(1)) if id_match else None,
            "raw": action_raw,
        }
    return {"action": "", "obj_name": "", "obj_id": None, "raw": action_raw}


def format_action(action_dict: Dict) -> str:
    """Convert unified action dict back to EAI string format."""
    name = action_dict["action"].upper()
    obj = action_dict["obj_name"]
    obj_id = action_dict.get("obj_id")
    if obj_id is not None:
        return f"[{name}] <{obj}> ({obj_id})"
    return f"[{name}] <{obj}>"


# ---------------------------------------------------------------------------
# Adaptive SubTree Generator
# ---------------------------------------------------------------------------

class AdaptiveSubTreeGenerator:
    """
    Reconstructs the failed subsequence [tstart, tend] using:
    1. SDG-constrained BFS to enforce preconditions
    2. LLM suggestions for corrective actions (optional)
    3. Original subsequence actions as fallback candidates
    """

    def __init__(self, sdg: StateDependencyGraph):
        self.sdg = sdg

    def generate(
        self,
        actions: List[Dict],
        diagnosis: DiagnosisResult,
        env_state: Dict,
        char_id: int,
        motion_planner,
    ) -> List[Dict]:
        """
        Generate a corrected action subsequence for [replan_start, replan_end].
        Returns the full corrected action list.
        """
        tstart = diagnosis.replan_start
        tend = diagnosis.replan_end
        error_idx = diagnosis.error_index

        logger.info(
            f"[SDA] Generating subtree for [{tstart}, {tend}], "
            f"error_type={diagnosis.error_type.value}"
        )

        # ---- Case 1: Environment state error → insert navigation step -----
        if diagnosis.error_type == ErrorType.ENVIRONMENT_STATE:
            return self._fix_env_state_error(actions, error_idx, env_state, char_id)

        # ---- Case 2: Action precondition error → reconstruct subsequence ---
        return self._fix_precondition_error(
            actions, diagnosis, env_state, char_id, motion_planner
        )

    # ------------------------------------------------------------------
    # Environment state error: insert missing prep action
    # ------------------------------------------------------------------

    def _fix_env_state_error(
        self,
        actions: List[Dict],
        error_idx: int,
        env_state: Dict,
        char_id: int,
    ) -> List[Dict]:
        """
        For environment state errors, insert a WALK action before the
        failed action so the agent navigates to the object.
        """
        failed = actions[error_idx]
        obj_name = failed.get("obj_name", "")
        obj_id = failed.get("obj_id")

        walk_action = {
            "action": "WALK",
            "obj_name": obj_name,
            "obj_id": obj_id,
            "raw": f"[WALK] <{obj_name}> ({obj_id})",
            "inserted_by_sda": True,
        }

        corrected = actions[:error_idx] + [walk_action] + actions[error_idx:]
        logger.info(f"[SDA] Inserted WALK before action at index {error_idx}")
        return corrected

    # ------------------------------------------------------------------
    # Precondition error: reconstruct [tstart, tend] subsequence
    # ------------------------------------------------------------------

    def _fix_precondition_error(
        self,
        actions: List[Dict],
        diagnosis: DiagnosisResult,
        env_state: Dict,
        char_id: int,
        motion_planner,
    ) -> List[Dict]:
        """
        Reconstruct the subsequence between tstart and tend using
        SDG-constrained candidate generation.
        """
        tstart = diagnosis.replan_start
        tend = diagnosis.replan_end
        error_idx = diagnosis.error_index
        unsatisfied = diagnosis.unsatisfied_conditions

        # Build candidate actions for the reconstruction
        candidates = self._build_candidate_actions(actions, tstart, tend, unsatisfied)

        # SDG-constrained BFS to find valid subsequence
        corrected_subseq = self._bfs_valid_subsequence(
            candidates, unsatisfied, env_state, char_id, actions[tend]["obj_name"] if tend < len(actions) else ""
        )

        if not corrected_subseq:
            # Fallback: just insert missing walk/open actions
            corrected_subseq = self._fallback_fix(actions, tstart, tend, unsatisfied)

        # Assemble full corrected action list:
        # prefix + corrected_subseq + suffix (from tend+1 onwards)
        prefix = actions[:tstart]
        suffix = actions[tend + 1:] if tend + 1 < len(actions) else []
        corrected = prefix + corrected_subseq + suffix

        logger.info(
            f"[SDA] Reconstructed subsequence: {len(corrected_subseq)} actions "
            f"replacing original {tend - tstart + 1} actions"
        )
        return corrected

    def _build_candidate_actions(
        self,
        actions: List[Dict],
        tstart: int,
        tend: int,
        unsatisfied,
    ) -> List[Dict]:
        """
        Candidate pool = original subsequence + corrective insertions.
        Corrective insertions are derived from SDG: for each unsatisfied
        precondition, find producer actions and add them as candidates.
        """
        original_subseq = actions[tstart : tend + 1]
        candidates = list(original_subseq)

        for cond in unsatisfied:
            producers = self.sdg.get_producers(cond.predicate)
            for producer in producers:
                # Create a corrective action with same object as original
                if original_subseq:
                    ref_action = original_subseq[0]
                    corrective = {
                        "action": producer.upper().replace("_", ""),
                        "obj_name": ref_action.get("obj_name", ""),
                        "obj_id": ref_action.get("obj_id"),
                        "raw": f"[{producer.upper()}] <{ref_action.get('obj_name','')}>",
                        "inserted_by_sda": True,
                    }
                    candidates.append(corrective)

        return candidates

    def _bfs_valid_subsequence(
        self,
        candidates: List[Dict],
        unsatisfied,
        env_state: Dict,
        char_id: int,
        target_obj: str,
    ) -> List[Dict]:
        """
        BFS over candidate actions to find a subsequence that satisfies
        all preconditions per the SDG.
        Uses a simple greedy approach: insert prep actions before each
        candidate that requires them.
        """
        result = []
        for action in candidates:
            action_name_raw = action.get("action", "")
            # Normalise action name for SDG lookup
            pddl_name = self._to_pddl_name(action_name_raw)
            required_preps = self.sdg.get_required_prep_actions(pddl_name)

            # Insert walk if required and not already last action
            if "walk_towards" in required_preps:
                if not result or self._to_pddl_name(result[-1].get("action", "")) != "walk_towards":
                    walk = {
                        "action": "WALK",
                        "obj_name": action.get("obj_name", ""),
                        "obj_id": action.get("obj_id"),
                        "raw": f"[WALK] <{action.get('obj_name','')}>",
                        "inserted_by_sda": True,
                    }
                    result.append(walk)

            result.append(action)

        return result

    def _fallback_fix(
        self,
        actions: List[Dict],
        tstart: int,
        tend: int,
        unsatisfied,
    ) -> List[Dict]:
        """
        Simple fallback: return original subsequence with WALK prepended.
        """
        subseq = actions[tstart : tend + 1]
        if subseq:
            first_obj = subseq[0].get("obj_name", "")
            first_id = subseq[0].get("obj_id")
            walk = {
                "action": "WALK",
                "obj_name": first_obj,
                "obj_id": first_id,
                "raw": f"[WALK] <{first_obj}> ({first_id})",
                "inserted_by_sda": True,
            }
            return [walk] + subseq
        return subseq

    def _to_pddl_name(self, action_str: str) -> str:
        name = action_str.strip("[]").lower().replace(" ", "_")
        name_map = {
            "walk":        "walk_towards",
            "switchon":    "switch_on",
            "switchoff":   "switch_off",
            "puton":       "put_on",
            "putinside":   "put_inside",
            "plugin":      "plug_in",
            "plugout":     "plug_out",
            "turnto":      "turn_to",
            "lookat":      "look_at",
            "wakeup":      "wake_up",
        }
        return name_map.get(name, name)


# ---------------------------------------------------------------------------
# Main SDA-Planner
# ---------------------------------------------------------------------------

class SDAPlanner:
    """
    SDA-Planner: State-Dependency Aware Adaptive Planner for VirtualHome.
    
    Wraps the EAI action_sequencing execution loop with adaptive replanning.
    Drop-in replacement for the static execution in evaluate_results.py.
    """

    MAX_REPLAN_ATTEMPTS = 5  # prevent infinite loops

    def __init__(self, pddl_path: str):
        self.sdg = build_sdg(pddl_path)
        self.diagnoser = ErrorDiagnoser(self.sdg)
        self.subtree_gen = AdaptiveSubTreeGenerator(self.sdg)
        self.replan_count = 0

    def execute(
        self,
        actions_raw: List,
        motion_planner,
        char_id: int,
        formal_checker,
    ) -> Tuple[bool, List, int, Dict]:
        """
        Execute a plan with adaptive replanning.

        Args:
            actions_raw:     List of actions from LLM (EAI format)
            motion_planner:  EAI motion_planner object
            char_id:         Character/agent ID
            formal_checker:  EAI TemporalOrderChecker

        Returns:
            (executable, history_actions, replan_count, error_info)
        """
        # Parse all actions into unified format
        actions = [parse_action(a) for a in actions_raw]

        history_actions = []
        history_env_states = []
        executable = True
        self.replan_count = 0
        error_info = {"error_type": None, "error_action": None}

        i = 0
        while i < len(actions):
            action = actions[i]
            action_raw = action.get("raw", actions_raw[i] if i < len(actions_raw) else "")

            # Try to execute action via EAI formal checker
            formal_info = formal_checker(action_raw, history_actions, history_env_states)

            if formal_info.is_valid():
                # Success: advance
                history_actions.append(action_raw)
                new_state = copy.deepcopy(motion_planner.env_state.to_dict())
                history_env_states.append(new_state)
                logger.debug(f"[SDA] Action {i} executed: {action_raw}")
                i += 1

            else:
                # Failure: attempt adaptive replanning
                eai_error_code = formal_info.get_error_type()
                logger.info(
                    f"[SDA] Action {i} failed (EAI code={eai_error_code}): {action_raw}"
                )

                if self.replan_count >= self.MAX_REPLAN_ATTEMPTS:
                    logger.warning("[SDA] Max replan attempts reached. Stopping.")
                    executable = False
                    error_info = {
                        "error_type": "max_replan_exceeded",
                        "error_action": str(action_raw),
                    }
                    break

                # Diagnose
                current_state = copy.deepcopy(motion_planner.env_state.to_dict())
                diagnosis = self.diagnoser.diagnose(
                    actions=actions,
                    error_index=i,
                    eai_error_code=eai_error_code,
                    env_state_history=history_env_states,
                    char_id=char_id,
                )
                logger.info(f"[SDA] Diagnosis: {diagnosis}")

                # Adapt plan
                corrected_actions = self.subtree_gen.generate(
                    actions=actions,
                    diagnosis=diagnosis,
                    env_state=current_state,
                    char_id=char_id,
                    motion_planner=motion_planner,
                )

                if corrected_actions == actions:
                    # No change possible — mark as non-executable
                    executable = False
                    error_info = {
                        "error_type": diagnosis.error_type.value,
                        "error_action": str(action_raw),
                    }
                    break

                # Apply corrected plan and re-execute from replan_start
                actions = corrected_actions
                i = diagnosis.replan_start
                self.replan_count += 1
                logger.info(
                    f"[SDA] Replanning #{self.replan_count}, "
                    f"resuming from index {i}"
                )

        return executable, history_actions, self.replan_count, error_info


# ---------------------------------------------------------------------------
# Integration helper for evaluate_results.py
# ---------------------------------------------------------------------------

def make_sda_executor(pddl_path: str) -> SDAPlanner:
    """
    Factory function. Call once per evaluation run.
    
    Usage in evaluate_results.py:
        from sda_planner.adaptive_planner import make_sda_executor
        sda = make_sda_executor("examples/virtualhome.pddl")
        
        # Replace the inner execution loop with:
        executable, history_actions, replan_count, err = sda.execute(
            actions, motion_planner, motion_planner.acting_char_id, formal_checker
        )
    """
    return SDAPlanner(pddl_path)
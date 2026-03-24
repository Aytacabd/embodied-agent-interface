"""
adaptive_subtree.py  —  Adaptive Action SubTree Generation for SDA-Planner

Given a DiagnosisResult that specifies a reconstruction window [t_start, t_end],
this module: salam

  1. Calls GPT-4o to propose corrective actions for the failed subsequence
  2. Combines LLM suggestions with the original subsequence as candidate nodes
  3. Builds a constrained search tree where each node's children are filtered
     by the State-Dependency Graph  (eq. 5, 6 in the paper)
  4. Runs BFS to extract the shortest valid executable subsequence
  5. Splices the new subsequence back into the full plan

The module also handles:
  - Reverse execution (backtracking physical state to t_start)
  - Fake execution (skipping irreversible actions already done)

GPT-4o API call follows the same prompt structure as EAI's one_shot.py
so the LLM output can be parsed by the same json_to_action helper.
"""

from __future__ import annotations

import copy
import json
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from openai import OpenAI

from sdg import StateDependencyGraph, StateNode
from state_tracker import (
    StateTracker,
    action_to_pddl_name,
    action_objects,
    normalise_action_name,
    EAI_TO_PDDL,
)
from error_diagnosis import DiagnosisResult, ErrorType


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

# def _get_openai_client() -> OpenAI:
#     api_key = os.environ.get("OPENAI_API_KEY", "")
#     return OpenAI(api_key=api_key)
from groq import Groq

def _get_openai_client():
    api_key = os.environ.get("GROQ_API_KEY", "")
    return Groq(api_key=api_key)


# ---------------------------------------------------------------------------
# LLM prompt for corrective action suggestion
# ---------------------------------------------------------------------------

REPLAN_SYSTEM_PROMPT = """\
You are a robot action planner for the VirtualHome simulator.
Your task is to suggest corrective actions to fix a failed action sequence.
Output ONLY a JSON object mapping action names to their object arguments.
Use uppercase action names. Include object names and IDs.
Example: {"WALK": ["chair", "1"], "GRAB": ["cup", "2"]}
"""

REPLAN_USER_TEMPLATE = """\
The robot was executing a task but encountered an error.

TASK INSTRUCTION:
{instruction}

CURRENT ENVIRONMENT STATE:
{env_state_summary}

OBJECTS AVAILABLE IN SCENE:
{objects_in_scene}

ERROR DIAGNOSIS:
{error_context}

ORIGINAL FAILED SUBSEQUENCE (steps {t_start} to {t_end}):
{original_subseq}

SUPPORTED ACTIONS:
WALK (1 obj), FIND (1 obj), GRAB (1 obj, must be GRABBABLE),
OPEN (1 obj, must be CAN_OPEN), CLOSE (1 obj, must be CAN_OPEN),
PUTBACK (2 obj), PUTIN (2 obj, 2nd must be CAN_OPEN),
SWITCHON (1 obj, must be HAS_SWITCH), SWITCHOFF (1 obj, must be HAS_SWITCH),
DRINK (1 obj), TURNTO (1 obj), LOOKAT (1 obj), WIPE (1 obj),
DROP (1 obj), READ (1 obj, must be READABLE), TOUCH (1 obj),
LIE (1 obj, must be LIEABLE), SIT (1 obj, must be SITTABLE),
STANDUP (0 obj), POUR (2 obj), TYPE (1 obj), WATCH (1 obj),
MOVE (1 obj), WASH (1 obj), SQUEEZE (1 obj), PLUGIN (1 obj),
PLUGOUT (1 obj), CUT (1 obj), EAT (1 obj), RELEASE (1 obj).

IMPORTANT RULES:
1. Always WALK to an object before interacting with it.
2. Character can hold at most 2 objects (one in each hand).
3. To grab from a closed container, OPEN it first.
4. Output only the corrective subsequence, not the full plan.
5. Output ONLY valid JSON, nothing else.

OUTPUT (corrective action sequence as JSON):
"""


def _summarise_env_state(env_state: dict, relevant_ids: Set[str]) -> str:
    """Build a short natural-language summary of relevant objects."""
    lines = []
    for node in env_state.get("nodes", []):
        nid = str(node.get("id", ""))
        if nid not in relevant_ids and relevant_ids:
            continue
        name   = node.get("class_name", "unknown")
        states = node.get("states", [])
        props  = node.get("properties", [])
        lines.append(
            f"  {name} (id={nid}): states={states}, properties={props}"
        )
    return "\n".join(lines) if lines else "  (no relevant object info)"


def _summarise_objects(env_state: dict) -> str:
    """List all objects by name and id."""
    lines = []
    for node in env_state.get("nodes", []):
        nid  = str(node.get("id", ""))
        name = node.get("class_name", "unknown")
        lines.append(f"  {name} (id={nid})")
    return "\n".join(lines[:40])   # cap at 40 to avoid prompt bloat


def _actions_to_str(actions: List[list]) -> str:
    if not actions:
        return "  (none)"
    return "\n".join("  " + " ".join(str(x) for x in a) for a in actions)


def _parse_llm_action_json(raw: str) -> List[list]:
    """
    Parse the LLM's JSON output into EAI action list format.
    Returns [] on any parse failure.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return []
    actions = []
    for action_name, args in d.items():
        row = [action_name.upper()] + [str(a) for a in args]
        actions.append(row)
    return actions


# ---------------------------------------------------------------------------
# Search tree node
# ---------------------------------------------------------------------------

@dataclass
class TreeNode:
    action: list                          # EAI action list
    parent: Optional["TreeNode"] = None
    depth: int = 0
    # Simulated env state AFTER this action (shallow-ish copy)
    sim_state: Optional[dict] = None

    def path_to_root(self) -> List[list]:
        """Return the sequence of actions from root to this node."""
        node = self
        path = []
        while node.parent is not None:
            path.append(node.action)
            node = node.parent
        return list(reversed(path))


# ---------------------------------------------------------------------------
# Main module
# ---------------------------------------------------------------------------

class AdaptiveSubtreeGenerator:
    """
    Implements the Adaptive Action SubTree Generation module.

    Parameters
    ----------
    sdg          : StateDependencyGraph
    tracker      : StateTracker
    motion_planner : EAI motion planner object (for fake-execute and reset)
    max_depth    : BFS depth limit for the search tree
    """

    def __init__(
        self,
        sdg: StateDependencyGraph,
        tracker: StateTracker,
        motion_planner,
        max_depth: int = 8,
    ):
        self.sdg = sdg
        self.tracker = tracker
        self.motion_planner = motion_planner
        self.max_depth = max_depth
        self._client = _get_openai_client()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(
        self,
        diagnosis: DiagnosisResult,
        full_plan: List[list],
        instruction: str = "",
    ) -> Tuple[List[list], bool]:
        """
        Generate a corrected plan.

        Returns
        -------
        (new_full_plan, success)
            new_full_plan : the full plan with the window replaced
            success       : True if a valid subsequence was found
        """
        if diagnosis.error_type == ErrorType.ENV_STATE_ERROR:
            return self._local_replan(diagnosis, full_plan, instruction)

        if diagnosis.error_type == ErrorType.ADDITIONAL_STEP:
            # Skip the failed action and continue
            new_plan = [
                a for i, a in enumerate(full_plan)
                if i != diagnosis.t_error - 1
            ]
            return new_plan, True

        # PRECONDITION_ERROR → full window reconstruction
        return self._reconstruct_window(diagnosis, full_plan, instruction)

    # ------------------------------------------------------------------
    # Local replan  (ENV_STATE_ERROR)
    # ------------------------------------------------------------------

    def _local_replan(
        self,
        diagnosis: DiagnosisResult,
        full_plan: List[list],
        instruction: str,
    ) -> Tuple[List[list], bool]:
        """
        Ask the LLM to generate additional steps from the current state
        to get past the current failure point, then continue with the
        rest of the original plan.
        """
        corrective = self._call_llm_for_correction(
            diagnosis=diagnosis,
            original_subseq=[diagnosis.failed_action],
            instruction=instruction,
            t_start=diagnosis.t_error,
            t_end=diagnosis.t_error,
        )
        if not corrective:
            return full_plan, False

        # Splice: executed so far + corrective + rest of plan (skip failed action)
        t_err_idx = diagnosis.t_error - 1   # 0-based index in full_plan
        new_plan = (
            full_plan[:t_err_idx]
            + corrective
            + full_plan[t_err_idx + 1:]
        )
        return new_plan, True

    # ------------------------------------------------------------------
    # Window reconstruction  (PRECONDITION_ERROR)
    # ------------------------------------------------------------------

    def _reconstruct_window(
        self,
        diagnosis: DiagnosisResult,
        full_plan: List[list],
        instruction: str,
    ) -> Tuple[List[list], bool]:
        """
        Full reconstruction of the action subsequence in [t_start, t_end].

        Steps:
          1. Get LLM suggestions for corrective actions
          2. Build search tree candidate node set
          3. BFS with SDG constraints
          4. Reverse-execute the physical state back to t_start
          5. Return the spliced plan
        """
        t_start = diagnosis.t_start
        t_end   = diagnosis.t_end

        # 0-based indices into full_plan
        start_idx = t_start - 1
        end_idx   = t_end   - 1

        # Original subsequence that failed
        original_subseq = full_plan[start_idx: end_idx + 1]

        # Step 1: Ask LLM
        llm_actions = self._call_llm_for_correction(
            diagnosis=diagnosis,
            original_subseq=original_subseq,
            instruction=instruction,
            t_start=t_start,
            t_end=t_end,
        )

        # Step 2: Build candidate node pool  (LLM ∪ original subsequence)
        candidates = self._build_candidate_pool(llm_actions, original_subseq)

        # Step 3: BFS with SDG constraints
        # Use the state just before t_start as the starting state
        start_state = self.tracker.state_at(max(0, t_start - 1))
        new_subseq = self._bfs_search(candidates, start_state, diagnosis)

        if not new_subseq:
            # BFS found nothing; fall back to raw LLM suggestion
            new_subseq = llm_actions if llm_actions else original_subseq

        # Step 4: Reverse-execute back to t_start
        self._reverse_execute(t_start, diagnosis.t_error)

        # Step 5: Splice into full plan
        # Remainder = everything after t_end in the original plan
        remainder = full_plan[end_idx + 1:]
        new_plan  = full_plan[:start_idx] + new_subseq + remainder
        return new_plan, True

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm_for_correction(
        self,
        diagnosis: DiagnosisResult,
        original_subseq: List[list],
        instruction: str,
        t_start: int,
        t_end: int,
    ) -> List[list]:
        """Call GPT-4o to propose corrective actions."""
        from error_diagnosis import ErrorDiagnosis  # avoid circular at module level
        # Build error context
        # Re-use the build_error_context helper without creating a full instance
        error_ctx_lines = []
        action_str = " ".join(str(x) for x in diagnosis.failed_action)
        error_ctx_lines.append(f"Failed action: {action_str}")
        error_ctx_lines.append(f"Error type: {diagnosis.error_type.name}")
        if diagnosis.violated_states:
            for s in diagnosis.violated_states:
                sign = "" if s.value else "NOT "
                error_ctx_lines.append(
                    f"Violated precondition: {sign}{s.predicate}"
                )
        error_ctx = "\n".join(error_ctx_lines)

        current_state = self.tracker.current_state
        relevant_ids  = diagnosis.error_object_ids

        user_msg = REPLAN_USER_TEMPLATE.format(
            instruction=instruction or "(not specified)",
            env_state_summary=_summarise_env_state(current_state, relevant_ids),
            objects_in_scene=_summarise_objects(current_state),
            error_context=error_ctx,
            t_start=t_start,
            t_end=t_end,
            original_subseq=_actions_to_str(original_subseq),
        )

        try:
            response = self._client.chat.completions.create(
                #model="gpt-4o",
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": REPLAN_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.0,
                max_tokens=512,
            )
            raw = response.choices[0].message.content or ""
            return _parse_llm_action_json(raw)
        except Exception as e:
            # If API call fails, return empty and let BFS fall back to
            # original subsequence
            import logging
            logging.getLogger(__name__).warning(
                f"LLM replan call failed: {e}"
            )
            return []

    # ------------------------------------------------------------------
    # Candidate pool construction
    # ------------------------------------------------------------------

    def _build_candidate_pool(
        self,
        llm_actions: List[list],
        original_subseq: List[list],
    ) -> List[list]:
        """
        Merge LLM suggestions and original subsequence into a deduplicated
        candidate pool.  LLM actions come first (they are the corrections),
        followed by any original actions not already present.
        """
        seen: Set[str] = set()
        pool: List[list] = []

        def add(action: list):
            key = "_".join(str(x) for x in action)
            if key not in seen:
                seen.add(key)
                pool.append(action)

        for a in llm_actions:
            add(a)
        for a in original_subseq:
            add(a)

        return pool

    # ------------------------------------------------------------------
    # BFS constrained search  (eq. 5, 6 in the paper)
    # ------------------------------------------------------------------

    def _bfs_search(
        self,
        candidates: List[list],
        start_state: dict,
        diagnosis: DiagnosisResult,
    ) -> List[list]:
        """
        BFS over the candidate action pool, pruned by SDG constraints.

        A child node Aj is valid if:
          satisfied(Aj, G)   → Aj's preconditions hold in the simulated state
          change(Aj, G)      → Aj has at least one effect
          notCovered(At, Aj) → Aj does not override the parent's effects  (eq. 6)

        Returns the shortest valid path, or [] if none found.
        """
        # Root node represents the state BEFORE t_start
        root = TreeNode(action=[], sim_state=copy.deepcopy(start_state), depth=0)
        queue: deque[TreeNode] = deque([root])
        visited_states: Set[str] = set()

        while queue:
            current = queue.popleft()

            if current.depth >= self.max_depth:
                continue

            for candidate in candidates:
                pddl_name = action_to_pddl_name(candidate)
                sim_state = current.sim_state or start_state

                # satisfied(Aj, G) — preconditions hold in simulated state
                prec_ok, _ = self.sdg.preconditions_satisfied(pddl_name, sim_state)
                if not prec_ok:
                    continue

                # change(Aj, G) — Aj has at least one effect
                if not self.sdg.seff(pddl_name):
                    # No-effect actions (like FIND) are allowed as prep steps
                    # only if they are state-prep actions
                    if not self.sdg.is_state_prep_action(pddl_name):
                        continue

                # notCovered(At, Aj) — eq. 6
                if current.action:
                    parent_pddl = action_to_pddl_name(current.action)
                    if self._overrides_parent_effects(pddl_name, parent_pddl):
                        continue

                # Already visited this (action, depth) combo?
                state_key = f"{pddl_name}_{current.depth}"
                if state_key in visited_states:
                    continue
                visited_states.add(state_key)

                # Simulate the effect on state (lightweight)
                new_sim_state = self._simulate_effects(pddl_name, sim_state)

                child = TreeNode(
                    action=candidate,
                    parent=current,
                    depth=current.depth + 1,
                    sim_state=new_sim_state,
                )

                # Check if this path already resolves the violated preconditions
                if self._resolves_error(child, diagnosis):
                    return child.path_to_root()

                queue.append(child)

        return []

    def _overrides_parent_effects(self, child_pddl: str, parent_pddl: str) -> bool:
        """
        eq. 6: notCovered returns False (i.e., child DOES override parent)
        if child's effects overlap with parent's effects on the same predicate.
        """
        parent_effs = {s.predicate for s in self.sdg.seff(parent_pddl)}
        child_effs  = {s.predicate for s in self.sdg.seff(child_pddl)}
        overlap = parent_effs & child_effs
        # If child cancels out at least one parent effect, it overrides
        for pred in overlap:
            parent_val = next(
                (s.value for s in self.sdg.seff(parent_pddl) if s.predicate == pred),
                None
            )
            child_val = next(
                (s.value for s in self.sdg.seff(child_pddl) if s.predicate == pred),
                None
            )
            if parent_val is not None and child_val is not None:
                if parent_val != child_val:
                    return True   # child negates parent's effect
        return False

    def _simulate_effects(self, pddl_name: str, state: dict) -> dict:
        """
        Lightweight optimistic state simulation: toggle state predicates
        based on SDG effects.  Does NOT actually execute in the simulator.
        Used only to guide BFS.
        """
        new_state = copy.deepcopy(state)
        for eff in self.sdg.seff(pddl_name):
            pred = eff.predicate.lower()
            val  = eff.value
            # Mark the predicate as present or absent in node states
            # (simplified: we just tag first node)
            if new_state.get("nodes"):
                node = new_state["nodes"][0]
                states = node.setdefault("states", [])
                if val and pred.upper() not in states:
                    states.append(pred.upper())
                elif not val:
                    states = [s for s in states if s.lower() != pred]
                    node["states"] = states
        return new_state

    def _resolves_error(
        self, leaf: TreeNode, diagnosis: DiagnosisResult
    ) -> bool:
        """
        A path is accepted when the sequence of actions in the path
        includes an action whose effect satisfies the violated preconditions
        of the original failed action.
        """
        if not diagnosis.violated_states:
            return leaf.depth >= 1   # any single corrective action accepted

        path = leaf.path_to_root()
        for action in path:
            pddl_name = action_to_pddl_name(action)
            effs = {(s.predicate, s.value) for s in self.sdg.seff(pddl_name)}
            for violated in diagnosis.violated_states:
                if (violated.predicate, violated.value) in effs:
                    return True
        return False

    # ------------------------------------------------------------------
    # Reverse execution
    # ------------------------------------------------------------------

    def _reverse_execute(self, t_start: int, t_error: int):
        """
        Attempt to reverse the physical environment state back to t_start
        by executing inverse actions for the window (t_start, t_error).

        Inverse map (best-effort):
            grab      → release / putback
            open      → close
            switch_on → switch_off
            plug_in   → plug_out
            sit / lie → standup
            putin     → grab  (take it back out)

        Irreversible actions (eat, drink, cut, pour, wash) are skipped
        using the "fake execution" strategy from the paper.
        """
        IRREVERSIBLE = {
            "eat", "drink", "cut", "pour", "wash", "rinse",
            "scrub", "wipe", "sleep", "wake_up"
        }
        INVERSE: Dict[str, str] = {
            "grab":      "RELEASE",
            "open":      "CLOSE",
            "switch_on": "SWITCHOFF",
            "plug_in":   "PLUGOUT",
            "sit":       "STANDUP",
            "lie":       "STANDUP",
            "put_on":    "GRAB",
            "put_inside":"GRAB",
        }

        # Actions from t_start to t_error, reversed
        actions_in_window = self.tracker.history_actions[t_start - 1: t_error - 1]
        for action in reversed(actions_in_window):
            pddl_name = action_to_pddl_name(action)
            if pddl_name in IRREVERSIBLE:
                # Fake execution: skip
                continue
            inv_name = INVERSE.get(pddl_name)
            if inv_name:
                inv_action = [inv_name] + list(action[1:])
                try:
                    self.motion_planner.my_execute_primitive_action_eval(inv_action)
                except Exception:
                    pass   # best-effort; ignore if reverse fails

        # Roll back the tracker to t_start
        self.tracker.rollback_to(t_start - 1)

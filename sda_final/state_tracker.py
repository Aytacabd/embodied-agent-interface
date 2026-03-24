"""
state_tracker.py  —  Environment State Tracker for SDA-Planner

Wraps the list of env-state snapshots that EAI's execution loop
maintains (history_env_states) and provides:

  1. Querying whether a specific predicate held at a given timestep
  2. Finding the timestep at which a predicate last changed value
     (needed for t_source computation in Error Backtrack & Diagnosis)
  3. Checking current preconditions against the SDG
  4. Mapping VirtualHome action format to SDG action names

Action format in EAI (after json_to_action):
    Each action is a list: [action_name, obj1_name, obj1_id]
    or for 2-arg:          [action_name, obj1_name, obj1_id, obj2_name, obj2_id]
    e.g. ["WALK", "chair", "1"] or ["PUTBACK", "cup", "2", "table", "5"]

Timestep convention (matches the paper):
    t=0  → initial state before any action
    t=k  → state AFTER action k-1 has been executed
    history_env_states[t] = env_state dict at timestep t
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Set, Tuple

from sdg import StateDependencyGraph, StateNode


# ---------------------------------------------------------------------------
# VirtualHome action name normalisation
# ---------------------------------------------------------------------------

# Map from EAI uppercase action names to PDDL lowercase action names
EAI_TO_PDDL: Dict[str, str] = {
    "WALK":      "walk_towards",
    "RUN":       "walk_towards",     # same preconditions
    "FIND":      "find",
    "SIT":       "sit",
    "STANDUP":   "standup",
    "GRAB":      "grab",
    "OPEN":      "open",
    "CLOSE":     "close",
    "PUTBACK":   "put_on",
    "PUTIN":     "put_inside",
    "SWITCHON":  "switch_on",
    "SWITCHOFF": "switch_off",
    "DRINK":     "drink",
    "TURNTO":    "turn_to",
    "LOOKAT":    "look_at",
    "LOOKAT_SHORT": "look_at",
    "LOOKAT_LONG":  "look_at",
    "WIPE":      "wipe",
    "DROP":      "drop",
    "READ":      "read",
    "TOUCH":     "touch",
    "LIE":       "lie",
    "POUR":      "pour",
    "TYPE":      "type",
    "WATCH":     "watch",
    "MOVE":      "move",
    "PUSH":      "move",
    "PULL":      "move",
    "WASH":      "wash",
    "RINSE":     "wash",
    "SCRUB":     "wash",
    "SQUEEZE":   "squeeze",
    "PLUGIN":    "plug_in",
    "PLUGOUT":   "plug_out",
    "CUT":       "cut",
    "EAT":       "eat",
    "SLEEP":     "sleep",
    "WAKEUP":    "wake_up",
    "RELEASE":   "drop",
    "PUTON":     "put_on_character",
    "PUTOFF":    "put_on_character",  # inverse but same object constraints
    "GREET":     "find",              # no special PDDL entry; treat as find
    "POINTAT":   "look_at",
}

# Actions that are "state preparation" by name even if PDDL is ambiguous
KNOWN_STATE_PREP: Set[str] = {"walk_towards", "find", "turn_to", "standup"}


def normalise_action_name(eai_name: str) -> str:
    """Convert an EAI uppercase action name to the PDDL lowercase key."""
    return EAI_TO_PDDL.get(eai_name.upper(), eai_name.lower())


def action_to_pddl_name(action: list) -> str:
    """Extract and normalise the action name from an EAI action list."""
    if not action:
        return ""
    return normalise_action_name(str(action[0]))


def action_objects(action: list) -> List[Tuple[str, str]]:
    """
    Return list of (object_name, object_id) pairs from an EAI action list.
    action = [name, obj1_name, obj1_id]            → 1 object
    action = [name, obj1_name, obj1_id, o2n, o2id] → 2 objects
    """
    pairs = []
    i = 1
    while i + 1 < len(action):
        pairs.append((str(action[i]), str(action[i + 1])))
        i += 2
    return pairs


# ---------------------------------------------------------------------------
# StateTracker
# ---------------------------------------------------------------------------

class StateTracker:
    """
    Maintains and queries the timeline of environment states.

    Parameters
    ----------
    sdg : StateDependencyGraph
        The pre-built SDG used for precondition lookups.
    initial_env_state : dict
        The env_state.to_dict() snapshot BEFORE any action is taken.
    """

    def __init__(self, sdg: StateDependencyGraph, initial_env_state: dict):
        self.sdg = sdg
        # history_env_states[0] = initial, [k] = state after action k
        self.history: List[dict] = [copy.deepcopy(initial_env_state)]
        # Parallel list of actions taken (len = len(history) - 1)
        self.history_actions: List[list] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_success(self, action: list, new_env_state: dict):
        """Call after a successful action execution."""
        self.history_actions.append(action)
        self.history.append(copy.deepcopy(new_env_state))

    def rollback_to(self, t: int):
        """
        Truncate history to timestep t.
        After this, len(history) == t+1, len(history_actions) == t.
        Used when SDA-Planner reverses execution to t_start.
        """
        t = max(0, min(t, len(self.history) - 1))
        self.history = self.history[: t + 1]
        self.history_actions = self.history_actions[:t]

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> dict:
        return self.history[-1]

    @property
    def current_t(self) -> int:
        """Current timestep (= number of successfully executed actions)."""
        return len(self.history) - 1

    def state_at(self, t: int) -> dict:
        """Return env_state snapshot at timestep t."""
        t = max(0, min(t, len(self.history) - 1))
        return self.history[t]

    def predicate_held_at(self, predicate: str, t: int) -> bool:
        """
        Return True if `predicate` appears anywhere in the env_state at t.
        Used by error diagnosis to trace when a state was last satisfied.
        """
        snapshot = self.state_at(t)
        pred_lower = predicate.lower()
        for node in snapshot.get("nodes", []):
            states = [s.lower() for s in node.get("states", [])]
            props  = [p.lower() for p in node.get("properties", [])]
            if pred_lower in states or pred_lower in props:
                return True
        for edge in snapshot.get("edges", []):
            if edge.get("relation_type", "").lower() == pred_lower:
                return True
        return False

    def find_last_corruption(self, predicate: str, needed_value: bool,
                              before_t: int) -> int:
        """
        Find t_source: the most recent timestep t < before_t where
        predicate changed FROM needed_value TO (not needed_value).

        If the predicate was never satisfied, returns 0.
        Returns -1 if it was always satisfied (no corruption found).
        """
        t_source = -1
        for t in range(1, before_t):
            prev_held = self.predicate_held_at(predicate, t - 1)
            curr_held = self.predicate_held_at(predicate, t)
            # Transition: was satisfied, now not satisfied
            if prev_held == needed_value and curr_held != needed_value:
                t_source = t
        return t_source if t_source != -1 else 0

    def check_preconditions(
        self, action: list
    ) -> Tuple[bool, List[StateNode]]:
        """
        Check SDG preconditions for `action` against current env state.
        Returns (all_ok, violated_list).
        """
        pddl_name = action_to_pddl_name(action)
        return self.sdg.preconditions_satisfied(pddl_name, self.current_state)

    def objects_in_action_history(
        self, t_start: int, t_end: int
    ) -> Set[str]:
        """
        Collect all object IDs involved in actions between t_start and t_end
        (inclusive of action indices, 0-based in history_actions).
        Used to compute the error object set O in the paper.
        """
        ids: Set[str] = set()
        for action in self.history_actions[t_start:t_end]:
            for _, obj_id in action_objects(action):
                ids.add(obj_id)
        return ids

    def action_name_at(self, t: int) -> str:
        """Return PDDL-normalised name of the action executed at step t (1-based)."""
        if t < 1 or t > len(self.history_actions):
            return ""
        return action_to_pddl_name(self.history_actions[t - 1])

    def is_state_prep_at(self, t: int) -> bool:
        """Return True if the action at step t is a state preparation action."""
        name = self.action_name_at(t)
        return (self.sdg.is_state_prep_action(name) or
                name in KNOWN_STATE_PREP)

    def get_history_actions(self) -> List[list]:
        return list(self.history_actions)

    def get_history_env_states(self) -> List[dict]:
        return list(self.history)

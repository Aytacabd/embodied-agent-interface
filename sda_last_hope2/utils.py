"""
utils.py (or action_validation.py)
==================================
Validates whether an action can be performed given the current environment state,
using the State Dependency Graph (SDG) and the Object State Model.
"""

import random
from typing import List, Optional, Dict

from object_state_model import ObjectStateModel
from sdg import get_preconditions

# ------------------------------------------------------------------------------
# Action name mapping to canonical SDG names
# ------------------------------------------------------------------------------
CANONICAL_ACTION_NAMES = {
    "walk": "WALK",
    "walktowards": "WALK",
    "walkto": "WALK",
    "walkforward": "WALK",
    "walk_into": "WALK",
    "run": "RUN",
    "find": "FIND",
    "turnto": "TURNTO",
    "turnleft": "TURNTO",
    "turnright": "TURNTO",
    "open": "OPEN",
    "close": "CLOSE",
    "grab": "GRAB",
    "put": "PUT_ON",            # will be refined based on object type
    "putback": "PUT_ON",
    "putin": "PUT_INSIDE",
    "puton": "PUT_ON_CHARACTER",
    "drop": "DROP",
    "release": "RELEASE",
    "pour": "POUR",
    "move": "MOVE",
    "push": "PUSH",
    "pull": "PULL",
    "greet": "GREET",
    "switchon": "SWITCHON",
    "switchoff": "SWITCHOFF",
    "plugin": "PLUGIN",
    "plugout": "PLUGOUT",
    "sit": "SIT",
    "standup": "STANDUP",
    "lie": "LIE",
    "sleep": "SLEEP",
    "wakeup": "WAKEUP",
    "wash": "WASH",
    "rinse": "RINSE",
    "scrub": "SCRUB",
    "wipe": "WIPE",
    "squeeze": "SQUEEZE",
    "cut": "CUT",
    "drink": "DRINK",
    "eat": "EAT",
    "read": "READ",
    "touch": "TOUCH",
    "watch": "WATCH",
    "lookat": "LOOKAT",
    "type": "TYPE",
    "pointat": "POINTAT",
}

# Actions that take zero object arguments
ZERO_ARG_ACTIONS = {
    "STANDUP",
    "SLEEP",
    "WAKEUP",
    "TURNLEFT",   # low‑level navigation (mapped to TURNTO, but we treat as zero‑arg)
    "TURNRIGHT",
}

# Actions that take two object arguments
TWO_ARG_ACTIONS = {
    "PUT_ON",
    "PUT_INSIDE",
    "PUT_ON_CHARACTER",
    "POUR",
}


def map_to_canonical(action_name: str) -> str:
    """Map a raw action name to the canonical form used in the SDG."""
    return CANONICAL_ACTION_NAMES.get(action_name.lower(), action_name.upper())


def args_per_action(action: str) -> int:
    """
    Return the number of object arguments required for a canonical action.
    Based on the SDG and PDDL definitions.
    """
    action = map_to_canonical(action)
    if action in ZERO_ARG_ACTIONS:
        return 0
    if action in TWO_ARG_ACTIONS:
        return 2
    return 1


def get_held_objects(state_model: ObjectStateModel) -> List[int]:
    """Return a list of object IDs that the character is currently holding."""
    held = []
    if state_model.hand_right:
        held.append(state_model.hand_right)
    if state_model.hand_left:
        held.append(state_model.hand_left)
    return held


def can_perform_action(
    action: str,
    obj_id: int,
    agent_id: int,
    state_model: ObjectStateModel,
    object_restrictions: Optional[Dict] = None,
    teleport: bool = True,
) -> Optional[str]:
    """
    Check if the given action can be performed on the specified object.

    Returns a formatted action string (e.g., "[GRAB] <apple> (42)") if possible,
    otherwise returns None.

    Parameters:
        action          : raw action name (e.g., "grab", "put", "walktowards")
        obj_id          : ID of the primary object (or container)
        agent_id        : ID of the character (not used directly, but state_model knows it)
        state_model     : ObjectStateModel representing the current world state
        object_restrictions : optional dict with 'objects_inside', 'objects_surface' (unused now)
        teleport        : if True, replace WALK with FIND (teleport navigation)
    """
    # Map to canonical action name
    canonical_action = map_to_canonical(action)

    # Special handling for walking with teleport
    if teleport and canonical_action == "WALK":
        canonical_action = "FIND"

    # Zero‑argument actions (e.g., STANDUP)
    if args_per_action(canonical_action) == 0:
        preconditions = get_preconditions(canonical_action)
        unsatisfied = state_model.check_all(preconditions, obj=None, target=None)
        if unsatisfied:
            return None
        return f"[{canonical_action}]"

    # --- Two‑argument actions: PUT_ON, PUT_INSIDE, POUR, PUT_ON_CHARACTER ---
    if canonical_action in TWO_ARG_ACTIONS:
        # For PUT_ON_CHARACTER, we need only the held object
        if canonical_action == "PUT_ON_CHARACTER":
            held = get_held_objects(state_model)
            if not held:
                return None
            held_id = held[0]
            if not state_model.satisfies("holds_obj", held_id):
                return None
            held_class = state_model.id_to_class.get(held_id, "unknown")
            return f"[{canonical_action}] <{held_class}> ({held_id})".strip()

        # For PUT_ON, PUT_INSIDE, POUR
        held = get_held_objects(state_model)
        if not held:
            return None
        held_id = held[0]          # object being held
        container_id = obj_id      # target container/surface

        # Check holds_obj on the held object
        if not state_model.satisfies("holds_obj", held_id):
            return None
        # Check next_to_target on the container (target)
        if not state_model.satisfies("next_to_target", None, container_id):
            return None

        held_class = state_model.id_to_class.get(held_id, "unknown")
        container_class = state_model.id_to_class.get(container_id, "unknown")
        action_str = (f"[{canonical_action}] "
                      f"<{held_class}> ({held_id}) "
                      f"<{container_class}> ({container_id})").strip()
        return action_str

    # --- One‑argument actions (most actions) ---
    preconditions = get_preconditions(canonical_action)
    unsatisfied = state_model.check_all(preconditions, obj_id, None)
    if unsatisfied:
        return None

    # Build the action string with the object
    obj_class = state_model.id_to_class.get(obj_id, "unknown")
    obj_str = f"<{obj_class}> ({obj_id})"
    return f"[{canonical_action}] {obj_str}".strip()


def convert_action(action_dict: Dict[int, Optional[str]]) -> List[str]:
    """
    Convert a dictionary of agent actions to a list of scripts.
    This is a simplified version; the original code handled multiple agents.
    For SDA, we typically have one agent.
    """
    scripts = []
    for agent_id, action_str in action_dict.items():
        if action_str is not None:
            scripts.append(f"<char{agent_id}> {action_str}")
    if not scripts:
        return [""]
    return scripts


# ------------------------------------------------------------------------------
# Example usage (for testing)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Build a dummy environment graph (similar to object_state_model test)
    env = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "fridge",
             "states": ["CLOSED", "PLUGGED_IN"],
             "properties": ["CAN_OPEN"]},
            {"id": 3, "class_name": "apple",
             "states": [],
             "properties": ["GRABBABLE", "EATABLE"]},
            {"id": 4, "class_name": "table",
             "states": [],
             "properties": ["SURFACE"]},
        ],
        "edges": [
            {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
            {"from_id": 1, "to_id": 4, "relation_type": "CLOSE"},
        ],
    }

    state = ObjectStateModel.from_env_dict(env)
    print("Initial state:", state)

    # Grab apple (inside closed fridge) – should fail
    res = can_perform_action("grab", 3, 1, state)
    print("Grab apple (inside closed fridge):", res)  # None

    # Open fridge
    state.apply("OPEN", 2)
    # Now grab should succeed
    res = can_perform_action("grab", 3, 1, state)
    print("Grab apple (fridge open):", res)  # Should be a string

    # After grabbing, put apple on table
    state.apply("GRAB", 3)   # Update state to reflect apple is held
    res = can_perform_action("put", 4, 1, state)
    print("Put apple on table:", res)  # Should be a string

    # Test walking while sitting
    state.apply("SIT", 4)
    res = can_perform_action("walktowards", 4, 1, state)
    print("Walk while sitting:", res)  # Should be None (if teleport=False) or [FIND] if teleport=True

    # Stand up
    res = can_perform_action("standup", None, 1, state)
    print("Stand up:", res)
    state.apply("STANDUP", None)

    # Walk after standing
    res = can_perform_action("walktowards", 4, 1, state)
    print("Walk after standing:", res)
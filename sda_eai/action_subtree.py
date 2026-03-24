"""
action_subtree.py
=================
Adaptive Action SubTree Generation
Based on SDA-Planner paper Section 4.4

Steps:
1. Generate candidate nodes (LLM suggestions + original subsequence)
2. Identify constrained subsequences (paper Section 4.4)
3. Build search tree with SDG constraints
4. BFS to find first valid executable subsequence
5. Return replacement subsequence to splice back into plan
"""

import re
from collections import deque
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# =============================================================================
# State simulation for search tree
# =============================================================================

class TreeState:
    """
    Tracks generic states during BFS tree search.
    Uses simple unqualified state names — object grounding happens
    at the action level, not the state level.
    """

    def __init__(self, initial_states: set):
        self.states = set(initial_states)

    def copy(self):
        return TreeState(set(self.states))

    def apply(self, action: str):
        """Apply action effects to update state."""
        effects = get_effects(action.upper())
        for effect in effects:
            if effect.startswith("not_"):
                positive = effect[4:]
                self.states.discard(positive)
                self.states.add(effect)
            else:
                self.states.add(effect)
                self.states.discard(f"not_{effect}")

    def satisfies(self, preconditions: list) -> bool:
        """
        Check if all preconditions are satisfied.

        Static object properties are assumed satisfied — they are
        per-object properties from properties_data.json checked by
        EAI simulator at runtime, not tracked here.

        not_on is also assumed satisfied — containers/furniture are
        not electrical devices. If a device IS on, env_dict shows
        ON state explicitly and we handle it during init.
        """
        for p in preconditions:
            if p == "sitting_or_lying":
                if "sitting" not in self.states and "lying" not in self.states:
                    return False
            elif p == "not_both_hands_full":
                if "both_hands_full" in self.states:
                    return False
            elif p == "target_open_or_not_openable":
                if ("open" not in self.states and
                        "not_openable" not in self.states and
                        "target_open_or_not_openable" not in self.states):
                    return False
            elif p == "next_to_target":
                # next_to_target satisfied if next_to_obj is satisfied
                if "next_to_obj" not in self.states:
                    return False
                
            # Static object properties + not_on:
            # assumed satisfied, checked by EAI at execution time
            elif p in (
                "grabbable", "has_switch", "can_open", "has_plug",
                "eatable", "drinkable", "readable", "movable",
                "lookable", "sittable", "lieable", "clothes",
                "not_on",
            ):
                continue
            elif p not in self.states:
                return False
        return True


# =============================================================================
# Search Tree Node
# =============================================================================

class TreeNode:
    """A single node in the action search tree."""

    def __init__(self, action: str, obj: str, target: str = None,
                 parent=None, state: TreeState = None, depth: int = 0,
                 forced_next=None):
        self.action      = action.upper()
        self.obj         = obj
        self.target      = target
        self.parent      = parent
        self.state       = state
        self.depth       = depth
        self.forced_next = forced_next

    def __repr__(self):
        if self.target:
            return f"{self.action}({self.obj}, {self.target})"
        return f"{self.action}({self.obj})"


# =============================================================================
# Candidate Node Generation
# =============================================================================

def parse_item(item):
    """
    Parse an action item into (action, obj, target).
    Handles:
      - dict format:  {"WALK": ["light"]}  or  {"PUTIN": ["soap", "fridge"]}
      - EAI string:   "[WALK] <light> (411)"
    """
    if isinstance(item, dict):
        for action, args in item.items():
            if isinstance(args, list):
                if len(args) == 0:
                    return action.upper(), "character", None
                elif len(args) == 1:
                    return action.upper(), str(args[0]), None
                else:
                    # Filter out numeric IDs — keep only names
                    names  = [str(a) for a in args if not str(a).isdigit()]
                    obj    = names[0] if len(names) > 0 else "character"
                    target = names[1] if len(names) > 1 else None
                    return action.upper(), obj, target
    # EAI string format: "[WALK] <light> (411)"
    s  = str(item)
    am = re.search(r'\[(\w+)\]', s)
    om = re.findall(r'<([^>]+)>', s)
    if am:
        return (am.group(1).upper(),
                om[0].strip() if om else "character",
                om[1].strip() if len(om) > 1 else None)
    return None, None, None


def generate_candidate_nodes(
    llm_suggestions:      list,
    original_subsequence: list,
    error_objects:        set,
) -> list:
    """
    Generate candidate action nodes for the search tree.
    Paper: "nodes generated using two sources: corrective actions from LLM
            and actions in the original subsequence"

    Constrained subsequence rule (paper Section 4.4):
    If consecutive original actions share the same object AND that object
    is NOT in O (error objects), only the first is selectable — the rest
    are forced (non-selectable).

    Returns list of (action, obj, target) tuples.
    """
    candidates = []
    seen = set()

    def add_candidate(action, obj, target=None):
        key = (action.upper(), obj, target)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    # LLM suggestions — primary source per paper
    for item in llm_suggestions:
        a, o, t = parse_item(item)
        if a:
            add_candidate(a, o, t)

    # Original subsequence — secondary source with constrained rule
    parsed_orig = []
    for item in original_subsequence:
        a, o, t = parse_item(item)
        if a:
            parsed_orig.append((a, o, t))

    for i, (a, o, t) in enumerate(parsed_orig):
        if i > 0:
            prev_a, prev_o, prev_t = parsed_orig[i - 1]
            # Constrained: same object as previous AND object NOT in error set
            if prev_o == o and o not in error_objects:
                continue  # non-selectable
        add_candidate(a, o, t)

    # Always ensure STANDUP is available
    add_candidate("STANDUP", "character", None)

    return candidates


# =============================================================================
# SDG Constraint Checks (Paper Equations 5 and 6)
# =============================================================================

def satisfied(action: str, state: TreeState) -> bool:
    """satisfied(Aj, G) — paper Equation 5."""
    preconditions = get_preconditions(action.upper())
    return state.satisfies(preconditions)


def changes_state(action: str) -> bool:
    """change(Aj, G) — paper Equation 5."""
    return len(get_effects(action.upper())) > 0


def not_covered(parent_action: str, child_action: str) -> bool:
    """
    notCovered(At, Aj) — paper Equation 6.
    True if parent has at least one effect that child does NOT share.
    """
    if parent_action is None or parent_action == "ROOT":
        return True

    parent_effects = set(get_effects(parent_action.upper()))
    child_effects  = set(get_effects(child_action.upper()))

    if not parent_effects:
        return True

    for pe in parent_effects:
        if pe not in child_effects:
            return True

    return False


# =============================================================================
# Search Tree Builder + BFS
# =============================================================================

def build_and_search_tree(
    candidates:     list,
    initial_state:  TreeState,
    target_effects: list,
    max_depth:      int = 6,
    max_nodes:      int = 500,
) -> list:
    """
    BFS over search tree to find valid replacement subsequence.
    Paper: "performs breadth-first search to extract a fully executable subsequence"
    """
    root = TreeNode(
        action="ROOT",
        obj="",
        state=initial_state.copy(),
        depth=0,
    )

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current = queue.popleft()
        nodes_expanded += 1

        # Check if target effects achieved
        if current.depth > 0 and _achieves_target(current.state, target_effects):
            return _extract_path(current)

        if current.depth >= max_depth:
            continue

        # Expand children — apply 3 SDG constraints
        for (action, obj, target) in candidates:

            # Constraint 1: satisfied(Aj, G)
            if not satisfied(action, current.state):
                continue

            # Constraint 2: change(Aj, G)
            if not changes_state(action):
                continue

            # Constraint 3: notCovered(At, Aj)
            if not not_covered(current.action, action):
                continue

            new_state = current.state.copy()
            new_state.apply(action)

            child = TreeNode(
                action=action,
                obj=obj,
                target=target,
                parent=current,
                state=new_state,
                depth=current.depth + 1,
            )
            queue.append(child)

    return []


def _achieves_target(state: TreeState, target_effects: list) -> bool:
    if not target_effects:
        return True
    for effect in target_effects:
        if effect not in state.states:
            return False
    return True


def _extract_path(node: TreeNode) -> list:
    path = []
    current = node
    while current.parent is not None:
        path.append((current.action, current.obj, current.target))
        current = current.parent
    path.reverse()
    return path


# =============================================================================
# Initial state builder — EAI confirmed defaults only
# =============================================================================

def _build_initial_state(env_dict: dict, char_sitting: bool, char_lying: bool) -> set:
    """
    Build initial TreeState from EAI environment dict.

    Confirmed defaults from EAI's graph_dict_helper (utils.py):
      open_closed    → default = CLOSED
      on_off         → default = OFF
      clean_dirty    → default = CLEAN
      plugged_in_out → default = PLUGGED_IN

    Static object properties (grabbable, has_switch, can_open, not_on etc.)
    are NOT set here — they are skipped in TreeState.satisfies() instead.
    """
    states = set()

    # Character posture — default: standing
    if char_sitting:
        states.add("sitting")
    else:
        states.add("not_sitting")

    if char_lying:
        states.add("lying")
    else:
        states.add("not_lying")

    # EAI-confirmed defaults from graph_dict_helper
    states.add("not_holds_obj")
    states.add("not_both_hands_full")
    states.add("plugged_in")   # default="PLUGGED_IN"
    states.add("off")          # default="OFF"
    states.add("closed")       # default="CLOSED"
    states.add("clean")        # default="CLEAN"

    # Read actual states from EAI environment dict
    try:
        nodes = env_dict.get("nodes", []) if env_dict else []
        edges = env_dict.get("edges", []) if env_dict else []

        # Check character holding state from edges
        char_id = None
        for node in nodes:
            if node.get("class_name") == "character":
                char_id = node.get("id")
                break

        if char_id:
            holds_count = sum(
                1 for e in edges
                if e.get("from_id") == char_id
                and e.get("relation_type") in ("HOLDS_RH", "HOLDS_LH")
            )
            if holds_count > 0:
                states.add("holds_obj")
                states.discard("not_holds_obj")
            if holds_count >= 2:
                states.add("both_hands_full")
                states.discard("not_both_hands_full")

        # Read actual object/scene states
        for node in nodes:
            node_states = [s.upper() for s in node.get("states", [])]

            if "OPEN" in node_states:
                states.add("open")
                states.discard("closed")
                states.add("obj_not_inside_closed_container")
                states.add("target_open_or_not_openable")
            if "CLOSED" in node_states:
                states.add("closed")
                states.discard("open")
                states.discard("obj_not_inside_closed_container")
                states.discard("target_open_or_not_openable")
            if "ON" in node_states:
                states.add("on")
                states.discard("off")
            if "OFF" in node_states:
                states.add("off")
                states.discard("on")
            if "PLUGGED_IN" in node_states:
                states.add("plugged_in")
                states.discard("plugged_out")
            if "PLUGGED_OUT" in node_states:
                states.add("plugged_out")
                states.discard("plugged_in")
            if "DIRTY" in node_states:
                states.add("dirty")
                states.discard("clean")
            if "SITTING" in node_states:
                states.discard("not_sitting")
                states.add("sitting")
            if "LYING" in node_states:
                states.discard("not_lying")
                states.add("lying")

    except Exception:
        pass

    # If no open containers found, objects inside containers not accessible
    states.add("obj_not_inside_closed_container")  # objects on surfaces by default
    if "open" not in states:
        states.discard("target_open_or_not_openable")

    return states


# =============================================================================
# Main Entry Point
# =============================================================================
def generate_replacement_subsequence(
    llm_suggestions:      list,
    original_subsequence: list,
    initial_state_dict:   dict,
    unsatisfied_needs:    list,
    error_objects:        set,
    char_sitting:         bool = False,
    char_lying:           bool = False,
    max_depth:            int = 6,
    max_nodes:            int = 500,
) -> list:

    initial_states     = _build_initial_state(initial_state_dict, char_sitting, char_lying)
    initial_tree_state = TreeState(initial_states)

    candidates = generate_candidate_nodes(
        llm_suggestions=llm_suggestions,
        original_subsequence=original_subsequence,
        error_objects=error_objects,
    )

    # Map unsatisfied preconditions → target effects for BFS termination
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(need)
        elif need == "holds_obj":
            target_effects.append("holds_obj")
        elif need in ("open", "target_open_or_not_openable",
                      "obj_not_inside_closed_container"):
            target_effects.append("open")
        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append("next_to_obj")
        elif need == "not_both_hands_full":
            target_effects.append("not_both_hands_full")

    # Deduplicate while preserving order
    seen_effects   = set()
    target_effects = [
        e for e in target_effects
        if not (e in seen_effects or seen_effects.add(e))
    ]

    path = build_and_search_tree(
        candidates=candidates,
        initial_state=initial_tree_state,
        target_effects=target_effects,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    if not path:
        return []

    # Append original subsequence actions not already in BFS path
    # These are the actions that come AFTER the corrective fix
    bfs_actions = {(a, o, t) for a, o, t in path}
    for item in original_subsequence:
        a, o, t = parse_item(item)
        if a and (a, o, t) not in bfs_actions:
            path.append((a, o, t))

    # Convert to EAI-compatible action dict format
    result = []
    for (action, obj, target) in path:
        if action == "STANDUP":
            result.append({action: []})
        elif target:
            result.append({action: [obj, target]})
        else:
            result.append({action: [obj]})

    return result
if __name__ == "__main__":
    # Test 1: character sitting — needs STANDUP
    print("Test 1 — Sitting character needs STANDUP:")
    result = generate_replacement_subsequence(
        llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
        original_subsequence = [{"WALK": ["bathroom"]}],
        initial_state_dict   = {},
        unsatisfied_needs    = ["not_sitting"],
        error_objects        = {"bathroom"},
        char_sitting         = True,
    )
    print("Result:", result)
    # Expected: [{"STANDUP": []}]

    # Test 2: needs WALK then GRAB
    print("\nTest 2 — Needs WALK + GRAB:")
    result = generate_replacement_subsequence(
        llm_suggestions      = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        original_subsequence = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        initial_state_dict   = {},
        unsatisfied_needs    = ["holds_obj"],
        error_objects        = {"clothes"},
    )
    print("Result:", result)
    # Expected: [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}]

    # Test 3: needs WALK + OPEN before PUTIN (agent already holds food)
    print("\nTest 3 — Needs WALK + OPEN before PUTIN:")
    result = generate_replacement_subsequence(
        llm_suggestions      = [{"WALK": ["fridge"]}, {"OPEN": ["fridge"]},
                                 {"PUTIN": ["food", "fridge"]}],
        original_subsequence = [{"PUTIN": ["food", "fridge"]}],
        initial_state_dict   = {
            "nodes": [
                {"id": 1, "class_name": "character",
                 "states": [], "properties": []},
            ],
            "edges": [
                {"from_id": 1, "to_id": 2, "relation_type": "HOLDS_RH"}
            ],
        },
        unsatisfied_needs    = ["target_open_or_not_openable"],
        error_objects        = {"food", "fridge"},
    )
    print("Result:", result)
    # Expected: [{"WALK": ["fridge"]}, {"OPEN": ["fridge"]}, {"PUTIN": ["food", "fridge"]}]
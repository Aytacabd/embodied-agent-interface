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
        """Check if all preconditions are satisfied."""
        for p in preconditions:
            if p == "sitting_or_lying":
                if "sitting" not in self.states and "lying" not in self.states:
                    return False
            elif p == "not_both_hands_full":
                if "both_hands_full" in self.states:
                    return False
            elif p == "target_open_or_not_openable":
                if "open" not in self.states and "not_openable" not in self.states:
                    return False
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
        self.forced_next = forced_next  # next forced node in constrained subsequence

    def __repr__(self):
        if self.target:
            return f"{self.action}({self.obj}, {self.target})"
        return f"{self.action}({self.obj})"


# =============================================================================
# Candidate Node Generation
# =============================================================================

def generate_candidate_nodes(
    llm_suggestions:      list,
    original_subsequence: list,
    error_objects:        set,
) -> list:
    """
    Generate candidate action nodes for the search tree.
    Paper: "nodes generated using two sources: corrective actions from LLM
            and actions in the original subsequence"

    Also identifies constrained subsequences per paper Section 4.4:
    If a subsequence has all same objects AND those objects are NOT in O,
    only the first action is selectable; subsequent ones are forced.

    Returns list of (action, obj, target) tuples — selectable candidates.
    """
    candidates = []
    seen = set()

    def add_candidate(action, obj, target=None):
        key = (action.upper(), obj, target)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    def parse_item(item):
        if isinstance(item, dict):
            for action, args in item.items():
                if isinstance(args, list):
                    if len(args) == 0:
                        return action, "character", None
                    elif len(args) == 1:
                        return action, args[0], None
                    else:
                        return action, args[0], args[1]
        import re
        s  = str(item)
        am = re.search(r'\[(\w+)\]', s)
        om = re.findall(r'<([^>]+)>', s)
        if am:
            return am.group(1), om[0] if om else "character", om[1] if len(om) > 1 else None
        return None, None, None

    # Add LLM suggestions (primary source per paper)
    for item in llm_suggestions:
        a, o, t = parse_item(item)
        if a:
            add_candidate(a, o, t)

    # Add original subsequence (secondary source per paper)
    # Apply constrained subsequence rule:
    # If consecutive actions share the same object AND that object is NOT in O,
    # only add the first action as selectable (rest are forced)
    parsed_orig = []
    for item in original_subsequence:
        a, o, t = parse_item(item)
        if a:
            parsed_orig.append((a, o, t))

    for i, (a, o, t) in enumerate(parsed_orig):
        # Check if this is part of a constrained subsequence
        # (same object as previous AND object not in error set)
        if i > 0:
            prev_a, prev_o, prev_t = parsed_orig[i-1]
            if prev_o == o and o not in error_objects:
                # This is a non-selectable node — skip adding as candidate
                # It will be handled as forced_next of the previous node
                continue
        add_candidate(a, o, t)

    # Always ensure STANDUP is available (handles sitting character)
    add_candidate("STANDUP", "character", None)

    return candidates


# =============================================================================
# SDG Constraint Checks (Paper Equations 5 and 6)
# =============================================================================

def satisfied(action: str, state: TreeState) -> bool:
    """
    satisfied(Aj, G): preconditions of action met by current state.
    Paper Equation 5.
    """
    preconditions = get_preconditions(action.upper())
    return state.satisfies(preconditions)


def changes_state(action: str) -> bool:
    """
    change(Aj, G): action must have at least one effect.
    Paper Equation 5.
    """
    return len(get_effects(action.upper())) > 0


def not_covered(parent_action: str, child_action: str) -> bool:
    """
    notCovered(At, Aj): child should not have an effect on the same
    state variable as parent.
    Paper Equation 6:
      True if ∃s where (At, s) ∈ E AND (Aj, s) ∉ E
      i.e., parent affects some state that child does NOT affect
    """
    if parent_action is None or parent_action == "ROOT":
        return True

    parent_effects = set(get_effects(parent_action.upper()))
    child_effects  = set(get_effects(child_action.upper()))

    if not parent_effects:
        return True

    # notCovered = True if parent has at least one effect that child does not share
    for pe in parent_effects:
        if pe not in child_effects:
            return True  # ∃s where parent affects it and child does not

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
    Build search tree and BFS to find valid replacement subsequence.
    Paper: "performs breadth-first search to extract a fully executable subsequence"
    """
    root = TreeNode(
        action = "ROOT",
        obj    = "",
        state  = initial_state.copy(),
        depth  = 0,
    )

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current = queue.popleft()
        nodes_expanded += 1

        # Check if target is achieved
        if current.depth > 0 and _achieves_target(current.state, target_effects):
            return _extract_path(current)

        if current.depth >= max_depth:
            continue

        # Expand children using 3 SDG constraints
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
                action = action,
                obj    = obj,
                target = target,
                parent = current,
                state  = new_state,
                depth  = current.depth + 1,
            )
            queue.append(child)

    return []


def _achieves_target(state: TreeState, target_effects: list) -> bool:
    """Check if current state satisfies all target effects."""
    if not target_effects:
        return True  # no targets = already satisfied (local replan case)
    for effect in target_effects:
        if effect not in state.states:
            return False
    return True


def _extract_path(node: TreeNode) -> list:
    """Extract path from root to node."""
    path = []
    current = node
    while current.parent is not None:
        path.append((current.action, current.obj, current.target))
        current = current.parent
    path.reverse()
    return path


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
    """
    Main function: generate replacement subsequence using search tree.
    Returns list of EAI action dicts or empty list if search fails.
    """
    initial_states     = _build_initial_state(initial_state_dict, char_sitting, char_lying)
    initial_tree_state = TreeState(initial_states)

    candidates = generate_candidate_nodes(
        llm_suggestions      = llm_suggestions,
        original_subsequence = original_subsequence,
        error_objects        = error_objects,
    )

    # Map unsatisfied preconditions to target effects
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(need)
        elif need == "holds_obj":
            target_effects.append("holds_obj")
        elif need == "open":
            target_effects.append("open")
        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append("next_to_obj")
        elif need == "obj_not_inside_closed_container":
            target_effects.append("open")

    path = build_and_search_tree(
        candidates     = candidates,
        initial_state  = initial_tree_state,
        target_effects = target_effects,
        max_depth      = max_depth,
        max_nodes      = max_nodes,
    )

    if not path:
        return []

    result = []
    for (action, obj, target) in path:
        if action == "STANDUP":
            result.append({action: []})
        elif target:
            result.append({action: [obj, target]})
        else:
            result.append({action: [obj]})

    return result


def _build_initial_state(env_dict: dict, char_sitting: bool, char_lying: bool) -> set:
    """Build initial state from actual EAI environment dict."""
    states = set()

    # Character posture
    if char_sitting:
        states.add("sitting")
    else:
        states.add("not_sitting")

    if char_lying:
        states.add("lying")
    else:
        states.add("not_lying")

    # Safe defaults
    states.add("not_both_hands_full")
    states.add("grabbable")
    states.add("plugged_in")

    # Read actual object states from environment
    has_open   = False
    has_closed = False
    try:
        for node in env_dict.get("nodes", []):
            node_states = [s.upper() for s in node.get("states", [])]
            props       = [p.upper() for p in node.get("properties", [])]

            if "OPEN" in node_states:
                states.add("open")
                states.add("obj_not_inside_closed_container")
                states.add("target_open_or_not_openable")
                has_open = True
            if "CLOSED" in node_states:
                states.add("closed")
                has_closed = True
            if "ON" in node_states:
                states.add("on")
            if "OFF" in node_states:
                states.add("off")
            if "PLUGGED_IN" in node_states:
                states.add("plugged_in")
            if "HAS_SWITCH" in props:
                states.add("has_switch")
            if "HAS_PLUG" in props:
                states.add("has_plug")
            if "CAN_OPEN" in props:
                states.add("can_open")
    except Exception:
        pass

    # If no explicit closed container found, assume objects are accessible
    if not has_closed:
        states.add("obj_not_inside_closed_container")

    return states


if __name__ == "__main__":
    # Test 1: character sitting, needs STANDUP before WALK
    print("Test 1 — Sitting character:")
    result = generate_replacement_subsequence(
        llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
        original_subsequence = [{"WALK": ["bathroom"]}],
        initial_state_dict   = {},
        unsatisfied_needs    = ["not_sitting"],
        error_objects        = {"bathroom"},
        char_sitting         = True,
    )
    print("Result:", result)

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

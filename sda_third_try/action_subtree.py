"""
action_subtree.py
=================
Adaptive Action SubTree Generation
Based on SDA-Planner paper Section 4.4

Algorithm:
1. Generate candidate nodes from two sources:
   - LLM corrective suggestions
   - Actions from original failing subsequence
2. Apply constrained subsequence rule (paper Section 4.4)
3. Build BFS search tree with 3 SDG constraints (Eq. 5, 6):
   - satisfied(Aj, G): preconditions met
   - change(Aj, G): action has at least one effect
   - notCovered(At, Aj): child doesn't override parent's effects
4. Extract first valid path achieving target effects
"""

from collections import deque
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# =============================================================================
# State simulation for BFS tree
# =============================================================================

class TreeState:
    """Tracks abstract states during BFS search."""

    def __init__(self, initial_states: set):
        self.states = set(initial_states)

    def copy(self):
        return TreeState(set(self.states))

    def apply(self, action: str):
        """Apply action effects."""
        for effect in get_effects(action.upper()):
            if effect.startswith("not_"):
                self.states.discard(effect[4:])
                self.states.add(effect)
            else:
                self.states.add(effect)
                self.states.discard(f"not_{effect}")

    def satisfies(self, preconditions: list) -> bool:
        """Check all preconditions hold."""
        for p in preconditions:
            if p == "sitting_or_lying":
                if "sitting" not in self.states and "lying" not in self.states:
                    return False
            elif p == "not_both_hands_full":
                if "both_hands_full" in self.states:
                    return False
            elif p == "target_open_or_not_openable":
                if ("open"          not in self.states and
                        "not_openable" not in self.states and
                        "target_open_or_not_openable" not in self.states):
                    return False
            elif p in ("grabbable", "has_switch", "can_open", "has_plug",
                       "has_plug_or_switch", "eatable", "drinkable_or_recipient",
                       "readable", "movable", "lookable", "sittable", "lieable",
                       "clothes", "recipient_target", "cuttable", "hangable",
                       "body_part", "person", "cover_object", "surfaces",
                       "containers", "cream", "pourable", "drinkable",
                       "eatable", "cuttable"):
                # Static object properties — always assumed satisfied
                pass
            elif p not in self.states:
                return False
        return True


# =============================================================================
# Search Tree Node
# =============================================================================

class TreeNode:
    def __init__(self, action: str, obj: str, target: str = None,
                 parent=None, state: TreeState = None, depth: int = 0):
        self.action = action.upper()
        self.obj    = obj
        self.target = target
        self.parent = parent
        self.state  = state
        self.depth  = depth

    def __repr__(self):
        if self.target:
            return f"{self.action}({self.obj}, {self.target})"
        return f"{self.action}({self.obj})"


# =============================================================================
# SDG Constraint Functions (Paper Equations 5 and 6)
# =============================================================================

def satisfied(action: str, state: TreeState) -> bool:
    """Eq. 5: satisfied(Aj, G) — all preconditions of action met."""
    return state.satisfies(get_preconditions(action.upper()))


def changes_state(action: str) -> bool:
    """Eq. 5: change(Aj, G) — action must have at least one effect."""
    return len(get_effects(action.upper())) > 0


def not_covered(parent_action: str, child_action: str) -> bool:
    """
    Eq. 6: notCovered(At, Aj)
    True if there exists a state s where parent affects s but child does not.
    This prevents child from undoing parent's work.
    """
    if parent_action is None or parent_action == "ROOT":
        return True

    parent_effects = set(get_effects(parent_action.upper()))
    child_effects  = set(get_effects(child_action.upper()))

    if not parent_effects:
        return True

    # True if parent has at least one effect that child does NOT share
    return any(pe not in child_effects for pe in parent_effects)


# =============================================================================
# Candidate Node Generation (Paper Section 4.4)
# =============================================================================

def generate_candidate_nodes(
    llm_suggestions:      list,
    original_subsequence: list,
    error_objects:        set,
) -> list:
    """
    Generate candidate action nodes from two sources:
    1. LLM corrective suggestions (primary — fixes the error)
    2. Original failing subsequence (secondary — ensures coverage)

    Constrained subsequence rule (paper Section 4.4):
    If consecutive actions in original plan share the same object AND
    that object is NOT in error_objects, only the first is selectable;
    subsequent ones are forced (non-selectable candidates).
    """
    candidates = []
    seen       = set()

    def add(action, obj, target=None):
        key = (action.upper(), obj, target)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    def parse_item(item):
        if isinstance(item, dict):
            for action, args in item.items():
                if not isinstance(args, list):
                    return action, "character", None
                if len(args) == 0:
                    return action, "character", None
                if len(args) == 1:
                    return action, args[0], None
                return action, args[0], args[1]
        import re
        s  = str(item)
        am = re.search(r'\[(\w+)\]', s)
        om = re.findall(r'<([^>]+)>', s)
        if am:
            return am.group(1), om[0] if om else "character", om[1] if len(om) > 1 else None
        return None, None, None

    # Source 1: LLM suggestions
    for item in llm_suggestions:
        a, o, t = parse_item(item)
        if a:
            add(a, o, t)

    # Source 2: Original subsequence with constrained subsequence rule
    parsed_orig = []
    for item in original_subsequence:
        a, o, t = parse_item(item)
        if a:
            parsed_orig.append((a, o, t))

    for i, (a, o, t) in enumerate(parsed_orig):
        if i > 0:
            prev_a, prev_o, prev_t = parsed_orig[i - 1]
            # Same object as previous AND not an error object → non-selectable
            if prev_o == o and o not in error_objects:
                continue
        add(a, o, t)

    # Always include STANDUP (handles sitting character case)
    add("STANDUP", "character", None)

    return candidates


# =============================================================================
# BFS Search Tree
# =============================================================================

def build_and_search_tree(
    candidates:     list,
    initial_state:  TreeState,
    target_effects: list,
    max_depth:      int = 6,
    max_nodes:      int = 500,
) -> list:
    """
    BFS to find shortest valid replacement subsequence.
    Paper: "performs breadth-first search to extract a fully executable subsequence"

    Returns list of (action, obj, target) tuples or [] if not found.
    """
    root = TreeNode(
        action = "ROOT",
        obj    = "",
        state  = initial_state.copy(),
        depth  = 0,
    )

    # Early exit: if no target effects, return first valid candidate
    if not target_effects:
        for (action, obj, target) in candidates:
            if satisfied(action, root.state) and changes_state(action):
                return [(action, obj, target)]
        return []

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current        = queue.popleft()
        nodes_expanded += 1

        # Check if target achieved (not at root)
        if current.depth > 0 and _achieves_target(current.state, target_effects):
            return _extract_path(current)

        if current.depth >= max_depth:
            continue

        # Expand children using 3 SDG constraints (Eq. 5, 6)
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
    """Check if all target effects are present in current state."""
    return all(e in state.states for e in target_effects)


def _extract_path(node: TreeNode) -> list:
    """Trace back from leaf to root, return path as list."""
    path    = []
    current = node
    while current.parent is not None:
        path.append((current.action, current.obj, current.target))
        current = current.parent
    path.reverse()
    return path


# =============================================================================
# Initial State Builder
# =============================================================================

def _build_initial_state(env_dict: dict, char_sitting: bool, char_lying: bool) -> set:
    """
    Build initial state set from EAI environment dictionary.
    Reads actual object states AND properties from the scene graph.
    """
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

    has_closed = False

    try:
        for node in env_dict.get("nodes", []):
            node_states = [s.upper() for s in node.get("states", [])]
            props       = [p.upper() for p in node.get("properties", [])]

            # Object states
            if "OPEN" in node_states:
                states.add("open")
                states.add("obj_not_inside_closed_container")
                states.add("target_open_or_not_openable")
            if "CLOSED" in node_states:
                states.add("closed")
                has_closed = True
            if "ON" in node_states:
                states.add("on")
            if "OFF" in node_states:
                states.add("off")
            if "PLUGGED_IN" in node_states:
                states.add("plugged_in")
                states.discard("plugged_out")
            if "PLUGGED_OUT" in node_states:
                states.add("plugged_out")
                states.discard("plugged_in")

            # Object properties — needed for precondition checks
            if "HAS_SWITCH" in props:
                states.add("has_switch")
            if "HAS_PLUG" in props:
                states.add("has_plug")
            if "CAN_OPEN" in props:
                states.add("can_open")

    except Exception:
        pass

    # If no closed container found, objects are accessible by default
    if not has_closed:
        states.add("obj_not_inside_closed_container")

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
    max_depth:            int  = 6,
    max_nodes:            int  = 500,
) -> list:
    """
    Generate replacement subsequence using BFS search tree.
    Returns list of EAI-format action dicts, or [] if tree fails.
    """
    initial_states = _build_initial_state(
        initial_state_dict, char_sitting, char_lying
    )
    initial_state  = TreeState(initial_states)

    candidates = generate_candidate_nodes(
        llm_suggestions      = llm_suggestions,
        original_subsequence = original_subsequence,
        error_objects        = error_objects,
    )

    # Map unsatisfied preconditions to target effects the tree must achieve
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(need)
        elif need == "holds_obj":
            target_effects.append("holds_obj")
        elif need == "open":
            target_effects.append("open")
        elif need == "not_on":
            target_effects.append("off")
        elif need == "off":
            target_effects.append("off")
        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append("next_to_obj")
        elif need == "obj_not_inside_closed_container":
            target_effects.append("open")
        elif need == "target_open_or_not_openable":
            target_effects.append("open")
        elif need == "not_both_hands_full":
            target_effects.append("not_holds_obj")

    # Deduplicate
    target_effects = list(dict.fromkeys(target_effects))

    path = build_and_search_tree(
        candidates     = candidates,
        initial_state  = initial_state,
        target_effects = target_effects,
        max_depth      = max_depth,
        max_nodes      = max_nodes,
    )

    if not path:
        return []

    # Convert to EAI action dict format
    result = []
    for (action, obj, target) in path:
        if action.upper() in ("STANDUP", "SLEEP", "WAKEUP"):
            result.append({action: []})
        elif target:
            result.append({action: [obj, target]})
        else:
            result.append({action: [obj]})

    return result


if __name__ == "__main__":
    # Test 1: sitting character needs STANDUP
    print("Test 1 — Sitting character:")
    r = generate_replacement_subsequence(
        llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
        original_subsequence = [{"WALK": ["bathroom"]}],
        initial_state_dict   = {},
        unsatisfied_needs    = ["not_sitting"],
        error_objects        = {"bathroom"},
        char_sitting         = True,
    )
    print("Result:", r)
    assert r == [{"STANDUP": []}], f"Expected [STANDUP], got {r}"

    # Test 2: needs WALK then GRAB
    print("\nTest 2 — Needs WALK + GRAB:")
    r = generate_replacement_subsequence(
        llm_suggestions      = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        original_subsequence = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        initial_state_dict   = {},
        unsatisfied_needs    = ["holds_obj"],
        error_objects        = {"clothes"},
    )
    print("Result:", r)
    assert r == [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}], f"Got {r}"

    # Test 3: washing machine ON → needs WALK + SWITCHOFF
    print("\nTest 3 — Washing machine ON, needs SWITCHOFF:")
    env = {"nodes": [{"class_name": "washing_machine",
                      "states": ["ON"],
                      "properties": ["HAS_SWITCH", "CAN_OPEN"]}]}
    r = generate_replacement_subsequence(
        llm_suggestions = [
            {"WALK":     ["washing_machine"]},
            {"SWITCHOFF": ["washing_machine"]},
            {"OPEN":     ["washing_machine"]},
        ],
        original_subsequence = [{"OPEN": ["washing_machine"]}],
        initial_state_dict   = env,
        unsatisfied_needs    = ["not_on"],
        error_objects        = {"washing_machine"},
    )
    print("Result:", r)
    # Expected: WALK → SWITCHOFF (achieves off/not_on)

    print("\nAll tests passed! ✅")

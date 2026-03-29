"""
action_subtree.py
=================
Adaptive Action SubTree Generation
Based on SDA-Planner paper Section 4.4

ID-safe version:
- internal object identity uses "class_name_id"
- LLM suggestions like ["light", "245"] are converted to "light_245"
- parsed EAI strings like [walk] <light> (245) are also converted to "light_245"
"""

from collections import deque
import re

from object_state_model import ObjectStateModel
from sdg import get_preconditions, get_effects


# =============================================================================
# ID helpers
# =============================================================================

def _combine_name_id(name, oid):
    """Convert (name, id) -> 'name_id'."""
    return f"{str(name).strip()}_{str(oid).strip()}"


def _split_name_id(obj):
    """
    Convert 'name_id' -> ('name', 'id') when possible.
    Returns (obj, None) if obj is not in that format.
    """
    s = str(obj).strip()
    if "_" not in s:
        return s, None
    base, maybe_id = s.rsplit("_", 1)
    if maybe_id.isdigit():
        return base, maybe_id
    return s, None


def _is_name_id(obj):
    _, oid = _split_name_id(obj)
    return oid is not None


# =============================================================================
# State wrapper for BFS — thin layer over ObjectStateModel
# =============================================================================

class TreeState:
    """
    Wraps ObjectStateModel for use in the BFS search tree.
    All precondition checks are per-object, not global.

    IMPORTANT:
    ObjectStateModel may still expect plain object names in your setup.
    So before calling into the model, we strip "name_id" -> "name".
    That preserves compatibility while still keeping instance identity
    inside subtree candidate generation and path output.
    """

    def __init__(self, model: ObjectStateModel):
        self.model = model

    def copy(self) -> "TreeState":
        return TreeState(self.model.copy())

    def apply(self, action: str, obj: str, target: str = None):
        obj_name, _ = _split_name_id(obj)
        tgt_name, _ = _split_name_id(target) if target is not None else (None, None)
        self.model.apply(action, obj_name, tgt_name)

    def satisfies(self, preconditions: list, obj: str, target: str = None) -> bool:
        obj_name, _ = _split_name_id(obj)
        tgt_name, _ = _split_name_id(target) if target is not None else (None, None)
        return len(self.model.check_all(preconditions, obj_name, tgt_name)) == 0

    def achieves(self, target_effects: list, obj: str, target: str = None) -> bool:
        return self.satisfies(target_effects, obj, target)


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

def satisfied(action: str, state: TreeState, obj: str, target: str = None) -> bool:
    """Eq. 5: satisfied(Aj, G) — all preconditions of action met for obj/target."""
    return state.satisfies(get_preconditions(action.upper()), obj, target)


def changes_state(action: str) -> bool:
    """Eq. 5: change(Aj, G) — action must have at least one effect."""
    return len(get_effects(action.upper())) > 0


def not_covered(parent_action: str, child_action: str) -> bool:
    """
    Eq. 6: notCovered(At, Aj)
    True if there exists a state s where parent affects s but child does not.
    Prevents child from completely overriding parent's work.
    """
    if parent_action is None or parent_action == "ROOT":
        return True

    parent_effects = set(get_effects(parent_action.upper()))
    child_effects  = set(get_effects(child_action.upper()))

    if not parent_effects:
        return True

    return any(pe not in child_effects for pe in parent_effects)


# =============================================================================
# Candidate Node Generation (Paper Section 4.4)
# =============================================================================

def generate_candidate_nodes(
    llm_suggestions:      list,
    original_subsequence: list,
    error_objects:        set,
    char_sitting:         bool = False,
    char_lying:           bool = False,
) -> list:
    """
    Generate candidate action nodes from two sources:
    1. LLM corrective suggestions (primary — fixes the error)
    2. Original failing subsequence (secondary — ensures coverage)

    Internal representation:
      obj / target are always either:
      - "class_name_id"
      - "character" for zero-arg pseudo object
    """
    candidates = []
    seen       = set()

    def add(action, obj, target=None):
        key = (action.upper(), obj, target)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    def parse_item(item):
        """
        Accepted formats:
        1) {"WALK": ["light", "245"]} -> ("WALK", "light_245", None)
        2) {"PUTIN": ["apple", "7", "fridge", "2"]} -> ("PUTIN", "apple_7", "fridge_2")
        3) {"WALK": ["light_245"]} -> ("WALK", "light_245", None)
        4) {"PUTIN": ["apple_7", "fridge_2"]} -> ("PUTIN", "apple_7", "fridge_2")
        5) [walk] <light> (245) -> ("WALK", "light_245", None)
        6) [putin] <apple> (7) <fridge> (2) -> ("PUTIN", "apple_7", "fridge_2")
        """
        if isinstance(item, dict):
            for action, args in item.items():
                if not isinstance(args, list):
                    return action, "character", None

                if len(args) == 0:
                    return action, "character", None

                if len(args) == 1:
                    return action, str(args[0]), None

                if len(args) == 2:
                    a0 = str(args[0]).strip()
                    a1 = str(args[1]).strip()

                    # one-object [name, id]
                    if a1.isdigit():
                        return action, _combine_name_id(a0, a1), None

                    # two-object already combined [obj, target]
                    return action, a0, a1

                if len(args) == 4:
                    a0, a1, a2, a3 = [str(x).strip() for x in args]
                    if a1.isdigit() and a3.isdigit():
                        return action, _combine_name_id(a0, a1), _combine_name_id(a2, a3)

                    # fallback
                    return action, a0, a2

                # fallback for weird formats
                return action, str(args[0]).strip(), str(args[1]).strip() if len(args) > 1 else None

        s = str(item)
        am = re.search(r'\[(\w+)\]', s)
        pairs = re.findall(r'<([^>]+)>\s*\((\d+)\)', s)

        if am:
            action = am.group(1)
            if len(pairs) == 0:
                return action, "character", None
            if len(pairs) == 1:
                return action, _combine_name_id(pairs[0][0], pairs[0][1]), None
            return action, _combine_name_id(pairs[0][0], pairs[0][1]), _combine_name_id(pairs[1][0], pairs[1][1])

        return None, None, None

    # Normalize error objects too
    normalized_error_objects = set(str(x) for x in error_objects)

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
            if prev_o == o and o not in normalized_error_objects:
                continue
        add(a, o, t)

    if char_sitting or char_lying:
        add("STANDUP", "character", None)

    return candidates


# =============================================================================
# BFS Search Tree
# =============================================================================

def build_and_search_tree(
    candidates:     list,
    initial_model:  ObjectStateModel,
    target_effects: list,
    error_objects:  set = None,
    max_depth:      int = 6,
    max_nodes:      int = 500,
) -> list:
    """
    BFS to find shortest valid replacement subsequence.

    target_effects is a list of tuples: ("check", precondition, specific_obj)
      - specific_obj=None means check against the candidate's own obj
      - specific_obj=<name_id> means always check against that specific object
    """
    initial_state = TreeState(initial_model.copy())
    error_objects = error_objects or set()

    root = TreeNode(
        action="ROOT",
        obj="",
        state=initial_state,
        depth=0,
    )

    def _achieves(state: TreeState, node: TreeNode) -> bool:
        if not target_effects:
            return False
        for (_, precondition, specific_obj) in target_effects:
            check_obj = specific_obj if specific_obj else node.obj
            check_tgt = node.target if not specific_obj else None
            obj_name, _ = _split_name_id(check_obj)
            tgt_name, _ = _split_name_id(check_tgt) if check_tgt is not None else (None, None)
            if not state.model.satisfies(precondition, obj_name, tgt_name):
                return False
        return True

    if not target_effects:
        for (action, obj, target) in candidates:
            if satisfied(action, root.state, obj, target) and changes_state(action):
                return [(action, obj, target)]
        return []

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current        = queue.popleft()
        nodes_expanded += 1

        if current.depth > 0 and _achieves(current.state, current):
            return _extract_path(current)

        if current.depth >= max_depth:
            continue

        for (action, obj, target) in candidates:
            if not satisfied(action, current.state, obj, target):
                continue

            simulated = current.state.copy()
            simulated.apply(action, obj, target)

            temp_node = TreeNode(
                action=action,
                obj=obj,
                target=target,
                parent=current,
                state=simulated,
                depth=current.depth + 1,
            )
            is_terminal = _achieves(simulated, temp_node)

            if not changes_state(action) and not is_terminal:
                continue

            if not not_covered(current.action, action):
                continue

            new_state = current.state.copy()
            new_state.apply(action, obj, target)

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


def _extract_path(node: TreeNode) -> list:
    path = []
    current = node
    while current.parent is not None:
        path.append((current.action, current.obj, current.target))
        current = current.parent
    path.reverse()
    return path


# =============================================================================
# Initial State Builder
# =============================================================================

def _build_initial_state(
    env_dict:     dict,
    char_sitting: bool,
    char_lying:   bool,
) -> ObjectStateModel:
    return ObjectStateModel.from_env_dict(
        env_dict or {},
        char_sitting=char_sitting,
        char_lying=char_lying,
    )


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
    Returns list of dict actions using ID-safe object strings, e.g.:
      {"WALK": ["light_245"]}
      {"PUTIN": ["apple_7", "fridge_2"]}
    """
    initial_model = _build_initial_state(
        initial_state_dict, char_sitting, char_lying
    )

    normalized_error_objects = set(str(x) for x in error_objects)

    guaranteed_candidates = []
    container_targets     = {}

    needs_set = set(unsatisfied_needs)

    if "obj_not_inside_closed_container" in needs_set or \
       "target_open_or_not_openable" in needs_set:
        for obj in normalized_error_objects:
            obj_name, _ = _split_name_id(obj)
            container = initial_model.get_container(obj_name)
            if container and not initial_model.satisfies("open", container):
                # container from model may be plain name; keep as plain unless you have IDs for it
                container_targets[obj] = str(container)
                guaranteed_candidates.append(("WALK", str(container), None))
                guaranteed_candidates.append(("OPEN", str(container), None))

        for obj in normalized_error_objects:
            if obj not in container_targets:
                continue
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("GRAB", obj, None))

    if "not_both_hands_full" in needs_set:
        for held_obj in filter(None, [initial_model.hand_right, initial_model.hand_left]):
            guaranteed_candidates.append(("DROP", str(held_obj), None))

    candidates = generate_candidate_nodes(
        llm_suggestions=llm_suggestions,
        original_subsequence=original_subsequence,
        error_objects=normalized_error_objects,
        char_sitting=char_sitting,
        char_lying=char_lying,
    )

    seen_keys = set()
    all_candidates = []
    for c in guaranteed_candidates + candidates:
        if c not in seen_keys:
            seen_keys.add(c)
            all_candidates.append(c)

    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(("check", need, None))

        elif need == "holds_obj":
            for obj in normalized_error_objects:
                target_effects.append(("check", "holds_obj", obj))
            break

        elif need == "open":
            for obj in normalized_error_objects:
                target_effects.append(("check", "open", obj))
            break

        elif need in ("not_on", "off"):
            for obj in normalized_error_objects:
                target_effects.append(("check", "off", obj))
            break

        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append(("check", "next_to_obj", None))

        elif need == "obj_not_inside_closed_container":
            for obj in normalized_error_objects:
                obj_name, _ = _split_name_id(obj)
                container = container_targets.get(obj) or initial_model.get_container(obj_name)
                if container:
                    target_effects.append(("check", "open", str(container)))
                if obj != "character":
                    target_effects.append(("check", "holds_obj", obj))

        elif need == "target_open_or_not_openable":
            for obj in normalized_error_objects:
                obj_name, _ = _split_name_id(obj)
                container = container_targets.get(obj) or initial_model.get_container(obj_name)
                if container:
                    target_effects.append(("check", "open", str(container)))

        elif need == "not_both_hands_full":
            target_effects.append(("check", "not_holds_obj", None))

        elif need == "facing_obj":
            target_effects.append(("check", "facing_obj", None))

        elif need == "plugged_in":
            for obj in normalized_error_objects:
                target_effects.append(("check", "plugged_in", obj))
            break

    seen_te = set()
    deduped_te = []
    for te in target_effects:
        if te not in seen_te:
            seen_te.add(te)
            deduped_te.append(te)
    target_effects = deduped_te

    path = build_and_search_tree(
        candidates=all_candidates,
        initial_model=initial_model,
        target_effects=target_effects,
        error_objects=normalized_error_objects,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    if not path:
        return []

    result = []
    ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
    for (action, obj, target) in path:
        if action.upper() in ZERO_ARG:
            result.append({action: []})
        elif target:
            result.append({action: [obj, target]})
        else:
            result.append({action: [obj]})

    return result


if __name__ == "__main__":
    print("action_subtree.py ID-safe version loaded.")
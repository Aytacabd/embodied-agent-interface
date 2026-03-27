"""
action_subtree.py
=================
Adaptive Action SubTree Generation
Based on SDA-Planner paper Section 4.4
... (docstring same) ...
"""

from collections import deque
from typing import List, Tuple, Optional, Set, Dict, Any

from object_state_model import ObjectStateModel
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# =============================================================================
# State wrapper for BFS — thin layer over ObjectStateModel
# =============================================================================

class TreeState:
    def __init__(self, model: ObjectStateModel):
        self.model = model

    def copy(self) -> "TreeState":
        return TreeState(self.model.copy())

    def apply(self, action: str, obj_id: Optional[int], target_id: Optional[int] = None):
        """Apply action effects to the underlying model."""
        self.model.apply(action, obj_id, target_id)

    def satisfies(self, preconditions: List[str], obj_id: Optional[int],
                  target_id: Optional[int] = None) -> bool:
        """True if ALL preconditions hold for this obj/target."""
        return len(self.model.check_all(preconditions, obj_id, target_id)) == 0

    def achieves(self, target_effects: List[str], obj_id: Optional[int],
                 target_id: Optional[int] = None) -> bool:
        return self.satisfies(target_effects, obj_id, target_id)


class TreeNode:
    def __init__(self, action: str, obj_id: Optional[int], target_id: Optional[int] = None,
                 parent=None, state: TreeState = None, depth: int = 0):
        self.action = action.upper()
        self.obj_id = obj_id
        self.target_id = target_id
        self.parent = parent
        self.state = state
        self.depth = depth

    def __repr__(self):
        if self.target_id is not None:
            return f"{self.action}({self.obj_id}, {self.target_id})"
        return f"{self.action}({self.obj_id})"


# =============================================================================
# SDG Constraint Functions (Paper Equations 5 and 6)
# =============================================================================

def satisfied(action: str, state: TreeState,
              obj_id: Optional[int], target_id: Optional[int] = None) -> bool:
    return state.satisfies(get_preconditions(action.upper()), obj_id, target_id)


def changes_state(action: str) -> bool:
    return len(get_effects(action.upper())) > 0


def not_covered(parent_action: str, child_action: str) -> bool:
    if parent_action is None or parent_action == "ROOT":
        return True
    parent_effects = set(get_effects(parent_action.upper()))
    child_effects = set(get_effects(child_action.upper()))
    if not parent_effects:
        return True
    return any(pe not in child_effects for pe in parent_effects)


# =============================================================================
# Candidate Node Generation
# =============================================================================

def resolve_object_id(model: ObjectStateModel, obj: Any) -> Optional[int]:
    if isinstance(obj, int):
        return obj if obj in model.states_by_id else None
    ids = model._resolve(obj)
    return next(iter(ids)) if ids else None


def generate_candidate_nodes(
    llm_suggestions: List[Dict],
    original_subsequence: List[Dict],
    error_objects: Set[int],
    initial_model: ObjectStateModel,
    char_sitting: bool = False,
    char_lying: bool = False,
) -> List[Tuple[str, Optional[int], Optional[int]]]:
    candidates = []
    seen = set()

    def add(action: str, obj_id: Optional[int], target_id: Optional[int] = None):
        key = (action.upper(), obj_id, target_id)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    for item in llm_suggestions:
        for action, args in item.items():
            action = action.upper()
            if not args:
                # zero‑argument action (STANDUP, SLEEP, WAKEUP)
                add(action, None)
            else:
                obj_name = args[0]
                obj_id = resolve_object_id(initial_model, obj_name)
                if obj_id is None:
                    continue
                if len(args) == 1:
                    add(action, obj_id)
                else:
                    target_name = args[1]
                    target_id = resolve_object_id(initial_model, target_name)
                    if target_id is None:
                        add(action, obj_id)
                    else:
                        add(action, obj_id, target_id)

    # Original subsequence
    orig_steps = []
    for item in original_subsequence:
        for action, args in item.items():
            action = action.upper()
            if not args:
                orig_steps.append((action, None, None))
            else:
                obj_name = args[0]
                obj_id = resolve_object_id(initial_model, obj_name)
                if obj_id is None:
                    continue
                if len(args) == 1:
                    orig_steps.append((action, obj_id, None))
                else:
                    target_name = args[1]
                    target_id = resolve_object_id(initial_model, target_name)
                    orig_steps.append((action, obj_id, target_id))

    # Constrained subsequence rule
    for i, (action, obj_id, target_id) in enumerate(orig_steps):
        if i > 0:
            prev_action, prev_obj, prev_tgt = orig_steps[i-1]
            if prev_obj == obj_id and obj_id not in error_objects:
                continue
        add(action, obj_id, target_id)

    # Include STANDUP only if sitting or lying
    if char_sitting or char_lying:
        add("STANDUP", None)

    return candidates


# =============================================================================
# BFS Search Tree
# =============================================================================

def build_and_search_tree(
    candidates: List[Tuple[str, Optional[int], Optional[int]]],
    initial_model: ObjectStateModel,
    target_effects: List[Tuple[str, str, Optional[int]]],
    error_objects: Set[int] = None,
    max_depth: int = 6,
    max_nodes: int = 500,
) -> List[Tuple[str, Optional[int], Optional[int]]]:
    initial_state = TreeState(initial_model.copy())
    error_objects = error_objects or set()

    root = TreeNode(
        action="ROOT",
        obj_id=None,
        state=initial_state,
        depth=0,
    )

    def _achieves(state: TreeState, node: TreeNode) -> bool:
        if not target_effects:
            return False
        for (_, precondition, specific_obj_id) in target_effects:
            check_obj = specific_obj_id if specific_obj_id is not None else node.obj_id
            check_tgt = node.target_id if specific_obj_id is None else None
            if not state.model.satisfies(precondition, check_obj, check_tgt):
                return False
        return True

    if not target_effects:
        for (action, obj_id, target_id) in candidates:
            if satisfied(action, root.state, obj_id, target_id) and changes_state(action):
                return [(action, obj_id, target_id)]
        return []

    queue = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current = queue.popleft()
        nodes_expanded += 1

        if current.depth > 0 and _achieves(current.state, current):
            return _extract_path(current)

        if current.depth >= max_depth:
            continue

        for (action, obj_id, target_id) in candidates:
            if not satisfied(action, current.state, obj_id, target_id):
                continue

            # Simulate to see if it achieves the target
            simulated = current.state.copy()
            simulated.apply(action, obj_id, target_id)
            temp_node = TreeNode(action=action, obj_id=obj_id, target_id=target_id,
                                 parent=current, state=simulated, depth=current.depth+1)
            is_terminal = _achieves(simulated, temp_node)

            if not changes_state(action) and not is_terminal:
                continue

            if not not_covered(current.action, action):
                continue

            new_state = current.state.copy()
            new_state.apply(action, obj_id, target_id)
            child = TreeNode(
                action=action,
                obj_id=obj_id,
                target_id=target_id,
                parent=current,
                state=new_state,
                depth=current.depth+1,
            )
            queue.append(child)

    return []


def _extract_path(node: TreeNode) -> List[Tuple[str, Optional[int], Optional[int]]]:
    path = []
    current = node
    while current.parent is not None:
        path.append((current.action, current.obj_id, current.target_id))
        current = current.parent
    path.reverse()
    return path


# =============================================================================
# Initial State Builder
# =============================================================================

def _build_initial_state(env_dict: Dict, char_sitting: bool, char_lying: bool) -> ObjectStateModel:
    return ObjectStateModel.from_env_dict(env_dict)


# =============================================================================
# Main Entry Point
# =============================================================================

def generate_replacement_subsequence(
    llm_suggestions: List[Dict],
    original_subsequence: List[Dict],
    initial_state_dict: Dict,
    unsatisfied_needs: List[str],
    error_objects: Set[Any],
    char_sitting: bool = False,
    char_lying: bool = False,
    max_depth: int = 6,
    max_nodes: int = 500,
) -> List[Dict]:
    initial_model = _build_initial_state(initial_state_dict, char_sitting, char_lying)

    # Convert error_objects to IDs
    error_ids = set()
    for obj in error_objects:
        obj_id = resolve_object_id(initial_model, obj)
        if obj_id is not None:
            error_ids.add(obj_id)
        elif isinstance(obj, int):
            error_ids.add(obj)

    # Guaranteed candidates for container issues
    guaranteed_candidates = []
    container_targets = {}
    needs_set = set(unsatisfied_needs)
    if "obj_not_inside_closed_container" in needs_set or "target_open_or_not_openable" in needs_set:
        for obj_id in error_ids:
            container = initial_model.get_container(obj_id)
            if container is not None and not initial_model.satisfies("open", container):
                container_targets[obj_id] = container
                guaranteed_candidates.append(("WALK", container, None))
                guaranteed_candidates.append(("OPEN", container, None))

    candidates = generate_candidate_nodes(
        llm_suggestions=llm_suggestions,
        original_subsequence=original_subsequence,
        error_objects=error_ids,
        initial_model=initial_model,
        char_sitting=char_sitting,
        char_lying=char_lying,
    )

    # Merge candidates (guaranteed first)
    seen_keys = set()
    all_candidates = []
    for c in guaranteed_candidates + candidates:
        if c not in seen_keys:
            seen_keys.add(c)
            all_candidates.append(c)

    # Map unsatisfied needs to target effects
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(("check", need, None))
        elif need == "holds_obj":
            for obj_id in error_ids:
                target_effects.append(("check", "holds_obj", obj_id))
            break
        elif need == "open":
            for obj_id in error_ids:
                target_effects.append(("check", "open", obj_id))
            break
        elif need in ("not_on", "off"):
            for obj_id in error_ids:
                target_effects.append(("check", "off", obj_id))
            break
        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append(("check", "next_to_obj", None))
        elif need == "obj_not_inside_closed_container":
            for obj_id in error_ids:
                container = container_targets.get(obj_id) or initial_model.get_container(obj_id)
                if container:
                    target_effects.append(("check", "open", container))
        elif need == "target_open_or_not_openable":
            for obj_id in error_ids:
                container = container_targets.get(obj_id) or initial_model.get_container(obj_id)
                if container:
                    target_effects.append(("check", "open", container))
        elif need == "not_both_hands_full":
            target_effects.append(("check", "not_holds_obj", None))
        elif need == "facing_obj":
            target_effects.append(("check", "facing_obj", None))
        elif need == "plugged_in":
            for obj_id in error_ids:
                target_effects.append(("check", "plugged_in", obj_id))
            break

    # Deduplicate target effects
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
        error_objects=error_ids,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    if not path:
        return []

    # Convert path to EAI action dict format (using class names)
    result = []
    for (action, obj_id, target_id) in path:
        if action in ("STANDUP", "SLEEP", "WAKEUP") and obj_id is None:
            result.append({action: []})
        else:
            obj_class = initial_model.id_to_class.get(obj_id, "unknown") if obj_id is not None else "character"
            if target_id is not None:
                target_class = initial_model.id_to_class.get(target_id, "unknown")
                result.append({action: [obj_class, target_class]})
            else:
                result.append({action: [obj_class]})
    return result


# =============================================================================
# Test Cases (same as before but now using None for zero-arg actions)
# =============================================================================

if __name__ == "__main__":
    # Test 1: sitting character needs STANDUP
    env1 = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": ["SITTING"], "properties": []},
            {"id": 2, "class_name": "bathroom", "states": [], "properties": []},
        ],
        "edges": []
    }
    r1 = generate_replacement_subsequence(
        llm_suggestions=[{"STANDUP": []}, {"WALK": ["bathroom"]}],
        original_subsequence=[{"WALK": ["bathroom"]}],
        initial_state_dict=env1,
        unsatisfied_needs=["not_sitting"],
        error_objects={"bathroom"},
        char_sitting=True,
    )
    print("Result:", r1)
    assert r1 == [{"STANDUP": []}], f"Expected [STANDUP], got {r1}"
    print("✅ Test 1 passed\n")

    # Test 2: need WALK + GRAB
    env2 = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "clothes", "states": [], "properties": ["GRABBABLE", "CLOTHES"]},
        ],
        "edges": []
    }
    r2 = generate_replacement_subsequence(
        llm_suggestions=[{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        original_subsequence=[{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        initial_state_dict=env2,
        unsatisfied_needs=["holds_obj"],
        error_objects={"clothes"},
    )
    print("Result:", r2)
    actions = [list(a.keys())[0] for a in r2]
    assert "WALK" in actions and "GRAB" in actions, f"Got {r2}"
    print("✅ Test 2 passed\n")

    # Test 3: washing machine ON → needs SWITCHOFF
    env3 = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "washing_machine",
             "states": ["ON"], "properties": ["HAS_SWITCH", "CAN_OPEN"]},
        ],
        "edges": [{"from_id": 1, "to_id": 2, "relation_type": "CLOSE"}]
    }
    r3 = generate_replacement_subsequence(
        llm_suggestions=[
            {"WALK": ["washing_machine"]},
            {"SWITCHOFF": ["washing_machine"]},
            {"OPEN": ["washing_machine"]},
        ],
        original_subsequence=[{"OPEN": ["washing_machine"]}],
        initial_state_dict=env3,
        unsatisfied_needs=["not_on"],
        error_objects={"washing_machine"},
    )
    print("Result:", r3)
    actions = [list(a.keys())[0] for a in r3]
    assert "SWITCHOFF" in actions, f"Expected SWITCHOFF, got {r3}"
    print("✅ Test 3 passed\n")

    # Test 4: apple inside closed fridge
    env4 = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "fridge", "states": ["CLOSED"], "properties": ["CAN_OPEN"]},
            {"id": 3, "class_name": "apple", "states": [], "properties": ["GRABBABLE", "EATABLE"]},
        ],
        "edges": [
            {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
        ]
    }
    r4 = generate_replacement_subsequence(
        llm_suggestions=[
            {"WALK": ["fridge"]},
            {"OPEN": ["fridge"]},
            {"GRAB": ["apple"]},
        ],
        original_subsequence=[{"GRAB": ["apple"]}],
        initial_state_dict=env4,
        unsatisfied_needs=["obj_not_inside_closed_container"],
        error_objects={"apple"},
    )
    print("Result:", r4)
    actions = [list(a.keys())[0] for a in r4]
    assert "OPEN" in actions, f"Expected OPEN, got {r4}"
    print("✅ Test 4 passed\n")

    print("All tests passed! ✅")
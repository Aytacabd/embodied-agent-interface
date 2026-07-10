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


def _repair_class(obj):
    """Class name of an object, id stripped and lowercased (None -> None)."""
    if obj is None:
        return None
    name, _ = _split_name_id(str(obj))
    return (name or "").lower().strip() or None


def _canonical_repair_seq(seq):
    """
    Canonicalize a repair sequence for equality comparison.

    Accepts a sequence whose items are either EAI action dicts
    (e.g. {"WALK": ["tv"]}, {"PUTIN": ["apple", "7", "fridge", "2"]}) or
    (action, obj, target) tuples (the internal BFS path form). Returns a
    tuple of (ACTION, obj_class, target_class) steps with object ids stripped,
    so a "repeat" is detected by action + object class rather than exact id
    string (which differs between LLM suggestions and BFS-built paths).
    """
    steps = []
    for item in seq:
        if isinstance(item, dict):
            for action, args in item.items():
                a = str(action).upper()
                if not isinstance(args, list):
                    args = []
                if len(args) == 0:
                    steps.append((a, None, None))
                elif len(args) == 1:
                    steps.append((a, _repair_class(args[0]), None))
                elif len(args) == 2:
                    # [name, id] one-object, or [obj, target] two-object
                    if str(args[1]).strip().isdigit():
                        steps.append((a, _repair_class(args[0]), None))
                    else:
                        steps.append((a, _repair_class(args[0]), _repair_class(args[1])))
                elif len(args) == 4:
                    steps.append((a, _repair_class(args[0]), _repair_class(args[2])))
                else:
                    steps.append((
                        a,
                        _repair_class(args[0]),
                        _repair_class(args[1]) if len(args) > 1 else None,
                    ))
                break
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            a = str(item[0]).upper()
            o = item[1]
            t = item[2] if len(item) > 2 else None
            steps.append((a, _repair_class(o), _repair_class(t)))
    return tuple(steps)


# =============================================================================
# State wrapper for BFS — thin layer over ObjectStateModel
# =============================================================================

class TreeState:
    """
    Wraps ObjectStateModel for use in the BFS search tree.
    All precondition checks are per-object, not global.

    ObjectStateModel is instance-aware: it accepts both bare class names
    ("light") and instance-qualified names ("light_245"), answering
    per-instance for the latter. Names are passed through unmodified so
    BFS simulation stays instance-accurate in multi-instance scenes.
    """

    def __init__(self, model: ObjectStateModel):
        self.model = model

    def copy(self) -> "TreeState":
        return TreeState(self.model.copy())

    def apply(self, action: str, obj: str, target: str = None):
        self.model.apply(action, obj, target)

    def satisfies(self, preconditions: list, obj: str, target: str = None) -> bool:
        return len(self.model.check_all(preconditions, obj, target)) == 0

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

    # Bind bare class names to the failing instance: when a suggestion says
    # "light" and exactly one error object is an instance of that class
    # (light_411), upgrade it — otherwise resolution later rejects the bare
    # name as ambiguous and the repair is silently dropped.
    _instance_by_class = {}
    for _e in error_objects or ():
        _name, _oid = _split_name_id(str(_e))
        if _oid is not None:
            _instance_by_class.setdefault(_name, set()).add(str(_e))

    def _bind_instance(token):
        if token is None or _is_name_id(token):
            return token
        matches = _instance_by_class.get(str(token).strip())
        if matches and len(matches) == 1:
            return next(iter(matches))
        return token

    def add(action, obj, target=None):
        key = (action.upper(), _bind_instance(obj), _bind_instance(target))
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
    excluded_paths: set = None,
) -> list:
    """
    BFS to find shortest valid replacement subsequence.

    target_effects is a list of tuples: ("check", precondition, specific_obj)
      - specific_obj=None means check against the candidate's own obj
      - specific_obj=<name_id> means always check against that specific object

    excluded_paths: set of canonical repair tuples (see _canonical_repair_seq)
      that already failed on prior replan attempts. A solution path whose
      canonical form is in this set is rejected and the search continues, so
      the same repair is never returned twice for the same failing action.
    """
    initial_state = TreeState(initial_model.copy())
    error_objects = error_objects or set()
    excluded_paths = excluded_paths or set()

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
            # Pass names through unmodified — the model is instance-aware,
            # so "light_411" is checked per-instance, "light" class-level.
            if not state.model.satisfies(precondition, check_obj, check_tgt):
                return False
        return True

    if not target_effects:
        for (action, obj, target) in candidates:
            if satisfied(action, root.state, obj, target) and changes_state(action):
                if _canonical_repair_seq([(action, obj, target)]) in excluded_paths:
                    continue
                return [(action, obj, target)]
        return []

    def _state_signature(state: TreeState) -> tuple:
        """Hashable snapshot of everything satisfies() can read."""
        m = state.model
        return (
            tuple(sorted((k, tuple(sorted(v)))
                         for k, v in m.object_states.items() if v)),
            tuple(sorted((k, tuple(sorted(v)))
                         for k, v in m.relations.items() if v)),
            tuple(sorted(m.container_of.items())),
            m.hand_right, m.hand_left, m.char_sitting, m.char_lying,
        )

    # Visited-state dedup (efficiency only). Disabled when:
    #  - excluded_paths is set: a *different* path to the same state is exactly
    #    what the exclusion mechanism needs BFS to find, or
    #  - any target effect binds to the candidate's own obj (specific_obj None):
    #    goal achievement then depends on the node, not just the state.
    use_dedup = (
        not excluded_paths
        and all(so is not None for (_, _, so) in target_effects)
    )
    visited = {_state_signature(initial_state)} if use_dedup else None

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current        = queue.popleft()
        nodes_expanded += 1

        if current.depth > 0 and _achieves(current.state, current):
            path = _extract_path(current)
            if _canonical_repair_seq(path) not in excluded_paths:
                return path
            # Previously-failed repair — skip it and keep searching for a
            # different path (do not expand this already-achieved goal node).
            continue

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

            if use_dedup:
                sig = _state_signature(new_state)
                if sig in visited:
                    continue
                visited.add(sig)

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
    failed_obj:           str  = None,
    failed_target:        str  = None,
    excluded_repairs:     list = None,
) -> list:
    """
    Generate replacement subsequence using BFS search tree.
    Returns list of dict actions using ID-safe object strings, e.g.:
      {"WALK": ["light_245"]}
      {"PUTIN": ["apple_7", "fridge_2"]}

    excluded_repairs: list of previously-tried repair sequences (EAI dicts)
      that failed for this same action. Any BFS solution matching one of them
      (by action + object class) is rejected, guaranteeing the search never
      returns a repair that already failed.
    """
    excluded_paths = {
        _canonical_repair_seq(seq)
        for seq in (excluded_repairs or [])
        if seq
    }
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
            container = initial_model.get_container(obj)
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

    # target_open_or_not_openable: the failed TARGET is itself the container
    # that must be opened (e.g. PUTIN x into a CLOSED washing_machine). The
    # block above only handles objects INSIDE a container, never the target.
    if "target_open_or_not_openable" in needs_set and failed_target:
        tgt_name, _ = _split_name_id(failed_target)
        if not initial_model.satisfies("open", tgt_name):
            # If the appliance is ON, OPEN is blocked (not_on precondition)
            # until it is switched off first.
            if initial_model.satisfies("on", tgt_name):
                guaranteed_candidates.append(("WALK", str(failed_target), None))
                guaranteed_candidates.append(("SWITCHOFF", str(failed_target), None))
            guaranteed_candidates.append(("WALK", str(failed_target), None))
            guaranteed_candidates.append(("OPEN", str(failed_target), None))

    if "not_both_hands_full" in needs_set:
        for held_obj in filter(None, [initial_model.hand_right, initial_model.hand_left]):
            guaranteed_candidates.append(("DROP", str(held_obj), None))

    if "holds_knife" in needs_set:
        # CUT needs a held knife. Find any knife class in the scene so BFS can
        # WALK to it and GRAB it deterministically (executor accepts any "knife*").
        knife_name = next(
            (n for n in initial_model.name_to_ids if "knife" in n),
            None,
        )
        if knife_name:
            guaranteed_candidates.append(("WALK", str(knife_name), None))
            guaranteed_candidates.append(("GRAB", str(knife_name), None))

    if "not_on" in needs_set:
        for obj in normalized_error_objects:
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("SWITCHOFF", obj, None))

    if "plugged_in" in needs_set:
        for obj in normalized_error_objects:
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("PLUGIN", obj, None))

    if "plugged_out" in needs_set:
        for obj in normalized_error_objects:
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("PLUGOUT", obj, None))

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

    # Bind spatial/global checks to the failed action's own objects so BFS
    # terminates on the state the retried action actually needs — not on
    # whichever candidate node happens to satisfy the predicate for its own
    # object (e.g. WALK(tool) must not satisfy a next_to(failed_obj) goal).
    _fobj = failed_obj if failed_obj and failed_obj != "unknown" else None
    _ftgt = failed_target if failed_target else None

    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(("check", need, "character"))

        elif need == "holds_obj":
            for obj in normalized_error_objects:
                target_effects.append(("check", "holds_obj", obj))
            break

        elif need == "holds_any_obj":
            # WIPE: BFS just needs character to grab anything (e.g. a sponge)
            target_effects.append(("check", "holds_any_obj", "character"))

        elif need == "holds_knife":
            # CUT: BFS needs character holding a knife (checked against hands,
            # obj argument is ignored by the holds_knife predicate)
            target_effects.append(("check", "holds_knife", "character"))

        elif need == "open":
            for obj in normalized_error_objects:
                target_effects.append(("check", "open", obj))
            break

        elif need in ("not_on", "off"):
            for obj in normalized_error_objects:
                target_effects.append(("check", "off", obj))
            break

        elif need == "next_to_obj":
            target_effects.append(("check", "next_to_obj", _fobj))

        elif need == "next_to_target":
            target_effects.append(("check", "next_to_obj", _ftgt))

        elif need == "obj_not_inside_closed_container":
            for obj in normalized_error_objects:
                obj_name, _ = _split_name_id(obj)
                container = container_targets.get(obj) or initial_model.get_container(obj)
                if container:
                    # BFS stops at OPEN(container); failed_eai retries the original action
                    target_effects.append(("check", "open", str(container)))
                elif obj != "character" and initial_model.satisfies("grabbable", obj_name):
                    # Only grabbable objects can satisfy holds_obj. Guarding this
                    # prevents an impossible goal (e.g. "hold the dining_room")
                    # from being handed to BFS, which would make it return [].
                    target_effects.append(("check", "holds_obj", obj))

        elif need == "target_open_or_not_openable":
            appended = False
            for obj in normalized_error_objects:
                obj_name, _ = _split_name_id(obj)
                container = container_targets.get(obj) or initial_model.get_container(obj)
                if container:
                    target_effects.append(("check", "open", str(container)))
                    appended = True
            if not appended and _ftgt:
                # No error object is inside a container — the failed target is
                # itself the container to open (PUTIN into a closed appliance).
                tgt_name, _ = _split_name_id(_ftgt)
                target_effects.append(("check", "open", tgt_name))

        elif need == "not_both_hands_full":
            # Goal is "at least one free hand" — checking not_holds_obj against
            # an arbitrary node.obj let WALK(x) terminate the search without
            # freeing a hand. not_both_hands_full ignores its obj argument.
            target_effects.append(("check", "not_both_hands_full", "character"))

        elif need == "facing_obj":
            target_effects.append(("check", "facing_obj", _fobj))

        elif need == "plugged_in":
            for obj in normalized_error_objects:
                target_effects.append(("check", "plugged_in", obj))
            break

        elif need == "plugged_out":
            for obj in normalized_error_objects:
                target_effects.append(("check", "plugged_out", obj))
            break

        elif need == "on":
            for obj in normalized_error_objects:
                target_effects.append(("check", "on", obj))
            break

        elif need == "closed":
            for obj in normalized_error_objects:
                target_effects.append(("check", "closed", obj))
            break

        elif need == "on_char":
            for obj in normalized_error_objects:
                target_effects.append(("check", "on_char", obj))
            break

        elif need == "sitting_or_lying":
            target_effects.append(("check", "sitting_or_lying", "character"))

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
        excluded_paths=excluded_paths,
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
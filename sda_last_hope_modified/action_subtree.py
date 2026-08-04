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


def _goal_placement_for(held_obj, goal_edge_relations, model):
    """
    If held_obj ('name_id' token) has a known placement goal — an edge goal
    FROM its id TO some container/surface — return (action, target_token),
    using PUTIN for an INSIDE goal and PUTBACK otherwise. Returns (None,
    None) when there's no such goal (nothing better to do than DROP it).
    """
    if not goal_edge_relations:
        return None, None
    _, oid = _split_name_id(held_obj)
    if oid is None:
        return None, None
    oid = int(oid)
    for (from_id, to_id), relation in goal_edge_relations.items():
        if from_id == oid:
            target_name = model.id_to_name.get(to_id)
            if not target_name:
                continue
            action = "PUTIN" if str(relation).upper() == "INSIDE" else "PUTBACK"
            return action, f"{target_name}_{to_id}"
    return None, None


# =============================================================================
# State wrapper for BFS — thin layer over ObjectStateModel
# =============================================================================

class TreeState:
    """
    Wraps ObjectStateModel for use in the BFS search tree.
    All precondition checks are per-object, not global.

    ObjectStateModel is ID-keyed: "name_id" tokens are checked/applied
    against that specific instance, so we pass tokens straight through.
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


# Effects the state model can actually verify (mirrors the VERIFIABLE_EFFECTS
# gate in error_diagnosis.diagnose_error). satisfies() answers optimistic True
# for unknown predicates, so without this whitelist actions whose effects are
# all unverifiable (PUTBACK/PUTIN: obj_ontop_target, obj_inside_target) would
# read as no-ops and get pruned from the tree.
_VERIFIABLE_EFFECTS = {
    "open", "closed", "on", "off",
    "plugged_in", "plugged_out",
    "holds_obj", "next_to_obj", "facing_obj",
    "on_char",
}


def changes_state(action: str, state: TreeState = None,
                  obj: str = None, target: str = None) -> bool:
    """Eq. 5: change(Aj, G) — action must be able to change the CURRENT state.

    With a state given, at least one verifiable positive effect must not
    already hold — prunes no-op repairs (WALK to an object the character is
    already next to, OPEN on an already-open container). Without a state
    (or when no effect is verifiable) falls back to the static check.
    """
    effects = get_effects(action.upper())
    if not effects:
        return False
    if state is None:
        return True
    verifiable = [e for e in effects
                  if not e.startswith("not_") and e in _VERIFIABLE_EFFECTS]
    if not verifiable:
        return True
    return any(not state.model.satisfies(e, obj, target) for e in verifiable)


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
    banned_paths:   set = None,
) -> list:
    """
    BFS to find shortest valid replacement subsequence.

    target_effects is a list of tuples: ("check", precondition, specific_obj)
      - specific_obj=None means check against the candidate's own obj
      - specific_obj=<name_id> means always check against that specific object

    banned_paths: set of path keys (tuples of (ACTION, obj, target) triples)
    that were already tried and did not resolve the failure. A goal node
    whose path is banned is skipped but its subtree keeps expanding, so BFS
    naturally yields the next-shortest untried repair.
    """
    initial_state = TreeState(initial_model.copy())
    error_objects = error_objects or set()
    banned_paths  = banned_paths or set()

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
            if not state.model.satisfies(precondition, check_obj, check_tgt):
                return False
        return True

    if not target_effects:
        for (action, obj, target) in candidates:
            if ((action, obj, target),) in banned_paths:
                continue
            if (satisfied(action, root.state, obj, target)
                    and changes_state(action, root.state, obj, target)):
                return [(action, obj, target)]
        return []

    queue          = deque([root])
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current        = queue.popleft()
        nodes_expanded += 1

        if current.depth > 0 and _achieves(current.state, current):
            path = _extract_path(current)
            if tuple(path) not in banned_paths:
                return path
            # Banned repair — skip it but keep expanding so BFS can
            # surface the next-shortest alternative.

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

            if not changes_state(action, current.state, obj, target) and not is_terminal:
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
    failed_obj:           str  = None,
    failed_target:        str  = None,
    banned_paths:         set  = None,
    banned_candidates:    set  = None,
    goal_edge_relations:  dict = None,
    prefer_goal_placement: bool = True,
) -> list:
    """
    Generate replacement subsequence using BFS search tree.
    Returns list of dict actions using ID-safe object strings, e.g.:
      {"WALK": ["light_245"]}
      {"PUTIN": ["apple_7", "fridge_2"]}

    failed_obj / failed_target: obj and target of the action that failed.
    error_objects mixes both roles together, but the BFS goal must be
    role-aware — only the obj needs to be HELD, only the target needs to
    be OPEN. Without this the goal can demand e.g. holding a washing
    machine, which is unsatisfiable and wastes every replan attempt.

    goal_edge_relations / prefer_goal_placement: when a hands-full repair
    must free a hand, prefer_goal_placement=True (default) has it place a
    held object at its own goal destination instead of dropping it, IF
    goal_edge_relations shows one. Pass prefer_goal_placement=False to get
    the plain-DROP behavior (the caller's fallback when the goal-aware
    attempt returns empty).
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
            container = initial_model.get_container(obj)
            if container and not initial_model.satisfies("open", container):
                # ID-keyed model returns container as "name_id" token,
                # so these candidates resolve exactly at the output boundary.
                # PDDL open requires not_on — a running container (washing
                # machine SWITCHONed too early) must be switched off first.
                container_targets[obj] = str(container)
                if initial_model.satisfies("on", container):
                    guaranteed_candidates.append(("SWITCHOFF", str(container), None))
                guaranteed_candidates.append(("WALK", str(container), None))
                guaranteed_candidates.append(("OPEN", str(container), None))

        for obj in normalized_error_objects:
            if obj not in container_targets:
                continue
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("GRAB", obj, None))

    # The failed action's target itself is a closed container (e.g. PUTIN
    # into a closed washing machine): guarantee the opening sequence.
    # PDDL open requires not_on, so a running appliance needs SWITCHOFF first.
    if "target_open_or_not_openable" in needs_set:
        open_targets = [failed_target] if failed_target else list(normalized_error_objects)
        for obj in open_targets:
            if (initial_model.satisfies("can_open", obj)
                    and not initial_model.satisfies("open", obj)):
                if initial_model.satisfies("on", obj):
                    guaranteed_candidates.append(("SWITCHOFF", obj, None))
                guaranteed_candidates.append(("WALK", obj, None))
                guaranteed_candidates.append(("OPEN", obj, None))

    # Hand-freeing candidates. Needed when the failed action itself demands a
    # free hand, AND whenever a guaranteed repair chain contains an OPEN while
    # both hands are full — OPEN requires a free hand (executor rule, modeled
    # in the SDG), so those chains are unsatisfiable without a DROP first.
    # Never drop the object the failed action needs to hold (failed_obj).
    #
    # A held object with its own placement goal (e.g. an item mid-carry to a
    # washing machine) is offered ONLY the WALK+PUTBACK/PUTIN chain to that
    # destination instead of DROP — a bare DROP abandons it with no record
    # that it still needs delivery, which cascades into orphaned items when
    # a multi-item carry runs out of hands (954_2: 5 items grabbed before any
    # placement, each hands-full repair used to just drop the oldest one).
    # If the destination isn't actually reachable right now (closed
    # container, etc.) this candidate won't satisfy its preconditions and
    # the tree search below returns empty; the caller then retries with
    # prefer_goal_placement=False to fall back to plain DROP, so this can
    # only ever help relative to the old behavior, never hurt it.
    _needs_open_chain = any(a == "OPEN" for (a, _, _) in guaranteed_candidates)
    goal_placed_objs = []
    if ("not_both_hands_full" in needs_set
            or (_needs_open_chain and initial_model.hands_full())):
        for held_obj in filter(None, [initial_model.hand_right, initial_model.hand_left]):
            if failed_obj and str(held_obj) == str(failed_obj):
                continue
            place_action, place_target = (
                _goal_placement_for(held_obj, goal_edge_relations, initial_model)
                if prefer_goal_placement else (None, None)
            )
            if place_action:
                guaranteed_candidates.append(("WALK", place_target, None))
                guaranteed_candidates.append((place_action, str(held_obj), place_target))
                # not_both_hands_full alone can already read True from a
                # rewound initial_state_dict that predates the OTHER hand
                # filling up (t_start is pinned just before the diagnosed
                # root cause, per hist_pos_to_plan_pos) — the search would
                # then treat the bare WALK as already "achieving" the goal
                # and never take the PUTBACK/PUTIN. Pin an explicit
                # per-object requirement so this chain can't short-circuit.
                goal_placed_objs.append(str(held_obj))
            else:
                guaranteed_candidates.append(("DROP", str(held_obj), None))

    # The failed action needs its object in hand: guarantee WALK + GRAB.
    if "holds_obj" in needs_set and failed_obj and failed_obj != "character":
        if (initial_model.satisfies("grabbable", failed_obj)
                and not initial_model.satisfies("holds_obj", failed_obj)):
            guaranteed_candidates.append(("WALK", failed_obj, None))
            guaranteed_candidates.append(("GRAB", failed_obj, None))

    # The failed action (WATCH/LOOKAT) needs the character facing its object.
    if "facing_obj" in needs_set and failed_obj and failed_obj != "character":
        guaranteed_candidates.append(("TURNTO", failed_obj, None))

    candidates = generate_candidate_nodes(
        llm_suggestions=llm_suggestions,
        original_subsequence=original_subsequence,
        error_objects=normalized_error_objects,
        char_sitting=char_sitting,
        char_lying=char_lying,
    )

    # ── Expand plain-name candidates to concrete instances ───────────────────
    # LLM suggestions often carry bare class names ({"GRAB": ["plate"]}).
    # With the ID-keyed model we expand them to per-instance candidates
    # ("plate_57", "plate_58") so the BFS simulates the right instance and
    # the output resolves exactly at the boundary instead of being rejected
    # as ambiguous. Capped per class to bound the branching factor.
    MAX_INSTANCES_PER_CLASS = 4

    def _instance_tokens(obj):
        if obj is None:
            return [None]
        toks = initial_model.resolve(obj)
        return toks[:MAX_INSTANCES_PER_CLASS] if toks else [str(obj)]

    seen_keys = set()
    all_candidates = []
    for (action, obj, target) in guaranteed_candidates + candidates:
        for obj_tok in _instance_tokens(obj):
            for tgt_tok in _instance_tokens(target):
                c = (action, obj_tok, tgt_tok)
                if c not in seen_keys:
                    seen_keys.add(c)
                    all_candidates.append(c)

    # Candidates that were part of an earlier repair and then failed in the
    # real environment are excluded outright — stronger than path-level
    # banning, since a bad action can appear on many paths.
    if banned_candidates:
        all_candidates = [c for c in all_candidates if c not in banned_candidates]

    # NOTE: no `break` in this loop — EVERY unsatisfied need must become a
    # BFS goal. Breaking after the first need meant e.g. that for
    # ['holds_obj', 'target_open_or_not_openable'] the open-target goal was
    # silently dropped and the found repair could never fix the real error.
    # Property filters (grabbable / can_open / has_switch) keep goals
    # satisfiable when error_objects mixes objects of different roles.
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(("check", need, None))

        elif need == "holds_obj":
            # Only the failed action's own object must end up held —
            # never its target (a PUTIN's washing machine is in
            # error_objects too, and can't be grabbed).
            holds_objs = [failed_obj] if failed_obj else list(normalized_error_objects)
            for obj in holds_objs:
                if obj != "character" and initial_model.satisfies("grabbable", obj):
                    target_effects.append(("check", "holds_obj", obj))

        elif need == "open":
            for obj in normalized_error_objects:
                if initial_model.satisfies("can_open", obj):
                    target_effects.append(("check", "open", obj))

        elif need in ("not_on", "off"):
            for obj in normalized_error_objects:
                if initial_model.satisfies("has_switch", obj):
                    target_effects.append(("check", "off", obj))

        elif need == "next_to_obj":
            # Pin to the failed action's own object. With None the check ran
            # against each node's obj, so a path ending anywhere (e.g. the
            # GRAB it just did) "achieved" the goal and repairs for compound
            # failures like [holds_obj, next_to_target] stopped one WALK
            # short — costing a full extra replan per failure.
            target_effects.append(("check", "next_to_obj", failed_obj or None))

        elif need == "next_to_target":
            # The failed action's TARGET is where the character must end up
            # (PUTBACK/PUTIN place-site). The state model clears CLOSE on
            # every WALK, so BFS is forced to order this WALK last.
            target_effects.append(("check", "next_to_obj", failed_target or None))

        elif need == "obj_not_inside_closed_container":
            # error_objects contains BOTH the blocked object and its container
            # (FIX 3). Only the object that is actually inside a container
            # must end up held — requiring holds_obj on the container itself
            # (e.g. a cabinet) makes the BFS goal unsatisfiable.
            for obj in normalized_error_objects:
                container = container_targets.get(obj) or initial_model.get_container(obj)
                if container:
                    target_effects.append(("check", "open", str(container)))
                    if obj != "character":
                        target_effects.append(("check", "holds_obj", obj))

        elif need == "target_open_or_not_openable":
            # The TARGET of the failed action must be open. Two cases:
            # the target itself is the openable container (washing machine,
            # fridge), or — legacy — an error object sits inside one.
            if failed_target and initial_model.satisfies("can_open", failed_target):
                target_effects.append(("check", "open", failed_target))
            else:
                for obj in normalized_error_objects:
                    if initial_model.satisfies("can_open", obj):
                        target_effects.append(("check", "open", obj))
                    container = container_targets.get(obj) or initial_model.get_container(obj)
                    if container:
                        target_effects.append(("check", "open", str(container)))

        elif need == "not_both_hands_full":
            # Global hands check. Pinning specific_obj to "character" matters:
            # with None the check runs against each node's own obj, and any
            # node whose obj isn't held (WALK cupboard) trivially "achieved"
            # the goal without freeing a hand.
            target_effects.append(("check", "not_both_hands_full", "character"))
            # initial_state_dict is often rewound to just before the OTHER
            # hand filled up, so this check alone can already read True —
            # letting the search stop after a bare WALK and never reach the
            # PUTBACK/PUTIN. Pin an explicit per-object requirement for each
            # object being goal-placed instead of dropped, so the chain
            # can't short-circuit (see goal_placed_objs above).
            for obj in goal_placed_objs:
                target_effects.append(("check", "not_holds_obj", obj))

        elif need == "holding_anything":
            target_effects.append(("check", "holding_anything", "character"))

        elif need == "facing_obj":
            target_effects.append(("check", "facing_obj", failed_obj or None))

        elif need == "plugged_in":
            for obj in normalized_error_objects:
                if initial_model.satisfies("has_plug_or_has_switch", obj):
                    target_effects.append(("check", "plugged_in", obj))

    # A holds_obj goal consumes the freed hand: demanding not_both_hands_full
    # in the FINAL state alongside it contradicts the grab whenever the other
    # hand stays occupied (forcing a needless second DROP past the search
    # budget). Hands-free AT GRAB TIME is already enforced inside the path by
    # GRAB's own SDG precondition, so keep the hands goal only when nothing
    # in the repair re-fills the hand (e.g. a failed OPEN).
    if any(p == "holds_obj" for (_, p, _) in target_effects):
        target_effects = [te for te in target_effects
                          if te[1] != "not_both_hands_full"]

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
        banned_paths=banned_paths,
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
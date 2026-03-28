# # """
# # action_subtree.py
# # =================
# # Adaptive Action SubTree Generation
# # Based on SDA-Planner paper Section 4.4

# # Algorithm:
# # 1. Generate candidate nodes from two sources:
# #    - LLM corrective suggestions
# #    - Actions from original failing subsequence
# # 2. Apply constrained subsequence rule (paper Section 4.4)
# # 3. Build BFS search tree with 3 SDG constraints (Eq. 5, 6):
# #    - satisfied(Aj, G): preconditions met for the specific obj/target
# #    - change(Aj, G): action has at least one effect
# #    - notCovered(At, Aj): child doesn't override parent's effects
# # 4. Extract first valid path achieving target effects
# # """

# # from collections import deque

# # from object_state_model import ObjectStateModel
# # from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# # # =============================================================================
# # # State wrapper for BFS — thin layer over ObjectStateModel
# # # =============================================================================

# # class TreeState:
# #     """
# #     Wraps ObjectStateModel for use in the BFS search tree.
# #     All precondition checks are per-object, not global.
# #     """

# #     def __init__(self, model: ObjectStateModel):
# #         self.model = model

# #     def copy(self) -> "TreeState":
# #         return TreeState(self.model.copy())

# #     def apply(self, action: str, obj: str, target: str = None):
# #         """Apply action effects to the underlying model."""
# #         self.model.apply(action, obj, target)

# #     def satisfies(self, preconditions: list, obj: str,
# #                   target: str = None) -> bool:
# #         """True if ALL preconditions hold for this obj/target."""
# #         return len(self.model.check_all(preconditions, obj, target)) == 0

# #     def achieves(self, target_effects: list, obj: str,
# #                  target: str = None) -> bool:
# #         """
# #         True if all target_effects are now satisfied for obj/target.
# #         target_effects are expressed as precondition strings so they
# #         can be checked the same way.
# #         """
# #         return self.satisfies(target_effects, obj, target)


# # # =============================================================================
# # # Search Tree Node
# # # =============================================================================

# # class TreeNode:
# #     def __init__(self, action: str, obj: str, target: str = None,
# #                  parent=None, state: TreeState = None, depth: int = 0):
# #         self.action = action.upper()
# #         self.obj    = obj
# #         self.target = target
# #         self.parent = parent
# #         self.state  = state
# #         self.depth  = depth

# #     def __repr__(self):
# #         if self.target:
# #             return f"{self.action}({self.obj}, {self.target})"
# #         return f"{self.action}({self.obj})"


# # # =============================================================================
# # # SDG Constraint Functions (Paper Equations 5 and 6)
# # # =============================================================================

# # def satisfied(action: str, state: TreeState,
# #               obj: str, target: str = None) -> bool:
# #     """Eq. 5: satisfied(Aj, G) — all preconditions of action met for obj/target."""
# #     return state.satisfies(get_preconditions(action.upper()), obj, target)


# # def changes_state(action: str) -> bool:
# #     """Eq. 5: change(Aj, G) — action must have at least one effect."""
# #     return len(get_effects(action.upper())) > 0


# # def not_covered(parent_action: str, child_action: str) -> bool:
# #     """
# #     Eq. 6: notCovered(At, Aj)
# #     True if there exists a state s where parent affects s but child does not.
# #     Prevents child from completely overriding parent's work.
# #     """
# #     if parent_action is None or parent_action == "ROOT":
# #         return True

# #     parent_effects = set(get_effects(parent_action.upper()))
# #     child_effects  = set(get_effects(child_action.upper()))

# #     if not parent_effects:
# #         return True

# #     # True if parent has at least one effect the child does NOT share
# #     return any(pe not in child_effects for pe in parent_effects)


# # # =============================================================================
# # # Candidate Node Generation (Paper Section 4.4)
# # # =============================================================================

# # def generate_candidate_nodes(
# #     llm_suggestions:      list,
# #     original_subsequence: list,
# #     error_objects:        set,
# #     char_sitting:         bool = False,
# #     char_lying:           bool = False,
# # ) -> list:
# #     """
# #     Generate candidate action nodes from two sources:
# #     1. LLM corrective suggestions (primary — fixes the error)
# #     2. Original failing subsequence (secondary — ensures coverage)

# #     Constrained subsequence rule (paper Section 4.4):
# #     If consecutive actions in original plan share the same object AND
# #     that object is NOT in error_objects, only the first is selectable;
# #     subsequent ones are forced (non-selectable candidates).
# #     """
# #     candidates = []
# #     seen       = set()

# #     def add(action, obj, target=None):
# #         key = (action.upper(), obj, target)
# #         if key not in seen:
# #             seen.add(key)
# #             candidates.append(key)

# #     def parse_item(item):
# #         if isinstance(item, dict):
# #             for action, args in item.items():
# #                 if not isinstance(args, list):
# #                     return action, "character", None
# #                 if len(args) == 0:
# #                     return action, "character", None
# #                 if len(args) == 1:
# #                     return action, args[0], None
# #                 return action, args[0], args[1]
# #         import re
# #         s  = str(item)
# #         am = re.search(r'\[(\w+)\]', s)
# #         om = re.findall(r'<([^>]+)>', s)
# #         if am:
# #             return am.group(1), om[0] if om else "character", om[1] if len(om) > 1 else None
# #         return None, None, None

# #     # Source 1: LLM suggestions
# #     for item in llm_suggestions:
# #         a, o, t = parse_item(item)
# #         if a:
# #             add(a, o, t)

# #     # Source 2: Original subsequence with constrained subsequence rule
# #     parsed_orig = []
# #     for item in original_subsequence:
# #         a, o, t = parse_item(item)
# #         if a:
# #             parsed_orig.append((a, o, t))

# #     for i, (a, o, t) in enumerate(parsed_orig):
# #         if i > 0:
# #             prev_a, prev_o, prev_t = parsed_orig[i - 1]
# #             # Same object as previous AND not an error object → skip
# #             if prev_o == o and o not in error_objects:
# #                 continue
# #         add(a, o, t)

# #     # Include STANDUP only if character is actually sitting or lying
# #     if char_sitting or char_lying:
# #         add("STANDUP", "character", None)

# #     return candidates


# # # =============================================================================
# # # BFS Search Tree
# # # =============================================================================

# # def build_and_search_tree(
# #     candidates:    list,
# #     initial_model: ObjectStateModel,
# #     target_effects: list,
# #     max_depth:     int = 6,
# #     max_nodes:     int = 500,
# # ) -> list:
# #     """
# #     BFS to find shortest valid replacement subsequence.
# #     Paper: "performs breadth-first search to extract a fully executable subsequence"

# #     Each candidate carries its (action, obj, target) so all SDG checks
# #     are done against the actual object involved — not a global flat state.

# #     Returns list of (action, obj, target) tuples or [] if not found.
# #     """
# #     initial_state = TreeState(initial_model.copy())

# #     root = TreeNode(
# #         action = "ROOT",
# #         obj    = "",
# #         state  = initial_state,
# #         depth  = 0,
# #     )

# #     # Early exit: if no target effects, return first valid candidate
# #     if not target_effects:
# #         for (action, obj, target) in candidates:
# #             if satisfied(action, root.state, obj, target) and changes_state(action):
# #                 return [(action, obj, target)]
# #         return []

# #     queue          = deque([root])
# #     nodes_expanded = 0

# #     while queue and nodes_expanded < max_nodes:
# #         current        = queue.popleft()
# #         nodes_expanded += 1

# #         # Check if target achieved for the node's specific obj/target
# #         if current.depth > 0:
# #             if current.state.achieves(target_effects, current.obj, current.target):
# #                 return _extract_path(current)

# #         if current.depth >= max_depth:
# #             continue

# #         # Expand children using the 3 SDG constraints (Eq. 5, 6)
# #         for (action, obj, target) in candidates:

# #             # Constraint 1: satisfied(Aj, G) — checked for this specific obj/target
# #             if not satisfied(action, current.state, obj, target):
# #                 continue

# #             # Constraint 2: change(Aj, G)
# #             # Allow zero-effect actions only as a terminal leaf that achieves target
# #             simulated = current.state.copy()
# #             simulated.apply(action, obj, target)
# #             is_terminal = simulated.achieves(target_effects, obj, target)
# #             if not changes_state(action) and not is_terminal:
# #                 continue

# #             # Constraint 3: notCovered(At, Aj)
# #             if not not_covered(current.action, action):
# #                 continue

# #             new_state = current.state.copy()
# #             new_state.apply(action, obj, target)

# #             child = TreeNode(
# #                 action = action,
# #                 obj    = obj,
# #                 target = target,
# #                 parent = current,
# #                 state  = new_state,
# #                 depth  = current.depth + 1,
# #             )
# #             queue.append(child)

# #     return []


# # def _extract_path(node: TreeNode) -> list:
# #     """Trace back from leaf to root, return path as list of (action, obj, target)."""
# #     path    = []
# #     current = node
# #     while current.parent is not None:
# #         path.append((current.action, current.obj, current.target))
# #         current = current.parent
# #     path.reverse()
# #     return path


# # # =============================================================================
# # # Initial State Builder
# # # =============================================================================

# # def _build_initial_state(
# #     env_dict:     dict,
# #     char_sitting: bool,
# #     char_lying:   bool,
# # ) -> ObjectStateModel:
# #     """
# #     Build per-object ObjectStateModel from the EAI environment dictionary.
# #     All objects in the scene are loaded — no filtering by error_objects,
# #     because the BFS needs the full scene to reason about container access,
# #     switch states, etc. for any object it might encounter.
# #     """
# #     return ObjectStateModel.from_env_dict(
# #         env_dict or {},
# #         char_sitting = char_sitting,
# #         char_lying   = char_lying,
# #     )


# # # =============================================================================
# # # Main Entry Point
# # # =============================================================================

# # def generate_replacement_subsequence(
# #     llm_suggestions:      list,
# #     original_subsequence: list,
# #     initial_state_dict:   dict,
# #     unsatisfied_needs:    list,
# #     error_objects:        set,
# #     char_sitting:         bool = False,
# #     char_lying:           bool = False,
# #     max_depth:            int  = 6,
# #     max_nodes:            int  = 500,
# # ) -> list:
# #     """
# #     Generate replacement subsequence using BFS search tree.
# #     Returns list of EAI-format action dicts, or [] if tree fails.
# #     """
# #     initial_model = _build_initial_state(
# #         initial_state_dict, char_sitting, char_lying
# #     )

# #     candidates = generate_candidate_nodes(
# #         llm_suggestions      = llm_suggestions,
# #         original_subsequence = original_subsequence,
# #         error_objects        = error_objects,
# #         char_sitting         = char_sitting,
# #         char_lying           = char_lying,
# #     )

# #     # Map unsatisfied preconditions to target effects the tree must achieve.
# #     # These are expressed as precondition strings so TreeState.achieves()
# #     # can check them via the same ObjectStateModel.satisfies() path.
# #     target_effects = []
# #     for need in unsatisfied_needs:
# #         if need in ("not_sitting", "not_lying"):
# #             target_effects.append(need)
# #         elif need == "holds_obj":
# #             target_effects.append("holds_obj")
# #         elif need == "open":
# #             target_effects.append("open")
# #         elif need in ("not_on", "off"):
# #             target_effects.append("off")
# #         elif need in ("next_to_obj", "next_to_target"):
# #             target_effects.append("next_to_obj")
# #         elif need == "obj_not_inside_closed_container":
# #             target_effects.append("open")
# #         elif need == "target_open_or_not_openable":
# #             target_effects.append("open")
# #         elif need == "not_both_hands_full":
# #             target_effects.append("not_holds_obj")
# #         elif need == "facing_obj":
# #             target_effects.append("facing_obj")
# #         elif need == "plugged_in":
# #             target_effects.append("plugged_in")

# #     # Deduplicate while preserving order
# #     target_effects = list(dict.fromkeys(target_effects))

# #     path = build_and_search_tree(
# #         candidates     = candidates,
# #         initial_model  = initial_model,
# #         target_effects = target_effects,
# #         max_depth      = max_depth,
# #         max_nodes      = max_nodes,
# #     )

# #     if not path:
# #         return []

# #     # Convert to EAI action dict format
# #     result = []
# #     ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
# #     for (action, obj, target) in path:
# #         if action.upper() in ZERO_ARG:
# #             result.append({action: []})
# #         elif target:
# #             result.append({action: [obj, target]})
# #         else:
# #             result.append({action: [obj]})

# #     return result


# # if __name__ == "__main__":
# #     # ── Test 1: sitting character needs STANDUP ───────────────────────────────
# #     print("Test 1 — Sitting character:")
# #     r = generate_replacement_subsequence(
# #         llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
# #         original_subsequence = [{"WALK": ["bathroom"]}],
# #         initial_state_dict   = {
# #             "nodes": [
# #                 {"id": 1, "class_name": "character",
# #                  "states": ["SITTING"], "properties": []},
# #                 {"id": 2, "class_name": "bathroom",
# #                  "states": [],         "properties": []},
# #             ],
# #             "edges": [],
# #         },
# #         unsatisfied_needs = ["not_sitting"],
# #         error_objects     = {"bathroom"},
# #         char_sitting      = True,
# #     )
# #     print("Result:", r)
# #     assert r == [{"STANDUP": []}], f"Expected [STANDUP], got {r}"
# #     print("✅ Passed\n")

# #     # ── Test 2: WALK then GRAB ────────────────────────────────────────────────
# #     print("Test 2 — Needs WALK + GRAB:")
# #     r = generate_replacement_subsequence(
# #         llm_suggestions      = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
# #         original_subsequence = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
# #         initial_state_dict   = {
# #             "nodes": [
# #                 {"id": 1, "class_name": "character",
# #                  "states": [], "properties": []},
# #                 {"id": 2, "class_name": "clothes",
# #                  "states": [], "properties": ["GRABBABLE", "CLOTHES"]},
# #             ],
# #             "edges": [],
# #         },
# #         unsatisfied_needs = ["holds_obj"],
# #         error_objects     = {"clothes"},
# #     )
# #     print("Result:", r)
# #     assert r == [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}], f"Got {r}"
# #     print("✅ Passed\n")

# #     # ── Test 3: washing machine ON → needs WALK + SWITCHOFF ──────────────────
# #     print("Test 3 — Washing machine ON, needs SWITCHOFF:")
# #     env = {
# #         "nodes": [
# #             {"id": 1, "class_name": "character",
# #              "states": [],          "properties": []},
# #             {"id": 2, "class_name": "washing_machine",
# #              "states": ["ON"],
# #              "properties": ["HAS_SWITCH", "CAN_OPEN"]},
# #         ],
# #         "edges": [
# #             {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
# #         ],
# #     }
# #     r = generate_replacement_subsequence(
# #         llm_suggestions = [
# #             {"WALK":      ["washing_machine"]},
# #             {"SWITCHOFF": ["washing_machine"]},
# #             {"OPEN":      ["washing_machine"]},
# #         ],
# #         original_subsequence = [{"OPEN": ["washing_machine"]}],
# #         initial_state_dict   = env,
# #         unsatisfied_needs    = ["not_on"],
# #         error_objects        = {"washing_machine"},
# #     )
# #     print("Result:", r)
# #     # Character is already next_to washing_machine (CLOSE edge in env),
# #     # so only SWITCHOFF should be needed.
# #     assert any(
# #         "SWITCHOFF" in list(a.keys())[0].upper() for a in r
# #     ), f"Expected SWITCHOFF in path, got {r}"
# #     print("✅ Passed\n")

# #     # ── Test 4: apple inside CLOSED fridge ────────────────────────────────────
# #     print("Test 4 — Apple inside closed fridge:")
# #     env2 = {
# #         "nodes": [
# #             {"id": 1, "class_name": "character",
# #              "states": [],         "properties": []},
# #             {"id": 2, "class_name": "fridge",
# #              "states": ["CLOSED"],
# #              "properties": ["CAN_OPEN"]},
# #             {"id": 3, "class_name": "apple",
# #              "states": [],
# #              "properties": ["GRABBABLE", "EATABLE"]},
# #         ],
# #         "edges": [
# #             {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
# #             {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
# #         ],
# #     }
# #     r = generate_replacement_subsequence(
# #         llm_suggestions      = [
# #             {"WALK": ["fridge"]},
# #             {"OPEN": ["fridge"]},
# #             {"GRAB": ["apple"]},
# #         ],
# #         original_subsequence = [{"GRAB": ["apple"]}],
# #         initial_state_dict   = env2,
# #         unsatisfied_needs    = ["obj_not_inside_closed_container"],
# #         error_objects        = {"apple"},
# #     )
# #     print("Result:", r)
# #     actions_in_path = [list(a.keys())[0].upper() for a in r]
# #     assert "OPEN" in actions_in_path, f"Expected OPEN in path, got {r}"
# #     print("✅ Passed\n")

# #     print("All tests passed! ✅")
# """
# action_subtree.py
# =================
# Adaptive Action SubTree Generation
# Based on SDA-Planner paper Section 4.4

# Algorithm:
# 1. Generate candidate nodes from two sources:
#    - LLM corrective suggestions
#    - Actions from original failing subsequence
# 2. Apply constrained subsequence rule (paper Section 4.4)
# 3. Build BFS search tree with 3 SDG constraints (Eq. 5, 6):
#    - satisfied(Aj, G): preconditions met for the specific obj/target
#    - change(Aj, G): action has at least one effect
#    - notCovered(At, Aj): child doesn't override parent's effects
# 4. Extract first valid path achieving target effects
# """

# from collections import deque

# from object_state_model import ObjectStateModel
# from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# # =============================================================================
# # State wrapper for BFS — thin layer over ObjectStateModel
# # =============================================================================

# class TreeState:
#     """
#     Wraps ObjectStateModel for use in the BFS search tree.
#     All precondition checks are per-object, not global.
#     """

#     def __init__(self, model: ObjectStateModel):
#         self.model = model

#     def copy(self) -> "TreeState":
#         return TreeState(self.model.copy())

#     def apply(self, action: str, obj: str, target: str = None):
#         """Apply action effects to the underlying model."""
#         self.model.apply(action, obj, target)

#     def satisfies(self, preconditions: list, obj: str,
#                   target: str = None) -> bool:
#         """True if ALL preconditions hold for this obj/target."""
#         return len(self.model.check_all(preconditions, obj, target)) == 0

#     def achieves(self, target_effects: list, obj: str,
#                  target: str = None) -> bool:
#         """
#         True if all target_effects are now satisfied for obj/target.
#         target_effects are expressed as precondition strings so they
#         can be checked the same way.
#         """
#         return self.satisfies(target_effects, obj, target)


# # =============================================================================
# # Search Tree Node
# # =============================================================================

# class TreeNode:
#     def __init__(self, action: str, obj: str, target: str = None,
#                  parent=None, state: TreeState = None, depth: int = 0):
#         self.action = action.upper()
#         self.obj    = obj
#         self.target = target
#         self.parent = parent
#         self.state  = state
#         self.depth  = depth

#     def __repr__(self):
#         if self.target:
#             return f"{self.action}({self.obj}, {self.target})"
#         return f"{self.action}({self.obj})"


# # =============================================================================
# # SDG Constraint Functions (Paper Equations 5 and 6)
# # =============================================================================

# def satisfied(action: str, state: TreeState,
#               obj: str, target: str = None) -> bool:
#     """Eq. 5: satisfied(Aj, G) — all preconditions of action met for obj/target."""
#     return state.satisfies(get_preconditions(action.upper()), obj, target)


# def changes_state(action: str) -> bool:
#     """Eq. 5: change(Aj, G) — action must have at least one effect."""
#     return len(get_effects(action.upper())) > 0


# def not_covered(parent_action: str, child_action: str) -> bool:
#     """
#     Eq. 6: notCovered(At, Aj)
#     True if there exists a state s where parent affects s but child does not.
#     Prevents child from completely overriding parent's work.
#     """
#     if parent_action is None or parent_action == "ROOT":
#         return True

#     parent_effects = set(get_effects(parent_action.upper()))
#     child_effects  = set(get_effects(child_action.upper()))

#     if not parent_effects:
#         return True

#     # True if parent has at least one effect the child does NOT share
#     return any(pe not in child_effects for pe in parent_effects)


# # =============================================================================
# # Candidate Node Generation (Paper Section 4.4)
# # =============================================================================

# def generate_candidate_nodes(
#     llm_suggestions:      list,
#     original_subsequence: list,
#     error_objects:        set,
#     char_sitting:         bool = False,
#     char_lying:           bool = False,
# ) -> list:
#     """
#     Generate candidate action nodes from two sources:
#     1. LLM corrective suggestions (primary — fixes the error)
#     2. Original failing subsequence (secondary — ensures coverage)

#     Constrained subsequence rule (paper Section 4.4):
#     If consecutive actions in original plan share the same object AND
#     that object is NOT in error_objects, only the first is selectable;
#     subsequent ones are forced (non-selectable candidates).
#     """
#     candidates = []
#     seen       = set()

#     def add(action, obj, target=None):
#         key = (action.upper(), obj, target)
#         if key not in seen:
#             seen.add(key)
#             candidates.append(key)

#     def parse_item(item):
#         if isinstance(item, dict):
#             for action, args in item.items():
#                 if not isinstance(args, list):
#                     return action, "character", None
#                 if len(args) == 0:
#                     return action, "character", None
#                 if len(args) == 1:
#                     return action, args[0], None
#                 return action, args[0], args[1]
#         import re
#         s  = str(item)
#         am = re.search(r'\[(\w+)\]', s)
#         om = re.findall(r'<([^>]+)>', s)
#         if am:
#             return am.group(1), om[0] if om else "character", om[1] if len(om) > 1 else None
#         return None, None, None

#     # Source 1: LLM suggestions
#     for item in llm_suggestions:
#         a, o, t = parse_item(item)
#         if a:
#             add(a, o, t)

#     # Source 2: Original subsequence with constrained subsequence rule
#     parsed_orig = []
#     for item in original_subsequence:
#         a, o, t = parse_item(item)
#         if a:
#             parsed_orig.append((a, o, t))

#     for i, (a, o, t) in enumerate(parsed_orig):
#         if i > 0:
#             prev_a, prev_o, prev_t = parsed_orig[i - 1]
#             # Same object as previous AND not an error object → skip
#             if prev_o == o and o not in error_objects:
#                 continue
#         add(a, o, t)

#     # Include STANDUP only if character is actually sitting or lying
#     if char_sitting or char_lying:
#         add("STANDUP", "character", None)

#     return candidates


# # =============================================================================
# # BFS Search Tree
# # =============================================================================

# def build_and_search_tree(
#     candidates:     list,
#     initial_model:  ObjectStateModel,
#     target_effects: list,
#     error_objects:  set  = None,
#     max_depth:      int  = 6,
#     max_nodes:      int  = 500,
# ) -> list:
#     """
#     BFS to find shortest valid replacement subsequence.

#     target_effects is a list of tuples: ("check", precondition, specific_obj)
#       - specific_obj=None means check against the candidate's own obj
#       - specific_obj=<name> means always check against that specific object

#     Returns list of (action, obj, target) tuples or [] if not found.
#     """
#     initial_state = TreeState(initial_model.copy())
#     error_objects = error_objects or set()

#     root = TreeNode(
#         action = "ROOT",
#         obj    = "",
#         state  = initial_state,
#         depth  = 0,
#     )

#     def _achieves(state: TreeState, node: TreeNode) -> bool:
#         """Check all target effects — each against its specific object."""
#         if not target_effects:
#             return False
#         for (_, precondition, specific_obj) in target_effects:
#             check_obj = specific_obj if specific_obj else node.obj
#             check_tgt = node.target if not specific_obj else None
#             if not state.model.satisfies(precondition, check_obj, check_tgt):
#                 return False
#         return True

#     # Early exit: no target effects → return first valid candidate
#     if not target_effects:
#         for (action, obj, target) in candidates:
#             if satisfied(action, root.state, obj, target) and changes_state(action):
#                 return [(action, obj, target)]
#         return []

#     queue          = deque([root])
#     nodes_expanded = 0

#     while queue and nodes_expanded < max_nodes:
#         current        = queue.popleft()
#         nodes_expanded += 1

#         if current.depth > 0 and _achieves(current.state, current):
#             return _extract_path(current)

#         if current.depth >= max_depth:
#             continue

#         for (action, obj, target) in candidates:

#             # Constraint 1: satisfied for THIS obj/target
#             if not satisfied(action, current.state, obj, target):
#                 continue

#             # Constraint 2: changes state or achieves target
#             simulated = current.state.copy()
#             simulated.apply(action, obj, target)

#             # Build a temp node to check achievement
#             temp_node = TreeNode(action=action, obj=obj, target=target,
#                                  parent=current, state=simulated,
#                                  depth=current.depth + 1)
#             is_terminal = _achieves(simulated, temp_node)

#             if not changes_state(action) and not is_terminal:
#                 continue

#             # Constraint 3: notCovered
#             if not not_covered(current.action, action):
#                 continue

#             new_state = current.state.copy()
#             new_state.apply(action, obj, target)

#             child = TreeNode(
#                 action = action,
#                 obj    = obj,
#                 target = target,
#                 parent = current,
#                 state  = new_state,
#                 depth  = current.depth + 1,
#             )
#             queue.append(child)

#     return []


# def _extract_path(node: TreeNode) -> list:
#     """Trace back from leaf to root, return path as list of (action, obj, target)."""
#     path    = []
#     current = node
#     while current.parent is not None:
#         path.append((current.action, current.obj, current.target))
#         current = current.parent
#     path.reverse()
#     return path


# # =============================================================================
# # Initial State Builder
# # =============================================================================

# def _build_initial_state(
#     env_dict:     dict,
#     char_sitting: bool,
#     char_lying:   bool,
# ) -> ObjectStateModel:
#     """
#     Build per-object ObjectStateModel from the EAI environment dictionary.
#     All objects in the scene are loaded — no filtering by error_objects,
#     because the BFS needs the full scene to reason about container access,
#     switch states, etc. for any object it might encounter.
#     """
#     return ObjectStateModel.from_env_dict(
#         env_dict or {},
#         char_sitting = char_sitting,
#         char_lying   = char_lying,
#     )


# # =============================================================================
# # Main Entry Point
# # =============================================================================

# def generate_replacement_subsequence(
#     llm_suggestions:      list,
#     original_subsequence: list,
#     initial_state_dict:   dict,
#     unsatisfied_needs:    list,
#     error_objects:        set,
#     char_sitting:         bool = False,
#     char_lying:           bool = False,
#     max_depth:            int  = 6,
#     max_nodes:            int  = 500,
# ) -> list:
#     """
#     Generate replacement subsequence using BFS search tree.
#     Returns list of EAI-format action dicts, or [] if tree fails.
#     """
#     initial_model = _build_initial_state(
#         initial_state_dict, char_sitting, char_lying
#     )

#     # ── Resolve actual containers from the scene graph ────────────────────────
#     # When obj_not_inside_closed_container or target_open_or_not_openable is
#     # unsatisfied, look up the REAL container from the env dict instead of
#     # relying on the LLM to guess it correctly.
#     # These become guaranteed candidates injected before LLM suggestions.
#     guaranteed_candidates = []
#     container_targets     = {}   # error_obj -> actual container name

#     needs_set = set(unsatisfied_needs)
#     if "obj_not_inside_closed_container" in needs_set or \
#        "target_open_or_not_openable"     in needs_set:
#         for obj in error_objects:
#             container = initial_model.get_container(obj)
#             if container and not initial_model.satisfies("open", container):
#                 container_targets[obj] = container
#                 # Inject WALK + OPEN for the actual container at the front
#                 guaranteed_candidates.append(("WALK", container, None))
#                 guaranteed_candidates.append(("OPEN", container, None))

#     candidates = generate_candidate_nodes(
#         llm_suggestions      = llm_suggestions,
#         original_subsequence = original_subsequence,
#         error_objects        = error_objects,
#         char_sitting         = char_sitting,
#         char_lying           = char_lying,
#     )

#     # Merge guaranteed candidates first (deduplicated), then LLM/original
#     seen_keys  = set()
#     all_candidates = []
#     for c in guaranteed_candidates + candidates:
#         if c not in seen_keys:
#             seen_keys.add(c)
#             all_candidates.append(c)

#     # ── Map unsatisfied preconditions to target effects ───────────────────────
#     # For container-access issues, use the ACTUAL container name so the
#     # achievement check fires on the right object, not the LLM's guess.
#     target_effects = []
#     for need in unsatisfied_needs:
#         if need in ("not_sitting", "not_lying"):
#             target_effects.append(("check", need, None))
#         elif need == "holds_obj":
#             # Achievement: character holds any of the error objects
#             for obj in error_objects:
#                 target_effects.append(("check", "holds_obj", obj))
#             break
#         elif need == "open":
#             for obj in error_objects:
#                 target_effects.append(("check", "open", obj))
#             break
#         elif need in ("not_on", "off"):
#             for obj in error_objects:
#                 target_effects.append(("check", "off", obj))
#             break
#         elif need in ("next_to_obj", "next_to_target"):
#             target_effects.append(("check", "next_to_obj", None))
#         elif need == "obj_not_inside_closed_container":
#             # Check that the container of the error object is open
#             for obj in error_objects:
#                 container = container_targets.get(obj) or initial_model.get_container(obj)
#                 if container:
#                     target_effects.append(("check", "open", container))
#                 else:
#                     # Object not inside anything — already accessible
#                     pass
#         elif need == "target_open_or_not_openable":
#             for obj in error_objects:
#                 container = container_targets.get(obj) or initial_model.get_container(obj)
#                 if container:
#                     target_effects.append(("check", "open", container))
#         elif need == "not_both_hands_full":
#             target_effects.append(("check", "not_holds_obj", None))
#         elif need == "facing_obj":
#             target_effects.append(("check", "facing_obj", None))
#         elif need == "plugged_in":
#             for obj in error_objects:
#                 target_effects.append(("check", "plugged_in", obj))
#             break

#     # Deduplicate while preserving order
#     seen_te     = set()
#     deduped_te  = []
#     for te in target_effects:
#         if te not in seen_te:
#             seen_te.add(te)
#             deduped_te.append(te)
#     target_effects = deduped_te

#     path = build_and_search_tree(
#         candidates     = all_candidates,
#         initial_model  = initial_model,
#         target_effects = target_effects,
#         error_objects  = error_objects,
#         max_depth      = max_depth,
#         max_nodes      = max_nodes,
#     )

#     if not path:
#         return []

#     # Convert to EAI action dict format
#     result = []
#     ZERO_ARG = {"STANDUP", "SLEEP", "WAKEUP"}
#     for (action, obj, target) in path:
#         if action.upper() in ZERO_ARG:
#             result.append({action: []})
#         elif target:
#             result.append({action: [obj, target]})
#         else:
#             result.append({action: [obj]})

#     return result


# if __name__ == "__main__":
#     # ── Test 1: sitting character needs STANDUP ───────────────────────────────
#     print("Test 1 — Sitting character:")
#     r = generate_replacement_subsequence(
#         llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
#         original_subsequence = [{"WALK": ["bathroom"]}],
#         initial_state_dict   = {
#             "nodes": [
#                 {"id": 1, "class_name": "character",
#                  "states": ["SITTING"], "properties": []},
#                 {"id": 2, "class_name": "bathroom",
#                  "states": [],         "properties": []},
#             ],
#             "edges": [],
#         },
#         unsatisfied_needs = ["not_sitting"],
#         error_objects     = {"bathroom"},
#         char_sitting      = True,
#     )
#     print("Result:", r)
#     assert r == [{"STANDUP": []}], f"Expected [STANDUP], got {r}"
#     print("✅ Passed\n")

#     # ── Test 2: WALK then GRAB ────────────────────────────────────────────────
#     print("Test 2 — Needs WALK + GRAB:")
#     r = generate_replacement_subsequence(
#         llm_suggestions      = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
#         original_subsequence = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
#         initial_state_dict   = {
#             "nodes": [
#                 {"id": 1, "class_name": "character",
#                  "states": [], "properties": []},
#                 {"id": 2, "class_name": "clothes",
#                  "states": [], "properties": ["GRABBABLE", "CLOTHES"]},
#             ],
#             "edges": [],
#         },
#         unsatisfied_needs = ["holds_obj"],
#         error_objects     = {"clothes"},
#     )
#     print("Result:", r)
#     assert r == [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}], f"Got {r}"
#     print("✅ Passed\n")

#     # ── Test 3: washing machine ON → needs WALK + SWITCHOFF ──────────────────
#     print("Test 3 — Washing machine ON, needs SWITCHOFF:")
#     env = {
#         "nodes": [
#             {"id": 1, "class_name": "character",
#              "states": [],          "properties": []},
#             {"id": 2, "class_name": "washing_machine",
#              "states": ["ON"],
#              "properties": ["HAS_SWITCH", "CAN_OPEN"]},
#         ],
#         "edges": [
#             {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
#         ],
#     }
#     r = generate_replacement_subsequence(
#         llm_suggestions = [
#             {"WALK":      ["washing_machine"]},
#             {"SWITCHOFF": ["washing_machine"]},
#             {"OPEN":      ["washing_machine"]},
#         ],
#         original_subsequence = [{"OPEN": ["washing_machine"]}],
#         initial_state_dict   = env,
#         unsatisfied_needs    = ["not_on"],
#         error_objects        = {"washing_machine"},
#     )
#     print("Result:", r)
#     # Character is already next_to washing_machine (CLOSE edge in env),
#     # so only SWITCHOFF should be needed.
#     assert any(
#         "SWITCHOFF" in list(a.keys())[0].upper() for a in r
#     ), f"Expected SWITCHOFF in path, got {r}"
#     print("✅ Passed\n")

#     # ── Test 4: apple inside CLOSED fridge ────────────────────────────────────
#     print("Test 4 — Apple inside closed fridge:")
#     env2 = {
#         "nodes": [
#             {"id": 1, "class_name": "character",
#              "states": [],         "properties": []},
#             {"id": 2, "class_name": "fridge",
#              "states": ["CLOSED"],
#              "properties": ["CAN_OPEN"]},
#             {"id": 3, "class_name": "apple",
#              "states": [],
#              "properties": ["GRABBABLE", "EATABLE"]},
#         ],
#         "edges": [
#             {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
#             {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
#         ],
#     }
#     r = generate_replacement_subsequence(
#         llm_suggestions      = [
#             {"WALK": ["fridge"]},
#             {"OPEN": ["fridge"]},
#             {"GRAB": ["apple"]},
#         ],
#         original_subsequence = [{"GRAB": ["apple"]}],
#         initial_state_dict   = env2,
#         unsatisfied_needs    = ["obj_not_inside_closed_container"],
#         error_objects        = {"apple"},
#     )
#     print("Result:", r)
#     actions_in_path = [list(a.keys())[0].upper() for a in r]
#     assert "OPEN" in actions_in_path, f"Expected OPEN in path, got {r}"
#     print("✅ Passed\n")

#     print("All tests passed! ✅")
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
   - satisfied(Aj, G): preconditions met for the specific obj/target
   - change(Aj, G): action has at least one effect
   - notCovered(At, Aj): child doesn't override parent's effects
4. Extract first valid path achieving target effects
"""

from collections import deque

from object_state_model import ObjectStateModel
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


# =============================================================================
# State wrapper for BFS — thin layer over ObjectStateModel
# =============================================================================

class TreeState:
    """
    Wraps ObjectStateModel for use in the BFS search tree.
    All precondition checks are per-object, not global.
    """

    def __init__(self, model: ObjectStateModel):
        self.model = model

    def copy(self) -> "TreeState":
        return TreeState(self.model.copy())

    def apply(self, action: str, obj: str, target: str = None):
        """Apply action effects to the underlying model."""
        self.model.apply(action, obj, target)

    def satisfies(self, preconditions: list, obj: str,
                  target: str = None) -> bool:
        """True if ALL preconditions hold for this obj/target."""
        return len(self.model.check_all(preconditions, obj, target)) == 0

    def achieves(self, target_effects: list, obj: str,
                 target: str = None) -> bool:
        """
        True if all target_effects are now satisfied for obj/target.
        target_effects are expressed as precondition strings so they
        can be checked the same way.
        """
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

def satisfied(action: str, state: TreeState,
              obj: str, target: str = None) -> bool:
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

    # True if parent has at least one effect the child does NOT share
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
            # Same object as previous AND not an error object → skip
            if prev_o == o and o not in error_objects:
                continue
        add(a, o, t)

    # Include STANDUP only if character is actually sitting or lying
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
    error_objects:  set  = None,
    max_depth:      int  = 6,
    max_nodes:      int  = 500,
) -> list:
    """
    BFS to find shortest valid replacement subsequence.

    target_effects is a list of tuples: ("check", precondition, specific_obj)
      - specific_obj=None means check against the candidate's own obj
      - specific_obj=<name> means always check against that specific object

    Returns list of (action, obj, target) tuples or [] if not found.
    """
    initial_state = TreeState(initial_model.copy())
    error_objects = error_objects or set()

    root = TreeNode(
        action = "ROOT",
        obj    = "",
        state  = initial_state,
        depth  = 0,
    )

    def _achieves(state: TreeState, node: TreeNode) -> bool:
        """Check all target effects — each against its specific object."""
        if not target_effects:
            return False
        for (_, precondition, specific_obj) in target_effects:
            check_obj = specific_obj if specific_obj else node.obj
            check_tgt = node.target if not specific_obj else None
            if not state.model.satisfies(precondition, check_obj, check_tgt):
                return False
        return True

    # Early exit: no target effects → return first valid candidate
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

            # Constraint 1: satisfied for THIS obj/target
            if not satisfied(action, current.state, obj, target):
                continue

            # Constraint 2: changes state or achieves target
            simulated = current.state.copy()
            simulated.apply(action, obj, target)

            # Build a temp node to check achievement
            temp_node = TreeNode(action=action, obj=obj, target=target,
                                 parent=current, state=simulated,
                                 depth=current.depth + 1)
            is_terminal = _achieves(simulated, temp_node)

            if not changes_state(action) and not is_terminal:
                continue

            # Constraint 3: notCovered
            if not not_covered(current.action, action):
                continue

            new_state = current.state.copy()
            new_state.apply(action, obj, target)

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


def _extract_path(node: TreeNode) -> list:
    """Trace back from leaf to root, return path as list of (action, obj, target)."""
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

def _build_initial_state(
    env_dict:     dict,
    char_sitting: bool,
    char_lying:   bool,
) -> ObjectStateModel:
    """
    Build per-object ObjectStateModel from the EAI environment dictionary.
    All objects in the scene are loaded — no filtering by error_objects,
    because the BFS needs the full scene to reason about container access,
    switch states, etc. for any object it might encounter.
    """
    return ObjectStateModel.from_env_dict(
        env_dict or {},
        char_sitting = char_sitting,
        char_lying   = char_lying,
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
    Returns list of EAI-format action dicts, or [] if tree fails.
    """
    initial_model = _build_initial_state(
        initial_state_dict, char_sitting, char_lying
    )

    # ── Resolve actual containers from the scene graph ────────────────────────
    # When obj_not_inside_closed_container or target_open_or_not_openable is
    # unsatisfied, look up the REAL container from the env dict instead of
    # relying on the LLM to guess it correctly.
    # These become guaranteed candidates injected before LLM suggestions.
    guaranteed_candidates = []
    container_targets     = {}   # error_obj -> actual container name

    needs_set = set(unsatisfied_needs)
    if "obj_not_inside_closed_container" in needs_set or \
       "target_open_or_not_openable"     in needs_set:
        for obj in error_objects:
            container = initial_model.get_container(obj)
            if container and not initial_model.satisfies("open", container):
                container_targets[obj] = container
                # Inject WALK + OPEN for the actual container at the front
                guaranteed_candidates.append(("WALK", container, None))
                guaranteed_candidates.append(("OPEN", container, None))
        # FIX 4a: also inject WALK + GRAB for the error objects themselves.
        # The original failed action (GRAB dish_soap) is inside the replaced
        # window [t_start, t_end], so it is NOT in `after`. Without these
        # candidates the tree stops at OPEN(container) and GRAB is lost.
        for obj in error_objects:
            if obj not in container_targets:
                continue   # only for objects that were inside containers
            guaranteed_candidates.append(("WALK", obj, None))
            guaranteed_candidates.append(("GRAB", obj, None))

    # FIX 4b: for not_both_hands_full, inject DROP for whichever objects the
    # character is actually holding. The LLM uses placeholder names like
    # "object_in_hand" which don't exist in the scene and always fail the
    # satisfied() check in BFS, so the tree never finds a DROP path.
    if "not_both_hands_full" in needs_set:
        for held_obj in filter(None, [initial_model.hand_right, initial_model.hand_left]):
            guaranteed_candidates.append(("DROP", held_obj, None))

    candidates = generate_candidate_nodes(
        llm_suggestions      = llm_suggestions,
        original_subsequence = original_subsequence,
        error_objects        = error_objects,
        char_sitting         = char_sitting,
        char_lying           = char_lying,
    )

    # Merge guaranteed candidates first (deduplicated), then LLM/original
    seen_keys  = set()
    all_candidates = []
    for c in guaranteed_candidates + candidates:
        if c not in seen_keys:
            seen_keys.add(c)
            all_candidates.append(c)

    # ── Map unsatisfied preconditions to target effects ───────────────────────
    # For container-access issues, use the ACTUAL container name so the
    # achievement check fires on the right object, not the LLM's guess.
    target_effects = []
    for need in unsatisfied_needs:
        if need in ("not_sitting", "not_lying"):
            target_effects.append(("check", need, None))
        elif need == "holds_obj":
            # Achievement: character holds any of the error objects
            for obj in error_objects:
                target_effects.append(("check", "holds_obj", obj))
            break
        elif need == "open":
            for obj in error_objects:
                target_effects.append(("check", "open", obj))
            break
        elif need in ("not_on", "off"):
            for obj in error_objects:
                target_effects.append(("check", "off", obj))
            break
        elif need in ("next_to_obj", "next_to_target"):
            target_effects.append(("check", "next_to_obj", None))
        elif need == "obj_not_inside_closed_container":
            # FIX 4c: require BOTH open(container) AND holds_obj(obj).
            # Old code only checked open(container), so the tree stopped at
            # OPEN and never produced WALK+GRAB — leaving the original GRAB
            # action lost from the spliced plan.
            for obj in error_objects:
                container = container_targets.get(obj) or initial_model.get_container(obj)
                if container:
                    target_effects.append(("check", "open", container))
                # Also require the character ends up holding the object
                if obj != "character":
                    target_effects.append(("check", "holds_obj", obj))
        elif need == "target_open_or_not_openable":
            for obj in error_objects:
                container = container_targets.get(obj) or initial_model.get_container(obj)
                if container:
                    target_effects.append(("check", "open", container))
        elif need == "not_both_hands_full":
            target_effects.append(("check", "not_holds_obj", None))
        elif need == "facing_obj":
            target_effects.append(("check", "facing_obj", None))
        elif need == "plugged_in":
            for obj in error_objects:
                target_effects.append(("check", "plugged_in", obj))
            break

    # Deduplicate while preserving order
    seen_te     = set()
    deduped_te  = []
    for te in target_effects:
        if te not in seen_te:
            seen_te.add(te)
            deduped_te.append(te)
    target_effects = deduped_te

    path = build_and_search_tree(
        candidates     = all_candidates,
        initial_model  = initial_model,
        target_effects = target_effects,
        error_objects  = error_objects,
        max_depth      = max_depth,
        max_nodes      = max_nodes,
    )

    if not path:
        return []

    # Convert to EAI action dict format
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
    # ── Test 1: sitting character needs STANDUP ───────────────────────────────
    print("Test 1 — Sitting character:")
    r = generate_replacement_subsequence(
        llm_suggestions      = [{"STANDUP": []}, {"WALK": ["bathroom"]}],
        original_subsequence = [{"WALK": ["bathroom"]}],
        initial_state_dict   = {
            "nodes": [
                {"id": 1, "class_name": "character",
                 "states": ["SITTING"], "properties": []},
                {"id": 2, "class_name": "bathroom",
                 "states": [],         "properties": []},
            ],
            "edges": [],
        },
        unsatisfied_needs = ["not_sitting"],
        error_objects     = {"bathroom"},
        char_sitting      = True,
    )
    print("Result:", r)
    assert r == [{"STANDUP": []}], f"Expected [STANDUP], got {r}"
    print("✅ Passed\n")

    # ── Test 2: WALK then GRAB ────────────────────────────────────────────────
    print("Test 2 — Needs WALK + GRAB:")
    r = generate_replacement_subsequence(
        llm_suggestions      = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        original_subsequence = [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}],
        initial_state_dict   = {
            "nodes": [
                {"id": 1, "class_name": "character",
                 "states": [], "properties": []},
                {"id": 2, "class_name": "clothes",
                 "states": [], "properties": ["GRABBABLE", "CLOTHES"]},
            ],
            "edges": [],
        },
        unsatisfied_needs = ["holds_obj"],
        error_objects     = {"clothes"},
    )
    print("Result:", r)
    assert r == [{"WALK": ["clothes"]}, {"GRAB": ["clothes"]}], f"Got {r}"
    print("✅ Passed\n")

    # ── Test 3: washing machine ON → needs WALK + SWITCHOFF ──────────────────
    print("Test 3 — Washing machine ON, needs SWITCHOFF:")
    env = {
        "nodes": [
            {"id": 1, "class_name": "character",
             "states": [],          "properties": []},
            {"id": 2, "class_name": "washing_machine",
             "states": ["ON"],
             "properties": ["HAS_SWITCH", "CAN_OPEN"]},
        ],
        "edges": [
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
        ],
    }
    r = generate_replacement_subsequence(
        llm_suggestions = [
            {"WALK":      ["washing_machine"]},
            {"SWITCHOFF": ["washing_machine"]},
            {"OPEN":      ["washing_machine"]},
        ],
        original_subsequence = [{"OPEN": ["washing_machine"]}],
        initial_state_dict   = env,
        unsatisfied_needs    = ["not_on"],
        error_objects        = {"washing_machine"},
    )
    print("Result:", r)
    # Character is already next_to washing_machine (CLOSE edge in env),
    # so only SWITCHOFF should be needed.
    assert any(
        "SWITCHOFF" in list(a.keys())[0].upper() for a in r
    ), f"Expected SWITCHOFF in path, got {r}"
    print("✅ Passed\n")

    # ── Test 4: apple inside CLOSED fridge ────────────────────────────────────
    print("Test 4 — Apple inside closed fridge:")
    env2 = {
        "nodes": [
            {"id": 1, "class_name": "character",
             "states": [],         "properties": []},
            {"id": 2, "class_name": "fridge",
             "states": ["CLOSED"],
             "properties": ["CAN_OPEN"]},
            {"id": 3, "class_name": "apple",
             "states": [],
             "properties": ["GRABBABLE", "EATABLE"]},
        ],
        "edges": [
            {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
        ],
    }
    r = generate_replacement_subsequence(
        llm_suggestions      = [
            {"WALK": ["fridge"]},
            {"OPEN": ["fridge"]},
            {"GRAB": ["apple"]},
        ],
        original_subsequence = [{"GRAB": ["apple"]}],
        initial_state_dict   = env2,
        unsatisfied_needs    = ["obj_not_inside_closed_container"],
        error_objects        = {"apple"},
    )
    print("Result:", r)
    actions_in_path = [list(a.keys())[0].upper() for a in r]
    assert "OPEN" in actions_in_path, f"Expected OPEN in path, got {r}"
    print("✅ Passed\n")

    print("All tests passed! ✅")
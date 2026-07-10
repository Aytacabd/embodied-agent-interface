# """
# object_state_model.py
# =====================
# Per-object state representation for SDA-Planner.
# Replaces the flat global state set that loses object identity.

# Each object gets its own state set. Relations are tracked as
# (from_obj, to_obj) -> set of relation types. Container membership
# is tracked explicitly for inside-closed-container checks.
# """


# class ObjectStateModel:
#     """
#     Tracks state per object, not globally.

#     object_states : { obj_name -> set of VH state/property strings }
#         e.g. {"fridge": {"CLOSED", "PLUGGED_IN"}, "apple": {"GRABBABLE"}}

#     relations     : { (from_name, to_name) -> set of relation types }
#         e.g. {("character", "apple"): {"HOLDS_RH"},
#                ("character", "fridge"): {"CLOSE"}}

#     container_of  : { obj_name -> container_name }
#         e.g. {"apple": "fridge"}  — apple is inside fridge
#     """

#     def __init__(self):
#         self.object_states: dict = {}   # obj_name -> set of state strings (merged across instances)
#         self.object_states_by_id: dict = {}  # obj_id -> set of state strings (per instance)
#         self.id_to_name: dict  = {}     # obj_id -> class_name
#         self.name_to_ids: dict = {}     # class_name -> list of ids
#         self.relations:   dict = {}     # (from_name, to_name) -> set of relation strings
#         self.container_of: dict = {}    # obj_name -> its direct container
#         self.hand_right:   str  = None
#         self.hand_left:    str  = None
#         self.char_sitting: bool = False
#         self.char_lying:   bool = False

#     # ──────────────────────────────────────────────────────────────────────────
#     # Loaders
#     # ──────────────────────────────────────────────────────────────────────────

#     @classmethod
#     def from_env_dict(
#         cls,
#         env_dict:     dict,
#         char_sitting: bool = False,
#         char_lying:   bool = False,
#     ) -> "ObjectStateModel":
#         """Build model from VirtualHome environment dict (nodes + edges)."""
#         m = cls()
#         m.char_sitting = char_sitting
#         m.char_lying   = char_lying

#         if not env_dict:
#             return m

#         id_to_name: dict = {}

#         # ── Pass 1: nodes → object_states_by_id + merged object_states ─────────
#         for node in env_dict.get("nodes", []):
#             name = node.get("class_name", "").lower().strip()
#             nid  = node.get("id")
#             if nid is not None:
#                 id_to_name[nid]       = name
#                 m.id_to_name[nid]     = name
#                 m.name_to_ids.setdefault(name, []).append(nid)

#             states = {s.upper() for s in node.get("states", [])}
#             props  = {p.upper() for p in node.get("properties", [])}

#             # ── Smart defaults based on object properties ─────────────────────
#             # VirtualHome convention: devices start OFF unless explicitly ON;
#             # containers start CLOSED unless explicitly OPEN;
#             # devices start PLUGGED_IN unless explicitly PLUGGED_OUT.
#             if "HAS_SWITCH" in props:
#                 if "ON" not in states:
#                     states.add("OFF")
#                 # EAI assumes all devices are plugged in
#                 if "PLUGGED_OUT" not in states:
#                     states.add("PLUGGED_IN")

#             if "HAS_PLUG" in props:
#                 if "PLUGGED_OUT" not in states:
#                     states.add("PLUGGED_IN")

#             if "CAN_OPEN" in props:
#                 if "OPEN" not in states:
#                     states.add("CLOSED")

#             combined = states | props

#             # Store per-ID (exact state of this specific instance)
#             if nid is not None:
#                 m.object_states_by_id[nid] = combined

#             # Store merged by name:
#             # - Properties (permanent): union across all instances
#             # - Dynamic states (ON/OFF, OPEN/CLOSED): keep ALL variants —
#             #   a name has a state if ANY instance has it. This is optimistic
#             #   but correct for planning: if one light is OFF we can SWITCHON it.
#             if name not in m.object_states:
#                 m.object_states[name] = set(combined)
#             else:
#                 m.object_states[name] |= combined

#             # Extract character posture from the scene graph when available
#             if name == "character":
#                 if "SITTING" in states:
#                     m.char_sitting = True
#                 if "LYING" in states:
#                     m.char_lying = True

#         # ── Pass 2: edges → relations + container_of ─────────────────────────
#         for edge in env_dict.get("edges", []):
#             from_id   = edge.get("from_id")
#             to_id     = edge.get("to_id")
#             rel       = edge.get("relation_type", "").upper()
#             from_name = id_to_name.get(from_id, "").lower()
#             to_name   = id_to_name.get(to_id,   "").lower()
#             if not from_name or not to_name:
#                 continue

#             key = (from_name, to_name)
#             m.relations.setdefault(key, set()).add(rel)

#             if rel == "INSIDE":
#                 m.container_of[from_name] = to_name

#             if from_name == "character":
#                 if rel == "HOLDS_RH":
#                     m.hand_right = to_name
#                 elif rel == "HOLDS_LH":
#                     m.hand_left = to_name

#         return m

#     def copy(self) -> "ObjectStateModel":
#         new = ObjectStateModel()
#         new.object_states       = {k: set(v) for k, v in self.object_states.items()}
#         new.object_states_by_id = {k: set(v) for k, v in self.object_states_by_id.items()}
#         new.id_to_name          = dict(self.id_to_name)
#         new.name_to_ids         = {k: list(v) for k, v in self.name_to_ids.items()}
#         new.relations           = {k: set(v) for k, v in self.relations.items()}
#         new.container_of        = dict(self.container_of)
#         new.hand_right          = self.hand_right
#         new.hand_left           = self.hand_left
#         new.char_sitting        = self.char_sitting
#         new.char_lying          = self.char_lying
#         return new

#     # ──────────────────────────────────────────────────────────────────────────
#     # Primitive queries
#     # ──────────────────────────────────────────────────────────────────────────

#     def has_state(self, obj: str, state: str) -> bool:
#         return state.upper() in self.object_states.get(obj.lower(), set())

#     def has_relation(self, from_obj: str, to_obj: str, rel: str) -> bool:
#         return rel.upper() in self.relations.get(
#             (from_obj.lower(), to_obj.lower()), set()
#         )

#     def is_next_to(self, obj: str) -> bool:
#         return self.has_relation("character", obj, "CLOSE")

#     def is_facing(self, obj: str) -> bool:
#         return self.has_relation("character", obj, "FACING")

#     def is_holding(self, obj: str) -> bool:
#         return obj.lower() in (self.hand_right, self.hand_left)

#     def hands_full(self) -> bool:
#         return self.hand_right is not None and self.hand_left is not None

#     def holding_anything(self) -> bool:
#         return self.hand_right is not None or self.hand_left is not None

#     def get_container(self, obj: str) -> str:
#         """Return direct container of obj, or None if not inside anything."""
#         return self.container_of.get(obj.lower())

#     def container_is_open(self, obj: str) -> bool:
#         """True if obj has no container, or its immediate container is OPEN."""
#         container = self.get_container(obj)
#         if container is None:
#             return True
#         return self.has_state(container, "OPEN")

#     def target_accessible(self, target: str) -> bool:
#         """
#         Target container is open or not openable.
#         PDDL put_inside: (not can_open) OR open
#         """
#         if not self.has_state(target, "CAN_OPEN"):
#             return True   # not a container — surface placement always OK
#         return self.has_state(target, "OPEN")

#     # ──────────────────────────────────────────────────────────────────────────
#     # Precondition checker (object-aware)
#     # ──────────────────────────────────────────────────────────────────────────

#     def satisfies(self, precondition: str, obj: str,
#                   target: str = None) -> bool:
#         """
#         Check a single precondition against the actual object states.
#         obj    — primary object of the action
#         target — secondary object (for 2-arg actions like PUTBACK, PUTIN)
#         """
#         obj    = (obj    or "").lower().strip()
#         target = (target or "").lower().strip()

#         # ── Spatial ──────────────────────────────────────────────────────────
#         if precondition == "next_to_obj":
#             return self.is_next_to(obj)
#         if precondition == "next_to_target":
#             return self.is_next_to(target)
#         if precondition == "facing_obj":
#             return self.is_facing(obj)

#         # ── Hands / holding ──────────────────────────────────────────────────
#         if precondition == "holds_obj":
#             return self.is_holding(obj)
#         if precondition == "not_holds_obj":
#             return not self.is_holding(obj)
#         if precondition == "not_both_hands_full":
#             return not self.hands_full()

#         # ── Container access ─────────────────────────────────────────────────
#         if precondition == "obj_not_inside_closed_container":
#             return self.container_is_open(obj)
#         if precondition == "target_open_or_not_openable":
#             return self.target_accessible(target)

#         # ── Object dynamic states ─────────────────────────────────────────────
#         if precondition == "open":
#             return self.has_state(obj, "OPEN")
#         if precondition == "closed":
#             return self.has_state(obj, "CLOSED")
#         if precondition == "on":
#             return self.has_state(obj, "ON")
#         if precondition == "off":
#             return self.has_state(obj, "OFF")
#         if precondition == "not_on":
#             return not self.has_state(obj, "ON")
#         if precondition == "plugged_in":
#             return self.has_state(obj, "PLUGGED_IN")
#         if precondition == "plugged_out":
#             return self.has_state(obj, "PLUGGED_OUT")

#         # ── Character posture ─────────────────────────────────────────────────
#         if precondition == "not_sitting":
#             return not self.char_sitting
#         if precondition == "not_lying":
#             return not self.char_lying
#         if precondition == "sitting_or_lying":
#             return self.char_sitting or self.char_lying

#         # ── Object static properties ──────────────────────────────────────────
#         if precondition == "can_open":
#             return self.has_state(obj, "CAN_OPEN")
#         if precondition == "has_switch":
#             return self.has_state(obj, "HAS_SWITCH")
#         if precondition == "has_plug":
#             return self.has_state(obj, "HAS_PLUG")
#         if precondition == "grabbable":
#             return self.has_state(obj, "GRABBABLE")
#         if precondition == "sittable":
#             return self.has_state(obj, "SITTABLE")
#         if precondition == "lieable":
#             return self.has_state(obj, "LIEABLE")
#         if precondition == "movable":
#             return self.has_state(obj, "MOVABLE")
#         if precondition == "readable":
#             return self.has_state(obj, "READABLE")
#         if precondition == "eatable":
#             return self.has_state(obj, "EATABLE")
#         if precondition == "cuttable":
#             return self.has_state(obj, "CUTTABLE")
#         if precondition == "clothes":
#             return self.has_state(obj, "CLOTHES")
#         if precondition == "lookable":
#             return self.has_state(obj, "LOOKABLE")
#         if precondition == "pourable":
#             return self.has_state(obj, "POURABLE")
#         if precondition == "drinkable":
#             return self.has_state(obj, "DRINKABLE")
#         if precondition == "hangable":
#             return self.has_state(obj, "HANGABLE")

#         # Unknown precondition — assume satisfied to avoid silent hard blocks
#         return True

#     def check_all(self, preconditions: list, obj: str,
#                   target: str = None) -> list:
#         """Return list of unsatisfied preconditions for (action, obj, target)."""
#         return [
#             p for p in preconditions
#             if not self.satisfies(p, obj, target)
#         ]

#     # ──────────────────────────────────────────────────────────────────────────
#     # Mutators — apply action effects
#     # ──────────────────────────────────────────────────────────────────────────

#     def apply(self, action: str, obj: str, target: str = None):
#         """Update model state after executing action on obj (and target)."""
#         action = action.upper()
#         obj    = (obj    or "").lower().strip()
#         target = (target or "").lower().strip()

#         # ── Navigation ───────────────────────────────────────────────────────
#         if action in ("WALK", "RUN", "FIND"):
#             self.relations.setdefault(("character", obj), set()).add("CLOSE")

#         elif action == "TURNTO":
#             self.relations.setdefault(("character", obj), set()).add("FACING")

#         elif action == "POINTAT":
#             pass  # no state change

#         # ── Grabbing / placing ───────────────────────────────────────────────
#         elif action == "GRAB":
#             if self.hand_right is None:
#                 self.hand_right = obj
#             elif self.hand_left is None:
#                 self.hand_left = obj
#             # Object leaves its container when grabbed
#             self.container_of.pop(obj, None)

#         elif action in ("PUTBACK", "PUTOBJBACK"):
#             self._release(obj)
#             if target:
#                 self.relations.setdefault((obj, target), set()).add("ON")

#         elif action == "PUTIN":
#             self._release(obj)
#             if target:
#                 self.container_of[obj] = target

#         elif action in ("DROP", "PUTON", "PUTOFF", "POUR", "RELEASE"):
#             self._release(obj)

#         # ── Containers ───────────────────────────────────────────────────────
#         elif action == "OPEN":
#             s = self.object_states.setdefault(obj, set())
#             s.add("OPEN")
#             s.discard("CLOSED")

#         elif action == "CLOSE":
#             s = self.object_states.setdefault(obj, set())
#             s.add("CLOSED")
#             s.discard("OPEN")

#         # ── Appliances ───────────────────────────────────────────────────────
#         elif action == "SWITCHON":
#             s = self.object_states.setdefault(obj, set())
#             s.add("ON")
#             s.discard("OFF")

#         elif action == "SWITCHOFF":
#             s = self.object_states.setdefault(obj, set())
#             s.add("OFF")
#             s.discard("ON")

#         elif action == "PLUGIN":
#             self.object_states.setdefault(obj, set()).add("PLUGGED_IN")
#             self.object_states[obj].discard("PLUGGED_OUT")

#         elif action == "PLUGOUT":
#             self.object_states.setdefault(obj, set()).add("PLUGGED_OUT")
#             self.object_states[obj].discard("PLUGGED_IN")

#         # ── Character posture ─────────────────────────────────────────────────
#         elif action == "SIT":
#             self.char_sitting = True
#             self.char_lying   = False

#         elif action == "LIE":
#             self.char_lying   = True
#             self.char_sitting = False

#         elif action in ("STANDUP", "WAKEUP"):
#             self.char_sitting = False
#             self.char_lying   = False

#         elif action == "SLEEP":
#             pass  # posture unchanged; character stays sitting/lying

#         # ── Cleaning ─────────────────────────────────────────────────────────
#         elif action in ("WASH", "RINSE", "SCRUB", "WIPE"):
#             s = self.object_states.setdefault(obj, set())
#             s.add("CLEAN")
#             s.discard("DIRTY")

#         # All other actions (EAT, DRINK, READ, WATCH, TOUCH …) have no tracked
#         # state change in the scene graph model.

#     def _release(self, obj: str):
#         """Free one hand holding obj."""
#         obj = obj.lower()
#         if self.hand_right == obj:
#             self.hand_right = None
#         elif self.hand_left == obj:
#             self.hand_left = None

#     # ──────────────────────────────────────────────────────────────────────────
#     # Debug helpers
#     # ──────────────────────────────────────────────────────────────────────────

#     def __repr__(self):
#         return (
#             f"ObjectStateModel("
#             f"sitting={self.char_sitting}, lying={self.char_lying}, "
#             f"rh={self.hand_right}, lh={self.hand_left}, "
#             f"objects={list(self.object_states.keys())})"
#         )


# if __name__ == "__main__":
#     env = {
#         "nodes": [
#             {"id": 1, "class_name": "character", "states": [], "properties": []},
#             {"id": 2, "class_name": "fridge",
#              "states": ["CLOSED", "PLUGGED_IN"],
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

#     m = ObjectStateModel.from_env_dict(env)
#     print(m)

#     # apple is inside CLOSED fridge → should NOT be accessible
#     assert not m.satisfies("obj_not_inside_closed_container", "apple"), \
#         "apple should be inaccessible inside closed fridge"

#     # Open fridge
#     m.apply("OPEN", "fridge")
#     assert m.satisfies("obj_not_inside_closed_container", "apple"), \
#         "apple should be accessible after opening fridge"

#     # Grab apple
#     m.apply("GRAB", "apple")
#     assert m.satisfies("holds_obj", "apple"), "should hold apple"
#     assert m.get_container("apple") is None, "apple should leave fridge on grab"

#     print("All assertions passed ✅")
"""
object_state_model.py
=====================
Per-object state representation for SDA-Planner.
Replaces the flat global state set that loses object identity.

Each object gets its own state set. Relations are tracked as
(from_obj, to_obj) -> set of relation types. Container membership
is tracked explicitly for inside-closed-container checks.
"""


import os as _os
import json as _json

# Load VirtualHome object states catalogue for smart defaults
_STATES_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "object_states.json")
try:
    with open(_STATES_PATH) as _f:
        _VH_OBJECT_STATES: dict = _json.load(_f)
except FileNotFoundError:
    _VH_OBJECT_STATES: dict = {}

# Objects that can be ON/OFF (from object_states.json)
_CAN_ON_OFF  = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "on" in v and "off" in v)
# Objects that can be OPEN/CLOSED
_CAN_OPEN_CL = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "open" in v and "closed" in v)
# Objects that can be PLUGGED/UNPLUGGED
_CAN_PLUGGED = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "plugged" in v or "unplugged" in v)
# Objects that are grabbable
_CAN_GRAB    = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "grabbed" in v)
# Objects SqueezeExecutor accepts in addition to CLOTHES (execution.py line 1971-1984)
_SQUEEZABLE_CLASSES = frozenset({
    "cleaning_solution", "tooth_paste", "shampoo", "food_peanut_butter",
    "dish_soap", "soap", "towel", "rag", "paper", "sponge",
    "food_lemon", "check",
})


def _split_nid(obj):
    """'light_245' -> ('light', '245'); 'light' -> ('light', None)."""
    s = str(obj or "").strip()
    if "_" in s:
        base, maybe = s.rsplit("_", 1)
        if maybe.isdigit():
            return base, maybe
    return s, None


class ObjectStateModel:
    """
    Tracks state per object, not globally.

    object_states : { obj_name -> set of VH state/property strings }
        e.g. {"fridge": {"CLOSED", "PLUGGED_IN"}, "apple": {"GRABBABLE"}}

    relations     : { (from_name, to_name) -> set of relation types }
        e.g. {("character", "apple"): {"HOLDS_RH"},
               ("character", "fridge"): {"CLOSE"}}
        Keys are stored at BOTH granularities when instance ids are known:
        ("character", "light") and ("character", "light_245") — so queries
        with an instance-qualified name (light_411) are answered per-instance
        while bare class-name queries keep the merged/legacy behaviour.

    container_of  : { obj_name -> container_name }
        e.g. {"apple": "fridge"}  — apple is inside fridge
    """

    def __init__(self):
        self.object_states: dict = {}   # obj_name -> set of state strings (merged across instances)
        self.object_states_by_id: dict = {}  # obj_id -> set of state strings (per instance)
        self.id_to_name: dict  = {}     # obj_id -> class_name
        self.name_to_ids: dict = {}     # class_name -> list of ids
        self.relations:   dict = {}     # (from_name, to_name) -> set of relation strings
        self.container_of: dict = {}    # obj_name -> its direct container
        self.hand_right:   str  = None
        self.hand_left:    str  = None
        self.char_sitting: bool = False
        self.char_lying:   bool = False

    # ──────────────────────────────────────────────────────────────────────────
    # Loaders
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def from_env_dict(
        cls,
        env_dict:     dict,
        char_sitting: bool = False,
        char_lying:   bool = False,
    ) -> "ObjectStateModel":
        """Build model from VirtualHome environment dict (nodes + edges)."""
        m = cls()
        m.char_sitting = char_sitting
        m.char_lying   = char_lying

        if not env_dict:
            return m

        id_to_name: dict = {}

        # ── Pass 1: nodes → object_states_by_id + merged object_states ─────────
        for node in env_dict.get("nodes", []):
            name = node.get("class_name", "").lower().strip()
            nid  = node.get("id")
            if nid is not None:
                id_to_name[nid]       = name
                m.id_to_name[nid]     = name
                m.name_to_ids.setdefault(name, []).append(nid)

            states = {s.upper() for s in node.get("states", [])}
            props  = {p.upper() for p in node.get("properties", [])}

            # ── Smart defaults based on object_states.json catalogue ──────────
            # Only apply defaults when the object type is known to support
            # the state — prevents false positives on unknown objects.
            obj_catalogue = _VH_OBJECT_STATES.get(name, {})

            if "HAS_SWITCH" in props or name in _CAN_ON_OFF:
                if "ON" not in states and "OFF" not in states:
                    states.add("OFF")   # default: off
                # EAI assumes all devices are plugged in
                if "PLUGGED_OUT" not in states:
                    states.add("PLUGGED_IN")
                # FIX 1: ensure inferred property is always explicit in combined.
                # At runtime, env_state.to_dict() sometimes omits properties for
                # objects the EAI derived from its catalogue rather than the scene.
                # Without this, BFS precondition checks like can_open / has_switch
                # silently return False and the tree always fails for those objects.
                props.add("HAS_SWITCH")

            if "HAS_PLUG" in props or name in _CAN_PLUGGED:
                if "PLUGGED_OUT" not in states and "PLUGGED_IN" not in states:
                    states.add("PLUGGED_IN")
                props.add("HAS_PLUG")

            if "CAN_OPEN" in props or name in _CAN_OPEN_CL:
                if "OPEN" not in states and "CLOSED" not in states:
                    states.add("CLOSED")   # default: closed
                props.add("CAN_OPEN")
                
            if "GRABBABLE" in props or name in _CAN_GRAB:
                props.add("GRABBABLE")

            combined = states | props

            # Store per-ID (exact state of this specific instance)
            if nid is not None:
                m.object_states_by_id[nid] = combined

            # Store merged by name:
            # - Properties (permanent): union across all instances
            # - Dynamic states (ON/OFF, OPEN/CLOSED): keep ALL variants —
            #   a name has a state if ANY instance has it. This is optimistic
            #   but correct for planning: if one light is OFF we can SWITCHON it.
            if name not in m.object_states:
                m.object_states[name] = set(combined)
            else:
                m.object_states[name] |= combined

            # Extract character posture from the scene graph when available
            if name == "character":
                if "SITTING" in states:
                    m.char_sitting = True
                if "LYING" in states:
                    m.char_lying = True

        # # ── Pass 2: edges → relations + container_of ─────────────────────────
        # for edge in env_dict.get("edges", []):
        #     from_id   = edge.get("from_id")
        #     to_id     = edge.get("to_id")
        #     rel       = edge.get("relation_type", "").upper()
        #     from_name = id_to_name.get(from_id, "").lower()
        #     to_name   = id_to_name.get(to_id,   "").lower()
        #     if not from_name or not to_name:
        #         continue

        #     key = (from_name, to_name)
        #     m.relations.setdefault(key, set()).add(rel)

        #     if rel == "INSIDE":
        #         m.container_of[from_name] = to_name

        #     if from_name == "character":
        #         if rel == "HOLDS_RH":
        #             m.hand_right = to_name
        #         elif rel == "HOLDS_LH":
        #             m.hand_left = to_name
        # ── Pass 2: edges → relations + container_of ─────────────────────────
        for edge in env_dict.get("edges", []):
            from_id   = edge.get("from_id")
            to_id     = edge.get("to_id")
            rel       = edge.get("relation_type", "").upper()
            from_name = id_to_name.get(from_id, "").lower()
            to_name   = id_to_name.get(to_id,   "").lower()
            if not from_name or not to_name:
                continue

            key = (from_name, to_name)
            m.relations.setdefault(key, set()).add(rel)

            # Instance-qualified duplicate keys (character stays bare) so
            # queries like is_next_to("light_411") answer per-instance.
            from_full = from_name if from_name == "character" or from_id is None \
                else f"{from_name}_{from_id}"
            to_full = to_name if to_name == "character" or to_id is None \
                else f"{to_name}_{to_id}"
            if (from_full, to_full) != key:
                m.relations.setdefault((from_full, to_full), set()).add(rel)

            if rel == "INSIDE":
                # FIX: Only store as container if to_name is an actual container
                # (has CAN_OPEN or CONTAINERS property). This prevents room membership
                # (dish_soap INSIDE dining_room) from being mistaken for container access,
                # which caused get_container() to return "dining_room" instead of the
                # real container, making the tree try to OPEN a room.
                to_states = m.object_states.get(to_name, set())
                if "CAN_OPEN" in to_states or "CONTAINERS" in to_states:
                    m.container_of[from_name] = to_name
                    if from_full != from_name:
                        m.container_of[from_full] = to_name

            if from_name == "character":
                if rel == "HOLDS_RH":
                    m.hand_right = to_name
                elif rel == "HOLDS_LH":
                    m.hand_left = to_name

        return m

    def copy(self) -> "ObjectStateModel":
        new = ObjectStateModel()
        new.object_states       = {k: set(v) for k, v in self.object_states.items()}
        new.object_states_by_id = {k: set(v) for k, v in self.object_states_by_id.items()}
        new.id_to_name          = dict(self.id_to_name)
        new.name_to_ids         = {k: list(v) for k, v in self.name_to_ids.items()}
        new.relations           = {k: set(v) for k, v in self.relations.items()}
        new.container_of        = dict(self.container_of)
        new.hand_right          = self.hand_right
        new.hand_left           = self.hand_left
        new.char_sitting        = self.char_sitting
        new.char_lying          = self.char_lying
        return new

    # ──────────────────────────────────────────────────────────────────────────
    # Primitive queries
    # ──────────────────────────────────────────────────────────────────────────

    def has_state(self, obj: str, state: str) -> bool:
        """Instance-aware: 'light_245' checks that instance's states when the
        id is known to the model; bare 'light' keeps the merged class view."""
        name, oid = _split_nid(obj.lower())
        if oid is not None:
            try:
                by_id = self.object_states_by_id.get(int(oid))
            except ValueError:
                by_id = None
            if by_id is not None:
                return state.upper() in by_id
        return state.upper() in self.object_states.get(name, set())

    def has_relation(self, from_obj: str, to_obj: str, rel: str) -> bool:
        return rel.upper() in self.relations.get(
            (from_obj.lower(), to_obj.lower()), set()
        )

    def is_next_to(self, obj: str) -> bool:
        return self.has_relation("character", obj, "CLOSE")

    def is_facing(self, obj: str) -> bool:
        return self.has_relation("character", obj, "FACING")

    def is_holding(self, obj: str) -> bool:
        # Hands store bare class names — strip any instance id for comparison.
        name, _ = _split_nid(obj.lower())
        return name in (self.hand_right, self.hand_left)

    def hands_full(self) -> bool:
        return self.hand_right is not None and self.hand_left is not None

    def holding_anything(self) -> bool:
        return self.hand_right is not None or self.hand_left is not None

    def holding_knife(self) -> bool:
        """True if either hand holds an object whose class name contains 'knife'.
        Mirrors CutExecutor's runtime knife check (any held 'knife*' class)."""
        return any(
            held is not None and "knife" in held
            for held in (self.hand_right, self.hand_left)
        )

    def _has_eatable_on(self, obj: str) -> bool:
        """True if any object with EATABLE property is ON `obj`.
        Matches EatExecutor's loophole that accepts e.g. EAT plate when food is on it.
        """
        obj = obj.lower()
        for (a, b), rels in self.relations.items():
            if b == obj and "ON" in rels:
                if self.has_state(a, "EATABLE"):
                    return True
        return False

    def get_container(self, obj: str) -> str:
        """Return direct container of obj, or None if not inside anything.
        Tries the instance-qualified key first, then the bare class name."""
        obj = obj.lower()
        if obj in self.container_of:
            return self.container_of[obj]
        name, _ = _split_nid(obj)
        return self.container_of.get(name)

    # def container_is_open(self, obj: str) -> bool:
    #     """True if obj has no container, or its immediate container is OPEN."""
    #     container = self.get_container(obj)
    #     if container is None:
    #         return True
    #     return self.has_state(container, "OPEN")
    def container_is_open(self, obj: str) -> bool:
        """True if obj has no container, or its immediate container is OPEN."""
        container = self.get_container(obj)
        if container is None:
            return True
        # Safety net: if container is not openable, treat as open
        if not self.has_state(container, "CAN_OPEN"):
            return True
        return self.has_state(container, "OPEN")

    def target_accessible(self, target: str) -> bool:
        """
        Target container is open or not openable.
        PDDL put_inside: (not can_open) OR open
        """
        if not self.has_state(target, "CAN_OPEN"):
            return True   # not a container — surface placement always OK
        return self.has_state(target, "OPEN")

    # ──────────────────────────────────────────────────────────────────────────
    # Precondition checker (object-aware)
    # ──────────────────────────────────────────────────────────────────────────

    def satisfies(self, precondition: str, obj: str,
                  target: str = None) -> bool:
        """
        Check a single precondition against the actual object states.
        obj    — primary object of the action
        target — secondary object (for 2-arg actions like PUTBACK, PUTIN)
        """
        obj    = (obj    or "").lower().strip()
        target = (target or "").lower().strip()

        # ── Spatial ──────────────────────────────────────────────────────────
        if precondition == "next_to_obj":
            return self.is_next_to(obj)
        if precondition == "next_to_target":
            return self.is_next_to(target)
        if precondition == "facing_obj":
            return self.is_facing(obj)

        # ── Hands / holding ──────────────────────────────────────────────────
        if precondition == "holds_obj":
            return self.is_holding(obj)
        if precondition == "holds_any_obj":
            # WIPE — executor requires holding ANY object as wiping tool
            return self.holding_anything()
        if precondition == "holds_knife":
            # CUT — executor requires a held object whose class contains "knife"
            return self.holding_knife()
        if precondition == "not_holds_obj":
            return not self.is_holding(obj)
        if precondition == "not_both_hands_full":
            return not self.hands_full()
        if precondition == "on_char":
            return "ON_CHAR" in self.relations.get(("character", obj), set())
        if precondition == "not_on_char":
            return "ON_CHAR" not in self.relations.get(("character", obj), set())

        # ── Container access ─────────────────────────────────────────────────
        if precondition == "obj_not_inside_closed_container":
            return self.container_is_open(obj)
        if precondition == "target_open_or_not_openable":
            return self.target_accessible(target)

        # ── Object dynamic states ─────────────────────────────────────────────
        if precondition == "open":
            return self.has_state(obj, "OPEN")
        if precondition == "closed":
            return self.has_state(obj, "CLOSED")
        if precondition == "on":
            return self.has_state(obj, "ON")
        if precondition == "off":
            return self.has_state(obj, "OFF")
        if precondition == "not_on":
            return not self.has_state(obj, "ON")
        if precondition == "plugged_in":
            return self.has_state(obj, "PLUGGED_IN")
        if precondition == "plugged_out":
            return self.has_state(obj, "PLUGGED_OUT")
        if precondition == "not_plugged_out":
            return not self.has_state(obj, "PLUGGED_OUT")
        if precondition == "not_plugged_in":
            return not self.has_state(obj, "PLUGGED_IN")

        # ── Character posture ─────────────────────────────────────────────────
        if precondition == "not_sitting":
            return not self.char_sitting
        if precondition == "not_lying":
            return not self.char_lying
        if precondition == "sitting_or_lying":
            return self.char_sitting or self.char_lying

        # ── Object static properties ──────────────────────────────────────────
        if precondition == "can_open":
            return self.has_state(obj, "CAN_OPEN")
        if precondition == "has_switch":
            # TypeExecutor special-cases keyboard: class_name=="keyboard" skips HAS_SWITCH check
            if _split_nid(obj.lower())[0] == "keyboard":
                return True
            return self.has_state(obj, "HAS_SWITCH")
        if precondition == "person":
            return self.has_state(obj, "PERSON")
        if precondition == "has_plug":
            return self.has_state(obj, "HAS_PLUG")
        if precondition == "has_plug_or_has_switch":
            return self.has_state(obj, "HAS_PLUG") or self.has_state(obj, "HAS_SWITCH")
        if precondition == "pourable_or_drinkable":
            return self.has_state(obj, "POURABLE") or self.has_state(obj, "DRINKABLE")
        if precondition == "drinkable_or_recipient":
            return self.has_state(obj, "DRINKABLE") or self.has_state(obj, "RECIPIENT")
        if precondition == "target_is_recipient":
            # executor PourExecutor: RECIPIENT property OR one of these classes
            return (self.has_state(target, "RECIPIENT")
                    or _split_nid(target)[0] in ("hands_both", "sponge", "face"))
        if precondition == "grabbable":
            return self.has_state(obj, "GRABBABLE")
        if precondition == "sittable":
            return self.has_state(obj, "SITTABLE")
        if precondition == "lieable":
            return self.has_state(obj, "LIEABLE")
        if precondition == "movable":
            return self.has_state(obj, "MOVABLE")
        if precondition == "readable":
            return self.has_state(obj, "READABLE")
        if precondition == "eatable":
            return self.has_state(obj, "EATABLE")
        if precondition == "cuttable":
            return self.has_state(obj, "CUTTABLE")
        if precondition == "clothes":
            return self.has_state(obj, "CLOTHES")
        if precondition == "clothes_or_squeezable":
            # SqueezeExecutor accepts CLOTHES OR specific squeezable classes
            return (self.has_state(obj, "CLOTHES")
                    or _split_nid(obj)[0] in _SQUEEZABLE_CLASSES)
        if precondition == "eatable_or_has_eatable_on":
            # EatExecutor accepts EATABLE OR objects with eatable items ON them
            return self.has_state(obj, "EATABLE") or self._has_eatable_on(obj)
        if precondition == "lookable":
            return self.has_state(obj, "LOOKABLE")
        if precondition == "pourable":
            return self.has_state(obj, "POURABLE")
        if precondition == "drinkable":
            return self.has_state(obj, "DRINKABLE")
        if precondition == "hangable":
            return self.has_state(obj, "HANGABLE")

        # Unknown precondition — assume satisfied to avoid silent hard blocks
        return True

    def check_all(self, preconditions: list, obj: str,
                  target: str = None) -> list:
        """Return list of unsatisfied preconditions for (action, obj, target)."""
        return [
            p for p in preconditions
            if not self.satisfies(p, obj, target)
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # Mutators — apply action effects
    # ──────────────────────────────────────────────────────────────────────────

    def _char_rel_keys(self, obj: str):
        """Both relation keys for a character→obj edge: instance + class."""
        keys = [("character", obj)]
        name, oid = _split_nid(obj)
        if oid is not None and name != obj:
            keys.append(("character", name))
        return keys

    def _mutate_states(self, obj: str, add=(), discard=()):
        """Mutate the class-level state bucket and, when an instance id is
        given, the per-instance bucket too — keeps both views consistent."""
        name, oid = _split_nid(obj)
        buckets = [self.object_states.setdefault(name, set())]
        if oid is not None:
            try:
                buckets.append(self.object_states_by_id.setdefault(int(oid), set()))
            except ValueError:
                pass
        for s in buckets:
            for a in add:
                s.add(a)
            for d in discard:
                s.discard(d)

    def apply(self, action: str, obj: str, target: str = None):
        """Update model state after executing action on obj (and target).
        obj/target may be bare class names or instance-qualified (light_245)."""
        action = action.upper()
        obj    = (obj    or "").lower().strip()
        target = (target or "").lower().strip()

        # ── Navigation ───────────────────────────────────────────────────────
        if action in ("WALK", "RUN"):
            # PDDL walk_towards effect:
            #   become next_to obj
            #   become next_to everything obj_next_to obj
            #   lose next_to everything NOT obj_next_to obj
            # We model this by clearing ALL previous character CLOSE relations
            # then adding the new one. Neighbours of obj (obj_next_to edges)
            # are preserved since we don't track obj_next_to separately.
            # Executor (WalkExecutor) also deletes all FACING edges on walk,
            # so a prior TURNTO no longer holds after moving away.
            old_close = [k for k in self.relations
                         if k[0] == "character" and "CLOSE" in self.relations[k]]
            for k in old_close:
                self.relations[k].discard("CLOSE")
            self._clear_char_facing()
            for k in self._char_rel_keys(obj):
                self.relations.setdefault(k, set()).add("CLOSE")

        elif action == "FIND":
            # FIND auto-navigates — EAI moves character next to obj internally.
            # Record CLOSE without clearing others (no teleport), but the
            # executor (_FindExecutor) does delete FACING edges, so clear those.
            self._clear_char_facing()
            for k in self._char_rel_keys(obj):
                self.relations.setdefault(k, set()).add("CLOSE")

        elif action == "TURNTO":
            # Executor (TurnToExecutor) deletes all prior FACING edges and
            # faces ONLY the new object — model the same so a stale facing
            # from an earlier TURNTO doesn't satisfy facing_obj for it.
            self._clear_char_facing()
            for k in self._char_rel_keys(obj):
                self.relations.setdefault(k, set()).add("FACING")

        elif action == "POINTAT":
            pass  # no state change

        # ── Grabbing / placing ───────────────────────────────────────────────
        elif action == "GRAB":
            # Hands store bare class names (is_holding strips ids to match).
            held, _ = _split_nid(obj)
            if self.hand_right is None:
                self.hand_right = held
            elif self.hand_left is None:
                self.hand_left = held
            # Object leaves its container when grabbed
            self.container_of.pop(obj, None)
            self.container_of.pop(held, None)

        elif action in ("PUTBACK", "PUTOBJBACK"):
            self._release(obj)
            if target:
                self.relations.setdefault((obj, target), set()).add("ON")

        elif action == "PUTIN":
            self._release(obj)
            if target:
                self.container_of[obj] = target
                name, _ = _split_nid(obj)
                if name != obj:
                    self.container_of[name] = target

        elif action in ("DROP", "RELEASE"):
            self._release(obj)

        elif action == "POUR":
            # executor PourExecutor: poured obj goes INSIDE target; the hand
            # is released ONLY when class_name == "water" — other pourables
            # (bottle, cup, …) stay held.
            if target:
                self.container_of[obj] = target
            if _split_nid(obj)[0] == "water":
                self._release(obj)

        elif action == "PUTON":
            # executor: releases hold, adds obj ON char relation
            self._release(obj)
            for k in self._char_rel_keys(obj):
                self.relations.setdefault(k, set()).add("ON_CHAR")

        elif action == "PUTOFF":
            # executor: removes obj ON char relation
            for k in self._char_rel_keys(obj):
                if k in self.relations:
                    self.relations[k].discard("ON_CHAR")

        # ── Containers ───────────────────────────────────────────────────────
        elif action == "OPEN":
            self._mutate_states(obj, add=("OPEN",), discard=("CLOSED",))

        elif action == "CLOSE":
            self._mutate_states(obj, add=("CLOSED",), discard=("OPEN",))

        # ── Appliances ───────────────────────────────────────────────────────
        elif action == "SWITCHON":
            self._mutate_states(obj, add=("ON",), discard=("OFF",))

        elif action == "SWITCHOFF":
            self._mutate_states(obj, add=("OFF",), discard=("ON",))

        elif action == "PLUGIN":
            self._mutate_states(obj, add=("PLUGGED_IN",), discard=("PLUGGED_OUT",))

        elif action == "PLUGOUT":
            self._mutate_states(obj, add=("PLUGGED_OUT",), discard=("PLUGGED_IN",))

        # ── Character posture ─────────────────────────────────────────────────
        elif action == "SIT":
            self.char_sitting = True
            self.char_lying   = False  # executor SitExecutor discards LYING

        elif action == "LIE":
            self.char_lying   = True
            self.char_sitting = False  # executor LieExecutor discards SITTING

        elif action == "STANDUP":
            self.char_sitting = False
            self.char_lying   = False

        elif action in ("SLEEP", "WAKEUP"):
            pass  # executor change_state([]) — no effect on posture

        # ── Cleaning ─────────────────────────────────────────────────────────
        elif action in ("WASH", "RINSE", "SCRUB", "WIPE"):
            self._mutate_states(obj, add=("CLEAN",), discard=("DIRTY",))

        # All other actions (EAT, DRINK, READ, WATCH, TOUCH …) have no tracked
        # state change in the scene graph model.

    def _release(self, obj: str):
        """Free one hand holding obj (hands store bare class names)."""
        name, _ = _split_nid(obj.lower())
        if self.hand_right == name:
            self.hand_right = None
        elif self.hand_left == name:
            self.hand_left = None

    def _clear_char_facing(self):
        """Drop every FACING relation from the character.
        Mirrors WalkExecutor / _FindExecutor / TurnToExecutor, which all delete
        the character's existing FACING edges before (re)establishing posture.
        """
        for k in self.relations:
            if k[0] == "character":
                self.relations[k].discard("FACING")

    # ──────────────────────────────────────────────────────────────────────────
    # Debug helpers
    # ──────────────────────────────────────────────────────────────────────────

    def __repr__(self):
        return (
            f"ObjectStateModel("
            f"sitting={self.char_sitting}, lying={self.char_lying}, "
            f"rh={self.hand_right}, lh={self.hand_left}, "
            f"objects={list(self.object_states.keys())})"
        )


if __name__ == "__main__":
    env = {
        "nodes": [
            {"id": 1, "class_name": "character", "states": [], "properties": []},
            {"id": 2, "class_name": "fridge",
             "states": ["CLOSED", "PLUGGED_IN"],
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

    m = ObjectStateModel.from_env_dict(env)
    print(m)

    # apple is inside CLOSED fridge → should NOT be accessible
    assert not m.satisfies("obj_not_inside_closed_container", "apple"), \
        "apple should be inaccessible inside closed fridge"

    # Open fridge
    m.apply("OPEN", "fridge")
    assert m.satisfies("obj_not_inside_closed_container", "apple"), \
        "apple should be accessible after opening fridge"

    # Grab apple
    m.apply("GRAB", "apple")
    assert m.satisfies("holds_obj", "apple"), "should hold apple"
    assert m.get_container("apple") is None, "apple should leave fridge on grab"

    print("All assertions passed ✅")
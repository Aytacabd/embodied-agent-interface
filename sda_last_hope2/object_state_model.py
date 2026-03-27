"""
object_state_model.py
=====================
Per-object state representation for SDA-Planner.
Supports both object IDs (for execution) and class names (for planning).
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

# Precompute sets for quick membership
_CAN_ON_OFF  = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "on" in v and "off" in v)
_CAN_OPEN_CL = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "open" in v and "closed" in v)
_CAN_PLUGGED = frozenset(k for k, v in _VH_OBJECT_STATES.items()
                          if "plugged" in v or "unplugged" in v)


class ObjectStateModel:
    """
    Tracks state per object instance.

    states_by_id : { obj_id -> set of state strings }
    id_to_class  : { obj_id -> class_name }
    class_to_ids : { class_name -> list of obj_ids }
    relations    : { (from_id, to_id) -> set of relation strings }
    container_of : { obj_id -> container_id }   # direct container
    hand_right, hand_left : obj_id or None
    char_sitting, char_lying : bool
    """

    def __init__(self):
        self.states_by_id: dict = {}          # obj_id -> set of state strings
        self.id_to_class: dict = {}           # obj_id -> class_name
        self.class_to_ids: dict = {}          # class_name -> list of ids

        self.relations: dict = {}             # (from_id, to_id) -> set of relation strings
        self.container_of: dict = {}          # obj_id -> container_id

        self.hand_right: int = None
        self.hand_left: int = None
        self.char_sitting: bool = False
        self.char_lying: bool = False

        # Cache for type-level properties (from object_states.json)
        self._type_props: dict = {}           # class_name -> set of permanent properties

    # --------------------------------------------------------------------------
    # Builders
    # --------------------------------------------------------------------------
    @classmethod
    def from_env_dict(cls, env_dict: dict) -> "ObjectStateModel":
        """Construct model from VirtualHome environment dict (nodes + edges)."""
        m = cls()

        if not env_dict:
            return m

        # ---- Pass 1: nodes ----
        for node in env_dict.get("nodes", []):
            obj_id = node.get("id")
            if obj_id is None:
                continue
            class_name = node.get("class_name", "").lower().strip()
            states = {s.upper() for s in node.get("states", [])}
            props = {p.upper() for p in node.get("properties", [])}

            # Store identity mapping
            m.id_to_class[obj_id] = class_name
            m.class_to_ids.setdefault(class_name, []).append(obj_id)

            # Smart defaults (only if state not provided)
            obj_catalogue = _VH_OBJECT_STATES.get(class_name, {})

            if "HAS_SWITCH" in props or class_name in _CAN_ON_OFF:
                if "ON" not in states and "OFF" not in states:
                    states.add("OFF")
                # EAI assumption: all such objects are plugged in unless told otherwise
                if "PLUGGED_OUT" not in states and "PLUGGED_IN" not in states:
                    states.add("PLUGGED_IN")

            if "HAS_PLUG" in props or class_name in _CAN_PLUGGED:
                if "PLUGGED_OUT" not in states and "PLUGGED_IN" not in states:
                    states.add("PLUGGED_IN")

            if "CAN_OPEN" in props or class_name in _CAN_OPEN_CL:
                if "OPEN" not in states and "CLOSED" not in states:
                    states.add("CLOSED")

            # Store states
            m.states_by_id[obj_id] = states | props

            # Extract character posture if this is the character
            if class_name == "character":
                if "SITTING" in states:
                    m.char_sitting = True
                if "LYING" in states:
                    m.char_lying = True

        # ---- Pass 2: edges ----
        for edge in env_dict.get("edges", []):
            from_id = edge.get("from_id")
            to_id = edge.get("to_id")
            rel = edge.get("relation_type", "").upper()
            if from_id is None or to_id is None:
                continue
            key = (from_id, to_id)
            m.relations.setdefault(key, set()).add(rel)

            if rel == "INSIDE":
                m.container_of[from_id] = to_id

            # Handle hand holding
            if m.id_to_class.get(from_id) == "character":
                if rel == "HOLDS_RH":
                    m.hand_right = to_id
                elif rel == "HOLDS_LH":
                    m.hand_left = to_id

        return m

    def copy(self) -> "ObjectStateModel":
        new = ObjectStateModel()
        new.states_by_id = {k: set(v) for k, v in self.states_by_id.items()}
        new.id_to_class = dict(self.id_to_class)
        new.class_to_ids = {k: list(v) for k, v in self.class_to_ids.items()}
        new.relations = {k: set(v) for k, v in self.relations.items()}
        new.container_of = dict(self.container_of)
        new.hand_right = self.hand_right
        new.hand_left = self.hand_left
        new.char_sitting = self.char_sitting
        new.char_lying = self.char_lying
        return new

    # --------------------------------------------------------------------------
    # Helper: resolve an object reference (ID or class name) to a set of IDs
    # --------------------------------------------------------------------------
    def _resolve(self, obj: int or str) -> set:
        """Return a set of object IDs matching the given reference.
        If obj is an ID, return {obj} if it exists; if it's a class name,
        return all IDs of that class. Return empty set if none found.
        """
        if isinstance(obj, int):
            return {obj} if obj in self.states_by_id else set()
        # assume string
        name = obj.lower().strip()
        return set(self.class_to_ids.get(name, []))

    def _single_id(self, obj: int or str) -> int:
        """Return a single ID for the given reference. Raises ValueError if
        ambiguous or not found. Used when we expect a specific instance.
        """
        ids = self._resolve(obj)
        if not ids:
            raise ValueError(f"Object {obj} not found")
        if len(ids) > 1:
            raise ValueError(f"Ambiguous object {obj} – multiple instances exist")
        return next(iter(ids))

    # --------------------------------------------------------------------------
    # Primitive queries (by ID)
    # --------------------------------------------------------------------------
    def has_state(self, obj_id: int, state: str) -> bool:
        return state.upper() in self.states_by_id.get(obj_id, set())

    def has_relation(self, from_id: int, to_id: int, rel: str) -> bool:
        return rel.upper() in self.relations.get((from_id, to_id), set())

    # --------------------------------------------------------------------------
    # Convenience queries that accept both IDs and class names
    # --------------------------------------------------------------------------
    def is_next_to(self, obj: int or str) -> bool:
        """Check if the character is CLOSE to any instance of obj (if class name)
        or to the specific object ID. If the object is inside a container,
        check next to the container instead."""
        ids = self._resolve(obj)
        if not ids:
            return False

        # Compute the effective object(s) to check next_to:
        # if an object is inside a container, we check next to the container.
        effective_ids = set()
        for oid in ids:
            container = self.get_container(oid)
            if container is not None:
                effective_ids.add(container)
            else:
                effective_ids.add(oid)

        char_id = self._single_id("character") if "character" in self.class_to_ids else None
        if char_id is None:
            return False

        for eid in effective_ids:
            if self.has_relation(char_id, eid, "CLOSE"):
                return True
        return False

    def is_facing(self, obj: int or str) -> bool:
        ids = self._resolve(obj)
        char_id = self._single_id("character") if "character" in self.class_to_ids else None
        if char_id is None:
            return False
        for oid in ids:
            if self.has_relation(char_id, oid, "FACING"):
                return True
        return False

    def is_holding(self, obj: int or str) -> bool:
        """Check if the character holds any instance of obj (if class name)
        or the specific object ID."""
        ids = self._resolve(obj)
        # FIXED: use boolean OR instead of any()
        return (self.hand_right in ids) or (self.hand_left in ids)

    def hands_full(self) -> bool:
        return self.hand_right is not None and self.hand_left is not None

    def holding_anything(self) -> bool:
        return self.hand_right is not None or self.hand_left is not None

    def get_container(self, obj_id: int) -> int:
        return self.container_of.get(obj_id)

    def container_is_open(self, obj_id: int) -> bool:
        """True if obj has no container, or its immediate container is OPEN."""
        container = self.get_container(obj_id)
        if container is None:
            return True
        return self.has_state(container, "OPEN")

    def target_accessible(self, target_id: int) -> bool:
        """Target container is open or not openable."""
        if not self.has_state(target_id, "CAN_OPEN"):
            return True   # not a container
        return self.has_state(target_id, "OPEN")

    # --------------------------------------------------------------------------
    # Precondition checker (object‑aware)
    # --------------------------------------------------------------------------
    def satisfies(self, precondition: str, obj: int or str,
                  target: int or str = None) -> bool:
        """
        Check a precondition.
        obj and target can be IDs or class names.
        For class names, the condition is true if ANY instance satisfies it.
        """
        # Spatial
        if precondition == "next_to_obj":
            return self.is_next_to(obj)
        if precondition == "next_to_target":
            return self.is_next_to(target)
        if precondition == "facing_obj":
            return self.is_facing(obj)

        # Hands / holding
        if precondition == "holds_obj":
            return self.is_holding(obj)
        if precondition == "not_holds_obj":
            return not self.is_holding(obj)
        if precondition == "not_both_hands_full":
            return not self.hands_full()

        # Container access
        if precondition == "obj_not_inside_closed_container":
            # Need a single ID to check container
            ids = self._resolve(obj)
            if len(ids) == 0:
                return True   # object not present? assume okay
            # For class name, we require that ALL instances satisfy? Or ANY?
            # The intended meaning: the object we're about to act on must be accessible.
            # Since we don't know which instance, we return True only if all are accessible.
            # This is a safe approximation.
            for oid in ids:
                if not self.container_is_open(oid):
                    return False
            return True
        if precondition == "target_open_or_not_openable":
            ids = self._resolve(target)
            if not ids:
                return True
            for tid in ids:
                if not self.target_accessible(tid):
                    return False
            return True

        # Object dynamic states
        if precondition in ("open", "closed", "on", "off", "not_on",
                            "plugged_in", "plugged_out"):
            # For class name, we need the state to hold for the specific instance.
            # But if we don't have an ID, we check if any instance has the state?
            # The precondition usually refers to a specific object in the action.
            # If obj is a class name, it's ambiguous. We'll check the first ID,
            # but ideally the caller should pass an ID.
            ids = self._resolve(obj)
            if not ids:
                return False
            oid = next(iter(ids))  # take the first
            if precondition == "not_on":
                return not self.has_state(oid, "ON")
            return self.has_state(oid, precondition.upper())

        # Character posture
        if precondition == "not_sitting":
            return not self.char_sitting
        if precondition == "not_lying":
            return not self.char_lying
        if precondition == "sitting_or_lying":
            return self.char_sitting or self.char_lying

        # Object static properties (type-level)
        if precondition in ("can_open", "has_switch", "has_plug", "grabbable",
                            "sittable", "lieable", "movable", "readable",
                            "eatable", "cuttable", "clothes", "lookable",
                            "pourable", "drinkable", "hangable"):
            # These are usually type-level properties. We'll check if any instance
            # of the class (or the given ID) has it.
            ids = self._resolve(obj)
            if not ids:
                return False
            # For class name, we require that all instances have it? Or any?
            # In planning, if an object type is grabbable, it's usually true for all.
            # We'll check the first.
            oid = next(iter(ids))
            return self.has_state(oid, precondition.upper())

        # Unknown precondition
        return True

    def check_all(self, preconditions: list, obj: int or str,
                  target: int or str = None) -> list:
        """Return list of unsatisfied preconditions."""
        return [
            p for p in preconditions
            if not self.satisfies(p, obj, target)
        ]

    # --------------------------------------------------------------------------
    # Mutators – apply action effects
    # --------------------------------------------------------------------------
    def apply(self, action: str, obj: int or str, target: int or str = None):
        """Update model state after executing action on obj (and target)."""
        action = action.upper()
        # Resolve obj and target to IDs for the specific instance we're acting on.
        # For actions that affect an object, we need a concrete ID.
        try:
            obj_id = self._single_id(obj) if obj is not None else None
        except ValueError:
            # If ambiguous, we cannot know which instance to change.
            # In a real execution, the simulator would give us the ID.
            # We'll raise to indicate a bug.
            raise ValueError(f"Ambiguous object {obj} in apply()")

        target_id = None
        if target is not None:
            try:
                target_id = self._single_id(target)
            except ValueError:
                raise ValueError(f"Ambiguous target {target} in apply()")

        # Navigation
        if action in ("WALK", "RUN"):
            # Clear all previous CLOSE relations for character
            char_id = self._single_id("character")
            keys_to_remove = [(f, t) for (f, t) in self.relations
                              if f == char_id and "CLOSE" in self.relations[(f, t)]]
            for k in keys_to_remove:
                self.relations[k].discard("CLOSE")
                if not self.relations[k]:
                    del self.relations[k]
            # Add new CLOSE
            self.relations.setdefault((char_id, obj_id), set()).add("CLOSE")

        elif action == "FIND":
            char_id = self._single_id("character")
            self.relations.setdefault((char_id, obj_id), set()).add("CLOSE")

        elif action == "TURNTO":
            char_id = self._single_id("character")
            self.relations.setdefault((char_id, obj_id), set()).add("FACING")

        # Grabbing / placing
        elif action == "GRAB":
            if self.hand_right is None:
                self.hand_right = obj_id
            elif self.hand_left is None:
                self.hand_left = obj_id
            # Object leaves its container
            self.container_of.pop(obj_id, None)

        elif action in ("PUTBACK", "PUTOBJBACK"):
            self._release(obj_id)
            if target_id:
                self.relations.setdefault((obj_id, target_id), set()).add("ON")

        elif action == "PUTIN":
            self._release(obj_id)
            if target_id:
                self.container_of[obj_id] = target_id

        elif action in ("DROP", "PUTON", "PUTOFF", "POUR", "RELEASE"):
            self._release(obj_id)

        # Containers
        elif action == "OPEN":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("OPEN")
            s.discard("CLOSED")

        elif action == "CLOSE":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("CLOSED")
            s.discard("OPEN")

        # Appliances
        elif action == "SWITCHON":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("ON")
            s.discard("OFF")

        elif action == "SWITCHOFF":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("OFF")
            s.discard("ON")

        elif action == "PLUGIN":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("PLUGGED_IN")
            s.discard("PLUGGED_OUT")

        elif action == "PLUGOUT":
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("PLUGGED_OUT")
            s.discard("PLUGGED_IN")

        # Character posture
        elif action == "SIT":
            self.char_sitting = True
            self.char_lying = False

        elif action == "LIE":
            self.char_lying = True
            self.char_sitting = False

        elif action in ("STANDUP", "WAKEUP"):
            self.char_sitting = False
            self.char_lying = False

        # Cleaning
        elif action in ("WASH", "RINSE", "SCRUB", "WIPE"):
            s = self.states_by_id.setdefault(obj_id, set())
            s.add("CLEAN")
            s.discard("DIRTY")

        # Other actions have no tracked state changes

    def _release(self, obj_id: int):
        """Free the hand holding obj_id."""
        if self.hand_right == obj_id:
            self.hand_right = None
        if self.hand_left == obj_id:
            self.hand_left = None

    # --------------------------------------------------------------------------
    # Debug
    # --------------------------------------------------------------------------
    def __repr__(self):
        return (
            f"ObjectStateModel(\n"
            f"  sitting={self.char_sitting}, lying={self.char_lying},\n"
            f"  rh={self.hand_right}, lh={self.hand_left},\n"
            f"  objects={list(self.id_to_class.keys())}\n)"
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

    # apple (id=3) is inside closed fridge → not accessible
    assert not m.satisfies("obj_not_inside_closed_container", 3), \
        "apple should be inaccessible inside closed fridge"

    # Open fridge
    m.apply("OPEN", 2)
    assert m.satisfies("obj_not_inside_closed_container", 3), \
        "apple should be accessible after opening fridge"

    # Grab apple
    m.apply("GRAB", 3)
    assert m.satisfies("holds_obj", 3), "should hold apple"
    assert m.get_container(3) is None, "apple should leave fridge on grab"

    # Test class‑name queries
    assert m.is_next_to("fridge"), "character should be next to fridge"
    assert m.is_holding("apple"), "character should hold apple"

    print("All assertions passed ✅")
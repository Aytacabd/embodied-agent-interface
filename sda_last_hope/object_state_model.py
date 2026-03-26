"""
object_state_model.py
=====================
Per-object state representation for SDA-Planner.
Replaces the flat global state set that loses object identity.

Each object gets its own state set. Relations are tracked as
(from_obj, to_obj) -> set of relation types. Container membership
is tracked explicitly for inside-closed-container checks.
"""


class ObjectStateModel:
    """
    Tracks state per object, not globally.

    object_states : { obj_name -> set of VH state/property strings }
        e.g. {"fridge": {"CLOSED", "PLUGGED_IN"}, "apple": {"GRABBABLE"}}

    relations     : { (from_name, to_name) -> set of relation types }
        e.g. {("character", "apple"): {"HOLDS_RH"},
               ("character", "fridge"): {"CLOSE"}}

    container_of  : { obj_name -> container_name }
        e.g. {"apple": "fridge"}  — apple is inside fridge
    """

    def __init__(self):
        self.object_states: dict = {}   # obj -> set of state strings
        self.relations:     dict = {}   # (from, to) -> set of relation strings
        self.container_of:  dict = {}   # obj -> its direct container
        self.hand_right:    str  = None  # object held in right hand
        self.hand_left:     str  = None  # object held in left hand
        self.char_sitting:  bool = False
        self.char_lying:    bool = False

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

        # ── Pass 1: nodes → object_states ────────────────────────────────────
        for node in env_dict.get("nodes", []):
            name = node.get("class_name", "").lower().strip()
            nid  = node.get("id")
            if nid is not None:
                id_to_name[nid] = name

            states = {s.upper() for s in node.get("states", [])}
            props  = {p.upper() for p in node.get("properties", [])}

            # ── Smart defaults based on object properties ─────────────────────
            # VirtualHome convention: devices start OFF unless explicitly ON;
            # containers start CLOSED unless explicitly OPEN;
            # devices start PLUGGED_IN unless explicitly PLUGGED_OUT.
            # Without these defaults ObjectStateModel incorrectly reports
            # preconditions like "off" as unsatisfied even when the object
            # has never been touched.
            if "HAS_SWITCH" in props:
                if "ON" not in states:
                    states.add("OFF")
                if "OFF" not in states:
                    states.discard("OFF")   # ON wins if explicitly set
                # EAI assumes all devices are plugged in (PLUGIN/PLUGOUT don't exist)
                if "PLUGGED_OUT" not in states:
                    states.add("PLUGGED_IN")

            if "HAS_PLUG" in props:
                if "PLUGGED_OUT" not in states:
                    states.add("PLUGGED_IN")

            if "CAN_OPEN" in props:
                if "OPEN" not in states:
                    states.add("CLOSED")
                if "CLOSED" not in states:
                    states.discard("CLOSED")  # OPEN wins if explicitly set

            # Merge states and properties into a single set per object.
            # Properties are permanent (GRABBABLE, CAN_OPEN …) while states
            # are dynamic (OPEN, CLOSED, ON, OFF …) — storing them together
            # lets satisfies() use one lookup for both.
            m.object_states[name] = states | props

            # Extract character posture from the scene graph when available
            if name == "character":
                if "SITTING" in states:
                    m.char_sitting = True
                if "LYING" in states:
                    m.char_lying = True

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

            if rel == "INSIDE":
                m.container_of[from_name] = to_name

            if from_name == "character":
                if rel == "HOLDS_RH":
                    m.hand_right = to_name
                elif rel == "HOLDS_LH":
                    m.hand_left = to_name

        return m

    def copy(self) -> "ObjectStateModel":
        new = ObjectStateModel()
        new.object_states = {k: set(v) for k, v in self.object_states.items()}
        new.relations     = {k: set(v) for k, v in self.relations.items()}
        new.container_of  = dict(self.container_of)
        new.hand_right    = self.hand_right
        new.hand_left     = self.hand_left
        new.char_sitting  = self.char_sitting
        new.char_lying    = self.char_lying
        return new

    # ──────────────────────────────────────────────────────────────────────────
    # Primitive queries
    # ──────────────────────────────────────────────────────────────────────────

    def has_state(self, obj: str, state: str) -> bool:
        return state.upper() in self.object_states.get(obj.lower(), set())

    def has_relation(self, from_obj: str, to_obj: str, rel: str) -> bool:
        return rel.upper() in self.relations.get(
            (from_obj.lower(), to_obj.lower()), set()
        )

    def is_next_to(self, obj: str) -> bool:
        return self.has_relation("character", obj, "CLOSE")

    def is_facing(self, obj: str) -> bool:
        return self.has_relation("character", obj, "FACING")

    def is_holding(self, obj: str) -> bool:
        return obj.lower() in (self.hand_right, self.hand_left)

    def hands_full(self) -> bool:
        return self.hand_right is not None and self.hand_left is not None

    def holding_anything(self) -> bool:
        return self.hand_right is not None or self.hand_left is not None

    def get_container(self, obj: str) -> str:
        """Return direct container of obj, or None if not inside anything."""
        return self.container_of.get(obj.lower())

    def container_is_open(self, obj: str) -> bool:
        """True if obj has no container, or its immediate container is OPEN."""
        container = self.get_container(obj)
        if container is None:
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
        if precondition == "not_holds_obj":
            return not self.is_holding(obj)
        if precondition == "not_both_hands_full":
            return not self.hands_full()

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
            return self.has_state(obj, "HAS_SWITCH")
        if precondition == "has_plug":
            return self.has_state(obj, "HAS_PLUG")
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

    def apply(self, action: str, obj: str, target: str = None):
        """Update model state after executing action on obj (and target)."""
        action = action.upper()
        obj    = (obj    or "").lower().strip()
        target = (target or "").lower().strip()

        # ── Navigation ───────────────────────────────────────────────────────
        if action in ("WALK", "RUN", "FIND"):
            self.relations.setdefault(("character", obj), set()).add("CLOSE")

        elif action == "TURNTO":
            self.relations.setdefault(("character", obj), set()).add("FACING")

        elif action == "POINTAT":
            pass  # no state change

        # ── Grabbing / placing ───────────────────────────────────────────────
        elif action == "GRAB":
            if self.hand_right is None:
                self.hand_right = obj
            elif self.hand_left is None:
                self.hand_left = obj
            # Object leaves its container when grabbed
            self.container_of.pop(obj, None)

        elif action in ("PUTBACK", "PUTOBJBACK"):
            self._release(obj)
            if target:
                self.relations.setdefault((obj, target), set()).add("ON")

        elif action == "PUTIN":
            self._release(obj)
            if target:
                self.container_of[obj] = target

        elif action in ("DROP", "PUTON", "PUTOFF", "POUR", "RELEASE"):
            self._release(obj)

        # ── Containers ───────────────────────────────────────────────────────
        elif action == "OPEN":
            s = self.object_states.setdefault(obj, set())
            s.add("OPEN")
            s.discard("CLOSED")

        elif action == "CLOSE":
            s = self.object_states.setdefault(obj, set())
            s.add("CLOSED")
            s.discard("OPEN")

        # ── Appliances ───────────────────────────────────────────────────────
        elif action == "SWITCHON":
            s = self.object_states.setdefault(obj, set())
            s.add("ON")
            s.discard("OFF")

        elif action == "SWITCHOFF":
            s = self.object_states.setdefault(obj, set())
            s.add("OFF")
            s.discard("ON")

        elif action == "PLUGIN":
            self.object_states.setdefault(obj, set()).add("PLUGGED_IN")
            self.object_states[obj].discard("PLUGGED_OUT")

        elif action == "PLUGOUT":
            self.object_states.setdefault(obj, set()).add("PLUGGED_OUT")
            self.object_states[obj].discard("PLUGGED_IN")

        # ── Character posture ─────────────────────────────────────────────────
        elif action == "SIT":
            self.char_sitting = True
            self.char_lying   = False

        elif action == "LIE":
            self.char_lying   = True
            self.char_sitting = False

        elif action in ("STANDUP", "WAKEUP"):
            self.char_sitting = False
            self.char_lying   = False

        elif action == "SLEEP":
            pass  # posture unchanged; character stays sitting/lying

        # ── Cleaning ─────────────────────────────────────────────────────────
        elif action in ("WASH", "RINSE", "SCRUB", "WIPE"):
            s = self.object_states.setdefault(obj, set())
            s.add("CLEAN")
            s.discard("DIRTY")

        # All other actions (EAT, DRINK, READ, WATCH, TOUCH …) have no tracked
        # state change in the scene graph model.

    def _release(self, obj: str):
        """Free one hand holding obj."""
        obj = obj.lower()
        if self.hand_right == obj:
            self.hand_right = None
        elif self.hand_left == obj:
            self.hand_left = None

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
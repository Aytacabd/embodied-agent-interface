"""
object_state_model.py  —  ID-keyed version
===========================================
Per-INSTANCE state representation for SDA-Planner.

Previous versions merged state by class name ("light" -> union of states of
every light in the scene), which made precondition checks wrong whenever a
class had duplicate instances (e.g. light_245 OFF + light_246 ON merged to
{ON, OFF}, so both "on" and "off" checks passed).

This version keys everything by canonical instance token:

    "<class_name>_<id>"   e.g. "light_245"
    "character"           special token for the agent

Queries and mutations accept:
  - exact tokens      "light_245"  -> that instance only
  - plain class names "light"      -> resolved to all instances of the class
      * queries are optimistic: satisfied if ANY instance satisfies
        (same planning semantics as the old merged model)
      * state mutations apply to every instance; GRAB takes one instance
  - unknown strings   kept under their own key, so blank-model replays in
      error_diagnosis.find_t_source stay self-consistent
"""


import os as _os
import re as _re
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
# The real VirtualHome executor (execution.py, GrabExecutor.check_grabbable)
# hardcodes an exemption from the GRABBABLE-tag requirement for these two
# class names — they can be grabbed even without the tag. Mirrored here so
# the SDG model doesn't misdiagnose e.g. water as permanently ungrabbable.
_GRAB_CLASS_EXCEPTIONS = frozenset({"water", "child"})


class ObjectStateModel:
    """
    Tracks state per object INSTANCE, not per class name.

    object_states : { token -> set of VH state/property strings }
        e.g. {"fridge_2": {"CLOSED", "PLUGGED_IN", "CAN_OPEN"},
              "apple_3":  {"GRABBABLE"}}

    relations     : { (from_token, to_token) -> set of relation types }
        e.g. {("character", "apple_3"):  {"HOLDS_RH"},
              ("character", "fridge_2"): {"CLOSE"}}

    container_of  : { token -> container token }
        e.g. {"apple_3": "fridge_2"}  — apple_3 is inside fridge_2
    """

    def __init__(self):
        self.object_states: dict = {}   # token -> set of state strings
        self.object_states_by_id: dict = {}  # obj_id -> same set object (alias view)
        self.id_to_name: dict  = {}     # obj_id -> class_name
        self.name_to_ids: dict = {}     # class_name -> list of ids
        self.relations:   dict = {}     # (from_token, to_token) -> set of relation strings
        self.container_of: dict = {}    # token -> its direct container token
        self.hand_right:   str  = None  # token or None
        self.hand_left:    str  = None
        self.worn:         set  = set()  # tokens currently ON the character
        self.char_sitting: bool = False
        self.char_lying:   bool = False

    # ──────────────────────────────────────────────────────────────────────────
    # Token resolution
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _split_token(obj: str):
        """'light_245' -> ('light', 245); 'light' -> ('light', None)."""
        s = str(obj).strip().lower()
        m = _re.match(r"^(.+)_(\d+)$", s)
        if m:
            return m.group(1), int(m.group(2))
        return s, None

    def resolve(self, obj) -> list:
        """
        Return the list of canonical instance tokens obj refers to.

        "light_245"  -> ["light_245"]           (id known in scene)
        "light"      -> ["light_245", "light_246", ...]
        "character"  -> ["character"]
        unknown      -> [obj] fallback key, so applies/queries on names the
                        scene doesn't know remain self-consistent
        """
        s = (str(obj) if obj is not None else "").strip().lower()
        if not s:
            return []
        if s == "character":
            return ["character"]
        base, oid = self._split_token(s)
        if oid is not None:
            if oid in self.id_to_name:
                return [f"{self.id_to_name[oid]}_{oid}"]
            return [s]
        ids = self.name_to_ids.get(s)
        if ids:
            return [f"{s}_{i}" for i in ids]
        return [s]

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

        # ── Pass 1: nodes → per-instance object_states ────────────────────────
        for node in env_dict.get("nodes", []):
            name = node.get("class_name", "").lower().strip()
            nid  = node.get("id")
            if nid is not None:
                m.id_to_name[nid] = name
                m.name_to_ids.setdefault(name, []).append(nid)

            states = {s.upper() for s in node.get("states", [])}
            props  = {p.upper() for p in node.get("properties", [])}

            # ── Smart defaults based on object_states.json catalogue ──────────
            # Only apply defaults when the object type is known to support
            # the state — prevents false positives on unknown objects.
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

            if "GRABBABLE" in props or name in _CAN_GRAB or name in _GRAB_CLASS_EXCEPTIONS:
                props.add("GRABBABLE")

            combined = states | props

            if name == "character":
                token = "character"
                if "SITTING" in states:
                    m.char_sitting = True
                if "LYING" in states:
                    m.char_lying = True
            elif nid is not None:
                token = f"{name}_{nid}"
            else:
                token = name   # no id in dict — fall back to plain key

            # Same set object stored in both views so they never diverge
            m.object_states[token] = combined
            if nid is not None:
                m.object_states_by_id[nid] = combined

        # ── Pass 2: edges → relations + container_of (all per instance) ──────
        for edge in env_dict.get("edges", []):
            from_id = edge.get("from_id")
            to_id   = edge.get("to_id")
            rel     = edge.get("relation_type", "").upper()
            from_name = m.id_to_name.get(from_id, "")
            to_name   = m.id_to_name.get(to_id, "")
            if not from_name or not to_name:
                continue

            from_tok = "character" if from_name == "character" else f"{from_name}_{from_id}"
            to_tok   = "character" if to_name   == "character" else f"{to_name}_{to_id}"

            m.relations.setdefault((from_tok, to_tok), set()).add(rel)

            if rel == "INSIDE":
                # Only store as container if to_tok is an actual container
                # (has CAN_OPEN or CONTAINERS property). This prevents room
                # membership (dish_soap INSIDE dining_room) from being mistaken
                # for container access, which made the tree try to OPEN a room.
                to_states = m.object_states.get(to_tok, set())
                if "CAN_OPEN" in to_states or "CONTAINERS" in to_states:
                    m.container_of[from_tok] = to_tok

            if from_tok == "character":
                if rel == "HOLDS_RH":
                    m.hand_right = to_tok
                elif rel == "HOLDS_LH":
                    m.hand_left = to_tok

            # Worn items: obj ON character
            if to_tok == "character" and rel == "ON":
                m.worn.add(from_tok)

        return m

    def copy(self) -> "ObjectStateModel":
        new = ObjectStateModel()
        new.object_states = {k: set(v) for k, v in self.object_states.items()}
        new.id_to_name    = dict(self.id_to_name)
        new.name_to_ids   = {k: list(v) for k, v in self.name_to_ids.items()}
        # Rebuild the by-id alias view onto the new sets
        for nid, name in new.id_to_name.items():
            tok = "character" if name == "character" else f"{name}_{nid}"
            if tok in new.object_states:
                new.object_states_by_id[nid] = new.object_states[tok]
        new.relations     = {k: set(v) for k, v in self.relations.items()}
        new.container_of  = dict(self.container_of)
        new.hand_right    = self.hand_right
        new.hand_left     = self.hand_left
        new.worn          = set(self.worn)
        new.char_sitting  = self.char_sitting
        new.char_lying    = self.char_lying
        return new

    # ──────────────────────────────────────────────────────────────────────────
    # Primitive queries — optimistic over resolved instances
    # ──────────────────────────────────────────────────────────────────────────

    def has_state(self, obj: str, state: str) -> bool:
        state = state.upper()
        return any(
            state in self.object_states.get(tok, set())
            for tok in self.resolve(obj)
        )

    def has_relation(self, from_obj: str, to_obj: str, rel: str) -> bool:
        rel = rel.upper()
        from_toks = self.resolve(from_obj)
        to_toks   = self.resolve(to_obj)
        return any(
            rel in self.relations.get((f, t), set())
            for f in from_toks for t in to_toks
        )

    def is_next_to(self, obj: str) -> bool:
        return self.has_relation("character", obj, "CLOSE")

    def is_facing(self, obj: str) -> bool:
        return self.has_relation("character", obj, "FACING")

    def is_holding(self, obj: str) -> bool:
        held = {h for h in (self.hand_right, self.hand_left) if h}
        return any(tok in held for tok in self.resolve(obj))

    def hands_full(self) -> bool:
        return self.hand_right is not None and self.hand_left is not None

    def holding_anything(self) -> bool:
        return self.hand_right is not None or self.hand_left is not None

    def get_container(self, obj: str) -> str:
        """
        Return direct container TOKEN (e.g. "fridge_2") of obj, or None.
        With a plain class name, returns the container of the first instance
        that is inside one.
        """
        for tok in self.resolve(obj):
            container = self.container_of.get(tok)
            if container is not None:
                return container
        return None

    def container_is_open(self, obj: str) -> bool:
        """
        True if obj (any resolved instance) is not blocked by a closed
        container: no container, container not openable, or container OPEN.
        """
        for tok in self.resolve(obj):
            container = self.container_of.get(tok)
            if container is None:
                return True
            # Safety net: if container is not openable, treat as open
            if "CAN_OPEN" not in self.object_states.get(container, set()):
                return True
            if "OPEN" in self.object_states.get(container, set()):
                return True
        return False

    def target_accessible(self, target: str) -> bool:
        """
        Target container is open or not openable.
        PDDL put_inside: (not can_open) OR open
        """
        for tok in self.resolve(target):
            states = self.object_states.get(tok, set())
            if "CAN_OPEN" not in states:
                return True   # not a container — surface placement always OK
            if "OPEN" in states:
                return True
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Precondition checker (instance-aware)
    # ──────────────────────────────────────────────────────────────────────────

    def satisfies(self, precondition: str, obj: str,
                  target: str = None) -> bool:
        """
        Check a single precondition against actual per-instance states.
        obj    — primary object of the action ("name_id" or plain name)
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
        if precondition == "holding_anything":
            return self.holding_anything()
        if precondition == "on_char":
            return any(tok in self.worn for tok in self.resolve(obj))
        if precondition == "not_on_char":
            return not any(tok in self.worn for tok in self.resolve(obj))

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
        if precondition == "has_plug_or_has_switch":
            return self.has_state(obj, "HAS_PLUG") or self.has_state(obj, "HAS_SWITCH")
        if precondition == "pourable_or_drinkable":
            return self.has_state(obj, "POURABLE") or self.has_state(obj, "DRINKABLE")
        if precondition == "drinkable_or_recipient":
            return self.has_state(obj, "DRINKABLE") or self.has_state(obj, "RECIPIENT")
        if precondition == "target_is_recipient":
            return self.has_state(target, "RECIPIENT")
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
        if precondition == "person":
            return self.has_state(obj, "PERSON")
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
    # Mutators — apply action effects (per instance)
    # ──────────────────────────────────────────────────────────────────────────

    def apply(self, action: str, obj: str, target: str = None):
        """Update model state after executing action on obj (and target)."""
        action = action.upper()
        obj    = (obj    or "").lower().strip()
        target = (target or "").lower().strip()

        obj_toks = self.resolve(obj)

        # ── Navigation ───────────────────────────────────────────────────────
        if action in ("WALK", "RUN"):
            # PDDL walk_towards effect: become next_to obj, lose next_to
            # everything else. Clear ALL previous character CLOSE relations,
            # then add the new one(s). The executor also clears FACING on
            # navigation (WalkExecutor deletes FACING edges).
            for k in self.relations:
                if k[0] == "character":
                    self.relations[k].discard("CLOSE")
                    self.relations[k].discard("FACING")
            for tok in obj_toks:
                self.relations.setdefault(("character", tok), set()).add("CLOSE")

        elif action == "FIND":
            # FIND auto-navigates — FindExecutor delegates to WALK+FIND when
            # the character is far, and clears FACING edges either way.
            for k in self.relations:
                if k[0] == "character":
                    self.relations[k].discard("FACING")
            for tok in obj_toks:
                self.relations.setdefault(("character", tok), set()).add("CLOSE")

        elif action == "TURNTO":
            for tok in obj_toks:
                self.relations.setdefault(("character", tok), set()).add("FACING")

        elif action == "POINTAT":
            pass  # no state change

        # ── Grabbing / placing ───────────────────────────────────────────────
        elif action == "GRAB":
            # Grab ONE instance (prefer one not already held)
            held = {h for h in (self.hand_right, self.hand_left) if h}
            tok  = next((t for t in obj_toks if t not in held),
                        obj_toks[0] if obj_toks else None)
            if tok is not None:
                if self.hand_right is None:
                    self.hand_right = tok
                elif self.hand_left is None:
                    self.hand_left = tok
                # Object leaves its container when grabbed
                self.container_of.pop(tok, None)

        elif action in ("PUTBACK", "PUTOBJBACK"):
            released = self._release(obj)
            if target:
                tgt_toks = self.resolve(target)
                src = released or (obj_toks[0] if obj_toks else None)
                if src and tgt_toks:
                    self.relations.setdefault((src, tgt_toks[0]), set()).add("ON")

        elif action == "PUTIN":
            released = self._release(obj)
            if target:
                tgt_toks = self.resolve(target)
                src = released or (obj_toks[0] if obj_toks else None)
                if src and tgt_toks:
                    self.container_of[src] = tgt_toks[0]

        elif action == "PUTON":
            # Wear the item: leaves the hand, goes onto the character
            released = self._release(obj)
            tok = released or (obj_toks[0] if obj_toks else None)
            if tok is not None:
                self.worn.add(tok)

        elif action == "PUTOFF":
            # Remove worn item: comes off the character, back into a hand
            tok = next((t for t in obj_toks if t in self.worn),
                       obj_toks[0] if obj_toks else None)
            if tok is not None:
                self.worn.discard(tok)
                if self.hand_right is None:
                    self.hand_right = tok
                elif self.hand_left is None:
                    self.hand_left = tok

        elif action in ("DROP", "POUR", "RELEASE"):
            self._release(obj)

        # ── Containers ───────────────────────────────────────────────────────
        elif action == "OPEN":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("OPEN")
                s.discard("CLOSED")

        elif action == "CLOSE":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("CLOSED")
                s.discard("OPEN")

        # ── Appliances ───────────────────────────────────────────────────────
        elif action == "SWITCHON":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("ON")
                s.discard("OFF")

        elif action == "SWITCHOFF":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("OFF")
                s.discard("ON")

        elif action == "PLUGIN":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("PLUGGED_IN")
                s.discard("PLUGGED_OUT")

        elif action == "PLUGOUT":
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("PLUGGED_OUT")
                s.discard("PLUGGED_IN")

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
            for tok in obj_toks:
                s = self.object_states.setdefault(tok, set())
                s.add("CLEAN")
                s.discard("DIRTY")

        # All other actions (EAT, DRINK, READ, WATCH, TOUCH …) have no tracked
        # state change in the scene graph model.

    def _release(self, obj: str):
        """Free one hand holding (an instance of) obj. Returns released token."""
        for tok in self.resolve(obj):
            if self.hand_right == tok:
                self.hand_right = None
                return tok
            if self.hand_left == tok:
                self.hand_left = None
                return tok
        return None

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

    # Plain-name queries still work (unique instances)
    assert not m.satisfies("obj_not_inside_closed_container", "apple"), \
        "apple should be inaccessible inside closed fridge"

    m.apply("OPEN", "fridge")
    assert m.satisfies("obj_not_inside_closed_container", "apple"), \
        "apple should be accessible after opening fridge"

    m.apply("GRAB", "apple")
    assert m.satisfies("holds_obj", "apple"), "should hold apple"
    assert m.satisfies("holds_obj", "apple_3"), "should hold apple_3 by token"
    assert m.get_container("apple") is None, "apple should leave fridge on grab"

    # ── ID-keyed: duplicate instances stay distinct ──────────────────────────
    env2 = {
        "nodes": [
            {"id": 1,   "class_name": "character", "states": [], "properties": []},
            {"id": 245, "class_name": "light", "states": ["OFF"],
             "properties": ["HAS_SWITCH"]},
            {"id": 246, "class_name": "light", "states": ["ON"],
             "properties": ["HAS_SWITCH"]},
        ],
        "edges": [
            {"from_id": 1, "to_id": 245, "relation_type": "CLOSE"},
        ],
    }
    m2 = ObjectStateModel.from_env_dict(env2)

    assert m2.satisfies("off", "light_245"), "light_245 is OFF"
    assert not m2.satisfies("on", "light_245"), "light_245 must NOT read as ON"
    assert m2.satisfies("on", "light_246"), "light_246 is ON"
    assert not m2.satisfies("off", "light_246"), "light_246 must NOT read as OFF"
    # Plain name stays optimistic (any instance)
    assert m2.satisfies("off", "light") and m2.satisfies("on", "light")
    # Spatial is per instance too
    assert m2.satisfies("next_to_obj", "light_245")
    assert not m2.satisfies("next_to_obj", "light_246")

    m2.apply("SWITCHON", "light_245")
    assert m2.satisfies("on", "light_245")
    assert m2.satisfies("on", "light_246"), "light_246 untouched"

    # Duplicate containers: apple_10 in fridge_2, apple_11 on nothing
    env3 = {
        "nodes": [
            {"id": 1,  "class_name": "character", "states": [], "properties": []},
            {"id": 2,  "class_name": "fridge", "states": ["CLOSED"], "properties": ["CAN_OPEN"]},
            {"id": 10, "class_name": "apple", "states": [], "properties": ["GRABBABLE"]},
            {"id": 11, "class_name": "apple", "states": [], "properties": ["GRABBABLE"]},
        ],
        "edges": [
            {"from_id": 10, "to_id": 2, "relation_type": "INSIDE"},
        ],
    }
    m3 = ObjectStateModel.from_env_dict(env3)
    assert not m3.satisfies("obj_not_inside_closed_container", "apple_10")
    assert m3.satisfies("obj_not_inside_closed_container", "apple_11")
    assert m3.get_container("apple_10") == "fridge_2", "container token carries the id"
    assert m3.get_container("apple_11") is None

    print("All assertions passed ✅")

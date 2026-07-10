"""
State Dependency Graph (SDG) for VirtualHome
Based on the uploaded VirtualHome PDDL, extended to cover the 42-action VirtualHome script vocabulary.

Key corrections from PDDL:
- SWITCHON requires plugged_in (PDDL line 219)
- FIND requires next_to and has no effect in the uploaded PDDL.
- TYPE requires has_switch (PDDL line 383)
- WATCH requires facing (PDDL line 391)
- READ requires readable + holds_obj (PDDL line 307)
- WAKEUP requires sitting_or_lying (PDDL line 483)
- CLOSE effect adds not_on (PDDL line 151)
- MOVE/PUSH/PULL require movable (PDDL line 399)
- SQUEEZE requires clothes (PDDL line 420)
- CUT requires eatable + cuttable (PDDL line 458)
- EAT requires eatable (PDDL line 469)
- WIPE requires next_to surface + holding a wiping tool.
"""

SDG = {

    # ── Navigation ───────────────────────────────────────────────────────────
    # PDDL walk_towards: not(sitting) and not(lying) → next_to
    # PDDL walk_into:    not(sitting) and not(lying) → inside(char, room)
    "WALK": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj", "inside_room"],
        "is_prep": True,
    },
    "RUN": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj", "inside_room"],
        "is_prep": True,
    },
    # Executor FindExecutor auto-walks to object if not close (JoinedExecutor: WalkExecutor + _FindExecutor).
    # Walk requires not_sitting/not_lying; _FindExecutor adds CLOSE relation (= next_to_obj).
    "FIND": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj"],
        "is_prep": True,
    },
    # PDDL turn_to: no precondition
    "TURNTO": {
        "needs":   [],
        "effects": ["facing_obj"],
        "is_prep": True,
    },
    # executor PointAtExecutor = LookAtExecutor — only checks facing, same as LOOKAT
    "POINTAT": {
        "needs":   ["facing_obj"],
        "effects": [],
        "is_prep": False,
    },

    # ── Object interaction ────────────────────────────────────────────────────
    # PDDL grab: grabbable + next_to + not inside closed + not both hands full
    "GRAB": {
        "needs":   [
            "next_to_obj",
            "grabbable",
            "not_both_hands_full",
            "obj_not_inside_closed_container",
        ],
        "effects": ["holds_obj"],
        "is_prep": False,
    },
    # PDDL put_on (2 objects): holds + next_to target; effects: obj_ontop + obj_next_to + not_holds
    "PUTBACK": {
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["not_holds_obj", "obj_ontop_target", "obj_next_to_target"],
        "is_prep": False,
    },
    # PDDL put_inside: holds + next_to + (not can_open OR open)
    "PUTIN": {
        "needs":   [
            "holds_obj",
            "next_to_target",
            "target_open_or_not_openable",
        ],
        "effects": ["not_holds_obj", "obj_inside_target"],
        "is_prep": False,
    },
    "PUTOBJBACK": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    # executor put_on_character: holds_obj + clothes (executor adds CLOTHES check)
    "PUTON": {
        "needs":   ["holds_obj", "clothes"],
        "effects": ["not_holds_obj", "on_char"],
        "is_prep": False,
    },
    # executor drop: only checks holds — no room check despite PDDL requiring it
    "DROP": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },

    # executor put_off: obj ON char + CLOTHES (no PDDL equivalent)
    "PUTOFF": {
        "needs":   ["on_char", "clothes"],
        "effects": ["not_on_char"],
        "is_prep": False,
    },
    # PDDL pour: (pourable OR drinkable) + holds + recipient(target) + next_to
    "POUR": {
        "needs":   ["holds_obj", "pourable_or_drinkable", "next_to_target", "target_is_recipient"],
        "effects": ["obj_inside_target"],
        "is_prep": False,
    },
    # executor move: movable + next_to + not inside closed + free hand required
    "MOVE": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    "PUSH": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    "PULL": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    # executor greet: checks PERSON property — fails with AFFORDANCE_ERROR if not a person
    "GREET": {
        "needs":   ["person"],
        "effects": [],
        "is_prep": False,
    },

    # ── Container interaction ─────────────────────────────────────────────────
    # executor open: can_open + closed + next_to + not(on) + free hand required
    "OPEN": {
        "needs":   ["next_to_obj", "can_open", "closed", "not_on", "not_both_hands_full"],
        "effects": ["open", "not_closed"],
        "is_prep": False,
    },
    # executor close: can_open + open + next_to; effect: closed only — does NOT turn off
    "CLOSE": {
        "needs":   ["next_to_obj", "can_open", "open"],
        "effects": ["closed"],
        "is_prep": False,
    },

    # ── Appliance interaction ─────────────────────────────────────────────────
    # PDDL switch_on: has_switch + off + plugged_in + next_to
    "SWITCHON": {
        "needs":   ["next_to_obj", "has_switch", "off", "plugged_in"],
        "effects": ["on", "not_off"],
        "is_prep": False,
    },
    # PDDL switch_off: has_switch + on + next_to
    "SWITCHOFF": {
        "needs":   ["next_to_obj", "has_switch", "on"],
        "effects": ["off", "not_on"],
        "is_prep": False,
    },
    # ── Character posture ─────────────────────────────────────────────────────
    # PDDL sit: next_to + sittable + not(sitting). Effect: sitting only (not_lying is NOT in PDDL)
    # executor SitExecutor discards LYING before adding SITTING
    "SIT": {
        "needs":   ["next_to_obj", "sittable", "not_sitting"],
        "effects": ["sitting", "ontop_obj", "not_lying"],
        "is_prep": False,
    },
    # PDDL standup: sitting OR lying
    "STANDUP": {
        "needs":   ["sitting_or_lying"],
        "effects": ["not_sitting", "not_lying"],
        "is_prep": False,
    },
    # PDDL lie: lieable + next_to + not(lying)
    "LIE": {
        "needs": ["next_to_obj", "lieable", "not_lying"],
        "effects": ["lying", "ontop_obj", "not_sitting"],
        "is_prep": False,
    },
    # PDDL sleep: sitting OR lying
    "SLEEP": {
        "needs":   ["sitting_or_lying"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL wake_up: sitting OR lying — effect is EMPTY in PDDL
    # (EAI handles posture reset internally after wakeup)
    "WAKEUP": {
        "needs":   ["sitting_or_lying"],
        "effects": [],
        "is_prep": False,
    },

    # ── Cleaning ──────────────────────────────────────────────────────────────
    # PDDL wash: next_to
    "WASH": {
        "needs":   ["next_to_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    "RINSE": {
        "needs":   ["next_to_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    "SCRUB": {
        "needs":   ["next_to_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    # executor wipe: next_to surface + hold ANY object (wiping tool — not the surface itself)
    "WIPE": {
        "needs":   ["next_to_obj", "holds_any_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    # executor squeeze: next_to + free hand + (CLOTHES OR sponge/rag/towel/soap/etc.)
    "SQUEEZE": {
        "needs":   ["next_to_obj", "clothes_or_squeezable", "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    # executor cut: next_to + eatable + cuttable + free hand + holding a knife
    # holds_knife is satisfied by any held object whose class name contains "knife"
    "CUT": {
        "needs":   ["next_to_obj", "eatable", "cuttable", "not_both_hands_full", "holds_knife"],
        "effects": [],
        "is_prep": False,
    },

    # ── Consumption / interaction ─────────────────────────────────────────────
    # PDDL drink: (drinkable OR recipient) + holds_obj
    "DRINK": {
        "needs":   ["holds_obj", "drinkable_or_recipient"],
        "effects": [],
        "is_prep": False,
    },
    # executor eat: next_to + (EATABLE OR something with EATABLE on the obj — e.g. plate with food)
    "EAT": {
        "needs":   ["next_to_obj", "eatable_or_has_eatable_on"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL read: readable + holds_obj
    "READ": {
        "needs":   ["holds_obj", "readable"],
        "effects": [],
        "is_prep": False,
    },
    # executor touch: only close_to + not_inside_closed (readable/holds NOT enforced)
    "TOUCH": {
        "needs":   ["next_to_obj", "obj_not_inside_closed_container"],
        "effects": [],
        "is_prep": False,
    },
    # executor watch: lookable + facing + same room + not inside closed container (lines 1778-1784)
    # same_room is not yet tracked by ObjectStateModel (unknown predicates satisfy → True),
    # so it documents intent without changing repair behaviour; WALK handles room navigation.
    "WATCH": {
        "needs":   ["lookable", "facing_obj", "obj_not_inside_closed_container", "same_room"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL look_at: facing
    "LOOKAT": {
        "needs":   ["facing_obj"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL type: has_switch + next_to
    "TYPE": {
        "needs":   ["next_to_obj", "has_switch"],
        "effects": [],
        "is_prep": False,
    },

    # executor PlugExecutor: HAS_PLUG + close + free hand + currently PLUGGED_OUT
    "PLUGIN": {
        "needs":   ["next_to_obj", "has_plug", "not_both_hands_full", "plugged_out"],
        "effects": ["plugged_in", "not_plugged_out"],
        "is_prep": False,
    },
    # executor PlugExecutor: HAS_PLUG + close + free hand + currently PLUGGED_IN
    # (ON check logs a warning but does NOT return False — not a hard precondition)
    "PLUGOUT": {
        "needs":   ["next_to_obj", "has_plug", "not_both_hands_full", "plugged_in"],
        "effects": ["plugged_out", "not_plugged_in"],
        "is_prep": False,
    },
    # executor RELEASE maps to DropExecutor — same as DROP
    "RELEASE": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Human-readable explanations for LLM feedback prompts
# ─────────────────────────────────────────────────────────────────────────────

PRECONDITION_EXPLANATIONS = {
    "next_to_obj":                     "The character must be next to the object — use WALK first.",
    "next_to_target":                  "The character must be next to the target — use WALK first.",
    "facing_obj":                      "The character must be facing the object — use TURNTO first.",
    "grabbable":                       "The object must be grabbable.",
    "not_both_hands_full":             "Both hands are full — use PUTBACK or DROP first.",
    "holds_obj":                       "The character must be holding the object — use GRAB first.",
    "holds_any_obj":                   "The character must be holding an object to wipe with (any held object works, e.g. a sponge, rag, or towel) — GRAB one first.",
    "holds_knife":                     "The character must be holding a knife to CUT — WALK to a knife and GRAB it first.",
    "clothes_or_squeezable":           "The object must be clothes, a sponge, rag, towel, soap, paper, or similar squeezable item.",
    "eatable_or_has_eatable_on":       "The object must be eatable, or have an eatable item on it.",
    "obj_not_inside_closed_container": "The object is inside a closed container — use OPEN first.",
    "target_open_or_not_openable":     "The target container must be open — use OPEN first.",
    "can_open":                        "The object must be openable.",
    "closed":                          "The object must be closed.",
    "open":                            "The object must be open.",
    "not_on":                          "The object must be switched off before opening.",
    "has_switch":                      "The object must have a switch.",
    "off":                             "The object must be off — use SWITCHOFF first.",
    "on":                              "The object must be on — use SWITCHON first.",
    "plugged_in":                      "The object must be PLUGGED_IN. SWITCHON cannot proceed if the device is PLUGGED_OUT.",
    "plugged_out":                     "The object must be PLUGGED_OUT — use PLUGIN to connect it first.",
    "not_plugged_out":                 "The object must not be PLUGGED_OUT.",
    "not_plugged_in":                  "The object must not be PLUGGED_IN.",
    "not_sitting":                     "The character is sitting — use STANDUP first.",
    "not_lying":                       "The character is lying — use STANDUP first.",
    "sitting_or_lying":                "The character must be sitting or lying first.",
    "not_holds_obj":                   "The character must not be holding the object.",
    "sittable":                        "The object must be sittable.",
    "lieable":                         "The object must be lieable.",
    "movable":                         "The object must be movable.",
    "readable":                        "The object must be readable.",
    "eatable":                         "The object must be eatable.",
    "cuttable":                        "The object must be cuttable.",
    "clothes":                         "The object must be clothes.",
    "lookable":                        "The object must be lookable.",
    "pourable":                        "The object must be pourable.",
    "drinkable":                       "The object must be drinkable.",
    "hangable":                        "The object must be hangable.",
    "has_plug":                        "The object must have a plug.",
    "person":                          "The target must be a person.",
    "obj_inside_room":                 "The object must be inside the current room.",
    "pourable_or_drinkable":           "The source object must be pourable or drinkable.",
    "target_is_recipient":             "The target must be a recipient (container for liquids).",
    "drinkable_or_recipient":          "The object must be drinkable or a recipient.",
    "has_plug_or_has_switch":          "The object must have a plug or a switch to be plugged in.",
    "same_room":                       "The character must be in the same room as the object — use WALK first.",
    "inside_room":                          "The character must be inside the room.",
    "on_char":                              "The object must currently be on the character.",
"not_on_char": "The object is no longer on the character.",
}


def get_preconditions(action: str) -> list:
    """Return Sdep[a] — preconditions for action a (paper Section 4.2)."""
    return SDG.get(action.upper(), {}).get("needs", [])


def get_effects(action: str) -> list:
    """Return Seff[a] — effects for action a (paper Section 4.2)."""
    return SDG.get(action.upper(), {}).get("effects", [])


def is_prep_action(action: str) -> bool:
    """True if action is a state preparation action (paper Section 4.2)."""
    return SDG.get(action.upper(), {}).get("is_prep", False)


def explain_precondition(precondition: str) -> str:
    """Human-readable explanation for LLM prompts."""
    return PRECONDITION_EXPLANATIONS.get(
        precondition,
        f"Precondition '{precondition}' must be satisfied."
    )


if __name__ == "__main__":
    print("=== SDG Verification against PDDL ===")
    checks = [
        ("WALK",      ["not_sitting", "not_lying"]),
        ("FIND", ["not_sitting", "not_lying"]),
        ("GRAB",      ["next_to_obj", "grabbable", "not_both_hands_full",
                       "obj_not_inside_closed_container"]),
        ("OPEN",      ["next_to_obj", "can_open", "closed", "not_on", "not_both_hands_full"]),
        ("SWITCHON",  ["next_to_obj", "has_switch", "off", "plugged_in"]),
        ("SWITCHOFF", ["next_to_obj", "has_switch", "on"]),
        ("STANDUP",   ["sitting_or_lying"]),
        ("TYPE",      ["next_to_obj", "has_switch"]),
        ("WATCH",     ["lookable", "facing_obj", "obj_not_inside_closed_container", "same_room"]),
        ("READ",      ["holds_obj", "readable"]),
        ("WAKEUP",    ["sitting_or_lying"]),
        ("CLOSE",     ["next_to_obj", "can_open", "open"]),
        ("PUTBACK",   ["holds_obj", "next_to_target"]),
        ("PUTIN",     ["holds_obj", "next_to_target", "target_open_or_not_openable"]),
        ("DROP",      ["holds_obj"]),
        ("DRINK",     ["holds_obj", "drinkable_or_recipient"]),
        ("POUR",      ["holds_obj", "pourable_or_drinkable", "next_to_target", "target_is_recipient"]),
        ("TOUCH",     ["next_to_obj", "obj_not_inside_closed_container"]),
        ("MOVE",      ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"]),
        ("PUSH",      ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"]),
        ("PULL",      ["next_to_obj", "movable", "obj_not_inside_closed_container", "not_both_hands_full"]),
        ("SIT",       ["next_to_obj", "sittable", "not_sitting"]),
        ("LIE",       ["next_to_obj", "lieable", "not_lying"]),
        ("WIPE",      ["next_to_obj", "holds_any_obj"]),
        ("SQUEEZE",   ["next_to_obj", "clothes_or_squeezable", "not_both_hands_full"]),
        ("CUT",       ["next_to_obj", "eatable", "cuttable", "not_both_hands_full", "holds_knife"]),
        ("PUTON",     ["holds_obj", "clothes"]),
        ("PUTOFF",    ["on_char", "clothes"]),
        ("GREET",     ["person"]),
    ]
    all_ok = True
    for action, expected in checks:
        actual = get_preconditions(action)
        ok     = set(actual) == set(expected)
        if not ok:
            all_ok = False
        print(f"{'✅' if ok else '❌'} {action}: {actual}")
    print("\n✅ All checks passed!" if all_ok else "\n❌ Some checks failed!")
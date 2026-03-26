"""
State Dependency Graph (SDG) for VirtualHome
Based on the ACTUAL VirtualHome PDDL (virtualhome.pddl)
Section 4.2 of SDA-Planner paper.

Key corrections from PDDL:
- SWITCHON requires plugged_in (PDDL line 219)
- FIND requires next_to (PDDL line 86)
- TYPE requires has_switch (PDDL line 383)
- WATCH requires facing (PDDL line 391)
- READ requires readable + holds_obj (PDDL line 307)
- WAKEUP requires sitting_or_lying (PDDL line 483)
- CLOSE effect adds not_on (PDDL line 151)
- MOVE/PUSH/PULL require movable (PDDL line 399)
- SQUEEZE requires clothes (PDDL line 420)
- CUT requires eatable + cuttable (PDDL line 458)
- EAT requires eatable (PDDL line 469)
- WIPE requires holds_obj (PDDL line 275)
"""

SDG = {

    # ── Navigation ───────────────────────────────────────────────────────────
    # PDDL: not(sitting) and not(lying)
    "WALK": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj"],
        "is_prep": True,
    },
    "RUN": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj"],
        "is_prep": True,
    },
    # PDDL find: precondition (next_to ?char ?obj)
    "FIND": {
        "needs":   [],              # navigates automatically, no prior state needed
        "effects": ["next_to_obj"],
        "is_prep": True,
    },
    # PDDL turn_to: no precondition
    "TURNTO": {
        "needs":   [],
        "effects": ["facing_obj"],
        "is_prep": True,
    },
    "POINTAT": {
        "needs":   ["next_to_obj"],
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
    # PDDL put_on (2 objects): holds + next_to target
    "PUTBACK": {
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["not_holds_obj", "obj_ontop_target"],
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
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    # PDDL put_on_character: holds_obj only (putting on self)
    "PUTON": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    "PUTOFF": {
        "needs":   [],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    # PDDL drop: holds_obj
    "DROP": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    # PDDL pour: (pourable OR drinkable) + holds + recipient + next_to
    "POUR": {
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["obj_inside_target"],
        "is_prep": False,
    },
    # PDDL move: movable + next_to + not inside closed
    "MOVE": {
        "needs":   ["next_to_obj", "movable"],
        "effects": [],
        "is_prep": False,
    },
    "PUSH": {
        "needs":   ["next_to_obj", "movable"],
        "effects": [],
        "is_prep": False,
    },
    "PULL": {
        "needs":   ["next_to_obj", "movable"],
        "effects": [],
        "is_prep": False,
    },
    "GREET": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },

    # ── Container interaction ─────────────────────────────────────────────────
    # PDDL open: can_open + closed + next_to + not(on)
    "OPEN": {
        "needs":   ["next_to_obj", "can_open", "closed", "not_on"],
        "effects": ["open", "not_closed"],
        "is_prep": False,
    },
    # PDDL close: can_open + open + next_to; effect closed + not(on)
    "CLOSE": {
        "needs":   ["next_to_obj", "can_open", "open"],
        "effects": ["closed", "not_open", "not_on"],
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
    # PDDL sit: next_to + sittable + not(sitting)
    "SIT": {
        "needs":   ["next_to_obj", "sittable", "not_sitting"],
        "effects": ["sitting", "not_lying"],
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
        "needs":   ["next_to_obj", "lieable", "not_lying"],
        "effects": ["lying", "not_sitting"],
        "is_prep": False,
    },
    # PDDL sleep: sitting OR lying
    "SLEEP": {
        "needs":   ["sitting_or_lying"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL wake_up: sitting OR lying
    "WAKEUP": {
        "needs":   ["sitting_or_lying"],
        "effects": ["not_sitting", "not_lying"],
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
    # PDDL wipe: next_to obj1 + holds obj2 (wiping cloth needed)
    "WIPE": {
        "needs":   ["next_to_obj", "holds_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    # PDDL squeeze: next_to + clothes
    "SQUEEZE": {
        "needs":   ["next_to_obj", "clothes"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL cut: next_to + eatable + cuttable
    "CUT": {
        "needs":   ["next_to_obj", "eatable", "cuttable"],
        "effects": [],
        "is_prep": False,
    },

    # ── Consumption / interaction ─────────────────────────────────────────────
    # PDDL drink: (drinkable OR recipient) + holds_obj
    "DRINK": {
        "needs":   ["holds_obj"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL eat: next_to + eatable
    "EAT": {
        "needs":   ["next_to_obj", "eatable"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL read: readable + holds_obj
    "READ": {
        "needs":   ["holds_obj", "readable"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL touch: next_to (simplified — PDDL uses readable+holds but EAI uses next_to)
    "TOUCH": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL watch: lookable + facing + not inside closed
    "WATCH": {
        "needs":   ["next_to_obj", "facing_obj"],
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
    "obj_not_inside_closed_container": "The object is inside a closed container — use OPEN first.",
    "target_open_or_not_openable":     "The target container must be open — use OPEN first.",
    "can_open":                        "The object must be openable.",
    "closed":                          "The object must be closed.",
    "open":                            "The object must be open.",
    "not_on":                          "The object must be switched off before opening.",
    "has_switch":                      "The object must have a switch.",
    "off":                             "The object must be off — use SWITCHOFF first.",
    "on":                              "The object must be on — use SWITCHON first.",
    "plugged_in":                      "The object must be plugged in — use PLUGIN first.",
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
    "facing_obj":                      "The character must be facing the object — use TURNTO first.",
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
        ("WALK",     ["not_sitting", "not_lying"]),
        ("FIND",     ["next_to_obj"]),
        ("GRAB",     ["next_to_obj", "grabbable", "not_both_hands_full",
                      "obj_not_inside_closed_container"]),
        ("OPEN",     ["next_to_obj", "can_open", "closed", "not_on"]),
        ("SWITCHON", ["next_to_obj", "has_switch", "off", "plugged_in"]),
        ("SWITCHOFF",["next_to_obj", "has_switch", "on"]),
        ("STANDUP",  ["sitting_or_lying"]),
        ("TYPE",     ["next_to_obj", "has_switch"]),
        ("WATCH",    ["next_to_obj", "facing_obj"]),
        ("READ",     ["holds_obj", "readable"]),
        ("WAKEUP",   ["sitting_or_lying"]),
        ("CLOSE",    ["next_to_obj", "can_open", "open"]),
    ]
    for action, expected in checks:
        actual = get_preconditions(action)
        ok     = set(actual) == set(expected)
        print(f"{'✅' if ok else '❌'} {action}: {actual}")

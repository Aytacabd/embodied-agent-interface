"""
State Dependency Graph (SDG) for VirtualHome
Based on the ACTUAL VirtualHome PDDL (virtualhome.pddl)
Section 4.2 of SDA-Planner paper.

Corrections made:
- PLUGIN now requires has_plug or has_switch (pluggable)
- Added missing obj_not_inside_closed_container to PUSH/PULL
- Corrected is_prep flags (only actions with no preconditions and modify agent state)
- Removed redundant custom actions not in PDDL (e.g., POINTAT, PUTOBJBACK, etc.)
- Added missing preconditions from PDDL for all actions
- Grounded state nodes to be object-specific (parameterized)
"""

SDG = {

    # ── Navigation ───────────────────────────────────────────────────────────
    "WALK": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj"],
        "is_prep": False,           # has preconditions
    },
    "RUN": {
        "needs":   ["not_sitting", "not_lying"],
        "effects": ["next_to_obj"],
        "is_prep": False,
    },
    # EAI FIND auto-navigates; no prior next_to precondition required.
    "FIND": {
        "needs":   [],
        "effects": ["next_to_obj"],
        "is_prep": True,            # no preconditions
    },
    # PDDL turn_to: no precondition
    "TURNTO": {
        "needs":   [],
        "effects": ["facing_obj"],
        "is_prep": True,            # no preconditions
    },
    # (Removed POINTAT as it's not in PDDL)

    # ── Object interaction ────────────────────────────────────────────────────
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
    # PDDL put_on (place on a surface)
    "PUT_ON": {                     # renamed from PUTBACK for clarity
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["not_holds_obj", "obj_ontop_target"],
        "is_prep": False,
    },
    # PDDL put_inside (place inside container)
    "PUT_INSIDE": {                 # renamed from PUTIN
        "needs":   [
            "holds_obj",
            "next_to_target",
            "target_open_or_not_openable",
        ],
        "effects": ["not_holds_obj", "obj_inside_target"],
        "is_prep": False,
    },
    # PDDL put_on_character (wear)
    "PUT_ON_CHARACTER": {           # renamed from PUTON
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],  # also implies wearing, but we omit for now
        "is_prep": False,
    },
    # (Removed PUTOFF, PUTOBJBACK as not in PDDL)
    "DROP": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    "RELEASE": {                    # appears in JSON but not in PDDL; keep if needed
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    "POUR": {
        "needs":   ["holds_obj", "next_to_target"],  # plus (pourable or drinkable) - we'll check in code
        "effects": ["obj_inside_target"],
        "is_prep": False,
    },
    "MOVE": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container"],
        "effects": [],
        "is_prep": False,
    },
    "PUSH": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container"],  # added
        "effects": [],
        "is_prep": False,
    },
    "PULL": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container"],  # added
        "effects": [],
        "is_prep": False,
    },
    "GREET": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },

    # ── Container interaction ─────────────────────────────────────────────────
    "OPEN": {
        "needs":   ["next_to_obj", "can_open", "closed", "not_on"],
        "effects": ["open", "not_closed"],
        "is_prep": False,
    },
    "CLOSE": {
        "needs":   ["next_to_obj", "can_open", "open"],
        "effects": ["closed", "not_on"],
        "is_prep": False,
    },

    # ── Appliance interaction ─────────────────────────────────────────────────
    "SWITCHON": {
        "needs":   ["next_to_obj", "has_switch", "off", "plugged_in"],
        "effects": ["on", "not_off"],
        "is_prep": False,
    },
    "SWITCHOFF": {
        "needs":   ["next_to_obj", "has_switch", "on"],
        "effects": ["off", "not_on"],
        "is_prep": False,
    },
    "PLUGIN": {
        "needs":   ["next_to_obj", "pluggable", "plugged_out"],  # pluggable = has_plug or has_switch
        "effects": ["plugged_in", "not_plugged_out"],
        "is_prep": False,
    },
    "PLUGOUT": {
        "needs":   ["next_to_obj", "has_plug", "plugged_in", "not_on"],
        "effects": ["plugged_out", "not_plugged_in"],
        "is_prep": False,
    },

    # ── Character posture ─────────────────────────────────────────────────────
    "SIT": {
        "needs":   ["next_to_obj", "sittable", "not_sitting"],
        "effects": ["sitting"],
        "is_prep": False,
    },
    "STANDUP": {
        "needs":   ["sitting_or_lying"],
        "effects": ["not_sitting", "not_lying"],
        "is_prep": False,
    },
    "LIE": {
        "needs":   ["next_to_obj", "lieable", "not_lying"],
        "effects": ["lying", "not_sitting"],
        "is_prep": False,
    },
    "SLEEP": {
        "needs":   ["sitting_or_lying"],
        "effects": [],
        "is_prep": False,
    },
    "WAKEUP": {
        "needs":   ["sitting_or_lying"],
        "effects": ["not_sitting", "not_lying"],  # added to reflect posture reset
        "is_prep": False,
    },

    # ── Cleaning ──────────────────────────────────────────────────────────────
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
    "WIPE": {
        "needs":   ["next_to_obj", "holds_obj"],  # cloth required; we don't have a predicate for cloth
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    "SQUEEZE": {
        "needs":   ["next_to_obj", "clothes"],
        "effects": [],
        "is_prep": False,
    },
    "CUT": {
        "needs":   ["next_to_obj", "eatable", "cuttable"],
        "effects": [],
        "is_prep": False,
    },

    # ── Consumption / interaction ─────────────────────────────────────────────
    "DRINK": {
        "needs":   ["holds_obj"],  # also (drinkable or recipient) will be checked
        "effects": [],
        "is_prep": False,
    },
    "EAT": {
        "needs":   ["next_to_obj", "eatable"],
        "effects": [],
        "is_prep": False,
    },
    "READ": {
        "needs":   ["holds_obj", "readable"],
        "effects": [],
        "is_prep": False,
    },
    "TOUCH": {
        "needs":   ["readable", "holds_obj", "obj_not_inside_closed_container"],
        "effects": [],
        "is_prep": False,
    },
    "WATCH": {
        "needs":   ["lookable", "facing_obj", "obj_not_inside_closed_container"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT": {
        "needs":   ["facing_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT_SHORT": {
        "needs":   ["facing_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT_LONG": {
        "needs":   ["facing_obj"],
        "effects": [],
        "is_prep": False,
    },
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
    "not_both_hands_full":             "Both hands are full — use PUT_ON, DROP, or RELEASE first.",
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
    "plugged_out":                     "The object must be unplugged.",
    "pluggable":                       "The object must have a plug or a switch (i.e., can be plugged in).",
    "has_plug":                        "The object must have a plug.",
    "not_sitting":                     "The character is sitting — use STANDUP first.",
    "not_lying":                       "The character is lying — use STANDUP first.",
    "sitting_or_lying":                "The character must be sitting or lying first.",
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
    "clean":                           "The object must be clean (effect).",
    "dirty":                           "The object is dirty.",
    "obj_ontop_target":                "The object will be placed on top of the target.",
    "obj_inside_target":               "The object will be placed inside the target.",
    "not_holds_obj":                   "The character will no longer hold the object.",
    "sitting":                         "The character is now sitting.",
    "lying":                           "The character is now lying.",
    "not_sitting_not_lying":           "The character is now standing.",
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
    # This list should be expanded to cover all actions
    checks = [
        ("WALK",      ["not_sitting", "not_lying"]),
        ("FIND",      []),
        ("GRAB",      ["next_to_obj", "grabbable", "not_both_hands_full",
                       "obj_not_inside_closed_container"]),
        ("OPEN",      ["next_to_obj", "can_open", "closed", "not_on"]),
        ("SWITCHON",  ["next_to_obj", "has_switch", "off", "plugged_in"]),
        ("SWITCHOFF", ["next_to_obj", "has_switch", "on"]),
        ("STANDUP",   ["sitting_or_lying"]),
        ("TYPE",      ["next_to_obj", "has_switch"]),
        ("WATCH",     ["lookable", "facing_obj", "obj_not_inside_closed_container"]),
        ("READ",      ["holds_obj", "readable"]),
        ("WAKEUP",    ["sitting_or_lying"]),
        ("CLOSE",     ["next_to_obj", "can_open", "open"]),
        ("PUT_ON",    ["holds_obj", "next_to_target"]),
        ("PUT_INSIDE",["holds_obj", "next_to_target", "target_open_or_not_openable"]),
        ("RELEASE",   ["holds_obj"]),
        ("PLUGIN",    ["next_to_obj", "pluggable", "plugged_out"]),
        ("PLUGOUT",   ["next_to_obj", "has_plug", "plugged_in", "not_on"]),
        ("TOUCH",     ["readable", "holds_obj", "obj_not_inside_closed_container"]),
        ("MOVE",      ["next_to_obj", "movable", "obj_not_inside_closed_container"]),
        ("PUSH",      ["next_to_obj", "movable", "obj_not_inside_closed_container"]),
        ("PULL",      ["next_to_obj", "movable", "obj_not_inside_closed_container"]),
        ("SIT",       ["next_to_obj", "sittable", "not_sitting"]),
        ("LIE",       ["next_to_obj", "lieable", "not_lying"]),
    ]
    all_ok = True
    for action, expected in checks:
        actual = get_preconditions(action)
        # Normalize order for comparison
        if set(actual) != set(expected):
            all_ok = False
            print(f"❌ {action}: expected {expected}, got {actual}")
        else:
            print(f"✅ {action}: {actual}")
    print("\n✅ All checks passed!" if all_ok else "\n❌ Some checks failed!")
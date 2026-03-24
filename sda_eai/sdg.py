"""
version 1.0
State Dependency Graph (SDG) for VirtualHome
Based on the actual VirtualHome PDDL action definitions.
Action names match EAI's valid_actions exactly.

For each action we define:
  - needs:    preconditions that must be satisfied before execution
  - effects:  state changes after successful execution
  - is_prep:  whether this is a "state preparation" action (no incoming deps)

NOTE: In VirtualHome, all devices are plugged_in by default.
So SWITCHON does NOT require plugged_in as a precondition.
"""

SDG = {

    # ── Navigation ──────────────────────────────────────────────────────────
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
    "FIND": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
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

    # ── Object interaction ───────────────────────────────────────────────────
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
    "PUTBACK": {
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["not_holds_obj", "obj_ontop_target"],
        "is_prep": False,
    },
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
    "DROP": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    "POUR": {
        "needs":   ["holds_obj", "next_to_target"],
        "effects": ["obj_inside_target"],
        "is_prep": False,
    },
    "MOVE": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "PUSH": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "PULL": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "GREET": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },

    # ── Container interaction ────────────────────────────────────────────────
    "OPEN": {
        "needs":   ["next_to_obj", "can_open", "closed", "not_on"],
        "effects": ["open", "not_closed"],
        "is_prep": False,
    },
    "CLOSE": {
        "needs":   ["next_to_obj", "can_open", "open"],
        "effects": ["closed", "not_open"],
        "is_prep": False,
    },

    # ── Appliance interaction ────────────────────────────────────────────────
    # NOTE: plugged_in is NOT a precondition in VirtualHome
    # All devices start plugged in by default
    "SWITCHON": {
        "needs":   ["next_to_obj", "has_switch", "off"],
        "effects": ["on", "not_off"],
        "is_prep": False,
    },
    "SWITCHOFF": {
        "needs":   ["next_to_obj", "has_switch", "on"],
        "effects": ["off", "not_on"],
        "is_prep": False,
    },

    # ── Character posture ────────────────────────────────────────────────────
    "SIT": {
        "needs":   ["next_to_obj", "not_sitting"],
        "effects": ["sitting", "not_lying"],
        "is_prep": False,
    },
    "STANDUP": {
        "needs":   ["sitting_or_lying"],
        "effects": ["not_sitting", "not_lying"],
        "is_prep": False,
    },
    "LIE": {
        "needs":   ["next_to_obj", "not_lying"],
        "effects": ["lying", "not_sitting"],
        "is_prep": False,
    },
    "SLEEP": {
        "needs":   ["sitting_or_lying"],
        "effects": [],
        "is_prep": False,
    },
    "WAKEUP": {
        "needs":   [],
        "effects": ["not_sitting", "not_lying"],
        "is_prep": False,
    },

    # ── Cleaning ─────────────────────────────────────────────────────────────
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
        "needs":   ["next_to_obj"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    "SQUEEZE": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "CUT": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },

    # ── Consumption / interaction ─────────────────────────────────────────────
    "DRINK": {
        "needs":   ["holds_obj"],
        "effects": [],
        "is_prep": False,
    },
    "EAT": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "READ": {
        "needs":   ["holds_obj"],
        "effects": [],
        "is_prep": False,
    },
    "TOUCH": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "WATCH": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT_SHORT": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT_MEDIUM": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "LOOKAT_LONG": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
    "TYPE": {
        "needs":   ["next_to_obj"],
        "effects": [],
        "is_prep": False,
    },
}


# ─────────────────────────────────────────────
# Human-readable explanations for LLM prompts
# ─────────────────────────────────────────────

PRECONDITION_EXPLANATIONS = {
    "next_to_obj":                      "The character must be next to the object — use WALK first.",
    "next_to_target":                   "The character must be next to the target — use WALK first.",
    "grabbable":                        "The object must be grabbable.",
    "not_both_hands_full":              "Both hands are full — use PUTBACK or DROP an object first.",
    "holds_obj":                        "The character must be holding the object — use GRAB first.",
    "obj_not_inside_closed_container":  "The object is inside a closed container — use OPEN first.",
    "target_open_or_not_openable":      "The target container must be open — use OPEN first.",
    "can_open":                         "The object must be openable.",
    "closed":                           "The object must be closed.",
    "open":                             "The object must be open.",
    "not_on":                           "The object must be switched off before opening.",
    "has_switch":                       "The object must have a switch.",
    "off":                              "The object must be off — use SWITCHOFF first.",
    "on":                               "The object must be on — use SWITCHON first.",
    "not_sitting":                      "The character is sitting — use STANDUP first.",
    "not_lying":                        "The character is lying — use STANDUP first.",
    "sitting_or_lying":                 "The character must be sitting or lying first.",
    "not_holds_obj":                    "The character must not be holding the object.",
}


# ─────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────

def get_preconditions(action: str) -> list:
    """Return preconditions for an action."""
    return SDG.get(action.upper(), {}).get("needs", [])


def get_effects(action: str) -> list:
    """Return effects for an action."""
    return SDG.get(action.upper(), {}).get("effects", [])


def is_prep_action(action: str) -> bool:
    """Return True if this is a state preparation action (e.g. WALK)."""
    return SDG.get(action.upper(), {}).get("is_prep", False)


def explain_precondition(precondition: str) -> str:
    """Return human-readable explanation of a precondition for LLM prompts."""
    return PRECONDITION_EXPLANATIONS.get(
        precondition, f"Precondition '{precondition}' must be satisfied."
    )


def get_actions_that_satisfy(precondition: str) -> list:
    """
    Return list of actions whose effects satisfy the given precondition.
    Used by AdaptiveSubTreeGenerator to find corrective actions.
    e.g. 'next_to_obj' → ['WALK', 'RUN']
         'holds_obj'   → ['GRAB']
         'open'        → ['OPEN']
    """
    EFFECT_TO_PRECOND_MAP = {
        "next_to_obj":    ["next_to_obj", "next_to_target"],
        "holds_obj":      ["holds_obj"],
        "open":           ["open", "target_open_or_not_openable"],
        "not_closed":     ["target_open_or_not_openable"],
        "not_sitting":    ["not_sitting"],
        "not_lying":      ["not_lying"],
        "facing_obj":     ["facing_obj"],
    }

    satisfying_actions = []
    for action, schema in SDG.items():
        for effect in schema["effects"]:
            mapped = EFFECT_TO_PRECOND_MAP.get(effect, [effect])
            if precondition in mapped:
                satisfying_actions.append(action)
                break
    return satisfying_actions


def get_required_prep_sequence(action: str) -> list:
    """
    Return the minimal sequence of prep actions needed before executing action.
    e.g. GRAB → [WALK]
         OPEN → [WALK]
         PUTIN → [WALK (to target)]
         SWITCHON → [WALK]
    """
    preconds = get_preconditions(action)
    prep_sequence = []
    for precond in preconds:
        satisfiers = get_actions_that_satisfy(precond)
        for s in satisfiers:
            if is_prep_action(s) and s not in prep_sequence:
                prep_sequence.append(s)
    return prep_sequence


if __name__ == "__main__":
    print("=== SDG Summary ===")
    for action in sorted(SDG.keys()):
        prep = " [PREP]" if is_prep_action(action) else ""
        print(f"{action}{prep}")
        print(f"  needs:   {get_preconditions(action)}")
        print(f"  effects: {get_effects(action)}")
    print(f"\nTotal actions: {len(SDG)}")
    print(f"Prep actions:  {[a for a in SDG if is_prep_action(a)]}")
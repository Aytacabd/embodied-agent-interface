"""
State Dependency Graph (SDG) for VirtualHome
Based on SDA-Planner paper Section 4.2

For each action we define:
  - needs:    preconditions (Sdep[a]) that must hold before execution
  - effects:  state changes (Seff[a]) after successful execution
  - is_prep:  True if action has no incoming state dependencies
              (paper: "action a is a state preparation action if its
               node na has exactly one outgoing edge to an agent state
               node and no incoming edges from other state nodes")

NOTE: In VirtualHome all devices are plugged_in by default.
      SWITCHON does NOT require plugged_in as a precondition.
"""

SDG = {

    # ── Navigation ───────────────────────────────────────────────────────────
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
        "needs":   [],
        "effects": ["next_to_obj"],
        "is_prep": True,
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

    # ── Container interaction ─────────────────────────────────────────────────
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

    # ── Appliance interaction ─────────────────────────────────────────────────
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

    # ── Character posture ─────────────────────────────────────────────────────
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
    "TYPE": {
        "needs":   ["next_to_obj"],
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
    "grabbable":                       "The object must be grabbable.",
    "not_both_hands_full":             "Both hands are full — use PUTBACK or DROP an object first.",
    "holds_obj":                       "The character must be holding the object — use GRAB first.",
    "obj_not_inside_closed_container": "The object is inside a closed container — use OPEN first.",
    "target_open_or_not_openable":     "The target container must be open — use OPEN first.",
    "can_open":                        "The object must be openable.",
    "closed":                          "The object must be closed before opening.",
    "open":                            "The object must be open.",
    "not_on":                          "The object must be switched off before opening.",
    "has_switch":                      "The object must have a switch.",
    "off":                             "The object must be off — use SWITCHOFF first.",
    "on":                              "The object must be on — use SWITCHON first.",
    "not_sitting":                     "The character is sitting — use STANDUP first.",
    "not_lying":                       "The character is lying — use STANDUP first.",
    "sitting_or_lying":                "The character must be sitting or lying first.",
    "not_holds_obj":                   "The character must not be holding the object.",
}


def get_preconditions(action: str) -> list:
    """Return precondition set Sdep[a] for action a."""
    return SDG.get(action.upper(), {}).get("needs", [])


def get_effects(action: str) -> list:
    """Return effect set Seff[a] for action a."""
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
    for action in ["WALK", "GRAB", "PUTBACK", "OPEN", "SWITCHON", "SWITCHOFF", "STANDUP"]:
        print(f"{action}: needs={get_preconditions(action)}, "
              f"effects={get_effects(action)}, prep={is_prep_action(action)}")

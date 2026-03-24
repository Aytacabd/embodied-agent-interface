# # """
# # State Dependency Graph (SDG) for VirtualHome
# # Based on the actual VirtualHome PDDL action definitions.
# # Action names match EAI's valid_actions exactly.

# # For each action we define:
# #   - needs:    preconditions that must be satisfied before execution
# #   - effects:  state changes after successful execution
# #   - is_prep:  whether this is a "state preparation" action (no incoming deps)

# # NOTE: In VirtualHome, all devices are plugged_in by default.
# # So SWITCHON does NOT require plugged_in as a precondition.
# # """

# # # SDG = {

# # #     # ── Navigation ──────────────────────────────────────────────────────────
# # #     "WALK": {
# # #         "needs":   ["not_sitting", "not_lying"],
# # #         "effects": ["next_to_obj"],
# # #         "is_prep": True,
# # #     },
# # #     "RUN": {
# # #         "needs":   ["not_sitting", "not_lying"],
# # #         "effects": ["next_to_obj"],
# # #         "is_prep": True,
# # #     },
# # #     "FIND": {
# # #         "needs":   [],          # FIND is a navigation/prep action in VirtualHome
# # #         "effects": ["next_to_obj"],
# # #         "is_prep": True,
# # #     },
# # #     "TURNTO": {
# # #         "needs":   [],
# # #         "effects": ["facing_obj"],
# # #         "is_prep": True,
# # #     },
# # #     "POINTAT": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },

# # #     # ── Object interaction ───────────────────────────────────────────────────
# # #     "GRAB": {
# # #         "needs":   [
# # #             "next_to_obj",
# # #             "grabbable",
# # #             "not_both_hands_full",
# # #             "obj_not_inside_closed_container",
# # #         ],
# # #         "effects": ["holds_obj"],
# # #         "is_prep": False,
# # #     },
# # #     "PUTBACK": {
# # #         "needs":   ["holds_obj", "next_to_target"],
# # #         "effects": ["not_holds_obj", "obj_ontop_target"],
# # #         "is_prep": False,
# # #     },
# # #     "PUTIN": {
# # #         "needs":   [
# # #             "holds_obj",
# # #             "next_to_target",
# # #             "target_open_or_not_openable",
# # #         ],
# # #         "effects": ["not_holds_obj", "obj_inside_target"],
# # #         "is_prep": False,
# # #     },
# # #     "PUTOBJBACK": {
# # #         "needs":   ["holds_obj", "next_to_target"],
# # #         "effects": ["not_holds_obj"],
# # #         "is_prep": False,
# # #     },
# # #     "PUTON": {
# # #         "needs":   ["holds_obj"],
# # #         "effects": ["not_holds_obj"],
# # #         "is_prep": False,
# # #     },
# # #     "PUTOFF": {
# # #         "needs":   [],
# # #         "effects": ["not_holds_obj"],
# # #         "is_prep": False,
# # #     },
# # #     "DROP": {
# # #         "needs":   ["holds_obj"],
# # #         "effects": ["not_holds_obj"],
# # #         "is_prep": False,
# # #     },
# # #     "POUR": {
# # #         "needs":   ["holds_obj", "next_to_target"],
# # #         "effects": ["obj_inside_target"],
# # #         "is_prep": False,
# # #     },
# # #     "MOVE": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "PUSH": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "PULL": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "GREET": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },

# # #     # ── Container interaction ────────────────────────────────────────────────
# # #     "OPEN": {
# # #         "needs":   ["next_to_obj", "can_open", "closed", "not_on"],
# # #         "effects": ["open", "not_closed"],
# # #         "is_prep": False,
# # #     },
# # #     "CLOSE": {
# # #         "needs":   ["next_to_obj", "can_open", "open"],
# # #         "effects": ["closed", "not_open"],
# # #         "is_prep": False,
# # #     },

# # #     # ── Appliance interaction ────────────────────────────────────────────────
# # #     # NOTE: plugged_in is NOT a precondition in VirtualHome
# # #     # All devices start plugged in by default
# # #     "SWITCHON": {
# # #         "needs":   ["next_to_obj", "has_switch", "off"],
# # #         "effects": ["on", "not_off"],
# # #         "is_prep": False,
# # #     },
# # #     "SWITCHOFF": {
# # #         "needs":   ["next_to_obj", "has_switch", "on"],
# # #         "effects": ["off", "not_on"],
# # #         "is_prep": False,
# # #     },

# # #     # ── Character posture ────────────────────────────────────────────────────
# # #     "SIT": {
# # #         "needs":   ["next_to_obj", "not_sitting"],
# # #         "effects": ["sitting", "not_lying"],
# # #         "is_prep": False,
# # #     },
# # #     "STANDUP": {
# # #         "needs":   ["sitting_or_lying"],
# # #         "effects": ["not_sitting", "not_lying"],
# # #         "is_prep": False,
# # #     },
# # #     "LIE": {
# # #         "needs":   ["next_to_obj", "not_lying"],
# # #         "effects": ["lying", "not_sitting"],
# # #         "is_prep": False,
# # #     },
# # #     "SLEEP": {
# # #         "needs":   ["sitting_or_lying"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "WAKEUP": {
# # #         "needs":   [],
# # #         "effects": ["not_sitting", "not_lying"],
# # #         "is_prep": False,
# # #     },

# # #     # ── Cleaning ─────────────────────────────────────────────────────────────
# # #     "WASH": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": ["clean", "not_dirty"],
# # #         "is_prep": False,
# # #     },
# # #     "RINSE": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": ["clean", "not_dirty"],
# # #         "is_prep": False,
# # #     },
# # #     "SCRUB": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": ["clean", "not_dirty"],
# # #         "is_prep": False,
# # #     },
# # #     "WIPE": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": ["clean", "not_dirty"],
# # #         "is_prep": False,
# # #     },
# # #     "SQUEEZE": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "CUT": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },

# # #     # ── Consumption / interaction ─────────────────────────────────────────────
# # #     "DRINK": {
# # #         "needs":   ["holds_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "EAT": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "READ": {
# # #         "needs":   ["holds_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "TOUCH": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "WATCH": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "LOOKAT": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # #     "TYPE": {
# # #         "needs":   ["next_to_obj"],
# # #         "effects": [],
# # #         "is_prep": False,
# # #     },
# # # }

# # SDG = {

# #     "WALK": {
# #         "needs":   ["not_sitting", "not_lying"],
# #         "effects": ["next_to_obj"],
# #         "is_prep": True,
# #     },
# #     "RUN": {
# #         "needs":   ["not_sitting", "not_lying"],
# #         "effects": ["next_to_obj"],
# #         "is_prep": True,
# #     },
# #     "FIND": {
# #         "needs":   [],
# #         "effects": ["next_to_obj"],
# #         "is_prep": True,
# #     },
# #     "TURNTO": {
# #         "needs":   [],
# #         "effects": ["facing_obj"],
# #         "is_prep": True,
# #     },
# #     "POINTAT": {
# #         "needs":   ["next_to_obj"],
# #         "effects": [],
# #         "is_prep": False,
# #     },

# #     "GRAB": {
# #         "needs":   [
# #             "next_to_obj",
# #             "not_both_hands_full",
# #             "obj_not_inside_closed_container",
# #             # REMOVED: "grabbable" — static property, always true if planner chose it
# #         ],
# #         "effects": ["holds_obj"],
# #         "is_prep": False,
# #     },
# #     "PUTBACK": {
# #         "needs":   ["holds_obj", "next_to_target"],
# #         "effects": ["not_holds_obj", "obj_ontop_target"],
# #         "is_prep": False,
# #     },
# #     "PUTIN": {
# #         "needs":   [
# #             "holds_obj",
# #             "next_to_target",
# #             "target_open_or_not_openable",
# #         ],
# #         "effects": ["not_holds_obj", "obj_inside_target"],
# #         "is_prep": False,
# #     },
# #     "PUTOBJBACK": {
# #         "needs":   ["holds_obj", "next_to_target"],
# #         "effects": ["not_holds_obj"],
# #         "is_prep": False,
# #     },
# #     "PUTON": {
# #         "needs":   ["holds_obj"],
# #         "effects": ["not_holds_obj"],
# #         "is_prep": False,
# #     },
# #     "PUTOFF": {
# #         "needs":   [],
# #         "effects": ["not_holds_obj"],
# #         "is_prep": False,
# #     },
# #     "DROP": {
# #         "needs":   ["holds_obj"],
# #         "effects": ["not_holds_obj"],
# #         "is_prep": False,
# #     },
# #     "POUR": {
# #         "needs":   ["holds_obj", "next_to_target"],
# #         "effects": ["obj_inside_target"],
# #         "is_prep": False,
# #     },
# #     "MOVE": {
# #         "needs":   ["next_to_obj"],
# #         "effects": [],
# #         "is_prep": False,
# #     },
# #     "PUSH": {
# #         "needs":   ["next_to_obj"],
# #         "effects": [],
# #         "is_prep": False,
# #     },
# #     "PULL": {
# #         "needs":   ["next_to_obj"],
# #         "effects": [],
# #         "is_prep": False,
# #     },
# #     "GREET": {
# #         "needs":   ["next_to_obj"],
# #         "effects": [],
# #         "is_prep": False,
# #     },

# #     "OPEN": {
# #         "needs":   ["next_to_obj", "closed"],
# #         # REMOVED: "can_open" — static property
# #         # REMOVED: "not_on"   — not a real VirtualHome precondition
# #         "effects": ["open", "not_closed"],
# #         "is_prep": False,
# #     },
# #     "CLOSE": {
# #         "needs":   ["next_to_obj", "open"],
# #         # REMOVED: "can_open" — static property
# #         "effects": ["closed", "not_open"],
# #         "is_prep": False,
# #     },

# #     "SWITCHON": {
# #         "needs":   ["next_to_obj", "off"],
# #         # REMOVED: "has_switch" — static property, always true for switchable objects
# #         "effects": ["on", "not_off"],
# #         "is_prep": False,
# #     },
# #     "SWITCHOFF": {
# #         "needs":   ["next_to_obj", "on"],
# #         # REMOVED: "has_switch" — static property
# #         "effects": ["off", "not_on"],
# #         "is_prep": False,
# #     },

# #     "SIT": {
# #         "needs":   ["next_to_obj", "not_sitting"],
# #         # REMOVED: "sittable" — static property
# #         "effects": ["sitting", "not_lying"],
# #         "is_prep": False,
# #     },
# #     "STANDUP": {
# #         "needs":   ["sitting_or_lying"],
# #         "effects": ["not_sitting", "not_lying"],
# #         "is_prep": False,
# #     },
# #     "LIE": {
# #         "needs":   ["next_to_obj", "not_lying"],
# #         "effects": ["lying", "not_sitting"],
# #         "is_prep": False,
# #     },
# #     "SLEEP": {
# #         "needs":   ["sitting_or_lying"],
# #         "effects": [],
# #         "is_prep": False,
# #     },
# #     "WAKEUP": {
# #         "needs":   [],
# #         "effects": ["not_sitting", "not_lying"],
# #         "is_prep": False,
# #     },

# #     "WASH":  {"needs": ["next_to_obj"], "effects": ["clean", "not_dirty"], "is_prep": False},
# #     "RINSE": {"needs": ["next_to_obj"], "effects": ["clean", "not_dirty"], "is_prep": False},
# #     "SCRUB": {"needs": ["next_to_obj"], "effects": ["clean", "not_dirty"], "is_prep": False},
# #     "WIPE":  {"needs": ["next_to_obj"], "effects": ["clean", "not_dirty"], "is_prep": False},
# #     "SQUEEZE": {"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# #     "CUT":     {"needs": ["next_to_obj"], "effects": [], "is_prep": False},

# #     "DRINK": {"needs": ["holds_obj"], "effects": [], "is_prep": False},
# #     "EAT":   {"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# #     "READ":  {"needs": ["holds_obj"],   "effects": [], "is_prep": False},
# #     "TOUCH": {"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# #     "WATCH": {"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# #     "LOOKAT":{"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# #     "TYPE":  {"needs": ["next_to_obj"], "effects": [], "is_prep": False},
# # }
# # # ─────────────────────────────────────────────
# # # Human-readable explanations for feedback prompts
# # # ─────────────────────────────────────────────

# # PRECONDITION_EXPLANATIONS = {
# #     "next_to_obj":                      "The character must be next to the object — use WALK first.",
# #     "next_to_target":                   "The character must be next to the target — use WALK first.",
# #     "grabbable":                        "The object must be grabbable.",
# #     "not_both_hands_full":              "Both hands are full — use PUTBACK or DROP an object first.",
# #     "holds_obj":                        "The character must be holding the object — use GRAB first.",
# #     "obj_not_inside_closed_container":  "The object is inside a closed container — use OPEN first.",
# #     "target_open_or_not_openable":      "The target container must be open — use OPEN first.",
# #     "can_open":                         "The object must be openable.",
# #     "closed":                           "The object must be closed.",
# #     "open":                             "The object must be open.",
# #     "not_on":                           "The object must be switched off before opening.",
# #     "has_switch":                       "The object must have a switch.",
# #     "off":                              "The object must be off — use SWITCHOFF first.",
# #     "on":                               "The object must be on — use SWITCHON first.",
# #     "not_sitting":                      "The character is sitting — use STANDUP first.",
# #     "not_lying":                        "The character is lying — use STANDUP first.",
# #     "sitting_or_lying":                 "The character must be sitting or lying first.",
# #     "not_holds_obj":                    "The character must not be holding the object.",
# # }


# # def get_preconditions(action: str) -> list:
# #     return SDG.get(action.upper(), {}).get("needs", [])


# # def get_effects(action: str) -> list:
# #     return SDG.get(action.upper(), {}).get("effects", [])


# # def is_prep_action(action: str) -> bool:
# #     return SDG.get(action.upper(), {}).get("is_prep", False)


# # def explain_precondition(precondition: str) -> str:
# #     return PRECONDITION_EXPLANATIONS.get(
# #         precondition, f"Precondition '{precondition}' must be satisfied."
# #     )


# # if __name__ == "__main__":
# #     for action in ["WALK", "GRAB", "PUTBACK", "OPEN", "SWITCHON", "STANDUP"]:
# #         print(f"{action}: needs={get_preconditions(action)}, prep={is_prep_action(action)}")
# SDG = {
#     "WALK": {
#         "needs":   ["not sitting(agent)", "not lying(agent)"],
#         "effects": ["next_to(agent, {obj})"],
#         "is_prep": True,
#     },
#     "FIND": {
#         "needs":   [],
#         "effects": ["next_to(agent, {obj})"],
#         "is_prep": True,
#     },
#     "TURNTO": {
#         "needs":   [],
#         "effects": ["facing(agent, {obj})"],
#         "is_prep": True,
#     },
#     "GRAB": {
#         "needs":   [
#             "next_to(agent, {obj})",
#             "not both_hands_full(agent)",
#             "not inside_closed_container({obj})"
#         ],
#         "effects": ["holding({obj})"],
#         "is_prep": False,
#     },
#     "PUTBACK": {
#         "needs":   ["holding({obj})", "next_to(agent, {target})"],
#         "effects": ["not holding({obj})", "ontop({obj}, {target})"],
#         "is_prep": False,
#     },
#     "PUTIN": {
#         "needs":   [
#             "holding({obj})",
#             "next_to(agent, {target})",
#             "open({target})" # Assuming the target needs to be open
#         ],
#         "effects": ["not holding({obj})", "inside({obj}, {target})"],
#         "is_prep": False,
#     },
#     "OPEN": {
#         "needs":   ["next_to(agent, {obj})", "closed({obj})"],
#         "effects": ["open({obj})", "not closed({obj})"],
#         "is_prep": False,
#     },
#     "CLOSE": {
#         "needs":   ["next_to(agent, {obj})", "open({obj})"],
#         "effects": ["closed({obj})", "not open({obj})"],
#         "is_prep": False,
#     },
#     "SWITCHON": {
#         "needs":   ["next_to(agent, {obj})", "off({obj})"],
#         "effects": ["on({obj})", "not off({obj})"],
#         "is_prep": False,
#     },
#     "SWITCHOFF": {
#         "needs":   ["next_to(agent, {obj})", "on({obj})"],
#         "effects": ["off({obj})", "not on({obj})"],
#         "is_prep": False,
#     },
#     "SIT": {
#         "needs":   ["next_to(agent, {obj})", "not sitting(agent)"],
#         "effects": ["sitting({obj})", "not lying(agent)"],
#         "is_prep": False,
#     },
#     "STANDUP": {
#         "needs":   ["sitting_or_lying(agent)"],
#         "effects": ["not sitting(agent)", "not lying(agent)"],
#         "is_prep": False,
#     }
# }

# # ─────────────────────────────────────────────
# # SDA-Planner Reverse Action Mapping
# # ─────────────────────────────────────────────
# REVERSE_ACTIONS = {
#     "OPEN": "CLOSE",
#     "CLOSE": "OPEN",
#     "SWITCHON": "SWITCHOFF",
#     "SWITCHOFF": "SWITCHON",
#     "GRAB": "PUTBACK", 
#     "PUTBACK": "GRAB",
#     "PUTIN": "GRAB",
#     "SIT": "STANDUP",
#     "LIE": "STANDUP",
#     "STANDUP": "SIT" 
# }

# # ─────────────────────────────────────────────
# # Grounding Functions for Live EAI Execution
# # ─────────────────────────────────────────────

# def _ground_template(template_list: list, obj: str = None, target: str = None) -> list:
#     """Replaces the placeholders with actual EAI/VirtualHome Object IDs."""
#     grounded = []
#     for condition in template_list:
#         c = condition
#         if obj:
#             c = c.replace("{obj}", obj)
#         if target:
#             c = c.replace("{target}", target)
#         grounded.append(c)
#     return grounded

# def get_preconditions(action: str, obj: str = None, target: str = None) -> list:
#     raw_needs = SDG.get(action.upper(), {}).get("needs", [])
#     return _ground_template(raw_needs, obj, target)

# def get_effects(action: str, obj: str = None, target: str = None) -> list:
#     raw_effects = SDG.get(action.upper(), {}).get("effects", [])
#     return _ground_template(raw_effects, obj, target)

# def is_prep_action(action: str) -> bool:
#     return SDG.get(action.upper(), {}).get("is_prep", False)

# def get_reverse_action(action: str) -> str:
#     return REVERSE_ACTIONS.get(action.upper(), None)

# if __name__ == "__main__":
#     # Test to ensure EAI-compliant grounding works
#     print("Test GRAB apple.1:")
#     print("Needs:", get_preconditions("GRAB", obj="apple.1"))
#     print("Effects:", get_effects("GRAB", obj="apple.1"))
    
#     print("\nTest PUTIN apple.1 into fridge.1:")
#     print("Needs:", get_preconditions("PUTIN", obj="apple.1", target="fridge.1"))
#     print("Effects:", get_effects("PUTIN", obj="apple.1", target="fridge.1"))
"""
sdg.py
State-Dependency Graph (SDG) for SDA-PLANNER in EAI/VirtualHome.
Formats states in PDDL syntax to match EAI's evaluation metrics.
"""

SDG = {
    "WALK": {
        "needs":   ["not sitting(agent)", "not lying(agent)"],
        "effects": ["next_to(agent, {obj})"],
        "is_prep": True,
    },
    "FIND": {
        "needs":   [],
        "effects": ["next_to(agent, {obj})"],
        "is_prep": True,
    },
    "TURNTO": {
        "needs":   [],
        "effects": ["facing(agent, {obj})"],
        "is_prep": True,
    },
    "GRAB": {
        "needs":   [
            "next_to(agent, {obj})",
            "not both_hands_full(agent)",
            "not inside_closed_container({obj})"
        ],
        "effects": ["holding({obj})"],
        "is_prep": False,
    },
    "PUTBACK": {
        "needs":   ["holding({obj})", "next_to(agent, {target})"],
        "effects": ["not holding({obj})", "ontop({obj}, {target})"],
        "is_prep": False,
    },
    "PUTIN": {
        "needs":   [
            "holding({obj})",
            "next_to(agent, {target})",
            "open({target})"
        ],
        "effects": ["not holding({obj})", "inside({obj}, {target})"],
        "is_prep": False,
    },
    "OPEN": {
        "needs":   ["next_to(agent, {obj})", "closed({obj})"],
        "effects": ["open({obj})", "not closed({obj})"],
        "is_prep": False,
    },
    "CLOSE": {
        "needs":   ["next_to(agent, {obj})", "open({obj})"],
        "effects": ["closed({obj})", "not open({obj})"],
        "is_prep": False,
    },
    "SWITCHON": {
        "needs":   ["next_to(agent, {obj})", "off({obj})"],
        "effects": ["on({obj})", "not off({obj})"],
        "is_prep": False,
    },
    "SWITCHOFF": {
        "needs":   ["next_to(agent, {obj})", "on({obj})"],
        "effects": ["off({obj})", "not on({obj})"],
        "is_prep": False,
    },
    "SIT": {
        "needs":   ["next_to(agent, {obj})", "not sitting(agent)"],
        "effects": ["sitting({obj})", "not lying(agent)"],
        "is_prep": False,
    },
    "STANDUP": {
        "needs":   ["sitting_or_lying(agent)"],
        "effects": ["not sitting(agent)", "not lying(agent)"],
        "is_prep": False,
    }
}

REVERSE_ACTIONS = {
    "OPEN": "CLOSE",
    "CLOSE": "OPEN",
    "SWITCHON": "SWITCHOFF",
    "SWITCHOFF": "SWITCHON",
    "GRAB": "PUTBACK", 
    "PUTBACK": "GRAB",
    "PUTIN": "GRAB",
    "SIT": "STANDUP",
    "LIE": "STANDUP",
    "STANDUP": "SIT" 
}

def _ground_template(template_list: list, obj: str = None, target: str = None) -> list:
    grounded = []
    for condition in template_list:
        c = condition
        if obj: c = c.replace("{obj}", obj)
        if target: c = c.replace("{target}", target)
        grounded.append(c)
    return grounded

def get_preconditions(action: str, obj: str = None, target: str = None) -> list:
    raw_needs = SDG.get(action.upper(), {}).get("needs", [])
    return _ground_template(raw_needs, obj, target)

def get_effects(action: str, obj: str = None, target: str = None) -> list:
    raw_effects = SDG.get(action.upper(), {}).get("effects", [])
    return _ground_template(raw_effects, obj, target)

def is_prep_action(action: str) -> bool:
    return SDG.get(action.upper(), {}).get("is_prep", False)

def get_reverse_action(action: str) -> str:
    return REVERSE_ACTIONS.get(action.upper(), None)
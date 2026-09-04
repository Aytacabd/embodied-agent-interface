"""
State Dependency Graph (SDG) for VirtualHome
Based on the uploaded VirtualHome PDDL as the primary specification, extended
to cover the 42-action VirtualHome script vocabulary.

Notable PDDL requirements carried over faithfully (easy to miss, not errors
in the PDDL -- verified directly against the PDDL text):
- SWITCHON requires plugged_in (PDDL line 219)
- TYPE requires has_switch (PDDL line 383)
- WATCH requires facing (PDDL line 391)
- READ requires readable + holds_obj (PDDL line 307)
- WAKEUP requires sitting_or_lying (PDDL line 483)
- CLOSE effect adds not_on (PDDL line 151)
- MOVE/PULL require movable (PDDL line 399)
- SQUEEZE requires clothes (PDDL line 420)
- CUT requires eatable + cuttable (PDDL line 458)
- EAT requires eatable (PDDL line 469)
- WIPE requires next_to surface + holding a wiping tool (PDDL uses a second,
  unconstrained ?obj2 variable distinct from the wiped surface).

Genuine PDDL-vs-executor overrides -- cases where execution.py's actual
Python precondition checks were read directly and found to disagree with
the PDDL text. The executor is the arbiter in all of these:
- FIND: PDDL requires next_to with no effect (circular at runtime).
  FindExecutor auto-navigates (delegates to WALK+FIND when not already
  close) -- modeled here with no precondition, next_to_obj as an effect.
- OPEN: PDDL doesn't mention hands. OpenExecutor.check_openable requires a
  free hand via _find_free_hand, confirmed OPEN-only (not required for
  CLOSE, which shares the same class via a boolean flag).
- PLUGIN: PDDL says (has_plug OR has_switch). PlugExecutor.check_plugable
  checks HAS_PLUG unconditionally for both plug directions -- no
  has_switch branch exists in the executor at all.
- PLUGIN, PLUGOUT: both require a free hand (_find_free_hand, unconditional
  on plug direction) -- not in the PDDL for either.
- MOVE, PUSH, PULL: all three require a free hand (_find_free_hand) -- not
  in the PDDL.
- PUSH specifically: MoveExecutor.check_movable's movable-property check is
  explicitly gated by `action_name != "push"`, so PUSH never actually needs
  "movable" (MOVE and PULL still do -- only push is exempted in the code).
- SQUEEZE: requires a free hand (_find_free_hand) -- not in the PDDL.
- POINTAT: PDDL has no point_at action at all. execution.py:2270 sets
  `PointAtExecutor = LookAtExecutor` -- a literal class alias, not a
  separate implementation -- so POINTAT's real precondition is facing_obj,
  not proximity.

Second executor-alignment pass (applied 2026-08-26) -- six entries that
were previously flagged as known gaps are now corrected:
- DROP: obj_inside_room removed (faithful to the PDDL, but
  DropExecutor.check_drop only ever checks holds_obj).
- GREET: needs was exactly backwards -- next_to_obj (never checked by
  GreetExecutor) replaced with person (Property.PERSON, which it does
  check). Required a new "person" branch in object_state_model.satisfies.
- PUTON, PUTOFF: both gained the Property.CLOTHES check the executor
  enforces.
- TOUCH: readable and holds_obj removed (TouchExecutor checks neither),
  next_to_obj added (it does check proximity).
- CUT: gained not_both_hands_full (_find_free_hand in check_cuttable).

Known remaining gaps (confirmed against the executor, NOT corrected --
each needs machinery this needs-list schema cannot express):
- CUT also requires holding an object whose class_name contains "knife".
  Modeling it needs a predicate that inspects held objects' class names;
  stated in the prompt layer instead (one_shot.py Rule 9).
- WATCH (same-room check), SIT/LIE (per-class-name occupancy caps), WALK
  (closed-door path-blocking), PUTOBJBACK (precondition depends on a
  remembered grab-origin sdg.py doesn't track) all have real executor
  logic that doesn't fit this needs-list schema at all.
"""

# ─────────────────────────────────────────────────────────────────────────────
# "is_prep" and the paper's state-preparation-action definition
# ─────────────────────────────────────────────────────────────────────────────
# The SDA-Planner paper (arXiv:2509.26375, Section 4.2) defines a state
# preparation action topologically: an action whose node "has exactly one
# outgoing edge to an agent state node and no incoming edges from other state
# nodes", i.e. one that "[is] not dependent on any prior state". Its example is
# ALFRED's "find".
#
# Applied literally to the entries below, that admits FIND and TURNTO only.
# WALK and RUN would be excluded, because the PDDL gives VirtualHome navigation
# two posture preconditions (not_sitting, not_lying) and two effects
# (next_to_obj, inside_room) -- ALFRED's "find" abstraction has neither.
#
# They are nevertheless flagged is_prep=True here, deliberately, because the
# paper's formal rule and the paper's own worked example disagree once
# transplanted onto this action set. is_prep is consumed in exactly one place:
# the backward extension of t_start (Eq. 4) in error_diagnosis.diagnose_error.
# In the paper's Fig. 3 example the root cause is ("pick up","pan") at t=3 and
# t_start=2, i.e. THE NAVIGATION THAT SET UP THE ROOT CAUSE IS INSIDE THE
# WINDOW. VirtualHome's WALK is the action that plays that role. Measured
# directly on that scenario re-expressed in VirtualHome terms:
#
#     WALK.is_prep=True  -> t_source=5, t_start=4, window contains "WALK tomato"
#     WALK.is_prep=False -> t_source=5, t_start=5, window contains nothing
#
# so the literal reading reproduces the definition while breaking the mechanism
# the definition exists to serve. Following the example is the better trade;
# the divergence is recorded rather than hidden. See chapter3_spec.md Section 9.2.
SDG = {

    # ── Navigation ───────────────────────────────────────────────────────────
    # PDDL walk_towards: not(sitting) and not(lying) → next_to
    # PDDL walk_into:    not(sitting) and not(lying) → inside(char, room)
    # is_prep=True: intentional divergence from the paper's formal definition —
    # see the note above the SDG dict.
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
    # PDDL find requires next_to with no effect, but the EAI executor
    # AUTO-NAVIGATES: FindExecutor delegates to WALK+FIND when the character
    # is not close (execution.py _walk_find_executor), so at runtime FIND
    # needs nothing and ends with the character next to the object.
    # The executor is the arbiter → model it like WALK.
    "FIND": {
        "needs":   [],
        "effects": ["next_to_obj"],
        "is_prep": True,
    },
    # PDDL turn_to: no precondition
    "TURNTO": {
        "needs":   [],
        "effects": ["facing_obj"],
        "is_prep": True,
    },
    # PDDL has no point_at action. execution.py:2270: PointAtExecutor =
    # LookAtExecutor (a literal class alias) -- checks facing, not
    # proximity. Verified against LookAtExecutor.check_lookat directly.
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
    # PDDL put_on_character: holds_obj only (putting on self).
    # PutOnExecutor.check_puton ALSO requires Property.CLOTHES -- not in the
    # PDDL, added after reading the executor. Without it, PUTON on a
    # non-clothes item fails in-env but diagnoses as Unsat=[].
    "PUTON": {
        "needs":   ["holds_obj", "clothes"],
        "effects": ["not_holds_obj", "on_char"],
        "is_prep": False,
    },
    # "PUTOFF": {
    #     "needs":   [],
    #     "effects": ["not_holds_obj"],
    #     "is_prep": False,
    # },
    # PDDL drop: holds_obj + obj_inside(?obj, ?room). DropExecutor.check_drop
    # checks ONLY holds -- the room condition exists in the PDDL but was
    # never implemented in the executor, so obj_inside_room was dropped here
    # (it could only ever cause a spurious repair, never catch a real
    # failure). RELEASE's entry was already correct on this point.
    "DROP": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },

    # PutOffExecutor.check_putoff: on_char + Property.CLOTHES. The PDDL has
    # no put_off action at all; clothes added after reading the executor.
    "PUTOFF": {
        "needs":   ["on_char", "clothes"],
        "effects": ["not_on_char", "holds_obj"],
        "is_prep": False,
    },
    "RELEASE": {
        "needs":   ["holds_obj"],
        "effects": ["not_holds_obj"],
        "is_prep": False,
    },
    # PDDL pour: (pourable OR drinkable) + holds + recipient(target) + next_to
    "POUR": {
        "needs":   ["holds_obj", "pourable_or_drinkable", "next_to_target", "target_is_recipient"],
        "effects": ["obj_inside_target"],
        "is_prep": False,
    },
    # PDDL move: movable + next_to + not inside closed container.
    # MoveExecutor.check_movable also requires a free hand
    # (_find_free_hand) for all three of MOVE/PUSH/PULL, unconditionally --
    # not in the PDDL, added after reading the executor directly.
    "MOVE": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container",
                    "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    # MoveExecutor.check_movable's movable-property check is explicitly
    # gated by `action_name != "push"` -- for PUSH specifically the check
    # can never fail, so "movable" is dropped here (kept for PULL, which
    # gets no such exemption in the code).
    "PUSH": {
        "needs":   ["next_to_obj", "obj_not_inside_closed_container",
                    "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    "PULL": {
        "needs":   ["next_to_obj", "movable", "obj_not_inside_closed_container",
                    "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    # GreetExecutor checks ONLY Property.PERSON -- there is no proximity
    # check anywhere in the class. sdg.py previously had this exactly
    # backwards: it required next_to_obj (never enforced, causing spurious
    # WALK repairs) and omitted person (actually enforced, so GREET on a
    # non-person failed in-env while diagnosing as Unsat=[] -- the blind
    # repair-loop case). Now executor-exact.
    "GREET": {
        "needs":   ["person"],
        "effects": [],
        "is_prep": False,
    },

    # ── Container interaction ─────────────────────────────────────────────────
    # PDDL open: can_open + closed + next_to + not(on).
    # The EAI executor ADDITIONALLY requires a free hand (OpenExecutor.
    # check_openable: _find_free_hand is None → fail, OPEN only, not CLOSE).
    # The executor is the arbiter — without this need, an OPEN-with-both-
    # hands-full failure diagnoses as Unsat=[] and the repair loop goes blind.
    "OPEN": {
        "needs":   ["next_to_obj", "can_open", "closed", "not_on",
                    "not_both_hands_full"],
        "effects": ["open", "not_closed"],
        "is_prep": False,
    },
    # PDDL close: can_open + open + next_to; effect: closed + not(on) — NOT not_open
    "CLOSE": {
        "needs":   ["next_to_obj", "can_open", "open"],
        "effects": ["closed", "not_on"],
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
    # PDDL plug_in precondition is next_to + (has_plug OR has_switch) +
    # plugged_out -- but PlugExecutor.check_plugable checks HAS_PLUG
    # unconditionally; no has_switch branch exists anywhere in the executor
    # for either plug direction. The PDDL and the executor disagree here;
    # the executor is the arbiter. Also adds not_both_hands_full: the
    # executor calls _find_free_hand and fails if both hands are full,
    # for both plug-in and plug-out (not gated by direction).
    "PLUGIN": {
        "needs":   ["next_to_obj", "has_plug", "plugged_out", "not_both_hands_full"],
        "effects": ["plugged_in", "not_plugged_out"],
        "is_prep": False,
    },
    # PDDL plug_out: next_to + has_plug + plugged_in + not(on). has_plug
    # here already matches the executor (unlike plug_in, plug_out's PDDL
    # never had an OR branch to begin with). Adds not_both_hands_full --
    # same unconditional _find_free_hand check, verified in the same
    # PlugExecutor.check_plugable method. NOTE: the executor's "still on"
    # check for plug-out sets an error message but never returns False
    # (falls through to True) -- looks like an upstream dead-code bug.
    # Keeping not_on here is the conservative choice: worst case it causes
    # an unnecessary SWITCHOFF-before-PLUGOUT repair, never a false failure.
    "PLUGOUT": {
        "needs":   ["next_to_obj", "has_plug", "plugged_in", "not_on", "not_both_hands_full"],
        "effects": ["plugged_out", "not_plugged_in"],
        "is_prep": False,
    },

    # ── Character posture ─────────────────────────────────────────────────────
    # PDDL sit: next_to + sittable + not(sitting). Effect: sitting only (not_lying is NOT in PDDL)
    "SIT": {
        "needs": ["next_to_obj", "sittable", "not_sitting"],
        "effects": ["sitting", "ontop_obj"],
        "is_prep": False,
    } , 
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
    # PDDL wipe: next_to ?obj1 (surface) + holds_lh/rh ?obj2 (any held object —
    # no property constraint). ?obj2 is a DIFFERENT variable from the wiped
    # surface: holds_obj(surface) is always false and routed WIPE failures to
    # wrong_action (surface not grabbable). holding_anything models ?obj2.
    "WIPE": {
        "needs":   ["next_to_obj", "holding_anything"],
        "effects": ["clean", "not_dirty"],
        "is_prep": False,
    },
    # PDDL squeeze: next_to + clothes. SqueezeExecutor.check_squeezable
    # also requires a free hand (_find_free_hand) -- not in the PDDL,
    # added after reading the executor directly. (The executor's actual
    # property check is broader than "clothes" -- a hardcoded list of
    # squeezable items -- kept as "clothes" here as a simplification.)
    "SQUEEZE": {
        "needs":   ["next_to_obj", "clothes", "not_both_hands_full"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL cut: next_to + eatable + cuttable. CutExecutor.check_cuttable
    # additionally requires a free hand (_find_free_hand) -- added here --
    # AND that the character holds an object whose class_name contains
    # "knife". The knife requirement is NOT modeled: it needs a predicate
    # that inspects the class name of held objects, which this needs-list
    # schema cannot express. It is stated in the prompt layer instead
    # (one_shot.py Rule 9 / SYSTEM_PROMPT RULE 8).
    "CUT": {
        "needs":   ["next_to_obj", "eatable", "cuttable", "not_both_hands_full"],
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
    # PDDL touch: readable + holds_lh/rh + not inside closed container.
    # TouchExecutor.check_reachable checks NEITHER readable NOR holding --
    # only _is_character_close_to and _is_inside (closed container). Both
    # PDDL conditions were dropped and next_to_obj added to match. (READ's
    # readable+holds IS real -- ReadExecutor does check both; TOUCH's entry
    # had been written by analogy to READ rather than from its own class.)
    "TOUCH": {
        "needs":   ["next_to_obj", "obj_not_inside_closed_container"],
        "effects": [],
        "is_prep": False,
    },
    # PDDL watch: lookable + facing + not inside closed container
    "WATCH": {
        "needs":   ["lookable", "facing_obj", "obj_not_inside_closed_container"],
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
    "plugged_out":                     "The object is already plugged in — PLUGIN is not needed here.",
    "not_sitting":                     "The character is sitting — use STANDUP first.",
    "not_lying":                       "The character is lying — use STANDUP first.",
    "sitting_or_lying":                "The character must be sitting or lying first.",
    "not_holds_obj":                   "The character must not be holding the object.",
    "holding_anything":                "The character must be holding something (e.g. a wiping tool) — GRAB one first.",
    "sittable":                        "The object must be sittable.",
    "lieable":                         "The object must be lieable.",
    "movable":                         "The object must be movable.",
    "readable":                        "The object must be readable.",
    "eatable":                         "The object must be eatable.",
    "cuttable":                        "The object must be cuttable.",
    "clothes":                         "The object must be clothes.",
    "person":                          "The object must be a person (e.g. man, woman, child).",
    "lookable":                        "The object must be lookable.",
    "pourable":                        "The object must be pourable.",
    "drinkable":                       "The object must be drinkable.",
    "hangable":                        "The object must be hangable.",
    "has_plug":                        "The object must have a plug.",
    "obj_inside_room":                 "The object must be inside the current room.",
    "pourable_or_drinkable":           "The source object must be pourable or drinkable.",
    "target_is_recipient":             "The target must be a recipient (container for liquids).",
    "drinkable_or_recipient":          "The object must be drinkable or a recipient.",
    "has_plug_or_has_switch":          "The object must have a plug or a switch to be plugged in.",
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
    print("=== SDG Verification against PDDL + executor-verified corrections ===")
    checks = [
        ("WALK",      ["not_sitting", "not_lying"]),
        ("FIND",      []),   # executor auto-navigates (deviation from PDDL)
        ("GRAB",      ["next_to_obj", "grabbable", "not_both_hands_full",
                       "obj_not_inside_closed_container"]),
        ("OPEN",      ["next_to_obj", "can_open", "closed", "not_on",
                       "not_both_hands_full"]),
        ("SWITCHON",  ["next_to_obj", "has_switch", "off", "plugged_in"]),
        ("SWITCHOFF", ["next_to_obj", "has_switch", "on"]),
        ("STANDUP",   ["sitting_or_lying"]),
        ("TYPE",      ["next_to_obj", "has_switch"]),
        ("WATCH",     ["lookable", "facing_obj", "obj_not_inside_closed_container"]),
        ("READ",      ["holds_obj", "readable"]),
        ("WAKEUP",    ["sitting_or_lying"]),
        ("CLOSE",     ["next_to_obj", "can_open", "open"]),
        ("PUTBACK",   ["holds_obj", "next_to_target"]),
        ("PUTIN",     ["holds_obj", "next_to_target", "target_open_or_not_openable"]),
        ("RELEASE",   ["holds_obj"]),
        ("DROP", ["holds_obj"]),   # obj_inside_room: PDDL-only, never in DropExecutor
        ("DRINK",     ["holds_obj", "drinkable_or_recipient"]),
        ("POUR",      ["holds_obj", "pourable_or_drinkable", "next_to_target", "target_is_recipient"]),
        ("PLUGIN",    ["next_to_obj", "has_plug", "plugged_out", "not_both_hands_full"]),
        ("PLUGOUT",   ["next_to_obj", "has_plug", "plugged_in", "not_on", "not_both_hands_full"]),
        # TouchExecutor.check_reachable checks proximity + closed-container
        # only -- neither readable nor holding appear anywhere in the class.
        ("TOUCH",     ["next_to_obj", "obj_not_inside_closed_container"]),
        ("MOVE",      ["next_to_obj", "movable", "obj_not_inside_closed_container",
                       "not_both_hands_full"]),
        ("PUSH",      ["next_to_obj", "obj_not_inside_closed_container",
                       "not_both_hands_full"]),
        ("PULL",      ["next_to_obj", "movable", "obj_not_inside_closed_container",
                       "not_both_hands_full"]),
        ("SIT",       ["next_to_obj", "sittable", "not_sitting"]),
        ("LIE",       ["next_to_obj", "lieable", "not_lying"]),
        ("WIPE", ["next_to_obj", "holding_anything"]),
        ("POINTAT",   ["facing_obj"]),
        ("SQUEEZE",   ["next_to_obj", "clothes", "not_both_hands_full"]),
        # 2026-08-26 executor-alignment pass -- these four had no test before.
        ("GREET",     ["person"]),                        # PERSON only, no proximity
        ("PUTON",     ["holds_obj", "clothes"]),          # CLOTHES enforced by PutOnExecutor
        ("PUTOFF",    ["on_char", "clothes"]),            # CLOTHES enforced by PutOffExecutor
        ("CUT",       ["next_to_obj", "eatable", "cuttable", "not_both_hands_full"]),
    ]
    all_ok = True
    for action, expected in checks:
        actual = get_preconditions(action)
        ok     = set(actual) == set(expected)
        if not ok:
            all_ok = False
        print(f"{'✅' if ok else '❌'} {action}: {actual}")
    print("\n✅ All checks passed!" if all_ok else "\n❌ Some checks failed!")
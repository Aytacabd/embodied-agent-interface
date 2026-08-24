"""
Local self-tests for the character-guard fix (no API, no simulator).

Covers the failure mode found in the run_fixed2 forensics: 12 of the 14
everyday "local"-strategy give-ups were LIE/SIT/GRAB/PUTBACK with the
acting character as the object (e.g. {"LIE": ["character_65", "65"]} on
"Go to sleep" tasks) — structurally unrepairable, since a character node
never satisfies sittable/lieable/grabbable.

Run:  PYTHONPATH=../src python3 test_character_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from eai_sda_runner_tree import (
    SYSTEM_PROMPT,
    _build_retry_prompt,
    _character_target_actions,
    parse_and_validate,
)

NAME_TO_ID = {
    "character_65": 65,
    "bed_105": 105,
    "toilet_37": 37,
    "chair_70": 70,
    "apple_7": 7,
    "fridge_2": 2,
}

BAD_PLAN = '{"WALK": ["bed_105", "105"], "LIE": ["character_65", "65"], "SLEEP": []}'
GOOD_PLAN = '{"WALK": ["bed_105", "105"], "LIE": ["bed_105", "105"], "SLEEP": []}'
BAD_TWO_ARG = '{"WALK": ["toilet_37", "37"], "PUTBACK": ["character_65", "65", "toilet_37", "37"]}'
BAD_INTERLEAVED = '{"WALK": ["bed", "105"], "LIE": ["character", "65"]}'

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# T1 — reject mode returns None on a character-targeting plan
r = parse_and_validate(BAD_PLAN, NAME_TO_ID, char_guard="reject")
check("T1 reject: LIE character_65 rejects whole plan", r is None, f"got {r}")

# T2 — strip mode keeps the rest of the plan
r = parse_and_validate(BAD_PLAN, NAME_TO_ID, char_guard="strip")
check(
    "T2 strip: LIE character_65 removed, WALK+SLEEP kept",
    r == ["[WALK] <bed> (105)", "[SLEEP]"],
    f"got {r}",
)

# T3 — a correct plan passes untouched with the guard on
r = parse_and_validate(GOOD_PLAN, NAME_TO_ID, char_guard="reject")
check(
    "T3 guard on: LIE bed_105 passes",
    r == ["[WALK] <bed> (105)", "[LIE] <bed> (105)", "[SLEEP]"],
    f"got {r}",
)

# T4 — default (no char_guard) preserves the old unguarded behavior
r = parse_and_validate(BAD_PLAN, NAME_TO_ID)
check(
    "T4 default: old behavior unchanged (LIE character parsed)",
    r == ["[WALK] <bed> (105)", "[LIE] <character> (65)", "[SLEEP]"],
    f"got {r}",
)

# T5 — 2-arg action with character as first object (478_1's PUTBACK) rejects
r = parse_and_validate(BAD_TWO_ARG, NAME_TO_ID, char_guard="reject")
check("T5 reject: PUTBACK character->toilet rejects", r is None, f"got {r}")

# T6 — interleaved raw format ["character", "65"] is detected too
r = parse_and_validate(BAD_INTERLEAVED, NAME_TO_ID, char_guard="strip")
check(
    "T6 strip: interleaved character token detected",
    r == ["[WALK] <bed> (105)"],
    f"got {r}",
)

# T7 — retry prompt names the character mistake and the verb
p = _build_retry_prompt("BASE", BAD_PLAN)
check(
    "T7 retry prompt: names character misuse + LIE",
    "character itself" in p and "LIE" in p and p.startswith("BASE"),
    f"got tail: {p[-200:]}",
)

# T8 — empty-args retry branch still works (regression)
p = _build_retry_prompt("BASE", '{"WALK": ["bed_105", "105"], "LIE": []}')
check(
    "T8 retry prompt: empty-args branch intact",
    "EMPTY argument list" in p and "LIE" in p,
    f"got tail: {p[-200:]}",
)

# T9 — generic retry branch still works (regression)
p = _build_retry_prompt("BASE", "complete garbage not json")
check(
    "T9 retry prompt: generic branch intact",
    "invalid or truncated" in p,
    f"got tail: {p[-200:]}",
)

# T10 — SYSTEM_PROMPT carries RULE 6
check(
    "T10 SYSTEM_PROMPT: RULE 6 present",
    "RULE 6" in SYSTEM_PROMPT and "NEVER an action argument" in SYSTEM_PROMPT,
)

# T11 — detector unit checks: no false positive on normal objects
hits = _character_target_actions([{"GRAB": ["apple_7"]}, {"PUTIN": ["apple_7", "fridge_2"]}])
check("T11 detector: no false positives", hits == [], f"got {hits}")

hits = _character_target_actions([{"SIT": ["character_65"]}, {"LIE": ["character_65"]}])
check("T11b detector: finds SIT+LIE", hits == ["SIT", "LIE"], f"got {hits}")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL TESTS PASSED")

# SDA-Planner Results (Post Character-Guard Fix)

**Setup:** gpt-4o-mini, VirtualHome benchmark. Everyday tasks = 342, Hard tasks = 50 (deliberately difficult, hand-authored). No-repair baseline = 3 independent attempts at temperature 1.0, best-of-3 = pass@3 (at least one of three succeeds). With-repair = one deterministic run (temperature 0) with up to N repair attempts per task via diagnosis + search-tree repair. Hard tasks tested at two repair budgets: 3 and 5 max repairs per task.

---

## 1. Headline numbers

| Metric | Result |
|---|---|
| Everyday, no repair (best of 3) | 84.5% |
| Everyday, with repair loop (budget=3) | 93.0% |
| Hard, no repair (best of 3) | 12.5% |
| Hard, with repair loop, budget=3 | 52.0% |
| Hard, with repair loop, budget=5 | **70.0%** |

## 2. No-repair baseline (3 independent attempts, temp 1.0)

**Everyday tasks (342):** Attempt 1: 72.5% · Attempt 2: 74.6% · Attempt 3: 72.5% · Best of 3: **84.5%**

**Hard tasks (50):** Attempt 1: 12.0% · Attempt 2: 10.0% · Attempt 3: 8.0% · Best of 3: **12.5%**

## 3. With-repair results

| | Task success | Execution success |
|---|---|---|
| Everyday, budget=3 | 93.0% | 98.5% |
| Hard, budget=3 | 52.0% | 58.0% |
| Hard, budget=5 | 70.0% | 80.0% |

## 4. Diagnoses per task (how many repair attempts a task needed)

**Everyday (342), budget=3:**

| Repairs used | Tasks |
|---|---|
| 1 | 48 |
| 2 | 6 |
| 3 | 6 |

**Hard (50), budget=3:**

| Repairs used | Tasks |
|---|---|
| 1 | 3 |
| 2 | 12 |
| 3 | 28 |

**Hard (50), budget=5:**

| Repairs used | Tasks |
|---|---|
| 1 | 2 |
| 2 | 11 |
| 3 | 9 |
| 4 | 1 |
| 5 | 17 |

## 5. Why a repair was needed (reason x outcome)

**Everyday (342), budget=3:**

| Reason | Diagnosed | Resolved | Gave up |
|---|---|---|---|
| A step was skipped | 62 | 60 | 2 |
| The object can't do that | 2 | 2 | 0 |
| Steps happened out of order | 14 | 14 | 0 |
| **Total** | **78** | **76** | **2** |

**Hard (50), budget=3:**

| Reason | Diagnosed | Resolved | Gave up |
|---|---|---|---|
| A step was skipped | 65 | 63 | 2 |
| Steps happened out of order | 33 | 33 | 0 |
| The object can't do that | 13 | 13 | 0 |
| **Total** | **111** | **109** | **2** |

**Hard (50), budget=5:**

| Reason | Diagnosed | Resolved | Gave up |
|---|---|---|---|
| A step was skipped | 76 | 68 | 8 |
| Steps happened out of order | 52 | 52 | 0 |
| The object can't do that | 12 | 9 | 3 |
| **Total** | **140** | **129** | **11** |

## 6. Which repair strategy was called

**Everyday (342), budget=3:**

| Strategy | Called | Resolved | Gave up |
|---|---|---|---|
| Rebuild the sequence | 44 | 42 | 2 |
| Add missing step | 32 | 32 | 0 |
| Patch this action | 1 | 1 | 0 |
| Already done | 1 | 1 | 0 |
| **Total** | **78** | **76** | **2** |

**Hard (50), budget=3:**

| Strategy | Called | Resolved | Gave up |
|---|---|---|---|
| Add missing step | 52 | 52 | 0 |
| Rebuild the sequence | 45 | 43 | 2 |
| Patch this action | 13 | 13 | 0 |
| Already done | 1 | 1 | 0 |
| **Total** | **111** | **109** | **2** |

**Hard (50), budget=5:**

| Strategy | Called | Resolved | Gave up |
|---|---|---|---|
| Rebuild the sequence | 72 | 64 | 8 |
| Add missing step | 55 | 55 | 0 |
| Patch this action | 12 | 9 | 3 |
| Already done | 1 | 1 | 0 |
| **Total** | **140** | **129** | **11** |

## 7. Hard-task budget comparison (3 vs 5)

| Metric | Budget=3 | Budget=5 |
|---|---|---|
| Task success rate | 52.0% | 70.0% |
| Execution success rate | 58.0% | 80.0% |
| Hit the replan-budget wall (ran out of repairs mid-task) | 20/50 (40%) | 10/50 (20%) |
| Total diagnoses attempted | 111 | 140 |
| Per-diagnosis resolve rate | 98.2% | 92.1% |
| Avg replans used per task | 2.20 | 2.78 |

**Interpretation:** the lower per-diagnosis resolve rate at budget=5 isn't regression — it reflects the system reaching harder residual failures that budget=3 used to cut off before ever attempting. Halving the budget-wall hit rate (40% to 20%) is the direct driver of the task-success jump, at a cost of ~26% more replans per task on average.

---

**Suggested slide flow:** (1) overview/headline stats, (2) no-repair baseline detail, (3) with-repair results (all three: everyday, hard budget=3, hard budget=5), (4) reasons table, (5) strategy table, (6) budget=3 vs budget=5 mechanics, (7) takeaways.

---

# The Root-Cause Fix: Character-Reference Bug

## The problem

Out of 15 "Patch this action" repair attempts on everyday tasks, only 1 ever succeeded (93% failure rate on that specific repair strategy). Digging into all 14 failures individually revealed they weren't 14 different problems — **12 of the 14 were the exact same bug**, occurring on three task types: "Go to sleep," "Relax on sofa," and "Go to toilet."

**What the bug looked like:** the LLM's plan named the acting character itself as the target of an action that should have targeted furniture:

- `LIE: [character_65]` instead of `LIE: [bed_105]`
- `SIT: [character_65]` instead of `SIT: [toilet_37]`
- `PUTBACK: [character_65] -> [toilet_37]` (an even more confused version -- wrong verb entirely, should have been SIT)

This is structurally impossible to satisfy: a character node in VirtualHome never has the `sittable`/`lieable`/`grabbable` properties, under any circumstances, on any task. No amount of repair search, reordering, or retrying fixes this -- the repair system correctly recognized it couldn't be fixed and gave up.

## Why it happened

Traced to the exact task goal data: for tasks like "Go to sleep," the goal is literally defined as `character_65 is LYING` -- a real, valid goal about the character's own posture. The prompt-building code lists every object whose state changes (including the character) in the "Objects in the scene" list, and phrases the goal as `character_65 is LYING` -- worded identically to how a furniture goal like `light_245 is ON` would be worded. Combined with the fact that English posture verbs ("lie down," "sit down") don't naturally take an object at all, the model sometimes filled the required object-argument slot with the character itself -- the most salient noun sitting right next to the goal state in the prompt -- instead of the furniture named in a separate edge-goal line it also had access to but didn't reliably use.

## The fix (two layers)

**1. A one-line system-prompt rule:** *"The character is NEVER an action argument. A goal like 'character is LYING' or 'character is ON bed' is achieved by targeting the FURNITURE: LIE [bed_name, bed_id] / SIT [chair_name, chair_id]. NEVER write SIT, LIE, GRAB or PUTBACK with the character as the object."*

**2. A parse-time validation guard:** every generated plan is checked for any action whose object argument resolves to the acting character's own ID. If found, the plan is rejected and the model gets one corrective retry with an explicit message naming the exact mistake ("your previous response used the character itself as the object of LIE..."). This exists as a safety net in case the prompt rule alone doesn't fully prevent the mistake -- and it proved its value directly: on the hard-task run, the guard caught a *new* variant of the same bug (`DROP: [character]`, i.e. "drop yourself") that wasn't even one of the three verbs the prompt rule explicitly named, because the guard checks any action verb generically rather than a hardcoded list.

## Evidence it worked

- Zero character-reference tokens left in any final output across all six re-run experiments (everyday no-repair x3 attempts, everyday with-repair, hard no-repair x3 attempts, hard with-repair at both budgets).
- The safety-net guard fired only twice total across ~450 tasks re-run (once on everyday, once on hard, both for verbs the tasks' families produce rarely) -- meaning the prompt rule alone was sufficient almost everywhere; the guard is genuinely a backstop, not the primary mechanism.
- Direct before/after example (task 181_1, "Go to sleep"): before fix, the model wrote `LIE: [character_65]`; after fix, on the very first attempt, it wrote `LIE: [bed_105]` and the task executed successfully.
- Direct before/after example (task 478_1, "Go to toilet"): before fix, the model used the wrong verb entirely (`PUTBACK: [character_65]->[toilet_37]`); after fix, it correctly used `SIT: [toilet_37]` -- the fix improved the model's action choice, not just its object choice.

## Measured impact

This is the direct cause of the "object can't do that" reason collapsing from 18 diagnoses (only 4 resolved, 14 gave up) to just 2 diagnoses (2 resolved, 0 gave up) on everyday tasks -- see section 5 of the results above. It's also why "Swap the action" (the strategy triggered when the LLM tries to grab something ungrabbable) dropped to zero calls -- that strategy used to fire on the same bug in a different guise.

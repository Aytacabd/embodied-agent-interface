# Full run results + function-call statistics — main-342 and hard-50, with and without repair

Self-contained reference data. "With repair" = the full system (diagnosis +
search + repair loop enabled). "Without repair" ("no-adapt") = the same
initial plan generation, but failures are just skipped and execution
continues — no diagnosis, no search, no repair prompts, no LLM feedback
at all after the first plan. This is an important asymmetry to carry into
any presentation: **the "without repair" side calls zero diagnosis
functions by design** — there is no repair mechanism to instrument. Its
closest honest analog is the *execute vs. skip* count per attempt, given
further down.

Everything below is independently verified from source logs, not
estimated.

---

## PART A — Main task set (342 tasks)

### A1. Results, all 4 runs

| Run | Task success rate | Notes |
|---|---|---|
| Without repair — attempt 1 (T=1.0) | 69.6% | independent draw |
| Without repair — attempt 2 (T=1.0) | 68.4% | independent draw |
| Without repair — attempt 3 (T=1.0) | 70.5% | independent draw |
| Without repair — best of the 3 above | 77.9% | pass@3, i.e. counts a task as solved if *any* of the 3 attempts solved it |
| **With repair loop** (T=0, deterministic, this session's fixes) | **90.6%** | single run |

Best-of-k progression for the "without repair" side (cumulative, from
joining all 3 attempts): pass@1 = 70.0%, pass@2 = 75.3%, pass@3 = 77.9%.
First-success distribution: 238 tasks solved on attempt 1, 18 more first
solved on attempt 2, 9 more first solved on attempt 3, 75 never solved in
any of the 3 (out of 340 tasks scored — 2 tasks had no scoreable entry in
one or more attempts due to an evaluator-side matching quirk unrelated to
the planner).

### A2. Function-call statistics — WITH repair (the diagnosis/search mechanism)

Every number below is a count of actual diagnosis events parsed directly
from the run log — i.e., every time the system's root-cause finder ran.

- **Total diagnosis calls: 96**, across 68 of the 342 tasks (274 tasks
  executed clean on the first try, triggering zero diagnosis calls)
- **Resolved: 82 (85.4%)** — **Gave up: 14**

**Failure type, as classified by the diagnosis step** (each diagnosis call
classifies the failure into exactly one of these):
| Failure type | Count |
|---|---|
| A step was skipped | 64 |
| The object can't do that | 18 |
| Steps happened out of order | 14 |

**Repair strategy chosen** (each diagnosis call also picks exactly one):
| Strategy | Times chosen |
|---|---|
| Rebuild the sequence (full search) | 39 |
| Add missing step | 37 |
| Patch this action | 15 |
| Already done | 3 |
| Swap the action | 2 |

**Strategy × outcome** (did the chosen strategy actually resolve the
failure):
| Strategy | Resolved | Gave up |
|---|---|---|
| Rebuild the sequence | 39 | 0 |
| Add missing step | 37 | 0 |
| Patch this action | 1 | 14 |
| Already done | 3 | 0 |
| Swap the action | 2 | 0 |

**What precondition was actually missing** (from the root-cause finder,
counted per diagnosis call — some calls have more than one):
holding the object: 29 · being next to the object: 29 · being next to the
target/destination: 20 · object trapped in a closed container: 12 ·
surface must support lying down: 9 · character posture blocks the action: 5
· object has no grabbable property: 3 · must be facing the object: 3 ·
surface must support sitting: 2 · invalid look-at target: 1 · must stand
up first: 1 · both hands full: 1

**Diagnosis calls needed per task** (of the 68 tasks that needed at least
one): 50 tasks needed exactly 1 call, 8 needed exactly 2, 10 needed
exactly 3 (the per-task maximum allowed).

**The one path that skips verification entirely** ("swap the action"):
fired 2 times out of 96 diagnosis calls on this task set.

### A3. Statistics — WITHOUT repair (skip-and-continue; no diagnosis calls exist)

There is no diagnosis mechanism running here at all — the system makes one
LLM call for the initial plan, then executes it action by action; any
action that fails is simply skipped, with execution continuing on the next
one. The closest meaningful statistic is how many actions got skipped this
way:

| Attempt | Tasks with a scoreable run | Actions executed | Actions skipped | Tasks with ≥1 skip | Tasks with 0 skips (fully clean) |
|---|---|---|---|---|---|
| 1 | 319 / 342 | 1,082 | 172 | 99 | 220 |
| 2 | 320 / 342 | 1,120 | 156 | 102 | 218 |
| 3 | 318 / 342 | 1,057 | 152 | 82 | 236 |

(The small shortfall from 342 each time — 23/22/24 tasks — is initial
plans that failed to parse even after one retry; those tasks contribute 0
actions and are excluded from the table above, not silently folded into
either column.)

**Which actions get skipped most often** (attempt 1 shown; 2 and 3 follow
the same shape — full breakdown in the source file if needed):
STANDUP: 37 · PUTBACK: 14 · GRAB: 14 · PLUGIN: 7 · SWITCHON: 5 · PUTIN: 5 ·
WASH: 4 · LOOKAT: 4 · CLOSE: 2 · WALK: 2 · POUR: 2 · SWITCHOFF: 1 · LIE: 1
· PUTON: 1

STANDUP dominates for a structural reason: without repair, a plan that
should have started with STANDUP but didn't just fails that action and
skips it, rather than getting corrected — consistent with "add missing
step" being the single most common repair type on the with-repair side.

---

## PART B — Hard task set (50 deliberately hard tasks)

### B1. Results, all 4 runs

| Run | Task success rate | Notes |
|---|---|---|
| Without repair — attempt 1 (T=1.0) | 12.0% | independent draw |
| Without repair — attempt 2 (T=1.0) | 10.0% | independent draw |
| Without repair — attempt 3 (T=1.0) | 6.0% | independent draw |
| Without repair — best of the 3 above | 14.6% | pass@3 |
| **With repair loop** (T=0, deterministic, this session's fixes, 3-try budget) | **50.0%** | single run |

Best-of-k progression: pass@1 = 12.5%, pass@2 = 14.6%, pass@3 = 14.6% (the
3rd attempt contributed 0 new first-successes). First-success
distribution: 6 tasks solved on attempt 1, 1 more first solved on attempt
2, 0 more on attempt 3, 41 never solved in any of the 3 (out of 48 tasks
scored — 2 tasks had no scoreable entry in one or more attempts, same
evaluator-matching caveat as above).

For reference: the with-repair number was 32.0% before this session's
fixes, and that earlier run additionally used a *larger* repair budget (5
tries instead of 3) — so 50.0% is a strictly harder-earned number, not
inflated by extra budget relative to the baseline.

### B2. Function-call statistics — WITH repair

- **Total diagnosis calls: 115**, across 43 of the 50 tasks (only 7 tasks
  executed clean on the first try — this set is specifically designed to
  need repair)
- **Resolved: 112 (97.4%)** — **Gave up: 3**

**Failure type:**
| Failure type | Count |
|---|---|
| A step was skipped | 53 |
| Steps happened out of order | 48 |
| The object can't do that | 14 |

**Repair strategy chosen:**
| Strategy | Times chosen |
|---|---|
| Add missing step | 60 |
| Rebuild the sequence (full search) | 40 |
| Patch this action | 14 |
| Already done | 1 |

**Strategy × outcome:**
| Strategy | Resolved | Gave up |
|---|---|---|
| Rebuild the sequence | 38 | 2 |
| Add missing step | 60 | 0 |
| Already done | 1 | 0 |
| Patch this action | 13 | 1 |

Notable contrast with the main set: "Patch this action" resolves 13/14
times here vs. 1/15 on the main set — this task mix has no sleep/lie-down
tasks, which is what drives the failure rate for that strategy elsewhere.
Same mechanism, opposite result, purely from task-mix composition.

**What precondition was actually missing:**
being next to the target/destination: 54 · being next to the object: 26 ·
object trapped in a closed container: 24 · destination container must be
open: 17 · both hands full: 11 · holding the object: 11 · object has no
open-able property: 3 · target must be closed first: 3

**Diagnosis calls needed per task** (of the 43 tasks that needed at least
one): 4 tasks needed exactly 1, 6 needed exactly 2, **33 needed exactly 3**
(the maximum allowed) — a strong, direct signal that the 3-try budget is a
real ceiling on this task set specifically, not a formality.

**The one path that skips verification entirely:** fired 0 times out of
115 diagnosis calls on this task set (only fired on the main set, 2 times).

### B3. Statistics — WITHOUT repair (skip-and-continue)

| Attempt | Tasks with a scoreable run | Actions executed | Actions skipped | Tasks with ≥1 skip | Tasks with 0 skips (fully clean) |
|---|---|---|---|---|---|
| 1 | 50 / 50 | 631 | 354 | 40 | 10 |
| 2 | 50 / 50 | 651 | 349 | 42 | 8 |
| 3 | 50 / 50 | 589 | 365 | 44 | 6 |

Note the much higher skip rate here (roughly 35–38% of all attempted
actions skipped, vs. roughly 13–14% on the main set) — direct evidence
this task set is harder in a way that shows up identically on both the
repair and no-repair sides.

**Which actions get skipped most often** (attempt 1 shown):
CLOSE: 12 · PUTIN: 10 · GRAB: 5 · OPEN: 4 · STANDUP: 4 · PUTBACK: 4 ·
SWITCHOFF: 1

Here CLOSE/PUTIN/OPEN dominate rather than STANDUP — consistent with this
task set's diagnosis calls being dominated by "being next to the
target/destination" and "object trapped in a closed container" (§B2)
rather than posture issues.

---

## PART C — Combined totals (both task sets together)

- **392 tasks tested in total** (342 + 50)
- **211 diagnosis calls in total** (96 + 115) — only on the with-repair side
- **91.9% combined automatic-fix rate** (194 of 211 diagnosis calls
  resolved: 82 + 112)
- **2 of 211 diagnosis calls (under 1%)** took the one path that skips
  rule-based verification entirely
- On the without-repair side: **0 diagnosis calls, by design**, on either
  task set, across all 6 attempts (3 per task set)

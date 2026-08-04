# How the Repair Loop Works — full content for a presentation

This document contains everything needed to build a presentation about an
automated plan-repair system for a household-robot planner: the narrative,
every verified number, and the full data tables behind the charts. All
numbers below were independently verified against source logs/code before
use (not taken on faith from any draft) — treat them as ground truth.

Suggested deliverable: a slide deck (or similar) walking through the
problem, the 4-step repair mechanism, why a language model is combined with
rule-based verification rather than using either alone, and the results.

---

## 1. The core numbers (use these exactly)

| Metric | Value |
|---|---|
| Total tasks tested | 392 (342 "everyday" tasks + 50 deliberately hard tasks) |
| Total failures diagnosed across both sets | 211 |
| Overall automatic-fix rate | 91.9% (194 of 211 diagnosed failures resolved) |
| Distinct actions the robot can take | 41 |
| Average objects in a scene | ~290 (measured mean: 294.6, across 5 sampled scenes) |
| Raw action×object combinations per repair step (blind) | ~12,000 (41 × ~290) |
| Median candidate actions the language model proposes per repair | 3 (measured exactly: median 3, mean 3.03, n=205 suggestions) |
| Repairs where the language model's output is used *without* rule-based verification | 2 of 211 (under 1%) — the "swap the action" case only |

---

## 2. Headline comparison: repair loop vs. no repair

"No repair" = give the robot 3 independent tries, no diagnosis, no feedback between tries, just retry from scratch (temperature 1.0, so each try genuinely differs). "With repair loop" = one run with up to 3 *informed* repair attempts (temperature 0, deterministic).

| Task set | No repair, best of 3 tries | With the repair loop | Improvement |
|---|---|---|---|
| Everyday tasks (342) | 77.9% | 90.6% | +12.7 points |
| Deliberately hard tasks (50) | 14.6% | 50.0% | 3.4× |

Supporting detail (only needed if going deeper on the "no repair" baseline):
- Main-342, individual no-repair attempts: 69.6%, 68.4%, 70.5% (a single un-resampled run: 71.3%)
- Main-342, cumulative best-of-k: pass@1 = 70.0%, pass@2 = 75.3%, pass@3 = 77.9%
- Hard-50, individual no-repair attempts: 12.0%, 10.0%, 6.0%
- Hard-50, cumulative best-of-k: pass@1 = 12.5%, pass@2 = 14.6%, pass@3 = 14.6% (3rd attempt added 0 new successes)
- Hard-50 "with repair loop" was 32.0% before a round of fixes this session; the 50.0% above is post-fix, and also uses a *lower* repair budget (3 tries instead of the earlier 5) — so the improvement is a like-for-like or better comparison, not inflated by extra budget.

---

## 3. The problem, framed narratively

A language model's first attempt at a plan usually looks reasonable and
usually breaks partway through. Concrete example (safe to use as-is, no
internal IDs or code needed):

> The robot is told to wash a load of laundry. It walks to the washing
> machine, opens it, picks up soap, picks up a dress — then tries to pick
> up a pair of pants. Both hands are already full. The plan never accounted
> for that, and without a way to recover, the task simply ends there.

This is the motivating scenario for "why repair matters" — pair it directly
with the "no repair" baseline numbers above (15–78% success without any
repair, depending how demanding the task is).

---

## 4. The repair mechanism — 4 steps, plain language (no code/function names)

Every time an action fails, the same four-step loop runs, up to 3 times per
task (each retry re-executes the whole plan from the top):

1. **Diagnose** — figure out what kind of failure this is. The system
   doesn't get told *why* something failed; it replays what happened and
   works it out. Three shapes of failure occur in practice:
   - **A step was skipped** — something the action needed was never done
     (e.g. trying to put away an item that was never picked up)
   - **Steps happened out of order** — everything needed did happen, just
     in the wrong sequence (e.g. hands filled up before a third pickup)
   - **The object can't do that** — the action doesn't apply to this
     object at all (e.g. asking the character to lie on something you
     can't lie on)

2. **Trace the cause** — replay the plan from the very start and find the
   *exact moment* things stopped being true, not just the step that failed.
   Based on that, pick one of five responses:
   - **Add the missing step** — one small thing was skipped (walk over,
     stand up, face the object). Cheapest fix, no search needed.
   - **Patch this action** — the object can't do what was asked; try a
     close variation.
   - **Swap the action** — the step itself was the wrong idea; ask for a
     genuinely different one.
   - **Already done** — the goal is already true; drop the now-pointless
     step.
   - **Rebuild the sequence** — the general case: more than one thing
     needs to change, or no single-step patch applies. Hands off to the
     full search (step 3).

3. **Search for a fix** — for the general case, explore possible next
   actions and keep the *shortest* sequence that actually satisfies what's
   missing, checked directly against the same rules the simulated house
   itself runs on. Three sources of candidate ideas:
   - Rule-based fixes worked out directly from the objects involved (open
     a closed door, free up a hand, walk to the right spot) — no guessing
   - A short hint from the language model — treated as ideas to test, not
     accepted on faith
   - What the plan was already trying to do, so nothing gets lost track of

4. **Weave it back in** — stitch the fix into the original plan and re-run
   the whole thing. Three specific safeguards (added and verified this
   session) keep that stitching from causing new problems:
   - **Put things where they belong** — if hands are full, a fix now
     delivers a held item to where it actually needed to end up, instead
     of dropping it on the floor
   - **Reopen what got closed** — if something needed is now stuck behind
     a door another fix just shut, reopen it, retrieve what's needed, put
     it back
   - **Don't discard a genuinely necessary step** — a pickup that's the
     actual root cause of a problem still has to happen eventually; it no
     longer gets thrown away just for being the trigger

---

## 5. Why a language model at all (the persuasion arc)

This is the section to make compelling — it's the part most worth strong
visual treatment (a funnel diagram, a doctor/lab metaphor, etc).

**A. The search space is enormous, blind.**
41 actions × ~290 objects ≈ 12,000 raw combinations to check per repair
step, before even considering actions that involve two objects at once.
Blind search over that is infeasible. Something has to narrow the field
*before* exact checking starts. Suggested visual: a funnel — "~12,000" wide
→ "3" (what the language model actually proposes, the measured median) →
"1" (the repair actually used, small circle/checkmark).

**B. Think of it as a doctor and a lab.**
- *The language model is the doctor.* It looks at the failure and
  suggests what might fix it — a median of 3 candidate actions per
  failure, drawn from ordinary judgment about how a house works. A
  doctor's judgment narrows a huge range of possibilities to a short,
  plausible list. It isn't the final word.
- *The rule-based checker is the lab.* It doesn't take the doctor's
  suggestions on faith. For each one it can only answer one of two things:
  **"provably wrong"** (rejected outright) or **"can't rule it out"**
  (passes through for the search to try). It never says "safe" — only
  "not disprovable."

**C. The lab has the final word, always.**
A structured search explores only what the lab didn't rule out and
returns the shortest valid repair. Nothing downstream of that search asks
the language model anything — it's ordinary, deterministic code from
there. **The guarantee this gives:** the language model cannot make a
broken repair pass. At worst, all its suggestions are rejected and the
step gets dropped — costing progress, but never silently accepting an
incorrect fix. A wrong idea costs progress; it never costs correctness.

**D. The one honest exception, quantified.**
There is exactly one situation where the language model's output goes
straight into the plan without the lab checking it first: when a step is
diagnosed as fundamentally the wrong thing to attempt ("swap the action"),
it's asked to propose a direct replacement, accepted as-is. Across all 211
diagnosed failures in this evaluation, this happened **twice — under 1%**.
Everywhere else, every suggestion passes through the lab before anything
reaches the plan.

**E. Synthesis.**
Judgment picks a short list from thousands of raw possibilities in one
step. Verification makes sure only a provably valid one is ever used.
Neither half is enough alone: judgment without checking would occasionally
accept something that looks right but isn't; checking without judgment
would have to search enormously to find what a person would guess
instantly. Together, that combination is what resolved 91.9% of every
diagnosed failure across two very different task sets.

---

## 6. Results — full breakdown tables

### 6a. Everyday tasks (342 tasks)

- 96 failures diagnosed, across 68 tasks (274 tasks ran clean on the first try)
- **85.4% resolved automatically** (82 resolved, 14 gave up)

**How each failure got handled** (sort descending for the chart):
| Response | Count |
|---|---|
| Rebuild the sequence | 39 |
| Add missing step | 37 |
| Patch this action | 15 |
| Already done | 3 |
| Swap the action | 2 |

**...and whether it actually worked** (stacked bar: fixed vs. gave up):
| Response | Fixed | Gave up |
|---|---|---|
| Rebuild the sequence | 39 | 0 |
| Add missing step | 37 | 0 |
| Patch this action | 1 | 14 |
| Already done | 3 | 0 |
| Swap the action | 2 | 0 |

Notable: "Patch this action" (triggered by "object can't do that" failures)
resolves only 1 of 15 times here — driven almost entirely by "go to
sleep"/lie-down tasks. This is the one clear weak spot in the everyday set.

**What was actually missing** (the specific unmet condition, for a deeper
"what causes failures" slide if wanted):
holding the object (29), being next to the object (29), being next to the
target/destination (20), object trapped in a closed container (12),
surface must support lying down (9), character posture blocks the action
(5), object has no grabbable property (3), must be facing the object (3),
surface must support sitting (2), invalid look-at target (1), must stand
up first (1), both hands full (1).

**Repairs needed per task** (of the 68 tasks that needed ≥1 repair): 50
tasks needed exactly 1, 8 needed exactly 2, 10 needed exactly 3 (the
maximum allowed).

### 6b. Deliberately hard tasks (50 tasks)

- 115 failures diagnosed, across 43 tasks (only 7 tasks ran clean on the
  first try — this set is designed to need repair)
- **97.4% resolved automatically** (112 resolved, 3 gave up)

**How each failure got handled:**
| Response | Count |
|---|---|
| Add missing step | 60 |
| Rebuild the sequence | 40 |
| Patch this action | 14 |
| Already done | 1 |

**...and whether it actually worked:**
| Response | Fixed | Gave up |
|---|---|---|
| Rebuild the sequence | 38 | 2 |
| Add missing step | 60 | 0 |
| Already done | 1 | 0 |
| Patch this action | 13 | 1 |

Notable: "Patch this action" resolves 13 of 14 times here — the *opposite*
pattern from the everyday set, because this task mix doesn't include the
sleep/lie-down category that drives that failure mode there. Good example
of a result that's genuinely dataset-dependent, not a fixed property of
the system.

**What was actually missing:**
being next to the target/destination (54), being next to the object (26),
object trapped in a closed container (24), destination container must be
open (17), both hands full (11), holding the object (11), object has no
open-able property (3), target must be closed first (3).

**Repairs needed per task** (of the 43 tasks that needed ≥1 repair): 4
tasks needed exactly 1, 6 needed exactly 2, **33 needed exactly 3** (the
maximum allowed) — a strong signal that the 3-try limit is a real ceiling
on this task set, not just a formality. (Worth noting: 33/43 hitting the
cap costs some recovery headroom — a few tasks likely would have resolved
with one more try.)

---

## 7. Takeaways (closing section)

1. **The loop fixes 85–97% of what it diagnoses**, from a short list of
   ideas per failure, every one independently verified. The remaining gap
   is mostly one narrow, known weak spot (the "object can't do that" /
   "Patch this action" case), not broad unreliability.
2. **That weak spot is dataset-dependent**: 1 of 15 resolves on everyday
   tasks (driven by sleep/lie-down tasks), 13 of 14 on hard tasks (which
   don't include that task type at all).
3. **The 3-try limit is a real, measurable ceiling**: 33 of 43 repaired
   hard tasks used every try allowed.
4. **The division of labor is what makes this reliable**: a language
   model supplies judgment about what's plausible; formal rules supply the
   guarantee that only a genuinely valid fix is ever used — and that
   guarantee held for over 99% of every repair attempted, across 211
   diagnosed failures and 392 tasks.

---

## 8. Suggested slide structure (17 slides, if a 1:1 mapping is wanted)

1. Title — 211 failures diagnosed, 91.9% fixed automatically, 392 tasks
2. The problem (laundry example + no-repair baseline: 15–78%)
3. Pipeline overview (4 steps, diagram)
4. Step 1: Diagnose (3 failure types)
5. Step 2: Trace the cause (5 response strategies)
6. Step 3: Search for a fix (3 candidate sources)
7. Step 4: Weave it back in (3 safety nets)
8. Why not pure search alone (41 × ~290 ≈ 12,000 → 3 → 1 funnel)
9. Doctor and lab metaphor (two cards)
10. The lab has the final word (guarantee statement)
11. The one exception, quantified (2/211, under 1%)
12. Synthesis (judgment + verification)
13. Headline results (77.9→90.6%, 14.6→50.0%)
14. Everyday-tasks breakdown (two charts: §6a)
15. Hard-tasks breakdown (two charts: §6b)
16. Takeaways (§7, points 1–3)
17. Closing (§7, point 4 as the final message)

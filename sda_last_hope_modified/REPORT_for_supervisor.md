# Progress Report — Improvements to the SDA Planner
### Comparison of the "highest results" version with the current version

**Prepared for:** Thesis supervision discussion
**Project:** SDA-Planner (adaptive re-planning) on the VirtualHome benchmark
(Embodied Agent Interface / EAI)

---

## A. One-paragraph summary

Our system asks a language model to write a step-by-step plan for a household
robot, then runs that plan inside a simulator. When a step fails, the system tries
to **diagnose why** and **repair** the plan automatically. Since the last saved
("highest results") version, I have done three things: (1) made the planner's
internal understanding of the rules match what the simulator *actually* enforces,
(2) improved the instructions we give the language model, and (3) fixed a number of
concrete bugs that were quietly hurting our results. Importantly, **the way results
are scored has not changed**, so the new version can be compared fairly against the
previous one.

---

## B. Background: how the system works (plain terms)

The pipeline has four stages:

1. **Generate** — the language model reads the scene and the goal, and writes a
   plan (a list of robot actions such as "walk to the cup, grab the cup, …").
2. **Execute** — each action is run inside the VirtualHome **simulator**. The
   simulator has strict rules ("you must be next to an object before you grab it",
   "you can only hold two things at once", etc.). I refer to the part of the
   simulator that checks these rules as the **executor**.
3. **Diagnose** — if an action fails, the system figures out *why* it failed (which
   rule was broken) and *where* the plan went wrong.
4. **Repair** — the system inserts or replaces a few actions to fix the problem,
   then re-runs the plan. This is the core idea of the SDA method.

Two terms used below:
* **Precondition** — something that must be true before an action can succeed
  (e.g. "the device must be plugged in before you can switch it on").
* **Grader** — the separate scoring script that decides whether a final plan is
  correct. The grader is what produces the numbers we report.

---

## C. What the previous ("highest results") version contributed

The previous saved version fixed a **formatting problem** in how plans were written
out. The grader expects each object to be written as a *name and an id separately*
(for example `"cup", "7"`). The earlier code wrote them *glued together*
(`"cup_7"`), which the grader could not read, so many perfectly good plans were
thrown out before they were even scored. Fixing this formatting is what produced
the jump in results — hence the name "highest results."

However, at that point the **diagnosis part** of the system was still based on an
older specification (the PDDL planning file) rather than on what the simulator
actually does. Some of those assumptions were wrong. The current version corrects
them.

---

## D. What changed in the current version

The changes fall into three groups.

### Group 1 — Making the planner's rules match the real simulator

Previously, the planner's internal rulebook (which it uses to diagnose and repair
failures) was copied from a written specification that did not always match the
simulator's real behavior. I went through the simulator code action by action and
corrected the rulebook. Examples:

| Action | Old (incorrect) assumption | Corrected to match simulator |
|---|---|---|
| FIND | needed to already be next to the object | automatically walks to the object |
| POINTAT | needed to be next to the object | needs to be facing the object |
| PUTON (clothes) | only needed to be holding the item | must be holding it **and** it must be clothing |
| DROP | required the object to be in the room | no such requirement |

This matters because if the planner's understanding is wrong, its *repairs* are
wrong — it tries to fix problems that don't exist, or misses the real cause.

### Group 2 — Better instructions for the language model

The instructions (the "prompt") given to the language model were rewritten to be
shorter, clearer, and consistent with the simulator. I corrected several
descriptions of what each action requires and added explicit reminders, such as:

* Turn to face an object before watching or looking at it.
* Plug a device in before switching it on.
* Switch an appliance off before opening it.
* Sit or lie down before sleeping.

Clearer instructions mean the language model's *first* plan is more often correct,
so there is less to repair later.

### Group 3 — Bug fixes found by examining real run logs

While reviewing actual runs, I found and fixed several concrete problems. Each one
was either silently lowering our score or wasting computation:

1. **Valid actions were being deleted.** The repair logic sometimes decided an
   action was "already done" and removed it — even when it was genuinely needed
   (for example, putting an object down or pouring). These goal-achieving actions
   were being dropped, which would fail the goal. *Fixed.*

2. **Confusion when the scene had two of the same object.** If there were two
   televisions, the system could think the target TV was already on (because the
   *other* one was on) and skip turning it on. *Fixed* so this shortcut only
   applies when there is a single, unambiguous object.

3. **A wasted re-try on almost every task.** The system double-checked that the
   plan "touched" every goal object. But many goals are about the robot itself
   (e.g. "the robot is sitting on the toilet"), and the robot is never written as
   an action argument — so this check always failed and triggered a second,
   pointless call to the language model. This was happening on nearly every task.
   *Fixed* by excluding the robot from that check. (This alone roughly halves the
   number of language-model calls on those tasks.)

4. **A task lost to a formatting quirk.** When the model wrote an object name that
   already contained its id and then repeated the id, the name became invalid and
   the whole task was discarded. *Fixed.*

5. **Cutting food.** The simulator requires the robot to be holding a knife to cut,
   but the planner did not know this, so it could not repair "cut" failures.
   *Fixed.*

6. **Facing direction not updated after moving.** In the simulator, walking makes
   the robot stop facing whatever it faced before; the planner did not track this,
   which hid the real cause of some failures. *Fixed.*

7. **Plans being cut off.** Long plans were being truncated by a length limit,
   producing broken output. The limit was raised. *Fixed.*

8. **Repair budget.** Minor automatic clean-ups were using up the limited number of
   repair attempts; they no longer do, leaving the full budget for real repairs.

---

## E. Why the comparison is still fair

This is the key point for interpreting the numbers:

* **The output format is unchanged** — plans are still written in the format the
  grader expects, exactly as in the "highest results" version.
* **The simulator and grader are unchanged.** (An earlier experimental change to
  the simulator was deliberately **undone**, because it would have altered how
  plans are scored and made comparison with the previous version unfair.)

Because the scoring machinery is identical, any difference in results comes from the
planner producing **genuinely better plans**, not from an easier test.

---

## F. What this means for the results

* The language model's first plan is more often correct (clearer instructions).
* When a plan does fail, the repair step is more reliable — it no longer deletes
  needed actions, mis-edits the plan, or misunderstands the cause.
* Fewer wasted language-model calls (cheaper and faster runs).
* All of this should improve the success/executability scores **without** changing
  how those scores are measured.

---

## G. Honesty notes / limitations (for discussion)

1. **The instructions are now quite detailed.** This helps the language model, but
   it also means our prompt gives more guidance than the original benchmark prompt.
   For a fair comparison we should run the "baseline" (no repair loop) with the
   *same* prompt, so the only thing being measured is the benefit of the SDA repair
   loop — not the richer instructions. This is exactly the two-experiment plan.
2. **One deeper limitation remains.** When a scene has several identical objects,
   the diagnosis still reasons at the level of the object *type* rather than the
   specific instance. I have contained the worst symptom, but a complete fix is a
   larger task and could be a follow-up.
3. **Nothing is committed yet.** All of these changes are currently saved in the
   working files but not yet recorded as official versions in the project history.

---

## H. Suggested next steps

1. Save (commit) the current changes in clearly labelled groups so the history is
   easy to follow.
2. Run the two planned experiments: one with the current (detailed) prompt, one
   with the original prompt — keeping the simulator identical for both.
3. Decide together whether the deeper "identical objects" limitation (point G.2) is
   worth addressing before the final results.

---

*A more technical version of this report, with file names and code-level detail, is
available in `REPORT_highest_results_vs_working_tree.md`.*

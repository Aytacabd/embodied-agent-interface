# One shot original vs last comparison

## What this file is

`one_shot.py` contains the instructions sent to the LLM (the "prompt") when it's asked to plan a sequence of household-robot actions in VirtualHome — things like WALK, GRAB, OPEN, PUTIN. Part of that prompt is a table telling the model what properties an object needs before an action can be used on it. For example, you can only SIT on something if it has the property SITTABLE.


- `one_shot_original.rtf` — the original prompt, exactly as it existed before anyone touched it.
- `one_shot_last.rtf` — the current, corrected version.

This file explains what changed between the two and why, in plain terms.

## A few terms used 

- **The executor** — this is the actual Python code (`execution.py`) that runs the simulation. When the robot tries to do GRAB on something, it's this code that decides whether that's actually allowed right now, based on the real state of the scene. It is the final, ground-truth authority on what an action requires — more authoritative than the prompt, and more authoritative than the PDDL below.
- **PDDL** — a separate specification file that describes, in a formal planning language, what each action is supposed to require. It was written as a design document. Sometimes the executor's real code doesn't actually match what the PDDL says it should do — when that happens, the executor wins, because that's the code that actually runs.


## 1. Four precondition fixes (the table itself was wrong)

These four are real bugs in the original prompt — cases where the property requirement told to the model didn't match what the simulator actually checks.

**PUTIN** — placing something inside a container (like a fridge or a box)
- Before: the target container had to have the property `CAN_OPEN`.
- Now: no fixed property requirement.
- Why: when we read the executor's actual code for this, it turned out the real rule is "if the container is the kind of thing that opens and closes, it just needs to be currently open — but if it's not that kind of container at all (nothing to open), that's completely fine too." The old prompt would have wrongly told the model it could never put something into a non-openable container. This is a case where the fix is one that isn't easy to write as a plain rule about a single property, so instead of forcing it into the table, it was turned into a separate written-out rule ("if the container is closed, open it first") elsewhere in the prompt.

**WATCH** — looking at something attentively
- Before: no requirement at all.
- Now: the object must have the property `LOOKABLE`.
- Why: the executor's code for WATCH does check for `LOOKABLE` — this was just missing from the original prompt. The model was never told about a real requirement that the simulator enforces.

**PUSH** — shoving something away from you
- Before: the object had to be `MOVABLE`.
- Now: no requirement at all.
- Why: when the executor's code was read carefully, it turned out that PUSH is specifically written as an exception — the code has a line that says "don't check MOVABLE if the action is PUSH." So PUSH never actually needed that property in the simulator; the original prompt had simply copied the requirement from the similar action MOVE without checking whether PUSH followed the same rule.

**CUT** — cutting food
- Before: `['EATABLE', 'CUTABLE']` (note: "CUTABLE" is missing a T)
- Now: `['EATABLE', 'CUTTABLE']`
- Why: this was a straightforward spelling mistake. The real property name in the simulator's code is `CUTTABLE` with two Ts. Because the old prompt spelled it wrong, that check could never actually succeed — the model was being told about a requirement that, if checked literally, could never be satisfied. The description text was also updated to say plainly that CUT needs BOTH properties together, not just one or the other (see the note on that below).

## 2. Two actions that were simply missing

`SLEEP` and `WAKEUP` weren't in the original prompt's list of actions at all — the model had no way to be told to use them, even though the simulator fully supports both (there is real, working code in the executor for each one). They were added back in as actions that need no object and have no special requirement: `SLEEP: (0, [])` and `WAKEUP: (0, [])`.


## 3. New explanation added, not a behavior change

The updated prompt added one paragraph explaining a rule that was always true but never actually stated: when a requirement lists more than one property (like `['POURABLE', 'DRINKABLE']`), the object normally only needs ONE of them, not both. CUT is called out as the one exception — it genuinely needs both of its listed properties at the same time. This didn't change what the model is allowed to do; it just makes an already-true rule explicit instead of leaving the reader to guess it.

A handful of other wording changes (rephrased instructions, a clearer worked example, bullet points instead of run-on paragraphs) also don't change what's allowed or required — they just make the prompt easier to read correctly.

## 4. The list of rules at the end got much bigger (8 → 13)

The original prompt ended with 8 short numbered "Notice" reminders. The current one has 13 "Important rules." This isn't just a renumbering — some old ones were removed, some were reworded, two got merged into one, and 10 are completely new. Here's what happened to each one.

### The original 8 "Notice" items, one by one

**Notice 1** — "CLOSE undoes OPEN (it changes the object's state from OPEN back to CLOSED)."
→ **Removed.** Not wrong, just unnecessary — the table already lists what CLOSE and OPEN each require, so spelling out their relationship in a sentence too didn't add anything new for the model to act on.

**Notice 2** — "You can't PUTIN the character into a room; use WALK for that instead."
→ **Removed.** Also unnecessary — the table already says the first thing you PUTIN somewhere has to be GRABBABLE, and a room isn't a graspable object, so this case was already ruled out without needing its own sentence.

**Notice 3** — "The robot itself (the character) is never one of the objects an action is performed on."
→ **Kept, reworded.** Became **Rule 1**. Same meaning, just said more plainly.

**Notice 4** — "Action names should be written in capital letters with no spaces."
→ **Removed.** Just a formatting note, and every example already shown in the prompt writes action names in capitals anyway, so it wasn't adding new information.

**Notice 5** — "Before doing anything to an object, walk to it first."
→ **Kept, combined with Notice 8 below, and extended.** Became **Rule 2**, which now also covers a case the original missed: if you walk somewhere to pick up a tool, and then need to use that tool on a *different* object across the room, you have to walk back to that object before using the tool on it. A plan that grabs a rag near the sink and then tries to wipe a counter across the room needs a second WALK in between, and the old wording didn't make that clear.

**Notice 6** — "Output the object's name and its ID, not just the name."
→ **Kept, reworded and made stricter.** Became **Rule 3**, which now spells out the exact shape expected for a 1-object action versus a 2-object action, so there's less room for the model to guess wrong and produce output that fails to parse.

**Notice 7** — "Never leave the output empty."
→ **Kept, but folded into an earlier paragraph** that already said something similar ("no action requirement does not mean empty output"), instead of being stated twice in two different places.

**Notice 8** — "If you want to act on an object, walk to it first." (The same instruction as Notice 5, stated again elsewhere in the prompt.)
→ **Merged into Rule 2**, so it's now said once instead of twice.

### The 10 rules that are completely new

None of these existed in any form in the original prompt. Each one exists because, while going through the simulator's actual code action by action, something turned up that the code genuinely requires but that the model was never told about — meaning a plan could look perfectly reasonable and still fail once it actually ran, for a reason nothing in the prompt warned about.

- **Rule 4 — Use the exact object, not just any object of the same type.** If there are two cups in the scene and the goal is about one specific cup, the model has to track and use that specific one, not swap in any cup with the same name.
- **Rule 5 — Match the placement action to what the goal actually asks for.** PUTBACK sets something on top of a surface; PUTIN puts something inside a container. Explains how to tell which one the goal requires, and what to do when the goal doesn't make it obvious either way.
- **Rule 6 — Plug it in before turning it on.** If an appliance is shown as unplugged, the simulator refuses to switch it on until it's plugged in first.
- **Rule 7 — Face something before watching, looking at, or pointing at it.** The simulator checks that the robot is actually turned toward the object for all three of these actions, not just standing near it.
- **Rule 8 — Prefer WALK over FIND for getting somewhere.** FIND happens to also walk the robot there automatically, which made it easy to reach for, but WALK is the clearer, more predictable choice, so the prompt now nudges toward that instead.
- **Rule 9 — Some actions require already holding something in your hand.** Drinking, reading, and putting something in or on a container all require holding the relevant object first. Wiping requires holding *something* (anything). Cutting food specifically requires holding a knife, not just standing next to something cuttable.
- **Rule 10 — DROP and RELEASE aren't the same thing, and neither is a substitute for placing an object properly.** DROP lets something fall to the floor. RELEASE just lets go of it while staying in the same room. Neither should be used as a shortcut for actually placing something into or onto a target — that still needs PUTIN, PUTBACK, or POUR.
- **Rule 11 — Open a container before putting something inside it, if it's closed.** This is the rule that ended up covering what used to be a (wrong) fixed requirement on PUTIN in the table itself — see section 1 above.
- **Rule 12 — Repeating the same action twice in the output is allowed and means "do it twice," not "the second one overwrites the first."**
- **Rule 13 — Your hand needs to be free before certain actions.** Grabbing, opening, moving, pushing, pulling, squeezing, cutting, and plugging something in or out all require at least one free hand. Since the robot has two hands, that also means it can hold at most two objects at a time. If both hands are already full, something needs to be put down or dropped first.



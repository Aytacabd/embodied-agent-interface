# one_shot.py action precondition table

Comparison of every action offered to the LLM in `one_shot.py`, checked against the original upstream prompt, the VirtualHome PDDL, and what `execution.py`'s Executor classes actually enforce at runtime.

- Baseline source: `embodied-agent-interface@main`, `src/virtualhome_eval/evaluation/action_sequencing/prompts/one_shot.py`
- Ground truth: `virtualhome.pddl` and `execution.py` (confirmed byte-identical to upstream)
- Scope: 42 dispatched actions checked, 41 offered in the current prompt

Full detail behind every row is in `sdg_pddl_executor_verification.txt` and `one_shot_prompt_diff_and_rationale.txt` in the repo root. This file is the condensed table form of both.

## Status key

- **Match** - unchanged, confirmed correct against the executor
- **Corrected** - baseline was wrong, current fixes it, kept
- **Clarified** - precondition unchanged, but comment expanded since the executor accepts more than the property table can express
- **Added** - action wasn't offered in the baseline prompt at all
- **Excluded** - a real, dispatched executor action deliberately left off the current prompt's list

## How to read a precondition entry

Every action in the table below is written as `(count, [[...], [...]])`. That format has two levels, and each level uses different logic:

- **`count`** is how many objects the action needs. **Each object gets its own list** of acceptable properties (`[...]`), one per object, in order. All of these per-object lists must pass. If an action needs 2 objects, both objects have to clear their own check, no exceptions. That's ordinary AND logic and it never changes.
- **Inside one object's own list**, if more than one property is listed, that object only needs *one* of them, not all. This is the only place OR shows up.

For example, POUR is `(2, [['POURABLE', 'DRINKABLE'], ['RECIPIENT']])`:

- Object 1 (the thing being poured) needs to be POURABLE *or* DRINKABLE - either tag is enough on its own.
- Object 2 (the thing being poured into) needs to be RECIPIENT.
- **Both** objects have to pass their own check for POUR to be valid. Object 1 passing does nothing if object 2 fails - they're separate objects with separate, mandatory requirements.

CUT is the one exception to the inner-list OR rule: `(1, [['EATABLE', 'CUTTABLE']])`. It only has one object, and that single object needs *both* tags at once (edible and cuttable together), not just one. That's because "cuttable" alone would also match things like rope or paper, and CUT is meant only for cutting food - so both properties are required on that one object, instead of either one qualifying it.

So: different objects are always AND'd together. Only within a single object's own list of alternative-property spellings does OR apply, and CUT is the only action where that single-object list is AND instead.

## Action-by-action table

| Action | Baseline entry | PDDL requirement | Executor reality | Current entry | Status | Reason |
|---|---|---|---|---|---|---|
| WALK | `(1, [[]])` | not sitting, not lying -> next_to | Matches. Also does room-graph pathfinding (closed-door blocking), outside the PDDL's vocabulary. | `(1, [[]])` | Match | Unchanged, confirmed correct. |
| RUN | `(1, [[]])` | No entry by name, aliases WALK | Same WalkExecutor family. | `(1, [[]])` | Match | Unchanged. |
| FIND | `(1, [[]])` | Requires next_to, produces no effect (circular at runtime) | FindExecutor auto-navigates (Walk+Find). Executor overrides PDDL, confirmed deliberate. | `(1, [[]])` | Match | PDDL override, executor-confirmed. |
| TURNTO | `(1, [[]])` | No precondition | `check_turn_to` returns True unconditionally. | `(1, [[]])` | Match | Unchanged. |
| POINTAT | `(1, [[]])` | No point_at action exists in the PDDL | `PointAtExecutor = LookAtExecutor`, a literal alias. Real precondition is facing_obj, not proximity. | `(1, [[]])` | Match | Table unchanged; facing now enforced by Rule 7 instead. |
| GRAB | `(1, [['GRABBABLE']])` | grabbable + next_to + free hand + not inside closed | `check_grabbable`: all 4 confirmed present. | `(1, [['GRABBABLE']])` | Match | Unchanged, exact match. |
| PUTBACK | `(2, [['GRABBABLE'], []])` | holds + next_to target | PutExecutor(ON): holds, close-to-target, no openable branch. | `(2, [['GRABBABLE'], []])` | Match | Unchanged. See Rule 5 for new ON-vs-INSIDE goal routing. |
| PUTIN | `(2, [['GRABBABLE'], ['CAN_OPEN']])` | holds + next_to + (not can_open OR open) | PutExecutor(INSIDE): open-if-openable is conditional, not a flat gate. | `(2, [['GRABBABLE'], []])` | Corrected | Static CAN_OPEN would wrongly block PUTIN into non-openable containers. Moved to dynamic Rule 11. |
| PUTOBJBACK | not offered | No entry by name, own executor class | Re-applies PUTBACK/PUTIN's full check against a remembered grab-origin `sdg.py` never tracks. | excluded | Excluded | Its real precondition depends on a remembered pickup location that `sdg.py` cannot track, so a failure could not be diagnosed or repaired. The runner's SYSTEM_PROMPT bans it for the same reason. |
| PUTON | `(1, [['CLOTHES']])` | holds_obj only (putting on self) | PutOnExecutor: holding-check + CLOTHES. | `(1, [['CLOTHES']])` | Match | Unchanged, correct. |
| PUTOFF | `(1, [['CLOTHES']])` | No entry by name | PutOffExecutor: on_char-check + CLOTHES. | `(1, [['CLOTHES']])` | Match | Unchanged, correct. |
| DROP | `(1, [[]])` | holds + obj_inside(room) | DropExecutor checks holds only, no room check exists in the class. | `(1, [[]])` | Match | Prompt matches the executor. The PDDL's room requirement is never implemented in `DropExecutor`. |
| RELEASE | `(1, [[]])` | No entry by name, aliases DROP | Same DropExecutor, holds only. | `(1, [[]])` | Match | Unchanged. See Rule 10 for DROP-vs-RELEASE usage guidance. |
| POUR | `(2, [['POURABLE','DRINKABLE'], ['RECIPIENT']])` | (pourable OR drinkable) + holds + recipient + next_to | `_check_pourable`: matches exactly. | `(2, [['POURABLE','DRINKABLE'], ['RECIPIENT']])` | Match | Unchanged. |
| MOVE | `(1, [['MOVABLE']])` | movable + next_to + not inside closed | MOVABLE required, not exempted (unlike PUSH). Free hand also required. | `(1, [['MOVABLE']])` | Match | Unchanged. Free-hand nuance covered by Rule 13. |
| PUSH | `(1, [['MOVABLE']])` | No entry by name, aliases MOVE | MoveExecutor exempts `action_name=="push"` from the movable check entirely. | `(1, [[]])` | Corrected | Baseline copied MOVE's requirement without reading the per-action-name branch. Only PUSH is exempt. |
| PULL | `(1, [['MOVABLE']])` | No entry by name, aliases MOVE | Not exempted; MOVABLE genuinely required. | `(1, [['MOVABLE']])` | Match | Unchanged, correct. |
| GREET | `(1, [['PERSON']])` | No entry by name | GreetExecutor checks only Property.PERSON, no proximity check at all. | `(1, [['PERSON']])` | Match | Baseline was already correct: PERSON is exactly what the executor checks. |
| OPEN | `(1, [['CAN_OPEN']])` | can_open + closed + next_to + not(on) | Matches, plus a free-hand check the PDDL omits (OPEN-only, not CLOSE). | `(1, [['CAN_OPEN']])` | Match | Unchanged. Free-hand covered by Rule 13. |
| CLOSE | `(1, [['CAN_OPEN']])` | can_open + open + next_to | Same class as OPEN, close=True branch. No free-hand, no not-on check, correctly absent. | `(1, [['CAN_OPEN']])` | Match | Unchanged. |
| SWITCHON | `(1, [['HAS_SWITCH']])` | has_switch + off + plugged_in + next_to | SwitchExecutor(True): matches exactly. | `(1, [['HAS_SWITCH']])` | Match | Unchanged. Plugged-in dependency covered by Rule 6. |
| SWITCHOFF | `(1, [['HAS_SWITCH']])` | has_switch + on + next_to | Matches; no plugged-state check. | `(1, [['HAS_SWITCH']])` | Match | Unchanged. |
| PLUGIN | `(1, [['HAS_PLUG']])` | (has_plug OR has_switch) + next_to + plugged_out | PlugExecutor checks HAS_PLUG unconditionally, no has_switch branch exists anywhere. | `(1, [['HAS_PLUG']])` | Match | Baseline was already correct. Note the PDDL states an OR with HAS_SWITCH, but the executor has no such branch — here the PDDL is wrong, not the prompt. |
| PLUGOUT | `(1, [['HAS_PLUG']])` | next_to + has_plug + plugged_in + not(on) | Matches; plug_out's PDDL never had an OR branch to begin with. | `(1, [['HAS_PLUG']])` | Match | Unchanged, correct. |
| SIT | `(1, [['SITTABLE']])` | next_to + sittable + not(sitting) | Matches; executor also enforces per-class occupancy caps (chair=1, couch=4...), outside any needs-list schema. | `(1, [['SITTABLE']])` | Match | Unchanged. |
| STANDUP | `(0, [])` | sitting OR lying | Not flagged as a divergence. | `(0, [])` | Match | Unchanged. |
| LIE | `(1, [['LIEABLE']])` | lieable + next_to + not(lying) | Matches; same occupancy-cap nuance as SIT (bed=3). | `(1, [['LIEABLE']])` | Match | Unchanged. |
| SLEEP | not offered | sitting OR lying | SleepExecutor confirmed dispatched, checks LYING-or-SITTING only. | `(0, [])` | Added | Real, dispatched executor action the baseline's list simply never included. |
| WAKEUP | not offered | sitting OR lying; effect is empty | WakeUpExecutor confirmed dispatched, same check as SLEEP. | `(0, [])` | Added | Same as SLEEP, missing vocabulary in baseline. |
| WASH | `(1, [[]])` | next_to only | WashExecutor: close-to only. | `(1, [[]])` | Match | Unchanged. |
| RINSE | `(1, [[]])` | Shares WASH's requirement | Same WashExecutor class. | `(1, [[]])` | Match | Unchanged. |
| SCRUB | `(1, [[]])` | Shares WASH's requirement | Same WashExecutor class. | `(1, [[]])` | Match | Unchanged. |
| WIPE | `(1, [[]])` | next_to (surface) + holds any object (unconstrained second variable) | WipeExecutor: close-to + holding anything, no class check at all. | `(1, [[]])` | Match | Table unchanged. Rule 9's tool-class example list ("rag, sponge, towel...") was later stripped; the executor never gated on it. |
| SQUEEZE | `(1, [['CLOTHES']])` | next_to + clothes | Also accepts a hardcoded non-clothes list (sponge, soap, rag, towel, cleaning_solution...) and requires a free hand. | `(1, [['CLOTHES']])` | Clarified | CLOTHES-only is directionally correct but narrower than the executor. Extra classes noted in a comment, not fabricated as a property. |
| CUT | `EATABLE, CUTABLE` (typo) | next_to + eatable + cuttable | AND-confirmed, plus free hand + must hold an object with "knife" in its class_name, neither in the PDDL. | `(1, [['EATABLE','CUTTABLE']])` | Corrected | Spelling fix plus AND-semantics called out explicitly. Knife/free-hand requirements live in Rules 9 and 13 instead. |
| DRINK | `(1, [['DRINKABLE','RECIPIENT']])` | (drinkable OR recipient) + holds | DrinkExecutor: matches, no proximity check either side. | `(1, [['DRINKABLE','RECIPIENT']])` | Match | Unchanged. |
| EAT | `(1, [['EATABLE']])` | next_to + eatable | EatExecutor has an extra fallback (checks objects resting on target), more permissive, low risk. | `(1, [['EATABLE']])` | Match | Unchanged. |
| READ | `(1, [['READABLE']])` | readable + holds | ReadExecutor: matches exactly. | `(1, [['READABLE']])` | Match | Unchanged. |
| TOUCH | `(1, [[]])` | readable + holds + not inside closed | TouchExecutor never checks READABLE, only proximity and not-inside-closed. | `(1, [[]])` | Match | Baseline was already correct: TouchExecutor checks no property at all. (READ's READABLE requirement is real — the two actions look parallel but aren't.) |
| WATCH | `(1, [[]])` | lookable + facing + not inside closed | WatchExecutor confirms LOOKABLE, plus a same-room check outside any PDDL vocabulary. | `(1, [['LOOKABLE']])` | Corrected | Baseline's omission of LOOKABLE was a real, confirmed gap, not stylistic. |
| LOOKAT | `(1, [[]])` | facing only | LookAtExecutor: only checks facing. | `(1, [[]])` | Match | Unchanged. Facing now backed by Rule 7. |
| TYPE | `(1, [['HAS_SWITCH']])` | has_switch + next_to | Matches; also exempts objects literally named "keyboard," more permissive, low risk. | `(1, [['HAS_SWITCH']])` | Match | Unchanged. |

## Behavioral rules: baseline Notices to current Rules

The baseline had eight numbered "Notice" items. The current prompt has thirteen "Important rules": some are the same instruction reworded, several are new (executor-grounded gaps the baseline never mentioned), and a couple were dropped as redundant.

| Baseline | Current | What changed | Why (executor grounding) |
|---|---|---|---|
| Notice 1 | dropped | "CLOSE reverses OPEN" | Redundant. OPEN/CLOSE share one executor class with a boolean flag; the property table already states both. |
| Notice 2 | dropped | "Can't PUTIN a character into a room" | Redundant with PUTIN's unchanged GRABBABLE gate. A room isn't grabbable. |
| Notice 3 | Rule 1 | "Character is never an argument" | Same content, reworded. No behavioral change. |
| Notice 4 | dropped | "Action names uppercase, no whitespace" | Pure formatting, already implied by every example in the prompt. |
| Notice 5 and 8 | Rule 2 | "WALK before acting," stated twice in baseline, merged into one, plus a new case: walk back to the real target after grabbing a tool elsewhere | Real gap. Grab a rag near the sink, then WIPE a counter across the room needs a second WALK. Baseline's phrasing didn't cover it. |
| Notice 6 | Rule 3 | "Output name + ID," now stricter, exact list shape per argument count | Reduces ambiguity that directly affects whether the JSON output parses at all. |
| - | Rule 4 (new) | Instance-ID discipline: use the exact goal instance, not any same-class object | Prompt-side half of the instance-level (not class-level) tracking design. |
| - | Rule 5 (new) | PUTBACK to ON / PUTIN to INSIDE goal-relation matching. Fallback wording later simplified from a curated container list to "ordinary judgment" | PutExecutor splits by a Relation param; the curated list covered roughly 13-20% of real classes and wasn't load-bearing anyway. |
| Notice 7 | folded into intro | "Output must not be empty" | Consolidated into the existing "no action requirement does not mean empty output" warning, said once instead of twice. |
| - | Rule 6 (new) | PLUGIN before SWITCHON if PLUGGED_OUT | SwitchExecutor confirmed: switch-on fails on a plugged-out device. |
| - | Rule 7 (new) | TURNTO before WATCH, LOOKAT, or POINTAT | All three check facing, not proximity. POINTAT added once its LookAtExecutor alias was found. |
| - | Rule 8 (new) | Prefer WALK over FIND for navigation | FIND auto-navigates and is functionally redundant; WALK is the more predictable, explicit choice. |
| - | Rule 9 (new) | Holding requirements for DRINK, READ, PUTIN, PUTBACK, WIPE, CUT (TOUCH removed once confirmed unnecessary; CUT's knife clause added later) | Matches each Executor's actual holds / holding_anything / knife-class checks. |
| - | Rule 10 (new) | DROP vs. RELEASE distinction | Baseline listed RELEASE with no usage guidance at all. |
| - | Rule 11 (new) | OPEN a closed container before PUTIN | Where PUTIN's dropped static CAN_OPEN gate is actually enforced, as a dynamic rule. |
| - | Rule 12 (new) | Repeated action keys are ordered, not overwritten | Matches the evaluator's order-preserving JSON loader; plain dict semantics would suggest otherwise. |
| - | Rule 13 (new) | Free hand required before GRAB, OPEN, MOVE, PUSH, PULL, SQUEEZE, PLUGIN, PLUGOUT, CUT | All nine call `_find_free_hand` and fail with both hands full — a case the baseline warned about nowhere, since holding exactly 2 objects still leaves zero free hands. PUSH is included because only its `movable` check is gated by `action_name != "push"`; the free-hand call is not. |

## Reading notes

**Why `sdg.py` isn't a column of its own.** `sdg.py` is a separate precondition model serving the repair loop, not the prompt. It expresses requirements as runtime conditions (`holds_obj`, `not_both_hands_full`) rather than object properties, so the two aren't directly comparable row by row — PUTBACK and PUTIN are the clearest case, where this table says `GRABBABLE` and `sdg.py` says `holds_obj` for the same constraint. Both are checked against the same arbiter, `execution.py`. The current state of that model is documented in `sdg_pddl_comparison.md`.

**Where the baseline was already right.** Four entries (GREET, PLUGIN, TOUCH, and PUTOBJBACK's absence) match the upstream baseline exactly. That is worth stating explicitly rather than leaving as a silent non-entry: each was checked against its Executor class and confirmed correct, and in PLUGIN's case the baseline is right *despite* contradicting the PDDL.

**Caveat.** All of this is verified by reading Executor source directly, not by an empirical A/B eval run. The WIPE and Rule 5 fallback-list removals in particular are inferred to be behaviorally neutral. Worth a real A/B run before treating that as fully settled.

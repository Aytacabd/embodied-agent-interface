# Technical Report: "Highest Results" Commit vs Current Working Tree

**Subject:** Complete comparison of the committed SDA-Planner baseline (`dff077a`,
"last update highest restuls") against the current uncommitted working tree.
**Component:** SDA-Planner adaptive replanning pipeline (`sda_last_hope_modified/`)
over the VirtualHome action-sequencing track of the Embodied Agent Interface (EAI).

| | Value |
|---|---|
| Baseline commit | `dff077a` — "last update highest restuls" (HEAD) |
| Comparison target | Current working tree (uncommitted) |
| Files changed | 8 (`+785 / −384` lines) |
| Output format | **Unchanged** — interleaved `[name, id]` JSON (grader-compatible) |
| Simulator (`execution.py`) | **Reverted to HEAD** — identical, not a difference |

---

## 1. Executive Summary

The working tree differs from the "highest results" commit by a large body of
uncommitted engineering. It is best understood as three layers stacked on top of
the committed baseline:

1. **An executor-alignment overhaul** of the diagnosis stack (SDG, state model,
   diagnosis, BFS) that replaces the earlier PDDL-derived assumptions with rules
   that match the actual VirtualHome executors in `execution.py`.
2. **A prompt rewrite** that condenses the instructions, corrects several action
   affordances, and adds explicit precondition rules.
3. **A set of targeted correctness and efficiency fixes** made during recent
   debugging sessions, each addressing a concrete failure observed in run logs.

Crucially, none of this changes the **predicted-plan output format** (still the
interleaved `[name, id]` JSON that made `dff077a` the "highest results" commit),
and the **simulator/grader (`execution.py`) was reverted** so it is byte-identical
to the baseline. Therefore the working tree remains directly comparable to the
baseline under the official grader: the differences affect *plan quality and
repair*, not *how plans are scored*.

---

## 2. Background: What the Baseline Commit Contains

`dff077a` is the commit whose key contribution was a **serialization fix**: it
emits predicted plans in the interleaved `[name, id]` format the EAI grader parses
with `len(params) // 2`, instead of the combined `name_id` single-token format that
the grader rejected as "parameter errors." That fix is what produced the
"highest results" label.

At that commit, however, the surrounding diagnosis modules
(`error_diagnosis.py`, `sdg.py`, `object_state_model.py`, `action_subtree.py`)
were still in an **earlier, PDDL-derived state**. For example, the committed SDG
modeled `FIND` with `needs=["next_to_obj"]` and no effect, `POINTAT` requiring
`next_to_obj`, `PUTON` requiring only `holds_obj`, and `DROP` requiring
`obj_inside_room`. Several of these do not match the real executor behavior. The
working tree corrects all of them (Section 5).

---

## 3. Overview of Working-Tree Changes

| File | Δ lines | Nature of change |
|---|---|---|
| `eai_sda_runner_tree.py` | ~486 | Runner loop, goal-string builder, replanning, recent fixes |
| `error_diagnosis.py` | ~186 | Per-object diagnosis, strategy selection, recent guards |
| `sdg.py` | ~159 | 42-action precondition/effect table aligned to executors |
| `one_shot.py` | ~114 | Prompt rewrite + corrected affordances + rules |
| `object_state_model.py` | ~103 | Per-object state tracking, smart defaults, recent fixes |
| `action_subtree.py` | ~89 | BFS repair search, candidate generation, recent fixes |
| `error_diagnosis_tree.py` | ~30 | Threads `initial_env_dict` to diagnosis |
| `eval_utils.py` | 2 | Inert `SQEEZE`→`SQUEEZE` typo (never read; no effect) |

A note on authorship: this diff is a **mix** of pre-existing uncommitted work
(the bulk of the overhaul and the prompt rewrite) and the targeted fixes made in
recent sessions (detailed in Section 6). None of it is committed yet.

---

## 4. The Output Format Is Preserved

The single most important property for benchmark integrity: the working tree keeps
the baseline's interleaved `[name, id]` output (`plan_to_json_str` still emits
`"WALK": ["light", "245"]`), and `json_to_action` still accepts that format. The
grader's `len(params) // 2` argument-count check therefore still passes for these
plans. Combined with the reverted `execution.py`, this means the working tree is
scored by exactly the same machinery as `dff077a` — the comparison is fair.

---

## 5. File-by-File Analysis

### 5.1 `one_shot.py` — Prompt
The verbose multi-section prompt was condensed and corrected. Affordance fixes that
now match the executors: `TOUCH` no longer requires READABLE (only proximity);
`GREET` requires the PERSON property; `WIPE` requires holding a wiping tool (not the
surface); `SQUEEZE` accepts CLOTHES or a squeezable class; `PLUGIN`/`PLUGOUT` use
HAS_PLUG. Fifteen explicit rules encode preconditions in prose, including the
recently added rule 6 (TURNTO before WATCH/LOOKAT/POINTAT), rule 13 (PLUGIN before
SWITCHON when PLUGGED_OUT), rule 14 (SWITCHOFF before OPEN on switchable
appliances), and rule 15 (SIT/LIE before SLEEP/WAKEUP). The worked example now
demonstrates duplicate keys (`WALK … GRAB … WALK … PUTBACK`) and uses a listed
surface (`table`). This prompt is materially more hint-rich than the published EAI
prompt — see Section 9 on comparability.

### 5.2 `sdg.py` — State Dependency Graph
The precondition/effect table for all 42 actions was re-derived from the executors
rather than the PDDL. Representative corrections vs the baseline: `FIND` now
`needs=["not_sitting","not_lying"]`, `effects=["next_to_obj"]`, `is_prep=True`
(auto-navigation); `POINTAT` now `needs=["facing_obj"]`; `PUTON` now requires
`["holds_obj","clothes"]`; `DROP` drops the `obj_inside_room` requirement the
executor does not enforce. The recent CUT change adds `holds_knife` to its
preconditions, with a matching explanation string and updated self-test
expectations.

### 5.3 `object_state_model.py` — Per-Object State
Builds a per-object state model from the environment graph with "smart defaults"
sourced from `object_states.json` (devices default OFF/PLUGGED_IN, containers
default CLOSED), and a container fix so room-membership edges are not mistaken for
container access. Recent additions: `_clear_char_facing()` invoked by WALK/RUN,
FIND, and TURNTO (mirrors the executors, which delete FACING edges on movement),
and `holding_knife()` plus a `holds_knife` predicate for CUT.

### 5.4 `error_diagnosis.py` — Diagnosis Engine
Implements the per-object precondition analysis, root-cause/window computation, and
strategy selection (`local`, `insert_prep`, `reconstruct`, `already_satisfied`,
`wrong_action`). Recent guards: an `EVALUABLE_EFFECTS` whitelist so the
`already_satisfied` shortcut only fires for effects the model can actually evaluate
(preventing valid PUTBACK/PUTIN/POUR from being deleted), and a **single-instance
guard** so `already_satisfied` does not fire when the object's class has multiple
instances (preventing the multi-television false positive). `holds_knife` is
registered as a dynamic precondition.

### 5.5 `error_diagnosis_tree.py` — Diagnosis Wrapper
Threads a new `initial_env_dict` argument through to `diagnose_error`, so root-cause
replay (`find_t_source`) starts from the true pre-execution state rather than a
blank model. Otherwise unchanged.

### 5.6 `action_subtree.py` — BFS Repair Search
Generates candidate repair actions and runs a bounded BFS to the target effects.
Recent additions: the search now binds spatial/global goal checks to the failing
action's own `failed_obj`/`failed_target` (so an unrelated WALK cannot falsely
satisfy a `next_to`/`hands-full` goal), and a `holds_knife` repair path that locates
a knife in the scene and emits WALK + GRAB candidates.

### 5.7 `eai_sda_runner_tree.py` — Main Runner
The largest change. Contains the runner-local goal-string builder
(`build_id_aware_goal_strings`), the multi-provider LLM client, plan parsing and
validation, the execute → diagnose → repair → re-execute loop, and result saving.
Recent fixes layered on top:
* `MAX_TOKENS = 1024` (initial plans were truncating at 512).
* `MAX_FREE_REMOVALS` budget so plan cleanups (already-satisfied / affordance
  skips) no longer consume the 3 LLM repair attempts.
* Plan-space index tracking (`executed_plan_indices`, `failed_plan_idx`) so that
  skipped actions no longer desynchronize splice/remove positions.
* Ambiguity tiebreak (`preferred=relevant_name_to_id`) when resolving repair
  objects with duplicate class names.
* Goal-coverage character exclusion (removes a wasted retry that fired on nearly
  every character-state goal task).
* Double-id parse guard (`toilet_1000` + `1000` no longer becomes
  `toilet_1000_1000`).

### 5.8 `eval_utils.py` — Shared Utility
Single change: `("SQEEZE", 1)` → `("SQUEEZE", 1)`. Element 0 of this tuple is never
read (only `.keys()` and element 1, the arg count, are used), so this is inert and
does not affect grading.

### 5.9 `execution.py` — Simulator (Reverted)
Earlier the working tree reclassified two executor failures (OPEN-while-ON and
SWITCHON-while-unplugged) from "satisfied" to "missing." Because the official
grader treats those codes differently (code 4 is skipped; code 1 is a hard
executability failure), that change altered scoring. It was **reverted** to keep
the baseline comparable, so `execution.py` is now identical to HEAD and is not part
of this diff.

---

## 6. Recent Correctness & Efficiency Fixes (Deep Dive)

These are the fixes made during recent debugging, each tied to an observed failure:

1. **`already_satisfied` over-deletion.** `ObjectStateModel.satisfies()` returns
   True for unknown predicates, so failed PUTBACK/PUTIN/POUR (whose effects are
   unknown predicates) were misdiagnosed as already satisfied and silently deleted.
   Fixed with the `EVALUABLE_EFFECTS` whitelist.
2. **Multi-instance false positive.** With two televisions, class-level state
   aggregation made the model believe the target TV was already on; the executor
   disagreed (MISSING_STEP). Fixed with the single-instance guard.
3. **Plan-space vs history-space indices.** After a skipped action, the diagnosis
   indices and plan slicing diverged, removing or duplicating the wrong steps.
   Fixed by tracking true plan indices.
4. **BFS goal binding.** Repairs could terminate on an unrelated object satisfying
   a generic goal (e.g. a WALK to the wrong thing). Fixed by binding checks to the
   failing object.
5. **CUT knife requirement.** The executor requires a held knife; the SDG omitted
   it, so knife-less CUT failures produced empty diagnoses. Fixed across SDG, the
   state model, diagnosis, and BFS.
6. **FACING not cleared on movement.** Executors delete FACING on WALK/FIND/TURNTO;
   the model retained it, hiding the real cause of WATCH/LOOKAT failures. Fixed.
7. **Token truncation.** Long initial plans were cut at 512 tokens; raised to 1024.
8. **Replan budget.** Free cleanups no longer consume repair attempts.
9. **Goal-coverage character exclusion.** The character id is never an argument, so
   character-state goals always triggered a futile retry; now excluded.
10. **Double-id parse guard.** Prevents a lost task when the model echoes the id
    inside the name.

---

## 7. Behavioral Impact Summary

* **Higher plan quality at generation time** from the richer, executor-accurate
  prompt — fewer initial-plan precondition violations.
* **More reliable repair** — the diagnosis no longer deletes valid goal actions,
  mis-splices around skipped steps, or flails on knife/facing cases.
* **Fewer wasted API calls** — the goal-coverage retry no longer fires uselessly on
  character-state goals, and the token limit prevents truncated retries.
* **No change to scoring** — same output format, same reverted simulator, so any
  score difference reflects genuinely better plans, not a changed metric.

---

## 8. Verification Status

All module self-tests pass in the working tree: `sdg.py` (precondition table),
`object_state_model.py` (container/grab/open), `error_diagnosis.py` (both scenario
tests), and `error_diagnosis_tree.py`. Targeted checks were run for each recent fix
(already-satisfied both directions, multi-instance guard, BFS binding, CUT knife,
FACING clearing, goal-coverage exclusion, double-id parse).

---

## 9. Risks, Caveats, and Integrity

* **Prompt comparability.** The working-tree prompt is substantially more
  prescriptive than the published EAI prompt. Comparing against published baseline
  numbers would be apples-to-oranges; any baseline must be regenerated with the
  same prompt so the only variable is the SDA loop.
* **Class-level state aggregation (deeper limitation).** The single-instance guard
  contains the worst symptom, but multi-instance scenes still diagnose at class
  level (closeness, states). A full fix requires instance-aware diagnosis (carry
  the id through `parse_eai_action` and key the state model by id).
* **Deployment.** These edits live in the repo; the runner imports
  `virtualhome_eval` from the installed package, so the `src/` edits must be synced
  into the deployment environment to take effect.
* **Uncommitted.** Everything here is in the working tree only; nothing is committed
  on top of `dff077a`.

---

## 10. Recommended Next Steps

1. Commit the working tree in logical groups (prompt; diagnosis overhaul; recent
   fixes) with clear messages, so the "highest results" baseline and the improved
   pipeline are distinguishable in history.
2. Run experiment 1 with the current (rich) prompt, then experiment 2 with the
   original prompt regenerated through the same pipeline, keeping the simulator
   identical for both.
3. If multi-instance tasks are common in the dataset, schedule the instance-aware
   diagnosis refactor (Section 9) as a follow-up.

---

## Appendix: Change Inventory

| Category | Files | Status |
|---|---|---|
| Prompt rewrite + rules | `one_shot.py` | pre-existing + recent rules 6/13/14/15 |
| Executor-aligned SDG | `sdg.py` | pre-existing + CUT knife |
| State model | `object_state_model.py` | pre-existing + FACING + knife |
| Diagnosis engine | `error_diagnosis.py` | pre-existing + EVALUABLE/single-instance/knife |
| Diagnosis wrapper | `error_diagnosis_tree.py` | pre-existing (initial_env_dict) |
| BFS repair | `action_subtree.py` | pre-existing + obj binding + knife |
| Runner | `eai_sda_runner_tree.py` | pre-existing + token/budget/index/tiebreak/coverage/double-id |
| Shared util | `eval_utils.py` | inert typo |
| Simulator | `execution.py` | reverted to HEAD (no diff) |

# Technical Specification — SDA-Planner adapted to the EAI Action Sequencing module (VirtualHome)

Scope: this document describes **implemented behaviour only**, read directly from
source. Where a docstring, comment or README disagrees with the code, both are
stated and the authoritative one is named. Where the code does not determine an
answer, the gap is marked `[UNVERIFIED: ...]` rather than inferred.

Section 9 additionally compares the implementation against the SDA-Planner paper
(Shen et al., arXiv:2509.26375v1, 30 Sep 2025), which was read directly; quotations there
are verbatim.

The active implementation is the directory `sda_last_hope_modified/`. The repository
contains ~10 sibling directories (`sda_eai/`, `sda_final/`, `sda_planner/`,
`sda_first_version_without_tree/`, `sda_second_try/`, `sda_third_try/`,
`sda_last_hope/`, `sda_last_hope2/`, `sgd_first/`) which are earlier iterations and
are **not** on the current execution path. Nothing in this document refers to them
unless explicitly named.

---

## 1. Repository map

### 1.1 The adapted planner — `sda_last_hope_modified/` (all files NEW in this thesis)

Core pipeline (imported at runtime):

| File | Lines | Role |
|---|---|---|
| `sda_last_hope_modified/eai_sda_runner_tree.py` | 2380 | Main runner. LLM client, all prompt constants, plan parsing/normalisation, the repair loop, goal guard, goal completion, output serialisation. Also defines `NoAdaptRunner` (ablation arm). |
| `sda_last_hope_modified/sdg.py` | 541 | The State-Dependency table: a static `SDG` dict of 42 actions → `needs` / `effects` / `is_prep`, plus `PRECONDITION_EXPLANATIONS` and the accessors `get_preconditions`, `get_effects`, `is_prep_action`, `explain_precondition`. |
| `sda_last_hope_modified/object_state_model.py` | 713 | `ObjectStateModel`: per-instance world-state representation built from a VirtualHome scene-graph dict; predicate evaluation (`satisfies`, `check_all`) and effect application (`apply`). |
| `sda_last_hope_modified/error_diagnosis.py` | 603 | `ActionStep`, `DiagnosisResult`, `StateTracker`, `_find_container_in_env`, and `diagnose_error` — the strategy-selection cascade and the `[t_start, t_end]` window computation. |
| `sda_last_hope_modified/error_diagnosis_tree.py` | 110 | Thin wrapper `diagnose_error_tree` adding `original_subsequence` and `error_objects`; `get_unsatisfied_explanation` renders unsatisfied needs for prompts. |
| `sda_last_hope_modified/action_subtree.py` | 750 | Adaptive Action SubTree: candidate generation, guaranteed-candidate injection, BFS (`build_and_search_tree`), the Eq.-5/Eq.-6 predicates (`satisfied`, `changes_state`, `not_covered`), entry point `generate_replacement_subsequence`. |
| `sda_last_hope_modified/object_states.json` | 313 entries | VirtualHome per-class state catalogue; read by `object_state_model.py` to derive property defaults. |

Run connectors (each monkey-patches config globals on `eai_sda_runner_tree` and then
instantiates a runner; none duplicates planner logic):

| File | Lines | Role |
|---|---|---|
| `sda_last_hope_modified/eai_sda_runner_hard.py` | 135 | Full SDA pipeline against the 50 hand-authored hard tasks (ids `9001_1`–`9050_1`). Overrides `TASK_DICT_PATH`, `ID2TASK_PATH`, `MODEL`, `MODEL_NAME`, `MAX_REPLAN`, `OUTPUT_DIR`. |
| `sda_last_hope_modified/eai_sda_runner_noadapt.py` | 187 | "w/o adaptation" ablation on the full EAI task set (uses `NoAdaptRunner`). |
| `sda_last_hope_modified/eai_sda_runner_hard_noadapt.py` | 206 | "w/o adaptation" ablation on the hard-50 set. |
| `sda_last_hope_modified/eai_sda_runner_tree_goalfix_test.py` | 55 | Tag-isolated connector for verifying the goal-completion pass on a fixed task-id list. |

Offline scoring / analysis utilities (no LLM calls, not part of the planner):

`eval_tag.py` (44) scores one output tag; `eval_main_bo.py` (44) scores every tag in the
output dir; `eval_hard50_one_budget.py` (99) scores one budget-sweep tag and appends a CSV
row; `combine_bo_attempts.py` (132) joins best-of-k attempt sweeps; `compare_goalfix.py` (66)
before/after comparison on a task subset; `analyze_budget_stabilization.py` (114) reconstructs
task success at budgets 1–10 from a single budget-10 run; `parse_diagnosis_stats.py` (160)
parses a runner log into per-diagnosis statistics; `rescore_all_charguard.py` (108) re-scores
saved outputs with the patched evaluator; `run_hard50_budget_sweep.sh` and
`run_hard50_once_budget10.sh` are the shell drivers for the budget sweep.

Test file: `sda_last_hope_modified/test_character_guard.py` (130) — offline self-tests for the
character-reference guard (no API, no simulator).

**Dead files inside `sda_last_hope_modified/`** — present but imported by nothing:
`utils.py` (137 lines; the runner's `import ... utils` at `eai_sda_runner_tree.py:33` resolves to
`virtualhome_eval.simulation.evolving_graph.utils`, not this file), `base_environment.py`
(13 lines, an abstract `BaseEnvironment` never subclassed), `object_action_info.json`
(a curated container/surface/grabbable class list; no reader).

### 1.2 New task-set generator

| File | Role |
|---|---|
| `difficult_tasks/generate_difficult_tasks.py` | Authors 50 hard tasks (`9001_1`–`9050_1`, scene 1) and writes their resource JSONs. NEW. |
| `difficult_tasks/resources/virtualhome/task_state_LTL_formula_accurate.json` | Generated task/goal registry for the hard set. NEW. |
| `difficult_tasks/resources/virtualhome/id2task.json` | Generated id→task-name map for the hard set. NEW. |
| `src/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds/init_and_final_graphs/TrimmedTestScene1_graph/results_intentions_march-13-18/file90*.json` (50 files) | Generated init/final scene graphs. NEW. |
| `.../executable_programs/TrimmedTestScene1_graph/results_intentions_march-13-18/file90*.txt` (50 files) | Generated gold scripts. NEW. |

### 1.3 Modified copies of EAI Benchmark files

These are modified **in place**; the upstream original of each is the same path at git
commit `531c62f` (2025-03-05, `update bddl_to_tl function`), the last upstream commit
before thesis work began at `73b7812` (2026-03-24).

| Path (also the upstream original path) | What changed |
|---|---|
| `src/virtualhome_eval/evaluation/action_sequencing/prompts/one_shot.py` | The baseline planning prompt. Rewritten — see §7. |
| `src/virtualhome_eval/evaluation/action_sequencing/scripts/evaluate_results.py` | Evaluator harness. Five changes — see §8. |
| `src/virtualhome_eval/simulation/evolving_graph/eval_utils.py` | `valid_actions` extended; `load_json_preserving_order` regex; `json_to_action` accepts combined `name_id` tokens; `scene_evaluate_wID` edge-goal `break` removed. See §8. |
| `src/virtualhome_eval/agent_eval.py` | Legacy: the `action_output_evaluation` import is commented out and replaced by `sys.path.insert(0, "/Users/aytaj/Desktop/embodied-agent-interface/sda_final")` + `from evaluate_results_sda import evaluate_results`. That absolute path does not exist in this repository. **Not on the current execution path** — `eval_tag.py` / `eval_main_bo.py` call `evaluate_results` directly. Listed for completeness; see §10. |
| `src/virtualhome_eval/evaluation/subgoal_decomposition/prompts/helm_prompts.json` | Belongs to a different EAI module (subgoal decomposition), not action sequencing. Not used by this pipeline. |

### 1.4 Unmodified benchmark files the pipeline depends on

Verified unchanged since the upstream commit `70bdf29` (2024-09-22):

- `src/virtualhome_eval/simulation/evolving_graph/execution.py` — the VirtualHome executor. It is the authority the SDG was audited against (§3.5); no change was made to it.
- `src/virtualhome_eval/simulation/evolving_graph/motion_planner.py` — `MotionPlanner`, `my_execute_primitive_action_eval`, `reset`, `env_state`, `acting_char_id`. Its `relevant_name_to_id[f"{class_name}_{id}"]` keying is **upstream** (commit `b2038c0`, comment `# edit by shiwenxuan`), not a thesis change.
- `src/virtualhome_eval/simulation/evolving_graph/checker.py` — `TemporalOrderChecker`. Its empty-precondition guard is **upstream** (commit `d502f3a`, 2025-01-12).
- `src/virtualhome_eval/resources/virtualhome/virtualhome.pddl` — the PDDL domain used as the primary specification for `sdg.py`.

---

## 2. The repair loop

### 2.1 Entry point and call chain

Entry point: `EAISDATreeRunner.run_all(max_tasks, task_ids)` at
`eai_sda_runner_tree.py:1504`. It loads `TASK_DICT_PATH` for `scene_{SCENEGRAPH_ID}`,
resumes from an existing `{MODEL_NAME}_outputs.json` (skipping identifiers whose
`llm_output` is not `""` or `"..."`), and calls `run_single_task` per task, sleeping 1 s
between tasks and check-pointing every 10 tasks.

The loop proper is `EAISDATreeRunner.run_single_task(file_id, task_name, task_goal_dict)`
at `eai_sda_runner_tree.py:1569`. Call chain, in execution order:

```
run_single_task
├── construct_planner(...)                          → MotionPlanner  (EAI, unmodified)
├── build_id_aware_goal_strings(...)                → prompt fragments + relevant_name_to_id
├── one_shot.prompt  (placeholder substitution)     → base_prompt
├── LLMClient.call(base_prompt, label="INITIAL PLAN")
├── parse_and_validate(..., char_guard="reject")    → EAI action strings
│   └── [on failure] _build_retry_prompt → one retry → parse_and_validate(char_guard="strip")
└── while True:                                     ← the repair loop
    ├── motion_planner.reset()
    ├── for action in current_plan_eai:  my_execute_primitive_action_eval(action)
    │   └── on failure: TemporalOrderChecker(my_info, history_cp).run_checker().get_error_type()
    ├── [executable] → goal guard → scene_evaluate_wID → _attempt_goal_completion → break
    ├── [budget check] → break
    ├── diagnose_error_tree(...)      (error_diagnosis_tree → error_diagnosis.diagnose_error)
    ├── window computation (hist_pos_to_plan_pos, root-cause exclusion)
    ├── strategy dispatch:
    │   ├── already_satisfied → delete the action, continue
    │   ├── wrong_action      → WRONG_ACTION_PROMPT → LLM → splice, continue
    │   └── otherwise         → SUGGESTION_PROMPT → LLM
    │                          → generate_replacement_subsequence (BFS)
    │                          → subtree_results_to_eai → splice
    └── loop
```

Every attempt re-executes the **whole** current plan from `motion_planner.reset()`. There
is no incremental execution and no state rollback (see §6.4).

### 2.2 Where the attempt budget is defined and what it counts

`MAX_REPLAN = 3` at `eai_sda_runner_tree.py:92`. The hard connector overrides it:
`eai_sda_runner_hard.py:63-68` reads the `HARD_MAX_REPLAN` environment variable, defaults
to `3`, and appends `_r<budget>` to `MODEL_NAME` when the override is set.

The counter is `replan_count`, initialised to 0 at `eai_sda_runner_tree.py:1624`. It is
incremented in exactly two places, both immediately before an LLM repair call:

- `eai_sda_runner_tree.py:2041` — before the `WRONG ACTION FIX` call.
- `eai_sda_runner_tree.py:2086` — before the `SUGGESTION (replan N)` call.

So the budget counts **LLM repair calls**, not replans-as-iterations and not diagnoses.
The following consume an iteration but **not** budget:

- `already_satisfied` removal (`:2011-2018`) — deletes the action and `continue`s.
- Tree-exhausted drop (`:2150-2163`) — deletes the action and `continue`s.
- The repeat-failure `wrong_action` drop (`:2023-2038`) — deletes the action and `continue`s.
- `if not new_subseq: continue` (`:2165`) — but here `replan_count` was already charged
  by the suggestion call above it.

One LLM call is genuinely **not** charged: the whole-plan fallback inside the
`wrong_action` branch (`:2069-2074`, `fallback_count += 1`, `self.llm.call(base_prompt)`)
runs after `replan_count` was already incremented once for that iteration, so a single
`wrong_action` iteration can issue two LLM calls against one unit of budget.

The comment at `:1664-1666` states the intent explicitly: *"removals (already_satisfied /
loop-breaker drops) do NOT consume the repair budget — only actual repair attempts (LLM
calls) do."* The code matches the comment.

### 2.3 The second limit, and what happens on exhaustion

```python
attempt = -1
max_total_iters = MAX_REPLAN + len(current_plan_eai) + 4
...
if replan_count >= MAX_REPLAN or attempt >= max_total_iters:
```
(`eai_sda_runner_tree.py:1667-1668`, `:1849`)

`max_total_iters` is the hard cap on loop iterations, guarding against a cascade of
budget-free removals. It is computed **once**, before the loop, from the length of the
*initial* plan; it is not recomputed as the plan grows or shrinks during repair.

Termination conditions, in the order they can fire:

1. **Success** — the plan executes with no unrecoverable failure → the `if executable:`
   branch (`:1738`) runs the goal guard and goal-completion pass, sets
   `raw_output = plan_to_json_str(clean_plan)`, and `break`s.
2. **Budget or iteration cap** (`:1849`) — logs `⚠️ Max replanning reached` and saves
   `plan_to_json_str(history_actions)`, i.e. **the actions that actually executed in the
   final attempt**. The un-attempted spliced plan that `raw_output` held at that point is
   logged and discarded. (Before this was corrected, the spliced plan was what got saved;
   see §9.16.)
3. **Diagnosis exception** (`:1902`) — any exception from `diagnose_error_tree` is caught
   and logged, and the same executed-prefix save is applied before `break`ing.

Both non-success exits emit an additional `⚠️ EMPTY EXECUTED PREFIX` warning when
`history_actions` is empty, because `plan_to_json_str([])` is `"{}"` and the evaluator
treats that as a parsing error (§9.16).

Two further ways a task terminates before the loop is entered at all:
`construct_planner` failure returns `("", 0, 0, 0)` (`:1594`), and an initial plan that
fails to parse twice returns the raw LLM text unparsed (`:1655-1657`).

`run_all` can also stop the whole sweep early via `max_tasks` (`:1533`).

### 2.4 The completion / verification pass after the loop's success branch

Two distinct passes run inside `if executable:`, before `break`, in this order.

**(a) The goal guard** (`:1744-1797`). Candidate set = actions that were skipped as
`ADDITIONAL_STEP` or `UNSEEN_OBJECT` during this attempt (`skipped_indices`) plus
`deferred_goal_actions` (actions deleted by a repair drop). For each candidate,
`goal_state_action_pair(act, goal_state_pairs)` returns `(obj_id, STATE)` if the action's
verb maps to a state in `_GOAL_STATE_EFFECTS` = `{CLOSE→CLOSED, OPEN→OPEN, SWITCHON→ON,
SWITCHOFF→OFF, PLUGIN→PLUGGED_IN, PLUGOUT→PLUGGED_OUT}` **and** that pair is one of the
task's node goals. If the goal is still unmet in the current env, the runner builds
`WALK <object>` + the original action, executes both against `motion_planner`, and appends
them to the plan **only if the whole pair succeeds**. This relocates goal-achieving
actions the LLM itself produced; it never invents new ones.

**(b) The generalised goal-completion pass** (`:1799-1836` → `_attempt_goal_completion`
at `:938`). It calls `scene_evaluate_wID` — the same function the offline evaluator
scores with — on the current env, and if `all_goals_ok` is false, hands the unsatisfied
node / edge / action goals to `_attempt_goal_completion`. That function is explicitly
**not a search** (docstring at `:955-967`): for each goal shape it builds the one obvious
satisfying sequence, executes it for real against `motion_planner`, and commits only if
the whole sequence succeeds. Coverage:

- Node/state goals: `_STATE_TO_ACTION[state]` (inverse of `_GOAL_STATE_EFFECTS`), preceded
  by `WALK`. States with no single achieving action (e.g. `DIRTY`) are skipped.
- Edge goals `ON` / `INSIDE` where `from_id == acting_char_id`: a posture goal. The posture
  is taken from the character's own unsatisfied node goal (`LYING`→`LIE`, `SITTING`→`SIT`);
  with no such goal it falls back to `SIT` for `ON` and `LIE` for `INSIDE`.
- Edge goals `ON` / `INSIDE` otherwise: a placement. Opens the object's source container
  (and `SWITCHOFF`s it first if it is running), fetches, opens the destination if `CLOSED`,
  places with `PUTBACK` for `ON` / `PUTIN` for `INSIDE`, then **restores** every container it
  opened (`CLOSE`) or switched off (`SWITCHON`) in a separate `_run` so a restore failure
  does not discard the placement.
- Edge goal `CLOSE`: a `WALK`. Edge goal `FACING`: `WALK` + `TURNTO`.
- Edge goals `HOLDS_RH` / `HOLDS_LH`: `WALK` + `GRAB`. The comment at `:1189-1195` records
  that `GRAB` cannot request a specific hand, so an exact-hand goal may remain unmet.
- Other relation types (`BETWEEN`, …) are left unsatisfied rather than guessed at.
- Action goals: `OR`-expressions are split on `|`; zero-arg verbs are executed directly;
  otherwise one LLM call (`ACTION_GOAL_PROMPT`, label `ACTION GOAL OBJECT`) asks for the
  target object. Verbs whose `_eai_valid_actions` arity is not 1 are skipped outright
  (`:1243-1256`) rather than committing a dangling `WALK`.

`walked_to` memoises the character's current location across sub-passes within one call;
it is cleared and re-set on every executed `WALK`. The `ON`/`INSIDE` placement branch
deliberately does **not** use it (comment at `:1073-1088`).

Neither pass is budget-limited, and the `ACTION GOAL OBJECT` call is not counted in
`replan_count` or `fallback_count`.

---

## 3. State-Dependency Graph

### 3.1 Where the graph lives and its data structures

There is **no graph object**. The State-Dependency Graph is realised as a static
module-level dictionary, `SDG`, in `sda_last_hope_modified/sdg.py`:

```python
SDG = {
    "GRAB": {
        "needs":   ["next_to_obj", "grabbable", "not_both_hands_full",
                    "obj_not_inside_closed_container"],
        "effects": ["holds_obj"],
        "is_prep": False,
    },
    ...
}
```

42 entries, one per VirtualHome script verb. It is a literal in the source file — it is
never built, extended or mutated at runtime. The accessors are `get_preconditions(action)`
→ `SDG[a]["needs"]` (docstring: *"Return Sdep[a] — preconditions for action a (paper
Section 4.2)"*), `get_effects(action)` → `SDG[a]["effects"]` (*"Return Seff[a]"*), and
`is_prep_action(action)` → `SDG[a]["is_prep"]`. Unknown actions return `[]` / `False`
(`.get(...)` with defaults), never raise.

The *state* side — the thing the SDG's predicates are evaluated against — is
`ObjectStateModel` (`object_state_model.py`), with these fields:

| Field | Type | Contents |
|---|---|---|
| `object_states` | `{token: set[str]}` | Per-instance union of VirtualHome states and properties, upper-cased, e.g. `{"fridge_2": {"CLOSED", "PLUGGED_IN", "CAN_OPEN"}}` |
| `object_states_by_id` | `{int: set[str]}` | Alias view onto the *same* set objects, keyed by node id |
| `id_to_name` | `{int: str}` | node id → class name |
| `name_to_ids` | `{str: list[int]}` | class name → all its instance ids |
| `relations` | `{(from_token, to_token): set[str]}` | e.g. `{("character","fridge_2"): {"CLOSE"}}` |
| `container_of` | `{token: token}` | direct container of an object |
| `hand_right`, `hand_left` | `str \| None` | token held in each hand |
| `worn` | `set[str]` | tokens currently `ON` the character |
| `char_sitting`, `char_lying` | `bool` | posture |

During search, `action_subtree.TreeState` is a thin wrapper over one `ObjectStateModel`
(`copy`, `apply`, `satisfies`, `achieves`), and `TreeNode` holds `(action, obj, target,
parent, state, depth)`. The frontier is a `collections.deque`.

### 3.2 Where preconditions and effects come from

Three distinct sources, each supplying a different part:

**(a) The `needs` / `effects` lists themselves — hardcoded in `sdg.py`.**
The module docstring names the two authorities: *"Based on the uploaded VirtualHome PDDL
as the primary specification"* — i.e. `src/virtualhome_eval/resources/virtualhome/virtualhome.pddl`
— *"extended to cover the 42-action VirtualHome script vocabulary"*, and a second pass of
direct reads of `src/virtualhome_eval/simulation/evolving_graph/execution.py`, of which the
docstring says *"The executor is the arbiter in all of these"*. Every non-PDDL entry carries
an inline comment naming the executor class and method it was derived from (e.g.
`OpenExecutor.check_openable`, `PlugExecutor.check_plugable`, `_find_free_hand`,
`MoveExecutor.check_movable`, `TouchExecutor.check_reachable`, `GreetExecutor`,
`CutExecutor.check_cuttable`, `SqueezeExecutor.check_squeezable`, `WipeExecutor.check_wipe`,
and `execution.py:2270` for `PointAtExecutor = LookAtExecutor`).

**(b) The truth of those predicates at a given moment — the VirtualHome scene graph.**
`ObjectStateModel.from_env_dict(env_dict, char_sitting, char_lying)` consumes the dict
returned by `motion_planner.env_state.to_dict()`, i.e. `{"nodes": [...], "edges": [...]}`.
Pass 1 reads `class_name`, `id`, `states`, `properties` per node; pass 2 reads
`from_id` / `to_id` / `relation_type` per edge into `relations`, `container_of`,
`hand_right` / `hand_left` (from `HOLDS_RH` / `HOLDS_LH`) and `worn` (from `X ON character`).

**(c) Property defaults — a hardcoded catalogue, `object_states.json` (313 classes).**
Loaded at import time into four frozensets:

```python
_CAN_ON_OFF  = {k for k,v in _VH_OBJECT_STATES.items() if "on" in v and "off" in v}
_CAN_OPEN_CL = {k for k,v in _VH_OBJECT_STATES.items() if "open" in v and "closed" in v}
_CAN_PLUGGED = {k for k,v in _VH_OBJECT_STATES.items() if "plugged" in v or "unplugged" in v}
_CAN_GRAB    = {k for k,v in _VH_OBJECT_STATES.items() if "grabbed" in v}
```

plus a fifth, fully hardcoded: `_GRAB_CLASS_EXCEPTIONS = frozenset({"water", "child"})`,
commented as mirroring a hardcoded exemption in `GrabExecutor.check_grabbable`.

These drive the "smart defaults" block in `from_env_dict`: a node whose class is in
`_CAN_ON_OFF` (or which already carries `HAS_SWITCH`) is given `OFF` if it has neither
`ON` nor `OFF`, is given `PLUGGED_IN` unless it has `PLUGGED_OUT`, and has `HAS_SWITCH`
added to its property set. Similarly for `HAS_PLUG`, `CAN_OPEN` (default `CLOSED`), and
`GRABBABLE`. The stated reason (comment "FIX 1") is that `env_state.to_dict()` sometimes
omits properties for objects EAI derived from its catalogue rather than the scene, which
made `can_open` / `has_switch` checks silently return `False`.

There is one more hardcoded table, in the runner rather than the model: `CONTAINER_OBJECTS`
in `parse_and_validate` (`eai_sda_runner_tree.py:764-768`), a 14-class list used **only** as
the fallback `PUTBACK`→`PUTIN` heuristic when no edge goal covers the object/target pair.

### 3.3 Object-instance tracking

**Key format.** The canonical token is `"<class_name>_<id>"`, lower-cased and stripped —
e.g. `light_245`, `washing_machine_1001` — with the single special token `"character"` for
the agent. `ObjectStateModel._split_token` implements it as `^(.+)_(\d+)$`.

**Where identity is preserved:**

- `from_env_dict` keys `object_states` by token, and `relations` / `container_of` by
  token pairs. Duplicate classes stay distinct — the module's own self-test asserts
  `light_245` reads `OFF` while `light_246` reads `ON`.
- `resolve(obj)` with an exact token whose id is in `id_to_name` returns exactly one token.
- `parse_eai_action` (`eai_sda_runner_tree.py:457`) parses the EAI action string
  `[walk] <light> (245)` into `obj = "light_245"`; a second `<name> (id)` pair becomes
  `target`. Diagnosis and search therefore operate per instance.
- `action_subtree._combine_name_id` / `_split_name_id` carry tokens through candidate
  generation and BFS unchanged; `TreeState` passes them straight to `ObjectStateModel`.
- `subtree_results_to_eai` → `_resolve_to_name_id` → `json_to_action` keeps the token to
  the final EAI string.
- The runner builds a `full_name_to_id` map over **every** node in the scene
  (`:1610-1616`), not just goal-relevant ones, so a repair may reference e.g. the cabinet
  a goal object is inside.

**Where identity is lost — five places, all deliberate:**

1. **Plain class names resolve to every instance.** `resolve("light")` returns
   `["light_245", "light_246", ...]`. Queries are then *optimistic*: `has_state`,
   `has_relation`, `is_holding` all use `any(...)` over the resolved set, so a plain-name
   query is satisfied if **any** instance satisfies it. The module docstring states this is
   deliberate — *"same planning semantics as the old merged model"*.
2. **Plain-name mutation hits every instance.** `apply("OPEN", "fridge")` iterates
   `obj_toks` and opens all of them. The one exception is `GRAB`, which picks a single
   token (preferring one not already held).
3. **`get_container(obj)`** returns the container of the *first* resolved instance that has
   one, discarding the rest.
4. **Unknown ids and unknown names become their own keys.** `resolve` returns `[s]` when an
   id is not in `id_to_name` or a class name is not in `name_to_ids`. The docstring
   justifies this as keeping blank-model replays in `find_t_source` self-consistent.
5. **Nodes without an `id`** fall back to a bare class-name key (`token = name`).

`_split_token`'s regex would misparse any class name ending in digits. The runner's
`_normalize_name_id_token` comment asserts *"VH class names never end in digits"*; this
assumption is not enforced anywhere in code.
`[UNVERIFIED: whether any class in the VirtualHome vocabulary actually ends in a digit — the
assumption is stated in a comment, not checked.]`

At the output boundary, `_resolve_to_name_id` (`eai_sda_runner_tree.py:1286`) is strict: an
exact token in either map passes through; a plain class name resolves only if exactly one
instance matches (`re.compile(rf"^{re.escape(obj_name)}_\d+$")`, numeric suffix only, so
`light` never matches `light_bulb_31`); more than one match **raises `ValueError`** rather
than guessing. Only after that does it try a `difflib.get_close_matches(..., n=2,
cutoff=0.85)` fuzzy pass, accepted only when it yields exactly one class name that itself
resolves to exactly one instance (motivated by scene-graph misspellings such as
`coffe_maker`).

### 3.4 How state-preparation actions are identified

By an **explicit boolean flag**, not by graph topology. Each `SDG` entry carries
`"is_prep": True|False`, and `is_prep_action(action)` reads it. Exactly four actions are
flagged: **`WALK`, `RUN`, `FIND`, `TURNTO`**.

The flag is consumed in exactly one place: the backward extension of `t_start` in
`error_diagnosis.diagnose_error` (`:441-448`), which walks backwards from `t_source` and
keeps extending the window while the preceding steps are prep actions. It is not used by
the BFS, by candidate generation, or by strategy selection.

The paper defines the same notion topologically — "exactly one outgoing edge to an agent
state node and no incoming edges from other state nodes" (§4.2) — and applying that
definition to this repository's own `SDG` entries admits only `FIND` and `TURNTO`;
`WALK` and `RUN` carry `needs: ["not_sitting", "not_lying"]`, i.e. incoming edges, which the
definition forbids. The hardcoded list therefore contradicts the paper's rule on two of its
four members. See §9.2.

A second, separate table also encodes prep knowledge: `simple_prep` in
`error_diagnosis.diagnose_error` maps five predicates to `STANDUP`/`WALK`/`TURNTO`. It
disagrees with `SDG`: `STANDUP` appears there as a preparation action while carrying
`is_prep: False` in `SDG`.

### 3.5 Divergences from `virtualhome.pddl`

`virtualhome.pddl` defines **33 actions** and 45 predicates. `SDG` defines **42 actions**.
The mapping and every divergence follows.

#### 3.5.1 Actions added with no PDDL counterpart (10)

`RUN`, `POINTAT`, `PUTOBJBACK`, `PUTOFF`, `RELEASE`, `PUSH`, `PULL`, `GREET`, `RINSE`,
`SCRUB`. The docstring's stated reason is coverage of *"the 42-action VirtualHome script
vocabulary"* — the PDDL covers only part of what the executor accepts.

- `RUN` is given `WALK`'s entry verbatim; `RINSE`/`SCRUB` are given `WASH`'s.
- `POINTAT`'s entry is justified by `execution.py:2270`, `PointAtExecutor = LookAtExecutor`
  (a class alias), so it takes `facing_obj`, matching `LOOKAT`, rather than proximity.
- `PUTOFF` is `["on_char", "clothes"]` → `["not_on_char", "holds_obj"]`, from
  `PutOffExecutor.check_putoff`. An earlier commented-out `PUTOFF` block with empty `needs`
  is still present in the file above the live one (dead code).
- `PUTOBJBACK` is `["holds_obj"]` → `["not_holds_obj"]`. The docstring for `CUT`/`WATCH`
  section states its real executor precondition *"depends on a remembered grab-origin
  `sdg.py` doesn't track"*; §4 shows how the diagnosis layer compensates.

#### 3.5.2 Actions merged

`walk_towards` and `walk_into` are collapsed into one `WALK` entry with the union of
effects: `["next_to_obj", "inside_room"]`. PDDL keeps them as separate operators with
separate parameter types (`?obj` vs `?room`).

#### 3.5.3 Preconditions **added** relative to the PDDL

All are attributed in comments to a direct read of the executor, which the docstring names
as the arbiter.

| Action | Added | Executor justification quoted in `sdg.py` |
|---|---|---|
| `OPEN` | `not_both_hands_full` | `OpenExecutor.check_openable` calls `_find_free_hand`; "confirmed OPEN-only (not required for CLOSE, which shares the same class via a boolean flag)" |
| `PLUGIN`, `PLUGOUT` | `not_both_hands_full` | `PlugExecutor.check_plugable` calls `_find_free_hand` unconditionally for both directions |
| `MOVE`, `PUSH`, `PULL` | `not_both_hands_full` | `MoveExecutor.check_movable` calls `_find_free_hand` |
| `SQUEEZE` | `not_both_hands_full` | `SqueezeExecutor.check_squeezable` |
| `CUT` | `not_both_hands_full` | `CutExecutor.check_cuttable` |
| `PUTON` | `clothes` | `PutOnExecutor.check_puton` requires `Property.CLOTHES`; comment: without it "a `PUTON` on a non-clothes item fails in-env but diagnoses as `Unsat=[]`" |
| `PUTOFF` | `clothes` | `PutOffExecutor.check_putoff` |
| `GREET` | `person` | `GreetExecutor` checks `Property.PERSON` |
| `TOUCH` | `next_to_obj` | `TouchExecutor.check_reachable` checks `_is_character_close_to` |

#### 3.5.4 Preconditions **dropped** relative to the PDDL

| Action | Dropped | Reason in code |
|---|---|---|
| `FIND` | `next_to` (the PDDL's only precondition) | `FindExecutor` auto-navigates via `_walk_find_executor`; keeping it would be "circular at runtime" |
| `DROP` | `obj_inside_room` | "`DropExecutor.check_drop` checks ONLY holds — the room condition exists in the PDDL but was never implemented in the executor" |
| `PUSH` | `movable` | "`MoveExecutor.check_movable`'s movable-property check is explicitly gated by `action_name != \"push\"`" |
| `TOUCH` | `readable`, `holds_lh/rh` | "`TouchExecutor.check_reachable` checks NEITHER"; the entry "had been written by analogy to `READ`" |
| `GREET` | `next_to_obj` | "there is no proximity check anywhere in the class"; the entry "previously had this exactly backwards" |
| `PLUGIN` | the `has_switch` branch of `(has_plug OR has_switch)` | "`PlugExecutor.check_plugable` checks `HAS_PLUG` unconditionally; no `has_switch` branch exists in the executor" |

#### 3.5.5 Predicates renamed, role-split, or newly introduced

The PDDL's predicates are typed and variable-bound; `sdg.py`'s are flat strings whose
meaning is fixed by position (`obj` vs `target`). This forces four kinds of change:

- **Role splitting.** PDDL's single `(next_to ?char ?obj)` becomes two predicates,
  `next_to_obj` (checked against the action's first argument) and `next_to_target` (against
  the second). Likewise `obj_ontop` → `obj_ontop_target`, `obj_next_to` → `obj_next_to_target`,
  `obj_inside` → `obj_inside_target`, `(recipient ?obj2)` → `target_is_recipient`,
  `(inside ?char ?room)` → `inside_room`, `(ontop ?char ?obj)` → `ontop_obj`,
  `(facing ?char ?obj)` → `facing_obj`.
- **Hand merging.** PDDL's `holds_lh` / `holds_rh` become the single `holds_obj`
  (`is_holding` checks both hands). The pair's *conjunction* becomes `not_both_hands_full`,
  promoting the PDDL `grab` precondition
  `(not (and (exists (?obj3) (holds_lh ...)) (exists (?obj4) (holds_rh ...))))` into a named,
  reusable predicate.
- **Disjunctions promoted to atoms.** `pourable_or_drinkable` (from `pour`),
  `drinkable_or_recipient` (from `drink`), `sitting_or_lying` (from `standup`/`sleep`/
  `wake_up`), `target_open_or_not_openable` (from `put_inside`'s
  `(not (can_open ?obj2)) OR (open ?obj2)`), `has_plug_or_has_switch` (from `plug_in`),
  and `obj_not_inside_closed_container` (from the `grab`/`touch`/`move`/`watch` existential
  `(not (exists (?obj2) (and (obj_inside ?obj ?obj2) (closed ?obj2))))`).
- **Genuinely new predicates with no PDDL analogue.**
  `holding_anything` — introduced to model the PDDL `wipe` operator's *second* variable
  `?obj2`, which is a different object from the wiped surface. The comment records the
  concrete failure: `holds_obj(surface)` "is always false and routed `WIPE` failures to
  `wrong_action` (surface not grabbable)".
  `person` — added for `GREET`; the comment notes it "Required a new `person` branch in
  `object_state_model.satisfies`".

**PDDL predicates never used anywhere in `SDG`:** `has_paper`, `cream`, `body_part`,
`containers`, `cover_object`, `surfaces`, `between`, `dirty` (only its negation `not_dirty`
appears, as an effect), `hangable` and `pourable` (both have entries in
`PRECONDITION_EXPLANATIONS` but appear in no `needs` list).

#### 3.5.6 Executor requirements knowingly **not** modelled

The docstring lists these under *"Known remaining gaps (confirmed against the executor,
NOT corrected — each needs machinery this needs-list schema cannot express)"*:

- `CUT` also requires holding an object whose `class_name` contains `"knife"`. Stated in
  the prompt layer instead — `one_shot.py` Rule 9 and `SYSTEM_PROMPT` RULE 8.
- `WATCH` (same-room check), `SIT`/`LIE` (per-class occupancy caps), `WALK` (closed-door
  path blocking), `PUTOBJBACK` (remembered grab origin).
- `SQUEEZE`: the executor's real check is "a hardcoded list of squeezable items", broader
  than `CLOTHES`; the entry keeps `clothes` "as a simplification".
- `PLUGOUT`: the comment records that the executor's still-on check "sets an error message
  but never returns False (falls through to True) — looks like an upstream dead-code bug",
  and that keeping `not_on` is "the conservative choice: worst case it causes an
  unnecessary `SWITCHOFF`-before-`PLUGOUT` repair, never a false failure".

#### 3.5.7 The optimistic-default hole, and the whitelist that patches it

`ObjectStateModel.satisfies` ends with:

```python
# Unknown precondition — assume satisfied to avoid silent hard blocks
return True
```

Every predicate that appears in any `needs` list **is** explicitly handled. But 13
predicates that appear only in `effects` lists are not, and therefore always evaluate
`True`: `obj_ontop_target`, `obj_next_to_target`, `obj_inside_target`, `ontop_obj`,
`inside_room`, `clean`, `not_dirty`, `sitting`, `lying`, `not_closed`, `not_off`,
`not_plugged_in`, `not_plugged_out`.

This matters because two places query *effects* through `satisfies`. Both guard against it
with the same whitelist, defined twice:

- `error_diagnosis.diagnose_error`'s local `VERIFIABLE_EFFECTS` (`:332-337`) — gates the
  `already_satisfied` strategy.
- `action_subtree._VERIFIABLE_EFFECTS` (`:134-139`) — gates `changes_state` (Eq. 5).

Both contain `{"open","closed","on","off","plugged_in","plugged_out","holds_obj",
"next_to_obj","facing_obj","on_char"}`. The `action_subtree` copy's comment says it
"mirrors" the diagnosis one; they are two independent literals with no shared definition.

---

## 4. Error classification

### 4.1 Simulator error → error-type string

```python
exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)
if not exe_flag:
    history_cp = copy.deepcopy(history_env_states)
    try:
        checker = TemporalOrderChecker(my_info, history_cp)
        code = checker.run_checker().get_error_type()
        err_type = ERROR_CODE_TO_TYPE.get(code, "UNKNOWN_ERROR")
    except Exception as ex:
        err_type = "UNKNOWN_ERROR"
```
(`eai_sda_runner_tree.py:1690-1702`)

`TemporalOrderChecker` is EAI's own unmodified checker. `ERROR_CODE_TO_TYPE`
(`:110-117`) is the runner's copy of the evaluator's map:

| Code | Identifier |
|---|---|
| 0 | `WRONG_TEMPORAL_ORDER` |
| 1 | `MISSING_STEP` |
| 2 | `AFFORDANCE_ERROR` |
| 3 | `UNSEEN_OBJECT` |
| 4 | `ADDITIONAL_STEP` |
| 5 | `UNKNOWN_ERROR` |

Any code outside 0–5, and any exception from the checker, both become `UNKNOWN_ERROR`.

### 4.2 Two categories short-circuit before diagnosis

Immediately after classification, and **before** any diagnosis call:

- `ADDITIONAL_STEP` (`:1704-1711`) — the action index is added to `skipped_indices` and
  execution `continue`s to the next action.
- `UNSEEN_OBJECT` (`:1712-1719`) — same treatment.

Everything else sets `executable = False`, records `failed_action` and `failed_plan_idx`,
and `break`s out of the execution loop into diagnosis.

**Consequence:** `diagnose_error`'s first branch, `if error_type == "ADDITIONAL_STEP":`
(`error_diagnosis.py:274-281`), is unreachable from this runner. It remains callable
directly (and by `sda_last_hope_modified/error_diagnosis.py`'s own self-tests), but on the
production path only `WRONG_TEMPORAL_ORDER`, `MISSING_STEP`, `AFFORDANCE_ERROR` and
`UNKNOWN_ERROR` ever reach it.

### 4.3 From error type to strategy — the full cascade

`diagnose_error_tree` (`error_diagnosis_tree.py:19`) forwards everything to
`error_diagnosis.diagnose_error` (`:237`), then adds two things: `original_subsequence`
(the plan slice `t_start ≤ index ≤ t_end`, which its own docstring warns is "exact only
when no steps were skipped… The runner recomputes the window skip-aware and overrides it")
and `error_objects` = `{failed.obj} ∪ {failed.target}`, plus — when
`obj_not_inside_closed_container` is unsatisfied — the real container token from
`_find_container_in_env` (comment "FIX 3": without it the tree "and LLM hallucinate generic
`container` names").

Inside `diagnose_error` the cascade is a straight-line sequence of guarded early returns.
In order:

1. `if error_type == "ADDITIONAL_STEP"` → **`local`**. (Unreachable from the runner, §4.2.)
2. Build a `StateTracker` whose `model` is `ObjectStateModel.from_env_dict(env_dict)` with
   `env_dict` = the environment **at failure**, and record `action_history` into it
   without mutating the model. Compute
   `unsatisfied = model.check_all(get_preconditions(failed.action), failed.obj, failed.target)`.
3. **Container blind-spot augmentation** (`:308-318`): if `holds_obj` is unsatisfied,
   `obj_not_inside_closed_container` is not, and the model says the object *is* inside a
   closed container, append `obj_not_inside_closed_container` to `unsatisfied`. The comment
   attributes this to `PUTBACK`/`PUTIN` declaring only `holds_obj`, so the container never
   surfaced and the repair tree got no container candidates.
4. **`already_satisfied`** — see §5.4 for the exact test.
5. `if error_type == "AFFORDANCE_ERROR"` → **`local`**.
6. `if "holds_obj" in unsatisfied and not model.satisfies("grabbable", ...)` →
   **`wrong_action`**.
7. `if failed.action == "PUTOBJBACK" and not unsatisfied` → **`wrong_action`**.
8. `if not unsatisfied` → **`local`**.
9. `key_prec` selection, then `if key_prec in simple_prep and len(dynamic_unsat) <= 1` →
   **`insert_prep`**.
10. Otherwise → **`reconstruct`**, followed by `find_t_source`, backward `t_start`
    extension and forward `t_end` extension.

### 4.4 Failure categories the code recognises

Two vocabularies are in play and should not be conflated.

**(a) Simulator error types** — the six `ERROR_CODE_TO_TYPE` identifiers above. Trigger
condition: whatever `TemporalOrderChecker.run_checker().get_error_type()` returns for the
`ExecutionInfo` the executor produced. The runner does not itself decide these.

**(b) Diagnosis outcomes** — the five values `DiagnosisResult.replan_strategy` can take.
Each identifier and its exact trigger:

| Identifier | Triggering condition (first match wins, order as in §4.3) |
|---|---|
| `local` | `error_type == "ADDITIONAL_STEP"` (unreachable), **or** `error_type == "AFFORDANCE_ERROR"`, **or** `unsatisfied == []` after the container augmentation |
| `already_satisfied` | every positive effect of the failed action is in `VERIFIABLE_EFFECTS` **and** all of them already hold for `(obj, target)` |
| `wrong_action` | `"holds_obj" in unsatisfied` **and** `satisfies("grabbable", obj)` is False; **or** `action == "PUTOBJBACK"` and `unsatisfied == []` |
| `insert_prep` | `key_prec ∈ {not_sitting, not_lying, next_to_obj, next_to_target, facing_obj}` **and** `len(dynamic_unsat) <= 1` |
| `reconstruct` | none of the above |

`key_prec` is the first element of `dynamic_unsat = [p for p in unsatisfied if p in
DYNAMIC_PRECONDITIONS]`, or `unsatisfied[0]` if that list is empty — i.e. dynamic
preconditions are preferred over static properties. `DYNAMIC_PRECONDITIONS`
(`error_diagnosis.py:27-34`) is a hardcoded 17-element set.

**Docstring/code contradictions to note:**

- `error_diagnosis.py`'s module docstring lists only three strategies
  (`"local"`, `"insert_prep"`, `"reconstruct"`), and `DiagnosisResult.__init__` carries the
  inline comment `# "insert_prep" | "local" | "reconstruct"`. Both omit `already_satisfied`
  and `wrong_action`, which the same file returns. **Trust the code.**
- The docstring's line `- "local" : Unsat=[] or AFFORDANCE_ERROR → generate additional
  steps` omits the `ADDITIONAL_STEP` case, which the code also routes to `local`. Immaterial
  in practice since that branch is unreachable from the runner. **Trust the code.**
- `STATIC_PROPERTIES` (`error_diagnosis.py:37-42`) is defined and never read. Dead constant.

---

## 5. The five repair strategies

All five are **selected** in one function, `error_diagnosis.diagnose_error`
(`sda_last_hope_modified/error_diagnosis.py:237`). They are **executed** in
`EAISDATreeRunner.run_single_task` (`eai_sda_runner_tree.py:1569`). Only three of the
five have distinct execution behaviour — this is the single most important structural
fact in this section, and it is stated per strategy below.

Coordinate convention, from `DiagnosisResult`'s docstring (`error_diagnosis.py:63-72`):
`t_start` is **1-based into the successful-action history**; `t_end` is **1-based into the
current plan** whenever `failed_plan_pos` was supplied (the runner always supplies it, as
`failed_plan_idx + 1`). They coincide only when nothing was skipped. `plan_anchor` inside
`diagnose_error` is `failed_plan_pos` when given, else `failed_step.index`. The runner
converts `t_start` into plan coordinates with the module-level helper
`hist_pos_to_plan_pos(h, hist_to_plan, failed_plan_idx)` (`:894`), where
`hist_to_plan[k]` is the 0-based plan index of the (k+1)-th successful action.

Selection order is the order of §4.3. Restated as a decision list:

```
ADDITIONAL_STEP?           → local                (unreachable from the runner)
all verifiable effects hold? → already_satisfied
AFFORDANCE_ERROR?          → local
holds_obj unsat & not grabbable? → wrong_action
PUTOBJBACK & unsat == []?  → wrong_action
unsat == []?               → local
key_prec in simple_prep & |dynamic_unsat| ≤ 1? → insert_prep
otherwise                  → reconstruct
```

### 5.1 `insert_prep`

**Selected at** `error_diagnosis.py:412-425`.

**Condition.** `key_prec` is a key of the hardcoded table
```python
simple_prep = {
    "not_sitting":    "STANDUP",
    "not_lying":      "STANDUP",
    "next_to_obj":    "WALK",
    "next_to_target": "WALK",
    "facing_obj":     "TURNTO",
}
```
**and** `len(dynamic_unsat) <= 1`. It is tested after `already_satisfied`, both
`wrong_action` tests and the empty-`unsatisfied` test, and before `reconstruct`.

**Window.** `t_start = failed_step.index` (history coords),
`t_end = plan_anchor` (plan coords). A single-step window at the failure.

**What it does to the plan.** *Nothing distinct.* The `simple_prep` dict's values
(`STANDUP` / `WALK` / `TURNTO`) are **never read** — the dict is used only as a membership
test. The runner has no `insert_prep` branch: `grep -n "insert_prep"` over
`eai_sda_runner_tree.py` returns one hit, inside an unrelated comment at `:2191`. Control
falls through to the shared suggestion-plus-BFS path (§5.3's "otherwise" arm), which is
byte-for-byte the same code the `local` and `reconstruct` strategies run. The prep action
that gets inserted comes from the BFS, not from `simple_prep`.

**LLM.** Yes, indirectly — the shared path issues one `SUGGESTION_PROMPT` call.

### 5.2 `local`

**Selected at** three points: `error_diagnosis.py:274-281` (`ADDITIONAL_STEP`, unreachable),
`:354-360` (`AFFORDANCE_ERROR`), `:398-404` (`unsatisfied == []`).

**Condition.** See above. Critically, `AFFORDANCE_ERROR` is tested **after**
`already_satisfied` — the comment at `:326-333` records why: `PLUGIN` on an already
plugged-in device surfaces as `AFFORDANCE_ERROR`, but "the right move is to drop the action
— replanning around it just inserts useless `WALK`s until the replan budget is exhausted".

**Window.** Identical to `insert_prep`: `t_start = failed_step.index`,
`t_end = plan_anchor`.

**What it does to the plan.** Identical to `insert_prep` — the shared suggestion-plus-BFS
path. `local` and `insert_prep` are behaviourally indistinguishable in the current
implementation; they differ only in the logged strategy label (`🔍 Strategy: ...`) and
therefore in what `parse_diagnosis_stats.py` reports offline.

**LLM.** Yes — one `SUGGESTION_PROMPT` call.

### 5.3 `reconstruct`

**Selected at** `error_diagnosis.py:428` — the fall-through case.

**Condition.** Reached when `unsatisfied` is non-empty and either `key_prec` is not one of
the five `simple_prep` predicates, or two or more dynamic preconditions are unsatisfied.

**Window.** The only strategy that computes a real window.

```python
t_source = tracker.find_t_source(key_prec, failed_step.obj,
                                 failed_step.index, failed_step.target)
t_start = t_source
for step in reversed(action_history):
    if step.index >= t_source: continue
    if is_prep_action(step.action): t_start = step.index
    else: break
```
`find_t_source` (`:136-186`) implements Eq. 2: it seeds a fresh `ObjectStateModel` from the
**initial** environment snapshot (`initial_env_dict`), replays the history step by step, and
returns the index of the last transition of `key_prec` from satisfied to unsatisfied for
that specific `(obj, target)`. Two documented details: the seed used to be a blank model,
which "made object-state preconditions (off/open/plugged_in) unsatisfiable from the start,
so they could never flip"; and `target` must be passed, or target-preconditions
"never flip either". If the precondition was never satisfied, `find_t_source` returns
`t_error` ("FIX 2" sentinel) so reconstruction starts at the failed step rather than
discarding the whole successful prefix.

`t_end` extends forward over the plan past every later step touching `error_objects`:
```python
t_end = plan_anchor
for step in full_plan:
    if step.index > plan_anchor:
        if step.obj in error_objects or (step.target and step.target in error_objects):
            t_end = step.index
```

**What it does to the plan.** The shared suggestion-plus-BFS path, **plus** one exclusive
extra: root-cause exclusion (`eai_sda_runner_tree.py:1994-2009`). When
`replan_strategy == "reconstruct"` **and** `root_cause_at != failed_step.index` **and**
`"not_both_hands_full" not in unsatisfied_needs`, the diagnosed root-cause action is
converted to a plan position and removed both from `orig_subseq` (so the BFS cannot
re-propose it) and from the retained retry tail (`:2175-2181`). The comment gives the
reason: retrying the action that corrupted the precondition "reproduces the exact same
failure regardless of what the search finds beforehand". The `not_both_hands_full`
carve-out is because there the root cause is "whichever earlier `GRAB` tipped hands over
capacity — a NECESSARY pickup, not a corrupting action".

Splice (`:2234`): `current_plan_eai = before + new_subseq + failed_eai + after`, where
`before = history_actions[:t_start-1]`, `failed_eai = current_plan_eai[win_start_plan-1 : t_end]`
(the retained retry of the original window) and `after = current_plan_eai[t_end:]`. Two
post-splice cleanups run on `failed_eai`: a redundant `WALK` to an object the repair just
`GRAB`bed is dropped (`:2202-2206`), and any later `PUTBACK`/`PUTIN` of an object the repair
already placed is stripped from both `failed_eai` and `after` (`:2214-2231`).

**LLM.** Yes — one `SUGGESTION_PROMPT` call.

### 5.4 `already_satisfied`

**Selected at** `error_diagnosis.py:326-352`. Executed at `eai_sda_runner_tree.py:2011-2018`.

**Condition.**
```python
positive_effects = [e for e in get_effects(failed_step.action) if not e.startswith("not_")]
if positive_effects and all(e in VERIFIABLE_EFFECTS for e in positive_effects):
    all_already_true = all(
        tracker.model.satisfies(e, failed_step.obj, failed_step.target)
        for e in positive_effects
    )
```
Both clauses matter. The `VERIFIABLE_EFFECTS` gate exists because `satisfies` returns
`True` for unmodelled predicates (§3.5.7); without it "effects like `obj_ontop_target` /
`obj_inside_target` / `on_char` / `sitting` would otherwise make EVERY failed
`PUTBACK`/`PUTIN`/`PUTON`/`SIT` look 'already satisfied' and get the goal-placing action
silently deleted". Position in the order is deliberate and documented: **before** the
`AFFORDANCE_ERROR` branch.

**Window.** `t_start = failed_step.index`, `t_end = plan_anchor`. Both are set but the
runner never uses them for this strategy.

**What it does to the plan.** Deletes the failed action outright:
```python
idx = failed_plan_idx if failed_plan_idx is not None else failed_step.index - 1
current_plan_eai = current_plan_eai[:idx] + current_plan_eai[idx + 1:]
raw_output = plan_to_json_str(current_plan_eai)
continue
```
Note this uses `failed_plan_idx` (true plan position), not `failed_step.index`, precisely
because the latter drifts when earlier steps were skipped.

**LLM.** No. No call, and `replan_count` is not incremented.

### 5.5 `wrong_action`

**Selected at** `error_diagnosis.py:369-380` and `:389-395`. Executed at
`eai_sda_runner_tree.py:2020-2076`.

**Condition (two independent triggers).**
1. `"holds_obj" in unsatisfied` **and** `model.satisfies("grabbable", obj)` is False —
   e.g. `PUTON <washing_machine>`. `unsatisfied_needs` is then reset to `[]`
   ("not a precondition problem").
2. `failed_step.action == "PUTOBJBACK"` **and** `unsatisfied == []` — the executor's
   remembered-origin requirement is not modellable, so the comment routes it here to have
   "the LLM substitute an explicit `PUTBACK`/`PUTIN`".

Tested after `already_satisfied` and `AFFORDANCE_ERROR`, before the empty-`unsatisfied`
`local` case.

**Window.** `t_start = failed_step.index`, `t_end = plan_anchor`. The runner ignores
`t_end` here and slices the tail by true plan position instead:
`after_idx = failed_plan_idx + 1`, `after_wrong = current_plan_eai[after_idx:]`.

**What it does to the plan.**
- If this is a **repeat** of the immediately preceding failure signature
  (`failure_sig == last_failure_sig`, where
  `failure_sig = (str(failed_action), err_type, tuple(unsatisfied_needs))`), the action is
  **dropped** without any LLM call; if it achieves a node goal it is pushed onto
  `deferred_goal_actions` for the goal guard.
- Otherwise: one `WRONG_ACTION_PROMPT` call. The reply is parsed with
  `char_guard="strip"` against a map merging `full_name_to_id` and `relevant_name_to_id`.
  Any action in the reply that is byte-identical to the failed action is removed
  ("Never re-accept the action just diagnosed as wrong").
  Splice: `current_plan_eai = history_actions + new_subseq + after_wrong`.
- If the reply yields nothing usable, a **full-plan fallback** fires: `fallback_count += 1`
  and a bare `self.llm.call(base_prompt)` regenerates the entire plan from scratch. This is
  the only place in the loop where the whole plan is replaced.

**LLM.** Yes — one `WRONG_ACTION_PROMPT` call (charged to `replan_count`), plus optionally
one uncharged full-plan regeneration.

### 5.6 Correspondence to the SDA-Planner paper

The paper (arXiv:2509.26375v1) §4.3 defines exactly three routes. Their triggers and
machinery, quoted, are set out in §9.4; the short form:

| Paper route | Trigger | Paper's machinery | LLM? | Subtree search? |
|---|---|---|---|---|
| local replan | **all** dependencies satisfied (Environment State Error) | generate additional steps forward from t_error | Yes | **No** |
| prep insertion | s_error's state node has one incoming edge, from a prep-action node | **graph lookup** → insert that prep action at t_error, "without the full reconstruction" | **No** | **No** |
| reconstruct | any other unsatisfied dependency | Eq. 2–4 window → Adaptive Action SubTree Generation | Yes | Yes |

**Mapping to the code:**

- `local`, `insert_prep`, `reconstruct` correspond to the paper's three routes **in name and
  in diagnosis, but not in behaviour**. All three execute the same code path — one
  `SUGGESTION_PROMPT` call plus the BFS — which is the paper's machinery for `reconstruct`
  only. `insert_prep` loses its no-LLM/no-search property; `local` is routed into a subtree
  reconstruction the paper reserves for the "Otherwise" branch. Full analysis in §9.4.
- `already_satisfied` and `wrong_action` have **no counterpart in the paper**. §4.3 defines
  three routes and neither of these is among them; the paper's two-class error taxonomy
  (Environment State Errors, Action Precondition Errors) does not cover "the effect is
  already true" or "the action is semantically impossible." Both are additions responding to
  VirtualHome-specific failure modes. See §9.13.

So of the paper's three mechanisms, one (`reconstruct`) is implemented as specified, and two
are implemented as aliases of it; two further strategies are original to this work.


## 6. Adaptive action subtree search

Entry point: `action_subtree.generate_replacement_subsequence(...)`
(`sda_last_hope_modified/action_subtree.py:438`). The runner calls it twice at most,
`prefer_goal_placement=True` first and `prefer_goal_placement=False` as a fallback
(`eai_sda_runner_tree.py:2126-2135`).

### 6.1 Candidate generation

`generate_candidate_nodes(llm_suggestions, original_subsequence, error_objects,
char_sitting, char_lying)` (`:185`) builds an ordered, de-duplicated list of
`(ACTION, obj, target)` triples from three sources:

**Source 1 — LLM corrective suggestions.** The parsed reply to `SUGGESTION_PROMPT`. The
inner `parse_item` accepts six formats: `{"WALK": ["light","245"]}`,
`{"PUTIN": ["apple","7","fridge","2"]}`, `{"WALK": ["light_245"]}`,
`{"PUTIN": ["apple_7","fridge_2"]}`, and the two EAI string forms
`[walk] <light> (245)` and `[putin] <apple> (7) <fridge> (2)`. Zero-argument and
non-list arguments become `("ACTION", "character", None)`.

**Source 2 — the original failing subsequence**, i.e. the plan slice `orig_subseq` the
runner recomputed skip-aware (`eai_sda_runner_tree.py:2003`), converted to dicts at
`:2100-2105`. A "constrained subsequence rule" applies:

```python
for i, (a, o, t) in enumerate(parsed_orig):
    if i > 0:
        prev_a, prev_o, prev_t = parsed_orig[i - 1]
        if prev_o == o and o not in normalized_error_objects:
            continue
    add(a, o, t)
```
Consecutive actions on the same object are collapsed to the first, unless that object is
one of the `error_objects`.

**Source 3 — posture.** If `char_sitting or char_lying`, `("STANDUP", "character", None)`
is appended.

**Instance expansion** (`:580-600`). Every candidate's `obj` and `target` are run through
`initial_model.resolve(...)` and the cross-product of the resolved tokens is taken, so a
bare class name from the LLM (`{"GRAB": ["plate"]}`) becomes one candidate per instance.
Capped by `MAX_INSTANCES_PER_CLASS = 4`.

**Exclusion.** `banned_candidates` (a set of exact triples) is applied last:
`all_candidates = [c for c in all_candidates if c not in banned_candidates]`.

### 6.2 Guaranteed-candidate injection

Built *before* `generate_candidate_nodes` and prepended to its output
(`guaranteed_candidates + candidates`, `:594`). Everything it reads comes from
`initial_model` — an `ObjectStateModel` built from `initial_state_dict`, which the runner
passes as `state_at_tstart = history_env_states[t_start - 1]`, the saved scene-graph
snapshot from just before the diagnosed root cause — plus `unsatisfied_needs`,
`failed_obj`, `failed_target`, and `goal_edge_relations`.

Four triggers, each producing a fixed sequence:

**(1) `obj_not_inside_closed_container` or `target_open_or_not_openable` in
`unsatisfied_needs`** (`:485-507`). For each object in `error_objects` that has a container
which is not open: record it in `container_targets`, and emit — in order —
`SWITCHOFF <container>` *if the container reads `on`* (because PDDL `open` requires
`not_on`), then `WALK <container>`, then `OPEN <container>`. Then, for each object that
had a container, also emit `WALK <obj>` and `GRAB <obj>`.

**(2) `target_open_or_not_openable`, target itself openable** (`:509-524`). Open-targets
are `[failed_target]` if known, else all of `error_objects`. For each that satisfies
`can_open` but not `open`: `SWITCHOFF` (if `on`), `WALK`, `OPEN`.

**(3) Hand-freeing** (`:526-561`). Fires when `not_both_hands_full` is unsatisfied **or**
when a guaranteed chain already contains an `OPEN` *and* `initial_model.hands_full()` —
because `OPEN` needs a free hand (§3.5.3), so such chains would otherwise be unsatisfiable.
For each held object that is **not** `failed_obj`:

```python
place_action, place_target = (
    _goal_placement_for(held_obj, goal_edge_relations, initial_model)
    if prefer_goal_placement else (None, None)
)
if place_action:
    guaranteed_candidates.append(("WALK", place_target, None))
    guaranteed_candidates.append((place_action, str(held_obj), place_target))
    goal_placed_objs.append(str(held_obj))
else:
    guaranteed_candidates.append(("DROP", str(held_obj), None))
```
`_goal_placement_for` (`:48`) scans `goal_edge_relations` for an edge goal whose `from_id`
is the held object's id, and returns `PUTIN` for an `INSIDE` goal, `PUTBACK` otherwise. So
an object mid-carry is offered a delivery chain rather than a bare `DROP`. The comment
records the alternative it replaced: "a bare `DROP` abandons it with no record that it
still needs delivery, which cascades into orphaned items when a multi-item carry runs out
of hands".

**(4) `holds_obj`** (`:562-567`) — if `failed_obj` is grabbable and not held, emit
`WALK <failed_obj>` and `GRAB <failed_obj>`. **`facing_obj`** (`:569-570`) — emit
`TURNTO <failed_obj>`.

### 6.3 The search

`build_and_search_tree(candidates, initial_model, target_effects, error_objects,
max_depth=6, max_nodes=500, banned_paths)` (`:301`). The runner passes
`TREE_MAX_DEPTH = 6` and `TREE_MAX_NODES = 500` (`eai_sda_runner_tree.py:94-95`).

**Traversal.** Breadth-first, `deque` frontier, `popleft()`. `nodes_expanded` counts
dequeues; the loop runs `while queue and nodes_expanded < max_nodes`. Because it is BFS
over a de-duplicated candidate set with a shortest-path goal test, the first goal node
reached is a shortest repair.

**Goal test.** `target_effects` is a list of `("check", precondition, specific_obj)`
triples. `_achieves` requires **all** of them:
```python
for (_, precondition, specific_obj) in target_effects:
    check_obj = specific_obj if specific_obj else node.obj
    check_tgt = node.target if not specific_obj else None
    if not state.model.satisfies(precondition, check_obj, check_tgt):
        return False
```
`specific_obj=None` means "check against this node's own object", which is why almost every
goal in `generate_replacement_subsequence` pins an explicit object — the comments record
three separate bugs caused by not pinning (`next_to_obj` satisfied by "a path ending
anywhere", `not_both_hands_full` satisfied by any node whose object isn't held,
`not_holds_obj` short-circuiting a goal-placement chain).

**Node expansion — the SDG constraint check at each level.** For every candidate at every
node, in this order:

1. `satisfied(action, current.state, obj, target)` — Eq. 5's `satisfied(Aj, G)`; all
   `get_preconditions(action)` must hold in the parent state. Fails → skip.
2. Simulate into `simulated`, and compute `is_terminal = _achieves(simulated, temp_node)`.
3. `if not changes_state(action, current.state, obj, target) and not is_terminal: continue`
   — Eq. 5's `change(Aj, G)`. `changes_state` (`:142`) returns `False` for an action with no
   effects at all; with a state given, it requires at least one **verifiable** positive
   effect (`_VERIFIABLE_EFFECTS`, §3.5.7) not to already hold; when no effect is verifiable
   it returns `True` unconditionally, so `PUTBACK`/`PUTIN` are never pruned. The
   `is_terminal` escape hatch lets a goal-reaching action through even if it looks like a
   no-op.
4. `if not not_covered(current.action, action): continue` — Eq. 6's `notCovered(At, Aj)`:
   `True` iff the parent has at least one effect the child does not also have. A `ROOT`
   parent, or a parent with no effects, always passes.
5. The child is built with a *freshly copied* state (`new_state = current.state.copy()`
   then `apply`), pushed onto the queue.

**Termination and failure.**
- Success: the first dequeued node at `depth > 0` satisfying `_achieves` whose extracted
  path is not in `banned_paths`. `_extract_path` walks `parent` links and reverses.
- A **banned** goal node is skipped but its subtree keeps expanding, "so BFS naturally
  yields the next-shortest untried repair".
- Nodes at `current.depth >= max_depth` are not expanded.
- `return []` when the queue empties or `nodes_expanded` reaches `max_nodes`.
- Special case: if `target_effects` is empty, no tree is built at all — the function scans
  candidates linearly and returns the first single action that is `satisfied` and
  `changes_state`, or `[]`.

**Post-processing** (`:737-748`). The path is emitted as dicts —
`{"STANDUP": []}` for `ZERO_ARG = {"STANDUP","SLEEP","WAKEUP"}`, `{action: [obj, target]}`
for two-argument actions, `{action: [obj]}` otherwise.

**Goal construction** (`:616-717`) deserves one note: the `for need in unsatisfied_needs`
loop has **no `break`** — every unsatisfied need becomes a goal. The comment records that
breaking after the first "meant e.g. that for `['holds_obj','target_open_or_not_openable']`
the open-target goal was silently dropped". A final consistency rule strips
`not_both_hands_full` whenever any `holds_obj` goal is present, because "a `holds_obj` goal
consumes the freed hand".

**What the runner does with the result.** `tree_result` non-empty → `tree_success += 1`,
the repair key is recorded in `tried_repairs[failure_sig]` **before** resolution ("if
resolution fails, the next attempt must get a different path"), then
`subtree_results_to_eai` resolves tokens and applies the goal-relation `PUTBACK`↔`PUTIN`
correction. `tree_result` empty → the failed action is **dropped** and the loop continues;
there is no LLM fallback on tree failure. `run_all` logs this explicitly:
`"LLM fallback on tree fail: DISABLED"`. (The `fallback_count` LLM call that does exist
belongs to the `wrong_action` branch, §5.5 — the log line is about tree failure only.)

**Repair memory.** Three mechanisms, all keyed by
`failure_sig = (str(failed_action), err_type, tuple(unsatisfied_needs))`:
`tried_repairs[sig]` — set of already-spliced repair paths, passed as `banned_paths`;
`banned_cands[sig]` — set of triples that failed in the real environment, passed as
`banned_candidates` and populated by the alternation guard at `:1921-1932` (if the action
that just failed was itself inserted by the previous repair, that candidate is banned for
the signature it was generated for); and the `repeat_failure` flag used by `wrong_action`.
The comment justifies the whole scheme by determinism: "Everything is deterministic
(temp-0 LLM + simulator), so a repeated failure signature means the last repair did not
help." `TEMPERATURE = 0` is set at `eai_sda_runner_tree.py:87` with the inline comment
"deterministic — the tabu/repair-memory logic relies on it".

### 6.4 State restoration and reverse execution

**There is no reverse execution.** No inverse-action table, no rollback, no undo path
exists anywhere in `sda_last_hope_modified/`. A repository-wide search for
`reverse`/`rollback`/`undo` in the pipeline files returns only `list.reverse()` in
`_extract_path`, `reversed(...)` in two backward scans, and prose comments.

This is a direct divergence from the paper, which specifies reverse execution in detail
(§4.4: "Sda-Planner first performs a reverse execution of the actions between t_start and
t_error … for irrecoverable actions that cannot restore the original state, it adopts a
*fake execution* strategy, i.e., skipping previously executed irreversible actions") and
illustrates it in its case study. See §9.10 for the full comparison and for why the
replay design makes the paper's irrecoverable-action handling unnecessary rather than
merely absent.

State is restored two different ways instead:

1. **Real simulator state — by full replay.** Every iteration of the repair loop begins
   with `motion_planner.reset()` (`eai_sda_runner_tree.py:1671`) and re-executes the entire
   current plan from the initial scene. Nothing is ever stepped backwards.
2. **Search-time state — by snapshot lookup.** The runner keeps
   `history_env_states`, a list of `copy.deepcopy(motion_planner.env_state.to_dict())`
   appended after every successful action, and seeds the BFS from
   `state_at_tstart = history_env_states[t_start - 1]` (`:2095-2101`), falling back to
   `env_at_failure` if the index is out of range. Inside the BFS, `TreeState.copy()` deep-copies
   the `ObjectStateModel` per node, so branches never share mutable state.
   `find_t_source` does the same thing from the other end, replaying from `initial_env_dict`.

**Irreversible actions are therefore never a problem for the planner, but they are for the
two goal-completion passes**, which execute directly against `motion_planner` with no
replay available. Both handle it the same way: build the whole sequence, execute it
step by step, and **commit only if every step succeeded** — the goal guard's
`if len(done) == len(seq)` (`:1788`) and `_attempt_goal_completion`'s `_run` returning
`None` on the first failure. Actions that already executed before a later step failed are
*not* undone; the comment at `:962-973` is explicit: "WALK is a real, un-rollback-able
action against `motion_planner` the moment it succeeds — the earlier steps of a
later-failing sequence still happened for real." The `walked_to` memo exists to stop those
orphaned `WALK`s from invalidating a spatial fact an earlier sub-pass established.

The one compensating mechanism is forward, not reverse: in `_attempt_goal_completion`'s
placement branch, containers opened or switched off as a *means* are restored afterwards
with `CLOSE` / `SWITCHON` (`:1149-1167`), tracked in `opened_toks` / `switched_off_toks`,
and run in a separate `_run` "so a restore failure doesn't discard the placement that
already succeeded".

---

## 7. Prompt layer

### 7.1 Path and baseline

Adapted file: `src/virtualhome_eval/evaluation/action_sequencing/prompts/one_shot.py`.
It exports one module-level string, `prompt`, which the runner imports at
`eai_sda_runner_tree.py:1615` and fills by substituting five placeholders —
`<object_in_scene>`, `<cur_change>`, `<node_goals>`, `<edge_goals>`, `<action_goals>` —
with the fragments returned by `build_id_aware_goal_strings`.

Baseline it derives from: the same path at git commit `531c62f`. A verbatim copy of that
baseline is kept in the repository root as `one_shot_original.txt` (byte-identical to the
upstream file apart from a trailing newline). The adapted file also carries a 55-line
header comment block (lines 1–55) recording the executor audit behind the changes and
naming `sdg_pddl_executor_verification.txt` as the full audit; `one_shot_prompt_diff_and_rationale.txt`
and `one_shot_rtf_diff_summary.md` in the repository root are further thesis-authored
diff notes.

Three further prompt constants live in the runner, not in `one_shot.py`, and have **no
upstream counterpart at all** — they are new: `SYSTEM_PROMPT` (`:170`),
`SUGGESTION_PROMPT` (`:219`), `WRONG_ACTION_PROMPT` (`:237`), `ACTION_GOAL_PROMPT` (`:259`).

An important asymmetry, documented at `LLMClient.call`'s docstring (`:388-394`):
`system_prompt=None` means no system message is sent. The **initial plan and the
whole-plan fallback are sent bare**, exactly as the EAI baseline sends `one_shot.prompt`
alone; only the feedback/repair calls (`SUGGESTION`, `WRONG ACTION FIX`,
`ACTION GOAL OBJECT`) attach `SYSTEM_PROMPT`. The stated reason: "so the baseline
comparison attributes gains to the repair loop, not to an enriched initial prompt".

### 7.2 Substantive differences between the adapted `one_shot.py` and the baseline

**Data-format section**
1. Objects in the scene: baseline documents the format as `<object_name> (object_id)`;
   adapted says "Each object is shown with its class name and ID" — matching what
   `build_id_aware_goal_strings` actually emits (`class_name_id, properties: [...]`).
2. Node goals: baseline `object_name is ... (some state)` → adapted
   `object_name_object_id is STATE`.
3. Edge goals: baseline `object_name A is ... to object_name B` → adapted
   `object_name_object_id is RELATION to object_name_object_id`.
4. The worked example changed from `"FIND": ["sink", "sink_id"]` (a literal placeholder
   string) to `"FIND": ["sink", "12"]` / `"PUTBACK": ["cup", "7", "sink", "12"]` (real
   numeric IDs), and its prose from "PUTBACK a cup **into** the sink" to "**onto** the sink".
5. New paragraph making the OR-semantics of property lists explicit: "When multiple
   properties appear in the same inner list … the object must have AT LEAST ONE of them
   (OR semantics) … Exception: CUT requires the object to have BOTH EATABLE and CUTTABLE".

**Supported Actions List** — four arity/property changes, two additions:

| Action | Baseline | Adapted | Note |
|---|---|---|---|
| `CUT` | `[['EATABLE', 'CUTABLE']]` | `[['EATABLE', 'CUTTABLE']]` | Baseline misspells the property; `CUTTABLE` is the real one |
| `PUSH` | `[['MOVABLE']]` | `[[]]` | `MoveExecutor.check_movable` exempts `action_name == "push"` |
| `PUTIN` | `[['GRABBABLE'], ['CAN_OPEN']]` | `[['GRABBABLE'], []]` | Target need not be openable |
| `WATCH` | `[[]]` | `[['LOOKABLE']]` | PDDL `watch` requires `lookable` |
| `SLEEP` | absent | `(0, [])` | added |
| `WAKEUP` | absent | `(0, [])` | added |

Eight further entries changed only in their trailing comment (`DRINK`, `FIND`, `PLUGIN`,
`PLUGOUT`, `POUR`, `PUTBACK`, `RELEASE`, `SQUEEZE`); the `SQUEEZE` note now records the
executor's broader class-name acceptance list, and `FIND`'s now states that it
auto-navigates.

**Note on `PUTOBJBACK`.** The header comment says "PUTOBJBACK removed from the supported
list". That is relative to an **earlier thesis revision** (commit `571cd66`), which had
added it. The upstream baseline never listed `PUTOBJBACK`, so relative to the baseline
this is not a change. Trust the file contents over the header comment on this point.

**Rules section** — the baseline's 8 numbered "Notice" items are replaced by 13
"Important rules". Mapping:

*Carried over (reworded):* baseline #3 (character is never an argument) → adapted #1;
baseline #5 + #8 (WALK first) → adapted #2, which adds "If you WALK to pick up a tool and
then need to use it on a different object, you must WALK back"; baseline #6 (names **and**
IDs) → adapted #3, which spells out both argument shapes explicitly.

*Dropped:* baseline #1 (`CLOSE` is the opposite of `OPEN`), #2 (`PUTIN <character>
<room>`), #4 (upper-case action names), and #7 (output must not be empty — the equivalent
warning survives inside the action-goals paragraph).

*New, with no baseline counterpart:*
- **#4** — with duplicate class names, use the exact ID from the goals/scene; do not
  substitute another instance. (Directly supports the ID-keyed state model, §3.3.)
- **#5** — `PUTBACK` creates `ON`, `PUTIN` creates `INSIDE`; the **edge goal decides**, not
  the target's class. This is the prompt-side half of the goal-relation correction the
  runner also enforces in code (§7.3).
- **#6** — `PLUGIN` before `SWITCHON` when the device shows `PLUGGED_OUT`.
- **#7** — `TURNTO` before `WATCH` / `LOOKAT` / `POINTAT` (justified by
  `PointAtExecutor = LookAtExecutor`).
- **#8** — prefer `WALK` over `FIND`; use `FIND` only when an action goal demands it.
- **#9** — holding requirements: `DRINK`/`READ`; `PUTIN`/`PUTBACK` first object;
  `WIPE` needs *any* held object; `CUT` needs a knife-classed tool.
- **#10** — `DROP`/`RELEASE` free a hand and must never be used to place something.
- **#11** — `OPEN` a `CLOSED` container before `PUTIN`.
- **#12** — repeated JSON keys are allowed and preserved in order.
- **#13** — at least one free hand before
  `GRAB`/`OPEN`/`MOVE`/`PUSH`/`PULL`/`SQUEEZE`/`PLUGIN`/`PLUGOUT`/`CUT`.

**Closing instruction:** baseline ends with a prose restatement of the JSON structure;
adapted ends with "Only output the JSON dictionary of action commands and nothing else."

**Documented non-validation.** The header comment states of the overfitting cleanup that
removed curated container/surface example lists: *"Not A/B-validated against the old
wording"*. The same caveat is repeated in the runner's `SYSTEM_PROMPT` header comment
(`eai_sda_runner_tree.py:168`).

### 7.3 Parse → validate → normalise path

Every LLM reply that becomes executable actions passes through
`parse_and_validate(raw, relevant_name_to_id, goal_edge_relations, char_guard)`
(`eai_sda_runner_tree.py:691`). Steps, in order:

1. **`parse_llm_output(raw)`** (`:422`). Strips ``` fences, then looks for `\{.*\}`; if
   there is no closing brace it retries with `\{.*` to **salvage a truncated reply**. It
   then extracts pairs with `r'"(\w+)"\s*:\s*(\[\s*\]|\[[^\]]+\])'` rather than
   `json.loads`, which (a) preserves **duplicate keys** in order and (b) drops an
   incomplete trailing pair so the prefix stays runnable. Returns `[]` on any exception.
2. **`filter_valid_actions(parsed)`** (`:446`) — keeps only actions in
   `EAI_VALID_ACTIONS` (`:119-127`, 42 verbs).
3. **Token normalisation.** Interleaved `[name, id]` arguments are folded into combined
   `name_id` tokens; each token then goes through **`_normalize_name_id_token`** (`:506`),
   which applies three regexes in order:
   - `^(.+?)_(\d+)_\2$` → collapse a repeated id suffix (`washing_machine_1001_1001`).
   - `^(.+?_\d+)_\d+$` → drop a trailing step counter appended to a complete token
     (`electric_shaver_2002_1` → `electric_shaver_2002`); justified by "VH class names
     never end in digits".
   - `^(.+)\.(\d+)$` → dot notation `light.245` → `light_245`.
   The original, simpler version of this function is retained commented-out above it.
4. **Character-reference guard** — see §7.4.
5. **Goal-relation correction** (`:764-800`). For `PUTBACK`/`PUTIN` with two arguments,
   `goal_edge_relations[(obj_id, tgt_id)]` decides: `ON` → force `PUTBACK`, `INSIDE` →
   force `PUTIN`. If no goal covers the pair and the action is `PUTBACK`, the fallback
   `CONTAINER_OBJECTS` heuristic may switch it to `PUTIN`. Corrections are logged.
6. **Zero-argument cleanup** — `STANDUP`/`SLEEP`/`WAKEUP` have their argument list forced
   to `[]`.
7. **`_check_grammar_combined(parsed)`** (`:527`) — one token per object, compared against
   `_eai_valid_actions[action][1]`. Returns `None` from `parse_and_validate` on failure.
8. **`json_to_action(parsed, relevant_name_to_id=...)`** — the (modified, §8) EAI function
   that produces the final `[ACTION] <name> (id)` strings.

The repair path is the same with one extra stage in front:
`subtree_results_to_eai` (`:1352`) runs `filter_valid_actions`, then
`_resolve_to_name_id` per argument (§3.3), then the **same** goal-relation correction, then
`_check_grammar_combined`, then `json_to_action` against a map merging `full_name_to_id`
and `relevant_name_to_id`. A `ValueError` from ambiguous resolution is caught and the whole
repair is discarded (`return None`).

Serialisation back out is `plan_to_json_str(eai_actions)` (`:1446`), which re-emits the
**interleaved** `"WALK": ["light", "245"]` form. Its `_dedup_name` helper strips a trailing
`_<id>` from the name, because `json_to_action` stores the full `relevant_name_to_id` key
as the class name. An older single-token version of this function is retained
commented-out above it.

### 7.4 The character-reference guard

**The prompt rules.** `one_shot.py` rule 1: *"The subject of all actions is the character
itself, that is, the robot. Do not include character as any action argument."*
`SYSTEM_PROMPT` RULE 6 (`eai_sda_runner_tree.py`): *"The character is NEVER an action
argument. A goal like 'character is LYING' or 'character is ON bed' is achieved by
targeting the FURNITURE … NEVER write SIT, LIE, GRAB or PUTBACK with the character as the
object — a character cannot be sat on, lain on, grabbed or placed."*

**The parse-time check.** `_character_target_actions(parsed)` (`:668`) returns the
upper-cased, order-preserving, de-duplicated list of action names whose argument list
contains a character reference:

```python
t = str(tok).strip().lower()
if t == "character" or re.match(r"^character(_\d+)+$", t):
```

It handles both the combined form (`"character_65"`) and the raw interleaved form
(`["character", "65"]`), so it works on normalised plans *and* on raw `parse_llm_output`
output — which is what lets `_build_retry_prompt` inspect a rejected reply.

**What happens when it fires.** `parse_and_validate` branches on the `char_guard` argument:

- `char_guard="reject"` → logs `🚷 Character used as object of ... — rejecting plan for
  corrective retry` and returns `None`. Used for the **initial plan only**
  (`:1639`, and `:2319` in `NoAdaptRunner`).
- `char_guard="strip"` → logs `🚷 ... — stripping those actions`, removes the offending
  actions and keeps the rest; returns `None` only if nothing survives. Used for the
  **retry** (`:1651`, `:2325`) and for both `wrong_action` paths (`:2048`, `:2072`).
- `char_guard=None` (the default) → no check at all. This is what `_attempt_goal_completion`'s
  internal `_mk()` helper uses, and the goal guard's `WALK` construction — both build their
  arguments from the environment rather than from an LLM reply.

On rejection the runner calls `_build_retry_prompt(base_prompt, raw_output)` (`:837`),
which checks the *rejected* reply for character references **first** and, if found, appends
a message naming the specific verbs and the correct alternative. The docstring records why
a generic message was insufficient: at temperature 0 a re-ask "reproduce[s] the IDENTICAL
mistake on retry … because the message never says what was wrong, only that something was."
The retry is then parsed with `char_guard="strip"`, whose rationale is stated inline: "if
the model repeats the character mistake, salvage the rest of the plan instead of failing
the whole parse — no worse than the eventual repair-loop drop, and it doesn't burn replan
budget on an unfixable action."

`_build_retry_prompt` has two further branches after the character one: an
**empty-argument** branch (a verb emitted as `{"LIE": []}` that is not in
`{STANDUP, SLEEP, WAKEUP}`) that names the verb and shows the correct shape, and a generic
"invalid or truncated" fallback.

Offline coverage: `sda_last_hope_modified/test_character_guard.py` exercises
`SYSTEM_PROMPT`, `_build_retry_prompt`, `_character_target_actions` and
`parse_and_validate` with no API and no simulator.

---

## 8. Interface with the evaluator

### 8.1 What the runner writes

`EAISDATreeRunner._save(outputs)` (`eai_sda_runner_tree.py:2247`) writes a JSON **list** to
`osp.join(OUTPUT_DIR, f"{MODEL_NAME}_outputs.json")` with `indent=4`. Each element is

```json
{"identifier": "<file_id>", "llm_output": "<JSON string>"}
```

`identifier` is the EAI script id verbatim as it appears as a key inside
`task_state_LTL_formula_accurate.json[scene_1][<task_name>]` — e.g. `"9001_1"`, `"650_2"`.
No translation is applied to it.

`llm_output` is the string produced by `plan_to_json_str(clean_plan)`, in the **interleaved**
format with **duplicate keys**:

```
{"WALK": ["cupboard", "229"], "WALK": ["cup", "1005"], "PUTBACK": ["cup", "1005", "table", "355"], ...}
```

Defaults: `OUTPUT_DIR = "/opt/iGibson/output_sda/virtualhome/action_sequencing"` (`:102`),
`MODEL_NAME = f"{MODEL}-sda-tree-final{os.environ.get('SDA_TAG_SUFFIX','')}"` (`:90`). The
connectors override both — e.g. `eai_sda_runner_hard.py` sets
`MODEL_NAME = f"{MODEL}-sda-tree_hard50"` (plus `_r<budget>` and `_a<attempt>` suffixes) and
redirects `OUTPUT_DIR` to a sibling `action_sequencing_hard50` directory so hard-task runs
never collide with the main run's resume checkpoint.

Note the file is **also** the resume checkpoint: `run_all` reloads it and skips identifiers
whose `llm_output` is neither `""` nor `"..."`.

### 8.2 ID-format translation

There are three representations and two translation points.

| Representation | Where |
|---|---|
| Combined token `"light_245"` | Internal to the planner: `ObjectStateModel`, diagnosis, BFS, `parse_and_validate`'s intermediate form |
| EAI action string `[WALK] <light> (245)` | What `motion_planner.my_execute_primitive_action_eval` consumes |
| Interleaved JSON `"WALK": ["light", "245"]` | What is saved and what the evaluator parses |

- **Combined → EAI string:** `json_to_action(parsed, relevant_name_to_id)` in
  `eval_utils.py`. This is a **modified** EAI function (§8.3): upstream accepted only the
  interleaved 2-token and 4-token shapes; the adapted version adds a 1-token branch and a
  `str(objects[1]).isdigit()` discriminator inside the 2-token branch, so
  `["light_245"]`, `["light","245"]`, `["apple_7","fridge_2"]` and
  `["apple","7","fridge","2"]` all work. The class name written into the output string is
  `key.rsplit("_", 1)[0]`, i.e. the id suffix is stripped back off.
- **EAI string → interleaved JSON:** `plan_to_json_str` (`eai_sda_runner_tree.py:1446`),
  whose `_dedup_name` strips a trailing `_<id>` from the captured name to avoid emitting
  `"washing_machine_1001", "1001"`.

The evaluator side must therefore accept interleaved pairs, and does: `check_action_grammar`
compares `len(params) // 2` against the expected object count, and
`check_no_hallucination_in_arg` walks `objects` in steps of two and looks up
`f"{obj_name}_{obj_id}"`. **Both of those are upstream changes**, committed by the EAI
maintainers at `74d816d` (2025-01-12, `fix(action_sequencing): name id format check`), not
thesis changes. Likewise `motion_planner.get_symbolic_goal_nl`'s
`relevant_name_to_id[f"{class_name}_{id}"]` keying is upstream (`b2038c0`).

### 8.3 Modifications to the evaluator and its inputs

Two files under `src/` were changed on the evaluation path. Upstream originals are the same
paths at commit `531c62f`.

**`src/virtualhome_eval/simulation/evolving_graph/eval_utils.py` — four changes**

1. `valid_actions` gains `"PLUGIN"`, `"PLUGOUT"`, `"RELEASE"` (each arity 1). Without them
   the grammar check raises `KeyError` on plans containing those verbs.
2. `load_json_preserving_order`'s regex changes from `r'"(\w+)"\s*:\s*(\[[^\]]+\])'` to
   `r'"(\w+)"\s*:\s*(\[\s*\]|\[[^\]]+\])'`, so an **empty** argument list (`"STANDUP": []`)
   is captured instead of silently dropped. The runner's `parse_llm_output` carries the
   identical regex change.
3. `json_to_action` accepts the combined `name_id` shapes (§8.2).
4. **`scene_evaluate_wID`: a `break` removed from the edge-goal loop.**
   ```python
   for gd_edge_goal in accurate_edge_goals:
       if gd_edge_goal in final_state_dict["edges"]:
           # FIX: upstream EAI had `break` here, which stopped checking after
           # the FIRST satisfied edge goal — capping edge_match_num at 1 and
           # making all_success impossible for tasks with >1 edge goal.
           edge_match_num += 1
       else:
           unsatisfied_edge_goals.append(gd_edge_goal)
   ```
   This is a scoring-semantics change and affects every method scored with this evaluator,
   not only the SDA runner. It is also the function the runner itself calls in-loop for the
   goal-completion pass (§2.4), so runner-side and evaluator-side goal judgements stay
   consistent by construction.

**`src/virtualhome_eval/evaluation/action_sequencing/scripts/evaluate_results.py` — five changes**

1. `error_code_to_number` is initialised with all six codes `{0,1,2,3,4,5}` instead of
   `{0,1,2,4}`, and the `assert failed_error_code in error_code_to_number` is replaced by a
   fallback that logs and counts the failure as code 5 (`UNKNOWN_ERROR`). Comment:
   "`TemporalOrderChecker` cannot classify some failures (e.g. `PUTOBJBACK` with empty hands
   yields `None`) — count as `UNKNOWN_ERROR` instead of aborting the whole evaluation."
2. `gold_action_goals = list(set(...))` → `list(dict.fromkeys(...))`. Comment: action goals
   are order-sensitive for `check_order_with_or_score`, and `set()` iteration order for
   strings depends on hash randomisation, so "a correct plan could score differently on two
   evaluation runs" for any task with two or more action goals.
3. `relevant_name_to_id` is expanded twice — once folding every existing entry into an
   additional `f"{name}_{id}"` key, and once over **every node in the scene** adding both
   `name` and `f"{name}_{id}"` keys ("ADD THIS room wouldnt appear").
4. **The gold-match shortcut is removed.** Upstream had
   `if actions == gd_actions: all_executable_plan += 1 else: <full scoring>`. The adapted
   version always runs the scoring path. Comment: the shortcut meant an exact-match plan
   "never got credited in `error_info` or `all_correct_plan` (silently absent from both,
   not marked as failed — a real undercount)". `error_info[file_id]["goals_satisfied"]` is
   also now recorded per task, for offline best-of-k joins.
5. Zero-division guard: a `_rate(matched, total)` helper returns `-1` (the `eval_utils`
   N/A convention) when a goal category is empty, instead of raising. Applied to
   `state_goal`, `relation_goal`, `action_goal`, `total_goal` in both the log lines and the
   `summary` dict. Motivated by task sets with zero action goals.
6. One upstream guard is **disabled**: the parse check
   `if actions is None or len(actions) == 0 or not check_name_id_format(actions)[0]:` is
   commented out and replaced by `if actions is None or len(actions) == 0:`.

   This is a thesis change that **weakens** an upstream check. The guard was added by the
   EAI maintainers at commit `74d816d` (2025-01-12, `fix(action_sequencing): name id format
   check`) together with the helper itself, which rejects any action whose parameter list
   has odd length — i.e. it enforces that every argument arrives as a (name, id) **pair**:
   ```python
   def check_name_id_format(action_list):
       for action_dict in action_list:
           for predicate_name, params in action_dict.items():
               if len(params) % 2 != 0:
                   return (False, f"Action {predicate_name} does not follow name_id format")
       return True, None
   ```

   **Measured effect on this thesis's own numbers: none.** Replaying the guard over every
   saved SDA output file in the repository (56 task records across
   `gpt-4o-mini-sda-tree_hard50_outputs_FINAL_20260718.json` and
   `…_SMOKE_20260715.json`) produces **zero** would-be rejections. This is structural, not
   luck: `plan_to_json_str` emits either `"ACTION": []` (length 0) or interleaved
   `name, id` pairs (even length), so runner output can never have an odd parameter list.
   The same holds for the `NoAdaptRunner` arm, which uses the identical serialiser.

   What the disabled guard *would* still catch is an output file **not** produced by this
   runner — e.g. a plan in the pre-`74d816d` bare-name format (`"WALK": ["kitchen"]`, length
   1), which now passes the format gate and is instead caught one stage later by
   `check_no_hallucination_in_arg` (which reads `objects[i]`/`objects[i+1]` in pairs) or by
   `check_action_grammar` (`len(params) // 2`). So the failure is reclassified from
   *parsing error* to *hallucination* or *parameter error* rather than being missed —
   but the reported error-category breakdown for such a file would differ from upstream's.

   `[UNVERIFIED: why the guard was disabled — no comment, commit message, or note in the
   repository states a reason. The behavioural consequences above are measured; the intent
   is not recoverable from the source.]`

   Recommendation: re-enable it. It is a no-op for every plan this pipeline produces, and
   restoring it removes a gratuitous difference from upstream that a reader of the thesis
   would otherwise have to account for.

**Not on the evaluation path:** `src/virtualhome_eval/agent_eval.py` was edited to import
`evaluate_results` from an absolute local path
(`/Users/aytaj/Desktop/embodied-agent-interface/sda_final`) that does not exist in this
repository. The thesis scoring scripts (`eval_tag.py`, `eval_main_bo.py`,
`eval_hard50_one_budget.py`, `rescore_all_charguard.py`) all import
`virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results` directly and
never go through `agent_eval`. See §10.

### 8.4 How scoring is invoked

`eval_tag.py` is the minimal case and shows the contract. It builds an `args` namespace with
`--tag`, `--llm_response_path` (default `/opt/iGibson/output_sda`), `--resource_dir`,
`--dataset_dir`, `--output_dir` (default `/opt/iGibson/results_<tag>`) and
`--dataset` (default `virtualhome`), then **monkey-patches model discovery** —

```python
er_mod.extract_model_names = lambda _dir: [args.tag]
er_mod.evaluate_results(args)
```

— so exactly one output file is scored instead of every `*_outputs.json` in the directory.
`eval_main_bo.py` does the opposite: it leaves discovery alone so one pass scores every tag
present, which is how a full-SDA arm and a no-adaptation arm get scored in a single run.

Note the path convention `evaluate_results` imposes:
`llm_response_path = osp.join(llm_response_path, dataset, "action_sequencing")`. The caller
passes the parent directory, and the evaluator appends `virtualhome/action_sequencing`
itself — so hard-set outputs must be **staged** into a matching directory layout before
scoring, together with `resource_dir` pointed at `difficult_tasks/resources`.

Results land in `<output_dir>/<tag>/summary.json` and `<output_dir>/<tag>/error_info.json`.

---

## 9. Divergences from the SDA-Planner paper

**Status of this section.** The paper (Shen et al., *Sda-Planner: State-Dependency Aware
Adaptive Planner for Embodied Task Planning*, arXiv:2509.26375v1, 30 Sep 2025) is now
available and has been read. Every claim below quotes it directly. The small number of
remaining `[UNVERIFIED]` markers are points the paper genuinely does not settle, not points
this document could not check.

Paper sections referenced: §4.2 State-Dependency Graph Generation; §4.3 Error Backtrack and
Diagnosis (Eq. 2–4); §4.4 Adaptive Action SubTree Generation (Eq. 5–6); §5.3 Ablation Study.
The paper evaluates on **ALFRED** under **LOTA-BENCH**; this implementation targets EAI /
VirtualHome action sequencing. Some divergences below are forced by that change of
benchmark and are marked as such; others are not.

**Where the decision record lives.** Not in the git history — thesis-era commit messages are
short colloquial labels with no design rationale. Every rationale that exists is in code
comments and docstrings.

### Classification of the divergences

The subsections below are ordered by mechanism, not by kind. For write-up purposes the
divergences fall into four categories that must not be conflated: **(A)** places the
implementation does not do what the paper specifies and arguably should; **(B)** changes
forced by targeting EAI/VirtualHome instead of ALFRED; **(C)** mechanisms with no paper
counterpart — the original work; **(D)** deliberate simplifications and omissions.

**A — Departures from the paper's specification** *(fix or disclose; an examiner can
challenge these)*

| # | Divergence | Effect | Status |
|---|---|---|---|
| A1 | `t_end` implemented `max{t | o_t ∈ O}`, not Eq. 4's `max{t | ∀i ∈ (t_error,t], o_i ∈ O}` — no early exit (§9.6) | Reconstruction window, candidate set and retained retry tail all inflated; unrelated intervening actions pulled in | **fixed** |
| A2 | `WALK`/`RUN` flagged `is_prep` although the paper's *formal definition* forbids it (§9.2) | `t_start` includes the navigation preceding the root cause — which is what the paper's own *worked example* does | **kept deliberately, now documented**; the literal fix breaks the mechanism (measured, §9.2) |
| A3 | `local` and `insert_prep` both routed through `reconstruct`'s machinery (§9.4) | The paper's no-LLM/no-search prep path is lost; `local` gets a windowed subtree reconstruction the paper reserves for the "Otherwise" branch | **open** |
| A4 | Constrained subsequence: code **deletes** `A_i+1…A_j` from candidates; paper marks them non-selectable but **fixes them as forced children** (§9.9) | Blocks in the original plan can never be replayed by the search | **open** |
| A5 | No `V_used` exclusion in Eq. 5 (§9.11) | A candidate may repeat along one root-to-node path, which Eq. 5 forbids | **open** |
| A6 | Eq. 2 fallback returns `t_error` where the paper returns `1` (§9.5) | Minimal instead of maximal window when Λ = ∅ | open, **documented and deliberate** |
| A7 | Non-success exits saved an un-attempted spliced plan (§9.16) | Ablation arms graded on different kinds of object | **fixed** |

**B — Forced by the benchmark change (ALFRED → EAI/VirtualHome)** *(adaptation work; legitimate to present as such)*

| # | Change | Why forced |
|---|---|---|
| B1 | SDG hand-authored from `virtualhome.pddl` + `execution.py` (§9.3) | The paper sanctions "external task-specific knowledge bases"; ALFRED ships no declarative domain to reconcile |
| B2 | Executor chosen as arbiter where PDDL and `execution.py` disagree (§3.5, §9.3) | This conflict has no ALFRED analogue; nine preconditions added, six dropped |
| B3 | Per-instance ID-keyed state model, `"<class>_<id>"` (§3.3) | The paper grounds `A = (a,o)` to one object and never addresses duplicate class instances, which are pervasive in VirtualHome scenes |
| B4 | Two coordinate systems, history vs plan (§9.12) | EAI's `ADDITIONAL_STEP`/`UNSEEN_OBJECT` skip-and-continue taxonomy has no ALFRED equivalent |
| B5 | The whole save-and-re-simulate measurement model (§8, §9.16) | EAI scores a serialised string offline; the paper reads SR/GC from the live LOTA-BENCH environment |
| B6 | 42-verb vocabulary vs ALFRED's narrow skill set | Drives the need for A/C-category machinery below |

**C — Original mechanisms with no paper counterpart** *(the contributions)*

| # | Mechanism | Where |
|---|---|---|
| C1 | `already_satisfied` repair strategy | §5.4, §9.13 |
| C2 | `wrong_action` strategy, `WRONG_ACTION_PROMPT`, full-plan fallback | §5.5, §9.13 |
| C3 | Guaranteed-candidate injection — a **third** candidate source; the paper's `V_r` has exactly two | §6.2, §9.14 |
| C4 | Goal-aware hand freeing (`_goal_placement_for`) — delivers a held object to its goal instead of dropping it | §6.2, §9.14 |
| C5 | Repair memory / tabu: `tried_repairs`, `banned_cands`, `repeat_failure`, alternation guard | §6.3, §9.14 |
| C6 | Goal guard — relocates goal-achieving actions the simulator skipped | §2.4a, §9.14 |
| C7 | Goal-completion pass — runs `scene_evaluate_wID` in-loop and executes direct fixes on **success** | §2.4b, §9.14 |
| C8 | Goal-relation `PUTBACK`↔`PUTIN` correction | §7.3, §8.2, §9.14 |
| C9 | Character-reference guard (prompt rule + parse-time check + targeted retry) | §7.4 |
| C10 | Truncation-salvage parser and problem-naming corrective retry | §7.3, §9.14 |
| C11 | The executor-vs-PDDL audit itself, and the prompt rewrite it produced | §3.5, §7.2 |
| C12 | Strict instance resolution with ambiguity rejection + conservative fuzzy fallback | §3.3 |
| C13 | **Hard-50 benchmark** and its generator (`difficult_tasks/`) | §1.2 |
| C14 | **No-adaptation ablation connectors** for both task sets | §1.1 |
| C15 | **Five evaluator corrections** (edge-goal `break`, gold-match shortcut, error-code tally, zero-division, action-goal ordering) — contributions to the *benchmark*, not to SDA-Planner, and they change every method's scores | §8.3 |

**D — Deliberate simplifications and omissions** *(disclose as limitations)*

| # | Omission | Consequence |
|---|---|---|
| D1 | No bipartite graph — `SDG` is a static table (§9.1) | Two paper mechanisms defined as topological queries become hardcoded tables (causing A2, A3) |
| D2 | No reverse execution, no fake-execution skip list (§9.10) | Replay from `s0` every attempt instead; correct and irreversibility-proof, but pays a full re-execution per attempt |
| D3 | No final LLM selection among tree alternatives (§9.8) | "Shortest" substitutes for the paper's semantic tie-break |
| D4 | Executor requirements not modellable in the needs-list schema: `CUT`'s knife, `WATCH` same-room, `SIT`/`LIE` caps, `WALK` door-blocking, `PUTOBJBACK` origin (§3.5.6, §10.2) | Failures from these cannot be diagnosed; `CUT` is handled in the prompt layer only |
| D5 | Optimistic `satisfies` default for unmodelled predicates (§3.5.7) | Requires two `VERIFIABLE_EFFECTS` whitelists to contain |

The honest one-line summary for the thesis: **of the paper's three repair routes one is
implemented as specified, two are implemented as aliases of it, and the graph-topological
machinery underlying both is replaced by hand-written tables — while fifteen mechanisms
absent from the paper were added to make the method work on a benchmark it was not designed
for.**

### 9.1 The paper specifies an explicit bipartite graph; the code has no graph

*Paper (§4.2):* "G is modeled as a **directed bipartite graph**, where the node set is
partitioned into two disjoint subsets: The first subset N_a contains action nodes n_a …
The second subset N_s contains state nodes n_s … A directed edge from an action node n_a to
a state node n_s indicates that action a modifies the state s … a directed edge from a state
node n_s to an action node n_a signifies that the action a requires the state s to have
value v as a precondition."

*Code:* a 42-entry literal dict, `sdg.SDG` (§3.1). No node type, no edge type, no adjacency.

*Why it matters — this is not cosmetic.* **Two paper mechanisms are defined purely as
topological queries on G, and both had to be replaced by hardcoded tables because there is
no graph to query:**

1. The state-preparation-action test (§9.2 below) — "exactly one outgoing edge … and no
   incoming edges."
2. The `insert_prep` trigger (§9.4 below) — "the state node n_s … has **only one incoming
   edge in G** and this edge originates from a state preparation action node n_a."

*Could differ:* An implementer could build the bipartite graph directly from the same
`needs`/`effects` data — it is a mechanical construction — and then both tests become
computable rather than hand-listed. Nothing in the domain prevented it.

### 9.2 State-preparation actions: the code's list contradicts the paper's own definition

*Paper (§4.2):* "an action a is considered a state preparation action if its corresponding
node n_a has **exactly one outgoing edge to an agent state node** and **no incoming edges
from other state nodes**. These actions are not dependent on any prior state … An example
is *find*."

*Code:* a hand-set `"is_prep"` boolean, `True` for `WALK`, `RUN`, `FIND`, `TURNTO`.

*The divergence is checkable, and the code fails its own test.* Applying the paper's
definition to this repository's `SDG` entries:

| Action | `needs` (incoming edges) | `effects` (outgoing edges) | Paper's verdict | Code's flag |
|---|---|---|---|---|
| `FIND` | `[]` | `[next_to_obj]` | **prep** | prep |
| `TURNTO` | `[]` | `[facing_obj]` | **prep** | prep |
| `WALK` | `[not_sitting, not_lying]` | `[next_to_obj, inside_room]` | **not prep** — two incoming edges, two outgoing | prep |
| `RUN` | `[not_sitting, not_lying]` | `[next_to_obj, inside_room]` | **not prep** — same | prep |

So `WALK` and `RUN` are flagged as state-preparation actions although the SDG the code
itself defines gives them preconditions, which the paper's definition explicitly forbids.

**The paper's formal definition and the paper's own worked example disagree here, and the
implementation follows the example.** `is_prep` is consumed in exactly one place — the
`t_start` backward extension. In the paper's Fig. 3 walkthrough the root cause is
("pick up", "pan") at t = 3 and t_start = 2, i.e. **the navigation that set up the root
cause is inside the window**; that is the entire purpose of the backward extension.
VirtualHome's `WALK` is the action playing ALFRED's "find" role.

Measured directly, on the Fig. 3 scenario re-expressed in VirtualHome terms (hands filled by
an earlier `GRAB`, failure on a later `GRAB`):

| | `t_source` | `t_start` | prefix pulled into the window |
|---|---|---|---|
| `WALK.is_prep = True` (current) | 5 | **4** | `WALK tomato` — the navigation preceding the root cause |
| `WALK.is_prep = False` (literal rule) | 5 | **5** | *nothing* |

Applying the definition literally therefore satisfies the rule while defeating the mechanism
the rule exists to serve. The flag is left as-is, and the tension is now documented in
`sdg.py` immediately above the `SDG` dict rather than left implicit.

*Root cause of the tension:* ALFRED's `find` abstraction carries no preconditions, so the
paper's "no incoming edges from other state nodes" clause costs nothing there. VirtualHome's
PDDL gives `walk_towards` two posture preconditions (`not_sitting`, `not_lying`) and two
effects (`next_to_obj`, `inside_room`), so the same navigation action fails both halves of
the test. This is a consequence of the benchmark change (category B), surfacing as an
apparent category-A defect.

*Could differ:* An implementer could satisfy both readings by splitting `WALK` into a
precondition-free navigation primitive plus an explicit posture guard — which is closer to
what ALFRED's abstraction does — but that changes the action vocabulary the benchmark
scores against, so it is not available here.

### 9.3 SDG construction by hand is within the paper's stated options

*Paper (§4.2):* "State-Dependency Graph Generation module leverages a **hybrid approach**:
it can either query an LLM for commonsense-driven annotations **or rely on external
task-specific knowledge bases**."

*Code:* hand-authored from `virtualhome.pddl` plus a direct read of `execution.py` (§3.2).

*Assessment:* this is the paper's second sanctioned option, not a divergence. The PDDL
domain and the executor source are exactly "external task-specific knowledge bases." The
choice of **executor over PDDL as arbiter** when the two disagree (§3.5) is a genuine
decision the paper does not address, because ALFRED under LOTA-BENCH does not ship a
declarative domain alongside a separate Python executor. The code documents its choice —
`sdg.py`: "The executor is the arbiter in all of these" — and the reasoning generalises:
without the executor-only preconditions, the corresponding failures "diagnose as
`Unsat=[]` and the repair loop goes blind."

### 9.4 `local` and `insert_prep`: the paper's two mechanisms, collapsed into one

This is the largest behavioural divergence in the implementation.

**What the paper says (§4.3).** The three routes are distinguished by *error class* and use
*different machinery*:

> "First, Error Backtrack and Diagnosis module analyses the dependent states of
> (a_error, o_error) sequentially, checking whether each of them is satisfied based on the
> executed actions. **If all dependent states are satisfied, the module uses the local
> replan strategy to generate additional action steps from the current time step onward.**
> Otherwise, the module further localizes the subsequence that needs to be modified … Then,
> the subsequence is reconstructed by the Adaptive Action SubTree Generation module."

> "**If the state node n_s corresponding to s_error has only one incoming edge in G and this
> edge originates from a state preparation action node n_a**, this implies that the error
> stems solely from the agent's internal state being unprepared. In this case, the plan can
> be adapted by **directly inserting the corresponding state preparation action** at
> timestep t_error, **without the full reconstruction**. At this point, we set
> t_start = t_error = t_end."

Read together with §4.1, the three routes map onto the paper's two error classes:

| Route | Trigger | Machinery | LLM call? | Subtree search? |
|---|---|---|---|---|
| `local` | **All** dependencies satisfied → *Environment State Error* | LLM generates additional steps forward from t_error | Yes | **No** |
| `insert_prep` | A dependency unsatisfied, and G says its state node has exactly one incoming edge, from a prep-action node → simplest *Action Precondition Error* | **Graph lookup**: insert the prep action G names, at t_error | **No** | **No** |
| `reconstruct` | Any other unsatisfied dependency | Eq. 2–4 window → Adaptive Action SubTree Generation | Yes | Yes |

The Adaptive Action SubTree module is invoked **only** on the `reconstruct` branch — the
paper is explicit that it follows the "Otherwise" clause.

**What the code does.** All three run the identical path: one `SUGGESTION_PROMPT` call
followed by the BFS in `action_subtree.generate_replacement_subsequence`. The runner has no
`insert_prep` branch and no `local` branch; only `reconstruct` gets anything of its own (the
root-cause exclusion, §5.3). The differences that survive are the window values and the
logged label.

**Three separable losses:**

1. **`insert_prep` loses its defining property.** In the paper it is the cheap path: no LLM
   call, no search — the graph already names the action to insert. In the code it costs one
   LLM call and a full BFS. The information the paper uses *is present in the source* — the
   `simple_prep` dict at `error_diagnosis.py:412` is precisely a hand-materialised version
   of the graph lookup:
   ```python
   simple_prep = {
       "not_sitting": "STANDUP", "not_lying": "STANDUP",
       "next_to_obj": "WALK",    "next_to_target": "WALK",
       "facing_obj":  "TURNTO",
   }
   ```
   — but only its **keys** are read, as a membership test. The values, which are the answer
   the paper's mechanism returns, are never used. Note also that `STANDUP` appears as a
   value here while carrying `is_prep: False` in `SDG`, so the two tables disagree about
   what a preparation action is.
2. **`local` loses its defining property too.** The paper's `local` "generate[s] additional
   action steps from the current time step onward" and does **not** enter the subtree
   module. The code routes it into the subtree BFS with a window, i.e. it applies the
   paper's `reconstruct` machinery to the paper's `local` case.
3. **The error-class distinction disappears.** The paper separates Environment State Errors
   (all dependencies hold; the world differs from assumption) from Action Precondition
   Errors (a dependency is violated). The code preserves this in the *diagnosis label* but
   discards it in the *response*.

**Also divergent: the trigger test itself.** The paper's `insert_prep` condition is
"exactly one incoming edge in G, from a prep-action node." The code's is
`key_prec in simple_prep and len(dynamic_unsat) <= 1` — the membership test stands in for
"from a prep-action node," and `len(dynamic_unsat) <= 1` stands in for "only one incoming
edge." The second is a weaker approximation: it counts *currently unsatisfied dynamic*
dependencies of the failed action, whereas the paper counts *all* producers of the state
node in the graph, satisfied or not.

*Could differ:* Yes, and cheaply. Restoring `insert_prep` to the paper's semantics is a
small change — on that branch, splice `simple_prep[key_prec]` (with the failed action's
object, or `[]` for `STANDUP`) directly before the failed action and skip both the LLM call
and the search.

### 9.5 Eq. 2: the `t_source` fallback differs

*Paper (Eq. 2–3):*

> t_source = max{t | t ∈ Λ} if Λ ≠ ∅, and **t_source = 1** if Λ = ∅,
> where Λ = {t | t < t_error ∧ s_error[t−1] = v_need ∧ s_error[t] ≠ v_need}

So when the required state was **never** corrupted — because it was never satisfied — the
paper rewinds to the **start of the plan**.

*Code (`error_diagnosis.find_t_source`, comment "FIX 2"):* returns **`t_error`** in that
case, "so reconstruction starts at the failed step instead of discarding the whole
successful prefix."

*Assessment:* a deliberate, documented divergence in the opposite direction from the paper —
minimal window instead of maximal. The code's rationale is real (Λ = ∅ arises routinely in
VirtualHome when an object starts inside a closed container, where rewinding to t = 1 would
discard a long correct prefix), but it is a change, and it interacts with the budget: the
paper's choice would spend one expensive reconstruct on the whole plan, the code's spends
several cheap ones.

### 9.6 Eq. 4: `t_start` matches the paper; `t_end` does not

*Paper (Eq. 4):*

> t_start = min{ t | {a_i | ∀i ∈ [t, t_source)} ⊆ A_prep }
> t_end  = max{ t | {o_i | ∀i ∈ (t_error, t]} ⊆ O }

**`t_start` — the code is correct.** The paper requires *every* action in `[t, t_source)` to
be a preparation action, so the extension stops at the first non-prep step. The code's
`break` implements exactly that. (Subject to §9.2: the *membership* of `A_prep` is wrong,
even though the extension rule is right.)

**`t_end` — the code is wrong.** The paper requires *every* object in `(t_error, t]` to be
an error object, so the window stops at the first later action touching anything else. The
code implements `max{t | o_t ∈ O}` instead:

```python
t_end = plan_anchor
for step in full_plan:
    if step.index > plan_anchor:
        if step.obj in error_objects or (step.target and step.target in error_objects):
            t_end = step.index
```
There is no early exit, so a single mention of an error object near the end of a long plan
drags the entire tail into the reconstruction window — including arbitrarily many
intervening actions on unrelated objects, which the paper's `∀` explicitly excludes. This
inflates the window, the candidate set, and the retained retry tail.

**Fixed.** The loop now `break`s on the first later action that touches something other than
an error item, implementing Eq. 4's universal quantifier. Measured on a constructed case
matching the paper's Fig. 3 scenario with a long tail (11-step plan, one late mention of an
error object at step 11, an unrelated `WALK drawer` at step 8): `t_end` drops from **11** to
**7**, shrinking the window from 6 plan steps to 2. All five module self-tests still pass.

Note the per-step predicate was left alone: the paper's `A_i` carries one object, VirtualHome
actions carry up to two, and the existing disjunctive "touches `obj` or `target`" test was
retained so that only the missing quantifier changed.

### 9.7 Selecting which unsatisfied dependency to trace

*Paper (§4.3):* the module "analyses the dependent states of (a_error, o_error)
**sequentially**, checking whether each of them is satisfied." Order is the order of
S_dep[a]; no further ranking is described.

*Code:* `dynamic_unsat[0] if dynamic_unsat else unsatisfied[0]` — first unsatisfied
**dynamic** precondition, where "dynamic" is membership in a hardcoded 17-element
`DYNAMIC_PRECONDITIONS` set, falling back to first-unsatisfied overall.

*Assessment:* the code adds a dynamic-before-static preference the paper does not have.
Defensible (a violated static property is usually an affordance error, not a repairable
precondition), but it is an addition. The tie-break within each group is the authoring
order of a hand-written table.

### 9.8 The subtree search returns the first BFS hit; the paper adds an LLM selection step

*Paper (§4.4):* "the Adaptive Action SubTree Generation module further considers the state
constraints and performs a breadth-first search to extract a fully executable subsequence.
Subsequently the derived subsequence is combined with the remaining original steps as the
action subtree of the original plan. **Finally, to guide planning, both the environment
context and task instructions are provided, enabling the LLM to evaluate alternatives and
choose an optimal, task-aligned plan from the tree.**"

*Code:* `build_and_search_tree` returns the **first** goal-satisfying node BFS dequeues —
the shortest untried repair — with no scoring, no ranking, and no second LLM call. The LLM's
only role in the module is supplying candidate actions up front.

*Confirmed divergence.* The paper's final step is a semantic tie-break among structurally
valid alternatives; the code substitutes "shortest." The `banned_paths` / `tried_repairs`
mechanism (§9.14) partially compensates by yielding the next-shortest alternative on a
repeat failure, but that is across attempts, not a choice within one.

### 9.9 The constrained-subsequence rule: the code discards what the paper preserves

*Paper (§4.4):*

> "if a subsequence in the original plan P′ = {A_i, ⋯, A_j} satisfies the condition that all
> involved items are the same … and o_n ∉ O, then we infer that this subsequence was
> designed to exert a complete and uninterrupted influence on o_n and **should not be split**
> during the reconstruction process. Accordingly, we designate actions A_{i+1} to A_j as
> **non-selectable nodes** in the search tree, while treating A_i as a **special optional
> node**."

and, crucially, in the child-expansion rule:

> "For the previously mentioned subsequence P′ …, **if node A_t belongs to this subsequence
> (A_t ∈ P′), then its child node is fixed as A_{t+1}.**"

So the paper keeps the block **intact as a forced chain**: `A_i` is the only entry point,
and once the search enters, the remaining actions follow in order automatically. The block
is preserved, not dropped.

*Code (`generate_candidate_nodes`):*
```python
if prev_o == o and o not in normalized_error_objects:
    continue
```
`A_{i+1} … A_j` are simply **not added to the candidate set**, and there is no fixed-child
mechanism anywhere in `build_and_search_tree` — every node expands over the full candidate
list. So a `WALK fridge → OPEN fridge → CLOSE fridge` block contributes only `WALK fridge`,
and the `OPEN`/`CLOSE` can never be replayed by the search at all.

*Confirmed divergence, and a consequential one:* "non-selectable as an independent choice,
but forced once entered" and "deleted" are opposite treatments. The paper's rule preserves
plan structure; the code's discards it.

### 9.10 Reverse execution and fake execution are specified by the paper and absent from the code

*Paper (§4.1):* "Sda-Planner incorporates a **backtracking mechanism that reverses
previously executed actions** when necessary. Furthermore, it adopts a **fake execution**
strategy to simulate the impact of planned actions before executing."

*Paper (§4.4), in full detail:* "after Adaptive Action SubTree Generation module completes
its adaptation process, Sda-Planner first performs a **reverse execution of the actions
between t_start and t_error**. This reversal aims to restore the environment state as
closely as possible to its condition at t_start (e.g., executing a *pick up* action in
response to a prior *put down*). After reversal, Sda-Planner re-executes the adapted plan
from t_start … In particular, for **irrecoverable actions** that cannot restore the original
state, it adopts a **fake execution** strategy, i.e., skipping previously executed
irreversible actions to prevent state conflicts."

The paper's case study (§5.4) shows it concretely: "Sda-Planner starts to execute the
reverse actions, (*close*, *drawer*) and (*pick up*, *credit card*), and continues to
execute the corrected plan after the reversal is complete."

*Code:* **no reverse execution, no inverse-action table, no fake-execution skip list.**
State is restored by `motion_planner.reset()` plus **full replay of the whole plan from
t = 0** at every attempt (§6.4).

*Confirmed divergence, and the deepest architectural one in the implementation.* Note the
two designs are not equivalent in cost or in behaviour:

- Replay is trivially correct and immune to irreversibility, so the paper's entire
  irrecoverable-action / fake-execution apparatus becomes unnecessary — the code has no
  need for it because it never has to undo anything.
- Replay costs a full re-execution of the plan prefix on **every** attempt, where the paper
  pays only for the reversal segment `[t_start, t_error]`.
- The paper's approach leaves the environment in a state produced by a *reversal*, which it
  admits is only approximate ("as closely as possible"); replay reaches the exact state.

*Partial correspondence worth claiming:* the BFS's simulation over `ObjectStateModel` — 
applying candidate effects to a copied state before committing — is a form of the paper's
"simulate the impact of planned actions before executing." The *other* sense of fake
execution (skipping already-executed irreversible actions during re-execution) has no
counterpart, because replay makes it moot.

### 9.11 Eq. 5 and Eq. 6 readings

*Paper (Eq. 5):* N = {A_j ∈ {V_r − V_used} | satisfied(A_j, G) ∧ change(A_j, G) ∧
notCovered(A_t, A_j)}, where `change(A_j, G)` denotes that A_j "can have an effect on the
current state."

*Paper (Eq. 6):* notCovered(A_t, A_j) = True iff ∃s, (A_t, s) ∈ E ∧ (A_j, s) ∉ E — "The
child node should not override the S_eff of the parent node."

*Code:*
- `changes_state` is **state-dependent**: with a state given it demands that at least one
  *verifiable* positive effect not already hold. The paper's phrase "can have an effect on
  the current state" supports this reading. But the code then falls back to unconditional
  `True` when no effect is in `_VERIFIABLE_EFFECTS` (exempting `PUTBACK`/`PUTIN` entirely),
  and the caller adds an `is_terminal` escape hatch. Those two carve-outs are the code's,
  forced by the optimistic-default hole (§3.5.7); they have no paper counterpart.
- `not_covered` compares **effect labels**: `any(pe not in child_effects for pe in
  parent_effects)`. Eq. 6 is stated over edges of G, `(A_t, s) ∈ E`, i.e. exactly the
  action→state edges — which *are* the effect sets. So this reading is faithful.
- `V_used` — Eq. 5 excludes "the list of optional nodes in the sequence of actions
  corresponding to A_t", i.e. actions already used on the current path. The code has **no
  `V_used` exclusion**: a candidate may repeat along one root-to-node path. In practice
  `changes_state` and `not_covered` suppress most repeats, but not all — an action whose
  effect was undone by an intervening action can legitimately recur, which Eq. 5 forbids and
  the code permits.

### 9.12 Two coordinate systems

*Paper:* indexes a single sequence P with a single timeline; nothing in §4.3 contemplates
skipped steps.

*Code:* because `ADDITIONAL_STEP` and `UNSEEN_OBJECT` failures are skipped and execution
continues (§4.2), successful-history position and plan position drift apart, and the
implementation carries **both** — `t_start` in history coordinates, `t_end` in plan
coordinates, converted by `hist_pos_to_plan_pos`.

*Assessment:* an artifact of the EAI executor's skip-and-continue error taxonomy, which has
no ALFRED equivalent. Forced by the benchmark change, not a free choice — but the *dual*
representation is a choice; renumbering once at the boundary would remove it.

### 9.13 `already_satisfied` and `wrong_action` have no counterpart in the paper — confirmed

Paper §4.3 defines exactly three routes: local replan, direct prep-action insertion, and
reconstruction via the subtree module. There is **no** strategy for "the action's effect is
already true, delete it" and **no** strategy for "this action is semantically wrong, replace
it." The paper's error taxonomy (§1, §4.1) has two classes — Environment State Errors and
Action Precondition Errors — and both of these fall outside it: an already-satisfied effect
is neither, and a semantically impossible action is a *plan-generation* fault the paper does
not model.

*Why the implementation needs them.* Both are artifacts of the target benchmark:

- `already_satisfied` — VirtualHome's executor rejects `PLUGIN` on an already-plugged-in
  device as an affordance error. Routed as an Action Precondition Error it produces a repair
  loop that "just inserts useless `WALK`s until the replan budget is exhausted."
- `wrong_action` — the LLM emits actions that are impossible in principle
  (`PUTON <washing_machine>`; a character node can never satisfy `sittable`/`grabbable`), and
  `PUTOBJBACK` carries an executor precondition the SDG cannot express (§3.5.6). "No amount
  of precondition fixing will help."

ALFRED's skill set under LOTA-BENCH is far narrower than VirtualHome's 42-verb vocabulary,
so this failure mode is much less likely to arise there. These are reasonable additions —
but they are additions, and the thesis should present them as such rather than as
reimplementations.

### 9.14 Mechanisms with no paper counterpart

Beyond §9.13, these appear nowhere in the paper. Each is documented in the source only by
the concrete failure it was introduced to stop.

1. **Guaranteed-candidate injection** (§6.2) — a hand-written VirtualHome-specific prior
   (`SWITCHOFF → WALK → OPEN` container chains, hand-freeing, `WALK`+`GRAB`, `TURNTO`)
   prepended to the candidate set. The paper's V_r is exactly two sources: "the corrective
   actions recommended by the LLM, and the actions in the original subsequence." This is a
   third.
2. **Goal-aware hand freeing** (`_goal_placement_for`) — requires the task's goal
   specification to be visible inside the repair module, a coupling the paper's module
   boundary does not have.
3. **Repair memory / tabu** (`tried_repairs`, `banned_cands`, `repeat_failure`) — rests on
   determinism, stated inline: "temp-0 LLM + simulator, so a repeated failure signature
   means the last repair did not help." The paper has no cross-attempt memory; its
   No. EC is simply a measured average.
4. **The goal guard** (§2.4a) and **the goal-completion pass** (§2.4b). The latter runs the
   evaluator's own `scene_evaluate_wID` in-loop and directly executes fixes for unmet goals.
   It fires on **success**, entirely outside the paper's failure-triggered loop, and is the
   only place the planner consults the goal specification as an oracle.
5. **The goal-relation `PUTBACK` ↔ `PUTIN` correction** (§7.3, §8.2) — justified by a
   property of the *evaluator* (edge goals matched by exact dict equality), not of the
   domain.
6. **The character-reference guard** (§7.4), the truncation-salvage parser, and the one
   corrective retry (§7.3).

Items 4 and 5 deserve particular attention in the thesis: both make the planner's behaviour
depend on how the benchmark scores, not only on whether the plan executes. Neither has a
paper counterpart, and both should be described as adaptations to EAI's offline-re-simulation
scoring model rather than as parts of SDA-Planner.

### 9.15 Free parameters the paper does not fix

- `MAX_REPLAN = 3`. The paper reports No. EC as a **measured outcome** (avg. 3.06 for
  Sda-Planner) and never states a cap, so the budget is entirely the implementation's.
  `[UNVERIFIED: whether the paper's implementation had a cap at all.]`
- What the budget **counts**: the code counts LLM repair calls only; removals are free,
  guarded by a separate iteration cap `MAX_REPLAN + len(initial_plan) + 4`, and the
  `wrong_action` whole-plan fallback escapes the count entirely (§2.2). The paper's No. EC
  counts "plan corrections triggered per task," which is closer to the code's *iteration*
  count than to `replan_count`.
- `TREE_MAX_DEPTH = 6`, `TREE_MAX_NODES = 500`, `MAX_INSTANCES_PER_CLASS = 4`. The paper
  gives no search bound. `[UNVERIFIED.]`
- Instance expansion itself has no paper counterpart: the paper's A = (a, o) grounds one
  object per action and never discusses duplicate instances of a class, which is a live
  problem in VirtualHome scenes.

### 9.16 What gets saved on failure — an EAI-only problem, and an asymmetry between arms

*Paper:* SR and GC are measured from the **live** environment after execution under
LOTA-BENCH. Nothing is serialised and re-simulated. The ablation (§5.3) is described only
as: "when an error is encountered, Sda-Planner no longer adapts the plan, but skips the
error and continues to execute the plan."

*Code:* EAI scores by re-simulating a **saved string** offline, so the saved string *is* the
measurement. This was a defect in the measurement setup rather than a divergence from the
paper (the paper has no save step), and it **has been corrected**.

| | SDA arm on success | SDA arm on non-success exit | `NoAdaptRunner` |
|---|---|---|---|
| **Before** | executed plan (+ guard/completion appends) | **last spliced plan**, never attempted | executed subsequence |
| **After** | unchanged | **executed prefix of the final attempt** | unchanged |

The old behaviour graded the two ablation arms on different kinds of object: the no-adapt
arm's saved plan is executable *by construction* (it is filtered to the actions that
worked), while the SDA arm's budget-exhausted plan was non-executable *by construction* (it
had never been run). The Execution-SR gap between the arms therefore measured the save rule
rather than the planners. Goal credit was also read at an arbitrary point, since the
evaluator stops at the first failure *in the saved string*, which bears no relation to where
the runner actually stopped.

**Verified effect** (replaying a budget-exhausted shape for task `27_2` against the real
executor and the evaluator's scoring path): the saved plan flips from
`executable=False, ran 1/3` to `executable=True, ran 1/1`, with goal credit unchanged at
`node 2/3, edge 0/2`. This is the typical case — the evaluator's replay of a spliced plan
usually breaks at the same point the runner did, so **the dominant effect is on Execution
SR, not on goal completion**. Goal credit can still move in either direction when a splice
inserts actions *before* the point the runner reached.

**Consequence for reporting.** After the correction both arms save an executable-by-
construction plan, so Execution SR rises toward ceiling for the SDA arm. That rise is the
removal of an artifact, **not a performance improvement, and must not be reported as one**.
Execution SR ceases to carry information once both arms use this convention; Task SR and
goal completion are the metrics that remain meaningful.

**Residual limitation.** When nothing executed at all, `plan_to_json_str([])` is `"{}"`,
which `evaluate_results.py` counts as a *parsing* error and then skips goal scoring
entirely — so such a task scores zero on every goal category even if the initial state
already satisfied some goals. This is a pre-existing evaluator behaviour shared by
`NoAdaptRunner` (which emits `"{}"` the same way when nothing executes), not something the
correction introduced. Both exits now log `⚠️ EMPTY EXECUTED PREFIX` so these tasks can be
counted and excluded from the parsing-error tally. Measured frequency in the existing
hard-50 log: 2 attempt-level occurrences out of 19 action failures, both the same
`SWITCHON`-without-`WALK` shape.


## 10. Known limitations

### 10.1 Markers

A search for `TODO`, `FIXME`, `HACK` and `XXX` across every `.py` file in
`sda_last_hope_modified/` returns **zero hits**. The codebase uses a different convention:
numbered `FIX N` comments (`FIX 1` in `object_state_model.from_env_dict`, `FIX 2` in
`find_t_source`, `FIX 3` in `diagnose_error_tree`) and long prose comments naming the task
id or failure that motivated a change. Those are catalogued in the relevant sections above.

### 10.2 Explicitly self-declared gaps

Documented in `sdg.py`'s docstring under *"Known remaining gaps (confirmed against the
executor, NOT corrected — each needs machinery this needs-list schema cannot express)"*:

- `CUT`'s knife requirement (`CutExecutor.check_cuttable` inspects held objects' class
  names). Handled only by prompt text — `one_shot.py` Rule 9 / `SYSTEM_PROMPT` RULE 8 —
  so a failure caused by it cannot be diagnosed.
- `WATCH`'s same-room check, `SIT`/`LIE`'s per-class occupancy caps, `WALK`'s closed-door
  path blocking, and `PUTOBJBACK`'s remembered grab-origin. `PUTOBJBACK` is the only one
  with a compensating mechanism: the `wrong_action` route at `error_diagnosis.py:388`.
- `SQUEEZE` keeps `clothes` although the executor accepts a broader hardcoded class list
  — "kept as `clothes` here as a simplification".
- `PLUGOUT` keeps `not_on` although the corresponding executor check "sets an error message
  but never returns False … looks like an upstream dead-code bug".
- `_attempt_goal_completion`'s `HOLDS_RH`/`HOLDS_LH` branch cannot satisfy an exact-hand
  goal, because `GRAB` has no hand argument (comment at `:1189-1195`).
- `_attempt_goal_completion` skips edge-goal relation types other than
  `ON`/`INSIDE`/`CLOSE`/`FACING`/`HOLDS_*` — "left unsatisfied rather than guessed at" —
  and skips action goals whose verb takes two arguments.
- Node-state goals whose state has no single achieving verb (e.g. `DIRTY`) are skipped.

### 10.3 Structural limitations visible in the code

- **`ADDITIONAL_STEP` never reaches diagnosis** (§4.2), so `diagnose_error`'s first branch
  is dead on the production path.
- **`local` and `insert_prep` are behaviourally identical** (§5.2); `simple_prep`'s verb
  values are never read.
- **`satisfies` returns `True` for any predicate it does not implement**, which covers 13
  effect predicates (§3.5.7). Two independent `VERIFIABLE_EFFECTS` whitelists exist in two
  files to contain the consequences; they are not derived from a shared definition and can
  drift apart.
- **`max_total_iters` is computed once** from the initial plan length and never recomputed
  as the plan grows.
- **The `wrong_action` whole-plan fallback is uncounted** against `replan_count`.
- **Empty executed prefix** — a non-success exit where nothing ran saves `"{}"`, which the evaluator buckets as a parsing error and excludes from goal scoring (§9.16). Logged, not worked around.
- **`_split_token`/`_normalize_name_id_token` assume no VirtualHome class name ends in a
  digit.** Stated in a comment, never enforced.
- **Plain-class-name queries are optimistic and plain-class-name mutations are broadcast**
  to every instance (§3.3), so any path that loses the id silently degrades to class-level
  reasoning.
- **`_resolve_to_name_id` raises on ambiguity**, and the caller discards the entire repair
  (`subtree_results_to_eai` returns `None`), consuming an iteration.

### 10.4 Dead code and unused declarations

| Item | Location |
|---|---|
| `utils.py`, `base_environment.py`, `object_action_info.json` | `sda_last_hope_modified/` — imported by nothing |
| `STATIC_PROPERTIES` | `error_diagnosis.py:37` — defined, never read |
| Commented-out `"PUTOFF"` entry | `sdg.py`, above the live `PUTOFF` entry |
| Commented-out `_normalize_name_id_token` | `eai_sda_runner_tree.py:506` (previous version, retained above the live one) |
| Commented-out `plan_to_json_str` | `eai_sda_runner_tree.py:1446` (single-token version) |
| Commented-out `NON_INTERACTABLE` filter block | `eai_sda_runner_tree.py:585-601` — a scene-object filter that was written and disabled |
| Commented-out `check_name_id_format` guard | `evaluate_results.py` (§8.3 item 6) |
| `has_plug_or_has_switch` | Defined in `PRECONDITION_EXPLANATIONS` and implemented in `satisfies`, but appears in **no** `SDG["needs"]` list — used only as a filter in `action_subtree`'s `plugged_in` goal construction |
| `hangable`, `pourable`, `drinkable`, `obj_inside_room` | Present in `PRECONDITION_EXPLANATIONS`, in no `needs` list |
| 12 effect predicates | Present in `SDG["effects"]`, absent from `PRECONDITION_EXPLANATIONS`: `clean`, `lying`, `not_closed`, `not_dirty`, `not_off`, `not_plugged_in`, `not_plugged_out`, `obj_inside_target`, `obj_next_to_target`, `obj_ontop_target`, `ontop_obj`, `sitting` |
| 13 effect predicates | Present in `SDG["effects"]`, not implemented in `satisfies` — the 12 above plus `inside_room` (which *is* explained but not evaluated) (§3.5.7) |
| `if __name__ == "__main__": pass` | `one_shot.py` — inherited from upstream |

### 10.5 Hardcoded values and environment-specific assumptions

**Absolute paths** (all assume the iGibson Docker container layout):

```python
sys.path.insert(0, "/opt/iGibson/sda_eai")                                  # :31
RESOURCE_DIR = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
DATASET_DIR  = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
OUTPUT_DIR   = "/opt/iGibson/output_sda/virtualhome/action_sequencing"
```
Also `/opt/iGibson` defaults in `eval_tag.py`, `eval_main_bo.py`,
`eval_hard50_one_budget.py`, `rescore_all_charguard.py` (`BASE = "/opt/iGibson"`), and both
shell drivers. `sda_last_hope_modified/` is expected to be deployed as
`/opt/iGibson/sda_eai`. None of these are configurable without editing.

**A broken absolute path:** `src/virtualhome_eval/agent_eval.py` contains
`sys.path.insert(0, "/Users/aytaj/Desktop/embodied-agent-interface/sda_final")` and
`from evaluate_results_sda import evaluate_results`. That directory belongs to a
differently-named checkout and does not exist here; `agent_eval.py` will raise
`ModuleNotFoundError` if imported. It is off the current scoring path, but it is the EAI
package's own top-level entry point, so anyone invoking the benchmark the standard way will
hit it.

**Scene restriction:** `SCENEGRAPH_ID = 1`. All task loading, planner construction, and the
hard-task preflight are hardwired to `scene_1` / `TrimmedTestScene1_graph`. Nothing in the
pipeline iterates over scenes.

**VirtualHome-specific vocabularies that would not transfer:**

| Constant | Location | Content |
|---|---|---|
| `SDG` | `sdg.py` | 42 VirtualHome script verbs and their VH-specific predicates |
| `EAI_VALID_ACTIONS` | runner `:119` | the same 42 verbs, duplicated |
| `ERROR_CODE_TO_TYPE` | runner `:110` | EAI's six error codes, duplicated from the evaluator |
| `object_states.json` | `sda_last_hope_modified/` | 313 VirtualHome class names → state lists |
| `_GRAB_CLASS_EXCEPTIONS` | `object_state_model.py:55` | `{"water", "child"}`, mirroring a hardcoded exemption in `GrabExecutor` |
| `CONTAINER_OBJECTS` | runner `:764` | 14 VirtualHome container class names |
| `_GOAL_STATE_EFFECTS` | runner `:910` | 6 verb→state pairs; `_STATE_TO_ACTION` is its inverse |
| `simple_prep` | `error_diagnosis.py:412` | 5 VH predicates → 3 VH verbs |
| `ZERO_ARG` | runner (×4 copies), `action_subtree.py` | `{"STANDUP","SLEEP","WAKEUP"}` |
| `DYNAMIC_PRECONDITIONS` | `error_diagnosis.py:27` | 17 VH predicate names |
| `VERIFIABLE_EFFECTS` / `_VERIFIABLE_EFFECTS` | `error_diagnosis.py:332`, `action_subtree.py:134` | 10 VH predicate names, duplicated |

**Scene-graph schema assumptions**, hardcoded throughout `object_state_model.from_env_dict`,
`_find_container_in_env` and `_attempt_goal_completion`: node dicts have `class_name`, `id`,
`states`, `properties`; edge dicts have `from_id`, `to_id`, `relation_type`; the relation
names `CLOSE`, `FACING`, `INSIDE`, `ON`, `HOLDS_RH`, `HOLDS_LH`; the property names
`CAN_OPEN`, `CONTAINERS`, `HAS_SWITCH`, `HAS_PLUG`, `GRABBABLE`, `CLOTHES`, `PERSON`,
`SURFACES`; the class name `"character"` as a magic token.

**Free numeric parameters with no stated derivation:** `MAX_REPLAN = 3`,
`TREE_MAX_DEPTH = 6`, `TREE_MAX_NODES = 500`, `MAX_INSTANCES_PER_CLASS = 4`,
`max_total_iters`' `+ 4` term, `difflib` `cutoff=0.85` and `n=2`, `MAX_TOKENS = 2048`,
`TEMPERATURE = 0`, `time.sleep(1)` between tasks, `urlopen(..., timeout=60)` in the Gemini
backend, "2-5 actions" in `SUGGESTION_PROMPT` and "2-6 actions" in `WRONG_ACTION_PROMPT`,
and the every-10-tasks checkpoint interval.

**Workaround-style choices explicitly labelled as such in comments:**

- The `from_env_dict` "smart defaults" block (`FIX 1`) exists because
  `env_state.to_dict()` "sometimes omits properties for objects the EAI derived from its
  catalogue rather than the scene".
- The `CLOSE`→`NEAR` relabelling when rendering edges into the prompt
  (`build_id_aware_goal_strings`, `:619` and `:638`) — a presentation-only rename.
- The `_normalize_name_id_token` counter-suffix rule, which exists to undo a specific
  malformation the model produced (`"electric_shaver_2002", "1"`).
- The truncation salvage in `parse_llm_output`, and the `MAX_TOKENS` bump whose comment
  records that "512 truncated 30-50-step hard-task plans mid-JSON".
- The `_walk`/`walked_to` memoisation in `_attempt_goal_completion`, and the explicit
  decision **not** to apply it in the `ON`/`INSIDE` placement branch (`:1073-1088`).
- `parse_and_validate`'s `CONTAINER_OBJECTS` fallback, which only runs when no edge goal
  covers the pair.

**Prompt changes not validated against their predecessor.** The `SYSTEM_PROMPT` header
comment states of the 2026-08-25 overfitting cleanup: *"Not A/B-validated against the old
wording — same caveat as the Part D cleanup"* (`eai_sda_runner_tree.py:168`).
`one_shot.py`'s header describes the parallel cleanup to its own Rule 5 and Rule 9 but
carries no such caveat. Prompt-attributable behaviour differences from that revision are
therefore unmeasured on the runner-prompt side, and unstated either way on the
`one_shot.py` side.

---

### 10.6 Recommended corrections

Collected here because each is a concrete, bounded change rather than an observation. Listed
most-consequential first. None is applied in the code as it stands.

**(a) ~~The save-format asymmetry between the two arms~~ — APPLIED, see §9.16.** Fixed at
both non-success exits of `run_single_task` (`eai_sda_runner_tree.py:1849` and `:1902`):
`raw_output = plan_to_json_str(history_actions)`. Both arms now save the actions that
actually executed. **Both arms must be re-run and re-scored**; and note that the SDA arm's
Execution SR will rise toward ceiling as an artifact-removal, not a gain. The original
analysis is retained below for the thesis write-up.

EAI scores by re-simulating a saved string offline, so the saved string *is* the
measurement — and the two arms previously used different conventions, which made Execution
SR and goal-completion not directly comparable between them:

- `NoAdaptRunner` saves only actions that executed, so its saved plan is executable **by
  construction** and its Execution SR is definitionally near-ceiling.
- The SDA arm saves the executed plan on success but, on budget exhaustion, saves the last
  spliced plan — which was never run to completion and will fail again under offline
  re-simulation, at a point that need not match where the runner's final attempt failed.

The fix was to pick **one** convention and apply it to both arms. Option 1 was taken:

1. *Save what executed, both arms* — **applied**, and closest to the paper's ablation
   wording. Both arms now report the environment state the agent actually reached.
2. *Save the full final plan, both arms* — the rejected alternative: keep the SDA arm's
   spliced plan and change `NoAdaptRunner` to save its complete parsed plan including
   failed actions. Execution SR would then measure "did the method emit a plan that runs end
   to end", which is also a real question, and both arms would still be treated identically.

Task SR is the metric least affected by the choice — a task scoring 1 must have executed and
met every goal under either rule — which is a further reason to lead with it.

**(b) ~~`t_end` does not implement Eq. 4~~ — APPLIED, see §9.6.** The `t_end` loop in
`error_diagnosis.diagnose_error` now `break`s on the first later action touching something
other than an error item, implementing Eq. 4's universal quantifier. Measured on a
constructed Fig. 3-shaped case: `t_end` 11 → 7, window 6 plan steps → 2. All module
self-tests still pass. **Both arms must be re-run**, since this narrows every reconstruction
window and therefore changes candidate sets and retained retry tails throughout.

**(c) `insert_prep` does not do what the paper's prep insertion does (§9.4).** On that
branch, splice `simple_prep[key_prec]` directly before the failed action — with the failed
action's object, or `[]` for `STANDUP` — and skip both the `SUGGESTION_PROMPT` call and the
BFS. The lookup table already exists; only its keys are currently read. This restores the
paper's cheap path and frees budget, since that branch would no longer consume an LLM call.

**(d) ~~Drop `WALK`/`RUN` from `A_prep`~~ — WITHDRAWN after measurement; documented
instead (§9.2).** This recommendation was wrong and is retained only to record why. `is_prep`
feeds one thing, the `t_start` backward extension. Applying the paper's literal rule was
measured on the Fig. 3 scenario in VirtualHome terms:

| | `t_source` | `t_start` | prefix inside the window |
|---|---|---|---|
| `WALK.is_prep = True` (kept) | 5 | 4 | `WALK tomato` — the navigation preceding the root cause |
| `WALK.is_prep = False` (proposed) | 5 | 5 | *nothing* |

The paper's own worked example puts the navigation preceding the root cause *inside* the
window (t_source = 3, t_start = 2), so the literal rule satisfies the definition while
defeating the mechanism it exists to serve. The flag is kept and the tension is documented in
a note above the `SDG` dict in `sdg.py`. **Still outstanding:** reconcile `simple_prep`'s
treatment of `STANDUP` as a preparation action with `SDG["STANDUP"]["is_prep"] = False` —
the two tables disagree, and only `simple_prep`'s keys are currently read, so nothing depends
on it today.

**(e) The constrained-subsequence rule deletes what the paper preserves (§9.9).** Restoring
the paper's semantics needs a fixed-child mechanism in `build_and_search_tree`, which is a
larger change; at minimum the divergence should be stated in the thesis rather than
presented as the paper's rule.

**(f) Re-enable `check_name_id_format` in the evaluator (§8.3 item 6).** Measured to be a
no-op for every plan this pipeline produces; re-enabling removes an unexplained difference
from upstream.

**(g) Deduplicate the two `VERIFIABLE_EFFECTS` literals** (`error_diagnosis.py:332`,
`action_subtree.py:134`) into one shared definition. They are currently identical by
coincidence and can drift.

---

## Appendix — quick index of the identifiers used above

**Modules:** `sdg`, `object_state_model`, `error_diagnosis`, `error_diagnosis_tree`,
`action_subtree`, `eai_sda_runner_tree`.

**Classes:** `ObjectStateModel`, `ActionStep`, `DiagnosisResult`, `StateTracker`,
`TreeState`, `TreeNode`, `LLMClient`, `_OpenAIChatBackend`, `_GeminiBackend`,
`EAISDATreeRunner`, `NoAdaptRunner`.

**Functions:** `get_preconditions`, `get_effects`, `is_prep_action`, `explain_precondition`,
`from_env_dict`, `resolve`, `satisfies`, `check_all`, `apply`, `_release`,
`container_is_open`, `target_accessible`, `holding_anything`, `diagnose_error`,
`diagnose_error_tree`, `find_t_source`, `record_action`, `_find_container_in_env`,
`get_unsatisfied_explanation`, `generate_candidate_nodes`, `build_and_search_tree`,
`generate_replacement_subsequence`, `satisfied`, `changes_state`, `not_covered`,
`_extract_path`, `_goal_placement_for`, `_combine_name_id`, `_split_name_id`,
`run_all`, `run_single_task`, `_save`, `parse_llm_output`, `filter_valid_actions`,
`parse_eai_action`, `parse_and_validate`, `_normalize_name_id_token`,
`_check_grammar_combined`, `_character_target_actions`, `_build_retry_prompt`,
`build_id_aware_goal_strings`, `hist_pos_to_plan_pos`, `goal_state_action_pair`,
`_attempt_goal_completion`, `_repair_key`, `_resolve_to_name_id`, `subtree_results_to_eai`,
`plan_to_json_str`, `get_char_state`.

**Constants:** `SDG`, `PRECONDITION_EXPLANATIONS`, `DYNAMIC_PRECONDITIONS`,
`STATIC_PROPERTIES`, `VERIFIABLE_EFFECTS`, `_VERIFIABLE_EFFECTS`, `_CAN_ON_OFF`,
`_CAN_OPEN_CL`, `_CAN_PLUGGED`, `_CAN_GRAB`, `_GRAB_CLASS_EXCEPTIONS`,
`MAX_REPLAN`, `TREE_MAX_DEPTH`, `TREE_MAX_NODES`, `MAX_INSTANCES_PER_CLASS`,
`TEMPERATURE`, `MAX_TOKENS`, `SCENEGRAPH_ID`, `MODEL_NAME`, `OUTPUT_DIR`, `RESOURCE_DIR`,
`DATASET_DIR`, `TASK_DICT_PATH`, `ID2TASK_PATH`, `DATA_DIR`, `ERROR_CODE_TO_TYPE`,
`EAI_VALID_ACTIONS`, `CONTAINER_OBJECTS`, `ZERO_ARG`, `_GOAL_STATE_EFFECTS`,
`_STATE_TO_ACTION`, `SYSTEM_PROMPT`, `SUGGESTION_PROMPT`, `WRONG_ACTION_PROMPT`,
`ACTION_GOAL_PROMPT`, `_BACKENDS`, `API_PROVIDER`, `API_KEY`, `API_BASE_URL`.

**EAI functions used unmodified or as modified in §8:** `construct_planner`,
`json_to_action`, `valid_actions`, `scene_evaluate_wID`, `TemporalOrderChecker`,
`load_json_preserving_order`, `check_action_grammar`, `check_no_hallucination_in_action`,
`check_no_hallucination_in_arg`, `check_name_id_format`, `check_order_with_or_score`,
`extract_model_names`, `evaluate_results`, `MotionPlanner.my_execute_primitive_action_eval`,
`MotionPlanner.reset`, `MotionPlanner.get_symbolic_goal_nl`,
`MotionPlanner.filter_unique_subdicts`.

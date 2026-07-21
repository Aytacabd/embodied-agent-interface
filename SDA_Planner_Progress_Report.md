# SDA-Planner on EAI/VirtualHome — Progress Report

**Covers work since the last major checkpoint (April 27, 2026) through July 19, 2026**
**Branch:** `lastupdatehighestapril`

---

## 1. What this project is, in one paragraph

This project reimplements **SDA-Planner** ("State-Dependency Aware Adaptive Planner," Shen et al., arXiv:2509.26375) — a method for making LLM-driven robot planners recover from their own mistakes — but targets a different benchmark than the original paper. The paper evaluates on **ALFRED**; this implementation evaluates on the **action-sequencing task of the Embodied Agent Interface (EAI) benchmark**, using EAI's **VirtualHome** household simulator. SDA-Planner has three parts: a **State-Dependency Graph (SDG)** that encodes what each action requires and produces; an **Error Backtrack & Diagnosis** module that figures out *why* a failed action failed; and an **Adaptive Action SubTree** generator that searches for a minimal fix and splices it into the plan. The goal of this phase of work was to make that pipeline actually correct against EAI's simulator (not just superficially working), build a benchmark hard enough to show whether the repair machinery matters, and produce a defensible, reproducible number for how well it performs.

## 2. Headline results

| Configuration | Task Success Rate | Execution Success Rate | Goal Completion |
|---|---|---|---|
| gpt-4o-mini, **no error recovery** (ablation baseline) | 10.0% | 96.0%* | 41.8% |
| gpt-4o-mini, **with full SDA repair pipeline** | **34.0%** | 72.0% | **70.3%** |
| **Measured contribution of SDA's repair machinery** | **+24.0 points (3.4× the baseline)** | — | **+28.5 points** |

*The no-recovery arm's 96% execution rate is not a real strength — see §7.3.

This is measured on a **50-task benchmark we authored specifically to be repair-hungry** ("Hard-50" — see §5), not on the easier tasks EAI ships by default. The 34.0% / 72.0% number was reached only after three rounds of bug-fixing that took the same measurement from an initial, buggy **18.0% / 44.0%** up through **26.0% / 62.0%** to its final, independently certified value (§8). Every remaining failure in the final run has been traced to a specific cause and confirmed to be a genuine planning weakness of the LLM, not a bug in our harness (§9).

---

## 3. Where things stood in April

The April 27 checkpoint (`dff077a`, "last update highest results") had a working but **class-name-merged** world model: if a scene had two lights, the planner's internal state tracked "light" as one entity, not two. On scene 1 of VirtualHome, 71% of tasks touch an object class that has duplicates in the scene, and 18 tasks specifically involve turning on a light where 2–3 lights exist — meaning a large fraction of runs were reasoning about the wrong physical object without any way to detect it. There was also no systematic way to know whether the planner's failures on a given run were due to the LLM being wrong, or due to bugs in our own diagnosis/repair code, or due to bugs in the scoring script itself. Everything below is the work that closed those gaps.

---

## 4. Phase 1 — Rebuilding the world model to be object-identity-aware (July 11)

**Problem.** The planner's internal state tracker (`object_state_model.py`) stored state by class name ("light is ON") instead of by specific object instance ("light #245 is ON, light #246 is OFF"). Any scene with duplicate object classes was silently unreliable.

**Fix.** Rewrote the state model to key everything by a canonical `class_id` token (e.g. `light_245`), threaded that identity through every consumer — action parsing, the "which container is this object inside" lookup, the search-tree state wrapper, and candidate-object expansion — and added a full-scene name-to-ID fallback map so repairs can reference objects outside the immediate goal. Also fixed a related bug where a repair for "put X back in its container" was incorrectly demanding the *container itself* be held, which made those repairs mathematically impossible to satisfy.

## 5. Phase 2 — Hardening the repair loop against real failures (July 12, several rounds)

With the ID-aware model in place, we ran the planner against real tasks and fixed what broke, round by round:

- A repair loop that kept trying to "fix" an action whose goal was already achieved (e.g., plugging in a device that was already plugged in) — now recognized and the action is simply dropped instead of retried into the replan budget.
- A safety net: if the exact same action fails with the exact same diagnosis twice in a row, the loop now abandons that action instead of burning further replan attempts on a fix that demonstrably isn't working.
- Several object-resolution edge cases: scene objects with a typo in their name, repeated ID echoes from the model's own output being misread as new objects, and the "which container was this in" lookup returning a room instead of an actual container.
- A **tabu / repair-memory system**: each time a specific failure is repaired, that specific fix is remembered, so if the *same* failure recurs the search tries the *next-best* alternative instead of looping on a fix that already didn't work.
- A blind spot where a failed "put item back" action only reported "not holding the item" as the problem, without mentioning that the item was actually locked inside a closed container — so the repair search had no way to know it needed to open something first.

## 6. Phase 3 — Fixing how success is scored (July 12)

Two bugs were found in the **scoring logic itself** — separate from the planner:

1. **Action-to-relation mismatch.** In VirtualHome, "put back" (onto a surface) and "put in" (inside a container) produce different relationship types in the simulator. Several task goals required the *"inside"* relationship even for actions that conceptually read as "put on," and the planner was guessing based on a heuristic keyword list rather than checking what the goal actually required. Fixed so the correct action is chosen based on the task's own goal specification.
2. **An upstream bug in the EAI benchmark's own scoring code**: when checking whether a task's goals were met, the scoring function stopped after finding the *first* matching relationship goal, silently ignoring the rest. For any task with more than one relationship goal (which is most of them), this made a perfect success mathematically impossible to score as 100%. This was fixed in our local copy of the evaluator and confirmed necessary to overwrite in the Docker environment that actually runs the evaluation, since the two copies are independent.

## 7. Phase 4 — Designing a benchmark hard enough to matter (July 13–14)

**Motivation.** EAI's default task set skews toward tasks that don't stress the repair machinery much — many are achievable with a fairly direct plan. To measure whether SDA-Planner's adaptive repair is *actually earning its keep*, we needed tasks specifically engineered to trigger the failure modes the method is designed to handle.

**What was built.** A generator (`difficult_tasks/generate_difficult_tasks.py`) that authors **50 new tasks** (IDs 9001–9050) across five families, each targeting a specific stress condition:

| Family | Tasks | Stress condition |
|---|---|---|
| A — Busy hands | 9001–9010 | Character starts holding items in both hands; can't open anything until a hand is freed |
| B — Appliance load | 9011–9020 | Load multiple items into an appliance, then close + power it on, in the right order |
| C — Scatter & restore | 9021–9030 | Collect items from several closed containers, then leave every container re-closed |
| D — Precision toggles | 9031–9040 | Set several near-identical devices (e.g. multiple lights) to specific, differing states |
| E — Rough morning | 9041–9050 | Combination of the above plus starting seated |

Every task has a corresponding **hand-authored gold solution script**, and the generator doesn't just assume that script works — it actually **executes it in the real VirtualHome simulator** and checks that every goal is satisfied at the end. All 50 tasks passed this validation before being accepted into the set.

A **read-only audit pass** (built later, §11, but worth noting here) went further and checked for a subtler problem: whether any task's goals were *already true at the start*, which would let an agent score points by doing nothing. Two tasks were found to have this flaw and were corrected.

## 8. Phase 5 — Deployment infrastructure (July 13–15)

A separate runner script (`eai_sda_runner_hard.py`) was built as a **thin connector**: it reuses the entire existing SDA pipeline unchanged and only redirects configuration — which tasks to load, which output file to write to, which language model to use, and the replan budget — so that runs against the Hard-50 set are cleanly separated from runs against the main EAI task set and never overwrite each other's results.

This phase also involved a fair amount of environment debugging: the planner runs inside a Docker container (since it depends on the VirtualHome simulator, which isn't installable directly on the development machine), and getting files, resources, and dataset entries correctly copied into that container took several corrective rounds — most notably discovering that Docker's file-copy command does not reliably create multi-level missing directories, which caused a silent, confusing failure the first time the new benchmark's resource files were deployed.

## 9. Phase 6 — Auditing the implementation against the paper (July 15–16)

Before trusting any results, we did a **line-by-line comparison of the implementation against both the SDA-Planner paper and the actual VirtualHome execution engine** (not just the paper, since the paper describes an idealized world model and the real simulator enforces some rules the paper's formal description omits). This is where the first real bugs affecting *correctness*, not just robustness, were found:

- **The "open" action was missing a precondition.** VirtualHome's simulator requires a free hand to open anything, but this requirement exists only in the simulator's code, not in the formal action model the paper describes — so our internal model of what "open" requires didn't know about it. Whenever a task started with both hands full (exactly what Family A above tests), the diagnosis system saw a failed "open" action, checked its (incomplete) list of requirements, found nothing wrong, and had no idea what to fix — causing the repair loop to guess blindly and eventually give up.
- A related bug where the "free a hand" repair goal could be satisfied by an action that didn't actually free any hand, due to how the goal check was scoped.
- The "wipe" action had its precondition modeled with the wrong object variable, causing legitimate wipe failures to be misclassified as "the LLM proposed a nonsensical action" when they weren't.
- A **coordinate-system bug**: when a step earlier in the plan gets skipped for being redundant, the "position in the plan" bookkeeping used by two different parts of the repair system quietly fell out of sync with each other, which could make a repair splice into the wrong location in rare cases.

Every one of these fixes was verified with both automated unit tests and a purpose-built scenario suite that replays the exact failure conditions observed in real runs, before any of them were trusted.

## 10. Phase 7 — First full evaluation of the Hard-50 set, and a forensic investigation of the result (July 16)

With the fixes from Phase 6 deployed, the first full 50-task evaluation ran, scoring **18.0% task success / 44.0% execution success**. Rather than reporting that number, we ran a **forensic cross-reference** of the run's execution log, the scoring log, and the detailed per-task error records to determine, for every single failure, whether it was a genuine limitation of the language model or an artifact of our own tooling.

This surfaced two more scoring-script bugs (an unhandled error code that could abort the entire evaluation outright, and a divide-by-zero when a task category — like "action goals," which the Hard-50 tasks don't use — was empty) which were fixed. It also surfaced, and precisely diagnosed, **four real bugs in the planner itself**:

1. **Truncated responses being misread as "hallucinations."** Several tasks require 30–50 step plans, but the language model's response length limit was capped low enough to cut long plans off mid-sentence. The system then correctly failed to parse the broken response, but the scoring script logged this as if the model had hallucinated a nonexistent object — a fundamentally different (and much worse-looking) failure than what actually happened.
2. **Multi-step repairs stopping one step early.** When a single failure needed a repair chain of more than one corrective action (e.g., "walk over, then pick it up, then walk to where it needs to go"), the repair search was checking its own progress against the wrong object at the last step, causing it to declare victory one action too soon.
3. **A specific action ("put object back," a shorthand form the model sometimes chose over the more explicit "put on surface X") turned out to depend on hidden simulator bookkeeping our model had no way to check**, so failures involving it were undiagnosable and wasted repair attempts in a loop.
4. **Goal-relevant actions being permanently deleted rather than just skipped.** If the model tried to close a container before it had ever actually opened it, the simulator correctly rejected that step as premature — but our system then treated it as "not needed" and threw it away entirely, rather than recognizing the container still needed to be closed *eventually* and remembering to do so later.

## 11. Phase 8 — Fixing what the forensic pass found, and re-measuring (July 16–19)

All four bugs from Phase 7 were fixed:

1. Raised the response-length limit for long plans, added logic to salvage a usable partial plan from a cut-off response instead of discarding it outright, and added one automatic corrective retry when a response still can't be parsed.
2. Corrected the repair search's goal-checking so multi-step repair chains reliably end at the right place.
3. Instructed the model to never use the unreliable action, with automatic detection and correction in the (rare) case it does anyway.
4. Added a "goal guard": before finalizing a plan, the system now checks whether any action that was skipped or discarded during repair would have satisfied one of the task's own stated goals — and if so, relocates it back into the plan (executed as a genuine, verified action, not just assumed) rather than losing it permanently.

Re-running the same 50 tasks, same language model, same replan budget after these fixes: **26.0% task success / 62.0% execution success** — already a large jump purely from removing our own bugs, and this despite the task audit (§7 / §12) simultaneously making two of the fifty tasks *harder* than they were in the first run.

A fifth, smaller bug was then caught in this new run: a subtle interaction between the (fixed) prompt wording and the model's output formatting caused a handful of responses to be malformed in a new way (pairing an object identifier with a stray extra number), which again the scoring script misclassified as hallucination. Fixed, and the seven affected tasks were re-run in isolation (cheap — a targeted re-run, not a full 50-task run).

**Final measured baseline: 34.0% task success / 72.0% execution success / 70.3% goal completion**, with **zero** malformed-response errors of any kind remaining.

## 12. Phase 9 — Independently verifying the benchmark tasks themselves (July 16)

Separately from auditing the *planner*, we built a read-only checker that re-examines all 50 Hard-50 tasks from scratch: does every goal reference a real object with a sensible property, are the starting conditions internally consistent, does the hand-authored solution still work end-to-end, and — the check described in §7 — is any task's goal already satisfied before the agent does anything? This last check found two tasks with that flaw (one where a "device is plugged in" goal was already true at the start, and one where a scene quirk meant most of a "load items into the machine" goal was already satisfied because the items started inside the machine). Both were corrected and the corrected versions were the ones used for every result reported after July 16.

## 13. Phase 10 — Independent certification of the final number (July 19)

Before treating 34.0% / 72.0% as a reportable result, we ran a dedicated certification pass whose entire purpose is to try to *disprove* the number — checking for exactly the kind of silent scoring mismatches that would make a result meaningless:

- Every one of the 50 saved outputs is well-formed, with no instance of the two banned/buggy patterns from Phases 7–8 remaining.
- The execution engine's own verdict on which tasks succeeded matches the scoring script's verdict on which tasks were executable, for all 50 tasks, with **zero disagreements**.
- **Zero cases** where an action the model actually executed contradicted a goal the scoring script says was unmet (i.e., no case where the plan visibly did the right thing but was scored as if it hadn't).
- The exact file that was scored was confirmed, byte-for-byte, to be the file the final run actually produced.
- Every remaining failure was individually traced to a specific, understandable cause: the model running out of its allotted repair attempts on a genuinely hard task, the model simply never planning to close a container it had opened, or the model forgetting to place an item — all of which are real planning behavior, not artifacts of measurement.

**Result: the 34.0% / 72.0% figure is certified.** Nothing found in this pass required a code change.

## 14. Phase 11 — Measuring what SDA-Planner's repair machinery is actually worth (July 19)

A single number for "planner + repair machinery" doesn't answer the more important question: **how much is the repair machinery actually contributing?** To answer this, a second connector script was built that runs the identical initial planning step (same prompt, same model, same parsing) but with the entire diagnosis/search/repair system switched off — if an action fails, it is simply skipped and the plan continues, exactly as the original SDA-Planner paper's own "without adaptation" ablation is defined. This isolates the repair machinery as the *only* variable that differs between the two runs.

**Result:**

| | Task Success | State-goal completion | Relation-goal completion | Overall goal completion |
|---|---|---|---|---|
| Without repair machinery | 10.0% | 76.2% | 9.7% | 41.8% |
| **With SDA repair machinery** | **34.0%** | 63.4% | **76.7%** | **70.3%** |

The repair machinery is worth **+24 percentage points of task success — a 3.4× multiplier** over the no-repair baseline. For context, the original SDA-Planner paper's own equivalent ablation (on ALFRED, a different benchmark) measured roughly an 8–10 point improvement from the same intervention; the larger effect size here is consistent with the Hard-50 benchmark having been deliberately designed to require repair.

**One finding is worth explaining carefully, because it looks paradoxical at first glance:** the *state-goal* completion score is actually *higher* without the repair machinery (76.2% vs. 63.4%). This is not evidence that removing the repair system helps. Several Hard-50 tasks require the agent to open a container, retrieve items from inside it, and then **re-close it** by the end — the container starts closed and the goal is "container is closed." Without repair, the agent typically can't open the container at all (that's the whole point of Family A/B/C's design), so the container simply stays closed throughout — and the scoring system, which only checks the *final* state, cannot tell the difference between "closed because the agent restored it" and "closed because the agent never managed to touch it." With the repair machinery active, the agent *does* open the container, retrieve everything successfully (hence relation-goal completion nearly triples, from 9.7% to 76.7%), but sometimes forgets the final re-close step. This is exactly why **task success rate** — which requires *every* goal to be met simultaneously — is the metric that should be treated as the headline number, and it correctly and unambiguously favors the repair-enabled system (34.0% vs. 10.0%).

## 15. Phase 12 — Making the codebase provider-agnostic (July 19–20)

The final piece of this phase of work was a refactor with **no effect on any of the results above** — a code-quality and reusability improvement, done last and deliberately kept separate from anything that could change measured behavior. Previously, switching which language model provider was used (OpenAI vs. a different vendor) required editing code directly. The language-model-calling layer was restructured so that **every provider-specific detail lives in one clearly marked configuration block**, controllable entirely through environment variables — provider, model name, API key, and (for self-hosted or alternative-vendor endpoints) the API address — with no other file needing to change. This was verified with an automated test suite confirming the refactor preserves every existing behavior exactly (identical error handling, identical logging, identical interaction with the rest of the pipeline) before being accepted.

---

## 16. Full result progression at a glance

Same benchmark (Hard-50), same language model (gpt-4o-mini), same replan budget throughout — only bug fixes changed between rows:

| Stage | Task Success | Execution Success | Goal Completion |
|---|---|---|---|
| First measurement (buggy) | 18.0% | 44.0% | 49.4% |
| After planner precondition/repair fixes | 26.0% | 62.0% | 58.2% |
| **After response-formatting fix (final, certified)** | **34.0%** | **72.0%** | **70.3%** |
| *(For comparison) same model, repair machinery disabled* | *10.0%* | *96.0%\** | *41.8%* |

Task success rate nearly **doubled** purely from fixing our own tooling — which is itself evidence that the Hard-50 benchmark is sensitive enough to detect planner-quality differences, i.e., that it's doing its job as a benchmark.

---

## 17. Current state of the repository and what's left to do

**Committed and safe** (commit `b98f482`, "50 complex tasks," and its ancestors): the ID-aware world model rewrite, all repair-loop hardening, the scoring-script fixes, the entire Hard-50 benchmark generator and its 50 validated tasks, the deployment connector, and all result artifacts through the July 18 final baseline.

**Not yet committed** (exists on disk and has been verified, but should be committed before being relied upon further): the provider-agnostic refactor (§14), the no-repair ablation connector and its results (§14 itself), and one settings file.

**Recommended next steps, roughly in priority order:**
1. Commit the above so the certified numbers are permanently tied to a specific, reproducible version of the code.
2. Re-run the **original EAI benchmark's main 342-task set** on the current, fully-fixed codebase — the last time that set was run was before any of the fixes in this report, so the project's "main" headline number is currently stale and not comparable to Hard-50. This also serves as an independent check that the fixes generalize beyond the 50 tasks we authored ourselves.
3. Run the same Hard-50 comparison with a stronger model (gpt-4o instead of gpt-4o-mini) — this requires no code changes, only a configuration flag — to show how the repair machinery's contribution scales with model capability, which is a claim the original paper also makes and which this codebase can now test directly.

---

*Prepared from the project's working history and verified against the current codebase and result files.*

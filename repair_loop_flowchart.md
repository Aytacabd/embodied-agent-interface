# Repair loop control flow

Two versions of the same thing. **Figure A** is for the chapter body — it is what a reader
needs in order to follow the method. **Figure B** is the complete branch-level reference,
with line numbers, for the appendix or for checking a claim. Both were verified against
`EAISDATreeRunner.run_single_task` in `eai_sda_runner_tree.py`.

---

## Figure A — the method (for the chapter body)

```mermaid
flowchart TD
    A(["Task: initial state + goals"]) --> B

    subgraph GEN["1 · GENERATE — a TEXT check<br/>can the answer even be read? nothing has been simulated yet"]
        B["LLM writes the plan"] --> C{"is the answer readable<br/>as executable actions?"}
        C -.->|"no — ask again ONCE,<br/>saying what was malformed"| B
    end

    C -->|"unreadable both times"| X(["abort — nothing executed"])
    C -->|yes| EX

    subgraph EXEC["2 · EXECUTE — the whole plan is replayed from scratch on every attempt"]
        EX["reset the environment,<br/>run the plan action by action"]
        EX -.->|"skippable failure"| SK["skip that action,<br/>carry on with the rest"]
        SK -.-> EX
    end

    EX --> Q{"hit a blocking<br/>failure?"}

    Q -->|no| GG
    subgraph FIN["3 · FINISH"]
        GG["Goal guard —<br/>put back goal actions that got skipped"] --> GC["Goal completion —<br/>directly achieve any goal still unmet"]
        GC --> S1(["save the executed plan"])
    end

    Q -->|yes| CAP{"repair budget<br/>left?"}
    CAP -->|no| S2(["save what actually executed"])
    CAP -->|yes| DG

    subgraph REP["4 · DIAGNOSE AND REPAIR — a WORLD check<br/>does the plan actually work? own budget, unrelated to step 1"]
        DG["Diagnosis — which precondition failed,<br/>and which slice of the plan to rebuild"] --> R{"outcome"}
        R -->|"redundant, impossible,<br/>or already tried and failed"| RM["drop the action —<br/>no LLM call, no budget spent"]
        R -->|"wrong action for the job"| RP["LLM proposes<br/>a replacement"]
        R -->|"preconditions unmet"| SR["LLM proposes candidates,<br/>then SDG-constrained tree search"]
    end

    RM --> EX
    RP --> EX
    SR --> EX

    style GEN fill:#f0ecff,stroke:#7a6ac0
    style REP fill:#fff2e0,stroke:#c98a3c
    style X fill:#ffe0e0
    style S1 fill:#e0f0e0
    style S2 fill:#fff0d0
    style SK fill:#e8e8ff
```

### Two different checks, easily confused

The figure contains two loops that both re-try something. They are unrelated:

| | **Generate** (top) | **Diagnose and repair** (bottom) |
|---|---|---|
| Question asked | Is the LLM's *text* readable as actions? | Does the plan *work* in the world? |
| When | Before anything is simulated | During simulation |
| Typical fault | `"LIE": []` — no object given; truncated JSON; the character used as an object | `GRAB` with both hands full; `OPEN` on a container that is switched on |
| Limit | 2 attempts, then abort the task | `MAX_REPLAN` LLM repair calls, plus an iteration cap |
| On giving up | Nothing executes at all | Whatever executed is saved |

The top loop fires when the model's answer cannot be converted into actions, so there is
nothing to execute and therefore nothing to diagnose. It is a format problem, not a planning
problem.

### The three things this figure is for

1. **Execution is a full replay, not a resume.** Every attempt calls
   `motion_planner.reset()` and re-runs the whole plan from the start. There is no reverse
   execution anywhere in the system — which is where the implementation departs from the
   paper (spec §9.10).
2. **Not every failure stops the plan.** A skippable failure is dropped and execution
   continues, so a plan can reach the success branch *having had failures*. That is exactly
   what the goal guard exists to repair.
3. **Diagnosis has three outcomes, and only two cost budget.** Dropping an action is free;
   the two LLM paths each charge one unit of `MAX_REPLAN`.

### What Figure A deliberately hides

All of these are in Figure B; state them if a reader asks.

- Three separate code paths converge on "drop the action": the action's effect is already
  true (`already_satisfied`), the same failure just repeated (`wrong_action` retry limit),
  and the search found nothing (`tree exhausted`).
- The tree search runs **twice** — once preferring to deliver a held object to its own goal
  destination, then once falling back to a plain `DROP`.
- Two rarer outcomes loop back with the plan **unchanged** rather than dropping anything:
  object-resolution failure, and a diagnosis exception.
- The replacement path has a full-plan regeneration fallback that is *not* charged to the
  budget.
- "Skippable failure" means `ADDITIONAL_STEP` or `UNSEEN_OBJECT`; every other error type
  blocks.

---

## Figure B — branch-level reference (appendix)

Every node corresponds to code in `EAISDATreeRunner.run_single_task`. Line numbers are given
for the non-obvious branches.

```mermaid
flowchart TD
    A["Task: initial state + formal goal"] --> B["LLM: initial plan<br/>one_shot prompt, no system prompt"]
    B --> C{"parse_and_validate<br/>char_guard=reject"}
    C -->|valid| RST
    C -->|invalid| D["Corrective retry<br/>_build_retry_prompt names the fault"]
    D --> E{"parse_and_validate<br/>char_guard=strip"}
    E -->|valid| RST
    E -->|invalid| ABORT["return raw output<br/>task never enters the loop :1656"]

    RST["motion_planner.reset<br/>start of attempt"] --> EX["execute next action"]
    EX --> AF{"action failed?"}
    AF -->|no, more actions| EX
    AF -->|no, plan finished| OK{"executable?"}
    AF -->|yes| TOC["TemporalOrderChecker<br/>error type :1697"]

    TOC -->|"ADDITIONAL_STEP<br/>UNSEEN_OBJECT"| SKIP["skip action, record in<br/>skipped_indices :1704-1719"]
    SKIP --> EX
    TOC -->|any other type| UNREC["executable = False<br/>stop executing this attempt"]
    UNREC --> OK

    OK -->|"yes — skips do not count"| GG["Goal guard: relocate skipped/dropped<br/>goal actions :1744"]
    GG --> GC["scene_evaluate_wID +<br/>_attempt_goal_completion :1799"]
    GC --> SAVE1["save executed plan — DONE"]

    OK -->|no| CAP{"replan_count >= MAX_REPLAN<br/>or attempt >= max_total_iters?"}
    CAP -->|yes| SAVE2["save executed prefix — DONE :1849"]
    CAP -->|no| DIAG["diagnose_error_tree<br/>strategy + window"]
    DIAG -->|exception| SAVE3["save executed prefix — DONE :1902"]
    DIAG -->|ok| STRAT{"replan_strategy"}

    STRAT -->|already_satisfied| DEL["delete action<br/>no LLM call :2011"]
    DEL --> RST

    STRAT -->|wrong_action| RPT{"same failure<br/>signature as last?"}
    RPT -->|yes| DROP1["drop action<br/>no LLM call :2074"]
    DROP1 --> RST
    RPT -->|no| WAP["WRONG_ACTION_PROMPT<br/>replan_count += 1"]
    WAP --> WOK{"replacement parses?"}
    WOK -->|yes| SPL["splice into plan"]
    WOK -->|no| REGEN["full-plan regeneration<br/>uncounted LLM call :2108"]
    REGEN --> RST

    STRAT -->|"local / insert_prep / reconstruct<br/>one shared path"| SUG["SUGGESTION_PROMPT<br/>replan_count += 1"]
    SUG --> BFS["BFS subtree search<br/>pass 1: prefer_goal_placement=True<br/>pass 2: False"]
    BFS --> TR{"tree found a repair?"}
    TR -->|no| DROP2["tree exhausted:<br/>drop action :2199"]
    DROP2 --> RST
    TR -->|yes| RES{"subtree_results_to_eai<br/>resolves all objects?"}
    RES -->|no| NOOP["continue, plan unchanged :2215"]
    NOOP --> RST
    RES -->|yes| SPL
    SPL --> RST

    style ABORT fill:#ffe0e0
    style SAVE1 fill:#e0f0e0
    style SAVE2 fill:#fff0d0
    style SAVE3 fill:#fff0d0
    style SKIP fill:#e8e8ff
```

### Six corrections against the first version of this diagram

1. **`TemporalOrderChecker` runs inside the execution loop**, per failed action, not after
   the budget check.
2. **The `ADDITIONAL_STEP` / `UNSEEN_OBJECT` skip path exists** and returns to execution
   without ever reaching diagnosis. Consequently the success test is *"was there an
   unrecoverable failure?"*, not *"did all actions succeed?"* — a plan reaches the success
   branch with `executable = True` despite having had failures. This is also why the goal
   guard exists.
3. **A failed retry aborts the task** before the loop is entered.
4. **`wrong_action` has a no-LLM path** (repeated failure signature → drop) and a full-plan
   regeneration fallback that is not charged to `replan_count`.
5. **The BFS branch has two non-splice exits**: tree exhausted → drop the action; object
   resolution failed → continue with the plan unchanged. The search itself is two passes.
6. **Three distinct exits save the executed prefix**: the budget/iteration cap and the
   diagnosis exception. (Success saves the executed plan plus guard/completion appends.)

### Budget accounting

`replan_count` increments at exactly two nodes — `WRONG_ACTION_PROMPT` and
`SUGGESTION_PROMPT`. Every `drop` / `delete` node is free, bounded instead by
`max_total_iters = MAX_REPLAN + len(initial_plan) + 4`. The `full-plan regeneration` node is
an LLM call that escapes the count.

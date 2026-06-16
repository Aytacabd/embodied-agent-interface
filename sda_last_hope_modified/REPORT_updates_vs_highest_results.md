# Technical Report: `updates` vs `last update highest restuls`

**Subject:** Differences between the two SDA-Planner commits and why the later one produced higher benchmark results
**Component:** VirtualHome action-sequencing evaluation (EAI benchmark)

| | Commit A | Commit B |
|---|---|---|
| Message | `updates` | `last update highest restuls` |
| Hash | `97cfa88` | `dff077a` |
| Author / date | u501632 — Apr 27 2026, 03:06 | u501632 — Apr 27 2026, 14:24 |
| Files touched | — | `eai_sda_runner_tree.py` (36 lines), `eval_utils.py` (32 lines), `run_full_20260427_0159.txt` (+13,666 lines, log only) |

---

## 1. Executive summary

The two commits are roughly eleven hours apart on the same day. The later commit
(`dff077a`, "highest results") is **not** a smarter planning algorithm — it is a
**data-format fix**. Commit A emitted predicted plans in a *combined* object-token
format (`["light_245"]`) that the official EAI grader could not parse, so a large
share of otherwise-valid plans were silently rejected at scoring time as
"parameter errors." Commit B switched the saved output to the *interleaved*
`[name, id]` format the grader natively expects, and updated the grammar check,
the JSON parser, and `json_to_action` to support that format on both the runner
side and the grader side. Aligning the output format with the grader is what
produced the higher executability and goal-success numbers.

---

## 2. Root cause: object-token format mismatch

The EAI grader validates argument counts in `evaluate_results.py` via
`check_action_grammar`, which computes:

```python
len(params) // 2 != valid_actions[predicate_name][1]
```

The `// 2` assumes the **interleaved** format, where each object occupies two list
entries: a name and an id.

* **Commit A (combined format):** the saved plan looked like
  `"WALK": ["light_245"]`. For this, `len(params) = 1`, and `1 // 2 = 0`, while the
  expected argument count for `WALK` is `1`. The check fails → the plan is flagged
  as a *parameter error* → it is discarded before it is ever executed or scored.
  This penalized many structurally-correct plans purely on a formatting
  technicality.

* **Commit B (interleaved format):** the saved plan looks like
  `"WALK": ["light", "245"]`. Now `len(params) = 2`, and `2 // 2 = 1`, which matches
  the expected count. The plan is parsed, executed, and graded normally.

This single difference accounts for the bulk of the score increase: Commit B did
not make the model produce better plans — it stopped good plans from being thrown
away.

---

## 3. Detailed changes

### 3.1 `eai_sda_runner_tree.py`

| Area | Commit A | Commit B |
|---|---|---|
| Final output (`plan_to_json_str`) | combined `["light_245"]` | interleaved `["light", "245"]` |
| Grammar check | imported `check_action_grammar` from `eval_utils` | local `_check_grammar_combined` |
| LLM JSON parsing (`parse_llm_output`) | imported `load_json_preserving_order` | inline regex over the response |
| Imports | `check_action_grammar`, `load_json_preserving_order` | `valid_actions as _eai_valid_actions` |

* **`plan_to_json_str`** — the decisive change. It now writes each object as two
  tokens (`"name", "id"`) instead of one fused token (`"name_id"`), so the saved
  prediction matches the grader's expected schema.
* **`_check_grammar_combined`** (new) — the runner uses a combined `name_id`
  representation *internally* (one token per object). The grader's
  `check_action_grammar` counts arguments as `len // 2`, which is wrong for the
  internal one-token-per-object form. Rather than fight that, Commit B added a
  small local grammar checker that counts one token per object for internal
  validation, while the *exported* output is still converted to the interleaved
  form for the grader.
* **Inline JSON parsing** — `parse_llm_output` no longer depends on
  `load_json_preserving_order`; it applies its own regex
  (`r'"(\w+)"\s*:\s*(\[\s*\]|\[[^\]]+\])'`) so the runner controls its own parsing
  and correctly captures empty arrays (e.g. `"STANDUP": []`).

### 3.2 `eval_utils.py`

* **`json_to_action` made format-agnostic.** Commit B added a `len(objects) == 1`
  branch (combined single-object `["light_245"]`) and made the `len(objects) == 2`
  branch auto-detect the format: if `objects[1]` is a digit it is treated as an
  interleaved one-argument action (`["light", "245"]`); otherwise it is treated as
  a two-argument combined action (`["apple_7", "fridge_2"]`). The four-token
  interleaved two-argument form (`["apple", "7", "fridge", "2"]`) is retained. The
  function now accepts all three representations.
* **`load_json_preserving_order` regex tweak.** Changed from
  `r'"(\w+)"\s*:\s*(\[[^\]]*\])'` to
  `r'"(\w+)"\s*:\s*(\[\s*\]|\[[^\]]+\])'` to robustly capture empty argument lists
  for zero-argument actions.

### 3.3 `run_full_20260427_0159.txt`

A ~13,666-line captured console log of the run. This is evidence/output only — it
contains no code and has no effect on behavior or scoring.

---

## 4. Why the results improved

1. **Fewer false rejections.** In Commit A, many plans failed the grader's
   `len // 2` argument-count check and were recorded as parameter errors. In
   Commit B those same plans pass the check, so they proceed to execution and goal
   evaluation.
2. **Consistent round-trip.** With the interleaved output and the generalized
   `json_to_action`, the prediction the runner saves is exactly the prediction the
   grader re-parses — no information is lost or mis-split between the two stages.
3. **No grader leniency was added.** The improvement comes from format
   correctness, not from relaxing any scoring rule, so the higher numbers reflect
   genuinely gradable plans rather than an easier metric.

---

## 5. Risks and notes

* The change is confined to **how predictions are serialized and parsed**, not to
  the planning logic or the simulator's executability rules. It does not alter what
  counts as a successful action.
* Because `json_to_action` now accepts multiple formats, any downstream consumer
  must keep relying on `relevant_name_to_id` for id resolution; ambiguous or
  malformed tokens still raise on lookup, which is the intended strict behavior.
* For fair comparison, any baseline numbers should be regenerated with the same
  (Commit B) serialization, since Commit A's combined format would understate a
  baseline for the same format-rejection reason described in Section 2.

---

## 6. Conclusion

`updates` → `last update highest restuls` is, at its core, a **single corrective
change**: predicted plans are now serialized in the interleaved `[name, id]` JSON
format that the EAI grader expects, with the grammar check, JSON parser, and
`json_to_action` updated to support that format end-to-end. The previous commit's
combined `name_id` format failed the grader's argument-count check and caused valid
plans to be discarded; fixing the format let those plans be scored, which is the
reason for the "highest results" label. The accompanying log file is output only.

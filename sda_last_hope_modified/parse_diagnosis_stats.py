"""
Parses an eai_sda_runner_tree.py run log into structured statistics about
the error-diagnosis / repair-search mechanism: for every diagnosed failure,
captures (error_type, replan_strategy, unsatisfied_needs, outcome), plus
per-task rollups (replans used, tree successes, final result).

Outcome classification, matched against the runner's actual control flow
(eai_sda_runner_tree.py):
  - already_satisfied  -> always "resolved" (direct removal, no search)
  - wrong_action       -> "resolved" if "Replaced with:" follows,
                          "gave_up" if "dropped" follows (repeat failure)
  - everything else (reconstruct / insert_prep / local) shares the tree
    search path -> "resolved" if "Tree found:" follows,
                   "gave_up" if "Tree exhausted" follows
"""
import re
import sys
import json
from collections import Counter, defaultdict

def parse_log(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    diagnoses = []
    current_task = None
    task_order = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        m_task = re.search(r"^\s*TASK:\s*(\S+)\s*\|", line)
        if m_task:
            current_task = m_task.group(1)
            task_order.append(current_task)
            i += 1
            continue

        if "ERROR DIAGNOSIS:" in line:
            block = lines[i:i+7]
            text = "".join(block)
            err_type = re.search(r"Error type\s*:\s*(\w+)", text)
            strategy = re.search(r"Replan strategy\s*:\s*(\w+)", text)
            unsat = re.search(r"Unsatisfied needs:\s*(\[[^\]]*\])", text)
            failed_action = re.search(r"Failed action\s*:\s*(.+)", text)

            needs = []
            if unsat:
                try:
                    needs = eval(unsat.group(1))
                except Exception:
                    needs = []

            # Scan forward for the outcome (bounded window; next diagnosis
            # or task boundary ends the search)
            outcome = "unknown"
            j = i + 1
            limit = min(i + 60, n)
            while j < limit:
                l2 = lines[j]
                if "TASK:" in l2 or "ERROR DIAGNOSIS:" in l2:
                    break
                if "ACTION ALREADY SATISFIED" in l2:
                    outcome = "resolved"; break
                if "Replaced with:" in l2:
                    outcome = "resolved"; break
                if "Wrong-action fix did not help" in l2:
                    outcome = "gave_up"; break
                if "Tree found:" in l2:
                    outcome = "resolved"; break
                if "Tree exhausted" in l2:
                    outcome = "gave_up"; break
                j += 1

            if strategy and strategy.group(1) == "already_satisfied":
                outcome = "resolved"

            diagnoses.append({
                "task": current_task,
                "error_type": err_type.group(1) if err_type else None,
                "replan_strategy": strategy.group(1) if strategy else None,
                "unsatisfied_needs": needs,
                "outcome": outcome,
                "failed_action_verb": (
                    re.search(r"\[(\w+)\]", failed_action.group(1)).group(1)
                    if failed_action and re.search(r"\[(\w+)\]", failed_action.group(1))
                    else None
                ),
            })
            i += 7
            continue

        i += 1

    return diagnoses, task_order


def summarize(diagnoses, task_order, error_info=None):
    total = len(diagnoses)
    by_error_type = Counter(d["error_type"] for d in diagnoses)
    by_strategy = Counter(d["replan_strategy"] for d in diagnoses)
    by_outcome = Counter(d["outcome"] for d in diagnoses)
    by_verb = Counter(d["failed_action_verb"] for d in diagnoses)

    need_counter = Counter()
    for d in diagnoses:
        for need in d["unsatisfied_needs"]:
            need_counter[need] += 1

    strategy_outcome = defaultdict(Counter)
    for d in diagnoses:
        strategy_outcome[d["replan_strategy"]][d["outcome"]] += 1

    errtype_outcome = defaultdict(Counter)
    for d in diagnoses:
        errtype_outcome[d["error_type"]][d["outcome"]] += 1

    per_task = Counter(d["task"] for d in diagnoses)
    tasks_with_diagnosis = len(per_task)
    tasks_total = len(set(task_order))

    resolved = by_outcome.get("resolved", 0)
    gave_up = by_outcome.get("gave_up", 0)

    return {
        "total_diagnoses": total,
        "tasks_total": tasks_total,
        "tasks_with_at_least_one_diagnosis": tasks_with_diagnosis,
        "tasks_with_zero_diagnosis_clean_first_try": tasks_total - tasks_with_diagnosis,
        "resolved_count": resolved,
        "gave_up_count": gave_up,
        "resolve_rate_pct": round(100 * resolved / total, 1) if total else 0,
        "by_error_type": dict(by_error_type.most_common()),
        "by_replan_strategy": dict(by_strategy.most_common()),
        "by_outcome": dict(by_outcome.most_common()),
        "by_failed_verb": dict(by_verb.most_common(15)),
        "by_unsatisfied_need": dict(need_counter.most_common()),
        "strategy_outcome_breakdown": {k: dict(v) for k, v in strategy_outcome.items()},
        "errtype_outcome_breakdown": {k: dict(v) for k, v in errtype_outcome.items()},
        "diagnoses_per_task_histogram": dict(Counter(per_task.values()).most_common()),
        "max_diagnoses_single_task": max(per_task.values()) if per_task else 0,
    }


if __name__ == "__main__":
    log_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    diagnoses, task_order = parse_log(log_path)
    stats = summarize(diagnoses, task_order)
    stats["_raw_diagnoses_sample"] = diagnoses[:5]
    output = json.dumps(stats, indent=2)
    if out_path:
        with open(out_path, "w") as f:
            f.write(output)
        print(f"Wrote {out_path}")
        print(json.dumps({k: v for k, v in stats.items() if k != "_raw_diagnoses_sample"}, indent=2))
    else:
        print(output)

"""
error_diagnosis_tree.py
=======================
Extended diagnosis wrapper for the full SDA-Planner pipeline.
Adds original_subsequence and error_objects to the standard DiagnosisResult
so the Adaptive Action SubTree Generation module can use them.
"""

from error_diagnosis import (
    ActionStep,
    DiagnosisResult,
    StateTracker,
    diagnose_error,
)
from sdg import get_preconditions, get_effects, is_prep_action, explain_precondition


def diagnose_error_tree(
    action_history: list,
    failed_step:    ActionStep,
    error_type:     str,
    full_plan:      list,
    char_sitting:   bool = False,
    char_lying:     bool = False,
    env_dict:       dict = None,
) -> tuple:
    """
    Extended diagnosis returning:
      (DiagnosisResult, original_subsequence, error_objects)

    original_subsequence : ActionSteps from [t_start, t_end] in full_plan
    error_objects        : set of object names involved in the error
    """
    result = diagnose_error(
        action_history = action_history,
        failed_step    = failed_step,
        error_type     = error_type,
        full_plan      = full_plan,
        char_sitting   = char_sitting,
        char_lying     = char_lying,
        env_dict       = env_dict,
    )

    t_start = result.t_start if result.t_start is not None else failed_step.index
    t_end   = result.t_end   if result.t_end   is not None else failed_step.index

    original_subsequence = [
        s for s in full_plan
        if t_start <= s.index <= t_end
    ]

    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)

    return result, original_subsequence, error_objects


def get_unsatisfied_explanation(unsatisfied_needs: list) -> str:
    """Human-readable explanation of unsatisfied preconditions for LLM prompt."""
    if not unsatisfied_needs:
        return "The action failed due to an unexpected environment state."
    return "\n".join(
        f"  - {explain_precondition(p)}"
        for p in unsatisfied_needs
    )


if __name__ == "__main__":
    env = {
        "nodes": [
            {"id": 1, "class_name": "character",        "states": [],          "properties": []},
            {"id": 2, "class_name": "washing_machine",  "states": ["CLOSED"],  "properties": ["CAN_OPEN"]},
            {"id": 3, "class_name": "clothes",          "states": [],          "properties": ["GRABBABLE", "CLOTHES"]},
        ],
        "edges": [
            {"from_id": 3, "to_id": 2, "relation_type": "INSIDE"},
            {"from_id": 1, "to_id": 2, "relation_type": "CLOSE"},
        ],
    }

    history = [ActionStep(1, "WALK", "washing_machine")]
    failed  = ActionStep(2, "GRAB", "clothes")
    plan    = history + [failed]

    result, orig_subseq, err_objs = diagnose_error_tree(
        history, failed, "MISSING_STEP", plan, env_dict=env
    )
    print("Diagnosis       :", result)
    print("Original subseq :", orig_subseq)
    print("Error objects   :", err_objs)
    assert "obj_not_inside_closed_container" in result.unsatisfied_needs, \
        f"Expected container precondition, got {result.unsatisfied_needs}"
    print("✅ Test passed")

"""
error_diagnosis_tree.py
=======================
Extended version of error_diagnosis.py that integrates
with the Adaptive Action SubTree Generation module.

Key addition: diagnose_error_tree() returns candidate nodes
alongside the standard DiagnosisResult, enabling the
search tree to use both LLM suggestions and original
subsequence actions as candidates.
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
    Extended diagnosis that also returns:
    - original_subsequence: actions from [t_start, t_end] in full_plan
    - error_objects: set of objects involved in the error

    Returns (DiagnosisResult, original_subsequence, error_objects)
    """

    # Run standard diagnosis
    result = diagnose_error(
        action_history = action_history,
        failed_step    = failed_step,
        error_type     = error_type,
        full_plan      = full_plan,
        char_sitting   = char_sitting,
        char_lying     = char_lying,
        env_dict       = env_dict,
    )

    # Extract original failing subsequence [t_start, t_end]
    t_start = result.t_start or failed_step.index
    t_end   = result.t_end   or failed_step.index

    original_subsequence = [
        s for s in full_plan
        if t_start <= s.index <= t_end
    ]

    # Identify error objects (failed action's objects + objects in unsatisfied needs)
    error_objects = {failed_step.obj}
    if failed_step.target:
        error_objects.add(failed_step.target)

    return result, original_subsequence, error_objects


def get_unsatisfied_explanation(unsatisfied_needs: list) -> str:
    """Human-readable explanation of unsatisfied preconditions for LLM prompt."""
    if not unsatisfied_needs:
        return "The action is in an unexpected environment state."
    return "\n".join(
        f"  - {explain_precondition(p)}"
        for p in unsatisfied_needs
    )


if __name__ == "__main__":
    history = [ActionStep(1, "WALK", "washing_machine")]
    failed  = ActionStep(2, "GRAB", "clothes")
    plan    = history + [failed]

    result, orig_subseq, err_objs = diagnose_error_tree(
        history, failed, "MISSING_STEP", plan
    )
    print("Diagnosis:", result)
    print("Original subsequence:", orig_subseq)
    print("Error objects:", err_objs)

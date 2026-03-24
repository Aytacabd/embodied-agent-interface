# """
# run_with_sda.py
# ===============
# Main pipeline: EAI + SDA-Planner Feedback Loop

# Flow:
# 1. Load EAI-generated prompts for VirtualHome tasks
# 2. Send to Llama 3.3 70B via Groq → get initial plan
# 3. Simulate plan in VirtualHome (via EAI's executor)
# 4. If action fails → SDA diagnosis → feedback → replan
# 5. Save results for EAI evaluation
# 6. Compare with baseline (no feedback)

# Usage:
#     python3 run_with_sda.py --task_file <path> --output_dir <path>
# """

# import os
# import json
# import argparse
# from pathlib import Path
# from dataclasses import dataclass, field

# from sdg import get_preconditions, is_prep_action
# from error_diagnosis import ActionStep, DiagnosisResult, diagnose_error
# from llm_client import LLMClient

# # ─────────────────────────────────────────────
# # Configuration
# # ─────────────────────────────────────────────

# MAX_REPLAN_ATTEMPTS = 3   # max times we try to replan per task
# DEFAULT_OUTPUT_DIR  = "./output_sda"
# DEFAULT_TASK_FILE   = "./tasks/virtualhome_tasks.json"


# # ─────────────────────────────────────────────
# # Data Classes
# # ─────────────────────────────────────────────

# @dataclass
# class TaskResult:
#     """Stores the result of running one task."""
#     task_id:           str
#     task_instruction:  str
#     initial_plan:      list = field(default_factory=list)
#     final_plan:        list = field(default_factory=list)
#     replan_count:      int  = 0
#     success:           bool = False
#     error_types:       list = field(default_factory=list)
#     diagnoses:         list = field(default_factory=list)


# # ─────────────────────────────────────────────
# # Action Parser
# # Converts EAI-style strings to ActionStep objects
# # ─────────────────────────────────────────────

# def parse_action_string(action_str: str, index: int) -> ActionStep:
#     """
#     Parse EAI action string into ActionStep.
#     Handles formats like:
#         [WALK] <phone> (247)
#         [PUTBACK] <phone> (247) <desk> (357)
#     """
#     import re

#     # Extract action name
#     action_match = re.search(r'\[(\w+)\]', action_str)
#     if not action_match:
#         return ActionStep(index, "UNKNOWN", "unknown")
#     action = action_match.group(1).upper()

#     # Extract object names (ignore IDs in parentheses)
#     objects = re.findall(r'<([^>]+)>', action_str)

#     if len(objects) == 0:
#         return ActionStep(index, action, "unknown")
#     elif len(objects) == 1:
#         return ActionStep(index, action, objects[0])
#     else:
#         return ActionStep(index, action, objects[0], objects[1])


# def parse_plan(plan_strings: list) -> list:
#     """Convert list of action strings to list of ActionStep objects."""
#     return [parse_action_string(s, i+1) for i, s in enumerate(plan_strings)]


# # ─────────────────────────────────────────────
# # EAI Execution Simulator
# # Checks if an action is executable given current state
# # This is a lightweight version — full version uses VirtualHome
# # ─────────────────────────────────────────────

# class SimpleExecutor:
#     """
#     Lightweight execution checker based on SDG.
#     Used for testing without full VirtualHome simulator.
#     For full evaluation, use EAI's built-in executor.
#     """

#     def __init__(self):
#         self.state = {
#             "not_sitting":   True,
#             "not_lying":     True,
#             "holds_obj":     False,
#             "hand_count":    0,
#         }
#         self.history = []

#     def can_execute(self, step: ActionStep) -> tuple:
#         """
#         Check if action can be executed.
#         Returns (can_execute: bool, error_type: str, unsatisfied: list)
#         """
#         preconditions = get_preconditions(step.action)
#         unsatisfied   = []

#         for precond in preconditions:
#             if not self._check_precond(precond, step):
#                 unsatisfied.append(precond)

#         if unsatisfied:
#             return False, "MISSING_STEP", unsatisfied

#         return True, None, []

#     def execute(self, step: ActionStep) -> bool:
#         """Execute action and update state."""
#         can_exec, _, _ = self.can_execute(step)
#         if can_exec:
#             self._apply_effects(step)
#             self.history.append(step)
#             return True
#         return False

#     def _check_precond(self, precond: str, step: ActionStep) -> bool:
#         """Check a single precondition."""
#         # Map preconditions to state checks
#         checks = {
#             "not_sitting":                     lambda: self.state.get("not_sitting", True),
#             "not_lying":                       lambda: self.state.get("not_lying", True),
#             "next_to_obj":                     lambda: True,   # simplified
#             "next_to_target":                  lambda: True,   # simplified
#             "grabbable":                       lambda: True,   # simplified
#             "not_both_hands_full":             lambda: self.state.get("hand_count", 0) < 2,
#             "obj_not_inside_closed_container": lambda: True,   # simplified
#             "holds_obj":                       lambda: self.state.get("hand_count", 0) > 0,
#             "target_open_or_not_openable":     lambda: True,   # simplified
#             "can_open":                        lambda: True,   # simplified
#             "closed":                          lambda: True,   # simplified
#             "open":                            lambda: self.state.get("open", False),
#             "not_on":                          lambda: not self.state.get("on", False),
#             "has_switch":                      lambda: True,   # simplified
#             "off":                             lambda: not self.state.get("on", False),
#             "on":                              lambda: self.state.get("on", False),
#             "plugged_in":                      lambda: self.state.get("plugged_in", True),
#             "sitting_or_lying":                lambda: (self.state.get("sitting", False) or
#                                                         self.state.get("lying", False)),
#         }
#         checker = checks.get(precond)
#         return checker() if checker else True

#     def _apply_effects(self, step: ActionStep):
#         """Apply action effects to state."""
#         action = step.action
#         if action == "GRAB":
#             self.state["hand_count"] = self.state.get("hand_count", 0) + 1
#             self.state["holds_obj"]  = True
#         elif action in ("PUTBACK", "PUT_ON", "PUT_INSIDE", "DROP"):
#             count = max(0, self.state.get("hand_count", 1) - 1)
#             self.state["hand_count"] = count
#             self.state["holds_obj"]  = count > 0
#         elif action == "WALK":
#             self.state["not_sitting"] = True
#             self.state["not_lying"]   = True
#         elif action == "SIT":
#             self.state["sitting"]     = True
#             self.state["not_sitting"] = False
#         elif action == "STANDUP":
#             self.state["sitting"]     = False
#             self.state["lying"]       = False
#             self.state["not_sitting"] = True
#             self.state["not_lying"]   = True
#         elif action == "OPEN":
#             self.state["open"]   = True
#             self.state["closed"] = False
#         elif action == "CLOSE":
#             self.state["open"]   = False
#             self.state["closed"] = True
#         elif action == "SWITCHON":
#             self.state["on"]  = True
#             self.state["off"] = False
#         elif action == "SWITCHOFF":
#             self.state["on"]  = False
#             self.state["off"] = True


# # ─────────────────────────────────────────────
# # SDA Pipeline
# # ─────────────────────────────────────────────

# class SDAPipeline:
#     """
#     Full SDA-Planner pipeline integrated with EAI.
#     """

#     def __init__(self, api_key: str = None):
#         self.llm      = LLMClient(api_key=api_key)
#         self.results  = []

#     def run_task(self, task_id: str, task_instruction: str,
#                  objects_in_scene: list = None) -> TaskResult:
#         """
#         Run a single task with SDA feedback loop.

#         Args:
#             task_id           : unique task identifier
#             task_instruction  : natural language instruction
#             objects_in_scene  : available objects (optional)

#         Returns:
#             TaskResult with final plan and statistics
#         """
#         print(f"\n{'='*60}")
#         print(f"Task: {task_instruction}")
#         print(f"{'='*60}")

#         result         = TaskResult(task_id=task_id, task_instruction=task_instruction)
#         objects        = objects_in_scene or []
#         replan_count   = 0

#         # ── Step 1: Generate initial plan ───────────────────────────────────
#         print("\n[1] Generating initial plan...")
#         plan_strings = self.llm.generate_initial_plan(task_instruction, objects)
#         plan         = parse_plan(plan_strings)
#         result.initial_plan = plan_strings

#         print(f"    Initial plan ({len(plan)} steps):")
#         for step in plan:
#             print(f"      {step}")

#         # ── Step 2: Execute with SDA feedback loop ───────────────────────────
#         executor        = SimpleExecutor()
#         executed        = []
#         current_plan    = plan[:]
#         step_idx        = 0

#         while step_idx < len(current_plan) and replan_count < MAX_REPLAN_ATTEMPTS:
#             step    = current_plan[step_idx]
#             can_run, error_type, unsatisfied = executor.can_execute(step)

#             if can_run:
#                 # ── Success: execute and move on ─────────────────────────────
#                 executor.execute(step)
#                 executed.append(step)
#                 print(f"\n  ✅ Step {step_idx+1}: {step} → OK")
#                 step_idx += 1

#             else:
#                 # ── Failure: trigger SDA diagnosis ───────────────────────────
#                 print(f"\n  ❌ Step {step_idx+1}: {step} → FAILED ({error_type})")
#                 result.error_types.append(error_type)
#                 replan_count += 1

#                 # Diagnose the error
#                 diagnosis = diagnose_error(
#                     action_history = executed,
#                     failed_step    = step,
#                     error_type     = error_type,
#                     full_plan      = current_plan,
#                 )
#                 result.diagnoses.append(str(diagnosis))

#                 print(f"\n  🔍 Diagnosis:")
#                 print(f"     Root cause : {diagnosis.root_cause}")
#                 print(f"     Unsatisfied: {diagnosis.unsatisfied_needs}")
#                 print(f"     Strategy   : {diagnosis.replan_strategy}")
#                 print(f"     Replan window: [{diagnosis.t_start}, {diagnosis.t_end}]")

#                 # Get remaining plan after reconstruction window
#                 remaining = [
#                     str(s) for s in current_plan
#                     if s.index > diagnosis.t_end
#                 ]

#                 # Ask LLM to replan
#                 print(f"\n  🔄 Replanning (attempt {replan_count}/{MAX_REPLAN_ATTEMPTS})...")
#                 corrected_strings = self.llm.replan_with_feedback(
#                     feedback_message = diagnosis.feedback_message,
#                     task_instruction = task_instruction,
#                     remaining_plan   = remaining,
#                 )

#                 print(f"     Corrected subsequence:")
#                 for s in corrected_strings:
#                     print(f"       {s}")

#                 # Rebuild plan: executed + corrected + remaining
#                 corrected_steps = parse_plan(corrected_strings)
#                 # Re-index corrected steps
#                 # base_idx = diagnosis.t_start
#                 # for i, s in enumerate(corrected_steps):
#                 #     s.index = base_idx + i
#                 # Re-index corrected steps continuously
#                 base_idx = len(executed) + 1
#                 for i, s in enumerate(corrected_steps):
#                     s.index = base_idx + i

#                 # Re-index remaining steps
#                 remaining_steps = [
#                     s for s in current_plan if s.index > diagnosis.t_end
#                 ]
#                 for i, s in enumerate(remaining_steps):
#                     s.index = base_idx + len(corrected_steps) + i

#                 # New plan = already executed + corrected + remaining
#                 current_plan = executed + corrected_steps + remaining_steps
#                 step_idx     = len(executed)  # restart from correction point

#         # ── Step 3: Check final success ──────────────────────────────────────
#         result.final_plan    = [str(s) for s in current_plan]
#         result.replan_count  = replan_count
#         result.success       = (step_idx >= len(current_plan))

#         print(f"\n{'─'*60}")
#         print(f"Final Result: {'✅ SUCCESS' if result.success else '❌ INCOMPLETE'}")
#         print(f"Replanning attempts: {replan_count}")
#         print(f"Final plan:")
#         for s in result.final_plan:
#             print(f"  {s}")

#         return result

#     def run_from_file(self, task_file: str, output_dir: str):
#         """
#         Run all tasks from a JSON file and save results.

#         Task file format:
#         [
#             {
#                 "task_id": "task_001",
#                 "instruction": "Pick up the phone and put it on the desk",
#                 "objects": ["phone", "desk", "table"]
#             },
#             ...
#         ]
#         """
#         # Load tasks
#         with open(task_file, "r") as f:
#             tasks = json.load(f)

#         print(f"Loaded {len(tasks)} tasks from {task_file}")

#         # Create output directory
#         Path(output_dir).mkdir(parents=True, exist_ok=True)

#         all_results = []
#         success_count = 0

#         for task in tasks:
#             result = self.run_task(
#                 task_id          = task.get("task_id", "unknown"),
#                 task_instruction = task["instruction"],
#                 objects_in_scene = task.get("objects", []),
#             )
#             all_results.append(result)
#             if result.success:
#                 success_count += 1

#             # Save individual result
#             output_path = Path(output_dir) / f"{result.task_id}.json"
#             with open(output_path, "w") as f:
#                 json.dump({
#                     "task_id":          result.task_id,
#                     "instruction":      result.task_instruction,
#                     "initial_plan":     result.initial_plan,
#                     "final_plan":       result.final_plan,
#                     "replan_count":     result.replan_count,
#                     "success":          result.success,
#                     "error_types":      result.error_types,
#                 }, f, indent=2)

#         # Save summary
#         summary = {
#             "total_tasks":    len(tasks),
#             "success_count":  success_count,
#             "success_rate":   round(success_count / len(tasks) * 100, 2),
#             "avg_replans":    round(sum(r.replan_count for r in all_results) / len(all_results), 2),
#         }
#         with open(Path(output_dir) / "summary.json", "w") as f:
#             json.dump(summary, f, indent=2)

#         print(f"\n{'='*60}")
#         print(f"FINAL SUMMARY")
#         print(f"{'='*60}")
#         print(f"Total tasks   : {summary['total_tasks']}")
#         print(f"Success rate  : {summary['success_rate']}%")
#         print(f"Avg replans   : {summary['avg_replans']}")
#         print(f"Results saved : {output_dir}")

#         return all_results


# # ─────────────────────────────────────────────
# # Quick Test
# # ─────────────────────────────────────────────

# if __name__ == "__main__":
#     pipeline = SDAPipeline(api_key=os.environ.get("GROQ_API_KEY"))

#     # Test with a simple task
#     # result = pipeline.run_task(
#     #     task_id          = "test_001",
#     #     task_instruction = "Pick up the phone and put it on the desk",
#     #     objects_in_scene = ["phone", "desk", "table", "chair"],
#     # )
#     # Test with a task that requires replanning
#     # result = pipeline.run_task(
#     #     task_id          = "test_002",
#     #     task_instruction = "Put the book inside the drawer",
#     #     objects_in_scene = ["book", "drawer", "desk", "shelf"],
#     # )
#     # Add this to run_with_sda.py to force a failure scenario
#     result = pipeline.run_task(
#         task_id          = "test_003",
#         task_instruction = "Wash the dishes and put them in the cupboard",
#         objects_in_scene = ["plate", "cupboard", "sink", "dish_soap", "sponge"],
#     )

#     print("\n\n=== TASK COMPLETE ===")
#     print(f"Success       : {result.success}")
#     print(f"Replan count  : {result.replan_count}")
#     print(f"Error types   : {result.error_types}")
"""
run_with_sda.py
===============
Main pipeline: EAI + SDA-Planner Feedback Loop

Flow:
1. Load EAI-generated prompts for VirtualHome tasks
2. Send to Llama 3.3 70B via Groq → get initial plan
3. Simulate plan in VirtualHome (via EAI's executor)
4. If action fails → SDA diagnosis → feedback → replan
5. Save results for EAI evaluation
6. Compare with baseline (no feedback)

Usage:
    python3 run_with_sda.py --task_file <path> --output_dir <path>
"""

import os
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field

from sdg import get_preconditions, is_prep_action
from error_diagnosis import ActionStep, DiagnosisResult, diagnose_error
from llm_client import LLMClient

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

MAX_REPLAN_ATTEMPTS = 3   # max times we try to replan per task
DEFAULT_OUTPUT_DIR  = "./output_sda"
DEFAULT_TASK_FILE   = "./tasks/virtualhome_tasks.json"


# ─────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────

@dataclass
class TaskResult:
    """Stores the result of running one task."""
    task_id:           str
    task_instruction:  str
    initial_plan:      list = field(default_factory=list)
    final_plan:        list = field(default_factory=list)
    replan_count:      int  = 0
    success:           bool = False
    error_types:       list = field(default_factory=list)
    diagnoses:         list = field(default_factory=list)


# ─────────────────────────────────────────────
# Action Parser
# Converts EAI-style strings to ActionStep objects
# ─────────────────────────────────────────────

def parse_action_string(action_str: str, index: int) -> ActionStep:
    """
    Parse EAI action string into ActionStep.
    Handles formats like:
        [WALK] <phone> (247)
        [PUTBACK] <phone> (247) <desk> (357)
    """
    import re

    # Extract action name
    action_match = re.search(r'\[(\w+)\]', action_str)
    if not action_match:
        return ActionStep(index, "UNKNOWN", "unknown")
    action = action_match.group(1).upper()

    # Extract object names (ignore IDs in parentheses)
    objects = re.findall(r'<([^>]+)>', action_str)

    if len(objects) == 0:
        return ActionStep(index, action, "unknown")
    elif len(objects) == 1:
        return ActionStep(index, action, objects[0])
    else:
        return ActionStep(index, action, objects[0], objects[1])


def parse_plan(plan_strings: list) -> list:
    """Convert list of action strings to list of ActionStep objects."""
    return [parse_action_string(s, i+1) for i, s in enumerate(plan_strings)]


# ─────────────────────────────────────────────
# EAI Execution Simulator
# Checks if an action is executable given current state
# This is a lightweight version — full version uses VirtualHome
# ─────────────────────────────────────────────

class SimpleExecutor:
    """
    Lightweight execution checker based on SDG.
    Used for testing without full VirtualHome simulator.
    For full evaluation, use EAI's built-in executor.
    """

    def __init__(self):
        self.state = {
            "not_sitting":   True,
            "not_lying":     True,
            "holds_obj":     False,
            "hand_count":    0,
        }
        self.history = []

    def can_execute(self, step: ActionStep) -> tuple:
        """
        Check if action can be executed.
        Returns (can_execute: bool, error_type: str, unsatisfied: list)
        """
        preconditions = get_preconditions(step.action)
        unsatisfied   = []

        for precond in preconditions:
            if not self._check_precond(precond, step):
                unsatisfied.append(precond)

        if unsatisfied:
            return False, "MISSING_STEP", unsatisfied

        return True, None, []

    def execute(self, step: ActionStep) -> bool:
        """Execute action and update state."""
        can_exec, _, _ = self.can_execute(step)
        if can_exec:
            self._apply_effects(step)
            self.history.append(step)
            return True
        return False

    def _check_precond(self, precond: str, step: ActionStep) -> bool:
        """Check a single precondition."""
        # Map preconditions to state checks
        checks = {
            "not_sitting":                     lambda: self.state.get("not_sitting", True),
            "not_lying":                       lambda: self.state.get("not_lying", True),
            "next_to_obj":                     lambda: True,   # simplified
            "next_to_target":                  lambda: True,   # simplified
            "grabbable":                       lambda: True,   # simplified
            "not_both_hands_full":             lambda: self.state.get("hand_count", 0) < 2,
            "obj_not_inside_closed_container": lambda: True,   # simplified
            "holds_obj":                       lambda: self.state.get("hand_count", 0) > 0,
            "target_open_or_not_openable":     lambda: True,   # simplified
            "can_open":                        lambda: True,   # simplified
            "closed":                          lambda: True,   # simplified
            "open":                            lambda: self.state.get("open", False),
            "not_on":                          lambda: not self.state.get("on", False),
            "has_switch":                      lambda: True,   # simplified
            "off":                             lambda: not self.state.get("on", False),
            "on":                              lambda: self.state.get("on", False),
            "plugged_in":                      lambda: self.state.get("plugged_in", True),
            "sitting_or_lying":                lambda: (self.state.get("sitting", False) or
                                                        self.state.get("lying", False)),
        }
        checker = checks.get(precond)
        return checker() if checker else True

    def _apply_effects(self, step: ActionStep):
        """Apply action effects to state."""
        action = step.action
        if action == "GRAB":
            self.state["hand_count"] = self.state.get("hand_count", 0) + 1
            self.state["holds_obj"]  = True
        elif action in ("PUTBACK", "PUT_ON", "PUT_INSIDE", "DROP"):
            count = max(0, self.state.get("hand_count", 1) - 1)
            self.state["hand_count"] = count
            self.state["holds_obj"]  = count > 0
        elif action == "WALK":
            self.state["not_sitting"] = True
            self.state["not_lying"]   = True
        elif action == "SIT":
            self.state["sitting"]     = True
            self.state["not_sitting"] = False
        elif action == "STANDUP":
            self.state["sitting"]     = False
            self.state["lying"]       = False
            self.state["not_sitting"] = True
            self.state["not_lying"]   = True
        elif action == "OPEN":
            self.state["open"]   = True
            self.state["closed"] = False
        elif action == "CLOSE":
            self.state["open"]   = False
            self.state["closed"] = True
        elif action == "SWITCHON":
            self.state["on"]  = True
            self.state["off"] = False
        elif action == "SWITCHOFF":
            self.state["on"]  = False
            self.state["off"] = True


# ─────────────────────────────────────────────
# SDA Pipeline
# ─────────────────────────────────────────────

class SDAPipeline:
    """
    Full SDA-Planner pipeline integrated with EAI.
    """

    def __init__(self, api_key: str = None):
        self.llm      = LLMClient(api_key=api_key)
        self.results  = []

    def run_task(self, task_id: str, task_instruction: str,
                 objects_in_scene: list = None) -> TaskResult:
        """
        Run a single task with SDA feedback loop.

        Args:
            task_id           : unique task identifier
            task_instruction  : natural language instruction
            objects_in_scene  : available objects (optional)

        Returns:
            TaskResult with final plan and statistics
        """
        print(f"\n{'='*60}")
        print(f"Task: {task_instruction}")
        print(f"{'='*60}")

        result         = TaskResult(task_id=task_id, task_instruction=task_instruction)
        objects        = objects_in_scene or []
        replan_count   = 0

        # ── Step 1: Generate initial plan ───────────────────────────────────
        print("\n[1] Generating initial plan...")
        plan_strings = self.llm.generate_initial_plan(task_instruction, objects)
        plan         = parse_plan(plan_strings)
        result.initial_plan = plan_strings

        print(f"    Initial plan ({len(plan)} steps):")
        for step in plan:
            print(f"      {step}")

        # ── Step 2: Execute with SDA feedback loop ───────────────────────────
        executor        = SimpleExecutor()
        executed        = []
        current_plan    = plan[:]
        step_idx        = 0

        while step_idx < len(current_plan) and replan_count < MAX_REPLAN_ATTEMPTS:
            step    = current_plan[step_idx]
            can_run, error_type, unsatisfied = executor.can_execute(step)

            if can_run:
                # ── Success: execute and move on ─────────────────────────────
                executor.execute(step)
                executed.append(step)
                print(f"\n  ✅ Step {step_idx+1}: {step} → OK")
                step_idx += 1

            else:
                # ── Failure: trigger SDA diagnosis ───────────────────────────
                print(f"\n  ❌ Step {step_idx+1}: {step} → FAILED ({error_type})")
                result.error_types.append(error_type)
                replan_count += 1

                # Diagnose the error
                diagnosis = diagnose_error(
                    action_history = executed,
                    failed_step    = step,
                    error_type     = error_type,
                    full_plan      = current_plan,
                )
                result.diagnoses.append(str(diagnosis))

                print(f"\n  🔍 Diagnosis:")
                print(f"     Root cause : {diagnosis.root_cause}")
                print(f"     Unsatisfied: {diagnosis.unsatisfied_needs}")
                print(f"     Strategy   : {diagnosis.replan_strategy}")
                print(f"     Replan window: [{diagnosis.t_start}, {diagnosis.t_end}]")

                # Get remaining plan after reconstruction window
                remaining = [
                    str(s) for s in current_plan
                    if s.index > diagnosis.t_end
                ]

                # Ask LLM to replan
                print(f"\n  🔄 Replanning (attempt {replan_count}/{MAX_REPLAN_ATTEMPTS})...")
                corrected_strings = self.llm.replan_with_feedback(
                    feedback_message = diagnosis.feedback_message,
                    task_instruction = task_instruction,
                    remaining_plan   = remaining,
                )

                print(f"     Corrected subsequence:")
                for s in corrected_strings:
                    print(f"       {s}")

                # Rebuild plan: executed + corrected + remaining
                corrected_steps = parse_plan(corrected_strings)

                # Re-index corrected steps continuously from current position
                base_idx = len(executed) + 1
                for i, s in enumerate(corrected_steps):
                    s.index = base_idx + i

                # Remaining = steps from original plan AFTER t_end only
                remaining_steps = [
                    s for s in current_plan
                    if s.index > diagnosis.t_end and s not in executed
                ]
                # Re-index remaining steps after corrected
                for i, s in enumerate(remaining_steps):
                    s.index = base_idx + len(corrected_steps) + i

                # New plan = already executed + corrected + remaining
                # IMPORTANT: do NOT re-include executed steps in current_plan
                current_plan = corrected_steps + remaining_steps
                step_idx     = 0  # restart from beginning of corrected plan

        # ── Step 3: Check final success ──────────────────────────────────────
        all_steps = executed + current_plan[step_idx:]
        result.final_plan    = [str(s) for s in all_steps]
        result.replan_count  = replan_count
        result.success       = (step_idx >= len(current_plan))

        print(f"\n{'─'*60}")
        print(f"Final Result: {'✅ SUCCESS' if result.success else '❌ INCOMPLETE'}")
        print(f"Replanning attempts: {replan_count}")
        print(f"Final plan:")
        for s in result.final_plan:
            print(f"  {s}")

        return result

    def run_from_file(self, task_file: str, output_dir: str):
        """
        Run all tasks from a JSON file and save results.

        Task file format:
        [
            {
                "task_id": "task_001",
                "instruction": "Pick up the phone and put it on the desk",
                "objects": ["phone", "desk", "table"]
            },
            ...
        ]
        """
        # Load tasks
        with open(task_file, "r") as f:
            tasks = json.load(f)

        print(f"Loaded {len(tasks)} tasks from {task_file}")

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        all_results = []
        success_count = 0

        for task in tasks:
            result = self.run_task(
                task_id          = task.get("task_id", "unknown"),
                task_instruction = task["instruction"],
                objects_in_scene = task.get("objects", []),
            )
            all_results.append(result)
            if result.success:
                success_count += 1

            # Save individual result
            output_path = Path(output_dir) / f"{result.task_id}.json"
            with open(output_path, "w") as f:
                json.dump({
                    "task_id":          result.task_id,
                    "instruction":      result.task_instruction,
                    "initial_plan":     result.initial_plan,
                    "final_plan":       result.final_plan,
                    "replan_count":     result.replan_count,
                    "success":          result.success,
                    "error_types":      result.error_types,
                }, f, indent=2)

        # Save summary
        summary = {
            "total_tasks":    len(tasks),
            "success_count":  success_count,
            "success_rate":   round(success_count / len(tasks) * 100, 2),
            "avg_replans":    round(sum(r.replan_count for r in all_results) / len(all_results), 2),
        }
        with open(Path(output_dir) / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        print(f"\n{'='*60}")
        print(f"FINAL SUMMARY")
        print(f"{'='*60}")
        print(f"Total tasks   : {summary['total_tasks']}")
        print(f"Success rate  : {summary['success_rate']}%")
        print(f"Avg replans   : {summary['avg_replans']}")
        print(f"Results saved : {output_dir}")

        return all_results


# ─────────────────────────────────────────────
# Quick Test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    pipeline = SDAPipeline(api_key=os.environ.get("GROQ_API_KEY"))

    # Test with a simple task
    # result = pipeline.run_task(
    #     task_id          = "test_001",
    #     task_instruction = "Pick up the phone and put it on the desk",
    #     objects_in_scene = ["phone", "desk", "table", "chair"],
    # )
    result = pipeline.run_task(
        task_id          = "test_003",
        task_instruction = "Wash the dishes and put them in the cupboard",
        objects_in_scene = ["plate", "cupboard", "sink", "dish_soap", "sponge"],
    )

    print("\n\n=== TASK COMPLETE ===")
    print(f"Success       : {result.success}")
    print(f"Replan count  : {result.replan_count}")
    print(f"Error types   : {result.error_types}")
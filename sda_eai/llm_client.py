# """
# LLM Client - Groq API Wrapper
# Sends feedback prompts to Llama 3 70B via Groq
# and parses the replanned action sequence.
# """

# import os
# import re
# from groq import Groq

# # ─────────────────────────────────────────────
# # Configuration
# # ─────────────────────────────────────────────

# GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "your_groq_api_key_here")
# MODEL        = "llama-3.3-70b-versatile"  # latest Llama 3 70B on Groq

# # Valid VirtualHome actions
# VALID_ACTIONS = {
#     "WALK", "FIND", "GRAB", "PUTBACK", "PUT_ON", "PUT_INSIDE",
#     "PUT_ON_CHARACTER", "DROP", "OPEN", "CLOSE", "SWITCHON",
#     "SWITCHOFF", "PLUGIN", "PLUGOUT", "SIT", "STANDUP", "LIE",
#     "WASH", "RINSE", "SCRUB", "WIPE", "SQUEEZE", "DRINK", "EAT",
#     "CUT", "POUR", "READ", "TOUCH", "MOVE", "WATCH", "TURN_TO",
#     "LOOK_AT", "TYPE", "SLEEP", "WAKE_UP",
# }

# # System prompt that sets the context for the LLM
# SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
# Your job is to generate executable action sequences for household tasks.

# Rules:
# - Use ONLY these valid actions: WALK, GRAB, PUTBACK, OPEN, CLOSE, SWITCHON, SWITCHOFF, DRINK, WASH, RINSE, FIND, SIT, STANDUP, LIE, DROP, PUT_INSIDE, WIPE, EAT, CUT, READ, POUR, TOUCH, MOVE, WATCH
# - Each action must have its preconditions satisfied by prior actions
# - WALK to an object before GRABbing it
# - OPEN containers before putting objects inside
# - Can only hold 2 objects at once (one per hand)
# - Output ONLY the action sequence, one action per line
# - Format: [ACTION] <object> or [ACTION] <object1> <object2>
# - No explanations, no numbering, just the action sequence
# """


# class LLMClient:
#     """Wrapper for Groq API calls."""

#     def __init__(self, api_key: str = None):
#         self.api_key = api_key or GROQ_API_KEY
#         self.client  = Groq(api_key=self.api_key)

#     def generate_initial_plan(self, task_instruction: str, objects_in_scene: list) -> list:
#         """
#         Generate an initial action plan for a task.

#         Args:
#             task_instruction : natural language task description
#             objects_in_scene : list of available objects

#         Returns:
#             list of action strings e.g. ["[WALK] <phone>", "[GRAB] <phone>"]
#         """
#         objects_str = ", ".join(objects_in_scene) if objects_in_scene else "various household objects"

#         prompt = f"""Task: {task_instruction}

# Available objects in the scene: {objects_str}

# Generate a complete action sequence to accomplish this task.
# Output ONLY the action sequence in this format:
# [ACTION] <object>
# [ACTION] <object1> <object2>"""

#         response = self._call_api(prompt)
#         return self._parse_actions(response)

#     def replan_with_feedback(self, feedback_message: str, task_instruction: str, remaining_plan: list) -> list:
#         """
#         Generate a corrected action sequence based on SDA feedback.

#         Args:
#             feedback_message : structured feedback from error_diagnosis.py
#             task_instruction : original task description
#             remaining_plan   : remaining steps after the reconstruction window

#         Returns:
#             list of corrected action strings
#         """
#         remaining_str = "\n".join(remaining_plan) if remaining_plan else "None"

#         prompt = f"""Original Task: {task_instruction}

# {feedback_message}

# Remaining steps after reconstruction window:
# {remaining_str}

# Generate ONLY the corrected action subsequence to replace the failed steps.
# Make sure preconditions are satisfied before each action."""

#         response = self._call_api(prompt)
#         return self._parse_actions(response)

#     def _call_api(self, user_prompt: str) -> str:
#         """Make a single API call to Groq."""
#         try:
#             response = self.client.chat.completions.create(
#                 model       = MODEL,
#                 temperature = 0,  # deterministic, same as EAI paper
#                 max_tokens  = 1024,
#                 messages    = [
#                     {"role": "system", "content": SYSTEM_PROMPT},
#                     {"role": "user",   "content": user_prompt},
#                 ],
#             )
#             return response.choices[0].message.content.strip()
#         except Exception as e:
#             print(f"[LLMClient] API call failed: {e}")
#             return ""

#     def _parse_actions(self, response: str) -> list:
#         """
#         Parse LLM response into a list of action strings.
#         Handles various formats the LLM might output.
#         """
#         if not response:
#             return []

#         actions = []
#         lines   = response.strip().split("\n")

#         for line in lines:
#             line = line.strip()
#             if not line:
#                 continue

#             # Remove numbering like "1." or "1)"
#             line = re.sub(r"^\d+[\.\)]\s*", "", line)

#             # Normalize to uppercase
#             line_upper = line.upper()

#             # Check if line contains a valid action
#             matched = False
#             for action in VALID_ACTIONS:
#                 if action in line_upper:
#                     # Extract objects from the line
#                     # Handle format: [WALK] <phone> or WALK phone
#                     objects = re.findall(r"<([^>]+)>", line)
#                     if not objects:
#                         # Try without angle brackets
#                         parts   = line.split()
#                         objects = [p for p in parts[1:] if not p.startswith("[")]

#                     if objects:
#                         if len(objects) == 1:
#                             actions.append(f"[{action}] <{objects[0]}>")
#                         elif len(objects) >= 2:
#                             actions.append(f"[{action}] <{objects[0]}> <{objects[1]}>")
#                     matched = True
#                     break

#             if not matched and line:
#                 # Keep the line as-is if we can't parse it
#                 actions.append(line)

#         return actions


# # ─────────────────────────────────────────────
# # Quick Test
# # ─────────────────────────────────────────────

# if __name__ == "__main__":
#     import sys

#     api_key = os.environ.get("GROQ_API_KEY")
#     client  = LLMClient(api_key=api_key)

#     print("\n--- Test 1: Generate Initial Plan ---")
#     plan = client.generate_initial_plan(
#         task_instruction = "Pick up the phone and put it on the desk",
#         objects_in_scene = ["phone", "desk", "table", "chair"],
#     )
#     print("Generated plan:")
#     for step in plan:
#         print(f"  {step}")

#     print("\n--- Test 2: Replan with SDA Feedback ---")
#     feedback = """=== EXECUTION ERROR DETECTED ===
# Failed Action   : [t=2] GRAB(phone)
# Error Type      : MISSING_STEP
# Unsatisfied Preconditions:
#   - The character must be next to the object. Use WALK first.
# The following subsequence needs to be replanned (steps 1 to 2):
#   [t=1] WALK(dining_room)
#   [t=2] GRAB(phone)
# === REPLANNING INSTRUCTIONS ===
# Please generate a corrected action sequence that satisfies all preconditions."""

#     corrected = client.replan_with_feedback(
#         feedback_message = feedback,
#         task_instruction = "Pick up the phone and put it on the desk",
#         remaining_plan   = ["[WALK] <desk>", "[PUTBACK] <phone> <desk>"],
#     )
#     print("Corrected plan:")
#     for step in corrected:
#         print(f"  {step}")
"""
LLM Client - Groq API Wrapper
Sends feedback prompts to Llama 3 70B via Groq
and parses the replanned action sequence.
"""

import os
import re
from groq import Groq

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "your_groq_api_key_here")
MODEL        = "llama-3.3-70b-versatile"  # latest Llama 3 70B on Groq

# Valid VirtualHome actions
VALID_ACTIONS = {
    "WALK", "FIND", "GRAB", "PUTBACK", "PUT_ON", "PUT_INSIDE",
    "PUT_ON_CHARACTER", "DROP", "OPEN", "CLOSE", "SWITCHON",
    "SWITCHOFF", "PLUGIN", "PLUGOUT", "SIT", "STANDUP", "LIE",
    "WASH", "RINSE", "SCRUB", "WIPE", "SQUEEZE", "DRINK", "EAT",
    "CUT", "POUR", "READ", "TOUCH", "MOVE", "WATCH", "TURN_TO",
    "LOOK_AT", "TYPE", "SLEEP", "WAKE_UP",
}

# System prompt that sets the context for the LLM
SYSTEM_PROMPT = """You are an embodied task planning assistant for a household robot in VirtualHome.
Your job is to generate executable action sequences for household tasks.

Rules:
- Use ONLY these valid actions: WALK, GRAB, PUTBACK, OPEN, CLOSE, SWITCHON, SWITCHOFF, DRINK, WASH, RINSE, FIND, SIT, STANDUP, LIE, DROP, PUT_INSIDE, WIPE, EAT, CUT, READ, POUR, TOUCH, MOVE, WATCH
- Each action must have its preconditions satisfied by prior actions
- WALK to an object before GRABbing it
- OPEN containers before putting objects inside
- Can only hold 2 objects at once (one per hand)
- Output ONLY the action sequence, one action per line
- Format: [ACTION] <object> or [ACTION] <object1> <object2>
- No explanations, no numbering, just the action sequence
"""


class LLMClient:
    """Wrapper for Groq API calls."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or GROQ_API_KEY
        self.client  = Groq(api_key=self.api_key)

    def generate_initial_plan(self, task_instruction: str, objects_in_scene: list) -> list:
        """
        Generate an initial action plan for a task.

        Args:
            task_instruction : natural language task description
            objects_in_scene : list of available objects

        Returns:
            list of action strings e.g. ["[WALK] <phone>", "[GRAB] <phone>"]
        """
        objects_str = ", ".join(objects_in_scene) if objects_in_scene else "various household objects"

        prompt = f"""Task: {task_instruction}

Available objects in the scene: {objects_str}

Generate a complete action sequence to accomplish this task.
Output ONLY the action sequence in this format:
[ACTION] <object>
[ACTION] <object1> <object2>"""

        response = self._call_api(prompt)
        return self._parse_actions(response)

    def replan_with_feedback(self, feedback_message: str, task_instruction: str, remaining_plan: list) -> list:
        """
        Generate a corrected action sequence based on SDA feedback.

        Args:
            feedback_message : structured feedback from error_diagnosis.py
            task_instruction : original task description
            remaining_plan   : remaining steps after the reconstruction window

        Returns:
            list of corrected action strings
        """
        remaining_str = "\n".join(remaining_plan) if remaining_plan else "None"

        prompt = f"""Original Task: {task_instruction}

{feedback_message}

Remaining steps after reconstruction window:
{remaining_str}

Generate ONLY the corrected action subsequence to replace the failed steps.
Make sure preconditions are satisfied before each action."""

        response = self._call_api(prompt)
        return self._parse_actions(response)

    def _call_api(self, user_prompt: str) -> str:
        """Make a single API call to Groq."""
        try:
            response = self.client.chat.completions.create(
                model       = MODEL,
                temperature = 0,  # deterministic, same as EAI paper
                max_tokens  = 1024,
                messages    = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[LLMClient] API call failed: {e}")
            return ""

    def _parse_actions(self, response: str) -> list:
        """
        Parse LLM response into a list of action strings.
        Handles various formats the LLM might output.
        """
        if not response:
            return []

        actions = []
        lines   = response.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Remove numbering like "1." or "1)"
            line = re.sub(r"^\d+[\.\)]\s*", "", line)

            # Normalize to uppercase
            line_upper = line.upper()

            # Check if line contains a valid action
            matched = False
            for action in VALID_ACTIONS:
                if action in line_upper:
                    # Extract objects from the line
                    # Handle format: [WALK] <phone> or WALK phone
                    objects = re.findall(r"<([^>]+)>", line)
                    if not objects:
                        # Try without angle brackets
                        parts   = line.split()
                        objects = [p for p in parts[1:] if not p.startswith("[")]

                    # Clean commas and whitespace from object names
                    objects = [o.strip().rstrip(",").strip() for o in objects]
                    objects = [o for o in objects if o]  # remove empty strings

                    if objects:
                        if len(objects) == 1:
                            actions.append(f"[{action}] <{objects[0]}>")
                        elif len(objects) >= 2:
                            actions.append(f"[{action}] <{objects[0]}> <{objects[1]}>")
                    matched = True
                    break

            if not matched and line:
                # Keep the line as-is if we can't parse it
                actions.append(line)

        return actions


# ─────────────────────────────────────────────
# Quick Test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    api_key = input("Enter your Groq API key: ").strip()
    client  = LLMClient(api_key=api_key)

    print("\n--- Test 1: Generate Initial Plan ---")
    plan = client.generate_initial_plan(
        task_instruction = "Pick up the phone and put it on the desk",
        objects_in_scene = ["phone", "desk", "table", "chair"],
    )
    print("Generated plan:")
    for step in plan:
        print(f"  {step}")

    print("\n--- Test 2: Replan with SDA Feedback ---")
    feedback = """=== EXECUTION ERROR DETECTED ===
Failed Action   : [t=2] GRAB(phone)
Error Type      : MISSING_STEP
Unsatisfied Preconditions:
  - The character must be next to the object. Use WALK first.
The following subsequence needs to be replanned (steps 1 to 2):
  [t=1] WALK(dining_room)
  [t=2] GRAB(phone)
=== REPLANNING INSTRUCTIONS ===
Please generate a corrected action sequence that satisfies all preconditions."""

    corrected = client.replan_with_feedback(
        feedback_message = feedback,
        task_instruction = "Pick up the phone and put it on the desk",
        remaining_plan   = ["[WALK] <desk>", "[PUTBACK] <phone> <desk>"],
    )
    print("Corrected plan:")
    for step in corrected:
        print(f"  {step}")
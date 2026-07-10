prompt = """
Guide the robot to take actions from the current state to satisfy the given node goals, edge goals, and action goals. Output action commands as a JSON dictionary executed sequentially.

Action goals specify required actions in relative order. Each line is one required action (or multiple OR-alternatives — include exactly one). "There is no action requirement" means no mandatory actions but output must never be empty.

Output format: JSON dictionary where keys are action names and values are argument lists.
- 0-argument action: []
- 1-argument action: [object_name, object_id]
- 2-argument action: [object1_name, object1_id, object2_name, object2_id]

Example (note the repeated WALK key — duplicate keys are allowed and execute in order):
{
  "WALK": ["cup", "7"],
  "GRAB": ["cup", "7"],
  "WALK": ["table", "12"],
  "PUTBACK": ["cup", "7", "table", "12"]
}

Note: When multiple properties appear in the same precondition list (e.g., ['POURABLE', 'DRINKABLE']), the object needs AT LEAST ONE (OR semantics). Exception: CUT requires BOTH EATABLE and CUTTABLE (AND semantics).

Supported Actions List:
CLOSE: (1, [['CAN_OPEN']]) # Change state from OPEN to CLOSED
DRINK: (1, [['DRINKABLE', 'RECIPIENT']]) # Consume from an item that is DRINKABLE or is a RECIPIENT
FIND: (1, [[]]) # Navigate to and confirm presence of an object; auto-moves character close to it
WALK: (1, [[]]) # Move towards something
GRAB: (1, [['GRABBABLE']]) # Take hold of an item that can be grabbed
LOOKAT: (1, [[]]) # Direct your gaze towards something
OPEN: (1, [['CAN_OPEN']]) # Open an item that can be opened
POINTAT: (1, [[]]) # Point towards something
PUTBACK: (2, [['GRABBABLE'], []]) # Place one object back onto a surface/object
PUTIN: (2, [['GRABBABLE'], []]) # Insert one object into another (target may be openable or non-openable)
PUTOBJBACK: (1, [[]]) # Put an object back to its original place — only requires holding it
RUN: (1, [[]]) # Run towards something
SIT: (1, [['SITTABLE']]) # Sit on a suitable object
STANDUP: (0, []) # Stand up from a sitting or lying position
SWITCHOFF: (1, [['HAS_SWITCH']]) # Turn off an item with a switch
SWITCHON: (1, [['HAS_SWITCH']]) # Turn on an item with a switch
TOUCH: (1, [[]])  # Physically touch something — only requires being close to the object
TURNTO: (1, [[]]) # Turn your body to face something
WATCH: (1, [['LOOKABLE']]) # Observe something attentively
WIPE: (1, [[]]) # Clean/dry a surface — requires holding a wiping tool (sponge, rag, towel, cloth, napkin, brush, paper_towel) first; the held tool is NOT the same as the surface
PUTON: (1, [['CLOTHES']]) # Dress the CHARACTER with a piece of clothing (result: item is ON the character). Does NOT place an item onto furniture/appliances — use PUTBACK/PUTIN for that.
PUTOFF: (1, [['CLOTHES']]) # Remove an item of clothing
GREET: (1, [['PERSON']]) # Offer a greeting to a person (target must have PERSON property)
DROP: (1, [[]]) # Let go of something so it falls
READ: (1, [['READABLE']]) # Read text from an object
LIE: (1, [['LIEABLE']]) # Lay oneself down on an object
POUR: (2, [['POURABLE', 'DRINKABLE'], ['RECIPIENT']]) # Pour from A (POURABLE or DRINKABLE) into B (RECIPIENT)
PUSH: (1, [['MOVABLE']]) # Exert force on something to move it away from you
PULL: (1, [['MOVABLE']]) # Exert force on something to bring it towards you
MOVE: (1, [['MOVABLE']]) # Change the location of an object
WASH: (1, [[]]) # Clean something by immersing and agitating it in water
RINSE: (1, [[]]) # Remove soap from something by applying water
SCRUB: (1, [[]]) # Clean something by rubbing it hard with a brush
SQUEEZE: (1, [[]]) # Compress to extract liquid — target must be clothes (CLOTHES property) OR one of: sponge, rag, towel, soap, dish_soap, shampoo, tooth_paste, paper, food_lemon, cleaning_solution; requires a free hand
CUT: (1, [['EATABLE', 'CUTTABLE']]) # Cut some food — object must have BOTH EATABLE and CUTTABLE properties (AND semantics, not OR)
EAT: (1, [[]]) # Eat some food — target must be EATABLE, or be a container/plate with an EATABLE item placed on it
SLEEP: (0, []) # Go to sleep
WAKEUP: (0, []) # Wake up from sleep
TYPE: (1, [['HAS_SWITCH']]) # Type on a keyboard
PLUGIN: (1, [['HAS_PLUG']]) # Connect an electrical device to a power source
PLUGOUT: (1, [['HAS_PLUG']]) # Disconnect an electrical device from a power source
RELEASE: (1, [[]])  # Let go of something inside the current room (same effect as DROP)

Important rules:
1. The subject of all actions is the character itself, that is, the robot. Do not include character as any action argument.

2. Before applying any action to an object, you should first WALK to that object unless the current state clearly shows that the robot is already NEAR it. If you WALK to pick up a tool and then need to use it on a different object, you must WALK back to that target object before applying the action.

3. Every action argument must include both object name and object ID.
- For 1-object actions, output exactly: [object_name, object_id]
- For 2-object actions, output exactly: [object1_name, object1_id, object2_name, object2_id]
- Do not output only object names.
- Do not omit IDs.

4. When multiple objects share the same class name, use the exact object ID shown in the goals or scene description. Do not substitute a different instance of the same class. Your plan must satisfy the specific goal instances, not just any object of the same class.

5. Use PUTIN for enclosed containers such as fridge, freezer, dishwasher, washing_machine, microwave, stove, cabinet, kitchen_cabinet, bathroom_cabinet, box, bag, trashcan, pantry, closet, dresser, or cupboard.
Use PUTBACK for surfaces such as table, counter, desk, shelf, sofa, bench, chair, or nightstand.

6. FIND auto-navigates the character close to the object. Only use FIND if it is explicitly required by the action goals.

7. DRINK, READ, PUTIN, PUTBACK, WIPE, CUT all require holding an object first — use WALK + GRAB before them. CUT specifically requires holding a knife.

8. DROP frees your hand and lets the object fall to the floor. Use DROP when you no longer need the object.

9. Before PUTIN into an openable container, if the container is CLOSED, WALK to the container and OPEN it first.

10. Repeated action keys are allowed and represent repeated ordered actions. The evaluator preserves duplicate keys in order.

11. You can hold at most 2 objects at once (one per hand). GRAB, OPEN, CUT, MOVE, PUSH, PULL, SQUEEZE, PLUGIN, and PLUGOUT all require at least one free hand. If both hands are full, DROP one object before attempting these actions.

12. Before using SWITCHON on a device, make sure it is plugged in. If its state shows PLUGGED_OUT, use PLUGIN first, then SWITCHON.

13. Before OPEN on an appliance that can also be switched on (e.g. microwave, stove, dishwasher, washing_machine), make sure it is OFF first — use SWITCHOFF if it is currently ON.

14. To satisfy a goal that an object must be ON or INSIDE a piece of furniture or appliance (e.g. "clothes ON washing_machine", "plate ON table"), GRAB the object then use PUTIN (for enclosed appliances/containers) or PUTBACK (for open surfaces) with the furniture/appliance as the second argument. NEVER use PUTON for this — PUTON only dresses the character itself.

Input:
The relevant objects in the scene are:
<object_in_scene>

The current environment state is:
<cur_change>

Node goals are:
<node_goals>

Edge goals are:
<edge_goals>

Action goals are:
<action_goals>

Please output the list of action commands in JSON format so that after the robot executes the action commands sequentially, the ending environment satisfies all the node goals, edge goals, and action goals.

Only output the JSON dictionary of action commands and nothing else.

Output:
"""


# Rules-only baseline variant (SDA_PROMPT_VARIANT=original): identical to
# `prompt` above (same output format, example, and action list, so parsing
# stays consistent) except the "Important rules" block is replaced by the
# verbatim upstream EAI rules (commit a8297b6).
prompt_original = """
Guide the robot to take actions from the current state to satisfy the given node goals, edge goals, and action goals. Output action commands as a JSON dictionary executed sequentially.

Action goals specify required actions in relative order. Each line is one required action (or multiple OR-alternatives — include exactly one). "There is no action requirement" means no mandatory actions but output must never be empty.

Output format: JSON dictionary where keys are action names and values are argument lists.
- 0-argument action: []
- 1-argument action: [object_name, object_id]
- 2-argument action: [object1_name, object1_id, object2_name, object2_id]

Example (note the repeated WALK key — duplicate keys are allowed and execute in order):
{
  "WALK": ["cup", "7"],
  "GRAB": ["cup", "7"],
  "WALK": ["table", "12"],
  "PUTBACK": ["cup", "7", "table", "12"]
}

Note: When multiple properties appear in the same precondition list (e.g., ['POURABLE', 'DRINKABLE']), the object needs AT LEAST ONE (OR semantics). Exception: CUT requires BOTH EATABLE and CUTTABLE (AND semantics).

Supported Actions List:
CLOSE: (1, [['CAN_OPEN']]) # Change state from OPEN to CLOSED
DRINK: (1, [['DRINKABLE', 'RECIPIENT']]) # Consume from an item that is DRINKABLE or is a RECIPIENT
FIND: (1, [[]]) # Navigate to and confirm presence of an object; auto-moves character close to it
WALK: (1, [[]]) # Move towards something
GRAB: (1, [['GRABBABLE']]) # Take hold of an item that can be grabbed
LOOKAT: (1, [[]]) # Direct your gaze towards something
OPEN: (1, [['CAN_OPEN']]) # Open an item that can be opened
POINTAT: (1, [[]]) # Point towards something
PUTBACK: (2, [['GRABBABLE'], []]) # Place one object back onto a surface/object
PUTIN: (2, [['GRABBABLE'], []]) # Insert one object into another (target may be openable or non-openable)
PUTOBJBACK: (1, [[]]) # Put an object back to its original place — only requires holding it
RUN: (1, [[]]) # Run towards something
SIT: (1, [['SITTABLE']]) # Sit on a suitable object
STANDUP: (0, []) # Stand up from a sitting or lying position
SWITCHOFF: (1, [['HAS_SWITCH']]) # Turn off an item with a switch
SWITCHON: (1, [['HAS_SWITCH']]) # Turn on an item with a switch
TOUCH: (1, [[]])  # Physically touch something — only requires being close to the object
TURNTO: (1, [[]]) # Turn your body to face something
WATCH: (1, [['LOOKABLE']]) # Observe something attentively
WIPE: (1, [[]]) # Clean/dry a surface — requires holding a wiping tool (sponge, rag, towel, cloth, napkin, brush, paper_towel) first; the held tool is NOT the same as the surface
PUTON: (1, [['CLOTHES']]) # Dress the CHARACTER with a piece of clothing (result: item is ON the character). Does NOT place an item onto furniture/appliances — use PUTBACK/PUTIN for that.
PUTOFF: (1, [['CLOTHES']]) # Remove an item of clothing
GREET: (1, [['PERSON']]) # Offer a greeting to a person (target must have PERSON property)
DROP: (1, [[]]) # Let go of something so it falls
READ: (1, [['READABLE']]) # Read text from an object
LIE: (1, [['LIEABLE']]) # Lay oneself down on an object
POUR: (2, [['POURABLE', 'DRINKABLE'], ['RECIPIENT']]) # Pour from A (POURABLE or DRINKABLE) into B (RECIPIENT)
PUSH: (1, [['MOVABLE']]) # Exert force on something to move it away from you
PULL: (1, [['MOVABLE']]) # Exert force on something to bring it towards you
MOVE: (1, [['MOVABLE']]) # Change the location of an object
WASH: (1, [[]]) # Clean something by immersing and agitating it in water
RINSE: (1, [[]]) # Remove soap from something by applying water
SCRUB: (1, [[]]) # Clean something by rubbing it hard with a brush
SQUEEZE: (1, [[]]) # Compress to extract liquid — target must be clothes (CLOTHES property) OR one of: sponge, rag, towel, soap, dish_soap, shampoo, tooth_paste, paper, food_lemon, cleaning_solution; requires a free hand
CUT: (1, [['EATABLE', 'CUTTABLE']]) # Cut some food — object must have BOTH EATABLE and CUTTABLE properties (AND semantics, not OR)
EAT: (1, [[]]) # Eat some food — target must be EATABLE, or be a container/plate with an EATABLE item placed on it
SLEEP: (0, []) # Go to sleep
WAKEUP: (0, []) # Wake up from sleep
TYPE: (1, [['HAS_SWITCH']]) # Type on a keyboard
PLUGIN: (1, [['HAS_PLUG']]) # Connect an electrical device to a power source
PLUGOUT: (1, [['HAS_PLUG']]) # Disconnect an electrical device from a power source
RELEASE: (1, [[]])  # Let go of something inside the current room (same effect as DROP)

Notice:
1. CLOSE action is opposed to OPEN action, CLOSE sth means changing the object's state from OPEN to CLOSE. 

2. You cannot [PUTIN] <character> <room name>. If you want robot INSIDE some room, please [WALK] <room name>.

3. The subject of all these actions is <character>, that is, robot itself. Do not include <character> as object_name. NEVER EVER use character as any of the object_name, that is, the argument of actions.

4. The action name should be upper case without white space. 

5. Importantly, if you want to apply ANY action on <object_name>, you should NEAR it. Therefore, you should apply WALK action as [WALK] <object_name> to first get near to the object before you apply any following actions, if you have no clue you are already NEAR <object_name>

6. Output only object names and their IDs, not just the names.

7. Output should not be empty! Always output some actions and their arguments.

8. If you want to apply an action on an object, you should WALK to the object first.

Input:
The relevant objects in the scene are:
<object_in_scene>

The current environment state is:
<cur_change>

Node goals are:
<node_goals>

Edge goals are:
<edge_goals>

Action goals are:
<action_goals>

Please output the list of action commands in JSON format so that after the robot executes the action commands sequentially, the ending environment satisfies all the node goals, edge goals, and action goals.

Only output the JSON dictionary of action commands and nothing else.

Output:
"""

if __name__ == "__main__":
    pass
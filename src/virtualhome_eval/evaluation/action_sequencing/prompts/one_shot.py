prompt = """
The task is to guide the robot to take actions from the current state to fulfill some node goals, edge goals, and action goals. The input will be the related objects in the scene, nodes and edges in the current environment, and the desired node goals, edge goals, and action goals. The output should be action commands in JSON format so that after the robot executes the action commands sequentially, the ending environment would satisfy the goals.

Data format:
Objects in the scene indicates those objects involved in the action execution. Each object is shown with its class name and ID.

Nodes and edges in the current environment show the nodes' names, states and properties, and edges in the environment.
Nodes follow the format:
object_name, states: ..., properties: ...

Edges follow the format:
object_name A is ... to object_name B

Node goals show target object states in the ending environment. They follow the format:
object_name_object_id is STATE

Edge goals show target relationships of objects in the ending environment. They follow the format:
object_name_object_id is RELATION to object_name_object_id

Action goals specify the necessary actions you need to include in your predicted action command sequence, and the order they appear in action goals should also be the RELATIVE order they appear in your predicted action command sequence if there are more than one line. Each line in action goals includes one action or more than one actions concatenated by OR. You only need to include ONE of the actions concatenated by OR in the same line.

For example, if the action goal is:
The following action(s) should be included:
GRAB
TYPE or TOUCH
OPEN
------------------------
Then your predicted action command sequence should include GRAB, either TYPE or TOUCH, and OPEN. Besides, GRAB should be executed earlier than TYPE or TOUCH, and TYPE or TOUCH should be executed earlier than OPEN.

If the action goal is:
The following action(s) should be included:
There is no action requirement.
It means there is no action you have to include in output, and you can use any action to achieve the node goals and edge goals. Warning: No action requirement does not mean empty output. You should always output some actions and their arguments.

Action commands include action names and objects. Each action's number of objects is fixed (0, 1, or 2).

Required output format:
- []: Represents 0 objects.
- [object_name, object_id]&#58; Represents 1 object.
- [object1_name, object1_id, object2_name, object2_id]&#58; Represents 2 objects.

The output must be in JSON format, where:
- Dictionary keys are action names.
- Dictionary values are lists containing the objects and IDs for the corresponding action.
- The order of execution is determined by the order in which the key-value pairs appear in the JSON dictionary.

Example:
If you want to first FIND the sink and then PUTBACK a cup onto the sink, you should output:
{
  "FIND": ["sink", "12"],
  "PUTBACK": ["cup", "7", "sink", "12"]
}

The object of an action must satisfy the required properties preconditions.

For example:
- SWITCHON has 1 object. To switch on something, the object should have property HAS_SWITCH.
  SWITCHON = ("Switch on", 1, [['HAS_SWITCH']])

- POUR has 2 objects. To pour something A into something B, A should be POURABLE and DRINKABLE, and B should be RECIPIENT.
  POUR = ("Pour", 2, [['POURABLE', 'DRINKABLE'], ['RECIPIENT']])

Action Definitions Format:
Each action is defined as a combination of:
- Action Name (String): a descriptive name for the action
- Required Number of Parameters (Integer): the count of parameters needed
- Preconditions for Each Object (List of Lists of Strings): conditions that must be met for each object involved

Supported Actions List:
CLOSE: (1, [['CAN_OPEN']]) # Change state from OPEN to CLOSED
DRINK: (1, [['DRINKABLE', 'RECIPIENT']]) # Consume a drinkable item
FIND: (1, [[]]) # Locate and approach an item
WALK: (1, [[]]) # Move towards something
GRAB: (1, [['GRABBABLE']]) # Take hold of an item that can be grabbed
LOOKAT: (1, [[]]) # Direct your gaze towards something
OPEN: (1, [['CAN_OPEN']]) # Open an item that can be opened
POINTAT: (1, [[]]) # Point towards something
PUTBACK: (2, [['GRABBABLE'], []]) # Place one object back onto or onto a surface/object
PUTIN: (2, [['GRABBABLE'], ['CAN_OPEN']]) # Insert one object into another
RUN: (1, [[]]) # Run towards something
SIT: (1, [['SITTABLE']]) # Sit on a suitable object
STANDUP: (0, []) # Stand up from a sitting or lying position
SWITCHOFF: (1, [['HAS_SWITCH']]) # Turn off an item with a switch
SWITCHON: (1, [['HAS_SWITCH']]) # Turn on an item with a switch
TOUCH: (1, [[]]) # Physically touch something
TURNTO: (1, [[]]) # Turn your body to face something
WATCH: (1, [[]]) # Observe something attentively
WIPE: (1, [[]]) # Clean or dry something by rubbing
PUTON: (1, [['CLOTHES']]) # Dress oneself with an item of clothing
PUTOFF: (1, [['CLOTHES']]) # Remove an item of clothing
GREET: (1, [['PERSON']]) # Offer a greeting to a person
DROP: (1, [[]]) # Let go of something so it falls
READ: (1, [['READABLE']]) # Read text from an object
LIE: (1, [['LIEABLE']]) # Lay oneself down on an object
POUR: (2, [['POURABLE', 'DRINKABLE'], ['RECIPIENT']]) # Transfer a liquid from one container to another
PUSH: (1, [['MOVABLE']]) # Exert force on something to move it away from you
PULL: (1, [['MOVABLE']]) # Exert force on something to bring it towards you
MOVE: (1, [['MOVABLE']]) # Change the location of an object
WASH: (1, [[]]) # Clean something by immersing and agitating it in water
RINSE: (1, [[]]) # Remove soap from something by applying water
SCRUB: (1, [[]]) # Clean something by rubbing it hard with a brush
SQUEEZE: (1, [['CLOTHES']]) # Compress clothes to extract liquid
CUT: (1, [['EATABLE', 'CUTABLE']]) # Cut some food
EAT: (1, [['EATABLE']]) # Eat some food
TYPE: (1, [['HAS_SWITCH']]) # Type on a keyboard

Important rules:
1. CLOSE is the opposite of OPEN. CLOSE something means changing the object's state from OPEN to CLOSED.

2. You cannot PUTIN character into a room. If you want the robot to be inside a room, use WALK to the room.

3. The subject of all actions is the character itself, that is, the robot. Do not include character as any action argument.

4. The action name must be upper case without whitespace.

5. Before applying any action to an object, you should first WALK to that object unless the current state clearly shows that the robot is already NEAR it.

6. Every action argument must include both object name and object ID.
- For 1-object actions, output exactly: [object_name, object_id]
- For 2-object actions, output exactly: [object1_name, object1_id, object2_name, object2_id]
- Do not output only object names.
- Do not omit IDs.

7. When multiple objects share the same class name, you must use the exact object ID shown in the goals or scene description. Do not substitute a different instance of the same class.

8. Read the node goals and edge goals carefully. Your plan must satisfy the specific goal instances, not just any object of the same class.

9. Output should not be empty. Always output some actions and their arguments.

10. Every plan should be executable step by step. Respect object properties and action preconditions.

11. Use PUTIN for enclosed containers such as fridge, dishwasher, washing_machine, microwave, cabinet, box, bag, or trashcan.
Use PUTBACK for surfaces such as table, counter, desk, shelf, sofa, bench, chair, or nightstand.

12. PUTON is only for wearing clothes on the robot body. Do not use PUTON for appliances or containers.

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
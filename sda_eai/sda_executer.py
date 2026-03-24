# import os
# from openai import OpenAI
# from src.virtualhome_eval.simulation.eval_env import EvalEnv
# import sdg  # This is the updated sdg.py file we created earlier

# # Ensure the OpenAI API key is set via the environment variable we discussed
# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# def query_gpt4_for_subtree(failed_action, error_msg, current_state, original_plan, current_step_idx):
#     """
#     This is the core of the 'Adaptive Action SubTree Generation' module.
#     It asks GPT-4 to generate a sequence of corrective actions based on the error.
#     """
#     prompt = f"""
#     You are an embodied AI agent operating in the VirtualHome simulator.
#     You were trying to execute the action: {failed_action}.
#     However, the simulator rejected it with this error: {error_msg}.
    
#     The remaining actions in your original plan were: {original_plan[current_step_idx:]}
    
#     Based on the current environment state, provide a short comma-separated list of actions 
#     to fix this error so you can continue the plan. 
#     Use the format: ACTION(object.id), ACTION(object.id)
#     Do not include any other text or explanations.
#     """
    
#     response = client.chat.completions.create(
#         model="gpt-4o", # You mentioned you are using GPT-4
#         messages=[{"role": "user", "content": prompt}],
#         temperature=0.0
#     )
    
#     # Clean and parse the output into a list of actions
#     raw_output = response.choices[0].message.content.strip()
#     return [action.strip() for action in raw_output.split(",") if action.strip()]

# def execute_with_sda(scene_id, initial_plan):
#     """
#     The main SDA-PLANNER closed-loop execution engine for VirtualHome.
#     """
#     print(f"Initializing VirtualHome Scene: {scene_id}")
#     env = EvalEnv(scene_id)
#     env.reset()
    
#     plan_queue = initial_plan.copy()
#     execution_history = []  # Tracks successful actions for backtracking
    
#     while plan_queue:
#         current_action = plan_queue.pop(0)
#         print(f"\nAttempting: {current_action}")
        
#         # 1. Try to execute the action in the simulator
#         success, message = env.apply_action(current_action)
        
#         if success:
#             print(f"  [Success] {current_action}")
#             execution_history.append(current_action)
#         else:
#             print(f"  [ERROR] {message}")
            
#             # 2. SDA Error Backtrack & Diagnosis
#             # (In a full implementation, you'd parse env.get_env_graph() here 
#             # to find exact t_source. For this skeleton, we trigger the LLM directly).
#             current_graph = env.get_env_graph()
            
#             print("  [SDA] Triggering Error Diagnosis & SubTree Generation...")
            
#             # 3. Adaptive Action SubTree Generation
#             corrective_actions = query_gpt4_for_subtree(
#                 failed_action=current_action,
#                 error_msg=message,
#                 current_state=str(current_graph)[:500], # Pass a snippet of the state graph to save tokens
#                 original_plan=initial_plan,
#                 current_step_idx=len(execution_history)
#             )
            
#             print(f"  [SDA] Generated Corrective SubTree: {corrective_actions}")
            
#             # 4. Reverse Execution (Optional / Context Dependent)
#             # If the error requires undoing recent steps, we use sdg.py to find reverse actions
#             # Example: If the agent is holding the wrong thing, put it back.
#             last_action_base = execution_history[-1].split("(")[0] if execution_history else None
#             reverse_verb = sdg.get_reverse_action(last_action_base) if last_action_base else None
            
#             if reverse_verb and ("hand_full" in message or "holding" in message):
#                 reverse_action = execution_history[-1].replace(last_action_base, reverse_verb)
#                 print(f"  [SDA] Executing Reverse Action to restore state: {reverse_action}")
#                 env.apply_action(reverse_action)
#                 # Note: If we reversed the last action, we should logically add it back to the queue 
#                 # after the fix, but GPT-4 usually handles that in its corrective subtree.
            
#             # 5. Prepend the corrective actions and the failed action back to the queue
#             # so the agent tries to fix the state, then tries the failed action again.
#             plan_queue = corrective_actions + [current_action] + plan_queue

#     print("\n[Finished] Task execution complete.")
#     return True

# if __name__ == "__main__":
#     # Test Example
#     scene_id = 0
#     test_plan = [
#         "WALK(apple.1)",
#         "GRAB(apple.1)",
#         "WALK(fridge.1)",
#         "PUTIN(apple.1, fridge.1)" # This will likely fail if the fridge is closed!
#     ]
    
#     execute_with_sda(scene_id, test_plan)
"""
sda_executor.py
Standalone execution engine implementing SDA-PLANNER Equations 2-6.
"""
import os
import re
from openai import OpenAI
import sdg  

from src.virtualhome_eval.simulation.eval_env import EvalEnv 

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def parse_eai_graph_to_pddl(env_graph):
    pddl_state = []
    nodes = {n['id']: n for n in env_graph.get('nodes', [])}
    for n in env_graph.get('nodes', []):
        name_id = f"{n['class_name']}.{n['id']}"
        for st in n.get('states', []):
            pddl_state.append(f"{st.lower()}({name_id})")
    for e in env_graph.get('edges', []):
        from_node = nodes.get(e['from_id'])
        to_node = nodes.get(e['to_id'])
        if from_node and to_node:
            rel = e['relation_type'].lower()
            pddl_state.append(f"{rel}({from_node['class_name']}.{from_node['id']}, {to_node['class_name']}.{to_node['id']})")
    return pddl_state 

def check_state(virtual_state, precondition):
    if precondition.startswith("not "):
        return precondition[4:] not in virtual_state
    return precondition in virtual_state

def diagnose_error_rigorous(state_history, action_history, original_plan, failed_action, error_message):
    t_error = len(action_history)
    s_error = error_message.split(":")[-1].strip() if ":" in error_message else error_message
    
    failed_obj_match = re.findall(r'<([^>]+)>|\(([^)]+)\)', str(failed_action))
    failed_obj = failed_obj_match[0][0] if failed_obj_match else "unknown"
    error_items = set([failed_obj]) 
    
    Lambda = []
    for t in range(1, t_error):
        if check_state(state_history[t-1], s_error) and not check_state(state_history[t], s_error):
            Lambda.append(t)
            
    t_source = max(Lambda) if Lambda else max(1, t_error - 1)
    
    t_start = t_source
    while t_start > 1:
        prev_verb = str(action_history[t_start - 1]).split("(")[0].strip().upper()
        if sdg.is_prep_action(prev_verb): t_start -= 1
        else: break
            
    t_end = t_error
    while t_end < len(original_plan):
        if failed_obj in str(original_plan[t_end]): t_end += 1
        else: break
            
    return t_start, t_end, s_error

def build_and_search_action_tree(llm_candidates, original_subsequence, start_state, target_state):
    Vr = llm_candidates + original_subsequence
    queue = [([], start_state.copy())]
    
    while queue:
        current_seq, current_virtual_state = queue.pop(0)
        if check_state(current_virtual_state, target_state):
            return current_seq 
            
        for action in Vr:
            verb = action.split("(")[0].strip()
            args = action.split("(")[1].replace(")", "").split(",") if "(" in action else []
            obj = args[0].strip() if len(args) > 0 else None
            target = args[1].strip() if len(args) > 1 else None
            
            preconditions = sdg.get_preconditions(verb, obj, target)
            effects = sdg.get_effects(verb, obj, target)
            
            is_satisfied = all(check_state(current_virtual_state, p) for p in preconditions)
            causes_change = any(not check_state(current_virtual_state, e) for e in effects)
            
            is_not_covered = True
            if current_seq:
                parent_verb = current_seq[-1].split("(")[0].strip()
                parent_effects = sdg.get_effects(parent_verb, obj, target) 
                if set(effects).intersection(set(parent_effects)):
                    is_not_covered = False

            if is_satisfied and causes_change and is_not_covered:
                new_state = current_virtual_state.copy()
                for e in effects:
                    if e.startswith("not "):
                        if e[4:] in new_state: new_state.remove(e[4:])
                    else:
                        new_state.append(e)
                queue.append((current_seq + [action], new_state))
                
    return llm_candidates 

def generate_adaptive_subtree_rigorous(failed_action, missing_precondition, state_history, original_plan, t_start, t_end):
    corrupted_sequence = original_plan[t_start:t_end]
    prompt = f"VirtualHome execution failed at: {failed_action}. Missing state: {missing_precondition}.\nOriginal sequence in this window: {corrupted_sequence}\nProvide a comma-separated list of candidate actions to fix the state. Use format: ACTION(obj)"
    
    response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.0)
    llm_candidates = [a.strip() for a in response.choices[0].message.content.strip().split(",")]
    start_state = state_history[t_start - 1] if t_start > 0 else state_history[0]
    
    valid_patch = build_and_search_action_tree(llm_candidates, corrupted_sequence, start_state, missing_precondition)
    return valid_patch + original_plan[t_end:]

def execute_with_sda(scene_id, initial_plan):
    env = EvalEnv(scene_id)
    plan_queue = initial_plan.copy()
    action_history = [] 
    state_history = [parse_eai_graph_to_pddl(env.get_env_graph())] 
    
    while plan_queue:
        current_action = plan_queue.pop(0)
        success, message = env.apply_action(current_action)
        
        if success:
            action_history.append(current_action)
            state_history.append(parse_eai_graph_to_pddl(env.get_env_graph()))
        else:
            t_start, t_end, missing_state = diagnose_error_rigorous(state_history, action_history, initial_plan, current_action, message)
            
            for i in range(len(action_history) - 1, t_start - 1, -1):
                verb = action_history[i].split("(")[0].strip()
                reverse_verb = sdg.get_reverse_action(verb)
                if reverse_verb:
                    env.apply_action(action_history[i].replace(verb, reverse_verb)) 
            
            action_history = action_history[:t_start]
            state_history = state_history[:t_start + 1]
            
            corrective_subtree = generate_adaptive_subtree_rigorous(current_action, missing_state, state_history, initial_plan, t_start, t_end)
            plan_queue = corrective_subtree + plan_queue

    return True

if __name__ == "__main__":
    test_plan = ["WALK(apple.1)", "GRAB(apple.1)", "WALK(fridge.1)", "PUTIN(apple.1, fridge.1)"]
    execute_with_sda(scene_id=0, initial_plan=test_plan)
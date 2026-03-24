import sys
import json
import time

sys.path.insert(0, '/sda_final')

import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    construct_planner,
    load_json_preserving_order,
    json_to_action,
    check_name_id_format,
    check_no_hallucination_in_arg,
    check_no_hallucination_in_action,
    check_action_grammar,
    remove_duplicate_dicts,
)

print('Loading data...')
t0 = time.time()

task_dicts = json.load(open('/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources/virtualhome/task_state_LTL_formula_accurate.json'))
id2task = json.load(open('/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources/virtualhome/id2task.json'))
llm_response = json.load(open('/usr/local/lib/python3.8/dist-packages/eai_eval/data/helm_output/virtualhome/action_sequencing/gpt-4o-2024-05-13_outputs.json'))[:5]

properties_data = utils.load_properties_data()
object_placing = utils.load_object_placing()
name_equivalence = utils.load_name_equivalence()
print(f'Data loaded in {time.time()-t0:.1f}s')

for i, output_dict in enumerate(llm_response):
    file_id = output_dict['identifier']
    task = id2task[file_id]
    print(f'\n--- Task {i+1}/5: {file_id} ({task}) ---')
    t1 = time.time()

    motion_planner, relevant_id, gd_actions, task_name, _ = construct_planner(
        name_equivalence, properties_data, object_placing,
        scenegraph_id=1,
        script_id=file_id,
        dataset_root='/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds',
    )
    print(f'  Planner built in {time.time()-t1:.1f}s: {task_name}')

    # Parse actions
    actions = output_dict['llm_output']
    if actions.startswith('```json'):
        actions = actions[7:]
    actions = actions.strip().replace('\n', '').replace("'", '"')
    try:
        actions = load_json_preserving_order(actions)
        print(f'  Parsed {len(actions)} actions')
    except Exception as e:
        print(f'  Parse error: {e}')
        continue

    if not check_name_id_format(actions)[0]:
        print('  Format error')
        continue

    _, relevant_name_to_id = motion_planner.get_symbolic_goal_nl([], [])[:2], {}
    # simple hallucination skip
    if not check_no_hallucination_in_action(actions):
        print('  Hallucination error')
        continue

    pass_check, _ = check_action_grammar(actions)
    if not pass_check:
        print('  Grammar error')
        continue

    # Convert to internal format
    # get relevant_name_to_id properly
    from virtualhome_eval.simulation.evolving_graph.eval_utils import scene_evaluate_wID
    task_scene = task_dicts[f'scene_1'][task][file_id]
    goals = task_scene['vh_goal']
    node_goals = [g for g in goals['goal'] if 'state' in g]
    edge_goals = [g for g in goals['goal'] if 'relation_type' in g]
    _, _, _, _, _, relevant_name_to_id = motion_planner.get_symbolic_goal_nl(
        node_goals, edge_goals, action_goals=goals['actions']
    )
    actions = json_to_action(actions, relevant_name_to_id=relevant_name_to_id)

    # Execute
    print(f'  Executing {len(actions)} actions...')
    motion_planner.reset()
    t2 = time.time()
    for j, action in enumerate(actions):
        exe_flag, my_info = motion_planner.my_execute_primitive_action_eval(action)
        status = 'OK' if exe_flag else f'FAIL: {str(my_info)[:60]}'
        print(f'    [{j+1}] {action[0]} -> {status}')
        if not exe_flag:
            break
    print(f'  Execution done in {time.time()-t2:.1f}s')

print('\nDone!')
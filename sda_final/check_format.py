import json
import virtualhome_eval.simulation.evolving_graph.utils as utils
from virtualhome_eval.simulation.evolving_graph.eval_utils import (
    load_json_preserving_order,
    check_name_id_format,
)

data = json.load(open('/usr/local/lib/python3.8/dist-packages/eai_eval/data/helm_output/virtualhome/action_sequencing/gpt-4o-2024-05-13_outputs.json'))

raw = data[0]['llm_output']
print('identifier:', data[0]['identifier'])
print('raw output:', raw[:300])

if raw.startswith('```json'):
    raw = raw[7:]
if raw.endswith('```'):
    raw = raw[:-3]
raw = raw.strip().replace('\n', '').replace("'", '"')

actions = load_json_preserving_order(raw)
print('\nparsed actions:', actions)

ok, err = check_name_id_format(actions)
print('\nformat ok:', ok)
print('error:', err)
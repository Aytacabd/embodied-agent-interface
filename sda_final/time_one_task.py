import sys
import os
import json
import time
import shutil
import glob

sys.path.insert(0, '/sda_final')
os.environ["GROQ_API_KEY"] = os.environ.get("GROQ_API_KEY", "")

from evaluate_results_sda import evaluate_results

class Args:
    dataset = "virtualhome"
    llm_response_path = "/usr/local/lib/python3.8/dist-packages/eai_eval/data/helm_output"
    resource_dir = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/resources"
    dataset_dir = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/dataset"
    output_dir = "/tmp/sda_test_output"
    scene_id = 1
    evaluation_dir = "/usr/local/lib/python3.8/dist-packages/virtualhome_eval/evaluation"
    num_workers = 1

response_path = "/usr/local/lib/python3.8/dist-packages/eai_eval/data/helm_output/virtualhome/action_sequencing"
target = f"{response_path}/gpt-4o-2024-05-13_outputs.json"

# Backup and truncate to 1 task
print("Truncating to 1 task...")
shutil.copy(target, target + ".backup")
data = json.load(open(target))
json.dump(data[:1], open(target, "w"), indent=2)

try:
    print("Starting evaluation...")
    t0 = time.time()
    result = evaluate_results(Args())
    print(f"Finished in {time.time()-t0:.1f}s")
    for model, (summary, _) in result.items():
        print(f"\nModel: {model}")
        print(json.dumps(summary, indent=2))
finally:
    shutil.move(target + ".backup", target)
    print("\nOriginal file restored.")
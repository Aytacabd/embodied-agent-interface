import sys
import os
import json
import glob
import shutil

sys.path.insert(0, "/sda_final")

# Set your API key here

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


def patched_evaluate(args, num_tasks=5):
    response_path = os.path.join(
        args.llm_response_path, args.dataset, "action_sequencing"
    )
    model_files = glob.glob(os.path.join(response_path, "gpt-4o-2024-05-13_outputs.json"))
    print(f"Found model files: {model_files}")

    for mf in model_files:
        data = json.load(open(mf))
        print(f"Truncating {mf} from {len(data)} to {num_tasks} tasks")
        shutil.copy(mf, mf + ".backup")
        json.dump(data[:num_tasks], open(mf, "w"), indent=2)

    try:
        result = evaluate_results(args)
    finally:
        for mf in model_files:
            if os.path.exists(mf + ".backup"):
                shutil.move(mf + ".backup", mf)
                print(f"Restored {mf}")

    return result


if __name__ == "__main__":
    result = patched_evaluate(Args(), num_tasks=5)
    print("\n========== RESULTS ==========")
    for model_name, (summary, error_info) in result.items():
        print(f"\nModel: {model_name}")
        print(json.dumps(summary, indent=2))
    print("========== DONE ==========")
"""
compare_goalfix.py
===================
Before/after check for the goal-completion fix on a targeted task subset.
"before" = the already-scored completed 342-task SDA run (results_sda_main_bo3/
gpt-4o-mini-sda-tree-final/error_info.json). "after" = evaluating the fresh
goalfix-test output, restricted to the same task IDs.

Usage:
    python3 compare_goalfix.py \
        --before results_sda_main_bo3/gpt-4o-mini-sda-tree-final/error_info.json \
        --after  results_goalfix_test/gpt-4o-mini-sda-tree-goalfix-test/error_info.json \
        --task_ids 180_1,181_2,...
"""
import argparse
import json

ap = argparse.ArgumentParser()
ap.add_argument("--before", required=True)
ap.add_argument("--after", required=True)
ap.add_argument("--task_ids", required=True,
                help="comma-separated — same set passed to the goalfix-test run")
args = ap.parse_args()

before = json.load(open(args.before))
after = json.load(open(args.after))
ids = args.task_ids.split(",")

flips_to_pass, flips_to_fail, unchanged, missing = [], [], [], []

for fid in ids:
    b = before.get(fid)
    a = after.get(fid)
    if a is None:
        missing.append(fid)
        continue
    b_ok = bool(b.get("goals_satisfied")) if b else False
    a_ok = bool(a.get("goals_satisfied"))
    if b_ok == a_ok:
        unchanged.append((fid, b_ok))
    elif a_ok and not b_ok:
        flips_to_pass.append(fid)
    else:
        flips_to_fail.append(fid)

print(f"Compared {len(ids)} tasks: {len(ids) - len(missing)} scored, {len(missing)} missing from --after\n")

print(f"FIXED  (fail -> pass): {len(flips_to_pass)}")
for fid in flips_to_pass:
    print(f"  {fid}")

print(f"\nREGRESSED (pass -> fail): {len(flips_to_fail)}   <-- should be 0")
for fid in flips_to_fail:
    print(f"  {fid}")

still_fail = [fid for fid, ok in unchanged if not ok]
still_pass = [fid for fid, ok in unchanged if ok]
print(f"\nUnchanged, still failing: {len(still_fail)}")
for fid in still_fail:
    print(f"  {fid}")
print(f"\nUnchanged, still passing (regression check): {len(still_pass)}")
for fid in still_pass:
    print(f"  {fid}")

if missing:
    print(f"\nMISSING from --after (not in this run's output — check task_ids/tag): {missing}")

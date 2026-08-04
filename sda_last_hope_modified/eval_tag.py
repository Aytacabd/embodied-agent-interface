"""
eval_tag.py
===========
Score exactly one tagged output file instead of eval_main_bo.py's default
behavior of rescanning every *_outputs.json under the output dir (which
gets slower every time a new run is added — 20+ historical tags at last
count). Same underlying evaluate_results(), just scoped to one tag.

Usage (inside the container):
    python3 sda_eai/eval_tag.py --tag gpt-4o-mini-sda-tree-final-fixed2
    python3 sda_eai/eval_tag.py --tag <model-tag> --output_dir /opt/iGibson/results_fixed2
"""

import argparse
import logging
import os.path as osp

import virtualhome_eval
import virtualhome_eval.evaluation.action_sequencing.scripts.evaluate_results as er_mod

PKG = osp.dirname(virtualhome_eval.__file__)

parser = argparse.ArgumentParser()
parser.add_argument("--tag", required=True,
                     help="exact model tag, i.e. the '<tag>' in '<tag>_outputs.json'")
parser.add_argument("--llm_response_path", default="/opt/iGibson/output_sda",
                     help="dir the runner wrote to; evaluator appends virtualhome/action_sequencing itself")
parser.add_argument("--resource_dir", default=osp.join(PKG, "resources"))
parser.add_argument("--dataset_dir", default=osp.join(PKG, "dataset"))
parser.add_argument("--output_dir", default=None,
                     help="defaults to /opt/iGibson/results_<tag>")
parser.add_argument("--dataset", default="virtualhome")
args = parser.parse_args()

if args.output_dir is None:
    args.output_dir = f"/opt/iGibson/results_{args.tag}"

er_mod.extract_model_names = lambda _dir: [args.tag]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
er_mod.evaluate_results(args)

print(f"\nerror_info.json -> {args.output_dir}/{args.tag}/error_info.json")
print(f"summary.json    -> {args.output_dir}/{args.tag}/summary.json")

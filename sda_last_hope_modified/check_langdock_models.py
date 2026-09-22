"""
check_langdock_models.py
========================
List the models a Langdock API key can actually use, so `--model` can be
set to something the provider will accept instead of guessing.

Queries the SAME endpoint the runner plans through — Langdock's
OpenAI-compatible surface at https://api.langdock.com/openai/<region>/v1 —
so whatever this prints is exactly what eai_sda_runner_tree.py will accept.
Region and key come from the runner's own config, so the two cannot drift
apart. If that surface does not implement model listing, it falls back to
Langdock's Agent API (/agent/v1/models), which documents the endpoint
explicitly.

Usage:
    export LANGDOCK_API_KEY=...
    python3 check_langdock_models.py            # list everything the key sees
    python3 check_langdock_models.py --probe    # and test which actually answer

The plain listing merges both endpoints. --probe then sends each model a
one-token request through the endpoint the runner plans with, which is the
only reliable test: a gateway may serve a model it does not advertise, and
advertise one it will not serve.

    # other region, or a key passed inline
    LANGDOCK_REGION=us python3 check_langdock_models.py
    python3 check_langdock_models.py <api_key>

Docs: https://docs.langdock.com/en/developer/completion-api/openai
      https://docs.langdock.com/api-endpoints/agent/agent-models
"""

import json
import os
import sys
import urllib.error
import urllib.request

# Region and the OpenAI-compatible base URL are defined once, in the runner.
# Importing them keeps this script honest: it can only ever report on the
# endpoint the runner would really call.
try:
    import eai_sda_runner_tree as core
    REGION = core.LANGDOCK_REGION
    OPENAI_COMPAT_BASE = core._BACKENDS["langdock"][1]
except Exception:  # standalone use, e.g. copied out of this directory
    REGION = os.environ.get("LANGDOCK_REGION", "eu")
    OPENAI_COMPAT_BASE = f"https://api.langdock.com/openai/{REGION}/v1"

AGENT_API_URL = "https://api.langdock.com/agent/v1/models"


def _get(url: str, api_key: str):
    """GET url with bearer auth -> parsed JSON, or (None, error string)."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return None, f"HTTP {e.code} — {body}"
    except urllib.error.URLError as e:
        return None, f"could not reach {url} — {e.reason}"
    except json.JSONDecodeError as e:
        return None, f"response was not JSON — {e}"


def main():
    # Positional key, ignoring flags, so `--probe` is never mistaken for one.
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    api_key = positional[0] if positional else os.environ.get("LANGDOCK_API_KEY")
    if not api_key:
        print("ERROR: no key. Set LANGDOCK_API_KEY or pass it as an argument.")
        sys.exit(1)

    # Both endpoints are queried, because they answer different questions
    # and their lists differ. The OpenAI-compatible surface is narrower: it
    # is the OpenAI-dialect subset, and it is the only one the runner can
    # plan through. The Agent API lists Langdock's whole brokered catalogue
    # (OpenAI plus Anthropic, Google, Mistral, Meta), which is useful for
    # knowing what the workspace has, but a model that appears only there
    # is NOT reachable from the runner as configured.
    usable = _ids(_get(f"{OPENAI_COMPAT_BASE}/models", api_key),
                  "OpenAI-compatible (what the runner plans through)",
                  f"{OPENAI_COMPAT_BASE}/models")
    catalogue = _ids(_get(AGENT_API_URL, api_key),
                     "Agent API (full Langdock catalogue)",
                     AGENT_API_URL)

    if usable is None and catalogue is None:
        print("Could not list models from either endpoint.")
        print("Check the key, and that LANGDOCK_REGION matches your workspace "
              f"(currently {REGION!r}).")
        sys.exit(1)

    every_model = sorted(set(usable or []) | set(catalogue or []))

    print("=" * 60)
    print(f"MODELS ACCESSIBLE WITH THIS LANGDOCK KEY — {len(every_model)}")
    print("=" * 60)
    for mid in every_model:
        print(f"  {mid}")
    print("=" * 60)

    if "--probe" not in sys.argv:
        print("\nThis is what the two listing endpoints report. A listing is\n"
              "not proof a model will answer, so to find out which of these\n"
              "the runner can really plan with, send each one a one-token\n"
              "request:\n"
              "    python3 check_langdock_models.py --probe")
        return

    print(f"\nProbing all {len(every_model)} through the endpoint the runner\n"
          f"uses ({OPENAI_COMPAT_BASE}) with a 1-token request each.\n"
          "This is the authoritative answer, and it costs a few cents.\n")
    working, failing = [], []
    for mid in every_model:
        ok, detail = _probe(mid, api_key)
        print(f"  {mid:<44}{'OK' if ok else 'FAILED — ' + detail}")
        (working if ok else failing).append(mid)

    print("\n" + "=" * 60)
    print(f"USABLE WITH --model : {len(working)}")
    for mid in working:
        print(f"  {mid}")
    if failing:
        print(f"\nNOT USABLE : {len(failing)}")
        for mid in failing:
            print(f"  {mid}")
    print("=" * 60)
    if working:
        print("\nRun one with:")
        print(f"  python3 eai_sda_runner_tree.py --model {working[0]}")


def _probe(model_id: str, api_key: str):
    """Ask one model for a single token through the runner's endpoint.

    The listing endpoints disagree about what exists, and a gateway can
    accept a model it does not advertise, so the only reliable test is to
    call it the same way the planner would.
    """
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        f"{OPENAI_COMPAT_BASE}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            json.loads(resp.read().decode("utf-8"))
        return True, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:  # surface the provider's own message, not the raw blob
            body = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            pass
        return False, f"HTTP {e.code}: {str(body)[:90]}"
    except Exception as e:
        return False, str(e)[:90]


def _ids(result, description: str, url: str):
    """Print the outcome of one endpoint query; return sorted model ids."""
    data, err = result
    print(f"Querying {description}:\n  {url}")
    if data is None:
        print(f"  -> {err}\n")
        return None
    models = data.get("data", [])
    print(f"  -> {len(models)} model(s)\n")
    return sorted(str(m.get("id", "?")) for m in models)


if __name__ == "__main__":
    main()

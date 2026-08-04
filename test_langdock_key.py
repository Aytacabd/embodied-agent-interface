"""
Quick connectivity test for a Langdock API key.

Langdock exposes an OpenAI-compatible chat completions endpoint, region-
scoped (EU / US) for data residency. This tries the EU endpoint first,
since that's Langdock's primary offering for institute/enterprise
customers — if your account is provisioned in the US region instead,
change EU to US below (or check your Langdock dashboard's "API" /
"Developer" page for the exact base URL and available model names, which
this script can't know for certain from here).

Usage:
    python3 test_langdock_key.py
    (paste the key when prompted — it's read via getpass, so it won't
    echo to the terminal or end up in shell history)

Needs: pip install requests
"""

import getpass
import json
import sys

import requests

REGION = "eu"  # change to "us" if that's where your key is provisioned
BASE_URL = f"https://api.langdock.com/openai/{REGION}/v1/chat/completions"
MODEL = "gpt-5-mini"  # confirmed available on this key via Langdock's own error response

def main():
    api_key = getpass.getpass("Paste your Langdock API key (input hidden): ").strip()
    if not api_key:
        print("No key entered, stopping.")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply with exactly: connection ok"}],
        "max_tokens": 20,
    }

    print(f"\nPOST {BASE_URL}")
    print(f"model: {MODEL}\n")

    try:
        resp = requests.post(BASE_URL, headers=headers, json=payload, timeout=20)
    except requests.exceptions.RequestException as e:
        print(f"Request itself failed (network / DNS / timeout), not an API error: {e}")
        sys.exit(1)

    print(f"Status: {resp.status_code}")

    if resp.status_code == 200:
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
        print(f"\n✅ Key works. Model replied: {reply!r}")
        print(f"\nFull usage info: {json.dumps(data.get('usage', {}), indent=2)}")
    else:
        print("\n❌ Not a clean success — full response body below.")
        print("If this is 401/403: the key itself is likely invalid/expired/wrong region.")
        print("If this is 404: the URL or model name is probably wrong for your account.")
        print("If this is 400 with a model-related message: try a different MODEL value.")
        try:
            print(json.dumps(resp.json(), indent=2))
        except ValueError:
            print(resp.text)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generates a small amount of real traffic through a running gateway so
Grafana dashboards, /metrics, and the admin API have non-empty data
immediately, rather than a reviewer staring at empty graphs on first launch.

Usage (after the gateway + mocks + Ollama + Redis are all running):
    python scripts/seed_demo_data.py [--host http://localhost:8010]
"""

import argparse
import sys

import httpx

TEAM_LOADTEST_KEY = "sk-loadtest-demo-777888"
TEAM_ALPHA_KEY = "sk-alpha-demo-000111"
TEAM_BETA_KEY = "sk-beta-demo-222333"

# team-loadtest carries most of the volume since its limits are generous;
# one call each for team-alpha/team-beta shows real per-team data without
# tripping their deliberately tight demo limits.
SEED_REQUESTS = [
    (TEAM_LOADTEST_KEY, "gpt-4o-mini", "What's a good name for a coffee shop?"),
    (TEAM_LOADTEST_KEY, "claude-3-5-sonnet", "Summarize the plot of Hamlet in one sentence."),
    (TEAM_LOADTEST_KEY, "gpt-4o-mini", "Write a haiku about databases."),
    (TEAM_ALPHA_KEY, "gpt-4o-mini", "Say hello to the team."),
    (TEAM_BETA_KEY, "claude-3-5-sonnet", "What model are you?"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://localhost:8010")
    args = parser.parse_args()

    try:
        health = httpx.get(f"{args.host}/healthz", timeout=5.0)
        health.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Gateway not reachable at {args.host}: {exc}", file=sys.stderr)
        return 1

    print(f"Seeding {len(SEED_REQUESTS)} requests against {args.host} ...")
    ok = 0
    with httpx.Client(timeout=30.0) as client:
        for api_key, model, message in SEED_REQUESTS:
            resp = client.post(
                f"{args.host}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": [{"role": "user", "content": message}]},
            )
            status = "OK" if resp.status_code == 200 else f"FAILED ({resp.status_code})"
            print(f"  [{status}] model={model} -> {resp.text[:80]}")
            if resp.status_code == 200:
                ok += 1

    print(f"\nSeeded {ok}/{len(SEED_REQUESTS)} requests successfully.")
    print(f"Check {args.host}/metrics or the admin API ({args.host}/admin/teams) for results.")
    return 0 if ok == len(SEED_REQUESTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

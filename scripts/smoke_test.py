"""Smoke-test a running Gridlock-d service."""

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    response = httpx.get(f"{args.base_url.rstrip('/')}/health", timeout=5)
    response.raise_for_status()
    if response.json() != {"status": "ok"}:
        raise SystemExit("Unexpected health response")
    print("health: ok")


if __name__ == "__main__":
    main()

"""Pair this PC to a private control plane and process registered project jobs."""
import argparse
import getpass
import json
from pathlib import Path

from tracebridge.local_runner import LocalRunner, pair_runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("output/local-runner/config.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair")
    pair.add_argument("--url", required=True)
    commands.add_parser("run")
    commands.add_parser("once")
    args = parser.parse_args()
    if args.command == "pair":
        result = pair_runner(args.url, getpass.getpass("일회용 페어링 코드: "), args.config)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "once":
        print(json.dumps(LocalRunner.from_config(args.config).run_once(), ensure_ascii=False))
    else:
        LocalRunner.from_config(args.config).run()


if __name__ == "__main__":
    main()

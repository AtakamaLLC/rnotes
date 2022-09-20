"""rnotes: parse args and main entrypoint"""

import sys
import argparse
import subprocess
import logging

from rnotes.runner import Runner, CONFIG_PATH

log = logging.getLogger("rnotes")


def parse_args(args):
    """Given args (not cmd name), parse and return the namespace."""
    config_path = CONFIG_PATH.lstrip("./")
    parser = argparse.ArgumentParser(description="Sane reno reporter")
    parser.add_argument(
        "--version", help="Version to report on (default: current branch)"
    )
    parser.add_argument(
        "--previous", help="Previous version, (default: ordinal previous tag)"
    )
    parser.add_argument(
        "--version-regex",
        help=f"Regex to use when parsing (default: from {config_path})",
    )
    parser.add_argument(
        "--notes-dir",
        "--rel-notes-dir",
        help="Release notes folder",
        default="./releasenotes",
    )
    parser.add_argument("--debug", help="Debug mode", action="store_true")
    parser.add_argument("--yaml", help="Dump yaml", action="store_true")
    parser.add_argument(
        "--lint", help="Lint notes for valid markdown", action="store_true"
    )
    parser.add_argument("--create", help="Create a new note", action="store_true")
    parser.add_argument(
        "--blame", help="Show more commit info in the report", action="store_true"
    )
    return parser.parse_args(args)


def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = parse_args(sys.argv[1:])
    if args.debug:
        log.setLevel(logging.DEBUG)
        log.debug("args: %s", args)
    r = Runner(args)
    try:
        r.run()
    except (subprocess.CalledProcessError, AssertionError) as e:
        print("ERROR:", str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
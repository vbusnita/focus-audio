"""Parser: stop is an argparse alias of skip."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from focus_audio import cli  # noqa: E402


def test_parser_stop_and_skip_dispatch_cmd_skip():
    parser = cli.build_parser()
    skip_ns = parser.parse_args(["skip"])
    stop_ns = parser.parse_args(["stop"])
    assert skip_ns.func is cli.cmd_skip
    assert stop_ns.func is cli.cmd_skip


"""Run external tools. Every tool workshop drives goes through run()."""

import json
import subprocess
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


def run(*argv: str, cwd: Path | str | None = None, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True, env=env)
    if done.returncode != 0:
        raise ToolError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed: {done.stderr.strip()}")
    return done.stdout


def run_json(*argv: str, cwd: Path | str | None = None) -> Any:
    return json.loads(run(*argv, cwd=cwd))

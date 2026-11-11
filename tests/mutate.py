"""Apply exact mutations to a production file and check the selected tests catch them.

Usage:
    python tests/mutate.py <file> --tests <pytest args...> \
        --mutation <search> <replace> [--mutation <search> <replace> ...]

Each mutation is applied alone to the original file bytes, the selected tests
run through ``sys.executable -m pytest``, and the file is restored from the
original bytes in a ``finally`` block. Search and replace text are matched as
UTF-8 bytes, so a file is byte-identical after every run.

A mutation is killed when pytest reports failed tests (exit code 1, or 2 for a
collection error), survived when pytest reports success (exit code 0), and
rejected when its search text does not occur exactly once. Other pytest exit
codes are reported as errors. The command exits non-zero unless every mutation
was killed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


KILLED = "killed"
SURVIVED = "survived"
REJECTED = "rejected"
ERROR = "error"

USAGE = (
    "python tests/mutate.py <file> --tests <pytest args...> "
    "--mutation <search> <replace> [--mutation <search> <replace> ...]"
)

HELP = f"""\
usage: {USAGE}

Apply each exact search/replace mutation alone to a production file, run the
selected pytest tests, and report whether they kill or survive it. The file is
always restored byte-for-byte.

arguments:
  <file>                production file to mutate
  --tests <args...>     pytest arguments; collect until --mutation or end
  --mutation S R        exact search text and its replacement; repeatable

Each search text must match exactly once in the original file, or its mutation
is rejected. A mutation is killed when the tests fail and survived when they
pass; the exit status is 0 only when every mutation is killed.

example:
  python tests/mutate.py strategy/ema50.py \\
      --tests tests/test_ema50.py -q \\
      --mutation 'return a + b' 'return a - b'
"""


class MutationResult(NamedTuple):
    status: str
    mutation: str
    detail: str


class Selection(NamedTuple):
    file: Path
    tests: list[str]
    mutations: list[tuple[str, str]]


def _fail(message: str) -> None:
    print(f"tests/mutate.py: error: {message}", file=sys.stderr)
    print(f"usage: {USAGE}", file=sys.stderr)
    raise SystemExit(2)


def _display(text: str) -> str:
    """Render search or replace text as one readable line."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return escaped if len(escaped) <= 60 else escaped[:57] + "..."


def _parse_args(argv: list[str]) -> Selection:
    file_path: Path | None = None
    tests: list[str] | None = None
    mutations: list[tuple[str, str]] = []

    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help"):
            print(HELP, end="")
            raise SystemExit(0)
        if token == "--tests":
            if tests is not None:
                _fail("--tests given more than once")
            index += 1
            tests = []
            while index < len(argv) and argv[index] != "--mutation":
                tests.append(argv[index])
                index += 1
            continue
        if token == "--mutation":
            if index + 2 >= len(argv):
                _fail("--mutation needs both a search and a replace value")
            mutations.append((argv[index + 1], argv[index + 2]))
            index += 3
            continue
        if file_path is None and not token.startswith("-"):
            file_path = Path(token)
            index += 1
            continue
        _fail(f"unexpected argument: {token}")

    if file_path is None:
        _fail("missing production file")
    if not file_path.is_file():
        _fail(f"not a file: {file_path}")
    if not tests:
        _fail("--tests needs at least one pytest argument")
    if not mutations:
        _fail("at least one --mutation is required")
    return Selection(file_path, tests, mutations)


def _clear_bytecode_cache(path: Path) -> None:
    """Drop cached bytecode for the mutated module so the tests see its new source."""
    cache_dir = path.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    prefix = f"{path.stem}."
    for cached in cache_dir.iterdir():
        if cached.name.startswith(prefix) and cached.suffix == ".pyc":
            cached.unlink(missing_ok=True)


def _check_mutation(
    path: Path,
    original: bytes,
    search: str,
    replace: str,
    tests: list[str],
) -> MutationResult:
    search_bytes = search.encode("utf-8", "surrogateescape")
    replace_bytes = replace.encode("utf-8", "surrogateescape")
    mutation = f"{_display(search)} -> {_display(replace)}"

    matches = original.count(search_bytes)
    if matches != 1:
        return MutationResult(
            REJECTED,
            mutation,
            f"search text matched {matches} times (need exactly 1)",
        )

    try:
        _clear_bytecode_cache(path)
        path.write_bytes(original.replace(search_bytes, replace_bytes, 1))
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *tests],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        path.write_bytes(original)
        _clear_bytecode_cache(path)

    if completed.returncode == 0:
        return MutationResult(SURVIVED, mutation, "tests passed")
    if completed.returncode in (1, 2):
        return MutationResult(
            KILLED,
            mutation,
            f"tests failed (pytest exit {completed.returncode})",
        )
    return MutationResult(
        ERROR,
        mutation,
        f"pytest exited {completed.returncode} before running the tests",
    )


def _print_table(path: Path, tests: list[str], results: list[MutationResult]) -> None:
    print(f"Mutation results for {path} (tests: {' '.join(tests)})")
    width = max(len(result.mutation) for result in results)
    print(f"{'#':<3} {'status':<9} {'mutation':<{width}} detail")
    for index, result in enumerate(results, start=1):
        print(
            f"{index:<3} {result.status:<9} "
            f"{result.mutation:<{width}} {result.detail}"
        )


def main(argv: list[str] | None = None) -> int:
    selection = _parse_args(sys.argv[1:] if argv is None else argv)
    original = selection.file.read_bytes()
    results = [
        _check_mutation(selection.file, original, search, replace, selection.tests)
        for search, replace in selection.mutations
    ]
    _print_table(selection.file, selection.tests, results)
    return 0 if all(result.status == KILLED for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())

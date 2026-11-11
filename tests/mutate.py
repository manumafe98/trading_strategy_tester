"""Apply exact mutations to a production file and check the selected tests catch them.

Usage:
    python tests/mutate.py <file> --tests <pytest args...> \
        --mutation <search> <replace> [--mutation <search> <replace> ...]

Each mutation is applied alone to the original file bytes, the selected tests
run through ``sys.executable -m pytest``, and the file is restored from the
original bytes in a ``finally`` block. Search and replace text are matched as
UTF-8 bytes, so a file is byte-identical after every run.

A mutation is killed when pytest reports failed tests (exit code 1), survived
when pytest reports success (exit code 0), and invalid when its search text
does not occur exactly once. Any other exit code, including 2 for an
interrupted run or a collection error, is reported as an error. The command
exits non-zero unless every mutation was killed.
"""

from __future__ import annotations

import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple


class MutationStatus(StrEnum):
    KILLED = "killed"
    SURVIVED = "survived"
    INVALID = "invalid"
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
is invalid. A mutation is killed when the tests fail and survived when they
pass. A run pytest cannot complete, such as an interrupted or collection-error
run (exit code 2), is an error, never a kill. The exit status is 0 only when
every mutation is killed.

example:
  python tests/mutate.py strategy/ema50.py \\
      --tests tests/test_ema50.py -q \\
      --mutation 'return a + b' 'return a - b'
"""


class Mutation(NamedTuple):
    search: str
    replace: str


class MutationResult(NamedTuple):
    status: MutationStatus
    label: str
    detail: str


class RunContext(NamedTuple):
    file: Path
    original: bytes
    tests: tuple[str, ...]


class Selection(NamedTuple):
    file: Path
    tests: list[str]
    mutations: list[Mutation]


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
    mutations: list[Mutation] = []

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
            mutations.append(Mutation(argv[index + 1], argv[index + 2]))
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


def _check_mutation(context: RunContext, mutation: Mutation) -> MutationResult:
    search_bytes = mutation.search.encode("utf-8", "surrogateescape")
    replace_bytes = mutation.replace.encode("utf-8", "surrogateescape")
    label = f"{_display(mutation.search)} -> {_display(mutation.replace)}"

    matches = context.original.count(search_bytes)
    if matches != 1:
        return MutationResult(
            MutationStatus.INVALID,
            label,
            f"search text matched {matches} times (need exactly 1)",
        )

    try:
        _clear_bytecode_cache(context.file)
        context.file.write_bytes(
            context.original.replace(search_bytes, replace_bytes, 1)
        )
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *context.tests],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        context.file.write_bytes(context.original)
        _clear_bytecode_cache(context.file)

    if completed.returncode == 0:
        return MutationResult(MutationStatus.SURVIVED, label, "tests passed")
    if completed.returncode == 1:
        return MutationResult(
            MutationStatus.KILLED,
            label,
            "tests failed (pytest exit 1)",
        )
    return MutationResult(
        MutationStatus.ERROR,
        label,
        f"pytest exited {completed.returncode} without completing the tests",
    )


def _print_table(context: RunContext, results: list[MutationResult]) -> None:
    print(
        f"Mutation results for {context.file} "
        f"(tests: {' '.join(context.tests)})"
    )
    width = max(len(result.label) for result in results)
    print(f"{'#':<3} {'status':<9} {'mutation':<{width}} detail")
    for index, result in enumerate(results, start=1):
        print(
            f"{index:<3} {result.status:<9} "
            f"{result.label:<{width}} {result.detail}"
        )


def main(argv: list[str] | None = None) -> int:
    selection = _parse_args(sys.argv[1:] if argv is None else argv)
    context = RunContext(
        selection.file,
        selection.file.read_bytes(),
        tuple(selection.tests),
    )
    results = [
        _check_mutation(context, mutation) for mutation in selection.mutations
    ]
    _print_table(context, results)
    return (
        0
        if all(result.status == MutationStatus.KILLED for result in results)
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())

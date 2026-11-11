from __future__ import annotations

import subprocess
import sys
from pathlib import Path


HELPER = Path(__file__).with_name("mutate.py")

MODULE_SOURCE = """\
def add(a, b):
    return a + b


def double(x):
    return x + x
"""

TEST_SOURCE = """\
import mutation_target


def test_add():
    assert mutation_target.add(2, 3) == 5


def test_double():
    assert mutation_target.double(4) == 8
"""


def _write_project(tmp_path: Path) -> tuple[Path, Path]:
    module = tmp_path / "mutation_target.py"
    test_file = tmp_path / "test_mutation_target.py"
    module.write_text(MODULE_SOURCE, encoding="utf-8", newline="\n")
    test_file.write_text(TEST_SOURCE, encoding="utf-8", newline="\n")
    return module, test_file


def _run(
    tmp_path: Path,
    module: Path,
    test_file: Path,
    mutations: list[tuple[str, str]],
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(HELPER),
        str(module),
        "--tests",
        str(test_file),
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    for search, replace in mutations:
        command += ["--mutation", search, replace]
    return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)


def test_mutation_the_test_catches_is_killed(tmp_path):
    module, test_file = _write_project(tmp_path)
    original = module.read_bytes()

    result = _run(tmp_path, module, test_file, [("return a + b", "return a - b")])

    assert result.returncode == 0
    assert "killed" in result.stdout
    assert "survived" not in result.stdout
    assert module.read_bytes() == original


def test_mutation_the_test_does_not_catch_survives(tmp_path):
    module, test_file = _write_project(tmp_path)
    original = module.read_bytes()

    result = _run(tmp_path, module, test_file, [("return a + b", "return b + a")])

    assert result.returncode != 0
    assert "survived" in result.stdout
    assert "killed" not in result.stdout
    assert module.read_bytes() == original


def test_search_text_that_does_not_match_once_is_invalid(tmp_path):
    module, test_file = _write_project(tmp_path)
    original = module.read_bytes()

    result = _run(
        tmp_path,
        module,
        test_file,
        [("return a * b", "return a / b"), ("return ", "yield ")],
    )

    assert result.returncode != 0
    assert result.stdout.count("invalid") == 2
    assert "matched 0 times" in result.stdout
    assert "matched 2 times" in result.stdout
    assert "killed" not in result.stdout
    assert "survived" not in result.stdout
    assert module.read_bytes() == original


INTERRUPTED_TEST_SOURCE = """\
def test_interrupted():
    raise KeyboardInterrupt
"""


def test_pytest_exit_2_is_reported_as_error_not_killed(tmp_path):
    module, _ = _write_project(tmp_path)
    interrupted_test = tmp_path / "test_interrupted.py"
    interrupted_test.write_text(
        INTERRUPTED_TEST_SOURCE, encoding="utf-8", newline="\n"
    )
    original = module.read_bytes()

    result = _run(
        tmp_path, module, interrupted_test, [("return a + b", "return a - b")]
    )

    assert result.returncode != 0
    assert "error" in result.stdout
    assert "killed" not in result.stdout
    assert "survived" not in result.stdout
    assert module.read_bytes() == original


def test_file_is_restored_after_a_failing_test_run(tmp_path):
    module, test_file = _write_project(tmp_path)
    original = module.read_bytes()

    result = _run(
        tmp_path,
        module,
        test_file,
        [
            ("return a + b", "return a - b"),
            ("return a + b", "return b + a"),
        ],
    )

    assert result.returncode != 0
    assert result.stdout.count("killed") == 1
    assert result.stdout.count("survived") == 1
    assert module.read_bytes() == original

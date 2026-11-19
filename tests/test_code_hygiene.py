from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKED_DIRS = ("tester_framework", "strategy", "tests", "tools")
TOOL_DIRECTIVES = ("# noqa", "# type:", "# pragma:")
DISTINCTIVE_AVOID_WORDS = {"zone", "imbalance", "holdout", "winner", "deal", "straddling"}
MAX_AVOID_PHRASE_WORDS = 3
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def python_files() -> list[Path]:
    return sorted(path for folder in CHECKED_DIRS for path in (ROOT / folder).rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def comment_lines(source: str) -> list[int]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return [
        token.start[0] for token in tokens
        if token.type == tokenize.COMMENT and not token.string.startswith(TOOL_DIRECTIVES)
    ]


def docstring_lines(tree: ast.Module) -> list[int]:
    owners = [tree, *(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return [owner.body[0].lineno for owner in owners if ast.get_docstring(owner, clean=False) is not None]


def word_parts(name: str) -> list[str]:
    return [part for part in name.lower().split("_") if part]


def avoid_phrases(glossary: str) -> list[tuple[str, ...]]:
    phrases = []
    for line in glossary.splitlines():
        if not line.startswith("_Avoid_:"):
            continue
        for entry in line.removeprefix("_Avoid_:").split(","):
            words = entry.strip().split()
            if 1 < len(words) <= MAX_AVOID_PHRASE_WORDS:
                phrases.append(tuple(re.findall(r"[a-z0-9]+", " ".join(words).lower().replace("/", ""))))
    return phrases


def contains_run(parts: list[str], phrase: tuple[str, ...]) -> bool:
    return any(tuple(parts[start:start + len(phrase)]) == phrase for start in range(len(parts) - len(phrase) + 1))


def names_in(tree: ast.Module) -> list[tuple[int, str]]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append((node.lineno, node.name.removeprefix("test_")))
        elif isinstance(node, ast.Name):
            names.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            names.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            names.append((node.lineno, node.arg))
        elif isinstance(node, ast.keyword) and node.arg:
            names.append((node.value.lineno, node.arg))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and IDENTIFIER.fullmatch(node.value):
            names.append((node.lineno, node.value))
    return names


def avoid_word_hits(name: str, phrases: list[tuple[str, ...]]) -> list[str]:
    parts = word_parts(name)
    hits = sorted(DISTINCTIVE_AVOID_WORDS.intersection(parts))
    return hits + [" ".join(phrase) for phrase in phrases if contains_run(parts, phrase)]


def test_code_has_no_comments_or_docstrings():
    findings = []
    for path in python_files():
        source = path.read_text(encoding="utf-8")
        findings += [f"{relative(path)}:{line} comment" for line in comment_lines(source)]
        findings += [f"{relative(path)}:{line} docstring" for line in docstring_lines(ast.parse(source))]
    assert findings == []


def test_code_names_avoid_glossary_avoid_words():
    phrases = avoid_phrases((ROOT / "GLOSSARY.md").read_text(encoding="utf-8"))
    own_file = Path(__file__).resolve()
    findings = []
    for path in python_files():
        if path == own_file:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        findings += sorted({
            f"{relative(path)}:{line} {name} ({hit})"
            for line, name in names_in(tree)
            for hit in avoid_word_hits(name, phrases)
        })
    assert findings == []


def test_avoid_phrases_read_multi_word_entries_only():
    glossary = "_Avoid_: S/R zone, level\n_Avoid_: ORB as a name for the range itself\n_Avoid_: end-of-day exit, run\n"
    assert avoid_phrases(glossary) == [("sr", "zone"), ("end", "of", "day", "exit")]


def test_avoid_word_hits_match_whole_snake_case_parts():
    phrases = [("break", "entry"), ("sr", "zone")]
    assert avoid_word_hits("zone_top", phrases) == ["zone"]
    assert avoid_word_hits("uses_break_entry_price", phrases) == ["break entry"]
    assert avoid_word_hits("session_timezone", phrases) == []
    assert avoid_word_hits("breakeven_entry", phrases) == []


def test_comment_lines_skip_tool_directives():
    source = "import os  # noqa: F401\nx = 1  # explains x\n"
    assert comment_lines(source) == [2]

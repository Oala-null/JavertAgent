from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUSH_SCRIPT = ROOT / "scripts" / "push_data_hub_filled.py"
INDEX_SQL = ROOT / "scripts" / "sql" / "create_data_hub_indexes.sql"


def _push_main() -> ast.FunctionDef:
    tree = ast.parse(PUSH_SCRIPT.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )


def _is_call(node: ast.AST, owner: str, method: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == owner
        and node.func.attr == method
    )


def _has_sys_exit(node: ast.AST) -> bool:
    return any(_is_call(child, "sys", "exit") for child in ast.walk(node))


def _first_connect_line(main: ast.FunctionDef) -> int:
    return min(
        node.lineno
        for node in ast.walk(main)
        if _is_call(node, "pyodbc", "connect")
    )


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def test_push_requires_explicit_database_argument() -> None:
    main = _push_main()
    database_args = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "--database"
    ]

    assert len(database_args) == 1
    keywords = {keyword.arg: keyword.value for keyword in database_args[0].keywords}
    assert isinstance(keywords.get("required"), ast.Constant)
    assert keywords["required"].value is True


def test_invalid_database_name_is_rejected_before_connect() -> None:
    main = _push_main()
    guards = []
    for node in ast.walk(main):
        if not isinstance(node, ast.If) or not _has_sys_exit(node):
            continue
        fullmatch_calls = [
            child
            for child in ast.walk(node.test)
            if _is_call(child, "re", "fullmatch")
        ]
        if fullmatch_calls:
            guards.append((node, fullmatch_calls))

    assert len(guards) == 1
    guard, fullmatch_calls = guards[0]
    assert len(fullmatch_calls) == 1
    fullmatch = fullmatch_calls[0]
    assert isinstance(fullmatch.args[0], ast.Constant)
    assert fullmatch.args[0].value == r"[A-Za-z0-9_]+"
    assert isinstance(fullmatch.args[1], ast.Name)
    assert fullmatch.args[1].id == "target_db"
    assert "非法数据库名" in "".join(_string_constants(guard))
    assert guard.lineno < _first_connect_line(main)


def test_non_owned_database_is_rejected_before_connect() -> None:
    main = _push_main()
    owned_assignments = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "owned"
            for target in node.targets
        )
    ]
    assert len(owned_assignments) == 1
    owned_strings = _string_constants(owned_assignments[0].value)
    assert "TP_data_hub" in owned_strings
    assert "sh_yb_platform" not in owned_strings

    guards = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "target_db"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.NotIn)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Name)
        and node.test.comparators[0].id == "owned"
        and _has_sys_exit(node)
    ]
    assert len(guards) == 1
    assert "不在自有库白名单" in "".join(_string_constants(guards[0]))
    assert guards[0].lineno < _first_connect_line(main)

    option_names = {
        arg.value
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    assert "--force-db" not in option_names


def test_index_sql_selects_no_database_and_fails_closed_before_ddl() -> None:
    sql = INDEX_SQL.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())

    assert not re.search(r"(?im)^\s*USE\b", executable)
    assert not re.search(r"(?im)^\s*:setvar\s+HUB_DATABASE\b", executable)

    on_error = re.search(r"(?im)^\s*:on\s+error\s+exit\s*$", executable)
    guard = re.search(
        r"(?is)\bIF\s+DB_NAME\s*\(\s*\)\s*<>\s*N?'\$\(HUB_DATABASE\)'",
        executable,
    )
    rejection = re.search(r"(?is)\bTHROW\s+\d+\s*,", executable)
    first_ddl = re.search(
        r"(?is)\b(?:CREATE|ALTER|DROP)\s+(?:NONCLUSTERED\s+)?"
        r"(?:INDEX|TABLE|DATABASE)\b",
        executable,
    )

    assert on_error is not None
    assert guard is not None
    assert rejection is not None
    assert first_ddl is not None
    assert on_error.start() < guard.start() < rejection.start() < first_ddl.start()


def test_index_sql_uses_canonical_operation_detail_table() -> None:
    sql = INDEX_SQL.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())

    assert "ON TB_OPERATION_DETAIL (JZLSH)" in executable
    assert "TB_OPRATION_DETAIL" not in executable

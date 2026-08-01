#!/usr/bin/env python3
"""Validate the repository maintainer map without third-party dependencies."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypeGuard


MAP_PATH = Path("docs/maintainers/maintenance-map.json")
PYTHON_HTTP_ENTRYPOINT_ROOTS = (Path("backend/src/omnibase"),)
FASTAPI_FACTORY_SYMBOLS = frozenset({"APIRouter", "FastAPI"})
FASTAPI_FACTORY_MODULES = {
    "fastapi": FASTAPI_FACTORY_SYMBOLS,
    "fastapi.applications": frozenset({"FastAPI"}),
    "fastapi.routing": frozenset({"APIRouter"}),
}
INVARIANT_REQUIRED_FIELDS = (
    "id",
    "title",
    "document",
    "source_paths",
    "test_paths",
)
MODULE_REQUIRED_FIELDS = (
    "id",
    "name",
    "invariants",
    "source_paths",
    "entrypoints",
    "public_interfaces",
    "depends_on",
    "verification",
    "recovery",
)


@dataclass
class ValidationStats:
    invariants: int = 0
    modules: int = 0
    path_specs: int = 0
    matched_files: int = 0
    entrypoints: int = 0
    discovered_http_entrypoints: int = 0
    verification_commands: int = 0


def _non_empty_string(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _single_line(message: object) -> str:
    return " ".join(str(message).splitlines())


def _relative_spec(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    if (
        PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in PurePosixPath(normalized).parts
    ):
        return None
    return normalized


def _repository_matches(repo_root: Path, spec: str) -> list[Path]:
    try:
        candidates = list(repo_root.glob(spec))
        if not any(character in spec for character in "*?[") or spec.endswith("/**"):
            candidates.extend(
                nested
                for directory in candidates
                if directory.is_dir()
                for nested in directory.rglob("*")
            )
        return sorted(
            {
                path.resolve()
                for path in candidates
                if path.is_file() and path.resolve().is_relative_to(repo_root)
            }
        )
    except (OSError, ValueError):
        return []


def _validate_path_specs(
    value: object,
    *,
    label: str,
    repo_root: Path,
    errors: list[str],
    stats: ValidationStats,
) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label}: expected a non-empty list")
        return

    for index, raw_spec in enumerate(value):
        item_label = f"{label}[{index}]"
        if not _non_empty_string(raw_spec):
            errors.append(f"{item_label}: expected a non-empty relative path or glob")
            continue
        spec = _relative_spec(raw_spec)
        if spec is None:
            errors.append(
                f"{item_label}: path must be relative and may not traverse '..'"
            )
            continue
        stats.path_specs += 1
        matches = _repository_matches(repo_root, spec)
        if not matches:
            errors.append(f"{item_label}: {raw_spec!r} matched no repository files")
            continue
        stats.matched_files += len(matches)


def _validate_document(
    value: object,
    *,
    label: str,
    repo_root: Path,
    errors: list[str],
) -> None:
    if not _non_empty_string(value):
        errors.append(f"{label}: expected a non-empty repository-relative path")
        return
    path_text = value.split("#", maxsplit=1)[0]
    relative_path = _relative_spec(path_text)
    if relative_path is None:
        errors.append(f"{label}: path must be relative and may not traverse '..'")
        return
    document = (repo_root / relative_path).resolve()
    if not document.is_relative_to(repo_root) or not document.is_file():
        errors.append(
            f"{label}: {value!r} does not reference an existing repository file"
        )


def _validate_string_list(
    value: object,
    *,
    label: str,
    errors: list[str],
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        errors.append(f"{label}: expected {qualifier} of non-empty strings")
        return []

    result: list[str] = []
    for index, item in enumerate(value):
        if not _non_empty_string(item):
            errors.append(f"{label}[{index}]: expected a non-empty string")
            continue
        result.append(item.strip())
    return result


def _python_top_level_symbols(source_file: Path) -> set[str]:
    source = source_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_file))
    symbols: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            symbols.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)

    return symbols


def _fastapi_imports(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    """Return direct factory aliases and module aliases imported from FastAPI."""
    direct_factories: dict[str, str] = {}
    module_aliases: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in FASTAPI_FACTORY_MODULES:
            allowed_factories = FASTAPI_FACTORY_MODULES[node.module]
            for imported in node.names:
                if imported.name in allowed_factories:
                    direct_factories[imported.asname or imported.name] = imported.name
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "fastapi":
                    module_aliases.add(imported.asname or imported.name)

    return direct_factories, module_aliases


def _fastapi_call_kind(
    value: ast.expr,
    *,
    direct_factories: dict[str, str],
    module_aliases: set[str],
) -> str | None:
    if not isinstance(value, ast.Call):
        return None
    function = value.func
    if isinstance(function, ast.Name):
        return direct_factories.get(function.id)
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id in module_aliases
        and function.attr in FASTAPI_FACTORY_SYMBOLS
    ):
        return function.attr
    return None


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    if isinstance(node, ast.AnnAssign):
        return {node.target.id} if isinstance(node.target, ast.Name) else set()
    return {target.id for target in node.targets if isinstance(target, ast.Name)}


def _assignment_value(node: ast.Assign | ast.AnnAssign) -> ast.expr | None:
    return node.value


def _public_fastapi_factory_functions(
    tree: ast.Module,
    *,
    direct_factories: dict[str, str],
    module_aliases: set[str],
) -> set[str]:
    """Find conservative application factories that directly return a FastAPI app.

    Only direct statements in a top-level function are considered. Route handler
    decorators and general public functions are intentionally outside this check.
    """
    factories: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        application_names: set[str] = set()
        returned_names: set[str] = set()
        for statement in node.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = _assignment_value(statement)
                if value is not None and _fastapi_call_kind(
                    value,
                    direct_factories=direct_factories,
                    module_aliases=module_aliases,
                ) == "FastAPI":
                    application_names.update(_assignment_names(statement))
            elif (
                isinstance(statement, ast.Return)
                and isinstance(statement.value, ast.Name)
            ):
                returned_names.add(statement.value.id)
        if application_names & returned_names:
            factories.add(node.name)
    return factories


def _discover_python_http_entrypoints(
    repo_root: Path,
    *,
    errors: list[str],
) -> set[str]:
    """Discover only statically unambiguous FastAPI composition symbols.

    The reverse audit covers top-level ``APIRouter``/``FastAPI`` assignments,
    top-level factories that directly create and return a ``FastAPI`` instance,
    and top-level assignments that instantiate one of those same-file factories.
    It deliberately does not treat every public function or decorated route
    handler as a maintainer-map architecture entrypoint.
    """
    repo_root = repo_root.resolve()
    discovered: set[str] = set()
    for relative_root in PYTHON_HTTP_ENTRYPOINT_ROOTS:
        scan_root = (repo_root / relative_root).resolve()
        if not scan_root.is_relative_to(repo_root) or not scan_root.is_dir():
            errors.append(
                f"HTTP entrypoint scan root does not exist: {relative_root.as_posix()}"
            )
            continue
        for source_file in sorted(scan_root.rglob("*.py")):
            relative_path = source_file.relative_to(repo_root).as_posix()
            try:
                tree = ast.parse(
                    source_file.read_text(encoding="utf-8"),
                    filename=str(source_file),
                )
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append(
                    f"could not scan Python HTTP entrypoints in {relative_path!r}: "
                    f"{_single_line(exc)}"
                )
                continue
            direct_factories, module_aliases = _fastapi_imports(tree)
            if not direct_factories and not module_aliases:
                continue
            application_factories = _public_fastapi_factory_functions(
                tree,
                direct_factories=direct_factories,
                module_aliases=module_aliases,
            )
            discovered.update(
                f"{relative_path}:{factory}" for factory in application_factories
            )
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = _assignment_value(node)
                if value is None:
                    continue
                kind = _fastapi_call_kind(
                    value,
                    direct_factories=direct_factories,
                    module_aliases=module_aliases,
                )
                is_factory_instance = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in application_factories
                )
                if kind is None and not is_factory_instance:
                    continue
                discovered.update(
                    f"{relative_path}:{name}"
                    for name in _assignment_names(node)
                    if not name.startswith("_")
                )
    return discovered


def _mapped_entrypoints(
    module_records: list[tuple[str, dict[object, object]]],
) -> set[str]:
    mapped: set[str] = set()
    for _, module in module_records:
        value = module.get("entrypoints")
        if not isinstance(value, list):
            continue
        for entrypoint in value:
            if not _non_empty_string(entrypoint) or ":" not in entrypoint:
                continue
            path_text, symbol = entrypoint.rsplit(":", maxsplit=1)
            relative_path = _relative_spec(path_text)
            if relative_path is not None and symbol.strip():
                mapped.add(f"{relative_path}:{symbol.strip()}")
    return mapped


def _validate_discovered_http_entrypoints(
    module_records: list[tuple[str, dict[object, object]]],
    *,
    repo_root: Path,
    errors: list[str],
    stats: ValidationStats,
) -> None:
    discovered = _discover_python_http_entrypoints(repo_root, errors=errors)
    stats.discovered_http_entrypoints = len(discovered)
    mapped = _mapped_entrypoints(module_records)
    for entrypoint in sorted(discovered - mapped):
        errors.append(f"unmapped discovered entrypoint: {entrypoint}")


def _validate_entrypoints(
    value: object,
    *,
    label: str,
    repo_root: Path,
    errors: list[str],
    stats: ValidationStats,
) -> None:
    entrypoints = _validate_string_list(
        value,
        label=label,
        errors=errors,
        allow_empty=False,
    )
    for index, entrypoint in enumerate(entrypoints):
        item_label = f"{label}[{index}]"
        if ":" not in entrypoint:
            errors.append(f"{item_label}: expected relative/path:symbol")
            continue
        path_text, symbol = entrypoint.rsplit(":", maxsplit=1)
        symbol = symbol.strip()
        relative_path = _relative_spec(path_text)
        if relative_path is None or not symbol:
            errors.append(f"{item_label}: expected relative/path:symbol")
            continue
        source_file = (repo_root / relative_path).resolve()
        if not source_file.is_relative_to(repo_root) or not source_file.is_file():
            errors.append(f"{item_label}: source file {path_text!r} does not exist")
            continue
        if source_file.suffix == ".py":
            try:
                symbols = _python_top_level_symbols(source_file)
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append(
                    f"{item_label}: could not parse Python source {path_text!r}: "
                    f"{_single_line(exc)}"
                )
                continue
            if symbol not in symbols:
                errors.append(
                    f"{item_label}: entrypoint symbol not found: {path_text}:{symbol}"
                )
                continue
        stats.entrypoints += 1


def _verification_commands(
    value: object,
    *,
    label: str,
    errors: list[str],
) -> list[str]:
    candidates: object = value
    if isinstance(value, dict):
        if "commands" in value:
            candidates = value["commands"]
        elif "command" in value:
            candidates = [value["command"]]
        else:
            errors.append(f"{label}: expected a command or commands field")
            return []
    elif isinstance(value, str):
        candidates = [value]

    if not isinstance(candidates, list) or not candidates:
        errors.append(f"{label}: expected at least one verification command")
        return []

    commands: list[str] = []
    for index, candidate in enumerate(candidates):
        command: object = candidate
        if isinstance(candidate, dict):
            command = candidate.get("command")
        if not _non_empty_string(command):
            errors.append(f"{label}[{index}]: verification command must be non-empty")
            continue
        commands.append(command.strip())
    return commands


def _validate_recovery(value: object, *, label: str, errors: list[str]) -> None:
    if isinstance(value, str):
        valid = bool(value.strip())
    elif isinstance(value, (list, dict)):
        valid = bool(value)
    else:
        valid = False
    if not valid:
        errors.append(f"{label}: expected non-empty recovery guidance")


def _record_id(
    value: object,
    *,
    label: str,
    seen_ids: dict[str, str],
    errors: list[str],
) -> str | None:
    if not _non_empty_string(value):
        errors.append(f"{label}: expected a non-empty string")
        return None
    record_id = value.strip()
    previous = seen_ids.get(record_id)
    if previous is not None:
        errors.append(f"{label}: duplicate ID {record_id!r}; first used by {previous}")
        return record_id
    seen_ids[record_id] = label
    return record_id


def _validate_invariants(
    invariants: list[object],
    *,
    repo_root: Path,
    seen_ids: dict[str, str],
    errors: list[str],
    stats: ValidationStats,
) -> set[str]:
    invariant_ids: set[str] = set()
    for index, invariant in enumerate(invariants):
        label = f"invariants[{index}]"
        if not isinstance(invariant, dict):
            errors.append(f"{label}: expected an object")
            continue
        for field in INVARIANT_REQUIRED_FIELDS:
            if field not in invariant:
                errors.append(f"{label}: missing required field {field!r}")
        invariant_id = _record_id(
            invariant.get("id"),
            label=f"{label}.id",
            seen_ids=seen_ids,
            errors=errors,
        )
        if invariant_id is not None:
            invariant_ids.add(invariant_id)
        if not _non_empty_string(invariant.get("title")):
            errors.append(f"{label}.title: expected a non-empty string")
        _validate_document(
            invariant.get("document"),
            label=f"{label}.document",
            repo_root=repo_root,
            errors=errors,
        )
        _validate_path_specs(
            invariant.get("source_paths"),
            label=f"{label}.source_paths",
            repo_root=repo_root,
            errors=errors,
            stats=stats,
        )
        _validate_path_specs(
            invariant.get("test_paths"),
            label=f"{label}.test_paths",
            repo_root=repo_root,
            errors=errors,
            stats=stats,
        )
    stats.invariants = len(invariants)
    return invariant_ids


def _collect_modules(
    modules: list[object],
    *,
    seen_ids: dict[str, str],
    errors: list[str],
) -> list[tuple[str, dict[object, object]]]:
    module_records: list[tuple[str, dict[object, object]]] = []
    for index, module in enumerate(modules):
        label = f"modules[{index}]"
        if not isinstance(module, dict):
            errors.append(f"{label}: expected an object")
            continue
        for field in MODULE_REQUIRED_FIELDS:
            if field not in module:
                errors.append(f"{label}: missing required field {field!r}")
        _record_id(
            module.get("id"),
            label=f"{label}.id",
            seen_ids=seen_ids,
            errors=errors,
        )
        module_records.append((label, module))
    return module_records


def _validate_module_details(
    module_records: list[tuple[str, dict[object, object]]],
    *,
    invariant_ids: set[str],
    module_ids: set[str],
    repo_root: Path,
    errors: list[str],
    stats: ValidationStats,
) -> None:
    for label, module in module_records:
        module_id = module.get("id")
        if not _non_empty_string(module.get("name")):
            errors.append(f"{label}.name: expected a non-empty string")
        references = _validate_string_list(
            module.get("invariants"),
            label=f"{label}.invariants",
            errors=errors,
            allow_empty=False,
        )
        for reference in references:
            if reference not in invariant_ids:
                errors.append(f"{label}.invariants: unknown invariant ID {reference!r}")
        _validate_path_specs(
            module.get("source_paths"),
            label=f"{label}.source_paths",
            repo_root=repo_root,
            errors=errors,
            stats=stats,
        )
        if "test_paths" in module:
            _validate_path_specs(
                module.get("test_paths"),
                label=f"{label}.test_paths",
                repo_root=repo_root,
                errors=errors,
                stats=stats,
            )
        _validate_entrypoints(
            module.get("entrypoints"),
            label=f"{label}.entrypoints",
            repo_root=repo_root,
            errors=errors,
            stats=stats,
        )
        _validate_string_list(
            module.get("public_interfaces"),
            label=f"{label}.public_interfaces",
            errors=errors,
            allow_empty=False,
        )
        dependencies = _validate_string_list(
            module.get("depends_on"),
            label=f"{label}.depends_on",
            errors=errors,
            allow_empty=True,
        )
        for dependency in dependencies:
            if dependency not in module_ids:
                errors.append(f"{label}.depends_on: unknown module ID {dependency!r}")
            if dependency == module_id:
                errors.append(f"{label}.depends_on: module may not depend on itself")
        commands = _verification_commands(
            module.get("verification"),
            label=f"{label}.verification",
            errors=errors,
        )
        stats.verification_commands += len(commands)
        _validate_recovery(
            module.get("recovery"),
            label=f"{label}.recovery",
            errors=errors,
        )


def validate_map(data: object, repo_root: Path) -> tuple[list[str], ValidationStats]:
    errors: list[str] = []
    stats = ValidationStats()
    if not isinstance(data, dict):
        return ["root: expected a JSON object"], stats

    if data.get("schema_version") != 1:
        errors.append("schema_version: expected integer 1")

    invariants = data.get("invariants")
    modules = data.get("modules")
    if not isinstance(invariants, list) or not invariants:
        errors.append("invariants: expected a non-empty list")
        invariants = []
    if not isinstance(modules, list) or not modules:
        errors.append("modules: expected a non-empty list")
        modules = []

    seen_ids: dict[str, str] = {}
    invariant_ids = _validate_invariants(
        invariants,
        repo_root=repo_root,
        seen_ids=seen_ids,
        errors=errors,
        stats=stats,
    )
    module_records = _collect_modules(
        modules,
        seen_ids=seen_ids,
        errors=errors,
    )
    module_ids = {
        module_id.strip()
        for _, module in module_records
        if _non_empty_string(module_id := module.get("id"))
    }
    stats.modules = len(modules)
    _validate_module_details(
        module_records,
        invariant_ids=invariant_ids,
        module_ids=module_ids,
        repo_root=repo_root,
        errors=errors,
        stats=stats,
    )
    _validate_discovered_http_entrypoints(
        module_records,
        repo_root=repo_root,
        errors=errors,
        stats=stats,
    )
    return errors, stats


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate docs/maintainers/maintenance-map.json.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (defaults to the root containing this script).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    map_path = repo_root / MAP_PATH

    if not repo_root.is_dir():
        print(f"ERROR: repo root does not exist: {repo_root}", file=sys.stderr)
        return 2
    try:
        with map_path.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        print(f"ERROR: maintainer map does not exist: {map_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: invalid JSON at line {exc.lineno}, column {exc.colno}: "
            f"{_single_line(exc.msg)}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"ERROR: could not read maintainer map: {_single_line(exc)}",
            file=sys.stderr,
        )
        return 2

    errors, stats = validate_map(data, repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {_single_line(error)}", file=sys.stderr)
        return 1

    print(
        "Maintainer map valid: "
        f"{stats.invariants} invariants, "
        f"{stats.modules} modules, "
        f"{stats.path_specs} path specs, "
        f"{stats.matched_files} matched files, "
        f"{stats.entrypoints} entrypoints, "
        f"{stats.discovered_http_entrypoints} discovered HTTP entrypoints, "
        f"{stats.verification_commands} verification commands."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

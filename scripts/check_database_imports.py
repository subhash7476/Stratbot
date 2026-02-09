#!/usr/bin/env python3
"""
CI Check: Database Import Validation
------------------------------------
Fails the build if deprecated database access patterns are found.

This script checks for:
1. Direct `from core.data.` imports (should use `core.database` instead)
2. Direct `duckdb.connect()` calls (should use DatabaseManager instead)

Usage:
    python scripts/check_database_imports.py

Exit Codes:
    0 - All checks passed
    1 - Deprecated patterns found
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Directories to exclude from checks
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "*.egg-info",
}

# Files to exclude (relative to project root)
EXCLUDED_FILES = {
    # Backward compatibility shims are allowed to use old imports
    "core/data/__init__.py",
    "core/data/duckdb_client.py",
    "core/data/schema.py",
    "core/data/analytics_persistence.py",
    "core/data/market_data_provider.py",
    "core/data/analytics_provider.py",
    "core/data/duckdb_market_data_provider.py",
    "core/data/duckdb_analytics_provider.py",
    "core/data/cached_analytics_provider.py",
    "core/data/live_market_provider.py",
    "core/data/market_hours.py",
    "core/data/market_session.py",
    "core/data/symbol_utils.py",
    "core/data/websocket_ingestor.py",
    "core/data/recovery_manager.py",
    "core/data/db_tick_aggregator.py",
    # Core database infrastructure (needs direct duckdb access)
    "core/database/manager.py",
    "core/database/legacy_adapter.py",
    # Tests are allowed to use direct duckdb for setup/teardown
    "tests/database/test_manager.py",
    # This script itself
    "scripts/check_database_imports.py",
}

# Patterns to check for
DEPRECATED_PATTERNS = [
    (
        r"from\s+core\.data\.\w+\s+import",
        "Deprecated import from core.data.* - use core.database instead"
    ),
    (
        r"duckdb\.connect\s*\(",
        "Direct duckdb.connect() call - use DatabaseManager instead"
    ),
]


def should_check_file(filepath: Path, project_root: Path) -> bool:
    """Determine if a file should be checked."""
    # Must be a Python file
    if filepath.suffix != ".py":
        return False

    # Check excluded directories
    for part in filepath.parts:
        if part in EXCLUDED_DIRS:
            return False

    # Check excluded files
    rel_path = filepath.relative_to(project_root)
    rel_path_str = str(rel_path).replace("\\", "/")

    return rel_path_str not in EXCLUDED_FILES


def check_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """
    Check a file for deprecated patterns.

    Returns:
        List of (line_number, line_content, violation_message)
    """
    violations = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        for i, line in enumerate(lines, start=1):
            for pattern, message in DEPRECATED_PATTERNS:
                if re.search(pattern, line):
                    violations.append((i, line.strip(), message))

    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}")

    return violations


def main() -> int:
    """Run the check and return exit code."""
    # Find project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    print("=" * 60)
    print("Database Import Validation Check")
    print("=" * 60)
    print(f"Project root: {project_root}")
    print()

    # Find all Python files
    all_violations = []
    files_checked = 0

    for filepath in project_root.rglob("*.py"):
        if not should_check_file(filepath, project_root):
            continue

        files_checked += 1
        violations = check_file(filepath)

        if violations:
            rel_path = filepath.relative_to(project_root)
            for line_num, line_content, message in violations:
                all_violations.append((rel_path, line_num, line_content, message))

    # Report results
    print(f"Files checked: {files_checked}")
    print()

    if all_violations:
        print("VIOLATIONS FOUND:")
        print("-" * 60)

        current_file = None
        for rel_path, line_num, line_content, message in all_violations:
            if rel_path != current_file:
                current_file = rel_path
                print(f"\n{rel_path}:")

            print(f"  Line {line_num}: {message}")
            print(f"    {line_content}")

        print()
        print("-" * 60)
        print(f"Total violations: {len(all_violations)}")
        print()
        print("To fix these issues:")
        print("  1. Replace 'from core.data.*' with 'from core.database.*'")
        print("  2. Replace 'duckdb.connect()' with 'DatabaseManager().read()'")
        print()
        print("See docs/DATABASE_REFACTOR_IMPLEMENTATION_STATUS.md for migration guide.")
        print()
        return 1

    print("All checks passed!")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

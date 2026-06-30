# RTK command guidelines

RTK reduces command output before it enters an agent's context. It does not
replace the underlying development tools or change project behavior.

## Default rule

Prefix shell commands with `rtk`:

```bash
rtk git status
rtk git diff
rtk find . -maxdepth 3 -type f
rtk read SPEC.md
rtk pytest
```

If RTK has no specialized formatter for a command, it passes the command
through. Using the prefix is therefore the safe default.

## Command chains

Prefix every command segment independently:

```bash
rtk lint && rtk pytest
rtk git add cellar tests && rtk git commit -m "Implement IRC runtime"
```

Do not apply one RTK prefix to an entire shell pipeline or command chain.

## Debugging

RTK intentionally hides routine successful output and emphasizes failures. If
that filtering conceals information needed to diagnose a problem, use
`rtk proxy` to preserve the underlying command's output while retaining RTK
usage tracking:

```bash
rtk proxy pytest tests/test_storage.py -vv -s
```

Return to normal filtered commands after diagnosing the problem.

## Useful commands

```bash
# Repository state
rtk git status --short
rtk git diff --check
rtk git log --oneline -10

# Files and search
rtk ls -la
rtk find . -maxdepth 3 -type f
rtk grep "search term" cellar tests
rtk read path/to/file.py

# Python validation
rtk pytest
rtk ruff check
rtk mypy cellar
rtk python -m compileall -q cellar tests

# Condensed output from an otherwise noisy command
rtk summary <command>
rtk err <command>
```

Only run tools that are installed in the active environment. Prefer the
repository virtual environment when one exists, for example:

```bash
rtk .venv/bin/pytest
```

## Scope

RTK controls output volume only. It does not grant permission for destructive
commands, network access, package installation, external writes, or Git
publishing. Existing project and agent safety rules still apply.

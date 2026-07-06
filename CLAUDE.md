<!-- grove:start -->
## Code navigation: grove for structure, shell for the rest

**grove** is a tree-sitter engine for *structural* code questions — byte-precise,
token-cheap (languages: python). Its tools are **deferred** MCP tools; load them in
one ToolSearch when a code question lands (don't default to a search agent or grep):
`mcp__grove__outline`, `mcp__grove__symbols`, `mcp__grove__source`, `mcp__grove__callers`, `mcp__grove__definition`, `mcp__grove__map`, `mcp__grove__check`.

**Use grove for named symbols and relationships** (every result carries a stable
`symbol-id`, `<lang>:<relpath>#<name>@<row>`, to pass forward; lines 1-based):
- What's in a file (skeleton, not the whole file) → `mcp__grove__outline` (`detail:0` if > 500 lines).
- Where a fn / type / struct / macro is defined → `mcp__grove__symbols` with `name` → `mcp__grove__source` with the id.
- One symbol's exact body → `mcp__grove__source`.
- Who calls it → `mcp__grove__callers`.
- Go-to-def from a usage (scope-aware, follows imports cross-file) → `mcp__grove__definition` with `at` (file:line:col).
- How a directory connects → `mcp__grove__map` (one call; prefer over many `mcp__grove__source`).
- Syntax after an edit → `mcp__grove__check`.

**Use the shell — the right tool, not a fallback — when grove can't see the target:**
- Text, not a symbol (a string, log / error message, config key, a macro's *value*,
  a constant, a flag, a TODO) → `grep -rn` / `rg`. grove finds definitions, not text.
- Non-code files (Makefiles, configs, data, docs) → `grep` / `read`.
- A quick fact (path exists, `ls`, `wc -l`, `find`, read a small file) → shell.

**Combine** (same 1-based lines, same bytes): `grep` a literal's line → `mcp__grove__definition`
`at` to resolve its symbol · `mcp__grove__outline` → bounded `read` (`offset`/`limit`) for
adjacent symbols · `mcp__grove__map` / `mcp__grove__symbols` to locate → `grep` a constant inside.

Rule of thumb: want a **symbol** → grove first (don't `grep` / `read` for it). Want
**text or a quick fact** → shell. Combining is fine.
<!-- grove:end -->

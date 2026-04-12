# Design: `weave providers list` + opencode built-in

**Date:** 2026-04-11
**Phase:** 4 (item 4.3)
**Status:** draft

## Problem

The provider registry has 5 built-in providers but no CLI to inspect them. Users have no way to see what's registered, what's installed, or what capabilities each provider declares. Additionally, `opencode` (sst/opencode) is now installed at `~/.opencode/bin/opencode` and should be a 6th built-in.

## Changes

1. **Add `opencode` as 6th built-in provider** — contract manifest + adapter script under `src/weave/providers/builtin/`.
2. **Add `weave providers list` CLI command** — reads the registry, probes health, displays a formatted table.
3. **Add `--json` flag** for machine-readable output.

## opencode contract

```json
{
  "contract_version": "1",
  "name": "opencode",
  "display_name": "OpenCode",
  "adapter": "opencode.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec"],
  "health_check": "opencode --version"
}
```

Adapter invokes `opencode run "$TASK"` and wraps output in the `weave.response.v1` JSON shape.

## CLI output format

```
$ weave providers list

Providers (6 registered):

  claude-code    workspace-write   [tool-use, file-edit, shell-exec, streaming]   installed   (builtin)
  codex          workspace-write   [tool-use, file-edit, shell-exec]              not found   (builtin)
  gemini         workspace-write   [tool-use, file-edit, shell-exec]              not found   (builtin)
  ollama         read-only         [structured-output]                            not found   (builtin)
  opencode       workspace-write   [tool-use, file-edit, shell-exec]              installed   (builtin)
  vllm           read-only         [structured-output]                            not found   (builtin)
```

With `--json`: dumps list of contract dicts with an `installed` boolean added.

## Files

| Path | Change |
|---|---|
| `src/weave/providers/builtin/opencode.contract.json` | NEW |
| `src/weave/providers/builtin/opencode.sh` | NEW |
| `src/weave/cli.py` | Add `providers` group + `list` subcommand |
| `tests/test_providers.py` | +3 tests |
| `tests/test_registry.py` | Update BUILTIN_NAMES to include opencode |

## Non-goals

- Provider management (add/remove/enable/disable via CLI)
- Detailed single-provider inspect (`weave providers show claude-code`)

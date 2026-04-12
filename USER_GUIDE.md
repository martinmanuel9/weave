# Weave User Guide

Weave is a governed runtime for composing AI agent CLIs into a single, observable, policy-enforced pipeline. Instead of calling `claude --print`, `codex exec`, or `opencode run` directly, you route invocations through weave — which adds security scanning, capability enforcement, session tracking, and audit logging around every call.

## Installation

```bash
cd ~/repos/weave
pip install -e ".[dev]"
```

Or run without installing via `PYTHONPATH=src`:

```bash
PYTHONPATH=src weave --help
```

## Quick Start

```bash
# 1. Initialize a weave project
cd my-project
weave init --name my-project

# 2. Check what's set up
weave status
weave providers list

# 3. Invoke a provider through the governed runtime
weave invoke "add input validation to the login form"

# 4. See what happened
weave status
```

## The .harness/ Directory

`weave init` creates this structure:

```
.harness/
├── config.json              # Project configuration (phase, providers, security, sandbox)
├── manifest.json            # Project identity (name, phase, status)
├── context/                 # Shared context sent to every provider
│   ├── conventions.md       # Coding standards and rules
│   ├── brief.md             # What this project is building
│   └── spec.md              # Requirements and acceptance criteria
├── hooks/                   # Pre/post-invoke hook scripts
├── providers/               # Adapter scripts + contract manifests
│   ├── claude-code.sh
│   ├── claude-code.contract.json
│   ├── codex.sh
│   └── ...
├── sessions/                # Session logs, bindings, markers
│   ├── {session-id}.jsonl   # Activity records (one JSON line per invocation stage)
│   ├── {session-id}.binding.json    # Session compatibility fingerprint
│   ├── {session-id}.start_marker.json   # Git state at session start
│   └── session_history.jsonl        # Compacted session ledger
└── integrations/            # External system configs
```

## Commands

### weave init

Scaffold a new weave project in the current directory.

```bash
weave init                              # defaults: sandbox phase, claude-code provider
weave init --name my-app --phase mvp    # custom name and phase
weave init --with-quality-gates         # install pytest + ruff post-invoke hooks
```

Options:
- `--name` — project name (defaults to directory name)
- `--provider` — default provider (default: claude-code)
- `--phase` — sandbox, mvp, or enterprise (default: sandbox)
- `--with-quality-gates` — copy built-in pytest + ruff hook scripts into `.harness/hooks/`

### weave invoke

Run an agent provider through the governed pipeline.

```bash
weave invoke "implement the user authentication system"
weave invoke "analyze this codebase" --provider ollama
weave invoke "deploy to staging" --risk-class external-network --timeout 600
```

The pipeline stages:
1. **Prepare** — load config, resolve provider contract, assemble context (stable + volatile), create session
2. **Policy check** — evaluate risk class against phase enforcement, run pre-invoke hooks
3. **Invoke** — spawn the adapter subprocess with the task payload (sandbox: sanitized environment)
4. **Security scan** — check changed files against write-deny list + supply chain rules
5. **Cleanup** — run post-invoke hooks
6. **Revert** — if security denied, roll back changed files via git
7. **Record** — append activity record to session JSONL

Options:
- `--provider` / `-p` — override default provider
- `--timeout` / `-t` — seconds (default: 300)
- `--risk-class` — request a specific risk class (must be <= provider ceiling)

### weave status

Show project state at a glance.

```bash
weave status
```

Output:
```
Project:  my-project
Phase:    sandbox
Status:   active
Provider: claude-code
Enabled providers: claude-code, ollama
Sessions: 3 active, 15 compacted

Recent activity:
  [2026-04-11 10:30] claude-code — success — implement auth middleware

Session history (compacted):
  [2026-04-10] sess-abc12345 — claude-code — 12 invocations — 45.0s — success
```

### weave providers list

Inspect the provider registry.

```bash
weave providers list           # formatted table
weave providers list --json    # machine-readable JSON
```

Shows each provider's name, capability ceiling, declared features, health status (installed / not found), and source (builtin / user).

### weave compact

Clean up old sessions: summarize to a ledger, delete raw files.

```bash
weave compact              # summarize + delete sessions beyond retention limit
weave compact --dry-run    # preview what would be removed
```

Controlled by `config.sessions.compaction.sessions_to_keep` (default: 50).

Within-session compaction happens automatically — each JSONL file is capped at `records_per_session` (default: 50) records via rolling compaction on every write.

### weave translate

Generate provider-specific context files from `.harness/context/`.

```bash
weave translate          # generate CLAUDE.md, AGENTS.md, GEMINI.md
weave translate --force  # overwrite hand-edited files
```

### weave validate

Check that `.harness/` is well-formed.

```bash
weave validate
```

Verifies: manifest exists and parses, config loads, providers resolve, context files readable.

### weave session-start / session-end

Wrap external execution (e.g., GSD plans) in weave governance.

```bash
# Start: captures git state, writes binding + marker, prints session ID
SESSION_ID=$(weave session-start --task "execute GSD plan phase 7")

# ... external work happens here (GSD, manual edits, etc.) ...

# End: scans changes, runs security policy, records outcome
weave session-end --session-id $SESSION_ID
```

### weave sync

Sync project context to Open Brain (if available).

```bash
weave sync
```

## Phases

Weave has three enforcement phases, set in `config.json`:

| Phase | Policy | Security | Sandbox env |
|---|---|---|---|
| **sandbox** | Denies external-network + destructive. Restricts workspace-write (extra write-deny, env sanitization). Allows read-only. | Security findings enforced as-is (deny = deny). | Credentials stripped, PATH restricted, HOME isolated in tmpdir. |
| **mvp** | Denies external-network + destructive. Allows workspace-write + read-only. | Security findings enforced as-is. | No env restriction. |
| **enterprise** | Same as mvp. | Same as mvp. | No env restriction. |

Promote a project by editing `config.json`:
```json
{ "phase": "mvp" }
```

## Risk Classes

Each provider declares a capability ceiling in its contract:

| Risk Class | Meaning | Examples |
|---|---|---|
| `read-only` | Cannot write files | ollama, vllm |
| `workspace-write` | Can create/modify files in the project | claude-code, codex, gemini, opencode |
| `external-network` | Can make outbound HTTP calls | (none by default) |
| `destructive` | Can delete files, run arbitrary commands | (none by default) |

Config can restrict a provider below its ceiling via `capability_override`:
```json
{
  "providers": {
    "claude-code": {
      "command": "claude",
      "enabled": true,
      "capability_override": "read-only"
    }
  }
}
```

## Provider Contracts

Each provider has a `.contract.json` manifest declaring its capabilities:

```json
{
  "contract_version": "1",
  "name": "claude-code",
  "display_name": "Claude Code",
  "adapter": "claude-code.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec", "streaming"],
  "health_check": "claude --version"
}
```

Built-in providers ship with weave. User-defined providers go in `.harness/providers/` as a `.sh` + `.contract.json` pair.

### Built-in Providers

| Provider | Ceiling | Features | Health check |
|---|---|---|---|
| claude-code | workspace-write | tool-use, file-edit, shell-exec, streaming | `claude --version` |
| codex | workspace-write | tool-use, file-edit, shell-exec | `codex --version` |
| gemini | workspace-write | tool-use, file-edit, shell-exec | `gemini --version` |
| ollama | read-only | structured-output | `ollama --version` |
| opencode | workspace-write | tool-use, file-edit, shell-exec | `opencode --version` |
| vllm | read-only | structured-output | `vllmc server status` |

## Configuration Reference

`config.json` fields:

```json
{
  "version": "1",
  "phase": "sandbox",
  "default_provider": "claude-code",

  "providers": {
    "claude-code": {
      "command": "claude",
      "enabled": true,
      "capability_override": null
    }
  },

  "hooks": {
    "pre_invoke": [],
    "post_invoke": [".harness/hooks/run-tests.sh"],
    "pre_commit": []
  },

  "sessions": {
    "compaction": {
      "records_per_session": 50,
      "sessions_to_keep": 50
    },
    "binding_policy": "warn"
  },

  "security": {
    "supply_chain_rules": {},
    "write_deny_list": [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "credentials.json", "config.json", ".harness/config.json", ".git/config"],
    "write_deny_extras": [],
    "write_allow_overrides": [],
    "scanner_allowlist": []
  },

  "sandbox": {
    "strip_env_patterns": ["AWS_*", "AZURE_*", "GCP_*", "GOOGLE_*", "GITHUB_TOKEN", "GITLAB_TOKEN", "NPM_TOKEN", "PYPI_TOKEN", "SSH_AUTH_SOCK", "GPG_*"],
    "safe_path_dirs": ["/usr/bin", "/bin", "/usr/local/bin"],
    "extra_write_deny": [".git/hooks/*", "Makefile", "Dockerfile", "docker-compose*", "*.sh", ".github/workflows/*", "package.json", "pyproject.toml", "Cargo.toml"],
    "restrict_home": true
  },

  "volatile_context": {
    "enabled": true,
    "git_diff_enabled": true,
    "git_diff_max_files": 30,
    "git_log_enabled": true,
    "git_log_max_entries": 10,
    "activity_enabled": true,
    "activity_max_records": 5,
    "max_total_chars": 8000
  },

  "logging": { "level": "info", "format": "jsonl" },
  "context": { "translate_to": ["claude-code", "codex", "gemini", "ollama"] }
}
```

### Key Config Sections

**sessions.binding_policy** — what happens when a reused session's binding has drifted:
- `"warn"` (default) — log warning, update binding, continue
- `"rebind"` — silently update binding, continue
- `"strict"` — refuse to proceed, raise error

**security.scanner_allowlist** — fnmatch patterns for files to skip during content scanning:
```json
{ "scanner_allowlist": ["tests/*", "src/weave/core/security.py"] }
```

**sandbox.strip_env_patterns** — env var patterns stripped in sandbox phase. Customize to allow specific credentials:
```json
{ "strip_env_patterns": ["AWS_*", "AZURE_*"] }
```

## Context Assembly

Every invocation sends the provider a context string assembled from two parts:

1. **Stable prefix** — concatenation of `.harness/context/*.md` files in canonical order (conventions.md, brief.md, spec.md, then alphabetical). Deterministic and cache-stable.

2. **Volatile section** — per-invocation context appended after a `---` separator:
   - Git diff (changed files since last commit)
   - Git log (recent commits)
   - Session activity (previous invocations in this session)

Volatile context is configurable via `volatile_context` in config. Set `enabled: false` to disable entirely.

## Security

### Write-Deny List

Files matching `security.write_deny_list` patterns are flagged when created or modified by a provider. In sandbox and mvp/enterprise phases, violations trigger file revert.

### Supply Chain Scanner

Six built-in rules scan file contents for dangerous patterns:
- `pth-injection` — Python .pth files (auto-run on import)
- `base64-exec` — base64 decode combined with dynamic code execution
- `encoded-subprocess` — subprocess with base64 arguments
- `outbound-exfil` — HTTP POST/PUT to external URLs
- `unsafe-deserialize` — unsafe deserialization APIs
- `credential-harvest` — reading from credential storage paths

Override per-rule actions in config:
```json
{ "supply_chain_rules": { "outbound-exfil": { "action": "log" } } }
```

### Sandbox Extra Restrictions

In sandbox phase, `sandbox.extra_write_deny` adds patterns that block CI/CD pipelines, build configs, and shell scripts from being modified by providers. See the config reference above for the full default list.

## Adding a Custom Provider

1. Create an adapter script (`.harness/providers/my-provider.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"

# Your invocation logic here
STDOUT=$(my-tool "$TASK" 2>/dev/null) || EXIT_CODE=$?

jq -n --arg stdout "$STDOUT" --arg stderr "" --argjson exitCode "${EXIT_CODE:-0}" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

2. Create a contract manifest (`.harness/providers/my-provider.contract.json`):

```json
{
  "contract_version": "1",
  "name": "my-provider",
  "display_name": "My Provider",
  "adapter": "my-provider.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": [],
  "health_check": "which my-tool"
}
```

3. Add to config:

```json
{
  "providers": {
    "my-provider": { "command": "my-tool", "enabled": true }
  }
}
```

4. Verify: `weave providers list` should show your provider.

## Wire Protocol v1

Adapters receive a JSON payload on stdin:

```json
{
  "protocol": "weave.request.v1",
  "session_id": "abc-123",
  "task": "implement the feature",
  "workingDir": "/path/to/project",
  "context": "# Conventions\n...\n---\n## Recent Git State\n...",
  "timeout": 300
}
```

Adapters must emit a JSON response on stdout:

```json
{
  "protocol": "weave.response.v1",
  "exitCode": 0,
  "stdout": "Done! Created 3 files.",
  "stderr": "",
  "structured": {}
}
```

## Development

```bash
# Run tests
PYTHONPATH=src pytest tests/ -v

# Type check
pip install pyright
PYTHONPATH=src pyright src/

# Current stats: 254 tests, 6 built-in providers, 11 CLI commands
```

# Harness Audit: Hermes Agent — Patterns for Internal Harness Development
**Agent:** Claude Code (Opus 4.6)
**Date:** 2026-04-08
**Project:** Internal Harness Development Strategy (Itzel + Weave)
**Source Repository:** [NousResearch/Hermes-Agent](https://github.com/NousResearch/Hermes-Agent) v0.8.0

---

## 1. Executive Summary

This audit deeply evaluates the **Hermes Agent** development harness — the configuration, CI/CD, security, testing, extensibility, and developer experience patterns used by Nous Research to build and maintain a production-grade AI agent. The goal is to extract best practices that Weave and Itzel can inherit for our internal harness.

### Key Finding

Hermes Agent's harness excels in **three areas we should adopt**: (1) supply-chain security scanning in CI, (2) a self-registering tool/skill architecture that cleanly separates concerns, and (3) bounded persistent memory with character limits that prevent context bloat. Its weaknesses — no pre-commit hooks, no linter enforcement, and no automated code formatting — are gaps our harness already addresses through Weave's hook system.

### Comparison to Gemini Audit

The Gemini audit (see `audit/gemini/report.md`) focused on Claw Code (a Rust/Python Claude Code port) vs. Weave. This audit focuses on **Hermes Agent itself** — the upstream project whose harness configuration we're evaluating. Where Gemini's audit recommended the "Fabric Architecture" (agent as plugin, Weave as orchestrator), this audit goes deeper into the *specific mechanisms* worth inheriting.

---

## 2. Harness Architecture Overview

Hermes Agent's harness is distributed across several layers:

```
Layer 1: Developer Onboarding
  ├── setup-hermes.sh          (one-command bootstrap)
  ├── flake.nix + devShell     (reproducible Nix environments)
  ├── .env.example             (17KB env template, all providers)
  └── cli-config.yaml.example  (42KB runtime config template)

Layer 2: Code Quality Gates
  ├── GitHub Actions CI        (6 workflows)
  ├── supply-chain-audit.yml   (litellm-pattern scanning)
  ├── CONTRIBUTING.md          (661-line contributor guide)
  └── AGENTS.md                (AI assistant development guide)

Layer 3: Runtime Architecture
  ├── Self-registering tools   (registry.register() at import time)
  ├── Toolset grouping         (platform-specific tool sets)
  ├── Bounded memory           (MEMORY.md: 2200 chars, USER.md: 1375 chars)
  └── Context compression      (auto-summarize at 50% context limit)

Layer 4: Extensibility
  ├── Skills system            (SKILL.md frontmatter + instructions)
  ├── Plugin system            (plugin.yaml + pip dependencies)
  ├── Skin engine              (data-driven CLI theming)
  └── MCP server               (stdio protocol for IDE integration)
```

---

## 3. Benefits — What to Inherit

### 3.1 Supply Chain Security Scanning (CRITICAL — Adopt Immediately)

**What:** A GitHub Actions workflow (`supply-chain-audit.yml`) that scans every PR diff for known attack patterns.

**Patterns Detected:**
| Pattern | Severity | Attack Reference |
|---------|----------|-----------------|
| `.pth` file additions | CRITICAL | litellm supply chain attack (auto-exec on Python startup) |
| `base64.b64decode` + `exec/eval` | CRITICAL | litellm payload obfuscation |
| `subprocess` with obfuscated args | HIGH | Command injection via encoded strings |
| Outbound `POST`/`PUT` calls | MEDIUM | Data exfiltration |
| `setup.py`/`setup.cfg` hooks | HIGH | Install-time code execution |
| Unsafe deserialization (`marshal.loads`, etc.) | HIGH | Arbitrary code execution |

**Why this matters for us:** Our harness orchestrates multiple AI agents that can write and commit code. A compromised dependency or a malicious PR could inject code that exfiltrates API keys. This scanner catches the exact patterns used in real attacks.

**How to adopt:** Port `supply-chain-audit.yml` to Weave's hook system as a `post-invoke` hook. After any agent writes code, scan the git diff for these patterns before allowing commit.

### 3.2 Self-Registering Tool Architecture

**What:** Each tool file co-locates its schema, handler, and registration. No central manifest to maintain.

```python
# tools/my_tool.py — everything in one file
registry.register(
    name="my_tool",
    toolset="my_toolset",
    schema=MY_TOOL_SCHEMA,
    handler=lambda args, **kw: my_tool(**args, **kw),
    check_fn=_check_requirements,      # availability check
    requires_env=["MY_API_KEY"],        # env var dependencies
)
```

**Why this matters for us:** Weave currently routes tasks to providers via bash adapter scripts. But when we build internal tools (e.g., CJE quality gates, Open Brain memory capture), we need a clean registration pattern. Self-registration eliminates manifest-drift — the tool IS its own registration.

**How to adopt:** Implement a `weave.tools.registry` module that mirrors this pattern. Each Weave tool (lint, test, capture, calibrate) registers itself at import time with schema + handler + availability check.

### 3.3 Bounded Persistent Memory

**What:** Agent memory is stored in two files with strict character limits:
- `MEMORY.md` — 2,200 characters (agent notes, project patterns)
- `USER.md` — 1,375 characters (user preferences, working style)

A nudge fires every 10 turns to encourage saving. Memory flushes on session exit.

**Why this matters for us:** Unbounded memory is a context-poisoning vector. An agent that accumulates 50KB of "memories" across sessions will eventually degrade its own performance. Character limits force consolidation — the agent must decide what's worth keeping.

**How to adopt:** Implement bounded memory in Weave's session system. Each provider invocation should have access to a memory budget (configurable in `.harness/config.json`). Weave's `post-invoke` hook should enforce the budget by truncating or summarizing excess.

### 3.4 Skill vs. Tool Decision Framework

**What:** CONTRIBUTING.md provides an explicit decision tree:
- **Skill** = instructions + shell commands + existing tools (expressible as markdown)
- **Tool** = requires custom Python integration, binary data, streaming, or precise execution

**Categorization:**
- **Bundled skills** (`skills/`) — broadly useful, shipped with every install
- **Optional skills** (`optional-skills/`) — official but niche, discoverable via hub
- **Hub skills** — community-contributed, installed on demand

**Why this matters for us:** Itzel needs this same distinction. When adding capabilities to our harness, we should default to skills (markdown instructions + existing CLI tools) and only build tools when we need precision, binary handling, or streaming.

**How to adopt:** Add a `skills/` directory to `.harness/` alongside `context/`. Skills are markdown files with frontmatter that Weave injects into provider context. Tools are Python modules registered via the registry pattern.

### 3.5 Multi-Environment Terminal Backends

**What:** Terminal tool execution is abstracted behind `BaseEnvironment` with 6 backends: local, Docker, SSH, Singularity, Modal, Daytona. Each backend implements the same interface (run command, get output, manage lifecycle).

**Why this matters for us:** We run on DGX Spark locally but may want to dispatch work to containers or remote machines. An environment abstraction lets us swap execution targets without changing the orchestration layer.

**How to adopt:** Weave's provider adapter scripts are already a lightweight version of this. Formalize the interface: `run(command) -> (exit_code, stdout, stderr)`, `is_available() -> bool`, `lifecycle(start/stop)`.

### 3.6 Context Compression

**What:** When the conversation reaches 50% of the model's context limit, a cheap/fast model (e.g., Gemini Flash) summarizes older messages. The first 3 turns and last 20 messages are protected from compression.

**Why this matters for us:** Long GSD cycles can exhaust context. Rather than failing, compress transparently.

**How to adopt:** Implement in Weave's session module. After each provider invocation, check context size. If approaching limit, compress using a local model (Ollama on DGX) or cheap API model.

---

## 4. Cons — What NOT to Inherit

### 4.1 No Pre-Commit Hooks

**Issue:** Hermes relies entirely on GitHub Actions for quality enforcement. There are no local pre-commit hooks — developers can push code that fails lint, has formatting issues, or violates conventions.

**Impact:** Quality issues are caught late (after push, during PR review). This adds friction to the contributor loop.

**Our advantage:** Weave already has a hook system (`pre-invoke`, `post-invoke`). We should use `pre-invoke` hooks to run linting and formatting before any agent writes code, and `post-invoke` hooks to verify the output.

### 4.2 No Automated Code Formatting

**Issue:** No `.ruff.toml`, `.flake8`, `.black.toml`, or equivalent. Code style is enforced only through CONTRIBUTING.md documentation and PR review.

**Impact:** Style inconsistencies across 660+ lines of contribution guidelines that humans and agents must internalize manually.

**Our approach:** Enforce formatting via `ruff` or `black` in Weave hooks. The hook runs automatically — no cognitive load on the agent or developer.

### 4.3 Monolithic Config Files

**Issue:** `cli-config.yaml.example` is 42.5KB and `.env.example` is 17KB. These are massive files that new contributors must navigate.

**Impact:** Configuration overwhelm. New users struggle to find the 3-4 settings they actually need.

**Our approach:** Weave's 3-layer config resolution (defaults -> user -> project -> local) is better. Keep sensible defaults, let users override only what they need, keep config files small.

### 4.4 No Type Checking (mypy/pyright)

**Issue:** `pyproject.toml` has no mypy or pyright configuration. No type-checking in CI.

**Impact:** Type errors are caught only at runtime. For an agent harness that orchestrates LLM calls with complex parameter passing, type safety is important.

**Our approach:** Add `pyright` or `mypy` to Weave's CI and as a pre-commit hook.

### 4.5 Single-Platform CI

**Issue:** Tests only run on `ubuntu-latest`. macOS and Windows are not tested in CI (only Nix evaluation on macOS).

**Impact:** Cross-platform bugs ship to users. The CONTRIBUTING.md acknowledges this with extensive cross-platform hardening guidelines, but those are advisory, not enforced.

**Our context:** We primarily target DGX Spark (Linux), so this is less of a concern. But if Itzel expands to other platforms, we should add matrix CI.

---

## 5. Best Practices Comparison Matrix

| Practice | Hermes Agent | Weave (Current) | Recommendation |
|----------|-------------|-----------------|----------------|
| **Onboarding** | `setup-hermes.sh` + Nix flake | Manual setup | Adopt: single bootstrap script |
| **Supply chain scanning** | CI workflow (6 attack patterns) | None | **ADOPT IMMEDIATELY** |
| **Tool registration** | Self-registering (`registry.register()`) | Bash adapter scripts | Adopt: Python registry for internal tools |
| **Memory bounds** | Character-limited MEMORY.md/USER.md | Unbounded JSONL sessions | Adopt: bounded memory per provider |
| **Context compression** | Auto-summarize at 50% limit | None | Adopt: compress via local model |
| **Pre-commit hooks** | None (CI only) | Hook system exists | Keep Weave's approach (hooks > CI-only) |
| **Code formatting** | Manual (no tooling) | Not configured | Add: `ruff format` in hooks |
| **Type checking** | None | None | Add: `pyright` in CI + hooks |
| **Skill/Tool separation** | Explicit framework (SKILL.md) | Skills via `.harness/context/` | Adopt: formalize skill frontmatter |
| **Security hardening** | 7-layer protection (see below) | Deny hooks only | Adopt: layered security model |
| **Session persistence** | SQLite + FTS5 + JSON logs | JSONL activity records | Keep: JSONL is simpler, append-only |
| **Multi-environment exec** | 6 backends via BaseEnvironment | Bash adapter scripts | Adopt: formalize environment ABC |
| **Config management** | Single 42KB YAML | 3-layer resolution | Keep: Weave's layered approach is better |
| **Conventional commits** | Enforced via PR template | Not enforced | Adopt: enforce in pre-commit hook |
| **AGENTS.md** | 800+ lines, comprehensive | `.harness/context/conventions.md` | Adopt: richer AI-specific guidance |

---

## 6. Security Model — Deep Dive

Hermes Agent implements 7 security layers. We should adopt a similar layered approach:

| Layer | Hermes Implementation | Weave Equivalent | Gap |
|-------|----------------------|-------------------|-----|
| **1. Dangerous command detection** | Regex patterns in `tools/approval.py` | Deny hooks | Partial — need regex patterns |
| **2. Sudo password protection** | `shlex.quote()` for all user input | Not applicable | N/A |
| **3. Cron prompt injection** | Scanner in `cronjob_tools.py` blocks instruction-override | Not implemented | **Gap** — if we add cron |
| **4. Write deny list** | Protected paths via `os.path.realpath()` (symlink-aware) | Not implemented | **Gap** — agents can write anywhere |
| **5. Skills guard** | Security scanner for hub-installed skills | Not implemented | **Gap** — if we add skills hub |
| **6. Code execution sandbox** | API keys stripped from env in `execute_code` | Provider isolation | Partial |
| **7. Container hardening** | Docker: caps dropped, no privilege escalation, PID/tmpfs limits | Not applicable | N/A |

### Priority Gaps to Close:
1. **Write deny list** — Agents should not be able to overwrite `.env`, `config.json`, SSH keys, etc. Implement in Weave's `pre-invoke` hook.
2. **Dangerous command detection** — Port the regex patterns from `tools/approval.py` to a Weave deny hook.
3. **Supply chain scanning** — Port `supply-chain-audit.yml` patterns to a `post-invoke` hook.

---

## 7. Testing Patterns

### What Hermes Does Well

1. **Test isolation:** `conftest.py` redirects `HERMES_HOME` to a temp dir, preventing test pollution of `~/.hermes/`
2. **Per-test timeout:** 30-second hard timeout per test (Unix only) prevents hanging tests
3. **Marker-based exclusion:** `@pytest.mark.integration` separates unit tests from API-dependent tests
4. **Parallel execution:** `pytest-xdist` with `-n auto` for fast feedback
5. **E2E separation:** Dedicated `tests/e2e/` directory with its own CI job

### What We Should Add to Weave

1. **Test isolation fixture** — Redirect `.harness/` to temp dir during tests (mirror Hermes pattern)
2. **Integration marker** — Mark tests that need Linear API, Open Brain, or provider CLIs
3. **Post-invoke test hook** — After any agent writes code, automatically run the project's test suite
4. **Supply chain test** — Unit test that verifies the supply chain scanner catches known attack patterns

---

## 8. Developer Experience Patterns

### AGENTS.md as AI-Specific Documentation

Hermes maintains a separate `AGENTS.md` (distinct from `CONTRIBUTING.md`) specifically for AI coding assistants. It includes:
- File dependency chains (import order)
- Exact patterns for adding tools, commands, config
- Architecture decisions explained at the abstraction level LLMs need

**Recommendation:** Weave's `.harness/context/conventions.md` serves this purpose but is template-level. We should populate it with the depth of Hermes's AGENTS.md — specific patterns, not generic guidelines.

### Skin/Theme Engine

Hermes's skin engine is **pure data** — no code changes to add a theme. Colors, spinner animations, branding text are all configurable via YAML.

**Recommendation:** Not directly applicable to Weave (which is a backend harness), but relevant for Itzel's CLI. Consider a data-driven theming system for Itzel's output formatting.

### Slash Command Registry

A single `COMMAND_REGISTRY` list drives CLI dispatch, gateway dispatch, Telegram menus, Slack routing, autocomplete, and help text. One source of truth, many consumers.

**Recommendation:** Adopt this pattern for Itzel's command system. Currently Itzel's commands are spread across multiple files. A central registry would reduce drift.

---

## 9. Architectural Insights for Weave Integration

### How Hermes and Weave Complement Each Other

```
Hermes Strength              Weave Strength              Combined
---------------------        ---------------------       ---------------------
Deep tool integration        Provider agnosticism        Tools register with Weave,
                                                         dispatch to any provider

Supply chain scanning        Hook lifecycle              Scanning runs as post-invoke
                                                         hook, catches agent-written code

Bounded memory               Session activity logs       Memory budget enforced per
                                                         session, logged in JSONL

Context compression          Multi-provider routing      Compress before routing to
                                                         expensive models, skip for cheap

Skill framework              Context injection           Skills injected via
                                                         .harness/context/, formatted
                                                         per provider
```

### Proposed Weave Enhancements (from this audit)

1. **`weave.tools.registry`** — Python module for self-registering internal tools
2. **`weave.security.scanner`** — Port supply chain patterns to a reusable scanner
3. **`weave.memory.bounded`** — Character-limited memory per provider session
4. **`weave.context.compressor`** — Auto-compress context before expensive model calls
5. **`.harness/skills/`** — Formal skill directory with SKILL.md frontmatter

---

## 10. Implementation Priority

Based on impact and effort, here's the recommended adoption order:

| Priority | Practice | Effort | Impact | Source |
|----------|----------|--------|--------|--------|
| **P0** | Supply chain scanner as post-invoke hook | Medium | Critical (security) | `supply-chain-audit.yml` |
| **P0** | Write deny list in pre-invoke hook | Low | Critical (security) | `tools/approval.py` |
| **P1** | Self-registering tool registry | Medium | High (extensibility) | `tools/registry.py` |
| **P1** | Bounded memory system | Low | High (context quality) | `cli-config.yaml` memory section |
| **P1** | Test isolation fixture | Low | High (test reliability) | `tests/conftest.py` |
| **P2** | Context compression | Medium | Medium (cost/reliability) | `agent/context_compressor.py` |
| **P2** | Skill framework formalization | Medium | Medium (extensibility) | `skills/`, CONTRIBUTING.md |
| **P2** | Conventional commits enforcement | Low | Medium (git hygiene) | PR template |
| **P3** | Bootstrap script | Low | Low (DX) | `setup-hermes.sh` |
| **P3** | Central command registry | Medium | Low (code org) | `hermes_cli/commands.py` |

---

## 11. Cross-Reference with Gemini Audit

The Gemini audit recommended 6 practices. Here's how this audit's findings align:

| Gemini Recommendation | This Audit's Finding | Status |
|----------------------|---------------------|--------|
| "Fabric Architecture" (agent as plugin) | Confirmed — Hermes's tool registry + Weave's hooks = clean separation | Aligned |
| Git-aware invocations (bracket with git status) | Hermes doesn't do this natively — this is a Weave strength | Weave already handles |
| Explicit quality gates (post-invoke hooks) | Supply chain scanner should be a quality gate | Extended with specific patterns |
| Structured knowledge loop (push to Open Brain) | Hermes has bounded memory; Open Brain integration is Weave's job | Complementary |
| Multi-model fallback | Hermes does this via smart model routing + context compression | Adopt compression strategy |
| Permission tiers (Deny Hooks) | Hermes has 7-layer security; Weave has hooks — combine both | Gap analysis provided |

---

## 12. Appendix: Key Files Reference

| File | Path (in hermes-agent) | What to Study |
|------|----------------------|---------------|
| Supply chain scanner | `.github/workflows/supply-chain-audit.yml` | Attack patterns to port |
| Tool registry | `tools/registry.py` | Self-registration pattern |
| Dangerous command detection | `tools/approval.py` | Regex patterns for deny list |
| Test fixtures | `tests/conftest.py` | Isolation + timeout patterns |
| Memory config | `cli-config.yaml.example` (memory section) | Bounded memory design |
| Context compression | `agent/context_compressor.py` | Compression strategy |
| Skill format | `skills/*/SKILL.md` | Frontmatter + instruction format |
| Security hardening | `CONTRIBUTING.md` (Security section) | 7-layer model |
| AI dev guide | `AGENTS.md` | Pattern for `.harness/context/conventions.md` |
| Skin engine | `hermes_cli/skin_engine.py` | Data-driven customization pattern |
| Command registry | `hermes_cli/commands.py` | Single source of truth pattern |

---

*This audit was generated by Claude Code (Opus 4.6) as part of the Weave harness evaluation initiative. It complements the Gemini CLI audit in `audit/gemini/report.md`.*

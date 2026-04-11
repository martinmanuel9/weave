# Harness Audit: Claw Code vs. Weave Fabric
**Agent:** Gemini CLI
**Date:** 2026-04-08
**Project:** Internal Harness Development Strategy

## 1. Executive Summary

This audit evaluates the **Claw Code** agent harness (a Rust/Python port of Claude Code) and the **Weave** orchestration sublayer (used by Itzel). The goal is to identify best practices for an internal development harness that balances deep agent-tool integration with pluggable, observable orchestration.

### Key Finding
While **Claw Code** provides the high-performance "engine" for tool execution and streaming interaction, **Weave** provides the "fabric" for multi-agent coordination, observability, and safety. An internal harness should use Claw Code as a primary provider while adopting Weave's explicit hook system and activity tracking.

---

## 2. Benefit Comparison

### Claw Code (The Engine)
*   **Performance:** The Rust implementation (under `rust/`) offers low-latency streaming and native tool execution (shell, file I/O, LSP).
*   **Rich UX:** Built-in terminal rendering (Markdown, spinners, diff views) makes for a polished developer experience.
*   **Integrated MCP:** First-class support for Model Context Protocol (stdio/bootstrap) allows the agent to discover and use local tools dynamically.
*   **Repo-Specific Context:** Uses `CLAW.md` discovery to automatically ingest project-specific rules and tech stack details.
*   **Compaction:** Sophisticated context window management (compaction) to handle long-running sessions.

### Weave (The Fabric)
*   **Provider Agnostic:** Wraps any CLI tool (Claw Code, Codex, Gemini) in a standard bash adapter, allowing easy provider swapping.
*   **Explicit Hook Lifecycle:** Supports `pre-invoke` and `post-invoke` hooks (Bash or Python), enabling security checks and automated verification (CJE quality gates).
*   **Automated Observability:** Captures structured `InvokeResult` including duration, exit codes, and—critically—**git diffs** of all files changed during a turn.
*   **Context Injection:** Automatically gathers context from `.harness/context/` and injects it into the task payload.
*   **Knowledge Integration:** Native integration with **Open Brain** (knowledge capture) for cross-project learning.

---

## 3. Pros & Cons Analysis

| Feature | Claw Code Harness | Weave Sublayer | Best Practice for Internal |
| :--- | :--- | :--- | :--- |
| **Coupling** | High (Internal Tooling) | Low (Subprocess/JSON) | **Low** (Decouple Agent from Orchestrator) |
| **Safety** | Permission Flags (`--danger`) | First-class Deny Hooks | **Dual** (Agent-level + Gatekeeper-level) |
| **State** | Persistent `.json` Sessions | JSONL Activity Records | **JSONL** (Append-only for better recovery) |
| **Context** | `CLAW.md` (Discovery) | `.harness/context/` (Explicit) | **Explicit Discovery** (Search then Inject) |
| **Verification** | Agent-driven | Hook-driven (e.g., `pytest`) | **Hook-driven** (Don't trust the agent) |

---

## 4. Inheritable Best Practices

We should adopt the following patterns for our internal development harness:

1.  **The "Fabric" Architecture:** Treat the agent harness as a plugin. Use a Weave-like layer to manage the lifecycle, while Claw Code handles the execution.
2.  **Git-Aware Invocations:** Every agent action must be bracketed by a git status check. Store the `git diff` in the activity log to ensure 100% traceability of agent changes.
3.  **Explicit Quality Gates:** Use `post-invoke` hooks to run linters, tests, or CJE (Calibrated Judgment Engine) before considering a task "complete."
4.  **Structured Knowledge Loop:** After successful completion, have a hook that extracts "learned patterns" (e.g., from gstack findings) and pushes them to a central knowledge base (**Open Brain**).
5.  **Multi-Model Fallback:** Implement the `is_weave_tool` pattern to allow routing certain tasks to specialized models (e.g., routing refactoring to Opus, and quick grep searches to Haiku/Flash).
6.  **Permission Tiers:** Inherit Claw Code's permission model (`DangerFullAccess`, `ReadOnly`, etc.) but enforce it at the Weave layer via Deny Hooks.

---

## 5. Implementation Roadmap (Internal)

1.  **Harness-Core (Rust):** Port the essential file/shell tools from Claw Code for maximum execution speed.
2.  **Weave-Bridge (Python):** Implement the orchestration layer in Python to allow easy integration with our existing data stack (Supabase, Open Brain).
3.  **Adapter Interface:** Standardize the JSON payload for adapters (`task`, `workingDir`, `context`).
4.  **Audit directory:** Maintain this `audit/` directory for continuous evaluation of upstream improvements in the Claw Code ecosystem.

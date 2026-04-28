"""Weave CLI — 6-command interface for the Weave agent harness."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click


def _load_dotenv():
    """Load .env files. Checks project dir first, then global locations.

    Priority (first found wins per key, existing env vars always take precedence):
      1. .env in cwd (project-level secrets)
      2. ~/.itzel/.env (Itzel global secrets)
      3. ~/.env (user-level fallback)
    """
    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".itzel" / ".env",
        Path.home() / ".env",
    ]
    for env_file in candidates:
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Weave — composing agents, CLIs, and workflows into fabric."""
    _load_dotenv()


# ---------------------------------------------------------------------------
# weave init
# ---------------------------------------------------------------------------

@main.command("init")
@click.option("--name", "-n", default=None, help="Project name (defaults to directory name)")
@click.option("--provider", "-p", default="claude-code", show_default=True, help="Default provider")
@click.option("--phase", default="sandbox", show_default=True,
              type=click.Choice(["sandbox", "mvp", "enterprise"]), help="Project phase")
@click.option("--with-quality-gates", is_flag=True, help="Install pytest + ruff post-invoke hooks")
def init_cmd(name, provider, phase, with_quality_gates):
    """Scaffold a new Weave project in the current directory."""
    try:
        from weave.core.scaffold import scaffold_project
        from weave.integrations.detection import detect_integrations

        cwd = Path.cwd()
        scaffold_project(cwd, name=name, default_provider=provider, phase=phase,
                         with_quality_gates=with_quality_gates)

        # Detect integrations and write to .harness/integrations/detected.json
        integrations = detect_integrations()
        detected_path = cwd / ".harness" / "integrations" / "detected.json"
        detected_path.parent.mkdir(parents=True, exist_ok=True)
        detected_data = [
            {
                "name": i.name,
                "type": i.type,
                "available": i.available,
                "reason": i.reason,
                "config": i.config,
            }
            for i in integrations
        ]
        detected_path.write_text(json.dumps(detected_data, indent=2))

        project_name = name or cwd.name
        click.echo(f"Initialized Weave project: {project_name}")
        click.echo(f"  Provider: {provider}  Phase: {phase}")
        click.echo(f"  Harness: {cwd / '.harness'}")
        click.echo("")
        click.echo("Integrations:")
        for i in integrations:
            status = "available" if i.available else "unavailable"
            click.echo(f"  [{status}] {i.name} ({i.type}) — {i.reason}")

        # Auto-create NotebookLM notebook if available
        nlm = next((i for i in integrations if i.name == "notebooklm" and i.available), None)
        nlm_config_path = cwd / ".harness" / "integrations" / "notebooklm.json"
        if nlm and not nlm_config_path.exists():
            try:
                import subprocess
                click.echo("")
                click.echo(f"Creating NotebookLM notebook for {project_name}...")
                result = subprocess.run(
                    ["notebooklm", "create", project_name],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Parse notebook ID (UUID) from output
                    import re
                    output = result.stdout.strip()
                    notebook_id = None
                    uuid_match = re.search(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        output, re.IGNORECASE,
                    )
                    if uuid_match:
                        notebook_id = uuid_match.group(0)

                    if not notebook_id:
                        # Fallback: list notebooks and find the one we just created
                        list_result = subprocess.run(
                            ["notebooklm", "list"],
                            capture_output=True, text=True, timeout=15,
                        )
                        # Find most recent notebook matching our name
                        for line in reversed(list_result.stdout.splitlines()):
                            if project_name.lower() in line.lower():
                                parts = line.split()
                                if parts and len(parts[0]) >= 8:
                                    notebook_id = parts[0].strip("│").strip()
                                    break

                    if notebook_id:
                        nlm_config_path.write_text(json.dumps({
                            "notebook_id": notebook_id,
                            "notebook_name": project_name,
                        }, indent=2))
                        click.echo(f"  NotebookLM notebook: {notebook_id[:12]}...")

                        # Add context files as sources
                        context_dir = cwd / ".harness" / "context"
                        subprocess.run(["notebooklm", "use", notebook_id],
                                       capture_output=True, text=True, timeout=10)
                        for ctx_file in ["brief.md", "conventions.md", "spec.md"]:
                            ctx_path = context_dir / ctx_file
                            if ctx_path.exists():
                                subprocess.run(["notebooklm", "source", "add", str(ctx_path)],
                                               capture_output=True, text=True, timeout=30)
                        click.echo("  Added harness context as notebook sources")
                    else:
                        click.echo("  NotebookLM notebook created but ID not captured")
                else:
                    click.echo(f"  NotebookLM notebook creation failed: {result.stderr.strip()[:100]}")
            except Exception as e:
                click.echo(f"  NotebookLM setup skipped: {e}")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave invoke
# ---------------------------------------------------------------------------

@main.command("invoke")
@click.argument("task")
@click.option("--provider", "-p", default=None, help="Override default provider")
@click.option("--timeout", "-t", default=300, show_default=True, help="Timeout in seconds")
@click.option("--risk-class", default=None,
              type=click.Choice(["read-only", "workspace-write", "external-network", "destructive"]),
              help="Request a specific risk class (must be <= provider ceiling)")
def invoke_cmd(task, provider, timeout, risk_class):
    """Invoke an agent provider with a task through the governed runtime."""
    try:
        from weave.core.runtime import execute
        from weave.schemas.policy import RiskClass, RuntimeStatus

        cwd = Path.cwd()
        requested = RiskClass(risk_class) if risk_class else None

        result = execute(
            task=task,
            working_dir=cwd,
            provider=provider,
            caller="cli",
            requested_risk_class=requested,
            timeout=timeout,
        )

        if result.policy_result.denials:
            for d in result.policy_result.denials:
                click.echo(f"Policy denied: {d}", err=True)
        if result.policy_result.warnings:
            for w in result.policy_result.warnings:
                click.echo(f"Policy warning: {w}", err=True)

        if result.security_result and result.security_result.findings:
            for f in result.security_result.findings:
                click.echo(
                    f"Security [{f.action_taken}] {f.rule_id}: {f.file}",
                    err=True,
                )

        if result.invoke_result is not None:
            output = result.invoke_result.stdout
            if result.invoke_result.structured and "stdout" in result.invoke_result.structured:
                output = result.invoke_result.structured["stdout"]
            if output:
                click.echo(output)
            if result.invoke_result.stderr:
                click.echo(result.invoke_result.stderr, err=True)

            duration_s = result.invoke_result.duration / 1000
            files_count = len(result.invoke_result.files_changed)
            active = provider or "weave"
            click.echo(
                f"\n{active} | {duration_s:.1f}s | {files_count} file(s) changed | "
                f"session {result.session_id} | status {result.status.value}"
            )

        if result.status == RuntimeStatus.DENIED:
            sys.exit(2)
        if result.status == RuntimeStatus.FAILED:
            sys.exit(result.invoke_result.exit_code if result.invoke_result else 1)
        if result.status == RuntimeStatus.TIMEOUT:
            sys.exit(124)

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave session-start
# ---------------------------------------------------------------------------

@main.command("session-start")
@click.option("--task", "-t", required=True, help="Task description for the wrapped session")
@click.option("--provider", "-p", default=None, help="Override default provider")
@click.option("--risk-class", default=None,
              type=click.Choice(["read-only", "workspace-write", "external-network", "destructive"]),
              help="Request a specific risk class (must be <= provider ceiling)")
def session_start_cmd(task, provider, risk_class):
    """Start a wrapped session for external execution (e.g., GSD plan).

    Captures pre-state, writes binding sidecar and start marker, prints
    the session ID to stdout. The caller is responsible for running
    `weave session-end --session-id <id>` after the wrapped work completes.
    """
    try:
        from weave.core.runtime import prepare
        from weave.core.session_marker import write_marker
        from weave.schemas.policy import RiskClass

        cwd = Path.cwd()
        requested = RiskClass(risk_class) if risk_class else None

        prepared = prepare(
            task=task,
            working_dir=cwd,
            provider=provider,
            caller="external",
            requested_risk_class=requested,
        )

        sessions_dir = cwd / ".harness" / "sessions"
        write_marker(
            session_id=prepared.session_id,
            task=task,
            working_dir=cwd,
            sessions_dir=sessions_dir,
        )

        # Print session_id to stdout for shell capture
        click.echo(prepared.session_id)

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave session-end
# ---------------------------------------------------------------------------

@main.command("session-end")
@click.option("--session-id", required=True, help="Session ID returned by session-start")
def session_end_cmd(session_id):
    """Finalize a wrapped session: scan changed files, run security policy, record outcome."""
    try:
        from weave.core.config import resolve_config
        from weave.core.context import assemble_context
        from weave.core.invoker import InvokeResult
        from weave.core.policy import evaluate_policy
        from weave.core.runtime import (
            PreparedContext,
            _record,
            _revert,
            _security_scan,
        )
        from weave.core.session_marker import compute_files_changed, read_marker
        from weave.schemas.policy import RuntimeStatus

        cwd = Path.cwd()
        sessions_dir = cwd / ".harness" / "sessions"

        # Load the marker
        marker = read_marker(session_id, sessions_dir)
        if marker is None:
            click.echo(
                f"Error: No start marker for session {session_id}. "
                f"Did you run 'weave session-start' first?",
                err=True,
            )
            sys.exit(1)

        # Reconstruct a PreparedContext directly (no second prepare() call)
        config = resolve_config(cwd)
        provider_name = config.default_provider
        provider_config = config.providers.get(provider_name)
        if provider_config is None:
            click.echo(f"Error: Provider '{provider_name}' not configured", err=True)
            sys.exit(1)

        # Resolve contract from registry
        from weave.core.registry import get_registry

        registry = get_registry()
        registry.load(cwd)
        if not registry.has(provider_name):
            known = ", ".join(sorted(c.name for c in registry.list()))
            click.echo(
                f"Error: unknown provider {provider_name!r}. Known: {known}",
                err=True,
            )
            sys.exit(1)
        contract = registry.get(provider_name)
        adapter_script = registry.resolve_adapter_path(provider_name)
        context = assemble_context(cwd)

        ctx = PreparedContext(
            config=config,
            active_provider=provider_name,
            provider_config=provider_config,
            provider_contract=contract,
            adapter_script=adapter_script,
            context=context,
            session_id=session_id,
            working_dir=cwd,
            phase=config.phase,
            task=marker.task,
            caller="external",
            requested_risk_class=None,
            pre_invoke_untracked=set(marker.pre_invoke_untracked),
        )

        # Compute the cumulative files_changed
        files_changed = compute_files_changed(marker, cwd)

        # Construct synthetic InvokeResult
        fake_invoke_result = InvokeResult(
            exit_code=0,
            stdout="",
            stderr="",
            structured=None,
            duration=0.0,
            files_changed=files_changed,
        )

        # Run security scan
        security_result = _security_scan(ctx, fake_invoke_result)

        # Determine status
        if security_result.action_taken == "denied":
            status = RuntimeStatus.DENIED
        elif security_result.action_taken == "flagged":
            status = RuntimeStatus.FLAGGED
        else:
            status = RuntimeStatus.SUCCESS

        # Run revert (no-op unless action_taken == "denied" and phase is mvp/enterprise)
        _revert(ctx, fake_invoke_result, security_result)

        # Re-evaluate policy at end-time using current config
        policy_result = evaluate_policy(
            contract=ctx.provider_contract,
            provider_config=provider_config,
            requested_class=None,
            phase=ctx.phase,
        )

        # Record the final activity
        _record(
            ctx=ctx,
            invoke_result=fake_invoke_result,
            policy_result=policy_result,
            security_result=security_result,
            pre_hook_results=[],
            post_hook_results=[],
            status=status,
        )

        # Print outcome to stdout
        click.echo(
            f"session {session_id} | status {status.value} | "
            f"{len(files_changed)} file(s) changed"
        )

        # Exit code mapping matches weave invoke
        if status == RuntimeStatus.DENIED:
            sys.exit(2)
        # SUCCESS and FLAGGED both exit 0

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave translate
# ---------------------------------------------------------------------------

@main.command("translate")
@click.option("--force", "-f", is_flag=True, default=False, help="Overwrite hand-edited files")
def translate_cmd(force):
    """Generate provider-specific context files (CLAUDE.md, AGENTS.md, GEMINI.md)."""
    try:
        from weave.core.config import resolve_config
        from weave.core.translate import translate_context

        cwd = Path.cwd()
        config = resolve_config(cwd)
        providers = config.context.translate_to

        result = translate_context(cwd, providers=providers, force=force)

        generated = result.get("generated", [])
        skipped = result.get("skipped", [])

        if generated:
            click.echo("Generated:")
            for f in generated:
                click.echo(f"  {f}")
        else:
            click.echo("No files generated.")

        if skipped:
            click.echo("Skipped (hand-edited — use --force to overwrite):")
            for f in skipped:
                click.echo(f"  {f}")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave validate
# ---------------------------------------------------------------------------

@main.command("validate")
def validate_cmd():
    """Validate the .harness/ project structure and configuration."""
    try:
        from weave.schemas.manifest import Manifest
        from weave.schemas.config import WeaveConfig

        cwd = Path.cwd()
        harness = cwd / ".harness"
        issues: list[str] = []

        # Check .harness/ exists
        if not harness.exists():
            issues.append(".harness/ directory not found — run 'weave init' first")
            click.echo("Issues found:")
            for issue in issues:
                click.echo(f"  - {issue}")
            sys.exit(1)

        # Check required subdirectories
        for subdir in ["context", "hooks", "providers", "sessions"]:
            if not (harness / subdir).is_dir():
                issues.append(f".harness/{subdir}/ directory missing")

        # Validate manifest.json
        manifest_path = harness / "manifest.json"
        if not manifest_path.exists():
            issues.append(".harness/manifest.json not found")
        else:
            try:
                Manifest.model_validate_json(manifest_path.read_text())
            except Exception as exc:
                issues.append(f".harness/manifest.json invalid: {exc}")

        # Validate config.json
        config_path = harness / "config.json"
        if not config_path.exists():
            issues.append(".harness/config.json not found")
        else:
            try:
                WeaveConfig.model_validate_json(config_path.read_text())
            except Exception as exc:
                issues.append(f".harness/config.json invalid: {exc}")

        # Check context files exist
        context_dir = harness / "context"
        if context_dir.is_dir():
            expected = ["conventions.md", "brief.md", "spec.md"]
            for fname in expected:
                if not (context_dir / fname).exists():
                    issues.append(f".harness/context/{fname} missing")
        else:
            issues.append(".harness/context/ directory missing (cannot check context files)")

        if issues:
            click.echo("Issues found:")
            for issue in issues:
                click.echo(f"  - {issue}")
            sys.exit(1)
        else:
            click.echo("Project is valid.")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave status
# ---------------------------------------------------------------------------

@main.command("status")
def status_cmd():
    """Show project status including phase, providers, and recent activity."""
    try:
        from weave.core.manifest import read_manifest
        from weave.core.config import resolve_config
        from weave.core.session import read_session_activities

        cwd = Path.cwd()

        # Read manifest
        try:
            manifest = read_manifest(cwd)
        except Exception:
            click.echo("Error: .harness/manifest.json not found — run 'weave init' first", err=True)
            sys.exit(1)

        # Resolve config
        config = resolve_config(cwd)
        enabled_providers = [
            name for name, pc in config.providers.items() if pc.enabled
        ]

        # Count sessions and gather recent activities
        sessions_dir = cwd / ".harness" / "sessions"
        active_count = 0
        recent_activities = []
        history_entries: list[dict] = []

        if sessions_dir.exists():
            session_files = sorted(
                (p for p in sessions_dir.glob("*.jsonl") if p.name != "session_history.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            active_count = len(session_files)
            for sf in session_files[:5]:
                session_id = sf.stem
                try:
                    acts = read_session_activities(sessions_dir, session_id)
                    recent_activities.extend(acts)
                except Exception:
                    pass
            # Sort by timestamp descending and take 10
            recent_activities.sort(key=lambda a: a.timestamp, reverse=True)
            recent_activities = recent_activities[:10]

            from weave.core.compaction import read_session_history
            history_entries = read_session_history(sessions_dir, max_entries=10)

        # Print status
        click.echo(f"Project:  {manifest.name}")
        click.echo(f"Phase:    {manifest.phase.value}")
        click.echo(f"Status:   {manifest.status.value}")
        click.echo(f"Provider: {config.default_provider}")
        click.echo(f"Enabled providers: {', '.join(enabled_providers) if enabled_providers else 'none'}")
        compacted_count = len(history_entries) if sessions_dir.exists() else 0
        click.echo(f"Sessions: {active_count} active, {compacted_count} compacted")

        if recent_activities:
            click.echo("\nRecent activity:")
            for act in recent_activities:
                ts = act.timestamp.strftime("%Y-%m-%d %H:%M")
                task_preview = (act.task or "")[:60]
                click.echo(f"  [{ts}] {act.provider or 'unknown'} — {act.status.value} — {task_preview}")
        else:
            click.echo("\nNo activity recorded yet.")

        if sessions_dir.exists():
            if not history_entries:
                from weave.core.compaction import read_session_history
                history_entries = read_session_history(sessions_dir, max_entries=10)
            if history_entries:
                click.echo("\nSession history (compacted):")
                for entry in history_entries:
                    started = entry.get("started", "")
                    date_str = started[:10] if started else "unknown"
                    sid = entry.get("session_id", "unknown")[:20]
                    provider = entry.get("provider") or "unknown"
                    count = entry.get("invocation_count", 0)
                    dur_s = (entry.get("total_duration_ms", 0) or 0) / 1000
                    status = entry.get("final_status", "unknown")
                    click.echo(f"  [{date_str}] {sid} — {provider} — {count} invocations — {dur_s:.1f}s — {status}")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave sync
# ---------------------------------------------------------------------------

@main.command("sync")
def sync_cmd():
    """Sync project context to Open Brain (if available)."""
    try:
        from weave.core.manifest import read_manifest
        from weave.integrations.detection import detect_integrations
        from weave.integrations.open_brain import capture_thought
        import os

        cwd = Path.cwd()

        # Read manifest for project name
        try:
            manifest = read_manifest(cwd)
            project_name = manifest.name
        except Exception:
            project_name = cwd.name

        # Load context from .harness/context/
        context_parts = []
        context_dir = cwd / ".harness" / "context"
        if context_dir.exists():
            for md_file in sorted(context_dir.glob("*.md")):
                if not md_file.name.startswith("."):
                    context_parts.append(md_file.read_text())
        context = "\n---\n".join(context_parts)

        if not context.strip():
            click.echo("No context to sync.")
            return

        # Detect integrations
        integrations = detect_integrations()
        ob = next((i for i in integrations if i.name == "open-brain" and i.available), None)

        if ob is None:
            click.echo("Open Brain not available — skipping sync.")
            click.echo("Set OPEN_BRAIN_URL and OPEN_BRAIN_KEY to enable.")
            return

        ob_url = os.environ.get("OPEN_BRAIN_URL", "")
        ob_key = os.environ.get("OPEN_BRAIN_KEY", "")

        synced: list[str] = []
        skipped: list[str] = []

        thought_content = f"[Weave sync] Project: {project_name}\n\n{context}"
        success = capture_thought(ob_url, ob_key, thought_content)

        if success:
            synced.append(f"{project_name} context")
            click.echo(f"Synced: {project_name} context -> Open Brain")
        else:
            skipped.append(f"{project_name} context")
            click.echo(f"Skipped: {project_name} context (Open Brain error)")

        # NotebookLM sync
        nlm = next((i for i in integrations if i.name == "notebooklm" and i.available), None)
        if nlm:
            from weave.integrations.notebooklm import sync_context_to_notebook
            # Look for notebook_id in .harness/integrations/notebooklm.json
            nlm_config_path = cwd / ".harness" / "integrations" / "notebooklm.json"
            if nlm_config_path.exists():
                import json as _json
                nlm_config = _json.loads(nlm_config_path.read_text())
                notebook_id = nlm_config.get("notebook_id", "")
                if notebook_id:
                    result = sync_context_to_notebook(
                        notebook_id, cwd / ".harness" / "context", project_name
                    )
                    if result["synced"]:
                        synced.append(f"{project_name} context -> NotebookLM")
                        click.echo(f"Synced: {project_name} context -> NotebookLM ({notebook_id[:8]}...)")
                    else:
                        skipped.append(f"NotebookLM: {result['error']}")
                        click.echo(f"Skipped: NotebookLM ({result['error']})")
                else:
                    skipped.append("NotebookLM: no notebook_id configured")
            else:
                click.echo("NotebookLM available but not configured. Create .harness/integrations/notebooklm.json with {\"notebook_id\": \"...\"}")
                skipped.append("NotebookLM: not configured")

        # Linear sync — create/update project + tasks from spec.md
        linear_int = next((i for i in integrations if i.name == "linear" and i.available), None)
        if linear_int:
            spec_path = cwd / ".harness" / "context" / "spec.md"
            if spec_path.exists():
                spec_content = spec_path.read_text().strip()
                if spec_content:
                    from weave.integrations.linear import sync_spec_to_linear
                    linear_result = sync_spec_to_linear(project_name, spec_content)
                    if linear_result.get("error"):
                        skipped.append(f"Linear: {linear_result['error']}")
                        click.echo(f"Skipped: Linear ({linear_result['error']})")
                    else:
                        n_tasks = linear_result["tasks_created"]
                        synced.append(f"{project_name} spec -> Linear ({n_tasks} tasks)")
                        click.echo(f"Synced: {project_name} spec -> Linear ({n_tasks} tasks)")
                        if linear_result.get("note"):
                            click.echo(f"  Note: {linear_result['note']}")

                        # Save Linear project config for future syncs
                        linear_config_path = cwd / ".harness" / "integrations" / "linear.json"
                        linear_config_path.parent.mkdir(parents=True, exist_ok=True)
                        linear_config_path.write_text(json.dumps({
                            "project_id": linear_result["project_id"],
                            "project_name": linear_result["project_name"],
                        }, indent=2))
                else:
                    skipped.append("Linear: spec.md is empty")
            else:
                skipped.append("Linear: no spec.md found")
        else:
            skipped.append("Linear: not configured (missing LINEAR_API_KEY)")

        click.echo(f"\nSynced: {len(synced)}  Skipped: {len(skipped)}")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# weave compact
# ---------------------------------------------------------------------------


@main.command("compact")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without acting")
def compact_cmd(dry_run):
    """Compact old sessions: summarize to ledger and delete raw files."""
    from weave.core.compaction import compact_sessions
    from weave.core.config import resolve_config

    working_dir = Path.cwd()
    config = resolve_config(working_dir)
    sessions_dir = working_dir / ".harness" / "sessions"
    sessions_to_keep = config.sessions.compaction.sessions_to_keep

    result = compact_sessions(sessions_dir, sessions_to_keep, dry_run=dry_run)

    if dry_run:
        click.echo(f"Dry run: would remove {result.removed} sessions ({result.kept} kept)")
    else:
        click.echo(f"Compacted {result.removed} sessions ({result.kept} kept)")
        if result.errors:
            for err in result.errors:
                click.echo(f"  error: {err}", err=True)


# ---------------------------------------------------------------------------
# weave providers
# ---------------------------------------------------------------------------

@main.group("providers")
def providers_group():
    """Manage provider contracts and registry."""
    pass


@providers_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def providers_list_cmd(as_json):
    """List all registered providers with health status and capabilities."""
    from weave.core.registry import get_registry
    from weave.core.providers import check_provider_health

    registry = get_registry()
    registry.load(Path.cwd())
    contracts = registry.list()

    if as_json:
        import json as json_mod
        output = []
        for c in contracts:
            installed = check_provider_health(c.health_check) if c.health_check else False
            output.append({
                "name": c.name,
                "display_name": c.display_name,
                "capability_ceiling": c.capability_ceiling.value,
                "declared_features": [f.value for f in c.declared_features],
                "health_check": c.health_check,
                "installed": installed,
                "source": c.source,
                "adapter_runtime": c.adapter_runtime.value,
            })
        click.echo(json_mod.dumps(output, indent=2))
        return

    click.echo(f"\nProviders ({len(contracts)} registered):\n")
    for c in contracts:
        installed = check_provider_health(c.health_check) if c.health_check else False
        features = ", ".join(f.value for f in c.declared_features)
        health = "installed" if installed else "not found"
        click.echo(
            f"  {c.name:<15} {c.capability_ceiling.value:<18} "
            f"[{features}]"
        )
        click.echo(
            f"  {'':<15} {health:<18} ({c.source})"
        )


# ---------------------------------------------------------------------------
# weave skill
# ---------------------------------------------------------------------------

@main.group("skill")
def skill_group():
    """Manage the skill registry."""


@skill_group.command("list")
def skill_list_cmd():
    """List all registered skills with metrics."""
    from weave.core.skills import list_skills

    cwd = Path.cwd()
    skills = list_skills(cwd)
    if not skills:
        click.echo("No skills registered. Use 'weave skill create' to add one.")
        return

    for s in skills:
        score = round(s.metrics.successes / s.metrics.invocations, 2) if s.metrics.invocations > 0 else 0.0
        click.echo(
            f"  {s.name:24s}  provider={s.strategy.primary_provider:12s}  "
            f"invocations={s.metrics.invocations:4d}  score={score:.2f}"
        )


@skill_group.command("show")
@click.argument("name")
def skill_show_cmd(name: str):
    """Show details for a specific skill."""
    from weave.core.skills import load_skill

    try:
        skill = load_skill(name, Path.cwd())
    except FileNotFoundError:
        click.echo(f"Skill not found: {name}")
        raise SystemExit(1)

    click.echo(skill.model_dump_json(indent=2))


@skill_group.command("create")
@click.argument("name")
@click.option("--provider", required=True, help="Primary provider name")
@click.option("--intent", multiple=True, required=True, help="Intent(s) this skill handles")
@click.option("--fallback", multiple=True, help="Fallback provider(s)")
@click.option("--context", default="", help="Context injection text")
def skill_create_cmd(
    name: str,
    provider: str,
    intent: tuple[str, ...],
    fallback: tuple[str, ...],
    context: str,
):
    """Create a new skill definition."""
    from weave.core.skills import save_skill
    from weave.schemas.skill import SkillDefinition, SkillStrategy

    skill = SkillDefinition(
        name=name,
        description=f"Skill for {', '.join(intent)}",
        intents=list(intent),
        strategy=SkillStrategy(
            primary_provider=provider,
            fallback_providers=list(fallback),
            context_injection=context,
        ),
    )
    save_skill(skill, Path.cwd())
    click.echo(f"Created skill: {name}")


@skill_group.command("promote")
@click.argument("name")
def skill_promote_cmd(name: str):
    """Promote a proven skill to Open Brain for cross-project sharing."""
    import json as _json
    from weave.core.skills import load_skill
    from weave.integrations.open_brain import capture_thought

    try:
        skill = load_skill(name, Path.cwd())
    except FileNotFoundError:
        click.echo(f"Skill not found: {name}")
        raise SystemExit(1)

    confidence = round(skill.metrics.successes / skill.metrics.invocations, 2) if skill.metrics.invocations > 0 else 0.0
    if confidence < 0.85 or skill.metrics.invocations < 5:
        click.echo(
            f"Skill {name} not ready for promotion "
            f"(score={confidence:.2f}, invocations={skill.metrics.invocations}). "
            f"Needs score >= 0.85 and >= 5 invocations."
        )
        raise SystemExit(1)

    config_path = Path.cwd() / ".harness" / "config.json"
    if not config_path.exists():
        click.echo("No .harness/config.json found.")
        raise SystemExit(1)

    config = _json.loads(config_path.read_text())
    integrations = config.get("integrations", {}).get("open_brain", {})
    ob_url = integrations.get("url", "")
    ob_key = integrations.get("key", "")

    if not ob_url or not ob_key:
        click.echo("Open Brain not configured in .harness/config.json")
        raise SystemExit(1)

    content = f"skill:{name}\n\n{skill.model_dump_json(indent=2)}"
    success = capture_thought(ob_url, ob_key, content)
    if success:
        click.echo(f"Promoted skill {name} to Open Brain")
    else:
        click.echo("Failed to promote to Open Brain")
        raise SystemExit(1)

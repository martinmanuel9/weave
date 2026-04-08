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
def init_cmd(name, provider, phase):
    """Scaffold a new Weave project in the current directory."""
    try:
        from weave.core.scaffold import scaffold_project
        from weave.integrations.detection import detect_integrations

        cwd = Path.cwd()
        scaffold_project(cwd, name=name, default_provider=provider, phase=phase)

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
                    ["notebooklm", "create", f"Weave — {project_name}"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Parse notebook ID from output (format: "Created notebook: <id>")
                    output = result.stdout.strip()
                    notebook_id = None
                    for line in output.splitlines():
                        line = line.strip()
                        # Try to find a UUID-like string
                        if len(line) >= 36 and "-" in line:
                            notebook_id = line
                            break
                        if ":" in line:
                            candidate = line.split(":")[-1].strip()
                            if len(candidate) >= 8:
                                notebook_id = candidate
                                break

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
                            "notebook_name": f"Weave — {project_name}",
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
def invoke_cmd(task, provider, timeout):
    """Invoke an agent provider with a task."""
    try:
        from weave.core.config import resolve_config
        from weave.core.hooks import HookContext, run_hooks
        from weave.core.invoker import invoke_provider
        from weave.core.session import create_session, append_activity
        from weave.schemas.activity import ActivityRecord, ActivityType, ActivityStatus

        cwd = Path.cwd()
        config = resolve_config(cwd)

        # Determine provider to use
        active_provider = provider or config.default_provider

        # Load context from .harness/context/*.md
        context_parts = []
        context_dir = cwd / ".harness" / "context"
        if context_dir.exists():
            for md_file in sorted(context_dir.glob("*.md")):
                if not md_file.name.startswith("."):
                    context_parts.append(md_file.read_text())
        context = "\n---\n".join(context_parts)

        # Run pre-invoke hooks
        pre_ctx = HookContext(
            provider=active_provider,
            task=task,
            working_dir=str(cwd),
            phase="pre-invoke",
        )
        pre_result = run_hooks(config.hooks.pre_invoke, pre_ctx)
        if not pre_result.allowed:
            denied_msg = next(
                (r.message for r in pre_result.results if r.result == "deny"), "denied by hook"
            )
            click.echo(f"Invocation denied by pre-invoke hook: {denied_msg}", err=True)
            sys.exit(1)

        # Build adapter script path
        provider_config = config.providers.get(active_provider)
        if provider_config is None:
            click.echo(f"Error: provider '{active_provider}' not configured", err=True)
            sys.exit(1)

        adapter_script = cwd / ".harness" / "providers" / f"{active_provider}.sh"

        # Invoke
        result = invoke_provider(
            adapter_script=adapter_script,
            task=task,
            working_dir=cwd,
            context=context,
            timeout=timeout,
        )

        # Run post-invoke hooks
        post_ctx = HookContext(
            provider=active_provider,
            task=task,
            working_dir=str(cwd),
            phase="post-invoke",
        )
        run_hooks(config.hooks.post_invoke, post_ctx)

        # Log activity
        session_id = create_session()
        sessions_dir = cwd / ".harness" / "sessions"
        status = ActivityStatus.success if result.exit_code == 0 else ActivityStatus.failure
        record = ActivityRecord(
            session_id=session_id,
            type=ActivityType.invoke,
            provider=active_provider,
            task=task,
            working_dir=str(cwd),
            duration=result.duration,
            exit_code=result.exit_code,
            files_changed=result.files_changed,
            status=status,
            hook_results=pre_result.results,
        )
        append_activity(sessions_dir, session_id, record)

        # Print output
        output = result.stdout
        if result.structured and "stdout" in result.structured:
            output = result.structured["stdout"]
        if output:
            click.echo(output)
        if result.stderr:
            click.echo(result.stderr, err=True)

        duration_s = result.duration / 1000
        files_count = len(result.files_changed)
        click.echo(
            f"\n{active_provider} | {duration_s:.1f}s | {files_count} file(s) changed | session {session_id}"
        )

        if result.exit_code != 0:
            sys.exit(result.exit_code)

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
        session_count = 0
        recent_activities = []

        if sessions_dir.exists():
            session_files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            session_count = len(session_files)
            for sf in session_files[:5]:
                session_id = sf.stem
                acts = read_session_activities(sessions_dir, session_id)
                recent_activities.extend(acts)
            # Sort by timestamp descending and take 10
            recent_activities.sort(key=lambda a: a.timestamp, reverse=True)
            recent_activities = recent_activities[:10]

        # Print status
        click.echo(f"Project:  {manifest.name}")
        click.echo(f"Phase:    {manifest.phase.value}")
        click.echo(f"Status:   {manifest.status.value}")
        click.echo(f"Provider: {config.default_provider}")
        click.echo(f"Enabled providers: {', '.join(enabled_providers) if enabled_providers else 'none'}")
        click.echo(f"Sessions: {session_count}")

        if recent_activities:
            click.echo("\nRecent activity:")
            for act in recent_activities:
                ts = act.timestamp.strftime("%Y-%m-%d %H:%M")
                task_preview = (act.task or "")[:60]
                click.echo(f"  [{ts}] {act.provider or 'unknown'} — {act.status.value} — {task_preview}")
        else:
            click.echo("\nNo activity recorded yet.")

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

        click.echo(f"\nSynced: {len(synced)}  Skipped: {len(skipped)}")

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

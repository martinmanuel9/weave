"""
Detect available integrations by checking environment variables and CLIs.
"""
import os
import shutil
from dataclasses import dataclass, field


@dataclass
class IntegrationStatus:
    name: str
    type: str          # memory | knowledge | tracking | ui
    available: bool
    reason: str
    config: dict = field(default_factory=dict)


def detect_integrations(env=None) -> list[IntegrationStatus]:
    """
    Check for known integrations and return their availability status.

    Parameters
    ----------
    env : dict | None
        Optional environment dict override (defaults to os.environ).
    """
    if env is None:
        env = os.environ

    results: list[IntegrationStatus] = []

    # --- open-brain (memory) ---
    ob_url = env.get("OPEN_BRAIN_URL", "")
    ob_key = env.get("OPEN_BRAIN_KEY", "")
    if ob_url and ob_key:
        results.append(IntegrationStatus(
            name="open-brain",
            type="memory",
            available=True,
            reason="OPEN_BRAIN_URL and OPEN_BRAIN_KEY are set",
            config={"url": ob_url},
        ))
    else:
        missing = []
        if not ob_url:
            missing.append("OPEN_BRAIN_URL")
        if not ob_key:
            missing.append("OPEN_BRAIN_KEY")
        results.append(IntegrationStatus(
            name="open-brain",
            type="memory",
            available=False,
            reason=f"Missing env vars: {', '.join(missing)}",
        ))

    # --- linear (tracking) ---
    linear_key = env.get("LINEAR_API_KEY", "")
    if linear_key:
        results.append(IntegrationStatus(
            name="linear",
            type="tracking",
            available=True,
            reason="LINEAR_API_KEY is set",
        ))
    else:
        results.append(IntegrationStatus(
            name="linear",
            type="tracking",
            available=False,
            reason="Missing env var: LINEAR_API_KEY",
        ))

    # --- notebooklm (knowledge) ---
    notebooklm_path = shutil.which("notebooklm")
    if notebooklm_path:
        results.append(IntegrationStatus(
            name="notebooklm",
            type="knowledge",
            available=True,
            reason=f"CLI found at {notebooklm_path}",
            config={"path": notebooklm_path},
        ))
    else:
        results.append(IntegrationStatus(
            name="notebooklm",
            type="knowledge",
            available=False,
            reason="notebooklm CLI not found in PATH",
        ))

    # --- 21st-dev (ui) ---
    tfd_key = env.get("TWENTY_FIRST_DEV_KEY", "") or env.get("TWENTY_FIRST_DEV_API_KEY", "") or env.get("21ST_DEV_KEY", "")
    if tfd_key:
        results.append(IntegrationStatus(
            name="21st-dev",
            type="ui",
            available=True,
            reason="TWENTY_FIRST_DEV_API_KEY or 21ST_DEV_KEY is set",
        ))
    else:
        results.append(IntegrationStatus(
            name="21st-dev",
            type="ui",
            available=False,
            reason="Missing env vars: TWENTY_FIRST_DEV_API_KEY or 21ST_DEV_KEY",
        ))

    return results

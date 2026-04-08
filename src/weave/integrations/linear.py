"""
Linear integration — create/sync project and tasks from harness spec.

Uses the Linear GraphQL API via urllib (no SDK dependency).
Requires LINEAR_API_KEY environment variable.
"""

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.linear.app/graphql"


def _graphql(query: str, variables: dict = None) -> dict:
    """Execute a Linear GraphQL query."""
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        raise ValueError("LINEAR_API_KEY not set")

    body = {"query": query}
    if variables:
        body["variables"] = variables

    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    if "errors" in result:
        logger.error(f"Linear API error: {result['errors']}")
    return result


def _get_team_id() -> str | None:
    """Get the first team ID."""
    result = _graphql("{ teams { nodes { id name } } }")
    nodes = result.get("data", {}).get("teams", {}).get("nodes", [])
    return nodes[0]["id"] if nodes else None


def _get_backlog_state_id(team_id: str) -> str | None:
    """Get the Backlog workflow state ID for a team."""
    result = _graphql(
        '{ workflowStates(filter: { name: { eqIgnoreCase: "Backlog" }, team: { id: { eq: "%s" } } }) { nodes { id } } }' % team_id
    )
    nodes = result.get("data", {}).get("workflowStates", {}).get("nodes", [])
    return nodes[0]["id"] if nodes else None


def find_project(name: str) -> dict | None:
    """Find a Linear project by name."""
    result = _graphql(
        '{ projects(filter: { name: { containsIgnoreCase: "%s" } }) { nodes { id name } } }' % name.replace('"', '\\"')
    )
    nodes = result.get("data", {}).get("projects", {}).get("nodes", [])
    return nodes[0] if nodes else None


def create_project(name: str, description: str = "") -> dict | None:
    """Create a new Linear project."""
    team_id = _get_team_id()
    if not team_id:
        return None

    result = _graphql(
        """mutation($input: ProjectCreateInput!) {
            projectCreate(input: $input) { success project { id name } }
        }""",
        {"input": {"name": name, "description": description[:2000], "teamIds": [team_id]}},
    )
    return result.get("data", {}).get("projectCreate", {}).get("project")


def create_task(title: str, description: str = "", project_id: str = None) -> dict | None:
    """Create a Linear issue in a project."""
    team_id = _get_team_id()
    if not team_id:
        return None

    input_data = {"teamId": team_id, "title": title, "priority": 3}
    if description:
        input_data["description"] = description[:2000]
    if project_id:
        input_data["projectId"] = project_id

    backlog_id = _get_backlog_state_id(team_id)
    if backlog_id:
        input_data["stateId"] = backlog_id

    result = _graphql(
        """mutation($input: IssueCreateInput!) {
            issueCreate(input: $input) { success issue { id identifier title url } }
        }""",
        {"input": input_data},
    )
    return result.get("data", {}).get("issueCreate", {}).get("issue")


def parse_tasks_from_spec(spec_content: str) -> list[dict]:
    """
    Parse a spec.md file and extract tasks from:
    - Checkbox items: - [ ] Task description
    - Requirements sections with bullet points
    - Acceptance criteria

    Returns list of {title, description} dicts.
    """
    tasks = []

    # Extract checkbox items (- [ ] ...)
    for match in re.finditer(r"^[-*]\s*\[[ x]?\]\s*(.+)$", spec_content, re.MULTILINE):
        title = match.group(1).strip()
        if title and len(title) > 5:
            tasks.append({"title": title, "description": ""})

    # If no checkboxes, extract bullet points under Requirements/Features/Acceptance headers
    if not tasks:
        sections = re.split(r"^##\s+", spec_content, flags=re.MULTILINE)
        for section in sections:
            header = section.split("\n")[0].lower()
            if any(kw in header for kw in ["requirement", "feature", "acceptance", "criteria", "task"]):
                for match in re.finditer(r"^[-*]\s+(.+)$", section, re.MULTILINE):
                    title = match.group(1).strip()
                    if title and len(title) > 5 and not title.startswith("#"):
                        tasks.append({"title": title, "description": ""})

    return tasks


def sync_spec_to_linear(project_name: str, spec_content: str) -> dict:
    """
    Create or find a Linear project and populate it with tasks from the spec.

    Returns dict with: project_id, project_name, tasks_created, tasks (list)
    """
    if not os.environ.get("LINEAR_API_KEY"):
        return {"error": "LINEAR_API_KEY not set", "tasks_created": 0}

    # Find or create project
    project = find_project(project_name)
    if not project:
        project = create_project(project_name, spec_content[:500])

    if not project:
        return {"error": "Failed to create Linear project", "tasks_created": 0}

    project_id = project["id"]

    # Parse tasks from spec
    tasks = parse_tasks_from_spec(spec_content)
    if not tasks:
        return {
            "project_id": project_id,
            "project_name": project.get("name", project_name),
            "tasks_created": 0,
            "tasks": [],
            "note": "No tasks found in spec. Add - [ ] items or Requirements section.",
        }

    # Create tasks in Linear
    created = []
    for task in tasks:
        issue = create_task(task["title"], task.get("description", ""), project_id)
        if issue:
            created.append(issue)

    return {
        "project_id": project_id,
        "project_name": project.get("name", project_name),
        "tasks_created": len(created),
        "tasks": created,
    }

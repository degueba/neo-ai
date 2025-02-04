import difflib
import os
import json
import typer
import yaml
import sys
from typing import Optional

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# Now you can import modules
from core.config import get_current_workspace, get_workspaces  # noqa: E402
from core.file_operations import (  # noqa: E402
    _cache_recent_file,
    _get_recent_files,
    get_codebase_structure,
)
from core.framework_helpers import get_framework_specific_prompt  # noqa: E402
from core.utils import open_in_editor  # noqa: E402
from modules.deepseek import json_prompt  # noqa: E402
from integrations.notion import list_tasks  # noqa: E402

app = typer.Typer()

# -----------------------------------------------------
# Database helpers: create/connect/seed
# -----------------------------------------------------
DB_NAME = "app_data.db"

RECENT_FILES_CACHE = os.path.join(os.path.dirname(__file__), ".recent_files")

WORKSPACE_CONFIG = os.path.join(project_root, "workspace_config.json")


# -----------------------------------------------------
# 1) show_config
# -----------------------------------------------------
@app.command()
def show_config(
    verbose: bool = typer.Option(False, "--verbose", help="Show config in detail?")
):
    """
    Shows the current configuration from modules/assistant_config.py.
    """
    try:

        config = ""

        with open("./assistant_config.yml", "r") as f:
            config = f.read()

        if verbose:
            result = f"Verbose config:\n{json.dumps(yaml.safe_load(config), indent=2)}"
        else:
            result = f"Config: {config}"
        typer.echo(result)
        return result
    except ImportError:
        result = "Error: Could not load assistant_config module"
        typer.echo(result)
        return result


# -----------------------------------------------------
# 2) list_files
# -----------------------------------------------------
@app.command()
def list_files(
    path: str = typer.Argument(..., help="Path to list files from"),
    all_files: bool = typer.Option(False, "--all", help="Include hidden files"),
):
    """
    Lists files in a directory. Optionally show hidden files.
    """
    if not os.path.isdir(path):
        msg = f"Path '{path}' is not a valid directory."
        typer.echo(msg)
        return msg

    entries = os.listdir(path)
    if not all_files:
        entries = [e for e in entries if not e.startswith(".")]

    result = f"Files in '{path}': {entries}"
    typer.echo(result)
    return result


# -----------------------------------------------------
# 3) compare_files
# -----------------------------------------------------
@app.command()
def compare_files(
    file_a: str = typer.Argument(..., help="First file to compare"),
    file_b: str = typer.Argument(..., help="Second file to compare"),
    diff_only: bool = typer.Option(
        False, "--diff-only", help="Show only the differences"
    ),
):
    """
    Compares two files, optionally showing only differences.
    """
    if not os.path.isfile(file_a) or not os.path.isfile(file_b):
        msg = f"One or both files do not exist: {file_a}, {file_b}"
        typer.echo(msg)
        return msg

    with open(file_a, "r") as fa, open(file_b, "r") as fb:
        lines_a = fa.readlines()
        lines_b = fb.readlines()

    diff = difflib.unified_diff(lines_a, lines_b, fromfile=file_a, tofile=file_b)

    if diff_only:
        # Show only differences
        differences = []
        for line in diff:
            if line.startswith("+") or line.startswith("-"):
                differences.append(line)
        result = "\n".join(differences)
    else:
        # Show entire unified diff
        result = "".join(diff)

    typer.echo(result if result.strip() else "Files are identical.")
    return result


# -----------------------------------------------------
# 4) edit_file
# -----------------------------------------------------
@app.command()
def edit_file(
    file_description: str = typer.Argument(..., help="Description of file to edit"),
    workspace: str = typer.Option(None, "--workspace", help="Specify workspace to use"),
):
    print("DEBUG: Starting edit_file command")
    try:
        # Get workspace config
        print(f"DEBUG: Getting workspace config for '{workspace}'")
        # Get current workspace configuration
        workspace = get_current_workspace()
        print(f"DEBUG: Current workspace: {workspace}")

        if workspace:
            # Get workspace config
            all_workspaces = get_workspaces()
            workspace_config = all_workspaces["workspaces"].get(workspace)

            if not workspace_config:
                print(f"DEBUG: Current workspace '{workspace}' not found in config")
                return f"Current workspace '{workspace}' not found"

            project_root = workspace_config["path"]
        else:
            # Fallback to default project root if no workspace configured
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            workspace_config = {"frameworks": {}}

        print(f"DEBUG: Project root set to: {project_root}")
        print(f"========== Starting edit_file with: {file_description}")

        # Check for recent files request
        if "recent" in file_description.lower():
            print("Handling recent files request")  # Debug print
            recent_files = _get_recent_files()
            if recent_files:
                typer.echo("Recent files:")
                for i, file_path in enumerate(recent_files[:5]):
                    typer.echo(f"{i+1}. {file_path}")
                choice = typer.prompt("Which file to open? (number)")
                try:
                    selected = recent_files[int(choice) - 1]
                    print(f"========== Opening file: {selected}")  # Debug print
                    os.system(f"code --goto {selected}")
                    return f"========== Opened recent file: {selected}"
                except (ValueError, IndexError):
                    return "========== Invalid selection"
            return "========== No recent files found"

        # Build DeepSeek prompt
        codebase_structure = get_codebase_structure(project_root)
        prompt = get_framework_specific_prompt(
            workspace, codebase_structure, file_description
        )
        print(f"========== Codebase structure: {codebase_structure}")
        print(f"DeepSeek prompt: {prompt}")

        # Get matches from DeepSeek
        response = json_prompt(prompt)
        print(f"DeepSeek response: {response}")

        # Parse matches using provided confidence score
        matches = []
        for match in response.get("results", []):
            matches.append(
                {
                    "file_path": match.get("file", ""),
                    "confidence_score": match.get("confidence_score", 0.8),
                    "file_type": match.get("file_type", "unknown"),
                }
            )

        # Sort by confidence
        matches.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)

        if not matches:
            return "No matching files found"

        # If single high-confidence match, open directly
        if matches[0]["confidence_score"] > 0.9:
            file_path = os.path.join(project_root, matches[0]["file_path"])
            print(f"High confidence match found: {file_path}")  # Debug print
            _cache_recent_file(file_path)
            open_in_editor(file_path)
            return f"Opened file: {file_path}"

        # Multiple matches - show options
        typer.echo("Multiple matches found:")
        for i, match in enumerate(matches[:5]):
            typer.echo(
                f"{i+1}. {match['file_type']} file: {match['file_path']} "
                f"(confidence: {match['confidence_score']:.2f})"
            )

        choice = typer.prompt("Which file to edit? (number)")
        try:
            selected = matches[int(choice) - 1]
            file_path = os.path.join(project_root, selected["file_path"])
            print(f"User selected file: {file_path}")  # Debug print
            _cache_recent_file(file_path)

            print(f"Opening file in editor: {file_path}")
            open_in_editor(file_path)
            return f"Opened {selected['file_type']} file: {file_path}"
        except (ValueError, IndexError):
            return "Invalid selection"
    except Exception as e:
        print(f"DEBUG: Error in edit_file: {e}")
        return "Error: Could not edit file"


# -----------------------------------------------------
# 5) list_notion_tasks
# -----------------------------------------------------
@app.command()
def list_notion_tasks(
    database_id: Optional[str] = typer.Option(
        None, "--database-id", help="Notion database ID to fetch tasks from"
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="Filter by Resolution Details status"
    ),
):
    """Lists all tasks from your Notion database."""
    try:
        tasks = list_tasks(database_id)

        if not tasks:
            typer.echo("No tasks found")
            return

        # Print header if tasks exist
        if tasks:
            typer.echo("\n=== Notion Tasks ===\n")

        for task in tasks:
            # Filter by status if specified
            if status and task.get("status", "").lower() != status.lower():
                continue

            # Main task title and status with color
            task_line = f"📌 {task['title']}\n"
            task_line += f"   └─ Status: {task.get('status', 'No status')}\n"

            # Group metadata in a clean indented block
            metadata = []
            if task.get("severity"):
                metadata.append(f"Severity: {task['severity']}")
            if task.get("type"):
                metadata.append(f"Type: {task['type']}")

            # People information
            people = []
            if task.get("reporter"):
                people.append(f"Reporter: {task['reporter']}")
            if task.get("assigned_to"):
                people.append(f"Assigned To: {task['assigned_to']}")

            # Assigned to
            if task.get("assigned_to"):
                people.append(f"Assigned To: {task['assigned_to']}")

            # Dates and URLs
            extra = []
            if task.get("date_reported"):
                extra.append(f"Reported: {task['date_reported']}")
            if task.get("url"):
                extra.append(f"URL: {task['url']}")

            # Add metadata blocks with proper indentation
            if metadata:
                task_line += f"   ├─ {' | '.join(metadata)}\n"
            if people:
                task_line += f"   ├─ {' | '.join(people)}\n"
            if extra:
                task_line += f"   └─ {' | '.join(extra)}"

            typer.echo(f"{task_line}\n")

    except ValueError as e:
        typer.echo(f"Error: {str(e)}", err=True)
    except Exception as e:
        typer.echo(f"Failed to fetch tasks: {str(e)}", err=True)


# -----------------------------------------------------
# Entry point
# -----------------------------------------------------
def main():
    app()


if __name__ == "__main__":
    main()

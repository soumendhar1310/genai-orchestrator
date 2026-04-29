import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def extract_field(issue_body: str, field_name: str) -> str:
    pattern = rf"###\s+{re.escape(field_name)}\s*\n(.*?)(?=\n###|\Z)"
    match = re.search(pattern, issue_body, re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def parse_issue(issue_body: str) -> dict:
    return {
        "repository_url": extract_field(issue_body, "Repository"),
        "branch": extract_field(issue_body, "Target Branch") or "main",
        "run_sonar": extract_field(issue_body, "Run SonarQube Analysis").lower() == "yes",
        "add_docs": extract_field(issue_body, "Add Inline Documentation").lower() == "yes",
        "notes": extract_field(issue_body, "Additional Notes"),
        "issue_number": os.getenv("ISSUE_NUMBER", ""),
        "issue_url": os.getenv("ISSUE_URL", "")
    }


def run_command(command: str, cwd: str | None = None) -> None:
    print(f"RUN: {command}")
    subprocess.run(command, shell=True, check=True, cwd=cwd)


def clone_repository(config: dict, workspace: Path) -> Path:
    repo_dir = workspace / "target-repo"
    if repo_dir.exists():
        run_command(f"rm -rf {repo_dir}")
    run_command(f"git clone --branch {config['branch']} {config['repository_url']} {repo_dir}")
    return repo_dir


def create_branch(repo_dir: Path, issue_number: str) -> str:
    branch_name = f"agent/issue-{issue_number or 'local-run'}"
    run_command(f"git checkout -b {branch_name}", cwd=str(repo_dir))
    return branch_name


def maybe_run_sonar(repo_dir: Path, config: dict) -> None:
    if not config["run_sonar"]:
        print("Skipping SonarQube by configuration")
        return

    sonar_token = os.getenv("SONAR_TOKEN", "")
    if not sonar_token:
        print("SONAR_TOKEN is not configured. Skipping SonarQube step.")
        return

    command = (
        'export PATH="$PATH:/Users/soumendhar/.dotnet/tools" && '
        'dotnet-sonarscanner begin '
        '/k:"sample-project" '
        '/d:sonar.host.url="http://localhost:9000" '
        f'/d:sonar.token="{sonar_token}" '
        '/d:sonar.cs.opencover.reportsPaths="BankingSystem.Tests/TestResults/coverage.opencover.xml" '
        '&& dotnet build BankingSystem.sln '
        f'&& dotnet-sonarscanner end /d:sonar.token="{sonar_token}"'
    )
    run_command(command, cwd=str(repo_dir))


def run_placeholder_workflow(repo_dir: Path, config: dict) -> None:
    print("Agents.md-driven execution placeholder started")
    print(json.dumps(config, indent=2))
    print("Next implementation step: add repository-specific code analysis, test generation, docs, commit, push, and PR creation.")
    print(f"Repository workspace: {repo_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse-issue")
    parse_parser.add_argument("--issue-body", required=True)
    parse_parser.add_argument("--output-json", required=True)

    run_parser = subparsers.add_parser("run-workflow")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--agents-file", required=True)

    args = parser.parse_args()

    if args.command == "parse-issue":
        parsed = parse_issue(args.issue_body)
        with open(args.output_json, "w", encoding="utf-8") as file:
            json.dump(parsed, file, indent=2)
        print(f"Wrote parsed issue configuration to {args.output_json}")
        return

    if args.command == "run-workflow":
        with open(args.config, "r", encoding="utf-8") as file:
            config = json.load(file)

        workspace = Path.cwd() / "genai-orchestrator-workspace"
        workspace.mkdir(exist_ok=True)

        repo_dir = clone_repository(config, workspace)
        branch_name = create_branch(repo_dir, config.get("issue_number", ""))
        print(f"Created working branch: {branch_name}")

        run_placeholder_workflow(repo_dir, config)
        maybe_run_sonar(repo_dir, config)
        print(f"Workflow completed using contract: {args.agents_file}")


if __name__ == "__main__":
    main()

# Made with Bob

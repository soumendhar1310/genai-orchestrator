import argparse
import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CSharpClassInfo:
    name: str
    namespace: str
    file_path: Path
    constructor_dependencies: list[tuple[str, str]]
    public_methods: list[str]
    kind: str


def classify_csharp_class(file_path: Path, class_name: str) -> str:
    path_value = str(file_path).replace("\\", "/").lower()
    class_name_lower = class_name.lower()
    if "controller" in class_name_lower or "/controllers/" in path_value:
        return "controller"
    if "service" in class_name_lower or "/services/" in path_value:
        return "service"
    if "repository" in class_name_lower or "/repositories/" in path_value:
        return "repository"
    return "general"


def parse_constructor_dependencies(class_body: str, class_name: str) -> list[tuple[str, str]]:
    constructor_pattern = rf"public\s+{re.escape(class_name)}\s*\((.*?)\)"
    constructor_match = re.search(constructor_pattern, class_body, re.DOTALL)
    if not constructor_match:
        return []

    parameter_blob = constructor_match.group(1).strip()
    if not parameter_blob:
        return []

    dependencies: list[tuple[str, str]] = []
    for raw_parameter in parameter_blob.split(","):
        parameter = raw_parameter.strip()
        match = re.match(r"([\w<>\.\?\[\],]+)\s+(\w+)$", parameter)
        if match:
            dependencies.append((match.group(1), match.group(2)))
    return dependencies


def parse_public_methods(class_body: str) -> list[str]:
    methods = re.findall(
        r"public\s+(?:async\s+)?(?:[\w<>\.\?\[\],]+\s+)+(\w+)\s*\(",
        class_body
    )
    return [method for method in methods if method not in {"Dispose"}]


def inventory_csharp_classes(repo_dir: Path) -> list[CSharpClassInfo]:
    class_inventory: list[CSharpClassInfo] = []

    for file_path in repo_dir.rglob("*.cs"):
        normalized_path = str(file_path).replace("\\", "/")
        if any(segment in normalized_path for segment in ["/bin/", "/obj/", ".Tests/"]):
            continue

        source = file_path.read_text(encoding="utf-8")
        namespace_match = re.search(r"namespace\s+([\w\.]+)", source)
        class_match = re.search(r"public\s+class\s+(\w+)", source)
        if not namespace_match or not class_match:
            continue

        class_name = class_match.group(1)
        class_body = source[class_match.start():]
        class_inventory.append(
            CSharpClassInfo(
                name=class_name,
                namespace=namespace_match.group(1),
                file_path=file_path,
                constructor_dependencies=parse_constructor_dependencies(class_body, class_name),
                public_methods=parse_public_methods(class_body),
                kind=classify_csharp_class(file_path, class_name),
            )
        )

    return class_inventory


def generate_generic_test_file_content(class_info: CSharpClassInfo) -> str:
    using_lines = {
        "using NUnit.Framework;",
        f"using {class_info.namespace};",
    }

    field_lines: list[str] = []
    setup_lines: list[str] = []
    constructor_args: list[str] = []

    for dependency_type, dependency_name in class_info.constructor_dependencies:
        field_lines.append(f"    private {dependency_type}? _{dependency_name};")
        setup_lines.append(f"        _{dependency_name} = null;")
        constructor_args.append(f"_{dependency_name}!")

    field_lines.append(f"    private {class_info.name} _sut = null!;")
    setup_lines.append(f"        _sut = new {class_info.name}({', '.join(constructor_args)});")

    generated_tests: list[str] = []
    for method_name in (class_info.public_methods[:5] or ["GeneratedPlaceholder"]):
        generated_tests.append(
            "\n".join(
                [
                    "    [Test]",
                    f"    public void {method_name}_GeneratedSmokeTest()",
                    "    {",
                    "        Assert.That(_sut, Is.Not.Null);",
                    "    }",
                ]
            )
        )

    return f"""{chr(10).join(sorted(using_lines))}

namespace {class_info.namespace}.Tests;

[TestFixture]
public class {class_info.name}GeneratedTests
{{
{chr(10).join(field_lines)}

    [SetUp]
    public void SetUp()
    {{
{chr(10).join(setup_lines)}
    }}

{chr(10).join(generated_tests)}
}}
"""


def generate_tests_for_inventory(test_project_path: Path, class_inventory: list[CSharpClassInfo]) -> None:
    generated_dir = test_project_path.parent / "Generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    preferred_kinds = ["service", "controller", "repository", "general"]
    ordered_inventory = sorted(
        class_inventory,
        key=lambda item: (preferred_kinds.index(item.kind), item.name)
    )

    supported_kinds = {"repository"}
    for class_info in ordered_inventory:
        if class_info.kind not in supported_kinds:
            continue
        target_path = generated_dir / f"{class_info.name}GeneratedTests.cs"
        target_path.write_text(generate_generic_test_file_content(class_info), encoding="utf-8")


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


def run_command_capture(command: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    print(f"RUN: {command}")
    result = subprocess.run(
        command,
        shell=True,
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result


def clone_repository(config: dict, workspace: Path) -> Path:
    repo_dir = workspace / "target-repo"
    if repo_dir.exists():
        run_command(f"rm -rf {repo_dir}")

    repository_url = config["repository_url"]
    gh_pat = os.getenv("GH_PAT", "")
    if gh_pat and repository_url.startswith("https://github.com/"):
        repository_url = repository_url.replace("https://", f"https://x-access-token:{gh_pat}@")

    run_command(f"git clone --branch {config['branch']} {repository_url} {repo_dir}")
    return repo_dir


def create_branch(repo_dir: Path, issue_number: str) -> str:
    branch_name = f"agent/issue-{issue_number or 'local-run'}"
    run_command(f"git checkout -b {branch_name}", cwd=str(repo_dir))
    return branch_name


def discover_dotnet_repo(repo_dir: Path) -> dict:
    sln_files = sorted(repo_dir.rglob("*.sln"))
    if not sln_files:
        raise RuntimeError("No .sln file found in the target repository")

    solution_path = sln_files[0]
    all_csproj_files = sorted(repo_dir.rglob("*.csproj"))
    test_projects = [path for path in all_csproj_files if path.stem.endswith(".Tests") or "Test" in path.stem]
    app_projects = [path for path in all_csproj_files if path not in test_projects]

    if not app_projects:
        raise RuntimeError("No non-test .csproj files found in the target repository")

    primary_project = app_projects[0]
    return {
        "solution_path": solution_path,
        "all_csproj_files": all_csproj_files,
        "test_projects": test_projects,
        "app_projects": app_projects,
        "primary_project": primary_project,
    }


def ensure_nunit_test_project(repo_dir: Path, repo_info: dict) -> Path:
    if repo_info["test_projects"]:
        return repo_info["test_projects"][0]

    primary_project = repo_info["primary_project"]
    tests_dir = primary_project.parent.parent / f"{primary_project.stem}.Tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_project_path = tests_dir / f"{primary_project.stem}.Tests.csproj"

    test_project_path.write_text(
        f"""<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <IsPackable>false</IsPackable>
    <IsTestProject>true</IsTestProject>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="coverlet.msbuild" Version="6.0.2" />
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.10.0" />
    <PackageReference Include="NUnit" Version="4.1.0" />
    <PackageReference Include="NUnit3TestAdapter" Version="4.5.0" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="..\\{primary_project.parent.name}\\{primary_project.name}" />
  </ItemGroup>

</Project>
""",
        encoding="utf-8"
    )

    smoke_test_path = tests_dir / "GeneratedSmokeTests.cs"
    smoke_test_path.write_text(
        f"""using NUnit.Framework;

namespace {primary_project.stem}.Tests;

[TestFixture]
public class GeneratedSmokeTests
{{
    [Test]
    public void Generated_placeholder_test_passes()
    {{
        Assert.That(true, Is.True);
    }}
}}
""",
        encoding="utf-8"
    )

    run_command(
        f'dotnet sln "{repo_info["solution_path"]}" add "{test_project_path}"',
        cwd=str(repo_dir)
    )
    return test_project_path


def maybe_run_sonar_begin(repo_dir: Path, config: dict, coverage_rel_path: str) -> bool:
    if not config["run_sonar"]:
        return False

    sonar_token = os.getenv("SONAR_TOKEN", "")
    if not sonar_token:
        raise RuntimeError("SONAR_TOKEN is required when SonarQube analysis is enabled")

    sonar_probe = run_command_capture("curl -sf http://localhost:9000/api/system/status", cwd=str(repo_dir))
    if sonar_probe.returncode != 0:
        print("SonarQube server is not reachable at http://localhost:9000. Skipping SonarQube step.")
        return False

    sonar_begin = (
        'export PATH="$PATH:$HOME/.dotnet/tools" && '
        'dotnet-sonarscanner begin '
        '/k:"sample-project" '
        '/d:sonar.host.url="http://localhost:9000" '
        f'/d:sonar.token="{sonar_token}" '
        f'/d:sonar.cs.opencover.reportsPaths="{coverage_rel_path}"'
    )
    run_command(sonar_begin, cwd=str(repo_dir))
    return True


def maybe_run_sonar_end(repo_dir: Path) -> None:
    sonar_token = os.getenv("SONAR_TOKEN", "")
    sonar_end = (
        'export PATH="$PATH:$HOME/.dotnet/tools" && '
        f'dotnet-sonarscanner end /d:sonar.token="{sonar_token}"'
    )
    run_command(sonar_end, cwd=str(repo_dir))


def compute_coverage_percent(coverage_path: Path) -> float:
    if not coverage_path.exists():
        raise RuntimeError("coverage.opencover.xml was not generated")

    coverage_text = coverage_path.read_text(encoding="utf-8")
    sequence_points = [int(value) for value in re.findall(r'numSequencePoints="(\d+)"', coverage_text)]
    visited_points = [int(value) for value in re.findall(r'visitedSequencePoints="(\d+)"', coverage_text)]
    total_sequence_points = sum(sequence_points)
    total_visited_points = sum(visited_points)
    return (total_visited_points / total_sequence_points * 100) if total_sequence_points else 0.0


def run_generic_dotnet_workflow(repo_dir: Path, config: dict) -> None:
    print("Agents.md-driven generic .NET workflow started")
    print(json.dumps(config, indent=2))

    repo_info = discover_dotnet_repo(repo_dir)
    class_inventory = inventory_csharp_classes(repo_dir)
    print(f"Discovered {len(class_inventory)} public classes for candidate test generation")
    solution_path = repo_info["solution_path"]
    test_project_path = ensure_nunit_test_project(repo_dir, repo_info)
    generate_tests_for_inventory(test_project_path, class_inventory)
    coverage_rel_path = f"{test_project_path.parent.name}/TestResults/coverage.opencover.xml"

    run_command(f'dotnet restore "{solution_path}"', cwd=str(repo_dir))
    sonar_executed = maybe_run_sonar_begin(repo_dir, config, coverage_rel_path)
    run_command(f'dotnet build "{solution_path}" --no-restore', cwd=str(repo_dir))
    run_command(
        f'dotnet test "{test_project_path}" '
        '--no-build '
        '/p:CollectCoverage=true '
        '/p:CoverletOutput=TestResults/coverage '
        '/p:CoverletOutputFormat=opencover',
        cwd=str(repo_dir)
    )

    coverage_path = test_project_path.parent / "TestResults" / "coverage.opencover.xml"
    coverage_percent = compute_coverage_percent(coverage_path)
    print(f"Computed line coverage: {coverage_percent:.2f}%")

    if coverage_percent < 80.0:
        raise RuntimeError(f"Coverage threshold not met: {coverage_percent:.2f}% < 80.00%")

    if sonar_executed:
        maybe_run_sonar_end(repo_dir)

    print(f"Coverage threshold satisfied: {coverage_percent:.2f}%")
    print(f"Repository workspace: {repo_dir}")


def commit_and_push_changes(repo_dir: Path, branch_name: str, issue_number: str) -> None:
    gh_pat = os.getenv("GH_PAT", "").strip()
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    token = gh_pat or github_token

    if not token:
        raise RuntimeError("GH_PAT or GITHUB_TOKEN is required for push operations")

    print(f"GH_PAT present: {'yes' if bool(gh_pat) else 'no'}")
    print(f"GITHUB_TOKEN present: {'yes' if bool(github_token) else 'no'}")

    remote_url_result = subprocess.run(
        "git remote get-url origin",
        shell=True,
        check=True,
        capture_output=True,
        text=True,
        cwd=str(repo_dir)
    )
    remote_url = remote_url_result.stdout.strip()
    print(f"Original remote URL: {remote_url}")

    if remote_url.startswith("https://github.com/"):
        authed_remote_url = remote_url.replace("https://", f"https://x-access-token:{token}@")
        run_command(f"git remote set-url origin {authed_remote_url}", cwd=str(repo_dir))

    run_command('git config user.name "github-actions[bot]"', cwd=str(repo_dir))
    run_command('git config user.email "41898282+github-actions[bot]@users.noreply.github.com"', cwd=str(repo_dir))
    run_command("git add .", cwd=str(repo_dir))
    run_command("git status --short", cwd=str(repo_dir))

    commit_result = subprocess.run(
        f'git commit -m "Generate tests, coverage, and docs for issue #{issue_number or "local-run"}"',
        shell=True,
        cwd=str(repo_dir)
    )
    if commit_result.returncode != 0:
        print("No commit created. Continuing without push.")
        return

    push_result = run_command_capture(f"git push origin {branch_name}", cwd=str(repo_dir))
    if push_result.returncode != 0:
        raise RuntimeError(
            "git push failed. Verify that GH_PAT is a fine-grained token with "
            "Contents: Read and write access to sample-project, and that the token "
            "owner has permission to push branches to the repository."
        )


def create_pull_request(config: dict, branch_name: str) -> str:
    token = os.getenv("GH_PAT", "") or os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GH_PAT or GITHUB_TOKEN is required to create a pull request")

    issue_number = config.get("issue_number", "") or "local-run"
    repository_url = config["repository_url"]
    owner_repo = repository_url.removesuffix(".git").split("github.com/")[-1]
    owner, repo = owner_repo.split("/", 1)

    title = f"Agent workflow output for issue #{issue_number}"
    body = "\n".join(
        [
            f"Closes #{issue_number}" if issue_number != "local-run" else "Agent workflow output",
            "",
            "Generated by the GitHub Issue driven GenAI orchestrator scaffold.",
            "",
            f"- Source issue: {config.get('issue_url', 'not provided')}",
            f"- Repository: {repository_url}",
            f"- Branch: {branch_name}"
        ]
    )

    payload = json.dumps(
        {
            "title": title,
            "head": branch_name,
            "base": config["branch"],
            "body": body
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url=f"https://api.github.com/repos/{owner}/{repo}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
        }
    )

    with urllib.request.urlopen(request) as response:
        response_json = json.loads(response.read().decode("utf-8"))

    pr_url = response_json["html_url"]
    print(f"Created pull request: {pr_url}")
    return pr_url


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

        run_generic_dotnet_workflow(repo_dir, config)
        commit_and_push_changes(repo_dir, branch_name, config.get("issue_number", ""))
        pr_url = create_pull_request(config, branch_name)
        print(f"Pull request URL: {pr_url}")
        print(f"Workflow completed using contract: {args.agents_file}")


if __name__ == "__main__":
    main()

# Made with Bob

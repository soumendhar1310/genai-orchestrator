import argparse
import importlib
import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CSharpClassInfo:
    name: str
    namespace: str
    file_path: Path
    constructor_dependencies: list[tuple[str, str]]
    public_methods: list[str]
    kind: str
    source: str


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
                source=source,
            )
        )

    return class_inventory


def sanitize_dependency_type(dependency_type: str) -> str:
    return dependency_type.replace("?", "").strip()


def normalize_nunit4_assertions(test_code: str) -> str:
    normalized = test_code

    normalized = re.sub(
        r"Assert\.AreEqual\((.+?),\s*(.+?)\);",
        r"Assert.That(\2, Is.EqualTo(\1));",
        normalized
    )
    normalized = re.sub(
        r"Assert\.IsNotNull\((.+?)\);",
        r"Assert.That(\1, Is.Not.Null);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.NotNull\((.+?)\);",
        r"Assert.That(\1, Is.Not.Null);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.IsNull\((.+?)\);",
        r"Assert.That(\1, Is.Null);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.IsTrue\((.+?)\);",
        r"Assert.That(\1, Is.True);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.True\((.+?)\);",
        r"Assert.That(\1, Is.True);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.IsFalse\((.+?)\);",
        r"Assert.That(\1, Is.False);",
        normalized
    )
    normalized = re.sub(
        r"Assert\.False\((.+?)\);",
        r"Assert.That(\1, Is.False);",
        normalized
    )
    normalized = re.sub(
        r"CollectionAssert\.AreEquivalent\((.+?),\s*(.+?)\);",
        r"Assert.That(\2, Is.EquivalentTo(\1));",
        normalized
    )
    normalized = re.sub(
        r"CollectionAssert\.Contains\((.+?),\s*(.+?)\);",
        r"Assert.That(\2, Does.Contain(\1));",
        normalized
    )

    normalized = normalized.replace("Assert.That(value, Is.Not.Null);", "Assert.That(value, Is.Not.Null);")
    return normalized


def is_supported_heuristic_kind(class_info: CSharpClassInfo, attempt: int) -> bool:
    if attempt == 1:
        return class_info.kind == "repository"
    if attempt == 2:
        return class_info.kind in {"repository", "controller"}
    return class_info.kind in {"repository", "controller", "service", "general"}


def build_heuristic_usings(class_info: CSharpClassInfo) -> list[str]:
    using_lines = {
        "using NUnit.Framework;",
        f"using {class_info.namespace};",
    }

    namespace_root = ".".join(class_info.namespace.split(".")[:2]) if "." in class_info.namespace else class_info.namespace
    namespace_candidates = {
        namespace_root,
        f"{namespace_root}.Abstractions",
        f"{namespace_root}.Interfaces",
        f"{namespace_root}.Contracts",
        f"{namespace_root}.Services",
        f"{namespace_root}.Repositories",
        f"{namespace_root}.Controllers",
        "Microsoft.Extensions.Logging",
    }

    for dependency_type, _ in class_info.constructor_dependencies:
        clean_type = sanitize_dependency_type(dependency_type)
        if "ILogger<" in clean_type:
            using_lines.add("using Microsoft.Extensions.Logging;")
        if clean_type.startswith("I"):
            using_lines.update({f"using {candidate};" for candidate in namespace_candidates})

    return sorted(using_lines)


def build_constructor_default_expression(dependency_type: str) -> str:
    clean_type = sanitize_dependency_type(dependency_type)
    if clean_type in {"string"}:
        return 'string.Empty'
    if clean_type in {"int", "long", "short", "byte", "double", "decimal", "float"}:
        return "0"
    if clean_type == "bool":
        return "false"
    if clean_type.startswith("ILogger<"):
        return "null!"
    if clean_type.endswith("[]"):
        return f"System.Array.Empty<{clean_type[:-2]}>()"
    return "null!"


def generate_heuristic_test_file_content(class_info: CSharpClassInfo) -> str:
    using_lines = build_heuristic_usings(class_info)
    field_lines: list[str] = []
    setup_lines: list[str] = []
    constructor_args: list[str] = []

    for dependency_type, dependency_name in class_info.constructor_dependencies:
        clean_type = sanitize_dependency_type(dependency_type)
        field_lines.append(f"    private {clean_type} _{dependency_name} = null!;")
        setup_lines.append(f"        _{dependency_name} = {build_constructor_default_expression(clean_type)};")
        constructor_args.append(f"_{dependency_name}")

    if constructor_args:
        setup_lines.append(f"        _sut = new {class_info.name}({', '.join(constructor_args)});")
    else:
        setup_lines.append(f"        _sut = new {class_info.name}();")

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

    return f"""{chr(10).join(using_lines)}

namespace {class_info.namespace}.Tests;

[TestFixture]
public class {class_info.name}GeneratedTests
{{
{chr(10).join(field_lines)}
    private {class_info.name} _sut = null!;

    [SetUp]
    public void SetUp()
    {{
{chr(10).join(setup_lines)}
    }}

{chr(10).join(generated_tests)}
}}
"""


def get_openai_client() -> Any | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        openai_module = importlib.import_module("openai")
        openai_factory = getattr(openai_module, "OpenAI", None)
        if openai_factory is None:
            print("OpenAI SDK is installed but OpenAI client was not found. Falling back to heuristic test generation.")
            return None
        return openai_factory(api_key=api_key)
    except ImportError:
        print("OpenAI SDK is not installed. Falling back to heuristic test generation.")
        return None


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```[a-zA-Z0-9]*\n", "", stripped)
    stripped = re.sub(r"\n```$", "", stripped)
    return stripped.strip()


def is_safe_generated_test_content(class_info: CSharpClassInfo, generated_content: str) -> bool:
    blocked_patterns = [
        r"\bclass\s+Dummy\w*",
        r"\brecord\s+Dummy\w*",
        r":\s*I\w+",
        r"\bAssert\.AreEqual\(",
        r"\bAssert\.IsNull\(",
        r"\bAssert\.IsNotNull\(",
        r"\bAssert\.IsTrue\(",
        r"\bAssert\.IsFalse\(",
        r"\bCollectionAssert\.",
    ]

    if class_info.kind in {"controller", "service"}:
        blocked_patterns.extend(
            [
                r"\bMock<",
                r"\bnew\s+Mock<",
                r"\bDummy\w*Service\b",
                r"\bDummy\w*Repository\b",
            ]
        )

    for pattern in blocked_patterns:
        if re.search(pattern, generated_content):
            print(f"Rejected OpenAI-generated test for {class_info.name} due to blocked pattern: {pattern}")
            return False

    return True


def generate_openai_test_file_content(class_info: CSharpClassInfo) -> str | None:
    client = get_openai_client()
    if client is None:
        return None

    dependency_summary = ", ".join(
        f"{dependency_type} {dependency_name}"
        for dependency_type, dependency_name in class_info.constructor_dependencies
    ) or "none"

    method_summary = ", ".join(class_info.public_methods[:10]) or "none"

    prompt = f"""
Generate a compilable C# NUnit 4 test file for the class below.

Requirements:
- Return only raw C# code, no markdown fences.
- Use NUnit 4 syntax only.
- Never use legacy assertion APIs such as Assert.AreEqual, Assert.IsNull, Assert.IsNotNull, Assert.IsTrue, Assert.IsFalse, or CollectionAssert.
- Use Assert.That(...) with Is.EqualTo(...), Is.Null, Is.Not.Null, Is.True, Is.False, Is.EquivalentTo(...), Does.Contain(...).
- Prefer simple deterministic tests.
- Include all necessary using statements.
- Namespace should be {class_info.namespace}.Tests
- Test class name should be {class_info.name}GeneratedTests
- Never invent enum members, property names, methods, interfaces, return types, DTO fields, or helper classes not present in source.
- Never create dummy implementations such as DummyService, DummyRepository, FakeService, FakeRepository, or any class implementing an interface manually.
- Never implement interfaces manually unless the exact full interface definition is provided in the prompt.
- Do not use Moq.
- If constructor dependencies are difficult to instantiate, use null-forgiving constructor arguments safely and generate only minimal smoke tests.
- If behavior is uncertain, generate the smallest compiling test class possible.
- Ensure code compiles against the source exactly as provided.

Class kind: {class_info.kind}
Class namespace: {class_info.namespace}
Class name: {class_info.name}
Constructor dependencies: {dependency_summary}
Public methods: {method_summary}

Source:
{class_info.source}
""".strip()

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt
        )
        output_text = getattr(response, "output_text", "") or ""
        cleaned = strip_code_fences(output_text)
        if not cleaned:
            return None
        cleaned = normalize_nunit4_assertions(cleaned)
        if not is_safe_generated_test_content(class_info, cleaned):
            return None
        return cleaned
    except Exception as exc:
        print(f"OpenAI generation failed for {class_info.name}: {exc}")
        return None


def generate_tests_for_inventory(test_project_path: Path, class_inventory: list[CSharpClassInfo], attempt: int) -> None:
    generated_dir = test_project_path.parent / "Generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    for existing_file in generated_dir.glob("*.cs"):
        existing_file.unlink()

    preferred_order = ["repository", "controller", "service", "general"]
    ordered_inventory = sorted(
        class_inventory,
        key=lambda item: (preferred_order.index(item.kind), item.name)
    )

    selected_classes = [item for item in ordered_inventory if is_supported_heuristic_kind(item, attempt)]
    print(f"Attempt {attempt}: generating tests for {len(selected_classes)} classes across supported heuristic kinds")

    openai_enabled = bool(get_openai_client())
    allow_openai_kinds = {"repository"} if attempt == 1 else {"repository", "controller"} if attempt == 2 else {"repository", "controller", "service"}
    print(f"OpenAI generation enabled: {'yes' if openai_enabled else 'no'}")

    for class_info in selected_classes:
        target_path = generated_dir / f"{class_info.name}GeneratedTests.cs"
        generated_content = None

        if openai_enabled and class_info.kind in allow_openai_kinds:
            generated_content = generate_openai_test_file_content(class_info)

        if not generated_content:
            generated_content = generate_heuristic_test_file_content(class_info)

        generated_content = normalize_nunit4_assertions(generated_content)
        target_path.write_text(generated_content, encoding="utf-8")


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
    <PackageReference Include="Microsoft.Extensions.Logging.Abstractions" Version="8.0.2" />
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
    coverage_rel_path = f"{test_project_path.parent.name}/TestResults/coverage.opencover.xml"

    run_command(f'dotnet restore "{solution_path}"', cwd=str(repo_dir))
    sonar_executed = maybe_run_sonar_begin(repo_dir, config, coverage_rel_path)

    last_coverage_percent = 0.0
    for attempt in range(1, 4):
        print(f"Starting generation/coverage attempt {attempt} of 3")
        generate_tests_for_inventory(test_project_path, class_inventory, attempt)

        build_result = run_command_capture(f'dotnet build "{solution_path}" --no-restore', cwd=str(repo_dir))
        if build_result.returncode != 0:
            print(f"Build failed on attempt {attempt}")
            if attempt == 3:
                raise RuntimeError("Build failed after 3 generation attempts")
            continue

        test_result = run_command_capture(
            f'dotnet test "{test_project_path}" '
            '--no-build '
            '/p:CollectCoverage=true '
            '/p:CoverletOutput=TestResults/coverage '
            '/p:CoverletOutputFormat=opencover',
            cwd=str(repo_dir)
        )
        if test_result.returncode != 0:
            print(f"Test execution failed on attempt {attempt}")
            if attempt == 3:
                raise RuntimeError("Test execution failed after 3 generation attempts")
            continue

        coverage_path = test_project_path.parent / "TestResults" / "coverage.opencover.xml"
        last_coverage_percent = compute_coverage_percent(coverage_path)
        print(f"Computed line coverage after attempt {attempt}: {last_coverage_percent:.2f}%")

        if last_coverage_percent >= 80.0:
            if sonar_executed:
                maybe_run_sonar_end(repo_dir)
            print(f"Coverage threshold satisfied: {last_coverage_percent:.2f}%")
            print(f"Repository workspace: {repo_dir}")
            return

        print(f"Coverage below threshold after attempt {attempt}: {last_coverage_percent:.2f}% < 80.00%")

    raise RuntimeError(f"Coverage threshold not met after 3 attempts: {last_coverage_percent:.2f}% < 80.00%")


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

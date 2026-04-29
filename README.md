# genai-orchestrator

This folder contains a starter scaffold for a GitHub Issue driven GenAI workflow.

## Included files

- `.github/ISSUE_TEMPLATE/agent-run.yml`
- `.github/workflows/issue-trigger.yml`
- `Agents.md`
- `runner.py`

## What this setup does

1. Shows a GitHub Issue form with a repository selector
2. Triggers a GitHub Action when the issue is opened
3. Parses the issue body into runtime inputs
4. Calls a Python runner
5. Uses `Agents.md` as the workflow contract
6. Prepares for branch creation and Pull Request based approval

## Current repository option in the issue form

- `https://github.com/soumendhar1310/sample-project.git`

## How to use this on GitHub

### 1. Create a new GitHub repository
Create a repository named:

- `genai-orchestrator`

### 2. Copy this folder content into that repository
The folder structure should exist at the root of your GitHub repository.

Important:
- Move the contents of `genai-orchestrator/.github/...` into the real repository `.github/...` location when you publish it.
- GitHub only recognizes issue forms and workflows from the repository root `.github` folder.

Expected final repo layout on GitHub:
- `.github/ISSUE_TEMPLATE/agent-run.yml`
- `.github/workflows/issue-trigger.yml`
- `Agents.md`
- `runner.py`
- `README.md`

### 3. Enable GitHub Issues
In your repository settings:
- enable Issues

### 4. Enable GitHub Actions
In your repository settings:
- allow GitHub Actions to run

### 5. Add repository secrets
In GitHub repository settings -> Secrets and variables -> Actions, add:

- `SONAR_TOKEN`

If you later want cross-repo push and PR creation beyond the same repository token, also add:
- `GH_PAT`

Recommended scopes for a fine-grained token:
- contents: read/write
- pull requests: read/write
- issues: read/write
- metadata: read

### 6. Open a new issue
Go to:
- Issues -> New issue

Choose:
- `Agent Workflow Request`

Fill in:
- Repository
- Target Branch
- Run SonarQube Analysis
- Add Inline Documentation
- Additional Notes

### 7. Submit the issue
When the issue is opened:
- GitHub Actions triggers `issue-trigger.yml`
- the issue body is parsed
- the Python runner is invoked

### 8. Monitor the workflow
Go to:
- Actions tab

Open the latest workflow run and inspect:
- parse step
- runner step
- any failure details

### 9. Review issue comments
The workflow is configured to comment success or failure back to the issue.

## Important current limitation

This is a scaffold, not the final autonomous implementation.

Right now `runner.py` does:
- parse issue form content
- clone the selected repository
- create a working branch
- optionally run SonarQube

It still needs to be expanded to:
- analyze codebase
- generate NUnit tests
- run coverage
- add inline documentation
- commit changes
- push branch
- create Pull Request

## Recommended next enhancements

### Add PR creation
Use either:
- GitHub CLI: `gh pr create`
- GitHub REST API
- `peter-evans/create-pull-request`

### Add commit and push
In `runner.py`, implement:
- `git add .`
- `git commit -m "Generated tests, coverage, and docs for issue #<n>"`
- `git push origin <branch>`

### Add actual AI execution
Extend `run_placeholder_workflow()` to:
- inspect source files
- call your model/provider
- write generated files
- run `dotnet test`
- run Coverlet
- retry if coverage < 80%

### Make repository selection broader
GitHub Issue Forms dropdown values are static.
For now, `sample-project` is hardcoded as requested.
Later you can:
- add more repos manually
- switch to a free-text repository field
- build a custom UI outside GitHub for dynamic repo discovery

## Local testing

### Parse issue content locally
Example:
```bash
python genai-orchestrator/runner.py parse-issue \
  --issue-body "$(cat sample_issue_body.txt)" \
  --output-json parsed_issue.json
```

### Run workflow locally
Example:
```bash
export SONAR_TOKEN=your_token_here
export ISSUE_NUMBER=123
export ISSUE_URL=https://github.com/your-org/genai-orchestrator/issues/123

python genai-orchestrator/runner.py run-workflow \
  --config parsed_issue.json \
  --agents-file genai-orchestrator/Agents.md
```

## Most important GitHub detail

GitHub will not detect issue forms or workflows if they remain inside a nested folder.

So after reviewing this scaffold, publish these files at repository root layout:
- `.github/...`
- `Agents.md`
- `runner.py`
- `README.md`

That is required for successful GitHub execution.
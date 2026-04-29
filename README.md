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
- `OPENAI_API_KEY`

If you later want cross-repo push and PR creation beyond the same repository token, also add:
- `GH_PAT`

Recommended scopes for a fine-grained token:
- contents: read/write
- pull requests: read/write
- issues: read/write
- metadata: read

### 5a. How to create `GH_PAT` on GitHub
Use a fine-grained personal access token.

Steps:
1. In GitHub, go to:
   - Profile picture
   - `Settings`
   - `Developer settings`
   - `Personal access tokens`
   - `Fine-grained tokens`
   - `Generate new token`

2. Give the token a name, for example:
   - `genai-orchestrator-cross-repo`

3. Choose an expiration such as:
   - 30 days
   - 90 days

4. Under **Resource owner**, select:
   - `soumendhar1310`

5. Under **Repository access**, choose:
   - `Only select repositories`

6. Select the target repository:
   - `sample-project`

7. Set repository permissions:
   - `Contents` -> `Read and write`
   - `Pull requests` -> `Read and write`
   - `Metadata` -> `Read-only`

8. Click **Generate token**

9. Copy the token immediately because GitHub will not show it again.

10. Add it to the `genai-orchestrator` repository as an Actions secret:
    - Go to `genai-orchestrator`
    - `Settings`
    - `Secrets and variables`
    - `Actions`
    - `New repository secret`

11. Create the secret:
    - Name: `GH_PAT`
    - Value: paste the generated token

Important:
- The token must have access to `sample-project`, because that is the repository the workflow will push to and where it will create Pull Requests.
- If `GH_PAT` is missing, branch push and PR creation will fail.

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

It now supports:
- analyze .NET solution and project structure
- create or reuse an NUnit test project
- generate NUnit tests using OpenAI when `OPENAI_API_KEY` is available
- fall back to heuristic generation when OpenAI is unavailable
- run `dotnet build`
- run Coverlet in OpenCover format
- compute overall coverage
- retry generation and coverage up to 3 times
- optionally run SonarQube when reachable
- commit changes
- push branch
- create Pull Request

It still needs improvement in:
- richer generic test quality for arbitrary repositories
- stronger inline documentation generation
- better repository-specific namespace/interface resolution

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

### OpenAI-based test generation
The runner now supports OpenAI-driven test generation.

How it works:
- if `OPENAI_API_KEY` is present, `runner.py` attempts OpenAI-based NUnit test generation first
- if OpenAI generation fails or no key is present, it falls back to heuristic generation
- the workflow installs the `openai` Python package during GitHub Actions execution

Optional model override:
- set `OPENAI_MODEL` as an Actions variable or environment variable
- default model used by the runner:
  - `gpt-4.1-mini`

Recommended additional secret/variable setup:
- Secret:
  - `OPENAI_API_KEY`
- Optional variable:
  - `OPENAI_MODEL`

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
export OPENAI_API_KEY=your_openai_key_here
export OPENAI_MODEL=gpt-4.1-mini
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
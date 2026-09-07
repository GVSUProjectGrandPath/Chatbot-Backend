---
description: Stage changes, write a Conventional Commit message, and commit
argument-hint: [optional scope or note]
allowed-tools: Bash, Read, Grep
---

Create a git commit for the current changes.

Steps:
1. Run `git status` and `git diff` (and `git diff --staged`) to see what changed.
2. Stage the relevant files (respect anything that should not be committed — secrets, `.env`, build artifacts).
3. Write a message in Conventional Commits format: `<type>(<scope>): <imperative summary>` with a body explaining *why* if the change is non-trivial. Follow the `commit-conventions` skill. Incorporate $ARGUMENTS if provided.
4. Commit. Show the final message and result.

Do not push unless asked. Do not commit if there are unresolved merge conflicts or failing pre-commit hooks — report instead.
Do not include any LLM name in Commit message

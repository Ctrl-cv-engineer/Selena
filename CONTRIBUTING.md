[简体中文](./CONTRIBUTING.zh-CN.md)

# Contributing to Selena

Thanks for considering a contribution.

Selena is still evolving, so the most useful contribution is not always a big feature. A clear bug report, a missing example, a cleaner module boundary, or a sharper piece of documentation can all move the project forward.

## Before you start

- Check whether the issue or idea already exists.
- If the change is large, open an issue or discussion first so the direction is aligned.
- If your change affects behavior, config, prompts, or docs, update the related documentation in the same PR.

## Local development

### Backend

```bash
cp config.example.json config.json
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d
python -m DialogueSystem.main
```

### Frontend

```bash
cd DialogueSystem/frontend
pnpm install
pnpm dev
```

## Good issue reports usually include

- What you expected to happen
- What actually happened
- Whether the problem is reproducible
- Minimal reproduction steps
- Logs, screenshots, or config snippets when relevant

Please remove API keys, tokens, local paths, and other sensitive values before posting configuration fragments.

## Suggested checks before opening a PR

The repository does not have full CI coverage yet, so a quick manual pass helps a lot:

- Make sure the backend still starts.
- If you touched the frontend, run `pnpm check` and `pnpm build`.
- If you changed prompts, skills, or configuration behavior, update the docs.
- In the PR description, explain what changed, why it changed, and how you verified it.

## PR style

- Prefer one main concern per PR.
- Small, reviewable changes are much easier to merge than huge mixed refactors.
- Do not commit real keys, local databases, runtime logs, or private conversation history.
- If something is still experimental, say so directly.

## Especially welcome contribution areas

- Documentation polish and examples
- Smoke tests, automated tests, and CI
- Further decomposition of `DialogueSystem/main.py`
- Frontend observability and debugging UX
- Skill system, MCP, and browser-agent improvements

## Collaboration norms

By contributing, you agree to follow [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).

If you are unsure whether a change is worth doing, opening a discussion first is completely fine.

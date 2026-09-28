# Security policy

## What this project does with secrets

All API keys and per-user configuration live in `config/api_keys.json`. That file is in `.gitignore` from day one — **never commit it, never paste it into an issue, never include it in a screenshot.**

The first-run setup dialog writes it; everything else in the codebase only reads it.

## If you accidentally commit a secret

1. **Rotate the key at your provider immediately.** Assume it's compromised the moment it touched GitHub, even if you delete it within seconds — GitHub forks and the Actions cache retain it.
2. Force-push a cleaned history (`git filter-repo` is the cleanest tool, not `git filter-branch`) to scrub it from the past.
3. Open an issue here if you want a reminder note added to the README.

## What to report via this channel

- Anything that lets a remote attacker read `config/api_keys.json` or the long-term memory file (`memory/long_term.json`).
- Anything that lets a remote attacker execute code via the LLM tool pipeline (prompt-injection-via-tool-output is the realistic one — e.g. a webpage tells the agent to run a destructive command).
- Dependency confusion / supply-chain issues with `requirements.txt`.

Open a **private** security advisory on GitHub (Security → Advisories → New draft security advisory) rather than a public issue. If GitHub isn't usable, email the address listed in the repo's commit history — there's always a way in.

## What is *not* a security issue

- "JARVIS did what I asked it to" — the project ships a voice assistant. If you didn't intend a command, that's a UX problem, not a security boundary.
- Crashes in the LLM client when a third-party endpoint returns garbage. Those are bugs; report them in issues, not security.
- The free community endpoints (Empero etc.) going down or being rate-limited. Not our servers.

## Threat model summary

| Asset | Where it lives | Protected by |
|---|---|---|
| API keys | `config/api_keys.json` | `.gitignore` + local file permissions |
| Long-term memory | `memory/long_term.json` | `.gitignore` + local file permissions |
| Microphone audio | RAM only | Never written to disk by default |
| Screen captures | RAM only | Never written to disk by default |
| Tool execution | subprocess / pyautogui | Runs with the user's own permissions — this assistant is a power tool, not a sandbox |

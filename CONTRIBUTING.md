# Contributing to MARK XLVIII

Thanks for your interest in making JARVIS better. This project is a personal-assistant framework with a voice + vision + tool-use loop on top of any OpenAI-compatible LLM.

## Ground rules

- **Be respectful.** This is a voice assistant; a lot of the work is micro-interactions and edge cases that are hard to argue about from a distance. State your use case, show the bug, propose the fix.
- **Keep the brain provider-agnostic.** Don't hardcode a specific model name or endpoint in shared code paths. If you need a new provider, add a preset in `core/llm_client.py:PRESETS` rather than special-casing.
- **No secrets in the repo.** Tests, fixtures, examples, screenshots, recordings — nothing with an API key, a real conversation transcript with personal info, or a long-term memory dump. CI scans for this.

## Development setup

```bash
git clone https://github.com/jarvis-community/mark-xlviii-anyllm.git
cd mark-xlviii-anyllm
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/api_keys.example.json config/api_keys.json
# edit config/api_keys.json with your endpoint + model + (optional) key
python main.py
```

The free [Empero community endpoint](https://free.empero.org/v1) is the easiest way to get up and running with no key required. See the README "Choose Your Brain" section for the full list of supported providers.

## Pull requests

1. **One concern per PR.** A bug fix and an unrelated refactor in the same PR slows review.
2. **Write a clear title and description.** What changed, why, what was tested. Link the issue if there is one.
3. **Include before/after behaviour** for any user-visible change — a short recording, two terminal transcripts, a screenshot, whatever fits.
4. **Run the existing tests** before opening the PR. If you're adding a new tool, add a test that calls it through `core/llm_client.call_llm` with a stub tool definition.
5. **Don't bump version numbers yourself.** That's done at release time.

## Code style

- Python 3.11+ syntax (the project targets 3.11/3.12; type hints encouraged).
- Match the surrounding file. Most modules are flat and explicit — no clever metaprogramming.
- New tools live in `actions/` and follow the existing `xxx(parameters, speak) -> str` contract; see `actions/web_search.py` for the canonical shape.
- User-facing strings should still read like JARVIS (short, spoken, occasionally addressed as "sir" — that's intentional).

## Adding a new tool

1. Create `actions/your_tool.py` with a function that takes a `parameters` dict and returns a string reply.
2. Declare it in `main.py` as a Gemini-style `function_declarations` entry with a clear description (the LLM reads this to decide when to call it).
3. Register the dispatch in `main.py` next to the other tools.
4. Test it manually: `"use the new tool to …"` should be enough to trigger it through voice.

## Reporting bugs

Use GitHub issues. Include:

- OS and Python version
- The provider / model you were using (from `config/api_keys.json`, redacted)
- The exact command or spoken phrase
- What you expected vs. what happened
- A short log excerpt from the terminal if relevant

## Security

If you find a secret-handling bug, see `SECURITY.md` instead of opening a public issue.

## Code of conduct

Be kind. Assume good faith. Don't be the person that makes the next contributor not bother.

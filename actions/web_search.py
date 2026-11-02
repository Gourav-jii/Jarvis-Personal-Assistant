#web_search.py
import json
import sys
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _llm_synthesize(prompt: str, max_tokens: int = 900) -> str:
    """Answer via the configured LLM (used with DDG results for grounding)."""
    from core.llm_client import call_llm_text
    return call_llm_text(prompt, max_tokens=max_tokens)


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    """DDG news search — returns actual articles, not website homepages."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                })
    except Exception as e:
        print(f"[WebSearch] ⚠️ DDG news() failed ({e}) — falling back to text search")
        results = _ddg_search(query, max_results=max_results)
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   Source: {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_news(query: str, results: list[dict]) -> str:
    if not results:
        return f"No news found for: {query}"

    lines = [f"Latest news: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        if not title:
            continue
        src = f"  [{r['source']}]" if r.get("source") else ""
        lines.append(f"{i}. {title}{src}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:140]}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Briefing helper ────────────────────────────────────────────────────────────

def _llm_headlines(n: int = 5) -> tuple[list[str], str]:
    """
    Fetches current headlines: DDG news (real articles) synthesised into a
    short numbered list by the configured LLM. Falls back to the raw DDG
    titles when the LLM is unreachable.
    Returns (headline_list, raw_text_for_display).
    """
    import re

    try:
        articles = _ddg_news("top world news today", max_results=10)
    except Exception as e:
        print(f"[WebSearch] ⚠️ DDG news failed ({e})")
        articles = []

    if not articles:
        return [], "No news source reachable."

    src = "\n".join(
        f"- {a.get('title', '')}: {a.get('snippet', '')[:160]}"
        for a in articles if a.get("title")
    )

    try:
        raw = _llm_synthesize(
            f"Here are current news snippets:\n{src}\n\n"
            f"Write the {n} most important headlines as a numbered list, "
            f"titles only, no commentary.",
            max_tokens=300,
        ).strip()
    except Exception as e:
        print(f"[WebSearch] ⚠️ Headline synthesis failed ({e}) — using raw titles.")
        raw = "\n".join(f"{i}. {a['title']}" for i, a in enumerate(articles[:n], 1))

    headlines = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Only accept lines that begin with a number — skips preamble/closing sentences
        if not re.match(r'^[\d]+[.\)\-]', line):
            continue
        clean = re.sub(r'^[\d]+[.\)\-]\s*', '', line)
        clean = re.sub(r'^\*+\s*',          '', clean).strip()
        if clean and len(clean) > 10:
            headlines.append(clean)

    return headlines[:n], raw.strip()


# ── Modes ──────────────────────────────────────────────────────────────────────

def _synth_search(query: str, max_results: int = 6) -> str:
    """DDG results grounded through the LLM; falls back to formatted links."""
    results = _ddg_search(query, max_results=max_results)
    if not results:
        raise RuntimeError("no search results")
    ctx = "\n".join(
        f"- {r.get('title', '')}: {r.get('snippet', '')} ({r.get('url', '')})"
        for r in results
    )
    try:
        return _llm_synthesize(
            f"Answer the user's question using these web results. "
            f"Be concise and factual; cite source names in parentheses.\n\n"
            f"Question: {query}\n\nResults:\n{ctx}",
            max_tokens=800,
        )
    except Exception as e:
        print(f"[WebSearch] ⚠️ Synthesis failed ({e}) — showing raw results.")
        return _format_ddg(query, results)


def _search(query: str) -> str:
    """Default search — DDG + LLM synthesis."""
    try:
        return _synth_search(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Search failed ({e}) — direct LLM answer...")
        try:
            return _llm_synthesize(
                f"Answer concisely: {query}", max_tokens=600,
            )
        except Exception as e2:
            return f"Search unavailable: {e2}"


def _news(query: str) -> str:
    """
    Two parallel racers: DDG-news+LLM synthesis vs direct LLM knowledge.
    Returns whichever delivers a valid result first.
    """
    import threading

    ddg_query = query if query else "world news today"

    result_box  = [None]   # first valid result lands here
    lock        = threading.Lock()
    done_evt    = threading.Event()
    failures    = [0]

    def _store(r: str) -> None:
        if r and len(r) > 60:
            with lock:
                if result_box[0] is None:
                    result_box[0] = r
            done_evt.set()
        else:
            with lock:
                failures[0] += 1
                if failures[0] >= 2:   # both failed — unblock caller
                    done_evt.set()

    def _try_ddg():
        try:
            results = _ddg_news(ddg_query, max_results=8)
            text    = _format_news(ddg_query, results)
            # enrich with one-line context per story when the LLM is available
            try:
                ctx = "\n".join(
                    f"- {r.get('title','')}: {r.get('snippet','')[:140]}"
                    for r in results
                )
                text = _llm_synthesize(
                    f"Summarise today's news on '{ddg_query}' from these items. "
                    f"2-3 sentences per item max, mention sources.\n{ctx}",
                    max_tokens=700,
                )
            except Exception:
                pass
            _store(text)
        except Exception as e:
            print(f"[WebSearch] ⚠️ DDG news failed ({e})")
            _store("")

    def _try_llm():
        try:
            _store(_llm_synthesize(
                f"What are the latest notable news events about: "
                f"{ddg_query}? Give a brief overview. Note that your information "
                f"may not include today's events.",
                max_tokens=500,
            ))
        except Exception as e:
            print(f"[WebSearch] ⚠️ LLM news failed ({e})")
            _store("")

    threading.Thread(target=_try_llm, daemon=True).start()
    threading.Thread(target=_try_ddg, daemon=True).start()

    done_evt.wait(timeout=12.0)
    return result_box[0] or f"No news found for: {query}"


def _research(query: str) -> str:
    """
    Deep dive — DDG fetch for grounding, then a comprehensive LLM write-up.
    Falls back to whichever single source works.
    """
    research_query = (
        f"Comprehensive, detailed explanation of: {query}. "
        "Include background context, key facts, current state, and important nuances."
    )
    try:
        results = _ddg_search(query, max_results=8)
        ctx = "\n".join(
            f"- {r.get('title','')}: {r.get('snippet','')}"
            for r in results
        )
        return _llm_synthesize(
            f"{research_query}\n\nUse these web findings where relevant:\n{ctx}",
            max_tokens=1200,
        )
    except Exception as e:
        print(f"[WebSearch] ⚠️ Research grounding failed ({e}) — DDG fallback...")
        results = _ddg_search(query, max_results=10)
        return _format_ddg(query, results)


def _price(query: str) -> str:
    """Product price lookup — searches for current market prices."""
    price_query = f"current price of {query} — how much does it cost today"
    try:
        return _synth_search(price_query, max_results=8)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Price search failed ({e}) — DDG fallback...")
        results = _ddg_search(f"{query} price buy", max_results=6)
        return _format_ddg(query, results)


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    ctx = "\n".join(
        f"- {item}: " + "; ".join(
            r.get("snippet", "") for r in rs[:2]
        )
        for item, rs in all_results.items()
    )

    if ctx.strip("; "):
        try:
            return _llm_synthesize(
                f"{query}\n\nWeb findings to ground your comparison:\n{ctx}\n\n"
                f"Structure the answer per item, then a one-line verdict.",
                max_tokens=900,
            )
        except Exception as e:
            print(f"[WebSearch] ⚠️ Compare synthesis failed: {e} — raw snippets")

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
            if r.get("url"):
                lines.append(f"    {r['url']}")
    return "\n".join(lines)


# ── Public entry point ─────────────────────────────────────────────────────────

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query."

    if items and mode not in ("compare",):
        mode = "compare"

    if player:
        player.write_log(f"[Search:{mode}] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 mode={mode!r}  query={query!r}")

    try:
        if mode == "compare" and items:
            return _compare(items, aspect)
        if mode == "news":
            return _news(query)
        if mode == "research":
            return _research(query)
        if mode == "price":
            return _price(query)
        return _search(query)

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed: {e}"

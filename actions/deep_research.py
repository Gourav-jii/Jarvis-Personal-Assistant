#deep_research.py
"""
Deep Research — long-horizon autonomous web research for JARVIS.

Unlike `web_search` (one shot, seconds), this runs for MINUTES or HOURS in a
background thread and keeps going until it has actually answered the question:

    plan ──► search (many queries) ──► fetch & read full pages
      ▲                                        │
      │                                        ▼
      └──── find knowledge gaps ◄──── summarise evidence

Every round it re-reads what it has learned, decides what is still missing, and
writes new queries for itself. It stops early when it is confident, or when the
time budget runs out. The final report is spoken as a short answer and saved as
a full markdown document.

Non-blocking by design: the tool returns immediately with a job id so JARVIS
stays responsive and you can keep talking to him while it works.
"""

import json
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR     = _get_base_dir()
REPORT_DIR   = BASE_DIR / "research"

# Tuning ---------------------------------------------------------------------
MAX_PAGE_CHARS      = 7000    # characters of article text fed to the LLM per source
FETCH_TIMEOUT       = 12      # seconds per page
FETCH_WORKERS       = 6       # parallel page downloads
QUERIES_PER_ROUND   = 4       # search queries issued each round
RESULTS_PER_QUERY   = 5       # DDG hits per query
PAGES_PER_ROUND     = 8       # pages actually downloaded+read per round
MIN_ROUNDS          = 2       # never stop before this many rounds
CONFIDENCE_TO_STOP  = 88      # early exit when the model is this confident (0-100)
MAX_ROUNDS_HARD     = 60      # absolute safety ceiling

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# Question words / filler that make DuckDuckGo return dictionary and stock-photo
# pages instead of the actual topic. Stripped from every query before searching.
_STOPWORDS = {
    "what", "whats", "who", "whom", "whose", "when", "where", "why", "how",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing", "done",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by",
    "it", "its", "this", "that", "these", "those", "there",
    "i", "me", "my", "you", "your", "we", "us", "our", "they", "them",
    "tell", "explain", "about", "please", "find", "out", "give", "know",
    "research", "search", "look", "up", "everything", "anything", "some",
}


def _clean_query(q: str) -> str:
    """
    Turn a natural-language question into a keyword query.

    DuckDuckGo scores literal phrases, so "What is the Model Context Protocol
    (MCP) and who adopted it?" matches dictionary pages, while
    "Model Context Protocol MCP adopted" matches the real sources. Quoted
    phrases and CamelCase/acronym tokens are always preserved.
    """
    q = (q or "").strip()
    if not q:
        return ""

    # keep explicit "quoted phrases" untouched
    quoted = re.findall(r'"[^"]+"', q)
    rest   = re.sub(r'"[^"]+"', " ", q)

    rest = rest.replace("(", " ").replace(")", " ").replace("?", " ")
    tokens, seen = [], set()
    for raw in re.split(r"[\s,;:!]+", rest):
        tok = raw.strip(".'\u2019\"")
        if not tok:
            continue
        low = tok.lower()
        # always keep acronyms, versions, numbers and CamelCase
        keep_always = (tok.isupper() and len(tok) > 1) or any(c.isdigit() for c in tok)
        if not keep_always and low in _STOPWORDS:
            continue
        if low in seen:
            continue
        seen.add(low)
        tokens.append(tok)

    out = " ".join(quoted + tokens).strip()
    # never return something uselessly short — fall back to the original
    return out if len(out) >= 3 else q


# ---------------------------------------------------------------------------
# Web plumbing
# ---------------------------------------------------------------------------

def _ddgs():
    """The DDGS class from whichever package is installed."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from duckduckgo_search import DDGS
        return DDGS


# Search backends in preference order. DuckDuckGo's own endpoint is frequently
# rate-limited/broken, so we rotate: whichever backend answers first wins, and
# a second one is added for source diversity.
_BACKENDS = ("google", "startpage", "brave", "duckduckgo", "auto")


def _ddg_text(query: str, max_results: int = 5) -> list[dict]:
    """Web search with backend rotation — returns [] only if every backend fails."""
    DDGS = _ddgs()
    q    = _clean_query(query)
    out: list[dict] = []
    seen: set[str] = set()
    used = 0

    for backend in _BACKENDS:
        if used >= 2:                       # two backends is enough per query
            break
        try:
            with DDGS() as ddgs:
                try:
                    rows = list(ddgs.text(q, backend=backend,
                                          max_results=max_results))
                except TypeError:           # older signature without backend=
                    rows = list(ddgs.text(q, max_results=max_results))
        except Exception as e:
            print(f"[Research] backend {backend} failed for {q!r}: "
                  f"{str(e)[:80]}")
            continue

        if not rows:
            continue
        used += 1
        for r in rows:
            url = (r.get("href") or r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "title":   r.get("title", ""),
                "snippet": r.get("body", "") or r.get("description", ""),
                "url":     url,
            })

    if not out:
        print(f"[Research] all search backends returned nothing for {q!r}")
    return out


def _ddg_news(query: str, max_results: int = 5) -> list[dict]:
    """News search — used for recency-sensitive topics."""
    DDGS = _ddgs()
    q    = _clean_query(query)
    out: list[dict] = []
    for backend in ("google", "duckduckgo", "auto"):
        try:
            with DDGS() as ddgs:
                try:
                    rows = list(ddgs.news(q, backend=backend,
                                          max_results=max_results))
                except TypeError:
                    rows = list(ddgs.news(q, max_results=max_results))
        except Exception as e:
            print(f"[Research] news backend {backend} failed: {str(e)[:80]}")
            continue
        if rows:
            for r in rows:
                url = (r.get("url") or r.get("href") or "").strip()
                if url:
                    out.append({
                        "title":   r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url":     url,
                        "source":  r.get("source", ""),
                    })
            break
    if not out:
        out = _ddg_text(query, max_results)     # fall back to web search
    return out


_BAD_TAGS = ("script", "style", "nav", "footer", "header", "aside",
             "form", "noscript", "svg", "iframe")


def _extract_text(html: str) -> str:
    """Pull the readable body text out of an HTML page."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(list(_BAD_TAGS)):
        tag.decompose()

    # prefer real article containers when present
    main = (soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.body
            or soup)

    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _fetch_page(url: str) -> tuple[str, str]:
    """Download one URL and return (url, extracted_text). Never raises."""
    import requests
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en,*"},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
        )
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code != 200 or "html" not in ctype and "text" not in ctype:
            return url, ""
        resp.encoding = resp.encoding or "utf-8"
        return url, _extract_text(resp.text)[:MAX_PAGE_CHARS]
    except Exception as e:
        print(f"[Research] fetch failed {url}: {e}")
        return url, ""


def _fetch_many(urls: list[str]) -> dict[str, str]:
    """Download pages in parallel; returns {url: text} for pages with content."""
    pages: dict[str, str] = {}
    if not urls:
        return pages
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_page, u) for u in urls]
        for fut in as_completed(futures):
            try:
                url, text = fut.result()
            except Exception:
                continue
            if text and len(text) > 300:      # ignore near-empty shells
                pages[url] = text
    return pages


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _ask(prompt: str, max_tokens: int = 1200, system: str | None = None) -> str:
    from core.llm_client import call_llm_text
    return call_llm_text(prompt, system=system, max_tokens=max_tokens)


def _ask_json(prompt: str, max_tokens: int = 900) -> dict:
    from core.llm_client import ask_llm_json
    try:
        out = ask_llm_json(prompt, max_tokens=max_tokens)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        print(f"[Research] JSON parse failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# The research job
# ---------------------------------------------------------------------------

class ResearchJob:
    """One long-running investigation. Owns its own thread."""

    def __init__(self, query: str, minutes: float,
                 player=None, speak=None, notify=None):
        self.id        = uuid.uuid4().hex[:8]
        self.query     = query.strip()
        self.budget    = max(0.5, float(minutes)) * 60.0
        self.player    = player
        self._speak_cb = speak
        self._notify   = notify        # called with the finished report text

        self.started   = time.monotonic()
        self.started_at = datetime.now()
        self.state     = "starting"    # starting|researching|writing|done|stopped|error
        self.round     = 0
        self.confidence = 0
        self.error     = ""

        self.plan: list[str]      = []
        self.findings: list[dict] = []   # {"url","title","summary"}
        self.seen_urls: set[str]  = set()
        self.gaps: list[str]      = []
        self.report               = ""
        self.report_path: Path | None = None

        self._stop  = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock  = threading.Lock()

    # ── plumbing ────────────────────────────────────────────────────────
    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget - self.elapsed)

    def _log(self, msg: str) -> None:
        print(f"[Research:{self.id}] {msg}")
        if self.player:
            try:
                self.player.write_log(f"[Research] {msg}")
            except Exception:
                pass

    def _say(self, msg: str) -> None:
        if self._speak_cb:
            try:
                self._speak_cb(msg)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"research-{self.id}")
        self._thread.start()

    # ── status ──────────────────────────────────────────────────────────
    def status_text(self) -> str:
        mins  = self.elapsed / 60.0
        left  = self.remaining / 60.0
        with self._lock:
            n = len(self.findings)
        base = (f"Research {self.id} — {self.state}. "
                f"Topic: {self.query}. "
                f"Round {self.round}, {n} sources read, "
                f"confidence {self.confidence}%. "
                f"Elapsed {mins:.1f} min, {left:.1f} min of budget left.")
        if self.gaps:
            base += " Currently chasing: " + "; ".join(self.gaps[:3]) + "."
        if self.state == "done" and self.report_path:
            base += f" Full report: {self.report_path}"
        if self.error:
            base += f" Error: {self.error}"
        return base

    # ── phase 1: plan ───────────────────────────────────────────────────
    def _preflight(self) -> str:
        """Confirm the brain is reachable before burning a time budget."""
        try:
            _ask("Reply with the single word: READY", max_tokens=200)
            return ""
        except Exception as e:
            msg = str(e)
            if "Max retries" in msg or "refused" in msg or "10061" in msg:
                from core.llm_client import resolve_config
                url = resolve_config()["url"]
                return (f"the language model at {url} is not responding. "
                        f"Start it, then ask me again.")
            return f"the language model rejected the request: {msg[:160]}"

    def _fallback_queries(self) -> list[str]:
        """Keyword-ish queries when the planner is unavailable."""
        cleaned = _clean_query(self.query)
        return [cleaned] if cleaned else [self.query]

    def _make_plan(self) -> list[str]:
        prompt = (
            f"You are a research director. Today is "
            f"{self.started_at.strftime('%Y-%m-%d')}.\n"
            f"RESEARCH QUESTION: {self.query}\n\n"
            "Break this into the specific sub-questions that must be answered "
            "to fully resolve it, then write web search queries that will find "
            "those answers. Queries must be short, keyword-style, and diverse — "
            "cover different angles, terminology, sources and viewpoints. "
            "Include at least one query aimed at criticism/limitations/"
            "counter-evidence.\n\n"
            'Reply as JSON only: {"subquestions": ["..."], "queries": ["..."], '
            '"needs_recency": true or false}'
        )
        data = _ask_json(prompt, max_tokens=900)
        self.plan = [str(q) for q in (data.get("subquestions") or [])][:10]
        queries   = [str(q) for q in (data.get("queries") or [])]
        self.needs_recency = bool(data.get("needs_recency"))
        if not queries:
            queries = self._fallback_queries()
        return queries[:QUERIES_PER_ROUND * 2]

    # ── phase 2: gather ─────────────────────────────────────────────────
    def _gather(self, queries: list[str]) -> int:
        """Search, download and summarise. Returns count of new sources read."""
        hits: list[dict] = []
        for q in queries[:QUERIES_PER_ROUND]:
            if self._stop.is_set():
                return 0
            self._log(f"searching: {q}")
            hits += _ddg_text(q, RESULTS_PER_QUERY)
            if getattr(self, "needs_recency", False):
                hits += _ddg_news(q, 3)

        # dedupe, skip anything already read
        fresh: list[dict] = []
        for h in hits:
            u = (h.get("url") or "").strip()
            if not u or u in self.seen_urls:
                continue
            self.seen_urls.add(u)
            fresh.append(h)
        fresh = fresh[:PAGES_PER_ROUND]
        if not fresh:
            return 0

        self._log(f"reading {len(fresh)} new pages…")
        pages = _fetch_many([h["url"] for h in fresh])
        if self._stop.is_set():
            return 0

        titles = {h["url"]: h.get("title", "") for h in fresh}
        snips  = {h["url"]: h.get("snippet", "") for h in fresh}

        # summarise each page against the research question, in parallel
        def _digest(url: str) -> dict | None:
            body = pages.get(url) or snips.get(url) or ""
            if not body:
                return None
            prompt = (
                f"RESEARCH QUESTION: {self.query}\n"
                f"SOURCE: {titles.get(url) or url}\nURL: {url}\n\n"
                f"PAGE CONTENT:\n{body}\n\n"
                "Extract ONLY what is relevant to the research question: concrete "
                "facts, numbers, dates, names, claims, and any caveats. Quote key "
                "figures exactly. If this page is irrelevant, reply exactly: "
                "IRRELEVANT.\nBe dense; no preamble."
            )
            try:
                summary = _ask(prompt, max_tokens=700)
            except Exception as e:
                print(f"[Research] digest failed {url}: {e}")
                return None
            if not summary or "IRRELEVANT" in summary[:40].upper():
                return None
            return {"url": url, "title": titles.get(url) or url,
                    "summary": summary.strip()}

        added = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(_digest, u): u for u in pages}
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                try:
                    d = fut.result()
                except Exception:
                    d = None
                if d:
                    with self._lock:
                        self.findings.append(d)
                    added += 1
        return added

    # ── phase 3: reflect ────────────────────────────────────────────────
    def _reflect(self) -> list[str]:
        """Score coverage and produce the next round's queries."""
        with self._lock:
            evidence = "\n\n".join(
                f"[{i+1}] {f['title']}\n{f['summary'][:900]}"
                for i, f in enumerate(self.findings[-24:])
            )
        prompt = (
            f"RESEARCH QUESTION: {self.query}\n"
            f"SUB-QUESTIONS: {json.dumps(self.plan, ensure_ascii=False)}\n"
            f"TIME LEFT: {self.remaining/60:.0f} minutes\n\n"
            f"EVIDENCE GATHERED SO FAR:\n{evidence or '(nothing yet)'}\n\n"
            "Judge the evidence. Which sub-questions are now answered with "
            "solid sourcing, and which are still weak, missing, contradictory "
            "or based on a single source? Then write the next batch of web "
            "search queries that would close the biggest remaining gaps. "
            "Do not repeat queries whose answers you already have.\n\n"
            'Reply as JSON only: {"confidence": 0-100, "gaps": ["..."], '
            '"next_queries": ["..."]}'
        )
        data = _ask_json(prompt, max_tokens=900)
        try:
            self.confidence = int(data.get("confidence") or 0)
        except Exception:
            self.confidence = 0
        self.gaps = [str(g) for g in (data.get("gaps") or [])][:6]
        nxt = [str(q) for q in (data.get("next_queries") or [])]
        return nxt[:QUERIES_PER_ROUND]

    # ── phase 4: write ──────────────────────────────────────────────────
    def _write_report(self) -> str:
        with self._lock:
            findings = list(self.findings)

        if not findings:
            return (f"I could not gather usable sources on “{self.query}”. "
                    "The searches came back empty or every page failed to load.")

        evidence = "\n\n".join(
            f"[{i+1}] {f['title']} — {f['url']}\n{f['summary']}"
            for i, f in enumerate(findings)
        )
        # keep the synthesis prompt within a sane size
        if len(evidence) > 90000:
            evidence = evidence[:90000] + "\n\n…(older evidence truncated)"

        prompt = (
            f"You are writing the final research report.\n"
            f"QUESTION: {self.query}\n"
            f"Date: {self.started_at.strftime('%Y-%m-%d')}. "
            f"Sources read: {len(findings)}. "
            f"Research time: {self.elapsed/60:.0f} minutes.\n\n"
            f"EVIDENCE (numbered sources):\n{evidence}\n\n"
            "Write a decisive markdown report:\n"
            "## Answer — lead with the direct answer in 2-4 sentences.\n"
            "## Key findings — bullets, each citing source numbers like [3].\n"
            "## Details — the substance, organised under your own subheadings, "
            "with concrete numbers and dates.\n"
            "## Disagreements & uncertainty — where sources conflict or "
            "evidence is thin. Be honest; do not smooth this over.\n"
            "## Bottom line — the practical takeaway.\n\n"
            "Rules: cite source numbers for every factual claim. Never invent "
            "facts that are not in the evidence. If the evidence does not "
            "answer part of the question, say so plainly."
        )
        report = _ask(prompt, max_tokens=4000)

        src_list = "\n".join(f"{i+1}. [{f['title']}]({f['url']})"
                             for i, f in enumerate(findings))
        header = (f"# Research: {self.query}\n\n"
                  f"*{self.started_at.strftime('%Y-%m-%d %H:%M')} · "
                  f"{len(findings)} sources · {self.round} rounds · "
                  f"{self.elapsed/60:.0f} min · confidence {self.confidence}%*\n\n")
        return f"{header}{report}\n\n## Sources\n{src_list}\n"

    def _save(self, text: str) -> Path | None:
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", self.query.lower()).strip("-")[:50]
            path = REPORT_DIR / f"{slug or 'research'}-{self.started_at:%Y%m%d-%H%M}.md"
            path.write_text(text, encoding="utf-8")
            return path
        except Exception as e:
            print(f"[Research] could not save report: {e}")
            return None

    # ── the loop ────────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            self._log(f"starting — budget {self.budget/60:.0f} min — {self.query}")
            self.state = "researching"

            problem = self._preflight()
            if problem:
                self.state = "error"
                self.error = problem
                self._log(f"aborted: {problem}")
                self._say(f"Sir, I cannot start that research — {problem}")
                return

            queries = self._make_plan()
            if self.plan:
                self._log(f"plan: {len(self.plan)} sub-questions")

            last_spoken = time.monotonic()

            while not self._stop.is_set():
                if self.remaining <= 20 and self.round >= 1:
                    self._log("time budget exhausted")
                    break
                if self.round >= MAX_ROUNDS_HARD:
                    break

                self.round += 1
                self._log(f"round {self.round} — {len(queries)} queries, "
                          f"{self.remaining/60:.0f} min left")

                added = self._gather(queries)
                if self._stop.is_set():
                    break
                with self._lock:
                    total = len(self.findings)
                self._log(f"round {self.round}: +{added} sources (total {total})")

                queries = self._reflect()
                self._log(f"confidence {self.confidence}% · "
                          f"{len(self.gaps)} open gaps")

                # occasional spoken progress, at most every 3 minutes
                if time.monotonic() - last_spoken > 180:
                    last_spoken = time.monotonic()
                    self._say(f"Still researching, sir. {total} sources read, "
                              f"confidence {self.confidence} percent.")

                if (self.round >= MIN_ROUNDS
                        and self.confidence >= CONFIDENCE_TO_STOP):
                    self._log("confident enough — wrapping up")
                    break
                if not queries:
                    if added == 0:
                        self._log("no new leads — wrapping up")
                        break
                    queries = [self.query]

            # ── synthesis ──
            if not self.findings:
                self.state = "error"
                self.error = ("no usable sources — every search came back empty "
                              "or every page failed to load/summarise")
                self._log(f"aborted: {self.error}")
                self._say(f"Sir, I could not find usable sources on {self.query}.")
                return

            if self._stop.is_set() and not self.findings:
                self.state = "stopped"
                self._log("stopped before any findings")
                return

            self.state = "writing"
            self._log("writing final report…")
            self.report      = self._write_report()
            self.report_path = self._save(self.report)
            self.state = "stopped" if self._stop.is_set() else "done"

            self._log(f"{self.state} — {len(self.findings)} sources, "
                      f"{self.elapsed/60:.1f} min"
                      + (f" — saved {self.report_path.name}" if self.report_path else ""))

            if self.player:
                try:
                    self.player.show_content(
                        f"RESEARCH — {self.query[:38]}", self.report)
                except Exception:
                    pass

            if self._notify:
                try:
                    self._notify(self)
                except Exception as e:
                    print(f"[Research] notify failed: {e}")

        except Exception as e:
            self.state = "error"
            self.error = str(e)[:300]
            self._log(f"failed: {self.error}")
            self._say(f"Sir, the research on {self.query} failed. {self.error[:120]}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_JOBS: dict[str, ResearchJob] = {}
_JOBS_LOCK = threading.Lock()


def _register(job: ResearchJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.id] = job
        # keep the registry small: drop finished jobs beyond the last 10
        finished = [j for j in _JOBS.values()
                    if j.state in ("done", "stopped", "error")]
        if len(finished) > 10:
            finished.sort(key=lambda j: j.started)
            for j in finished[:-10]:
                _JOBS.pop(j.id, None)


def _get(job_id: str | None) -> ResearchJob | None:
    with _JOBS_LOCK:
        if job_id:
            return _JOBS.get(job_id)
        if not _JOBS:
            return None
        # most recently started
        return max(_JOBS.values(), key=lambda j: j.started)


def _active() -> list[ResearchJob]:
    with _JOBS_LOCK:
        return [j for j in _JOBS.values()
                if j.state in ("starting", "researching", "writing")]


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def deep_research(
    parameters:     dict,
    response=None,
    player=None,
    speak=None,
    session_memory=None,
) -> str:
    """
    action = start   : begin a background investigation (returns immediately)
             status  : progress of a running job
             report  : the finished report text
             stop    : end a job early and write up whatever it has
             list    : all known jobs
    """
    params  = parameters or {}
    action  = (params.get("action") or "start").lower().strip()
    query   = (params.get("query") or "").strip()
    job_id  = (params.get("job_id") or "").strip() or None

    try:
        minutes = float(params.get("minutes") or 10)
    except Exception:
        minutes = 10.0
    minutes = max(0.5, min(minutes, 480.0))     # 30 s … 8 h

    # ── list ──
    if action == "list":
        with _JOBS_LOCK:
            jobs = sorted(_JOBS.values(), key=lambda j: j.started, reverse=True)
        if not jobs:
            return "No research jobs yet."
        return "\n".join(
            f"{j.id} — {j.state} — {j.query[:60]} "
            f"({len(j.findings)} sources, {j.elapsed/60:.0f} min)"
            for j in jobs
        )

    # ── status ──
    if action == "status":
        job = _get(job_id)
        if not job:
            return "No research job is running. Start one with action='start'."
        return job.status_text()

    # ── report ──
    if action == "report":
        job = _get(job_id)
        if not job:
            return "No research job found."
        if job.state in ("starting", "researching"):
            return ("That investigation is still running. "
                    + job.status_text()
                    + " Ask again later, or use action='stop' to wrap it up now.")
        if job.state == "writing":
            return "The report is being written right now — give it a moment."
        if job.state == "error":
            return f"That research failed: {job.error}"
        if not job.report:
            return "No report was produced."
        head = job.report[:6000]
        if job.report_path:
            head += f"\n\n(Full report saved to {job.report_path})"
        return head

    # ── stop ──
    if action == "stop":
        job = _get(job_id)
        if not job:
            return "No research job to stop."
        if job.state in ("done", "stopped", "error"):
            return f"Research {job.id} already finished ({job.state})."
        job.stop()
        return (f"Stopping research {job.id} on “{job.query}”. "
                f"It will write up the {len(job.findings)} sources it has.")

    # ── start ──
    if not query:
        return "What should I research?"

    running = _active()
    if len(running) >= 2:
        return ("Two investigations are already running: "
                + "; ".join(f"{j.id} ({j.query[:40]})" for j in running)
                + ". Let one finish, or stop it first.")

    def _on_done(job: ResearchJob) -> None:
        """Hand the finished report to JARVIS so he reports back unprompted."""
        if speak:
            try:
                lead = _ask(
                    f"Research question: {job.query}\n\n"
                    f"REPORT:\n{job.report[:9000]}\n\n"
                    "Tell the user the answer out loud as JARVIS would: "
                    "start with the direct answer, then the 2-3 most important "
                    "supporting points, then any serious caveat. Spoken prose, "
                    "no markdown, no source numbers, under 130 words.",
                    max_tokens=500,
                )
                intro = (f"Sir, I've finished researching {job.query}. "
                         f"I read {len(job.findings)} sources. ")
                speak(intro + (lead or "").strip())
            except Exception as e:
                print(f"[Research] spoken summary failed: {e}")
                speak(f"Sir, the research on {job.query} is complete. "
                      f"{len(job.findings)} sources read. The report is on screen.")

    job = ResearchJob(query, minutes, player=player, speak=speak,
                      notify=_on_done)
    _register(job)
    job.start()

    return (f"[RESEARCH STARTED — job {job.id}]\n"
            f"Investigating “{query}” for up to {minutes:.0f} minutes in the "
            f"background. I will search, read full pages, find my own knowledge "
            f"gaps and keep digging until I have a solid answer.\n"
            f"Tell the user this is now running in the background, that they can "
            f"keep talking to you normally, and that you will report back the "
            f"moment it is done. Do NOT pretend to have results yet.")

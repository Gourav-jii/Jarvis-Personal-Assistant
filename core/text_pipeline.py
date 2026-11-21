"""
Text-mode voice pipeline for MARK XLVIII.

Replaces the Gemini Live audio session with a fully provider-agnostic chain:

    mic ──► VAD gate ──► Whisper STT ──► LLM (DeepSeek / any OpenAI-compatible /
                                          Ollama local) ──► streamed sentences
         ──► TTS (EdgeTTS / Kokoro / ElevenLabs)

Features preserved from Live mode:
  • hands-free conversation (VAD end-of-speech detection)
  • tool calling in a multi-round agent loop (max 6 rounds)
  • sentence-by-sentence speech — talking starts before generation finishes
  • instant interrupt (ESC / button) — kills playback + discards generation
  • text input path (UI box + dashboard) routed into the same brain
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
import queue as _queue
import numpy as np

from core.llm_client import (
    stream_chat,
    call_llm,
    resolve_config,
    ensure_ollama_running,
    warmup_model,
    describe_image,
    gemini_declarations_to_openai,
)

# Sentence boundary: [.!?] + whitespace, or blank line. Avoids decimals (3.5).
_SENT_END = re.compile(r"(?<=[.!?])\s+|(?<=\n)\s*\n")

MAX_TOOL_ROUNDS = 6

_STOP_KEYWORDS_EN = [
    'stop', 'halt', 'pause', 'shut up', 'be quiet', 'quiet', 'silence',
    'stop it', 'stop talking', 'stop speaking', 'freeze'
]
_STOP_KEYWORDS_HINGLISH = [
    'chup', 'chup raho', 'chup ho jao', 'chup kar', 'chup karo', 'chup chap',
    'chup rhne', 'chup rhna', 'chup reh', 'ruk jao', 'ruko', 'ruk ja', 'ruk',
    'rukja', 'rukna', 'shant', 'shant raho', 'shant ho jao', 'shanti',
    'band karo', 'bolna band karo', 'bolna band', 'band kar',
    'bas karo', 'bas kar', 'bas', 'khatam karo', 'khatam'
]
_STOP_KEYWORDS_DEV = [
    'चुप', 'चुप रहो', 'चुप हो जाओ', 'चुप कर', 'चुप करो', 'चुपचाप',
    'रुक जाओ', 'रुको', 'रुक जा', 'रुक', 'शांत', 'शांत रहो', 'शांत हो जाओ',
    'बंद करो', 'बोलना बंद', 'बस करो', 'बस कर', 'बस', 'स्टॉप', 'शट अप'
]

_STOP_EN_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in sorted(_STOP_KEYWORDS_EN + _STOP_KEYWORDS_HINGLISH, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

_WHISPER_HALLUCINATIONS = {
    "i'm sorry", "i am sorry",
    "how can i help you today", "how can i help you today?",
    "what's on your mind", "what's on your mind?",
    "what is on your mind", "what is on your mind?",
    "i am all yours", "i am all yours, watch on your mind",
    "i am all yours, watch on your mind.",
    "watch on your mind", "watch on your mind.",
    "deep.", "deep",
    "thank you", "thank you for watching", "thank you for watching.",
    "thanks for watching", "thanks for watching.",
    "subtitles by", "subtitles by the amara.org community",
    "subscribe", "please subscribe",
    "you",
    "bye", "goodbye",
}

_ARABIC_URDU_RE   = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')
_LEADING_MATRA_RE = re.compile(r'(?:^|\s)[\u093E-\u094C\u0962\u0963]')
_NON_STD_DEV_RE   = re.compile(r'[\u093B\u094E\u094F\u0955-\u0957]')

def _is_gibberish(text: str) -> bool:
    """Detect non-standard Devanagari extensions, isolated matras, Arabic/Urdu, or garbage."""
    if not text:
        return True
    t = text.strip()
    if _ARABIC_URDU_RE.search(t):
        return True
    if _NON_STD_DEV_RE.search(t):
        return True
    if _LEADING_MATRA_RE.search(t):
        return True
    return False

def _is_whisper_hallucination(text: str) -> bool:
    if not text:
        return True
    t = text.strip().lower().strip(" .!?,:;\"'")
    if not t or len(t) < 2:
        return True
    if _is_gibberish(text):
        return True
    if t in _WHISPER_HALLUCINATIONS:
        return True
    for phrase in ("subtitles by", "thank you for watching", "thanks for watching", "amara.org", "watch on your mind"):
        if phrase in t:
            return True
    return False

def _is_stop_command(text: str) -> bool:
    """Check if user input is an explicit instruction to stop, be quiet, or halt."""
    if not text:
        return False
    t = text.strip().lower()
    words = t.split()
    # A true stop command is concise (<= 4 words). Do not trigger on long sentences.
    if len(words) > 4:
        return False
    if _STOP_EN_RE.search(t):
        return True
    for w in _STOP_KEYWORDS_DEV:
        if re.search(r'(?<!\S)' + re.escape(w) + r'(?!\S)', t):
            return True
    return False


def get_base_dir():
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _ensure_json_str(val) -> str:
    """Ensure tool arguments are a valid single JSON string, never double-encoded."""
    if isinstance(val, str):
        val = val.strip()
        try:
            parsed = json.loads(val)
            if isinstance(parsed, str):
                try:
                    json.loads(parsed)
                    return parsed
                except Exception:
                    pass
            return val
        except Exception:
            if val.startswith("{") and "}{" in val:
                first_obj = val[:val.find("}{") + 1]
                try:
                    json.loads(first_obj)
                    return first_obj
                except Exception:
                    pass
            return json.dumps(val)
    return json.dumps(val or {})


# ─────────────────────────────────────────────────────────────────────────────
# Voice activity detection
# ─────────────────────────────────────────────────────────────────────────────

class _VADGate:
    """Adaptive energy-based VAD running on 16 kHz int16 mono frames."""

    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.007,
        silence_secs: float = 0.65,
        min_speech_secs: float = 0.22,
        max_utterance_secs: float = 20.0,
        prebuffer_secs: float = 0.45,
    ):
        sr = sample_rate
        self.sr             = sample_rate
        self.threshold      = threshold
        self.silence_frames  = int(silence_secs * sr / 1024)
        self.min_speech_frames = int(min_speech_secs * sr / 1024)
        self.max_frames     = int(max_utterance_secs * sr / 1024)
        self.prebuf: list[np.ndarray] = []
        self.prebuf_max     = int(prebuffer_secs * sr / 1024)
        self.noise_floor    = 0.003
        self.reset()

    def reset(self):
        self.buf: list[np.ndarray] = []
        self.prebuf = self.prebuf[-self.prebuf_max:]
        self.silence_run  = 0
        self.speech_run   = 0
        self.in_speech    = False

    def feed(self, frame_i16: np.ndarray) -> np.ndarray | None:
        """
        Feed one int16 frame. Returns the completed utterance (float32 mono)
        when speech has ended, otherwise None.
        """
        x = frame_i16.astype(np.float32).flatten() / 32768.0
        rms = float(np.sqrt(np.mean(np.square(x)) + 1e-12))

        if not self.in_speech:
            self.noise_floor = 0.96 * self.noise_floor + 0.04 * min(rms, 0.05)
            self.prebuf.append(frame_i16)
            if len(self.prebuf) > self.prebuf_max:
                self.prebuf.pop(0)
            speech_trigger = max(self.threshold, self.noise_floor * 1.8 + 0.002)
            if rms >= speech_trigger:
                self.in_speech  = True
                self.speech_run = 1
                self.buf        = list(self.prebuf)   # include lead-in context
                self.silence_run = 0
                print(f"[Pipeline] 🎙️ Speech started (RMS: {rms:.4f}, noise floor: {self.noise_floor:.4f})", flush=True)
            return None

        # ── in speech ──
        self.buf.append(frame_i16)
        speech_hold = max(self.threshold * 0.75, self.noise_floor * 1.3)
        if rms >= speech_hold:
            self.speech_run  += 1
            self.silence_run  = 0
        else:
            self.silence_run += 1

        ended = (
            (self.silence_run >= self.silence_frames and self.speech_run >= self.min_speech_frames)
            or len(self.buf) >= self.max_frames
        )
        if ended:
            utterance = np.concatenate(self.buf).astype(np.float32).flatten() / 32768.0
            dur = len(utterance) / self.sr
            peak_rms = float(np.sqrt(np.mean(np.square(utterance)) + 1e-12))
            max_amp = float(np.max(np.abs(utterance)))
            self.reset()
            # If the utterance doesn't have sufficient energy or length, discard it
            if dur < 0.22 or peak_rms < 0.004 or max_amp < 0.015:
                print(f"[Pipeline] 🔇 Faint noise discarded (dur={dur:.2f}s, rms={peak_rms:.4f}, peak={max_amp:.4f})", flush=True)
                return None
            print(f"[Pipeline] 🎙️ Utterance captured ({dur:.1f}s, RMS: {peak_rms:.4f}, Peak: {max_amp:.4f})", flush=True)
            return utterance
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TextPipeline:

    def __init__(self, jarvis, tools: list | None = None):
        """
        jarvis: JarvisLive instance — provides .ui, .set_speaking(), ._run_tool(),
                ._loop, dashboard hooks.
        tools:  Gemini-style tool declarations (converted internally).
        """
        self.jarvis   = jarvis
        self.ui       = jarvis.ui
        self.tools    = gemini_declarations_to_openai(tools) if tools else None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []

        # conversation state
        self.history: list[dict] = []
        self.MAX_HISTORY = 24                    # keep last N messages

        # speech state
        self._speaking_lock = threading.Lock()
        self._is_speaking   = False
        self._interrupted   = False
        self._tts           = None               # lazy TTSPlayer
        self._tts_lock      = threading.Lock()
        self.voice_muted    = False              # UI toggle: silence TTS output
        self._last_spoken_text = ""

        # mic / vad
        self._vad          = _VADGate()
        self._mic_paused   = False               # True while JARVIS speaks or muted
        self._post_speak_cooldown_until = 0.0
        self._utterance_q: "_queue.Queue[np.ndarray]" = _queue.Queue(maxsize=4)
        self._text_q:      "_queue.Queue[str]"           = _queue.Queue(maxsize=16)
        self._speak_q:     "_queue.Queue[str | None]"    = _queue.Queue(maxsize=64)
        self._stt          = None
        self._stt_lang     = None
        self._mic_thread   = None
        self._tts_worker   = None
        self._started      = False

        cfg_path = get_base_dir() / "config" / "api_keys.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        self._vad.threshold = float(cfg.get("vad_threshold", self._vad.threshold))
        lang = (cfg.get("stt_language") or "").strip().lower()
        self._stt_lang = None if lang in ("", "auto") else lang

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Call once inside the running asyncio loop."""
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_event_loop()

        cfg = resolve_config()
        print(f"[Pipeline] Provider: {cfg['provider']}  model: {cfg['model']}  url: {cfg['url']}")
        self.ui.write_log(f"SYS: Brain online — {cfg['model']} @ {cfg['url']}")

        # health check + warmup in background
        async def _warm():
            ok = await asyncio.to_thread(ensure_ollama_running)
            if not ok:
                self.ui.write_log(
                    f"ERR: Cannot reach LLM at {cfg['url']} — check config/internet."
                )
                return
            await asyncio.to_thread(warmup_model, None)
        self._tasks.append(self._loop.create_task(_warm()))

        # STT loader (background thread — Whisper takes seconds)
        def _load_stt():
            try:
                from core.stt import WhisperSTT
                self._stt = WhisperSTT(model_name="base", language=self._stt_lang)
                print("[Pipeline] STT ready")
            except Exception as e:
                print(f"[Pipeline] STT load failed: {e}")
                self.ui.write_log(f"ERR: STT failed — {e}")
        threading.Thread(target=_load_stt, daemon=True, name="stt-loader").start()

        # TTS worker (serialises every spoken sentence)
        def _load_tts():
            try:
                from core.tts import create_tts_player
                cfg2 = {}
                p = get_base_dir() / "config" / "api_keys.json"
                try:
                    cfg2 = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
                self._tts = create_tts_player(cfg2)
                print("[Pipeline] TTS ready")
            except Exception as e:
                print(f"[Pipeline] TTS load failed: {e}")
                self.ui.write_log(f"ERR: TTS failed — {e}")
        threading.Thread(target=_load_tts, daemon=True, name="tts-loader").start()

        self._tts_worker = threading.Thread(target=self._tts_loop, daemon=True, name="tts-worker")
        self._tts_worker.start()

        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True, name="mic-vad")
        self._mic_thread.start()

        self._tasks.append(self._loop.create_task(self._respond_loop()))

    # ── microphone → VAD → utterance queue ───────────────────────────────

    def _mic_loop(self) -> None:
        import sounddevice as sd
        try:
            default_dev = sd.query_devices(kind='input')
            dev_name = default_dev.get('name', 'default')
        except Exception:
            dev_name = 'default'
        print(f"[Pipeline] 🎤 Mic started ({dev_name}, 16 kHz, adaptive VAD)")

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                busy = self._is_speaking
            if busy or self.ui.muted or self._interrupted:
                self._vad.reset()
                return
            if time.monotonic() < self._post_speak_cooldown_until:
                self._vad.reset()
                return
            utt = self._vad.feed(indata.copy())
            if utt is not None:
                try:
                    self._utterance_q.put_nowait(utt)
                except _queue.Full:
                    pass

        while True:
            try:
                with sd.InputStream(
                    samplerate=16000, channels=1, dtype="int16",
                    blocksize=1024, callback=callback,
                ):
                    while True:
                        time.sleep(0.2)
            except Exception as e:
                print(f"[Pipeline] ❌ Mic error, retrying in 2s: {e}")
                time.sleep(2.0)

    # ── TTS worker ───────────────────────────────────────────────────────

    def _tts_loop(self) -> None:
        """Dedicated thread: pulls sentences and speaks them serially."""
        import sounddevice as sd
        while True:
            text = self._speak_q.get()
            if text is None:
                continue
            if self._interrupted:
                continue
            # ── Voice-mute: skip synthesis entirely; reply stays in the log ──
            if self.voice_muted:
                self.ui.write_log(f"Jarvis (muted): {text}")
                self._post_speak_cooldown_until = time.monotonic() + 0.15
                continue
            if not self._tts:
                time.sleep(0.2)
                self._speak_q.put(text)          # retry until TTS engine loads
                time.sleep(0.8)
                continue
            with self._speaking_lock:
                if self._interrupted:
                    continue
                self._is_speaking = True
            try:
                self._last_spoken_text = str(text or "")
                self.ui.set_state("SPEAKING")
                self._tts.speak(text)
            except Exception as e:
                print(f"[Pipeline] TTS error: {e}")
            finally:
                with self._speaking_lock:
                    self._is_speaking = False
                    was_interrupted = self._interrupted
                if not self.ui.muted and not was_interrupted:
                    self.ui.set_state("LISTENING")
                # brief guard so speaker audio doesn't re-trigger VAD
                self._post_speak_cooldown_until = time.monotonic() + 0.6

    def say(self, text: str) -> None:
        """Thread-safe: speak a sentence (used by tools, briefing, alerts)."""
        text = (text or "").strip()
        if not text:
            return
        try:
            self._speak_q.put_nowait(text)
        except _queue.Full:
            pass

    def submit_text(self, text: str) -> None:
        """Thread-safe: feed typed/dashboard text straight into the brain."""
        try:
            self._text_q.put_nowait(text)
        except _queue.Full:
            pass

    def interrupt(self) -> None:
        """Stop talking NOW: kill audio, discard pending speech + generation."""
        self._interrupted = True
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        # drain speech queue
        while True:
            try:
                self._speak_q.get_nowait()
            except _queue.Full:
                break
            except _queue.Empty:
                break
        with self._speaking_lock:
            self._is_speaking = False
        self._post_speak_cooldown_until = time.monotonic() + 0.25
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: Interrupted — listening...")
        # allow mic again shortly; flag cleared lazily on next utterance cycle
        def _clear():
            time.sleep(0.3)
            self._interrupted = False
        threading.Thread(target=_clear, daemon=True).start()

    # ── transcription ────────────────────────────────────────────────────

    def _wait_stt(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while self._stt is None and time.time() < deadline:
            time.sleep(0.25)
        return self._stt is not None

    # ── respond loop ─────────────────────────────────────────────────────

    async def _respond_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            # ── grab next input: typed text first, then voice ──
            text = ""
            try:
                text = self._text_q.get_nowait()
            except _queue.Empty:
                text = ""

            if not text:
                try:
                    utt = self._utterance_q.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.04)
                    continue

                if self._interrupted:
                    continue
                if not self._wait_stt():
                    self.say("My speech recognition is still loading, sir.")
                    continue
                self.ui.set_state("PROCESSING")
                try:
                    text = await loop.run_in_executor(None, self._stt.transcribe, utt)
                    print(f"[Pipeline] 🎙️ Transcribed: {repr(text)}", flush=True)
                except Exception as e:
                    print(f"[Pipeline] STT error: {e}", flush=True)
                    if not self.ui.muted and not self._is_speaking:
                        self.ui.set_state("LISTENING")
                    continue

            if not text or not text.strip():
                if not self.ui.muted and not self._is_speaking:
                    self.ui.set_state("LISTENING")
                continue
            text = text.strip()

            # ── 1. Stop / Silence command check (chup, stop, ruk jao, etc.) ───
            if _is_stop_command(text):
                print(f"[Pipeline] 🛑 Stop command received: {text}", flush=True)
                self.ui.write_log(f"You: {text}")
                self.interrupt()
                self.ui.write_log("Jarvis: (Quiet / Stopped)")
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                continue

            # ── 2. Whisper hallucination discard (background noise / phantom speech) ─
            if _is_whisper_hallucination(text):
                print(f"[Pipeline] 🔇 Discarded Whisper hallucination: {repr(text)}", flush=True)
                if not self.ui.muted and not self._is_speaking:
                    self.ui.set_state("LISTENING")
                continue

            # ── 3. Discard if speaker echo while speaking ─────────────────────
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or self._looks_like_echo(text):
                print(f"[Pipeline] Echo discarded while speaking: {text[:60]}", flush=True)
                if not self.ui.muted and not self._is_speaking:
                    self.ui.set_state("LISTENING")
                continue

            print(f"[Pipeline] 🧠 Processing: {text}", flush=True)
            self.ui.write_log(f"You: {text}")
            self._last_user_text = text
            self.jarvis.note_user_activity()
            await self._converse(text)
            if not self.ui.muted and not self._is_speaking:
                self.ui.set_state("LISTENING")

    async def _converse(self, user_text: str) -> None:
        """One full user turn: LLM ⇄ tools until final answer."""
        low = user_text.strip().lower()
        if any(cmd in low for cmd in ("change api key", "update api key", "change key", "settings", "open settings", "quota reached", "api key setting")):
            self.ui.write_log("SYS: Opening API Key & System Settings overlay...")
            self.say("Opening system settings, sir. You can update your API key or provider now.")
            self.ui.prompt_reconfig()
            return

        self.history.append({"role": "user", "content": user_text})
        self._trim_history()

        for _round in range(MAX_TOOL_ROUNDS):
            if self._interrupted:
                return

            reply_content, tool_calls = await self._stream_reply(self.history)

            if tool_calls:
                # record assistant tool-call turn (content must be None for Gemini function calls)
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": _ensure_json_str(tc["function"].get("arguments", {})),
                            },
                            **({"extra_content": tc["extra_content"]} if "extra_content" in tc else {}),
                        }
                        for tc in tool_calls
                    ],
                }
                self.history.append(assistant_msg)

                for tc in tool_calls:
                    if self._interrupted:
                        # Clean up trailing assistant message if interrupted before tool responses
                        if self.history and self.history[-1] is assistant_msg:
                            self.history.pop()
                        return
                    name = tc["function"]["name"]
                    args = tc["function"].get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    result = await self.jarvis.run_tool(name, args)
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": name,
                        "content": str(result)[:4000],
                    })
                continue                              # next round with tool results

            if reply_content:
                self.history.append({"role": "assistant", "content": reply_content})
            return                                     # turn complete

    async def _stream_reply(self, messages: list) -> tuple[str, list]:
        """
        Stream one LLM completion; speak complete sentences while streaming.
        Returns (full_content, tool_calls).
        """
        loop = asyncio.get_running_loop()
        content_buf   = ""
        sent_buf      = ""
        tool_calls: list = []
        gen_error: list = []

        def _extract_sentences(buf: str) -> tuple[list[str], str]:
            outs = []
            while True:
                m = _SENT_END.search(buf)
                if not m:
                    break
                s = buf[: m.start() + 1].strip()
                buf = buf[m.end():]
                if s:
                    outs.append(s)
            # hard-split very long unpunctuated buffers at a comma
            if len(buf) > 260:
                idx = buf.rfind(", ")
                if idx > 40:
                    outs.append(buf[: idx + 1].strip())
                    buf = buf[idx + 2:]
            return outs, buf

        def _pump():
            nonlocal content_buf, sent_buf, tool_calls
            try:
                for ev in stream_chat(messages, tools=self.tools, timeout=90,
                                      max_tokens=600):
                    if self._interrupted:
                        break
                    if ev["type"] == "delta":
                        content_buf += ev["text"]
                        sent_buf    += ev["text"]
                        sents, sent_buf = _extract_sentences(sent_buf)
                        for s in sents:
                            self.say(s)
                    elif ev["type"] == "done":
                        tool_calls = ev.get("tool_calls") or []
            except Exception as e:
                gen_error.append(e)

        await loop.run_in_executor(None, _pump)

        if gen_error and not content_buf:
            err = str(gen_error[0])
            is_auth_or_quota = any(k in err.lower() for k in (
                "api key not valid", "1007", "quota", "resource_exhausted",
                "insufficient_quota", "rate limit", "429", "exceeded your current quota"
            ))
            if is_auth_or_quota:
                self.ui.write_log("ERR: API key or Quota limit reached. Opening Settings overlay...")
                self.say("Sir, the API key quota has been reached. Please update your key in Settings.")
                self.ui.prompt_reconfig()
            else:
                self.ui.write_log(f"ERR: {err[:160]}")
                self.say(f"Sorry sir, the language model call failed. {err[:140]}")
            return "", []

        # flush tail
        tail = sent_buf.strip()
        if tail:
            self.say(tail)

        if gen_error:
            print(f"[Pipeline] Stream error (partial): {gen_error[0]}")

        return content_buf, [] if self._interrupted else tool_calls

    # ── helpers ──────────────────────────────────────────────────────────

    def _trim_history(self) -> None:
        if len(self.history) > self.MAX_HISTORY:
            dropped = len(self.history) - self.MAX_HISTORY
            keep_from = dropped
            # Ensure history always starts with a 'user' turn
            while keep_from < len(self.history) and self.history[keep_from]["role"] != "user":
                keep_from += 1
            if keep_from < len(self.history):
                self.history = self.history[keep_from:]

    _ECHO_MARKERS = (
        "jarvis,", "hey jarvis", "ok jarvis",
    )

    def _looks_like_echo(self, text: str) -> bool:
        """
        Discard transcriptions that arrive while our own TTS answer is still
        being spoken or match our recently spoken sentences (speaker bleed).
        """
        with self._speaking_lock:
            speaking = self._is_speaking
        if speaking:
            return True
        last = getattr(self, "_last_spoken_text", "").strip().lower()
        if last and text:
            lt = text.strip().lower()
            if lt in last or last in lt:
                return True
            w1 = set(lt.split())
            w2 = set(last.split())
            overlap = w1.intersection(w2)
            if len(overlap) >= 3 or (len(w1) > 0 and len(overlap) / len(w1) >= 0.6):
                return True
        return False

    # vision helper reused by screen_process tool --------------------------------

    async def analyze_image(self, image_bytes: bytes, mime_type: str, question: str) -> str:
        """Ask the configured (vision-capable) model about an image."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, describe_image, image_bytes, mime_type, question,
        )


def encode_image_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")

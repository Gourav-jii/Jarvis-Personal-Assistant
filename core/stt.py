"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import re
import numpy as np


_ARABIC_URDU_RE   = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')
_LEADING_MATRA_RE = re.compile(r'(?:^|\s)[\u093E-\u094C\u0962\u0963]')
_NON_STD_DEV_RE   = re.compile(r'[\u093B\u094E\u094F\u0955-\u0957]')


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base", language: str | None = None):
        import os
        from faster_whisper import WhisperModel
        print(f"[STT] Loading Whisper '{model_name}'…")
        try:
            import torch
            device  = "cuda" if torch.cuda.is_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
        except Exception:
            device, compute = "cpu", "int8"

        try:
            self._model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as _first_err:
            # Offline flag set but model not cached yet → clear flags and download once.
            # Keywords cover multiple huggingface_hub error message variants across versions.
            _e = str(_first_err).lower()
            _offline_keywords = (
                "offline", "not found", "cache", "localentry",
                "does not exist", "outgoing", "local_files_only",
            )
            if any(k in _e for k in _offline_keywords):
                print(f"[STT] Whisper '{model_name}' not in local cache — downloading (one-time, internet required)…")
                os.environ.pop("HF_HUB_OFFLINE",      None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE",  None)
                try:
                    self._model = WhisperModel(model_name, device=device, compute_type=compute)
                except Exception as _dl_err:
                    raise RuntimeError(
                        f"Whisper '{model_name}' model download failed.\n"
                        f"Internet access is required the first time to download the speech model (~75–290 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            else:
                raise

        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        print(f"[STT] Whisper '{model_name}' ready ({device})")

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            if isinstance(audio, np.ndarray):
                audio = audio.flatten().astype(np.float32)
            else:
                audio = np.asarray(audio, dtype=np.float32).flatten()
            if len(audio) == 0:
                return ""

            # Check RMS energy — if silence / faint noise floor, skip Whisper entirely
            rms = float(np.sqrt(np.mean(np.square(audio)) + 1e-12))
            if rms < 0.008:
                return ""

            target_lang = self._language
            segments, info = self._model.transcribe(
                audio,
                language=target_lang,
                initial_prompt="Hello Jarvis, namaste",
                beam_size=1,                       # greedy — 2-3x faster
                best_of=1,
                condition_on_previous_text=False,  # no hallucinations, faster
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )

            # If auto-detected language is Urdu / Arabic script, reroute to Hindi
            detected = getattr(info, "language", None)
            if target_lang is None and detected in ("ur", "ar", "fa", "ps", "sd"):
                target_lang = "hi"
                segments, _ = self._model.transcribe(
                    audio,
                    language="hi",
                    initial_prompt="नमस्ते, मैं जार्विस हूँ।",
                    beam_size=1,
                    best_of=1,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 300},
                )

            valid_texts = []
            for s in segments:
                # Discard segments flagged as non-speech / noise
                if getattr(s, "no_speech_prob", 0.0) > 0.45:
                    continue
                # Discard segments with extremely low confidence
                if getattr(s, "avg_logprob", 0.0) < -1.15:
                    continue
                # Discard repetitive loops
                if getattr(s, "compression_ratio", 1.0) > 2.4:
                    continue
                st = s.text.strip()
                if st:
                    valid_texts.append(st)

            text = " ".join(valid_texts).strip()

            # Discard Arabic/Urdu script, non-standard Devanagari extensions, or isolated matras
            if _ARABIC_URDU_RE.search(text) or _NON_STD_DEV_RE.search(text) or _LEADING_MATRA_RE.search(text):
                print(f"[STT] Discarded gibberish/hallucination: {repr(text)}")
                return ""

            return text
        except Exception as e:
            print(f"[STT] Transcription error: {e}")
            raise


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import Model, KaldiRecognizer
        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False

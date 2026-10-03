"""diktat.py — standalone Tkinter dictation app.

Records from a selectable microphone using WebRTC VAD, transcribes each
utterance with Whisper, and lets you play back the last recording.
Also imports and transcribes audio files (wav, mp3, m4a).

Dependencies: openai-whisper, sounddevice, webrtcvad-wheels, numpy,
              soundfile, pydub, imageio-ffmpeg
No system ffmpeg needed — imageio-ffmpeg supplies a bundled binary.
"""

import io
import os
import queue
import ssl
import subprocess
import threading
import tkinter as tk
import warnings
from tkinter import ttk, scrolledtext, filedialog

# ── Corporate proxy SSL bypass (must be before any network-capable imports) ───
ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings("ignore", message="Unverified HTTPS")
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

import imageio_ffmpeg
import numpy as np
import sounddevice as sd
import soundfile as sf
import webrtcvad
import whisper

# ── Constants ────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 480
VAD_AGGRESSIVENESS = 2
SILENCE_FRAMES = 25   # ~750 ms of silence → end of utterance
MIN_SPEECH_FRAMES = 5

LANGUAGES = {
    "Auto-detect": None,
    "English": "en",
    "German": "de",
    "Spanish": "es",
    "French": "fr",
    "Italian": "it",
    "Dutch": "nl",
    "Polish": "pl",
    "Hindi": "hi",
    "Chinese": "zh",
}

WHISPER_SIZES = ["tiny", "base", "small", "medium", "large"]


def _decode_audio(path: str) -> np.ndarray:
    """Decode any audio file to float32 mono 16 kHz PCM using bundled ffmpeg."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-i", path,
        "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").splitlines()[-1])
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# ── App ───────────────────────────────────────────────────────────────────────
class DiktatApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diktat — Live Transcription")
        self.resizable(True, True)

        self._model: whisper.Whisper | None = None
        self._model_name = tk.StringVar(value="base")
        self._recording = False
        self._last_wav: bytes | None = None   # raw WAV bytes of last utterance
        self._audio_segments: list[np.ndarray] = []  # all PCM chunks in session
        self._audio_q: queue.Queue = queue.Queue()
        self._stream = None

        self._build_ui()
        self._refresh_mics()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Mic:").grid(row=0, column=0, sticky="w")
        self._mic_var = tk.StringVar()
        self._mic_cb = ttk.Combobox(top, textvariable=self._mic_var, width=38, state="readonly")
        self._mic_cb.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(top, text="Language:").grid(row=1, column=0, sticky="w")
        self._lang_var = tk.StringVar(value="Auto-detect")
        lang_cb = ttk.Combobox(top, textvariable=self._lang_var,
                               values=list(LANGUAGES.keys()), width=18, state="readonly")
        lang_cb.grid(row=1, column=1, sticky="w", padx=4)

        ttk.Label(top, text="Model:").grid(row=1, column=2, sticky="w", padx=(12, 0))
        model_cb = ttk.Combobox(top, textvariable=self._model_name,
                                values=WHISPER_SIZES, width=8, state="readonly")
        model_cb.grid(row=1, column=3, sticky="w", padx=4)

        top.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", **pad)

        self._rec_btn = ttk.Button(btn_row, text="⏺  Record", command=self._toggle_record)
        self._rec_btn.pack(side="left", padx=2)

        self._play_btn = ttk.Button(btn_row, text="▶  Play all", command=self._play_all,
                                    state="disabled")
        self._play_btn.pack(side="left", padx=2)

        self._stop_play_btn = ttk.Button(btn_row, text="⏹  Stop play", command=sd.stop,
                                         state="disabled")
        self._stop_play_btn.pack(side="left", padx=2)

        self._import_btn = ttk.Button(btn_row, text="📂  Import file…", command=self._import_file)
        self._import_btn.pack(side="left", padx=2)

        ttk.Button(btn_row, text="🗑  Clear", command=self._clear_log).pack(side="left", padx=2)

        self._load_btn = ttk.Button(btn_row, text="Load model", command=self._load_model_async)
        self._load_btn.pack(side="left", padx=2)

        self._status = ttk.Label(btn_row, text="Model not loaded", foreground="grey")
        self._status.pack(side="left", padx=8)

        self._dot = tk.Label(btn_row, text="●", foreground="grey", font=("", 14))
        self._dot.pack(side="right", padx=4)

        self._log = scrolledtext.ScrolledText(self, wrap="word", height=18, state="disabled")
        self._log.pack(fill="both", expand=True, **pad)
        self._log.tag_config("ts", foreground="#888888")
        self._log.tag_config("text", foreground="#1a1aff")
        self._log.tag_config("info", foreground="#888888", font=("", 9, "italic"))
        self._log.tag_config("err", foreground="red")

    # ── Mic list ──────────────────────────────────────────────────────────────
    def _refresh_mics(self):
        devices = sd.query_devices()
        mics = [(i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
        self._mic_map = {d["name"]: i for i, d in enumerate(devices) if d["max_input_channels"] > 0}
        self._mic_cb["values"] = [name for _, name in mics]
        default = sd.default.device[0]
        if default is not None and default < len(devices):
            self._mic_var.set(devices[default]["name"])
        elif mics:
            self._mic_var.set(mics[0][1])

    # ── Model loading ─────────────────────────────────────────────────────────
    def _load_model_async(self):
        self._load_btn.config(state="disabled")
        self._set_status("Loading model…", "orange")
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        try:
            name = self._model_name.get()
            model_dir = None
            self._model = whisper.load_model(name, download_root=model_dir)
            self.after(0, lambda: self._set_status(f"Model '{name}' ready", "green"))
        except Exception as exc:
            self.after(0, lambda: self._set_status(f"Error: {exc}", "red"))
        finally:
            self.after(0, lambda: self._load_btn.config(state="normal"))

    # ── Record / Stop ─────────────────────────────────────────────────────────
    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._model is None:
            self._log_append("Load a model first.", "err")
            return
        self._recording = True
        self._rec_btn.config(text="⏹  Stop")
        self._play_btn.config(state="disabled")
        self._set_dot("green")
        threading.Thread(target=self._record_loop, daemon=True).start()

    def _stop_recording(self):
        self._recording = False
        self._rec_btn.config(text="⏺  Record")
        if self._last_wav:
            self._play_btn.config(state="normal")
        self._set_dot("grey")

    def _record_loop(self):
        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        mic_index = self._mic_map.get(self._mic_var.get())

        speech_buf: list[bytes] = []
        silence_count = 0
        in_speech = False

        def callback(indata, frames, time_info, status):
            self._audio_q.put(bytes(indata))

        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                               dtype="int16", channels=1, device=mic_index,
                               callback=callback):
            while self._recording:
                try:
                    frame = self._audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                is_speech = vad.is_speech(frame, SAMPLE_RATE)

                if is_speech:
                    if not in_speech:
                        in_speech = True
                        self.after(0, lambda: self._set_dot("lime green"))
                    speech_buf.append(frame)
                    silence_count = 0
                else:
                    if in_speech:
                        speech_buf.append(frame)
                        silence_count += 1
                        if silence_count >= SILENCE_FRAMES:
                            if len(speech_buf) >= MIN_SPEECH_FRAMES:
                                self.after(0, lambda: self._set_dot("orange"))
                                self._transcribe_utterance(speech_buf[:])
                            speech_buf.clear()
                            silence_count = 0
                            in_speech = False
                            self.after(0, lambda: self._set_dot("green"))

        # flush remaining
        if in_speech and len(speech_buf) >= MIN_SPEECH_FRAMES:
            self._transcribe_utterance(speech_buf)

    # ── Transcription ─────────────────────────────────────────────────────────
    def _transcribe_utterance(self, frames: list[bytes]):
        pcm = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
        self._save_last_wav(pcm)

        lang_key = self._lang_var.get()
        lang = LANGUAGES[lang_key]

        try:
            result = self._model.transcribe(pcm, language=lang, fp16=False)
            text = result["text"].strip()
            detected = result.get("language", "?")
            label = f"[{detected}] " if lang is None else ""
            self.after(0, lambda t=text, lb=label: self._log_append(lb + t, "text"))
        except Exception as exc:
            self.after(0, lambda e=exc: self._log_append(f"Transcription error: {e}", "err"))

    # ── File import ───────────────────────────────────────────────────────────
    def _import_file(self):
        if self._model is None:
            self._log_append("Load a model first.", "err")
            return
        path = filedialog.askopenfilename(
            title="Import audio file",
            filetypes=[("Audio files", "*.wav *.mp3 *.m4a"), ("All files", "*.*")],
        )
        if not path:
            return
        self._log_append(f"Importing: {os.path.basename(path)}", "info")
        self._import_btn.config(state="disabled")
        threading.Thread(target=self._transcribe_file, args=(path,), daemon=True).start()

    def _transcribe_file(self, path: str):
        try:
            pcm = _decode_audio(path)
            self._save_last_wav(pcm)

            lang = LANGUAGES[self._lang_var.get()]
            result = self._model.transcribe(pcm, language=lang, fp16=False)
            text = result["text"].strip()
            detected = result.get("language", "?")
            label = f"[{detected}] " if lang is None else ""
            self.after(0, lambda t=text, lb=label: self._log_append(lb + t, "text"))
        except Exception as exc:
            self.after(0, lambda e=exc: self._log_append(f"Import error: {e}", "err"))
        finally:
            self.after(0, lambda: self._import_btn.config(state="normal"))

    # ── WAV save / playback ───────────────────────────────────────────────────
    def _save_last_wav(self, pcm: np.ndarray):
        buf = io.BytesIO()
        sf.write(buf, pcm, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        self._last_wav = buf.getvalue()
        self._audio_segments.append(pcm)
        self.after(0, lambda: self._play_btn.config(state="normal"))

    def _play_all(self):
        if not self._audio_segments:
            return
        def _play():
            self.after(0, lambda: self._rec_btn.config(state="disabled"))
            self.after(0, lambda: self._play_btn.config(state="disabled"))
            self.after(0, lambda: self._stop_play_btn.config(state="normal"))
            combined = np.concatenate(self._audio_segments)
            sd.play(combined, SAMPLE_RATE)
            try:
                while sd.get_stream().active:
                    sd.sleep(100)
            except Exception:
                pass
            finally:
                self.after(0, lambda: self._rec_btn.config(state="normal"))
                self.after(0, lambda: self._play_btn.config(state="normal"))
                self.after(0, lambda: self._stop_play_btn.config(state="disabled"))
        threading.Thread(target=_play, daemon=True).start()

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")
        self._audio_segments.clear()
        self._last_wav = None
        self._play_btn.config(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _set_status(self, msg: str, color: str = "black"):
        self._status.config(text=msg, foreground=color)

    def _set_dot(self, color: str):
        self._dot.config(foreground=color)

    def _log_append(self, text: str, tag: str = "text"):
        self._log.config(state="normal")
        self._log.insert("end", text + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")


if __name__ == "__main__":
    app = DiktatApp()
    app.mainloop()

# Diktat - offline transcription app

A standalone desktop dictation app built with Tkinter. Records from a microphone using voice activity detection (VAD), transcribes speech with OpenAI Whisper on your own computer, and can also import and transcribe audio files.

Internet access is only needed for initial Whisper model download.

No system ffmpeg installation needed — a bundled binary is provided via `imageio-ffmpeg`.

## Features

- Live microphone recording with automatic speech/silence detection
- Transcription via OpenAI Whisper (runs fully offline after model download)
- Import and transcribe audio files (`.wav`, `.mp3`, `.m4a`)
- Playback of the last recorded or imported audio
- Language selection or auto-detect
- Choice of Whisper model size (tiny → large)

<img width="716" height="435" alt="diktat" src="https://github.com/user-attachments/assets/8d0b5ece-eb92-4b26-a2f6-15bd63a43622" />

## Requirements

Python 3.11+ is recommended.

Install dependencies:

```
pip install openai-whisper sounddevice webrtcvad-wheels numpy soundfile imageio-ffmpeg
```

> On Windows, `webrtcvad-wheels` is preferred over `webrtcvad` as it ships pre-built binaries.

## Usage

```
python diktat.py
```

### Workflow

1. Select your microphone from the **Mic** dropdown.
2. Select a language (or leave on **Auto-detect**).
3. Choose a Whisper model size and click **Load model**.
   - Models are downloaded once and cached in `%USERPROFILE%\.cache\whisper`.
   - `base` is a good starting point — fast and reasonably accurate.
4. Click **⏺ Record** to start listening. The app detects speech automatically and transcribes each utterance when you pause.
5. Click **⏹ Stop** to end the session.

### Buttons

| Button | Description |
|---|---|
| ⏺ Record / ⏹ Stop | Toggle live recording |
| ▶ Play last | Play back the last recorded or imported audio |
| ⏹ Stop play | Stop playback immediately |
| 📂 Import file… | Import a `.wav`, `.mp3`, or `.m4a` file for transcription |
| 🗑 Clear | Clear the transcript log |
| Load model | Download (if needed) and load the selected Whisper model |

### Status indicator

The coloured dot (●) in the top-right shows the current state:

| Colour | Meaning |
|---|---|
| Grey | Idle |
| Green | Recording, waiting for speech |
| Lime green | Speech detected |
| Orange | Transcribing |

## Model sizes

| Model | VRAM | Speed | Accuracy |
|---|---|---|---|
| tiny | ~1 GB | fastest | lowest |
| base | ~1 GB | fast | good |
| small | ~2 GB | moderate | better |
| medium | ~5 GB | slow | very good |
| large | ~10 GB | slowest | best |

Models run on CPU if no compatible GPU is available (`fp16=False` is set automatically).

## Notes

- Recording and playback are mutually exclusive — buttons are disabled accordingly.
- The last audio (recorded or imported) is always available for playback until a new one replaces it.
- Transcription of imported files runs in a background thread and does not block the UI.

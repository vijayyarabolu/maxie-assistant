"""Voice on the Mac: all local. Mic -> VAD -> Whisper (MLX) -> hub -> Kokoro -> speakers.

`python -m maxie.voice` runs the loop (launchd: dev.maxie.voice). Nothing leaves the Mac
before the wake phrase: audio is only transcribed locally, and only the text after "Hey Max" goes to
the hub on 127.0.0.1.
"""

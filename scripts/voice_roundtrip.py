"""Language check: Maxie's voice (Kokoro, the voice from Settings) read back by Whisper (language detection
on, so a non-English sound shows up as another language). Each sentence passes when Whisper hears English and the
words match closely. Also prints the phonemes Kokoro was given, to spot an odd phonemizer fallback.

Usage: .venv/bin/python scripts/voice_roundtrip.py [--voice af_jessica] [--out f.json]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re

import numpy as np

SENTENCES = (
    "Today you have Dussehra, all day, on your Home calendar.",
    "Tomorrow at ten you have a call with Priya Raghavan.",
    "Diwali is on November eighth this year.",
    "Your next meeting is at three with the design team.",
    "Remind me to call Amma at six in the evening.",
    "Holi falls in March, and Navratri ends with Dussehra.",
    "It's seventy two degrees and sunny in Austin right now.",
    "You have two new emails, one from Venkatesh and one from LinkedIn.",
    "Pongal and Sankranti are both in the middle of January.",
    "I added milk, curd and paneer to your groceries list.",
    "Your flight to Hyderabad leaves at six fifteen in the morning.",
    "Ganesh Chaturthi was on the twenty seventh of August.",
    "Playing something calm from your Spotify playlist.",
    "The interview with Anjali Sharma moved to Thursday at two.",
    "Onam is a harvest festival celebrated in Kerala.",
    "You walked eight thousand steps today, a little more than yesterday.",
    "Raksha Bandhan is on the ninth of August next year.",
    "Your note from yesterday says to renew the car insurance.",
    "Karthik said he will be late, around seven thirty.",
    "Good night, Alex. Your alarm is set for seven.",
)
PASS_RATIO = 0.8
# Whisper writes numbers as digits ("7:30", "8,000"): how a number is written is not what this checks
NUMBER_WORDS = frozenset("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                         "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty "
                         "ninety hundred thousand eighth ninth seventh twenty-seventh".split())


def _norm(text: str) -> str:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return " ".join(w for w in words if w not in NUMBER_WORDS and not re.fullmatch(r"\d+(?:st|nd|rd|th)?", w))


def run(voice: str) -> list[dict]:
    import mlx_whisper
    from scipy.signal import resample_poly

    from maxie.voice.speech import MODELS, TTS
    tts = TTS(voice=voice)
    rows = []
    for text in SENTENCES:
        samples, rate = tts.synth(text)
        audio = resample_poly(samples, 16000, rate).astype(np.float32)
        out = mlx_whisper.transcribe(audio, path_or_hf_repo=MODELS["base"], fp16=True, temperature=0.0,
                                     condition_on_previous_text=False)  # no language given: Whisper says what it hears
        heard = (out.get("text") or "").strip()
        ratio = difflib.SequenceMatcher(None, _norm(text), _norm(heard)).ratio()
        phonemes = tts.kokoro.tokenizer.phonemize(text, "en-us") if tts.kokoro else ""
        rows.append({"said": text, "heard": heard, "language": out.get("language"), "match": round(ratio, 2),
                     "ok": out.get("language") == "en" and ratio >= PASS_RATIO, "phonemes": phonemes})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="af_jessica")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = run(args.voice)
    for r in rows:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['language']} {r['match']:.2f}  {r['said']}\n       heard: {r['heard']}\n       /{r['phonemes']}/")
    print(f"{sum(r['ok'] for r in rows)}/{len(rows)} passed")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()

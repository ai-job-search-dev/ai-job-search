#!/usr/bin/env python3
"""Transcribe an audio/video file to a timestamped text file with faster-whisper.

Usage: transcribe <input> [output.txt] [--model small.en]
Output defaults to <input>.txt next to the input file.

Installed as the `transcribe` command by tools/install_transcribe.sh, which copies this
file to ~/.local/share/whisper/. Used by /outcome to transcribe call recordings locally.
"""
import argparse
import os
import av
import numpy as np
from faster_whisper import WhisperModel


def decode(path, sr=16000):
    # Decode with PyAV directly: faster-whisper 1.2.1's own decoder is incompatible with av 19.
    chunks = []
    with av.open(path) as c:
        rs = av.AudioResampler(format="s16", layout="mono", rate=sr)
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
        for f in rs.resample(None):
            chunks.append(f.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) / 32768.0


p = argparse.ArgumentParser()
p.add_argument("input")
p.add_argument("output", nargs="?")
p.add_argument("--model", default="small.en", help="e.g. small.en, medium.en, small (multilingual, for Portuguese)")
a = p.parse_args()
out = a.output or os.path.splitext(a.input)[0] + ".txt"

audio = decode(a.input)
print(f"decoded {len(audio) / 16000:.0f}s of audio", flush=True)
m = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 4) - 2))
segs, _ = m.transcribe(audio, vad_filter=True, beam_size=1)
with open(out, "w") as f:
    for s in segs:
        f.write(f"[{int(s.start // 60):02d}:{int(s.start % 60):02d}] {s.text.strip()}\n")
        f.flush()
print(f"wrote {out}", flush=True)

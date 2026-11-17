"""Chatterbox TTS helper (runs in its own environment; the worker calls it as a subprocess).

    uv run --project tools/chatterbox python tools/chatterbox/speak.py job.json

job.json: {"lines": [{"text": "...", "out": "C:/.../line-000.wav"}], "voiceRef": null | "C:/.../ref.wav",
           "exaggeration": 0.5, "cfgWeight": 0.5}
Prints one JSON line per finished line: {"out", "seconds", "audioSec"}.

Weights: ResembleAI/chatterbox on Hugging Face (MIT), pinned revision (rule 9). Output carries Resemble's
inaudible Perth watermark (AI-generated audio). Only clone a voice you have the rights to (your own).
"""

import json
import sys
import time

import torch
import torchaudio
from huggingface_hub import snapshot_download

REPO = "ResembleAI/chatterbox"
REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"


def main(path: str) -> None:
    from chatterbox.tts import ChatterboxTTS

    job = json.load(open(path, encoding="utf-8"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t = time.time()
    ckpt = snapshot_download(REPO, revision=REVISION, allow_patterns=[
        "ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"])
    model = ChatterboxTTS.from_local(ckpt, device)
    print(json.dumps({"loaded": round(time.time() - t, 1), "device": device}), flush=True)
    for line in job["lines"]:
        t = time.time()
        wav = model.generate(line["text"], audio_prompt_path=job.get("voiceRef"),
                             exaggeration=float(job.get("exaggeration", 0.5)), cfg_weight=float(job.get("cfgWeight", 0.5)))
        torchaudio.save(line["out"], wav.cpu(), model.sr)
        print(json.dumps({"out": line["out"], "seconds": round(time.time() - t, 2),
                          "audioSec": round(wav.shape[-1] / model.sr, 2)}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

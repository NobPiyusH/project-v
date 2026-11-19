"""ACE-Step 1.5 (MIT) music generator on Modal: an A/B candidate for Lyria (19 Nov 2026), off by default.

Deploy:  uv run modal deploy workers/modal_apps/ace_music.py   (not named acestep.py: that would hide the acestep package)
Called by the media worker via modal.Cls.from_name("projectv-acestep", "AceStep").

2B turbo DiT (8 steps) + the 1.7B "5Hz" planner LM, on an L4 (24 GB: both fit without offload or quantisation).
One call renders all takes of a video in one batch, so the models load once per video.
Weights come from Hugging Face (ACE-Step/Ace-Step1.5) into a Modal Volume (CLAUDE.md rule 9: never from Google Drive).
The container shuts down 30 s after the last call (CLAUDE.md rule 8).
"""

import inspect
import os
import time

import modal

APP_NAME = "projectv-acestep"
REPO = "https://github.com/ace-step/ACE-Step-1.5.git"
COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"   # main on 29 Aug 2026; pinned so a rebuild is reproducible
SRC = "/opt/acestep"
CKPT = "/cache/acestep/checkpoints"
DIT = "acestep-v15-turbo"
LM = "acestep-5Hz-lm-1.7B"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1", "build-essential")
    .run_commands(
        f"git clone {REPO} {SRC} && cd {SRC} && git checkout {COMMIT}",
        # the repo pins these CUDA 12.8 builds for Linux (they live on the PyTorch index, not PyPI)
        f"pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url {TORCH_INDEX}",
        # nano-vllm (the LM's fast backend) ships inside the repo, not on PyPI
        f"pip install {SRC}/acestep/third_parts/nano-vllm",
        f"pip install -e {SRC} --extra-index-url {TORCH_INDEX}",
    )
    .env({"ACESTEP_CHECKPOINTS_DIR": CKPT, "ACESTEP_PROJECT_ROOT": SRC, "HF_HOME": "/cache/hf"})
)

hf_cache = modal.Volume.from_name("projectv-hf-cache", create_if_missing=True)


def _call(fn, **kwargs):
    """Call with only the keyword arguments this version of ACE-Step accepts (its API is still moving)."""
    params = inspect.signature(fn).parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)
    return fn(**{k: v for k, v in kwargs.items() if k in params})


@app.cls(
    gpu="L4",
    image=image,
    volumes={"/cache": hf_cache},
    timeout=1800,
    scaledown_window=30,   # shut down 30 s after the last call (CLAUDE.md rule 8)
)
class AceStep:
    @modal.enter()
    def load(self) -> None:
        t = time.time()
        os.makedirs(CKPT, exist_ok=True)
        # the DiT handler looks for <project root>/checkpoints: point it at the volume
        link = os.path.join(SRC, "checkpoints")
        if not os.path.exists(link):
            os.symlink(CKPT, link)
        os.chdir(SRC)

        from acestep.model_downloader import ensure_main_model
        ok, msg = _call(ensure_main_model, checkpoints_dir=CKPT, prefer_source="huggingface")
        if not ok:
            raise RuntimeError(f"ACE-Step weights download failed: {msg}")
        hf_cache.commit()

        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler
        self.dit = AceStepHandler()
        status = _call(self.dit.initialize_service, project_root=SRC, config_path=DIT, device="cuda",
                       offload_to_cpu=False, quantization=None)
        print("DiT:", status)
        self.llm = LLMHandler()
        msg, ok = self.llm.initialize(checkpoint_dir=CKPT, lm_model_path=LM, backend="vllm", device="cuda")
        if not ok:
            raise RuntimeError(f"ACE-Step planner LM failed to load: {msg}")
        self.load_seconds = time.time() - t
        print(f"ACE-Step ready in {self.load_seconds:.0f} s")

    @modal.method()
    def generate(self, job: dict) -> dict:
        """job: {caption, durationSec, seeds:[int]} -> {takes:[{seed, wav: bytes, sampleRate}], seconds, loadSeconds}
        All seeds render in one batch (instrumental, turbo 8 steps, shift 3 as the docs recommend for turbo)."""
        import tempfile

        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        t = time.time()
        seeds = [int(s) for s in job["seeds"]]
        params = GenerationParams(
            caption=job["caption"][:512],
            lyrics="[Instrumental]",
            instrumental=True,
            duration=float(job["durationSec"]),
            inference_steps=8,
            shift=3.0,
            thinking=True,
        )
        config = GenerationConfig(batch_size=len(seeds), use_random_seed=False, seeds=seeds, audio_format="wav")
        with tempfile.TemporaryDirectory() as out_dir:
            result = generate_music(self.dit, self.llm, params, config, save_dir=out_dir)
            if not result.success:
                raise RuntimeError(f"ACE-Step generation failed: {result.error or result.status_message}")
            takes = []
            for i, audio in enumerate(result.audios):
                with open(audio["path"], "rb") as f:
                    wav = f.read()
                seed = (audio.get("params") or {}).get("seed", seeds[i] if i < len(seeds) else None)
                takes.append({"seed": int(seed) if seed is not None else None, "wav": wav,
                              "sampleRate": audio.get("sample_rate", 48000)})
        return {"takes": takes, "seconds": time.time() - t, "loadSeconds": getattr(self, "load_seconds", None),
                "metadata": str((result.extra_outputs or {}).get("lm_metadata"))[:2000]}

    @modal.method()
    def info(self) -> dict:
        return {"commit": COMMIT, "dit": DIT, "lm": LM, "load_seconds": getattr(self, "load_seconds", None)}

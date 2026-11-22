# Warm-start pod image (docs/warm-start-plan.md, option A): the pod's slow, identical-every-time boot steps done at build time.
# Baked: Python deps (diffusers, transformers, ...), ComfyUI at the pinned tag with its requirements, ComfyUI-KJNodes.
# NOT baked: model weights (still from Hugging Face at boot), the worker itself (still shipped through SV_BOOT_* env, so worker
# edits never need a rebuild), and any secret. The image is PUBLIC on purpose: nothing in it is private, and a public image
# needs no registry credentials on a rented pod (Vast pods must hold no account secret).
#
# Build NOT on the laptop (C: is small): .github/workflows/pod-image.yml, or docker/pod/build.sh on any Linux box.
# The base must stay the broker's plain image (projectv.gpu.pod-image) so both paths run on the same CUDA/torch.
ARG BASE=runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
FROM ${BASE}

ENV SV_ROOT=/opt/projectv PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_XET=1
# server.py --bake is the SAME code the pod would run at boot (install(), fetch_comfy(), fetch_kjnodes()), so the image and
# the fallback boot path can never drift apart. The pinned tag is in the folder name: a tag bump without a rebuild makes the
# worker ignore this ComfyUI and fetch the new one.
# The worker is bind-mounted for this one RUN only, so it never lands in any image layer (the image is public and holds
# nothing of the studio's own code: only ComfyUI, its nodes and public pip packages).
RUN --mount=type=bind,source=workers/pod/server.py,target=/tmp/bake/server.py \
    python /tmp/bake/server.py --bake && rm -rf /root/.cache

LABEL org.opencontainers.image.source="https://github.com/NobPiyusH/project-v" \
      org.opencontainers.image.description="Project V pod: ComfyUI + nodes + deps, no weights"

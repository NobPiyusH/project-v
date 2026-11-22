#!/usr/bin/env bash
# Builds and pushes the warm-start pod image from a Linux box with Docker (a rented CPU box or CI), never the owner's laptop.
# Usage: IMAGE=ghcr.io/<owner>/projectv-pod TAG=comfy-v0.45.0 docker/pod/build.sh [--push]
# Needs `docker login` for the registry first. Spends nothing by itself; --push uploads to YOUR registry account.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${IMAGE:?set IMAGE, e.g. ghcr.io/<owner>/projectv-pod}"
# Default tag = the ComfyUI tag the worker pins, so the image name says what is inside.
TAG="${TAG:-comfy-$(grep -oP '^COMFY_TAG = "\K[^"]+' workers/pod/server.py)}"
echo "building $IMAGE:$TAG (needs ~25 GB free disk)"
docker build -f docker/pod/Dockerfile -t "$IMAGE:$TAG" .
docker image ls "$IMAGE:$TAG"
if [ "${1:-}" = "--push" ]; then
  docker push "$IMAGE:$TAG"
  echo "pushed. Then set POD_WARM_IMAGE=$IMAGE:$TAG in .env (after a canary says so)."
fi

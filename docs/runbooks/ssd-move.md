# Moving the studio's heavy data to the media SSD (M:)

Why: C: has ~37 GB free (18 Nov 2026) and CLAUDE.md rule 12 says media, model weights and Docker volumes must not
live on C: once the media SSD is installed. Nothing here deletes data until a copy is verified.

## 0. Before you start
- No video may be running: every stage idle (check the dashboard, or `stage_run` has no `RUNNING`).
- Stop the studio: `powershell -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\stop.ps1`

## 1. Fit and prepare the drive (owner)
1. Shut down fully (Shift + Shut down), unplug, open the back panel, fit the M.2 2280 NVMe in the free slot.
2. Boot. Disk Management (Win + X > Disk Management) > the new disk > Initialize as **GPT**.
3. New Simple Volume > NTFS > letter **M:** > label `Media`.
4. Check: `Get-Volume -DriveLetter M` shows ~931 GB free.

## 2. Docker data (Postgres, Temporal, SeaweedFS) -> M:
Docker Desktop keeps every named volume inside one WSL disk image (on C: under
`%LOCALAPPDATA%\Docker\wsl\disk`). Docker Desktop moves it for us, safely:
1. Docker Desktop > Settings > Resources > Advanced > **Disk image location** > Browse > `M:\Docker` > Apply & restart.
2. Wait until Docker says it is running again (the move copies the image; a few minutes).
3. Check: `docker volume ls` still lists `postgres-data`, `temporal-data`, `seaweedfs-data`;
   `docker compose -f infra/docker-compose.yml up -d` then the dashboard opens with every old video.
4. Only after that check: Docker Desktop removes the old image itself; confirm C: free space went up.

## 3. Worker temp files, model cache and uv cache -> M:
Create the folders: `New-Item -ItemType Directory -Force M:\projectv\tmp, M:\projectv\hf, M:\projectv\uv-cache`
Add to `.env` (never committed):
```
PROJECTV_TMP=M:\projectv\tmp
HF_HOME=M:\projectv\hf
UV_CACHE_DIR=M:\projectv\uv-cache
```
`scripts/start.ps1` points the worker's TEMP/TMP at `PROJECTV_TMP` when it is set (FFmpeg renders, downloads and
Blender frames go there instead of C:\Users\...\Temp).

## 4. Tools on M:
- Blender (portable zip from blender.org, current LTS) unzipped to `M:\tools\blender`.
- Later local models (Kokoro, RIFE weights) download into `HF_HOME` from Hugging Face (rule 9).

## 5. Final check
- `powershell -ExecutionPolicy Bypass -File C:\dev\project-v\scripts\start.ps1` -> all four "ok".
- Open an old video in the dashboard: its master plays (SeaweedFS data moved intact).
- `Get-PSDrive C, M`: C: has its space back; M: holds Docker + projectv folders.

## Undo
Docker Desktop > Disk image location back to the old folder; remove the three lines from `.env`.

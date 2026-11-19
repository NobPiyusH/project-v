"""Upload a finished Short to the owner's YouTube channel (YouTube Data API v3, resumable upload).

Runs only from the control plane's PostWorkflow, which starts only when the owner taps Post. Tried once (no automatic
retry), so a lost reply can never post the same Short twice. The video is marked as altered/synthetic content
(``containsSyntheticMedia``) because it is made with AI tools.

Note: Google keeps videos uploaded through an API project that has not passed its audit as *private*; the result
reports the privacy YouTube actually set.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from temporalio import activity

from workers.common import download, log, workdir
from workers.youtube import token

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CATEGORY_FILM = "1"  # Film & Animation


def metadata(req: dict) -> dict:
    """The video's snippet and status, inside YouTube's limits (title 100, description 5000, tags 500 chars)."""
    tags, total = [], 0
    for t in req.get("tags") or []:
        if total + len(t) + 1 > 450:
            break
        tags.append(t)
        total += len(t) + 1
    return {
        "snippet": {"title": (req.get("title") or "")[:100], "description": (req.get("description") or "")[:5000],
                    "tags": tags, "categoryId": CATEGORY_FILM},
        "status": {"privacyStatus": req.get("privacy") or "private", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }


def upload_file(path: str, req: dict, access: str, opener=urllib.request.urlopen) -> dict:
    """Starts a resumable session, then sends the file in one PUT. Returns YouTube's video resource."""
    size = os.path.getsize(path)
    start = urllib.request.Request(UPLOAD_URL, data=json.dumps(metadata(req)).encode(), method="POST", headers={
        "Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
    with opener(start, timeout=60) as r:
        session = r.headers.get("Location")
    if not session:
        raise RuntimeError("YouTube did not open an upload session")
    with open(path, "rb") as f:
        data = f.read()
    put = urllib.request.Request(session, data=data, method="PUT", headers={
        "Authorization": f"Bearer {access}", "Content-Type": "video/mp4", "Content-Length": str(size)})
    with opener(put, timeout=1800) as r:
        return json.loads(r.read().decode())


def reason(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = json.loads(e.read().decode())
            return f"HTTP {e.code}: {body.get('error', {}).get('message', '')}"[:400]
        except Exception:  # noqa: BLE001 - any unreadable body
            return f"HTTP {e.code}"
    return str(e)[:400]


@activity.defn(name="youtube_upload")
def youtube_upload(req: dict) -> dict:
    privacy = req.get("privacy") or "private"
    try:
        access = token.access_token()
        with workdir("youtube") as tmp:
            path = str(download(req["videoUri"], Path(tmp) / "short.mp4"))
            video = upload_file(path, req, access)
        vid = video["id"]
        actual = video.get("status", {}).get("privacyStatus", privacy)
        log.info("Uploaded run %s to YouTube as %s (%s)", req.get("runId"), vid, actual)
        return {"videoId": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": actual, "error": None}
    except Exception as e:  # noqa: BLE001 - the owner sees the reason and taps Post again
        log.warning("YouTube upload for run %s failed: %s", req.get("runId"), reason(e))
        return {"videoId": None, "url": None, "privacy": privacy, "error": reason(e)}

"""Vast 2FA session key from VAST_TOTP_SECRET (same flow as VastProvider.java). Never prints secrets. One attempt, one retry in the next 30 s window."""
import base64, hashlib, hmac, json, re, struct, time, urllib.request, urllib.error


def _env():
    d = {}
    for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            d[m.group(1)] = m.group(2).strip().strip('"')
    return d


def totp(secret, t=None):
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    c = struct.pack(">Q", int((t or time.time()) // 30))
    h = hmac.new(key, c, hashlib.sha1).digest(); o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7fffffff) % 1000000)


def login():
    e = _env()
    for attempt in range(2):
        if attempt:
            time.sleep(31)
        req = urllib.request.Request("https://console.vast.ai/api/v0/tfa/", method="POST",
                                     data=json.dumps({"tfa_method": "totp", "code": totp(e["VAST_TOTP_SECRET"])}).encode(),
                                     headers={"Authorization": "Bearer " + e["VAST_API_KEY"], "Content-Type": "application/json", "User-Agent": "projectv-bakeoff"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=30).read())
            if r.get("session_key"):
                return r["session_key"].strip()
        except urllib.error.HTTPError as ex:
            print("vast 2FA login HTTP", ex.code)
    raise SystemExit("Vast 2FA login failed twice; not retrying (avoid account lock)")

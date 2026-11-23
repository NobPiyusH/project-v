import json, os, re, subprocess
import boto3
V = "d3e53cd1-520e-4dd3-8ecb-b6ed0714aa93"
ORDS = [0, 3, 17, 24, 31]
OUT = r"E:\project-data\outputs\bakeoff\inputs"
q = ("select json_agg(t) from (select s.ord, s.camera_move, s.description, s.prompt_json->>'motionPrompt' as motion, "
     "s.prompt_json->>'suggestedPrompt' as suggested, a.uri_hot as uri from shot s join scene sc on sc.id=s.scene_id "
     "join asset a on a.id=s.keyframe_asset_id where sc.video_id='%s' and s.ord in (%s) order by s.ord) t;" % (V, ",".join(map(str, ORDS))))
out = subprocess.run(["docker", "exec", "projectv-postgres-1", "psql", "-U", "projectv", "-d", "projectv", "-t", "-A", "-c", q],
                     capture_output=True, text=True)
if out.returncode:
    raise SystemExit(out.stderr[:500])
rows = json.loads(out.stdout)
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^(S3_[A-Z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"], aws_access_key_id=env["S3_ACCESS_KEY"],
                  aws_secret_access_key=env["S3_SECRET_KEY"], region_name="us-east-1")
shots = []
for r in rows:
    b, k = re.match(r"s3://([^/]+)/(.*)", r["uri"]).groups()
    p = os.path.join(OUT, "shot%02d.png" % r["ord"])
    s3.download_file(b, k, p)
    prompt = r["motion"] or r["suggested"] or r["description"]
    shots.append({"ord": r["ord"], "image": p, "camera": r["camera_move"], "description": r["description"], "prompt": prompt})
    print(r["ord"], os.path.getsize(p) // 1000, "KB |", r["camera_move"], "|", prompt[:90])
json.dump(shots, open(os.path.join(OUT, "shots.json"), "w"), indent=1)

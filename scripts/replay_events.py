"""Send the SYNTHETIC sample events to the gateway over HTTP (exercises the full path)."""
import argparse
import json
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--gateway", default="http://localhost:8000")
ap.add_argument("--file", default="data/samples/events.jsonl")
a = ap.parse_args()
ok = bad = 0
for line in open(a.file):
    req = urllib.request.Request(f"{a.gateway}/events", data=line.encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req); ok += 1
    except Exception as e:  # noqa: BLE001
        bad += 1; print("failed:", e)
print(json.dumps({"accepted": ok, "failed": bad}))

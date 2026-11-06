import os

import httpx


def make_client():
    """Real API if BACKLOT_API_URL is set, otherwise the in-process synthetic demo. Returns (client, labels, mode)."""
    url = os.getenv("BACKLOT_API_URL")
    if url:
        return httpx.Client(base_url=url, timeout=60), {}, "api"
    from fastapi.testclient import TestClient
    from frontend.demo import build_demo
    app, labels = build_demo()
    return TestClient(app), labels, "demo"

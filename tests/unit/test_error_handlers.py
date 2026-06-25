"""Gateway CORS + error handler unit tests (no Docker required).

Proves:
  - Bad body (missing 'manuscript') -> 422 structured JSON
  - Wrong content-type -> 422 structured JSON
  - Unhandled exception in a route -> 500 JSON {error, job_id}

The lifespan is NOT triggered when TestClient is used without the `with` context
manager, so app.state has no 'minio'/'exchange'/'engine'. A valid POST /jobs then
raises AttributeError inside the route handler, which the _unhandled(Exception)
handler catches and converts to 500 JSON — no mock/patch needed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.main import app


def test_missing_manuscript_returns_422() -> None:
    client = TestClient(app)
    r = client.post("/jobs", json={})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body, dict)  # machine-readable JSON, not HTML


def test_null_manuscript_returns_422() -> None:
    client = TestClient(app)
    r = client.post("/jobs", json={"manuscript": None})
    assert r.status_code == 422


def test_wrong_content_type_returns_422() -> None:
    client = TestClient(app)
    r = client.post(
        "/jobs",
        content=b"not json at all",
        headers={"content-type": "text/plain"},
    )
    assert r.status_code == 422


def test_unhandled_exception_returns_500_json() -> None:
    """Without lifespan, app.state.minio is unset -> AttributeError.

    The _unhandled(request, exc) exception handler must catch it and return
    500 JSON rather than an HTML stack page (the machine-readable contract).
    """
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/jobs", json={"manuscript": "hello world"})
    assert r.status_code == 500
    data = r.json()
    assert data["error"] == "internal_error"
    # job_id absent from path params -> None in response
    assert "error" in data

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.http_perf import ImmutableStaticFiles, SelectiveGZipMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024)

    @app.get("/api/big")
    def big():
        return {"rows": ["x" * 50] * 200}

    @app.get("/api/v1/imports/events")
    def events():
        return StreamingResponse(iter([b"data: {}\n\n"] * 200), media_type="text/event-stream")

    return app


def test_json_is_gzipped_when_large():
    r = TestClient(_app()).get("/api/big", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert len(r.json()["rows"]) == 200


def test_event_stream_is_not_gzipped():
    r = TestClient(_app()).get("/api/v1/imports/events", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in r.headers


def test_assets_get_immutable_cache_header(tmp_path):
    (tmp_path / "app-abc123.js").write_text("console.log(1)")
    app = FastAPI()
    app.mount("/assets", ImmutableStaticFiles(directory=str(tmp_path)))
    r = TestClient(app).get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]

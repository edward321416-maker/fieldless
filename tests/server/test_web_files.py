from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import Settings


class Idle:
    def start(self):
        pass

    def stop(self):
        pass


def test_the_web_app_goes_out_whole_gzipped_and_kept_by_the_browser(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    script = b"console.log('fieldless');" * 400
    (dist / "assets" / "index-AbC123.js").write_bytes(script)
    (dist / "index.html").write_text("<html><script src=/assets/index-AbC123.js></script></html>", encoding="utf-8")
    client = TestClient(create_app(Settings(static_dir=dist), worker=Idle(), preview=lambda *a: None))

    js = client.get("/assets/index-AbC123.js", headers={"accept-encoding": "gzip"})
    assert js.status_code == 200 and js.content == script  # the client undoes the gzip
    assert js.headers["content-encoding"] == "gzip" and js.headers["content-type"].startswith("text/javascript")
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert int(js.headers["content-length"]) < len(script) // 5
    raw = client.get("/assets/index-AbC123.js", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in raw.headers and raw.content == script
    again = client.get("/assets/index-AbC123.js", headers={"if-none-match": js.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    page = client.get("/")
    assert page.status_code == 200 and b"index-AbC123.js" in page.content
    assert page.headers["cache-control"] == "no-cache"  # it names this build's assets
    same = client.get("/", headers={"if-none-match": page.headers["etag"]})
    assert same.status_code == 304 and same.headers["cache-control"] == "no-cache"  # or the old page is kept for an hour
    assert client.get("/assets/missing.js").status_code == 404

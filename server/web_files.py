"""The built web app (web/dist), each file sent whole and, for text, gzipped once and kept.

On the live host (Modal) a file streamed in Starlette's 64 KB chunks came at about 20-65 KB/s, while a response sent
whole came at MB/s: the 1.26 MB app script took 19 s to load (2026-10-29). So every file goes out in one piece. The
asset files' names carry a hash of their content (Vite), so the browser keeps them for a year; index.html, which
names them, is asked for again every time, its "not changed" reply included.
"""
import gzip
import hashlib
import os

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

MEDIA = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
         ".svg": "image/svg+xml", ".json": "application/json", ".txt": "text/plain; charset=utf-8",
         ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon", ".woff2": "font/woff2"}
TEXT = {".js", ".mjs", ".css", ".html", ".svg", ".json", ".txt"}
ASSET_CACHE = "public, max-age=31536000, immutable"  # Vite's hashed asset names
HTML_CACHE = "no-cache"  # index.html names one build's asset files: a stale copy after a redeploy is a blank page
OTHER_CACHE = "public, max-age=3600"


class WholeFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._kept: dict[tuple[str, int], tuple[bytes, bytes | None, str]] = {}

    def _load(self, full_path: str, key: tuple[str, int]) -> tuple[bytes, bytes | None, str]:
        with open(full_path, "rb") as f:
            body = f.read()
        ext = os.path.splitext(full_path)[1].lower()
        zipped = gzip.compress(body, 6, mtime=0) if ext in TEXT and len(body) > 1024 else None
        kept = (body, zipped, '"' + hashlib.md5(body).hexdigest() + '"')
        self._kept[key] = kept
        return kept

    def file_response(self, full_path, stat_result, scope, status_code: int = 200) -> Response:
        full_path = str(full_path)
        key = (full_path, stat_result.st_mtime_ns)
        body, zipped, etag = self._kept.get(key) or self._load(full_path, key)
        ext = os.path.splitext(full_path)[1].lower()
        asked = Headers(scope=scope)
        cache = (HTML_CACHE if ext == ".html" else ASSET_CACHE if f"{os.sep}assets{os.sep}" in full_path else OTHER_CACHE)
        headers = {"ETag": etag, "Vary": "Accept-Encoding", "Cache-Control": cache}
        if asked.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if zipped is not None and "gzip" in asked.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
            body = zipped
        return Response(body, status_code=status_code, headers=headers,
                        media_type=MEDIA.get(ext, "application/octet-stream"))

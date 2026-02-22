#!/usr/bin/env python3
"""
PagerAmp Web Upload Server — HTTP file upload service for music files.

Runs on port 1337. Accepts .mp3, .wav, .m3u uploads to /mmc/music/.
Mobile-friendly drag-and-drop interface.

Usage: python3 upload_server.py [--port PORT] [--dir MUSIC_DIR]
"""

import os
import sys
import json
import cgi
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

MUSIC_DIR = "/mmc/music"
ALLOWED_EXT = {".mp3", ".wav", ".m3u"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

# HTML template path
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "templates")


def get_template():
    """Load the HTML template."""
    path = os.path.join(TEMPLATE_DIR, "index.html")
    try:
        with open(path, "r") as f:
            return f.read()
    except (IOError, OSError):
        return "<html><body><h1>PagerAmp Upload</h1><p>Template not found.</p></body></html>"


def list_music_files(music_dir):
    """List music files with sizes."""
    files = []
    if not os.path.isdir(music_dir):
        return files
    for name in sorted(os.listdir(music_dir)):
        full = os.path.join(music_dir, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in ALLOWED_EXT:
            size = os.path.getsize(full)
            files.append({
                "name": name,
                "size": size,
                "size_str": _format_size(size),
            })
    return files


def _format_size(size):
    if size < 1024:
        return "%d B" % size
    elif size < 1024 * 1024:
        return "%.1f KB" % (size / 1024)
    else:
        return "%.1f MB" % (size / (1024 * 1024))


class UploadHandler(BaseHTTPRequestHandler):
    music_dir = MUSIC_DIR

    def log_message(self, format, *args):
        sys.stderr.write("[upload] %s\n" % (format % args))

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_page()
        elif self.path == "/api/library":
            self._serve_library()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/upload":
            self._handle_upload()
        elif self.path == "/api/delete":
            self._handle_delete()
        else:
            self.send_error(404)

    def _serve_page(self):
        html = get_template()
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_library(self):
        files = list_music_files(self.music_dir)
        data = json.dumps({"files": files}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_response(400, {"error": "Must be multipart/form-data"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_SIZE:
            self._json_response(413, {"error": "File too large (max 50MB)"})
            return

        # Parse multipart form
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            }
        )

        uploaded = []
        errors = []

        # Handle single file or multiple files
        file_items = form["file"] if "file" in form else []
        if not isinstance(file_items, list):
            file_items = [file_items]

        os.makedirs(self.music_dir, exist_ok=True)

        for item in file_items:
            if not hasattr(item, "filename") or not item.filename:
                continue

            filename = os.path.basename(item.filename)
            ext = os.path.splitext(filename)[1].lower()

            if ext not in ALLOWED_EXT:
                errors.append("%s: unsupported format" % filename)
                continue

            # Sanitize filename
            filename = filename.replace(" ", "_")
            dest = os.path.join(self.music_dir, filename)

            try:
                with open(dest, "wb") as f:
                    while True:
                        chunk = item.file.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                uploaded.append(filename)
                self.log_message("Uploaded: %s", filename)
            except (IOError, OSError) as e:
                errors.append("%s: %s" % (filename, str(e)))

        result = {"uploaded": uploaded, "errors": errors}
        self._json_response(200, result)

    def _handle_delete(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._json_response(400, {"error": "Invalid JSON"})
            return

        filename = data.get("filename", "")
        if not filename or "/" in filename or "\\" in filename:
            self._json_response(400, {"error": "Invalid filename"})
            return

        path = os.path.join(self.music_dir, filename)
        if not os.path.isfile(path):
            self._json_response(404, {"error": "File not found"})
            return

        try:
            os.remove(path)
            self._json_response(200, {"deleted": filename})
        except OSError as e:
            self._json_response(500, {"error": str(e)})

    def _json_response(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(port=1337, music_dir=MUSIC_DIR):
    UploadHandler.music_dir = music_dir
    server = HTTPServer(("0.0.0.0", port), UploadHandler)
    sys.stderr.write("PagerAmp upload server on http://0.0.0.0:%d\n" % port)
    sys.stderr.write("Music directory: %s\n" % music_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PagerAmp Upload Server")
    parser.add_argument("--port", type=int, default=1337)
    parser.add_argument("--dir", default=MUSIC_DIR)
    args = parser.parse_args()
    run_server(args.port, args.dir)

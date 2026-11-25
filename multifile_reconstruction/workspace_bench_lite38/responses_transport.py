"""Supply hosted Responses credentials outside the Codex tool sandbox."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@contextmanager
def credential_proxy(endpoint, key, trace, *, extra_fields=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != '/responses':
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(body)
            if extra_fields:
                payload.update(extra_fields)
                body = json.dumps(payload).encode('utf-8')
            record = {'model': payload.get('model'), 'stream': payload.get('stream'),
                      'tool_names': [tool.get('name', tool.get('type')) for tool in payload.get('tools', [])]}
            headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                       'Accept': self.headers.get('Accept', 'text/event-stream')}
            try:
                upstream = urlopen(Request(endpoint, data=body, headers=headers), timeout=600)
            except HTTPError as exc:
                upstream = exc
            except OSError:
                self.send_error(502, 'Model provider connection failed')
                trace.append(record | {'status': 502})
                return
            with upstream:
                trace.append(record | {'status': upstream.code})
                self.send_response(upstream.code)
                self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
                self.end_headers()
                try:
                    for line in upstream:
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()

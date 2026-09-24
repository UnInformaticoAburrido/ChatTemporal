"""Prueba local de Alertmanager → receptor HTTP; nunca envía correo externo."""

import argparse
import json
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Queue


def run(binary: Path) -> None:
    received: Queue[dict] = Queue()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.put(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    receiver = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="chat-alert-test-") as folder:
            config = Path(folder) / "alertmanager.yml"
            config.write_text(f"""route:
  receiver: local-test
  group_wait: 0s
  group_interval: 1s
  repeat_interval: 1h
receivers:
  - name: local-test
    webhook_configs:
      - url: http://127.0.0.1:{receiver.server_port}/alerts
        send_resolved: true
""")
            with subprocess.Popen([str(binary), "--config.file=" + str(config), "--storage.path=" + folder,
                                   f"--web.listen-address=127.0.0.1:{port}", "--cluster.listen-address="],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
                try:
                    base = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            with urllib.request.urlopen(base + '/-/ready', timeout=1):
                                break
                        except (OSError, urllib.error.URLError):
                            if time.monotonic() >= deadline:
                                raise RuntimeError("Alertmanager no disponible") from None
                            time.sleep(.1)
                    now = datetime.now(UTC)
                    alert = {"labels": {"alertname": "ChatLocalDeliveryTest", "severity": "high"},
                             "startsAt": (now - timedelta(seconds=5)).isoformat(),
                             "endsAt": (now + timedelta(minutes=1)).isoformat()}
                    def send() -> None:
                        request = urllib.request.Request(base + '/api/v2/alerts', json.dumps([alert]).encode(),
                                                         {'Content-Type': 'application/json'})
                        with urllib.request.urlopen(request, timeout=3) as response:
                            assert response.status == 200
                    send()
                    firing = received.get(timeout=15)
                    assert firing['status'] == 'firing'
                    assert firing['alerts'][0]['labels']['alertname'] == 'ChatLocalDeliveryTest'
                    alert['endsAt'] = datetime.now(UTC).isoformat()
                    send()
                    assert received.get(timeout=15)['status'] == 'resolved'
                finally:
                    process.terminate()
                    process.wait(timeout=5)
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=2)
    print('Alertmanager: firing y resolved recibidos por HTTP local.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    run(parser.parse_args().binary.resolve())

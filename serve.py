#!/usr/bin/env python3
"""
armsim TCP serve loop -- minimal pattern mirroring stdlib/http_server.rail.
Per request: write raw bytes to /tmp/armsim_req.txt, exec the compiled Rail
handler at /tmp/armsim_handler, stream stdout back to the client.

Usage:  python3 serve.py [port]
"""
import os, socket, subprocess, sys, threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7071
HANDLER = "/tmp/armsim_handler"
REQ_FILE = "/tmp/armsim_req.txt"
REQ_LOCK = threading.Lock()


def handle(conn):
    try:
        data = conn.recv(16384)
        if not data:
            return
        with REQ_LOCK:
            with open(REQ_FILE, "wb") as fh:
                fh.write(data)
            r = subprocess.run([HANDLER], capture_output=True, timeout=5)
        conn.sendall(r.stdout)
    except Exception as exc:
        body = f"500 armsim error: {exc}".encode()
        conn.sendall(
            b"HTTP/1.1 500 Internal Server Error\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    if not os.path.exists(HANDLER):
        print(f"armsim: handler missing at {HANDLER}", file=sys.stderr)
        sys.exit(1)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen(32)
    print(f"armsim listening on :{PORT}", flush=True)
    while True:
        conn, addr = s.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()

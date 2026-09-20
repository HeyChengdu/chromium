#!/usr/bin/env python3
"""Smoke-test Mideo's persistent BGRA frame buffer against headless_shell."""

import argparse
import base64
import json
import mmap
import pathlib
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import websocket


def wait_for_debugger(process: subprocess.Popen[str]) -> tuple[str, list[str]]:
    lines: list[str] = []
    deadline = time.monotonic() + 30
    pattern = re.compile(r"DevTools listening on (ws://[^\s]+)")
    assert process.stderr is not None
    while time.monotonic() < deadline:
        line = process.stderr.readline()
        if line:
            lines.append(line.rstrip())
            match = pattern.search(line)
            if match:
                return match.group(1), lines
        elif process.poll() is not None:
            break
    raise RuntimeError("headless_shell did not expose DevTools: " + "\n".join(lines))


def send(socket: websocket.WebSocket, request_id: int, method: str, params=None):
    socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        response = json.loads(socket.recv())
        if response.get("id") == request_id:
            if "error" in response:
                raise RuntimeError(f"{method} failed: {response['error']}")
            return response.get("result", {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("headless_shell", type=pathlib.Path)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=64)
    parser.add_argument("--slots", type=int, default=3)
    args = parser.parse_args()

    frame_bytes = args.width * args.height * 4
    with tempfile.TemporaryDirectory(prefix="mideo-frame-buffer-") as directory:
        buffer_path = pathlib.Path(directory) / "frames.bgra"
        with buffer_path.open("wb") as output:
            output.truncate(frame_bytes * args.slots)

        command = [
            str(args.headless_shell),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            f"--window-size={args.width},{args.height}",
            f"--mideo-frame-buffer={buffer_path}",
            f"--mideo-frame-buffer-slots={args.slots}",
            "about:blank",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            browser_ws, _ = wait_for_debugger(process)
            port = urllib.parse.urlparse(browser_ws).port
            assert port is not None
            deadline = time.monotonic() + 10
            targets = []
            while time.monotonic() < deadline:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:
                    targets = json.load(response)
                if targets:
                    break
                time.sleep(0.1)
            page_ws = targets[0]["webSocketDebuggerUrl"]
            socket = websocket.create_connection(page_ws, timeout=10)
            try:
                send(socket, 1, "Page.enable")
                page = (
                    "data:text/html,<style>html,body{margin:0;width:100%;height:100%;"
                    "background:%23ff0000}</style>"
                )
                send(socket, 2, "Page.navigate", {"url": page})
                time.sleep(0.25)
                result = send(
                    socket,
                    3,
                    "Page.captureScreenshot",
                    {
                        "format": "mideo-shm",
                        "fromSurface": True,
                        "captureBeyondViewport": False,
                        "clip": {
                            "x": 0,
                            "y": 0,
                            "width": args.width,
                            "height": args.height,
                            "scale": 1,
                        },
                    },
                )
            finally:
                socket.close()

            metadata = json.loads(base64.b64decode(result["data"]))
            assert metadata == {
                "version": 1,
                "slot": 0,
                "sequence": "0",
                "width": args.width,
                "height": args.height,
                "stride": args.width * 4,
                "pixelFormat": "bgra8-unpremul",
            }
            with buffer_path.open("r+b") as frame_file:
                with mmap.mmap(frame_file.fileno(), 0, access=mmap.ACCESS_READ) as frames:
                    pixel = bytes(frames[0:4])
                    assert pixel == bytes((0, 0, 255, 255)), pixel
                    assert any(frames[0:frame_bytes])
            print(json.dumps({"ok": True, "metadata": metadata}))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()

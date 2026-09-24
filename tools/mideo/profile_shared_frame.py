#!/usr/bin/env python3
"""在四浏览器负载下用 Linux perf 采样 Chromium 进程树。"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from shared_frame_buffer_smoke import Browser


def descendants(pid):
    found = {pid}
    pending = [pid]
    while pending:
        current = pending.pop()
        path = Path(f'/proc/{current}/task/{current}/children')
        if not path.exists():
            continue
        for child in path.read_text().split():
            number = int(child)
            if number not in found:
                found.add(number)
                pending.append(number)
    return found


def capture(browser, stop):
    frames = 0
    while not stop.is_set():
        browser.update(frames)
        meta = json.loads(base64.b64decode(browser.capture()['data']))
        browser.release(meta)
        frames += 1
    return frames


def run(binary, output):
    report = {'perfEventParanoid': Path('/proc/sys/kernel/perf_event_paranoid').read_text().strip(),
              'perfVersion': None, 'status': 'unavailable'}
    perf = shutil.which('perf')
    if not perf:
        report['reason'] = 'perf executable not installed'
        return report
    report['perfVersion'] = subprocess.run([perf, '--version'], text=True, capture_output=True).stdout.strip()
    probe = subprocess.run([perf, 'stat', '-e', 'cpu-clock', '--', 'sleep', '0.1'],
                           text=True, capture_output=True)
    (output / 'perf-probe.txt').write_text(probe.stdout + probe.stderr)
    if probe.returncode:
        report['reason'] = f'perf stat denied or unsupported (exit {probe.returncode})'
        return report

    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='mideo-perf-') as folder:
        root = Path(folder)
        browsers = []
        try:
            for index in range(4):
                directory = root / f'browser-{index}'
                directory.mkdir()
                browsers.append(Browser(binary, directory, 2560, 1440))
            pids = sorted(set().union(*(descendants(browser.process.pid) for browser in browsers)))
            report['processCount'] = len(pids)
            stop = threading.Event()
            command = [perf, 'record', '-F', '99', '-g', '--call-graph', 'dwarf,4096',
                       '-p', ','.join(map(str, pids)), '-o', str(output / 'perf.data'),
                       '--', 'sleep', '15']
            with (output / 'perf-record.txt').open('w') as log:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(capture, browser, stop) for browser in browsers]
                    try:
                        recorded = subprocess.run(command, text=True, stdout=log,
                                                  stderr=subprocess.STDOUT, timeout=30)
                    finally:
                        stop.set()
                    report['framesPerBrowser'] = [future.result(timeout=30) for future in futures]
            if recorded.returncode:
                report['reason'] = f'perf record failed (exit {recorded.returncode})'
                return report
            with (output / 'perf-report.txt').open('w') as log:
                reported = subprocess.run([perf, 'report', '--stdio', '--sort', 'comm,dso,symbol',
                                           '--percent-limit', '0.5', '-i', str(output / 'perf.data')],
                                          text=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            report['status'] = 'sampled' if reported.returncode == 0 else 'report_failed'
            report['perfDataBytes'] = (output / 'perf.data').stat().st_size
            return report
        finally:
            for browser in browsers:
                browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('headless_shell', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = run(args.headless_shell, args.output)
    (args.output / 'profile.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))

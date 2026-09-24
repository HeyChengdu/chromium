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
    commands = [[perf], ['sudo', '-n', perf]]
    selected = None
    probe_logs = []
    for command in commands:
        probe = subprocess.run([*command, 'stat', '-e', 'cpu-clock', '--', 'sleep', '0.1'],
                               text=True, capture_output=True)
        probe_logs.append(f"$ {' '.join(command)} stat\n{probe.stdout}{probe.stderr}")
        if probe.returncode == 0:
            selected = command
            break
    (output / 'perf-probe.txt').write_text('\n'.join(probe_logs))
    if selected is None:
        report['reason'] = 'perf stat denied for both runner and sudo'
        return report
    report['privilege'] = 'sudo' if selected[0] == 'sudo' else 'runner'

    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='mideo-perf-') as folder:
        root = Path(folder)
        browsers = []
        try:
            for index in range(4):
                directory = root / f'browser-{index}'
                directory.mkdir()
                browsers.append(Browser(binary, directory, 2560, 1440))
            report['sampleScope'] = 'system-wide under four-browser load'
            stop = threading.Event()
            # 附着进程树时 perf 不随 sleep 退出；系统范围采样由命令生命周期终止，
            # 后续报告按 Chromium 进程名过滤，同时保留其他进程开销供比较。
            command = [*selected, 'record', '-a', '-B', '-N', '-F', '49', '-g',
                       '--call-graph', 'dwarf,2048', '-o', str(output / 'perf.data'),
                       '--', 'sleep', '8']
            with (output / 'perf-record.txt').open('w') as log:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(capture, browser, stop) for browser in browsers]
                    try:
                        recorded = subprocess.run(command, text=True, stdout=log,
                                                  stderr=subprocess.STDOUT, timeout=45)
                    finally:
                        stop.set()
                    report['framesPerBrowser'] = [future.result(timeout=30) for future in futures]
            record_status = recorded.returncode
            if record_status != 0:
                report['reason'] = f'perf record failed (exit {record_status})'
                return report
            if report['privilege'] == 'sudo':
                subprocess.run(['sudo', '-n', 'chmod', 'a+r', str(output / 'perf.data')], check=True)
            with (output / 'perf-report.txt').open('w') as log:
                try:
                    reported = subprocess.run(
                        [*selected, 'report', '--stdio', '--no-children', '-g', 'none',
                         '--comms=headless_shell', '--sort', 'comm,dso,symbol',
                         '--percent-limit', '0.5', '-i', str(output / 'perf.data')],
                        text=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
                except subprocess.TimeoutExpired:
                    report['reason'] = 'perf report timed out after 120 seconds'
                    report['perfDataBytes'] = (output / 'perf.data').stat().st_size
                    return report
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

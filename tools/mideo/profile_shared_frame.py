#!/usr/bin/env python3
"""在四浏览器负载下用 Linux perf 采样 Chromium 进程树。"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import signal
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
            # 四浏览器制造真实竞争，仅采样第一棵进程树，控制 perf 写盘开销。
            pids = sorted(descendants(browsers[0].process.pid))
            report['processCount'] = len(pids)
            stop = threading.Event()
            control = output / 'perf-control.fifo'
            os.mkfifo(control, 0o666)
            control_fd = os.open(control, os.O_RDWR | os.O_NONBLOCK)
            command = [*selected, 'record', '-F', '49', '-g', '--call-graph', 'dwarf,2048',
                       '-p', ','.join(map(str, pids)), '-o', str(output / 'perf.data'),
                       f'--control=fifo:{control.resolve()}']
            try:
                with (output / 'perf-record.txt').open('w') as log:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = [pool.submit(capture, browser, stop) for browser in browsers]
                        recorder = None
                        try:
                            recorder = subprocess.Popen(command, text=True, stdout=log,
                                                        stderr=subprocess.STDOUT,
                                                        start_new_session=True)
                            time.sleep(8)
                            os.write(control_fd, b'stop\n')
                            record_status = recorder.wait(timeout=30)
                        finally:
                            stop.set()
                            if recorder is not None and recorder.poll() is None:
                                if report['privilege'] == 'sudo':
                                    subprocess.run(['sudo', '-n', 'kill', '-KILL', '--',
                                                    f'-{recorder.pid}'], check=False, timeout=10)
                                else:
                                    os.killpg(recorder.pid, signal.SIGKILL)
                                recorder.wait(timeout=10)
                        report['framesPerBrowser'] = [future.result(timeout=30) for future in futures]
            finally:
                os.close(control_fd)
                control.unlink()
            if record_status not in (0, 130, -signal.SIGINT):
                report['reason'] = f'perf record failed (exit {record_status})'
                return report
            if report['privilege'] == 'sudo':
                subprocess.run(['sudo', '-n', 'chmod', 'a+r', str(output / 'perf.data')], check=True)
            with (output / 'perf-report.txt').open('w') as log:
                reported = subprocess.run([*selected, 'report', '--stdio', '--sort', 'comm,dso,symbol',
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

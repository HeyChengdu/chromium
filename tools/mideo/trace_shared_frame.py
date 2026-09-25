"""在四浏览器负载下采集共享帧调用与 Chromium Viz 阶段 trace。"""
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time

from shared_frame_buffer_smoke import Browser


class TraceBrowser(Browser):
    def stop_trace(self):
        self.request_id += 1
        target = self.request_id
        self.socket.send(json.dumps({'id': target, 'method': 'Tracing.end', 'params': {}}))
        stream = None
        acknowledged = False
        deadline = time.monotonic() + 60
        while (stream is None or not acknowledged) and time.monotonic() < deadline:
            message = json.loads(self.socket.recv())
            if message.get('id') == target:
                if 'error' in message:
                    raise RuntimeError(f'Tracing.end: {message["error"]}')
                acknowledged = True
            elif message.get('method') == 'Tracing.tracingComplete':
                stream = message['params']['stream']
        if stream is None:
            raise RuntimeError('Tracing.tracingComplete timed out')
        chunks = []
        while True:
            result = self.send('IO.read', {'handle': stream, 'size': 1024 * 1024})
            value = result['data']
            chunks.append(base64.b64decode(value) if result.get('base64Encoded') else value.encode())
            if result.get('eof'):
                break
        self.send('IO.close', {'handle': stream})
        return b''.join(chunks)


def run(binary: Path, root: Path):
    barrier = threading.Barrier(4)

    def worker(index):
        directory = root / f'browser-{index}'
        directory.mkdir()
        browser_type = TraceBrowser if index == 0 else Browser
        browser = browser_type(binary, directory, 2560, 1440)
        try:
            if index == 0:
                browser.send('Tracing.start', {
                    'categories': 'viz,cc,mojom,devtools.timeline,mideo',
                    'options': 'record-as-much-as-possible',
                    'transferMode': 'ReturnAsStream',
                })
            barrier.wait(timeout=30)
            samples = []
            for frame in range(60):
                browser.update(frame)
                started = time.perf_counter()
                result = browser.capture()
                elapsed = (time.perf_counter() - started) * 1000
                if frame >= 10:
                    samples.append(elapsed)
                meta = json.loads(base64.b64decode(result['data']))
                browser.release(meta)
            raw = browser.stop_trace() if index == 0 else None
            return samples, raw
        finally:
            browser.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    trace = json.loads(results[0][1])
    groups = defaultdict(list)
    requests = {}
    completions = {}
    deliveries = []
    for event in trace['traceEvents']:
        name = event.get('name', '')
        if name in ('Mideo.CaptureRequest', 'Mideo.CaptureResult'):
            sequence = event.get('args', {}).get('sequence')
            if isinstance(sequence, int) and isinstance(event.get('ts'), (int, float)):
                (requests if name == 'Mideo.CaptureRequest' else completions)[sequence] = event['ts']
        if name == 'Mideo.DeliverPresentedFrame' and event.get('ph') == 'X':
            deliveries.append(event)
        if event.get('ph') != 'X' or not isinstance(event.get('dur'), (float, int)):
            continue
        if name.startswith(('Mideo.', 'SoftwareRenderer::', 'Display::', 'DirectRenderer::', 'viz::mojom::CopyOutput', 'CopyOutput')):
            groups[name].append(event['dur'] / 1000)
    rows = sorted(({'name': name, 'count': len(times), 'sumMs': round(sum(times), 3), 'meanMs': round(statistics.mean(times), 3), 'maximumMs': round(max(times), 3)} for name, times in groups.items()), key=lambda item: -item['sumMs'])
    timings = []
    for index, (samples, _) in enumerate(results):
        timings.append({'browser': index, 'count': len(samples), 'meanMs': round(statistics.mean(samples), 3), 'p50Ms': round(statistics.median(samples), 3), 'p95Ms': round(sorted(samples)[int(.95 * (len(samples)-1))], 3), 'samplesMs': [round(sample, 3) for sample in samples]})
    breakdown = {'requestCount': len(requests), 'resultCount': len(completions), 'deliveryCount': len(deliveries)}
    if len(requests) == len(completions) == len(deliveries) == 60:
        deliveries.sort(key=lambda event: event['ts'])
        segments = defaultdict(list)
        for index, sequence in enumerate(sorted(requests)):
            if index < 10:
                continue
            requested = requests[sequence]
            delivery = deliveries[index]
            completed = completions[sequence]
            segments['requestToDeliveryMs'].append((delivery['ts'] - requested) / 1000)
            segments['deliveryMs'].append(delivery['dur'] / 1000)
            segments['deliveryToResultMs'].append((completed - delivery['ts'] - delivery['dur']) / 1000)
            segments['requestToResultMs'].append((completed - requested) / 1000)
        breakdown['segments'] = {name: {'meanMs': round(statistics.mean(values), 3), 'p50Ms': round(statistics.median(values), 3), 'p95Ms': round(sorted(values)[int(.95 * (len(values)-1))], 3)} for name, values in segments.items()}
    return {'traceEvents': len(trace['traceEvents']), 'traceBytes': len(results[0][1]), 'timings': timings, 'breakdown': breakdown, 'events': rows[:35]}, gzip.compress(results[0][1], compresslevel=6)


if __name__ == '__main__':
    binary = Path(sys.argv[1])
    output = Path(sys.argv[2])
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='mideo-trace-') as folder:
        summary, compressed = run(binary, Path(folder))
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    output.with_suffix('.json.gz').write_bytes(compressed)
    print(json.dumps(summary, ensure_ascii=False))

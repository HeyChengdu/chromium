#!/usr/bin/env python3
"""真实 Chromium/FFmpeg 门禁：像素、连续帧、槽位背压、成片差异与捕获耗时。"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import mmap
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from PIL import Image
import websocket

class Browser:
    def __init__(self, binary, directory, width, height):
        self.width, self.height = width, height
        self.frame_bytes = width * height * 4
        self.path = directory / 'frames.bgra'
        self.file = self.path.open('w+b')
        self.file.truncate(self.frame_bytes * 3)
        self.mapping = mmap.mmap(self.file.fileno(), 0)
        self.log = (directory / 'browser.log').open('w+')
        self.process = subprocess.Popen([
            str(binary), '--headless', '--disable-gpu', '--no-sandbox',
            '--force-color-profile=srgb', '--remote-debugging-port=0',
            '--remote-allow-origins=*', f'--window-size={width},{height}',
            f'--mideo-frame-buffer={self.path}', '--mideo-frame-buffer-slots=3',
            'about:blank'], stdout=subprocess.DEVNULL, stderr=self.log)
        self.socket = None
        self.request_id = 0
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                match = re.search(r'DevTools listening on (ws://\S+)', (directory / 'browser.log').read_text())
                if match:
                    port = urllib.parse.urlparse(match[1]).port
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
                        targets = json.load(response)
                    if targets:
                        self.socket = websocket.create_connection(targets[0]['webSocketDebuggerUrl'], timeout=30)
                        break
                if self.process.poll() is not None:
                    raise RuntimeError('Chromium 在建立会话前退出')
                time.sleep(.1)
            if self.socket is None:
                raise RuntimeError('Chromium 启动超时')
            self.send('Page.enable')
            self.send('Runtime.enable')
            self.send('Emulation.setDeviceMetricsOverride', dict(width=width, height=height, deviceScaleFactor=1, mobile=False))
            self.evaluate('''document.body.innerHTML = `<style>body{margin:0;background:#e7edf4;color:#132239;font:24px serif}svg{position:absolute;left:45%;top:30%}canvas{width:40%;height:50%}</style><div id="text">Geometry 数理化 x²+α=β ∑ₙ 0</div><canvas width="640" height="360"></canvas><svg width="120" height="100"><path d="M0 0 L110 90 L30 70Z" fill="#985bf0" opacity=".4"/></svg>`;''')
        except BaseException:
            self.close()
            raise

    def send(self, method, params=None, fail=False):
        self.request_id += 1
        self.socket.send(json.dumps(dict(id=self.request_id, method=method, params=params or {})))
        while True:
            response = json.loads(self.socket.recv())
            if response.get('id') != self.request_id:
                continue
            if fail:
                assert 'error' in response, (method, response)
                return
            if 'error' in response:
                raise RuntimeError(f'{method}: {response["error"]}')
            return response.get('result', {})

    def evaluate(self, expression):
        result = self.send('Runtime.evaluate', dict(expression=expression, awaitPromise=True, returnByValue=True))
        assert 'exceptionDetails' not in result, result

    def update(self, n):
        self.evaluate(f'''(() => {{document.querySelector('#text').textContent='Geometry 数理化 x²+α=β ∑ₙ {n}'; const c=document.querySelector('canvas').getContext('2d'); c.clearRect(0,0,640,360); const g=c.createLinearGradient(0,0,640,360);g.addColorStop(0,'#0485f7');g.addColorStop(1,'#eb8855');c.fillStyle=g;c.fillRect(0,0,640,360);c.fillStyle='rgba(255,255,255,.37)';c.fillRect({n % 300},30,100,120); return new Promise(r=>requestAnimationFrame(()=>r()));}})()''')

    def capture(self, format='shared', fail=False):
        if format == 'shared':
            return self.send('Page.captureMideoFrame', fail=fail)
        return self.send('Page.captureScreenshot', dict(format=format, fromSurface=True, captureBeyondViewport=False, optimizeForSpeed=True), fail)

    def shared(self):
        result = self.capture()
        meta = json.loads(base64.b64decode(result['data']))
        assert meta['version'] == 3 and meta['producer'] == 'viz-presented-software'
        assert (meta['width'], meta['height'], meta['stride']) == (self.width, self.height, self.width*4)
        start = meta['slot'] * self.frame_bytes
        return meta, bytes(self.mapping[start:start+self.frame_bytes])

    def release(self, meta, fail=False):
        self.send('Page.releaseMideoFrame', dict(slot=meta['slot'], sequence=meta['sequence']), fail)

    def close(self):
        if self.socket:
            self.socket.close()
        self.process.terminate()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=5)
        self.mapping.close(); self.file.close(); self.log.close()


def quality(binary, ffmpeg, directory):
    browser = Browser(binary, directory, 640, 360)
    frames = []
    metadata = []
    try:
        # 不释放时占满三个槽，第四次必须失败且不能覆盖前面任一槽。
        for n in range(3):
            browser.update(n)
            meta, data = browser.shared()
            metadata.append(meta); frames.append(data)
        before = bytes(browser.mapping)
        browser.capture(fail=True)
        assert bytes(browser.mapping) == before
        for meta in metadata: browser.release(meta)
        browser.release(metadata[0], fail=True)
        pngs = []
        hashes = []
        common = ['-v', 'error', '-y']
        output = ['-c:v', 'libx264', '-crf', '15', '-preset', 'slow', '-x264-params', 'aq-mode=3', '-pix_fmt', 'yuv420p', '-flags', '+cgop', '-r', '30', '-movflags', '+faststart']
        native = subprocess.Popen([str(ffmpeg), *common, '-f', 'mideoshm', '-buffer_path', str(browser.path), '-video_size', '640x360', '-framerate', '30', '-ack_fd', '1', '-i', 'pipe:0', *output, str(directory/'shared.mp4')], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for n in range(36):
                browser.update(n)
                png = base64.b64decode(browser.capture('png')['data'])
                expected = Image.open(io.BytesIO(png)).convert('RGBA').tobytes('raw', 'BGRA')
                meta, actual = browser.shared()
                if n in (0, 18, 35):
                    (directory/f'baseline-{n}.png').write_bytes(png)
                    Image.frombytes('RGBA', (640,360), actual, 'raw', 'BGRA').save(directory/f'shared-{n}.png')
                assert actual == expected, f'frame {n}: PNG 与 Viz BGRA 不一致'
                hashes.append(hashlib.sha256(actual).hexdigest())
                native.stdin.write(f'{meta["slot"]} {n}\n'.encode()); native.stdin.flush()
                # 外层 CI 为整个测试设置硬超时，避免子进程损坏无限等待。
                assert native.stdout.readline() == f'{n}\n'.encode()
                browser.release(meta)
                pngs.append(png)
            native.stdin.close()
            assert native.wait(timeout=90) == 0
        finally:
            if native.poll() is None: native.kill(); native.wait()
        assert len(set(hashes)) == len(hashes), '变化帧重复或过期'
        subprocess.run([str(ffmpeg), *common, '-f', 'image2pipe', '-framerate', '30', '-vcodec', 'png', '-i', 'pipe:0', *output, str(directory/'png.mp4')], input=b''.join(pngs), check=True, timeout=90)
        decoded = []
        for name in ['png', 'shared']:
            result = subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(directory/f'{name}.mp4'), '-f', 'rawvideo', '-pix_fmt', 'yuv420p', 'pipe:1'], stdout=subprocess.PIPE, check=True, timeout=90)
            decoded.append(result.stdout)
        assert decoded[0] == decoded[1], '成片解码像素存在差异，拒绝发布'
        assert len(decoded[0]) == 36 * 640 * 360 * 3 // 2
        # 不等待 rAF：连续同步修改 DOM 后，当前帧必须立即包含最新颜色。
        browser.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=\"mideo-freshness\" style=\"position:fixed;inset:0;z-index:2147483647\"></div>')")
        for color in ('#123456', '#e85a20', '#2879c1', '#c43be0'):
            browser.evaluate(f"document.querySelector('#mideo-freshness').style.backgroundColor='{color}'")
            meta, actual = browser.shared()
            expected_pixel = bytes.fromhex(color[5:7] + color[3:5] + color[1:3] + 'ff')
            assert actual[:4] == expected_pixel, f'同步 DOM 修改后抓到了旧帧：{color}'
            browser.release(meta)
        browser.evaluate("document.querySelector('#mideo-freshness').remove()")
        # 透明背景验证非预乘 Alpha；PNG 仍作为同构建基线。
        browser.send('Emulation.setDefaultBackgroundColorOverride', dict(color=dict(r=0,g=0,b=0,a=0)))
        browser.evaluate("document.body.style.background='transparent'")
        png = base64.b64decode(browser.capture('png')['data'])
        meta, actual = browser.shared()
        assert actual == Image.open(io.BytesIO(png)).convert('RGBA').tobytes('raw','BGRA')
        browser.release(meta)
        return dict(pixelExact=True, encodedPixelExact=True, frames=36, backpressure=True, alphaExact=True)
    finally: browser.close()


def benchmark(binary, directory):
    browser = Browser(binary, directory, 2560, 1440)
    samples = {name: [] for name in ['png', 'shared']}
    try:
        for n in range(25):
            browser.update(n)
            for format in samples:
                start = time.perf_counter()
                result = browser.capture(format)
                elapsed = (time.perf_counter()-start)*1000
                if format == 'shared':
                    browser.release(json.loads(base64.b64decode(result['data'])))
                if n >= 5: samples[format].append(elapsed)
        return {key: dict(count=len(values), meanMs=statistics.mean(values), p50Ms=statistics.median(values), p95Ms=sorted(values)[int(.95*(len(values)-1))]) for key, values in samples.items()}
    finally: browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('headless_shell', type=Path)
    parser.add_argument('ffmpeg', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mideo-gate-', dir='/dev/shm') as temp:
        root = Path(temp)
        def directory(name):
            value=root/name;value.mkdir();return value
        report = {}
        try:
            report['quality'] = quality(args.headless_shell, args.ffmpeg, directory('quality'))
            report['singleBrowser'] = benchmark(args.headless_shell, directory('single'))
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(benchmark,args.headless_shell,directory(f'four-{n}')) for n in range(4)]
                report['fourBrowsers'] = [f.result() for f in futures]
            report['passed'] = True
        except BaseException as error:
            report['passed'] = False
            report['error'] = str(error)
            raise
        finally:
            evidence = args.report.parent / 'verification'
            evidence.mkdir(exist_ok=True)
            for file in root.rglob('*'):
                if file.suffix in ('.png', '.mp4', '.log'):
                    shutil.copy2(file, evidence / (file.parent.name + '-' + file.name))
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__': main()

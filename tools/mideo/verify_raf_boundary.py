#!/usr/bin/env python3
"""[INPUT]: 已验 Linux Shell 与 Browser 的真实 CDP、mmap 和租约端口。
[OUTPUT]: DPR2 混合画面的零/一 rAF/显式裁切 PNG 全字节及视口恢复证据。
[POS]: 独立实验合同；不证明课程性能，不修改生产等待策略。
[PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from shared_frame_buffer_smoke import Browser


def verify(binary, output):
    output.mkdir(parents=True, exist_ok=True)
    browser = Browser(binary, output, 2560, 1440)
    rows = []
    sequence = -1
    report = dict(experimentalOnly=True, courseAccepted=False,
                  performanceAccepted=False, cases=rows, passed=False)
    with binary.open('rb') as source:
        report['binarySha256'] = hashlib.file_digest(source, 'sha256').hexdigest()

    def capture():
        nonlocal sequence
        meta, pixels = browser.shared()
        try:
            current = int(meta['sequence'])
            assert current == sequence + 1, meta
            assert meta['pixelFormat'] == 'bgra8-unpremul', meta
            sequence = current
            assert len(pixels) == 2560 * 1440 * 4
            return pixels
        finally:
            browser.release(meta)

    def geometry():
        result = browser.send('Runtime.evaluate', dict(
            expression='''(() => {
              const rect = document.querySelector('canvas').getBoundingClientRect();
              const v = window.visualViewport;
              return {dpr:devicePixelRatio, innerWidth, innerHeight,
                canvas:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
                visualViewport:{width:v.width,height:v.height,scale:v.scale,
                  offsetLeft:v.offsetLeft,offsetTop:v.offsetTop}};
            })()''', returnByValue=True))
        return dict(dom=result['result']['value'],
                    layout=browser.send('Page.getLayoutMetrics', {}))

    try:
        browser.send('Emulation.setDeviceMetricsOverride', dict(
            width=1280, height=720, deviceScaleFactor=2, mobile=False,
            viewport=dict(x=0, y=0, width=1280, height=720, scale=1)))
        browser.evaluate('''(async () => {
          document.body.innerHTML = `<style>body{margin:0;background:#102030}
          #text{font:32px serif;color:white;height:80px}
          #clip{height:30px;background:linear-gradient(90deg,#f008,#08ff)}
          canvas{width:1280px;height:610px}</style><div id="text"></div>
          <div id="clip"></div><canvas width="2560" height="1220"></canvas>`;
          await document.fonts.ready;
          const image = new Image();
          image.src = 'data:image/svg+xml,' + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>');
          await image.decode(); window.readyImage = image;
        })()''')
        for frame in (0, 1, 7, 30, 59, 60, 2, 0):
            browser.evaluate('''(() => {
              const n = FRAME;
              const text = document.querySelector('#text');
              text.textContent = `两条线段 ${n}：x² + y² = r²`;
              text.style.transform = `translateX(${n / 7}px)`;
              document.querySelector('#clip').style.clipPath = `inset(0 ${100-n}% 0 0)`;
              const c = document.querySelector('canvas').getContext('2d');
              c.clearRect(0,0,2560,1220); c.strokeStyle='rgba(33,200,120,.6)';
              c.lineWidth=2.5; c.beginPath(); c.moveTo(27+n,24.6);
              c.lineTo(2320.6-n,1101.4); c.stroke();
              c.drawImage(window.readyImage,100+n,150);
            })()'''.replace('FRAME', str(frame)))
            zero = capture()
            browser.evaluate('new Promise(r => requestAnimationFrame(r))')
            one = capture()
            before_png = geometry()
            # 无 clip 路径会另改 emulation scale；显式物理映射不做事后缩图。
            png = base64.b64decode(browser.send('Page.captureScreenshot', dict(
                format='png', fromSurface=True, captureBeyondViewport=False,
                optimizeForSpeed=True,
                clip=dict(x=0, y=0, width=1280, height=720, scale=1)))['data'])
            after_png = geometry()
            restored = capture()
            decoded = Image.open(io.BytesIO(png)).convert('RGBA')
            assert decoded.size == (2560, 1440), decoded.size
            baseline = decoded.tobytes('raw', 'BGRA')
            row = dict(frame=frame, zeroEqualsOne=zero == one,
                       oneEqualsPng=one == baseline,
                       oneEqualsRestored=one == restored,
                       geometryBeforePng=before_png, geometryAfterPng=after_png,
                       geometryRestored=before_png == after_png,
                       dprIsTwo=before_png['dom']['dpr'] == 2,
                       zeroSha256=hashlib.sha256(zero).hexdigest(),
                       oneSha256=hashlib.sha256(one).hexdigest())
            rows.append(row)
            if not all(row[key] for key in (
                    'zeroEqualsOne', 'oneEqualsPng', 'oneEqualsRestored',
                    'geometryRestored', 'dprIsTwo')):
                for label, data in (('zero', zero), ('one', one),
                                    ('png', baseline), ('restored', restored)):
                    Image.frombytes('RGBA', (2560, 1440), data, 'raw', 'BGRA').save(
                        output / f'{len(rows)}-{label}.png')
                raise AssertionError(row)
        # 作者排队的回调不属于同步 DOM 合同，不把它当作捕获屏障。
        browser.evaluate('''(() => {
          window.pendingExecuted = false;
          requestAnimationFrame(() => {
            document.querySelector('#text').textContent = '排队刷新已执行';
            window.pendingExecuted = true;
          });
        })()''')
        pending = capture()
        browser.evaluate('new Promise(r => requestAnimationFrame(r))')
        settled = capture()
        state = browser.send('Runtime.evaluate', dict(
            expression='window.pendingExecuted', returnByValue=True))
        assert state['result']['value'] is True, state
        report['pendingRafObservation'] = dict(
            capturedPixelsEqualAfterRaf=pending == settled,
            callbackExecutedAfterExplicitRaf=True,
            callbackExecutionAtFirstCapture='unknown',
            pendingSha256=hashlib.sha256(pending).hexdigest(),
            settledSha256=hashlib.sha256(settled).hexdigest())
        report['passed'] = True
    except Exception as error:
        report['error'] = dict(type=type(error).__name__, message=str(error))
        raise
    finally:
        browser.close()
        (output / 'report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    verify(args.binary.resolve(), args.output.resolve())

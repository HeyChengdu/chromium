#!/usr/bin/env python3
"""复用固定候选，仅定位呈现帧交付失败；不作为保真或性能验收。"""
import argparse
import base64
from collections import Counter
import gzip
import fcntl
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time

from PIL import Image

from trace_shared_frame import TraceBrowser


def diagnose(binary, output, preparation):
    with tempfile.TemporaryDirectory(prefix='mideo-delivery-', dir='/dev/shm') as temp:
        directory = Path(temp)
        browser = TraceBrowser(binary, directory, 640, 360)
        report = {'preparation': preparation, 'diagnosticOnly': True}
        try:
            browser.send('Tracing.start', {
                'categories': 'viz,cc,renderer,gpu,benchmark,mojom',
                'options': 'record-as-much-as-possible',
                'transferMode': 'ReturnAsStream',
            })
            try:
                browser.update(0)
                if preparation == 'animation-frames':
                    browser.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                elif preparation == 'png':
                    # 仅诊断初始化依赖，绝不把截图预热当作交付路径的修复。
                    browser.capture('png')
                started = time.monotonic()
                try:
                    result = browser.capture()
                    report['captureSucceeded'] = True
                    metadata = json.loads(base64.b64decode(result['data']))
                    report['frame'] = {key: metadata.get(key) for key in ('width', 'height', 'stride', 'version', 'producer')}
                    browser.release(metadata)
                except Exception as error:
                    report['captureSucceeded'] = False
                    report['error'] = str(error)
                report['callMs'] = round((time.monotonic() - started) * 1000, 3)
            finally:
                # 即便交付失败也先停止并保存 Trace，再关闭浏览器。
                raw = browser.stop_trace()
                (output / f'{preparation}.trace.json.gz').write_bytes(gzip.compress(raw))
                events = json.loads(raw)['traceEvents']
                selected = [event for event in events if event.get('name', '').startswith((
                    'Mideo.', 'SoftwareRenderer::', 'SkiaRenderer::',
                    'SkiaOutputSurfaceImplOnGpu::', 'Display::',
                    'WidgetBase::ForceRedrawForMideo'))]
                report['eventCounts'] = dict(Counter(event['name'] for event in selected))
                report['mideoEvents'] = [{key: event[key] for key in ('name', 'ph', 'ts', 'dur', 'pid', 'tid', 'args') if key in event}
                                         for event in selected if event['name'].startswith(('Mideo.', 'WidgetBase::ForceRedrawForMideo'))][:60]
        except Exception as error:
            report['diagnosticError'] = str(error)
        finally:
            browser.close()
            shutil.copy2(directory / 'browser.log', output / f'{preparation}.browser.log')
        return report


def diagnose_alpha(binary, output, prior_opaque):
    """固定半透明场景记录原始像素，不将预热后的结果视为冷帧通过。"""
    name = 'alpha-after-opaque' if prior_opaque else 'alpha-cold'
    with tempfile.TemporaryDirectory(prefix='mideo-alpha-', dir='/dev/shm') as temp:
        browser = TraceBrowser(binary, Path(temp), 640, 360)
        report = {'case': name, 'diagnosticOnly': True, 'captures': []}
        def record(label, data):
            report['captures'].append({
                'label': label, 'firstBGRA': list(data[:4]),
                'lastBGRA': list(data[-4:]),
                'alphaCounts': dict(Counter(data[3::4])),
            })
            Image.frombytes('RGBA', (640, 360), data, 'raw', 'BGRA').save(
                output / f'{name}-{label}.png')
        def shared(label):
            meta, data = browser.shared()
            try:
                record(label, data)
            finally:
                browser.release(meta)
        try:
            if prior_opaque:
                # 复现门禁在不透明连续帧及同步 DOM 检查后的切换。
                browser.update(0)
                shared('opaque')
                browser.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=\"mideo-freshness\" style=\"position:fixed;inset:0;z-index:2147483647\"></div>')")
                for color in ('#123456', '#e85a20', '#2879c1', '#c43be0'):
                    browser.evaluate(f"document.querySelector('#mideo-freshness').style.backgroundColor='{color}'")
                    shared('fresh-' + color[1:])
                browser.evaluate("document.querySelector('#mideo-freshness').remove()")
            browser.send('Emulation.setDefaultBackgroundColorOverride', dict(color=dict(r=0,g=0,b=0,a=0)))
            browser.evaluate("document.body.style.background='transparent'")
            browser.evaluate("document.body.innerHTML='<div style=\"position:fixed;left:0;top:0;width:16px;height:16px;background:rgba(255,0,0,0.5)\"></div>'")
            shared('first-shared')
            png = base64.b64decode(browser.capture('png')['data'])
            record('png', Image.open(io.BytesIO(png)).convert('RGBA').tobytes('raw', 'BGRA'))
            shared('after-png')
            browser.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            shared('after-raf')
        except Exception as error:
            report['error'] = str(error)
        finally:
            browser.close()
            shutil.copy2(Path(temp) / 'browser.log', output / f'{name}.browser.log')
        return report



def diagnose_dpr(binary, output, dpr, buffer_width, buffer_height):
    """对照逻辑窗口与物理文件尺寸；小文件只用于定位，绝不作为输出降级。"""
    name = f'dpr-{dpr}-buffer-{buffer_width}x{buffer_height}'
    with tempfile.TemporaryDirectory(prefix='mideo-dpr-', dir='/dev/shm') as temp:
        browser = TraceBrowser(binary, Path(temp), buffer_width, buffer_height)
        report = {'case': name, 'diagnosticOnly': True,
                  'logicalSize': [1280, 720], 'dpr': dpr,
                  'bufferSize': [buffer_width, buffer_height],
                  'bufferBytes': browser.path.stat().st_size}
        try:
            browser.send('Emulation.setDeviceMetricsOverride', dict(
                width=1280, height=720, deviceScaleFactor=dpr, mobile=False))
            browser.update(0)
            report['dom'] = browser.send('Runtime.evaluate', dict(
                expression='({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})',
                returnByValue=True))['result']['value']
            # 首次 CDP 请求之前探测并立即释放锁，区分锁竞争与长度校验拒绝。
            with browser.path.open('r+b') as probe:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                report['exclusiveLockAvailableBeforeCapture'] = True
                fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            try:
                meta, data = browser.shared()
                try:
                    report['captureSucceeded'] = True
                    report['metadata'] = meta
                    Image.frombytes('RGBA', (buffer_width, buffer_height), data,
                                    'raw', 'BGRA').save(output / f'{name}-shared.png')
                finally:
                    browser.release(meta)
            except Exception as error:
                report['captureSucceeded'] = False
                report['error'] = str(error)
            # 共享调用已完成或拒绝后才取 PNG，不能用 PNG 预热掩盖首帧问题。
            png = base64.b64decode(browser.capture('png')['data'])
            (output / f'{name}-reference.png').write_bytes(png)
            report['pngSize'] = list(Image.open(io.BytesIO(png)).size)
        except Exception as error:
            report['diagnosticError'] = str(error)
        finally:
            browser.close()
            shutil.copy2(Path(temp) / 'browser.log', output / f'{name}.browser.log')
        return report



def diagnose_physical_viewport(binary, output, logical_window=False, resize_root=False):
    """保持 CSS 视口和 DPR，仅显式设置合成视口，并与独立普通截图比较。"""
    name = 'dpr-2-physical-viewport' + ('-logical-window' if logical_window else '') + ('-resize-root' if resize_root else '')
    report = {'case': name, 'diagnosticOnly': True, 'frames': []}
    with tempfile.TemporaryDirectory(prefix='mideo-viewport-', dir='/dev/shm') as temp:
        root = Path(temp)
        browsers = []
        try:
            for label, mideo in (('shared', True), ('reference', False)):
                directory = root / label
                directory.mkdir()
                browser = TraceBrowser(binary, directory, 2560, 1440, mideo=mideo,
                                       window_size=(1280, 720) if logical_window else None)
                browsers.append(browser)
                params = dict(width=1280, height=720, deviceScaleFactor=2, mobile=False)
                if mideo:
                    window = browser.send('Browser.getWindowForTarget')
                    report['initialWindow'] = window['bounds']
                    if resize_root:
                        browser.send('Browser.setWindowBounds', dict(
                            windowId=window['windowId'], bounds=dict(width=2560, height=1440)))
                    report['captureWindow'] = browser.send('Browser.getWindowForTarget')['bounds']
                    params['viewport'] = dict(x=0, y=0, width=1280, height=720, scale=1)
                browser.send('Emulation.setDeviceMetricsOverride', params)
                report[label + 'DOM'] = browser.send('Runtime.evaluate', dict(
                    expression='({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})',
                    returnByValue=True))['result']['value']
            shared, reference = browsers
            for frame in range(4):
                for browser in browsers:
                    browser.update(frame)
                    if frame == 3:
                        browser.send('Emulation.setDefaultBackgroundColorOverride',
                                     dict(color=dict(r=0,g=0,b=0,a=0)))
                        browser.evaluate("document.body.style.background='transparent';document.body.innerHTML='<div style=\"position:fixed;left:0;top:0;width:16px;height:16px;background:rgba(255,0,0,0.5)\"></div>'")
                meta, actual = shared.shared()
                try:
                    png = base64.b64decode(reference.send('Page.captureScreenshot', dict(
                        format='png', fromSurface=True, captureBeyondViewport=False,
                        optimizeForSpeed=True,
                        clip=dict(x=0, y=0, width=1280, height=720, scale=1)))['data'])
                    decoded = Image.open(io.BytesIO(png)).convert('RGBA')
                    expected = decoded.tobytes('raw', 'BGRA')
                    (output / f'{name}-{frame}-reference.png').write_bytes(png)
                    Image.frombytes('RGBA', (2560, 1440), actual, 'raw', 'BGRA').save(
                        output / f'{name}-{frame}-shared.png')
                    report['frames'].append(dict(
                        frame=frame, metadata=meta, pngSize=list(decoded.size),
                        pixelExact=actual == expected,
                        sharedSha256=hashlib.sha256(actual).hexdigest(),
                        referenceSha256=hashlib.sha256(expected).hexdigest(),
                        firstBGRA=list(actual[:4]), lastBGRA=list(actual[-4:])))
                finally:
                    shared.release(meta)
        except Exception as error:
            report['error'] = str(error)
        finally:
            for browser in browsers:
                browser.close()
            for log in root.rglob('browser.log'):
                shutil.copy2(log, output / f'{name}-{log.parent.name}.browser.log')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = [diagnose(args.binary, args.output, preparation)
               for preparation in ('cold', 'animation-frames', 'png')]
    reports.extend(diagnose_alpha(args.binary, args.output, prior_opaque)
                   for prior_opaque in (False, True))
    reports.extend(diagnose_dpr(args.binary, args.output, dpr, width, height)
                   for dpr, width, height in ((1, 1280, 720), (2, 2560, 1440), (2, 1280, 720)))
    reports.extend(diagnose_physical_viewport(args.binary, args.output, logical_window, resize_root)
                   for logical_window, resize_root in ((False, False), (True, False), (True, True)))
    data = json.dumps(reports, ensure_ascii=False, indent=2) + '\n'
    (args.output / 'diagnosis.json').write_text(data)
    print(data)


if __name__ == '__main__':
    main()

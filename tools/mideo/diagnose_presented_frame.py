#!/usr/bin/env python3
"""复用固定候选，仅定位呈现帧交付失败；不作为保真或性能验收。"""
import argparse
import base64
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import time

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = [diagnose(args.binary, args.output, preparation)
               for preparation in ('cold', 'animation-frames', 'png')]
    data = json.dumps(reports, ensure_ascii=False, indent=2) + '\n'
    (args.output / 'diagnosis.json').write_text(data)
    print(data)


if __name__ == '__main__':
    main()

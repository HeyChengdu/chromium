#!/usr/bin/env python3
"""[INPUT]: 新构建 Shell、真实 CDP WebSocket 与三槽 mmap。
[OUTPUT]: renderer ready、外部 tick 与最终共享像素的独立红绿证据。
[POS]: 显式实验驱动；单接收循环保留异步响应，不验课程性能。
[PROTOCOL]: 变更时检查 AGENTS.md。
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import time

from PIL import Image
from shared_frame_buffer_smoke import Browser


class ExternalTickBrowser(Browser):
    def __init__(self, binary, directory):
        self.responses = {}
        self.events = []
        self.transcript = []
        self.pending = set()
        self.transcript_bytes = 0
        # 根窗口控制 Display 尺寸；DPR2 的逻辑视口另由 Emulation 设置。
        super().__init__(binary, directory, 2560, 1440,
                         extra_switches=('--enable-begin-frame-control',
                                         '--run-all-compositor-stages-before-draw',
                                         '--enable-logging=stderr', '--log-level=0',
                                         '--mideo-experimental-external-tick'))

    def request(self, method, params=None):
        self.request_id += 1
        message = dict(id=self.request_id, method=method, params=params or {})
        if len(self.pending) >= 8:
            raise ValueError('CDP并发请求预算超限')
        self.pending.add(self.request_id)
        self.socket.send(json.dumps(message))
        self.transcript.append(dict(direction='send', message=message))
        return self.request_id

    def receive(self, deadline):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('CDP总截止时间到达')
        self.socket.settimeout(remaining)
        raw = self.socket.recv()
        if not raw:
            raise EOFError('CDP连接已关闭')
        message = json.loads(raw)
        self.transcript_bytes += len(raw)
        if self.transcript_bytes > 32*1024*1024 or len(self.events) >= 4096:
            raise ValueError('CDP证据预算超限')
        self.transcript.append(dict(direction='receive', message=message))
        if 'id' in message:
            if message['id'] not in self.pending or message['id'] in self.responses:
                raise ValueError('未知或重复CDP响应')
            self.responses[message['id']] = message
        else:
            self.events.append(message)

    def response(self, request, deadline):
        while request not in self.responses:
            self.receive(deadline)
        self.pending.remove(request)
        return self.responses.pop(request)

    def send(self, method, params=None, fail=False):
        reply = self.response(self.request(method, params), time.monotonic()+30)
        if fail:
            assert 'error' in reply, reply
            return reply
        if 'error' in reply:
            raise RuntimeError(str(reply))
        return reply.get('result', {})

    def ready(self, capture, sequence, deadline):
        while True:
            for index, event in enumerate(self.events):
                if event.get('method') == 'Page.mideoFrameReady':
                    ready = self.events.pop(index)['params']
                    if ready.get('sequence') != str(sequence) or type(ready.get('ready')) is not bool:
                        raise ValueError('过期或错误ready事件')
                    return ready
            if capture in self.responses:
                raise RuntimeError('ready前capture已终结：'+str(self.responses[capture]))
            self.receive(deadline)

    def tick(self):
        return self.send('HeadlessExperimental.beginFrame', dict(noDisplayUpdates=False))


def verify(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, experimentalOnly=True, courseAccepted=False,
                  performanceAccepted=False, cases=[])
    browser = None
    try:
        with binary.open('rb') as stream:
            report['binarySha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        browser = ExternalTickBrowser(binary, output)
        browser.send('Emulation.setDeviceMetricsOverride', dict(
            width=1280, height=720, deviceScaleFactor=2, mobile=False,
            viewport=dict(x=0,y=0,width=1280,height=720,scale=1)))
        report['initialGeometry'] = dict(
            window=browser.send('Browser.getWindowForTarget'),
            layout=browser.send('Page.getLayoutMetrics'),
            dom=browser.send('Runtime.evaluate', dict(
                expression='({dpr:devicePixelRatio,width:innerWidth,height:innerHeight,scale:visualViewport.scale})',
                returnByValue=True)))
        bounds = report['initialGeometry']['window']['bounds']
        assert (bounds['width'], bounds['height']) == (2560, 1440), bounds
        dom = report['initialGeometry']['dom']['result']['value']
        assert dom == dict(dpr=2, width=1280, height=720, scale=1), dom
        # 初始 Surface 必须实际提交；此 tick 不计入捕获实验。
        browser.tick()
        browser.evaluate('''document.body.innerHTML = `<style>body{margin:0;background:#102030;color:white}
          #text{font:32px serif;height:110px}canvas{width:1280px;height:610px}</style>
          <div id="text"></div><canvas width="2560" height="1220"></canvas>`;''')
        browser.evaluate('document.fonts.ready')
        browser.tick()
        last_sequence = -1
        for frame in (0,7,1):
            browser.evaluate(f'''(() => {{
              document.querySelector('#text').textContent='两条线段 x²+α {frame}';
              document.querySelector('#text').style.transform='translateX({frame/7}px)';
              const c=document.querySelector('canvas').getContext('2d');
              c.clearRect(0,0,2560,1220);c.fillStyle='rgba(30,200,120,.6)';
              c.fillRect({frame},20.5,2300,1170);c.clearRect(100,100,203,97);
            }})()''')
            request = browser.request('Page.captureMideoFrame', dict(prepareForExternalTick=True))
            ready = browser.ready(request, last_sequence+1, time.monotonic()+20)
            row = dict(frame=frame, ready=ready)
            report['cases'].append(row)
            assert ready['sequence'].isdigit() and int(ready['sequence'])==last_sequence+1, ready
            if not ready['ready']:
                # 拒绝不释放槽；等原Viz终态后才结束这个独立会话。
                row['terminal'] = browser.response(request,time.monotonic()+20)
                assert 'error' in row['terminal'], row
                assert 'MIDEO_EXTERNAL_TICK_STUB_REJECTED' in (output/'browser.log').read_text(), '未证明renderer拒绝stub实际执行'
                raise AssertionError('renderer ready合同拒绝：目标行为红灯')
            # 第二个请求必须被原pending守卫拒绝，不消耗新序号。
            overlap=browser.send('Page.captureMideoFrame',dict(prepareForExternalTick=True),fail=True)
            assert 'busy' in overlap['error']['message'], overlap
            tick = browser.request('HeadlessExperimental.beginFrame', dict(noDisplayUpdates=False))
            row['tick'] = browser.response(tick,time.monotonic()+20)
            assert 'error' not in row['tick'] and row['tick']['result']['hasDamage'], row
            final = browser.response(request,time.monotonic()+20)
            assert 'error' not in final, final
            meta=json.loads(base64.b64decode(final['result']['data']))
            assert (meta['width'],meta['height'],meta['stride'],meta['version'],meta['pixelFormat'],meta['producer'])==(2560,1440,10240,3,'bgra8-unpremul','viz-presented-software'),meta
            assert meta['sequence']==ready['sequence'] and 0<=meta['slot']<3,meta
            last_sequence=int(meta['sequence'])
            start=meta['slot']*browser.frame_bytes
            pixels=bytes(browser.mapping[start:start+browser.frame_bytes])
            (output/f'{frame}.bgra').write_bytes(pixels)
            browser.release(meta)
            geometry_expression='JSON.stringify({dpr:devicePixelRatio,w:innerWidth,h:innerHeight,scale:visualViewport.scale})'
            before=browser.send('Runtime.evaluate',dict(expression=geometry_expression,returnByValue=True))
            png_request=browser.request('Page.captureScreenshot',dict(
                format='png',fromSurface=True,captureBeyondViewport=False,
                clip=dict(x=0,y=0,width=1280,height=720,scale=1)))
            # 外部控制下不阻塞等待PNG；独立参考也获得一个有界tick。
            browser.tick()
            png_reply=browser.response(png_request,time.monotonic()+20)
            assert 'error' not in png_reply,png_reply
            png=base64.b64decode(png_reply['result']['data'])
            (output/f'{frame}.png').write_bytes(png)
            image=Image.open(io.BytesIO(png)).convert('RGBA')
            assert image.size==(2560,1440) and image.tobytes('raw','BGRA')==pixels
            after=browser.send('Runtime.evaluate',dict(expression=geometry_expression,returnByValue=True))
            assert before==after and json.loads(before['result']['value'])['dpr']==2
            restored_request=browser.request('Page.captureMideoFrame',dict(prepareForExternalTick=True))
            restored_ready=browser.ready(restored_request,last_sequence+1,time.monotonic()+20)
            assert restored_ready['ready'],restored_ready
            browser.tick()
            restored_reply=browser.response(restored_request,time.monotonic()+20)
            assert 'error' not in restored_reply,restored_reply
            restored=json.loads(base64.b64decode(restored_reply['result']['data']))
            assert (restored['width'],restored['height'],restored['stride'],restored['version'],restored['pixelFormat'],restored['producer'])==(2560,1440,10240,3,'bgra8-unpremul','viz-presented-software'),restored
            assert restored['sequence']==str(last_sequence+1) and type(restored['slot']) is int and 0<=restored['slot']<3,restored
            start=restored['slot']*browser.frame_bytes
            restored_pixels=bytes(browser.mapping[start:start+browser.frame_bytes])
            (output/f'{frame}.restored.bgra').write_bytes(restored_pixels)
            browser.release(restored)
            last_sequence+=1
            assert restored_pixels==pixels
            row['restoredSequence']=restored['sequence']
            row['pixelSha256']=hashlib.sha256(pixels).hexdigest()
        # 故意不发tick：等待真实Viz超时，不用driver超时冒充释放成功。
        timed_request=browser.request('Page.captureMideoFrame',dict(prepareForExternalTick=True))
        timed_ready=browser.ready(timed_request,last_sequence+1,time.monotonic()+20)
        assert timed_ready['ready'],timed_ready
        busy=browser.send('Page.captureMideoFrame',dict(prepareForExternalTick=True),fail=True)
        assert 'busy' in busy['error']['message'],busy
        terminal=browser.response(timed_request,time.monotonic()+20)
        assert terminal.get('error',{}).get('message')=='Viz presented-frame delivery failed',terminal
        last_sequence+=1
        recovery=browser.request('Page.captureMideoFrame',dict(prepareForExternalTick=True))
        recovery_ready=browser.ready(recovery,last_sequence+1,time.monotonic()+20)
        assert recovery_ready['ready'],recovery_ready
        browser.tick()
        recovered=browser.response(recovery,time.monotonic()+20)
        assert 'error' not in recovered,recovered
        recovered_meta=json.loads(base64.b64decode(recovered['result']['data']))
        assert recovered_meta['sequence']==str(last_sequence+1),recovered_meta
        browser.release(recovered_meta)
        report['timeoutRecovery']=dict(ready=timed_ready,terminal=terminal,recoveredSequence=recovered_meta['sequence'])
        # pending时关闭CDP后结束整个独立进程；绝不向该会话归还或复用槽。
        disconnected=browser.request('Page.captureMideoFrame',dict(prepareForExternalTick=True))
        disconnected_ready=browser.ready(disconnected,last_sequence+2,time.monotonic()+20)
        assert disconnected_ready['ready'],disconnected_ready
        browser.socket.close()
        report['disconnectedPendingSession']=dict(sequence=disconnected_ready['sequence'],slotReused=False)
        report['passed']=True
    except BaseException as error:
        report['error']=repr(error)
        raise
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if browser:
            try:
                (output/'cdp-transcript.json').write_text(json.dumps(browser.transcript,ensure_ascii=False))
            finally:
                browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    verify(args.binary,args.output)

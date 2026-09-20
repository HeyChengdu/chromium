#!/usr/bin/env python3
"""下载固定 FFmpeg 提交的成功构建，验证提交与二进制校验和。"""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
lock = json.loads((root / 'ffmpeg-artifact-lock.json').read_text())
repo, commit = lock['repository'], lock['commit']
endpoint = f"repos/{repo}/actions/workflows/{lock['workflow']}/runs?head_sha={commit}&status=success&per_page=10"
runs = json.loads(subprocess.check_output(['gh', 'api', endpoint]))['workflow_runs']
if not runs:
    raise SystemExit('固定 FFmpeg 提交尚无成功构建，请先完成独立媒体构建。')
run = runs[0]
assert run['head_sha'] == commit
output = Path('media-artifact')
output.mkdir(exist_ok=True)
subprocess.run(['gh', 'run', 'download', str(run['id']), '--repo', repo,
                '--name', f'mideo-ffmpeg-linux-x64-{commit}', '--dir', str(output)], check=True)
assert (output / 'ffmpeg-commit.txt').read_text().strip() == commit
checks = dict(line.split()[::-1] for line in (output / 'SHA256SUMS').read_text().splitlines())
for name in ('ffmpeg', 'ffprobe'):
    assert hashlib.sha256((output / name).read_bytes()).hexdigest() == checks[name]
(output / 'source-artifact.json').write_text(json.dumps({'repository': repo, 'commit': commit, 'runId': run['id'], 'sha256': checks}, indent=2))

#!/usr/bin/env python3
"""仅在 Linux CI 构建与 Mideo 锁定来源相同的 FFmpeg，并加入共享帧输入。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

root = Path(__file__).resolve().parent
lock = json.loads((root / 'media-lock.json').read_text())
work = Path(os.environ['RUNNER_TEMP']) / 'mideo-media'
work.mkdir(exist_ok=True)
prefix = work / 'prefix'

def extract(key):
    spec = lock[key]
    data = urllib.request.urlopen(spec['url'], timeout=120).read()
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise RuntimeError(f'{key}: source checksum mismatch')
    archive = work / (key + '.tar')
    archive.write_bytes(data)
    destination = work / key
    destination.mkdir(exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(destination, filter='data')
    return next(p for p in destination.iterdir() if p.is_dir())

def run(command, cwd, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True)

x264 = extract('x264Source')
run(['./configure', f'--prefix={prefix}', '--enable-static', '--enable-pic', '--disable-cli', '--disable-opencl'], x264)
run(['make', '-j4'], x264)
run(['make', 'install'], x264)
lame = extract('lameSource')
run(['./configure', f'--prefix={prefix}', '--disable-shared', '--enable-static', '--with-pic', '--disable-frontend'], lame)
run(['make', '-j4'], lame)
run(['make', 'install'], lame)
ffmpeg = extract('ffmpegSource')
shutil.copyfile(root / 'ffmpeg/mideoshm.c', ffmpeg / 'libavformat/mideoshm.c')
with (ffmpeg / 'libavformat/Makefile').open('a') as f:
    f.write('\nOBJS-$(CONFIG_MIDEOSHM_DEMUXER) += mideoshm.o\n')
# 声明必须出现在生成的 demuxer_list.c 引用之前。
formats = ffmpeg / 'libavformat/allformats.c'
source = formats.read_text()
anchor = 'extern const FFInputFormat '
if anchor not in source or 'ff_mideoshm_demuxer' in source:
    raise RuntimeError('Unexpected FFmpeg demuxer registration layout')
source = source.replace(anchor, 'extern const FFInputFormat ff_mideoshm_demuxer;\n' + anchor, 1)
formats.write_text(source)
env = dict(os.environ, PKG_CONFIG_PATH=str(prefix / 'lib/pkgconfig'))
run(['./configure', f'--prefix={prefix}', *lock['ffmpegConfigure'],
     '--enable-demuxer=mideoshm', '--enable-swscale',
     f'--extra-cflags=-I{prefix}/include', f'--extra-ldflags=-L{prefix}/lib'], ffmpeg, env)
run(['make', '-j4'], ffmpeg, env)
run(['make', 'install'], ffmpeg, env)
output = Path(os.environ['GITHUB_WORKSPACE']) / 'media-artifact'
output.mkdir(exist_ok=True)
shutil.copy2(prefix / 'bin/ffmpeg', output)
shutil.copy2(prefix / 'bin/ffprobe', output)
for source, name in [(x264 / 'COPYING', 'x264-license.txt'), (lame / 'COPYING', 'lame-license.txt'), (ffmpeg / 'COPYING.GPLv2', 'ffmpeg-license.txt')]:
    shutil.copy2(source, output / name)
shutil.copy2(root / 'media-lock.json', output)

#!/usr/bin/env python3
"""相同提交的 CI 续编：仅为内容和权限相同的输入恢复原时间戳。"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs(root):
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')
                   and not (Path(directory) == root and d == 'out')]
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                yield path


def snapshot(root, manifest, commit):
    records = {}
    for path in inputs(root):
        stat = path.stat()
        records[str(path.relative_to(root))] = [stat.st_size, stat.st_mode,
                                               stat.st_mtime_ns, digest(path)]
    with gzip.open(manifest, 'wt') as stream:
        json.dump({'commit': commit, 'root': str(root), 'files': records}, stream)


def restore(root, manifest, commit):
    with gzip.open(manifest, 'rt') as stream:
        data = json.load(stream)
    if data['commit'] != commit or data['root'] != str(root):
        raise RuntimeError('Checkpoint commit or absolute build path mismatch')
    restored = 0
    # 不同输入必须使旧对象失效，不允许静默混用工具链或 hooks 产物。
    for name, (size, mode, mtime, sha) in data['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Invalid checkpoint path')
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f'Checkpoint input missing: {name}')
        # depot_tools 禁止自动更新的标记只检查存在性，内容包含每次运行时间。
        # update_depot_tools_toggle.py 写入时间；不作为编译输入恢复时间戳。
        if name == 'third_party/depot_tools/.disable_auto_update':
            if not path.read_text().startswith('Disabled by '):
                raise RuntimeError('Unexpected depot_tools sentinel format')
            continue
        stat = path.stat()
        if stat.st_size != size or stat.st_mode != mode or digest(path) != sha:
            raise RuntimeError(f'Checkpoint input changed: {name}')
        os.utime(path, ns=(stat.st_atime_ns, mtime))
        restored += 1
    print(f'Restored timestamps for {restored} content-verified inputs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['snapshot', 'restore'])
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    globals()[args.mode](root, args.manifest, commit)

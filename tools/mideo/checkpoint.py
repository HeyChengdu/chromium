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
    verified = []
    differences = []
    # 不同输入必须使旧对象失效，不允许静默混用工具链或 hooks 产物。
    for name, (size, mode, mtime, sha) in data['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Invalid checkpoint path')
        path = root / relative
        if not path.is_file() or path.is_symlink():
            differences.append({'path': name, 'reason': 'missing'})
            continue
        # depot_tools 禁止自动更新的标记只检查存在性，内容包含每次运行时间。
        # update_depot_tools_toggle.py 写入时间；不作为编译输入恢复时间戳。
        if name == 'third_party/depot_tools/.disable_auto_update':
            if not path.read_text().startswith('Disabled by '):
                raise RuntimeError('Unexpected depot_tools sentinel format')
            continue
        stat = path.stat()
        actual_sha = digest(path)
        if stat.st_size != size or stat.st_mode != mode or actual_sha != sha:
            differences.append({'path': name, 'reason': 'changed',
                                'old_size': size, 'new_size': stat.st_size,
                                'old_mode': mode, 'new_mode': stat.st_mode,
                                'old_sha256': sha, 'new_sha256': actual_sha})
            continue
        verified.append((path, stat.st_atime_ns, mtime))
    if differences:
        report = manifest.with_name('restore-differences.json')
        report.write_text(json.dumps(differences, indent=2))
        for difference in differences:
            print(json.dumps(difference), flush=True)
        raise RuntimeError(f'Checkpoint input changed or missing: {len(differences)}; see {report}')
    # 全部核验通过后才修改时间戳，失败不留下部分恢复状态。
    for path, atime, mtime in verified:
        os.utime(path, ns=(atime, mtime))
    print(f'Restored timestamps for {len(verified)} content-verified inputs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['snapshot', 'restore'])
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    globals()[args.mode](root, args.manifest, commit)

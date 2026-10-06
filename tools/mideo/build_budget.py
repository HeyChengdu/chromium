"""有界运行编译命令；归档前收敛编译进程组。"""
import os
import signal
import subprocess
import sys
import time


def signal_group(group, sig):
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        pass


def live_group(group):
    rows = subprocess.check_output(['ps', '-eo', 'pid=,pgid=,stat=,args='], text=True).splitlines()
    return [row for row in rows if len(row.split()) >= 3
            and row.split()[1] == str(group) and not row.split()[2].startswith('Z')]


def run(command, budget, grace=60):
    process = subprocess.Popen(command, start_new_session=True)
    try:
        result = process.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        print('编译预算结束，向完整进程组发送 SIGINT', flush=True)
        signal_group(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        result = 124
    finally:
        # 启动器退出不证明 Ninja 或生成器已退出；归档前清除同组存活者。
        signal_group(process.pid, signal.SIGKILL)
        process.wait()
        deadline = time.monotonic() + 10
        while live_group(process.pid):
            if time.monotonic() >= deadline:
                raise RuntimeError('编译进程组未收敛，禁止归档：' + repr(live_group(process.pid)))
            time.sleep(.1)
        print('编译进程组已收敛，无存活输出写入者', flush=True)
    return result


if __name__ == '__main__':
    if len(sys.argv) < 3 or float(sys.argv[1]) <= 0:
        raise SystemExit('需要正数秒预算和编译命令')
    result = run(sys.argv[2:], float(sys.argv[1]))
    raise SystemExit(result if result >= 0 else 128 - result)

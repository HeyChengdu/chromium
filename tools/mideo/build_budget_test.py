"""通过真实进程验证编译窗口结束后不再有存活的输出写入者。"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('build_budget', Path(__file__).with_name('build_budget.py'))
build_budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_budget)


class BuildBudgetTest(unittest.TestCase):
    def test_success_preserves_exit_code(self):
        self.assertEqual(build_budget.run([sys.executable, '-c', 'pass'], 5, 1), 0)

    def test_failure_preserves_exit_code(self):
        self.assertEqual(build_budget.run([sys.executable, '-c', 'raise SystemExit(7)'], 5, 1), 7)

    def test_budget_kills_parent_that_ignores_interrupt(self):
        command = 'import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(60)'
        self.assertEqual(build_budget.run([sys.executable, '-c', command], .5, .1), 124)

    def test_budget_stops_writer_even_when_parent_exits_on_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'writer.pid'
            child = "import os,signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); open(%r,'w').write(str(os.getpid())); time.sleep(60)" % str(pid_file)
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(60)" % child
            try:
                self.assertEqual(build_budget.run([sys.executable, '-c', parent], 1, .1), 124)
                self.assertTrue(pid_file.exists(), '实际写入子进程必须已启动')
                pid = int(pid_file.read_text())
                rows = subprocess.check_output(['ps', '-eo', 'pid=,stat='], text=True).splitlines()
                alive = [row for row in rows if row.split()[0] == str(pid) and not row.split()[1].startswith('Z')]
                self.assertEqual(alive, [], '归档前不能留下忽略 SIGINT 的写入者')
            finally:
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()

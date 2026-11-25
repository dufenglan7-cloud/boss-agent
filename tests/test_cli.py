# 通过当前测试解释器运行 CLI，验证退出码和演示输出。
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class CliTests(unittest.TestCase):
    # 无 Key 演示成功；空简历失败并返回非零退出码。
    def test_demo_without_key_and_bad_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "resume.md").write_text("【虚构示例】Python", encoding="utf-8")
            (root / "jobs.json").write_text("[]", encoding="utf-8")
            command = [sys.executable, "-m", "boss_agent.cli", "--resume", str(root / "resume.md"),
                       "--jobs", str(root / "jobs.json"), "--output", str(root / "plan.json"),
                       "--db", str(root / "test.db")]
            env = dict(os.environ, BOSS_AGENT_API_KEY="", BOSS_AGENT_MODEL="")
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("real applications sent=0", result.stdout)
            self.assertEqual(json.loads((root / "plan.json").read_text())["matches"], [])
            (root / "resume.md").write_text("  ")
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Resume must not be empty", result.stderr)

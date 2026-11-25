# 完整计划接口回归，使用临时数据库，不访问招聘平台。
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from boss_agent.main import create_app
from boss_agent.api.dependencies import get_agents_factory
from boss_agent.agents import Agents

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class ApiTests(unittest.TestCase):
    # 每个用例独立创建临时状态，避免测试共享业务数据库。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.app = create_app(self.db)
        self.client = self.enterContext(TestClient(self.app))
        self.payload = dict(resume_text="Python", jobs=[dict(id="1", title="dev", company="test", city="杭州", description="Python")])

    # 服务应能提供健康检查和接口规范，且明确真实投递关闭。
    def test_health_and_openapi(self):
        self.assertFalse(self.client.get("/health").json()["real_submission_enabled"])
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/plans", spec["paths"])

    # 计划在应用重新构造后仍可读取，不能只保存在进程内存中。
    def test_create_then_fetch_and_restart(self):
        response = self.client.post("/api/v1/plans", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["applications_sent"], 0)
        self.assertEqual(data["status"], "planned")
        with TestClient(create_app(self.db)) as reopened:
            fetched = reopened.get("/api/v1/plans/" + data["run_id"])
            self.assertEqual(fetched.json(), data)

    # 空白简历、非法模式、重复职位和不完整职位需在入口拒绝。
    def test_invalid_inputs(self):
        for changes in [dict(resume_text="  "), dict(mode="send"), dict(jobs=self.payload["jobs"] * 2), dict(jobs=[{}])]:
            with self.subTest(changes=changes):
                response = self.client.post("/api/v1/plans", json=self.payload | changes)
                self.assertEqual(response.status_code, 422)

    # 不存在的计划返回 404，不生成虚构记录。
    def test_unknown_plan(self):
        self.assertEqual(self.client.get("/api/v1/plans/missing").status_code, 404)

    # 模型模式缺配置时返回 503，不回退成伪造的智能分析。
    def test_missing_model_configuration(self):
        with patch.dict(os.environ, {"BOSS_AGENT_API_KEY": "", "BOSS_AGENT_MODEL": ""}):
            response = self.client.post("/api/v1/plans", json=self.payload | {"mode": "llm"})
        self.assertEqual(response.status_code, 503)

    # 上游异常中的密钥或私人资料标记不得出现在 HTTP 错误正文。
    def test_model_failure_is_sanitized(self):
        # 故意失败的依赖替身，用于证明错误不会被静默吞掉。
        class FailingAgents(Agents):
            def profile(self, resume, requirements):
                raise ValueError("secret-api-key and private-resume")
        self.app.dependency_overrides[get_agents_factory] = lambda: lambda mode: FailingAgents(None)
        response = self.client.post("/api/v1/plans", json=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-api-key", response.text)
        self.assertNotIn("private-resume", response.text)

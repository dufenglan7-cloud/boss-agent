# 组合服务回归：跳过岗位、空输入与存储查询边界。
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from boss_agent.agents import Agents
from boss_agent.channels import MemoryJobSource
from boss_agent.models import Profile, Job, Match
from boss_agent.storage import Store
from boss_agent.workflow import Workflow

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class ServiceTests(unittest.TestCase):
    # 被跳过的职位不能继续生成沟通文案。
    def test_skipped_job_never_gets_draft(self):
        agents = Mock(spec=Agents)
        agents.profile.return_value = Profile(experience_summary="Python")
        agents.match.return_value = Match(job_id="1", decision="skip", evidence=["城市不符"])
        jobs = [Job(id="1", title="dev", company="test", city="上海", description="Python")]
        plan = Workflow(agents, MemoryJobSource(jobs)).run("Python", "杭州")
        self.assertEqual(plan.drafts, [])
        agents.draft.assert_not_called()

    # 空职位列表可返回空计划，空白简历仍应拒绝。
    def test_empty_jobs_and_resume(self):
        workflow = Workflow(Agents(None), MemoryJobSource([]))
        plan = workflow.run("Python", "")
        self.assertEqual(plan.matches, [])
        with self.assertRaises(ValueError):
            workflow.run("  ", "")

    # 带 SQL 特殊字符的标识只能作为值处理，不能改变查询条件。
    def test_sql_like_id_is_literal(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.db")
            plan = Workflow(Agents(None), MemoryJobSource([])).run("Python", "")
            run_id = "x' OR 1=1 --"
            store.save(run_id, "demo", plan)
            self.assertIsNone(store.get("missing"))
            self.assertEqual(store.get(run_id)["plan"], plan)

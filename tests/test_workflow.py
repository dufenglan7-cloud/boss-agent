# 兼容完整计划工具的离线回归，固定示例不代表真实匹配质量。
import json
import tempfile
import unittest
from pathlib import Path
from boss_agent.agents import Agents
from boss_agent.channels import JsonJobSource, BossBrowserChannel
from boss_agent.models import Profile, Job
from boss_agent.storage import Store
from boss_agent.workflow import Workflow

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class WorkflowTests(unittest.TestCase):
    # 明确城市与薪资条件应使不符合的职位跳过。
    def test_explicit_constraints_skip(self):
        agent = Agents(None)
        profile = Profile(experience_summary="test", cities=["杭州"], minimum_monthly_salary=20000)
        job = Job(id="1", title="dev", company="test", city="上海", description="Python")
        self.assertEqual(agent.match("Python", "杭州", profile, job).decision, "skip")
        job.city = "杭州"
        job.salary_max = 15000
        self.assertEqual(agent.match("Python", "", profile, job).decision, "skip")

    # 组合计划可落库，但浏览器占位接口仍明确未实现。
    def test_graph_persistence_and_no_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            jobs = path / "jobs.json"
            jobs.write_text(json.dumps([dict(id="1", title="dev", company="test", city="杭州", description="Python")]))
            plan = Workflow(Agents(None), JsonJobSource(jobs)).run("Python", "杭州")
            self.assertEqual(len(plan.drafts), 1)
            self.assertEqual(plan.matches[0].decision, "consider")
            Store(path / "test.db").save("run-1", "demo", plan)
            with self.assertRaises(NotImplementedError):
                BossBrowserChannel().search([])

    # 导入源拒绝重复 ID，防止后续结果关联含糊。
    def test_duplicate_jobs_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "jobs.json"
            job = dict(id="1", title="dev", company="test", city="杭州", description="Python")
            path.write_text(json.dumps([job, job]))
            with self.assertRaises(ValueError):
                JsonJobSource(path).search([])

if __name__ == "__main__":
    unittest.main()

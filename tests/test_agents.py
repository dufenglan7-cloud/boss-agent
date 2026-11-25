# 子 Agent 契约回归：必要证据读取、硬约束、岗位关联和文案引用。
import unittest
from boss_agent.agents import Agents
from boss_agent.models import Profile, Job, Match, Draft
from boss_agent.harness import HarnessLimit
from boss_agent.harness.protocol import Decision

# 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
class FixtureModel:
    def __init__(self, final):
        self.final = final
        self.seen = []
    # 按脚本返回结构化决策，并可记录模型实际看到的观察内容。
    def ask(self, prompt, data, schema):
        self.seen.append(data)
        successful = {x['action']['tool'] for x in data['history'] if x.get('action', {}).get('action') == 'tool' and x['observation']['status'] == 'completed'}
        for tool in data['required_reads']:
            if tool not in successful:
                return Decision(action='tool', tool=tool)
        return Decision(action='final', result=self.final.model_dump())

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class AgentTests(unittest.TestCase):
    # 每个用例独立创建临时状态，避免测试共享业务数据库。
    def setUp(self):
        self.job = Job(id='1', title='dev', company='excluded', city='杭州', description='Python')
        self.profile = Profile(experience_summary='Python')
        self.match = Match(job_id='1', decision='recommend', evidence=['Python'])

    # 模型必须先看到排除原因，再给出符合硬约束的结果。
    def test_hard_exclusion_is_observed_then_respected(self):
        self.profile.excluded_companies = ['excluded']
        model = FixtureModel(Match(job_id='1', decision='skip', evidence=['公司排除']))
        result = Agents(model).match('Python', '', self.profile, self.job)
        self.assertEqual(result.decision, 'skip')
        self.assertEqual(len(model.seen), 3)
        self.assertEqual(model.seen[-1]['history'][-1]['observation']['data']['hard_skip_reason'], '公司在排除列表中')

    # 模型持续尝试违背硬约束时，应耗尽预算并失败，不接受违规推荐。
    def test_model_cannot_override_hard_skip(self):
        self.profile.excluded_companies = ['excluded']
        with self.assertRaises(HarnessLimit):
            Agents(FixtureModel(self.match)).match('Python', '', self.profile, self.job)

    # 模型把结果关联到其他职位时，最终输出不能通过校验。
    def test_wrong_job_rejected(self):
        model = FixtureModel(Match(job_id='other', decision='recommend', evidence=[]))
        with self.assertRaises(HarnessLimit):
            Agents(model).match('Python', '', self.profile, self.job)

    # 缺失、空白或不在简历中的文案依据必须被拒绝。
    def test_invalid_draft_evidence_rejected(self):
        for evidence in [[], ['Rust'], [' ']]:
            with self.subTest(evidence=evidence):
                model = FixtureModel(Draft(job_id='1', message='test', resume_evidence=evidence))
                with self.assertRaises(HarnessLimit):
                    Agents(model).draft('掌握 Python', '', self.job, self.match)

    # 合法文案需在读取真实证据后输出。
    def test_valid_draft_reads_evidence_before_final(self):
        model = FixtureModel(Draft(job_id='1', message='掌握 Python', resume_evidence=['Python']))
        self.assertEqual(Agents(model).draft('掌握 Python', '', self.job, self.match).job_id, '1')
        self.assertEqual(model.seen[-1]['history'][0]['observation']['data']['resume'], '掌握 Python')

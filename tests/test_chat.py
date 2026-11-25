# 会话回归：持久化、上下文、版本和计划失效规则。
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from boss_agent.main import create_app
from boss_agent.api.dependencies import get_agents_factory
from boss_agent.agents import Agents
from boss_agent.agents.profile import ProfileAgent
from boss_agent.agents.matching import MatchingAgent
from boss_agent.agents.drafting import DraftingAgent
from boss_agent.agents.conversation import ConversationAgent
from boss_agent.agents.conversation.schemas import ConversationReply
from boss_agent.storage import Store, SessionConflict

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class ChatTests(unittest.TestCase):
    # 每个用例独立创建临时状态，避免测试共享业务数据库。
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temp) / 'test.db'
        self.app = create_app(self.path)
        self.client = self.enterContext(TestClient(self.app))
        self.session = self.client.post('/api/v1/sessions', json={'mode': 'demo'}).json()
        self.url = '/api/v1/sessions/' + self.session['id']
        self.job = dict(id='demo', title='虚构岗位', company='虚构公司', city='杭州', description='Python')

    def turn(self, **kwargs):
        return self.client.post(self.url + '/messages', json={'version': 0, 'message': '杭州，Python 开发', **kwargs})

    # 首页和静态资源应由同一服务正常提供。
    def test_home_and_static_assets(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        for path in ['/static/app.js', '/static/styles.css']:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIn('求职工作台', self.client.get('/').text)

    # 注册器中每项能力都必须是独立的 Agent 实例和模块。
    def test_distinct_agents(self):
        agents = Agents(None)
        for field, cls in [('profile_agent', ProfileAgent), ('matching_agent', MatchingAgent),
                           ('drafting_agent', DraftingAgent), ('conversation_agent', ConversationAgent)]:
            self.assertIsInstance(getattr(agents, field), cls)
        self.assertNotEqual(ProfileAgent.__module__, MatchingAgent.__module__)

    # 演示回复也需明确标记并正确落库，应用重建后保留会话。
    def test_demo_chat_persists_and_reload(self):
        result = self.turn(resume_text='【虚构】Python', jobs=[self.job])
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertIn('演示模式', data['session']['messages'][-1]['content'])
        self.assertEqual(data['session']['version'], 1)
        self.assertEqual(data['applications_sent'], 0)
        with TestClient(create_app(self.path)) as reopened:
            self.assertEqual(reopened.get(self.url).json(), data['session'])
        listing = self.client.get('/api/v1/sessions').json()
        self.assertEqual(listing[0]['id'], self.session['id'])
        self.assertNotIn('resume_text', listing[0])

    # 后续消息必须带上之前的对话及当前资料，而非每次从零开始。
    def test_multiple_turns_reach_conversation_agent(self):
        agents = Agents(None)
        agents.conversation_agent = Mock()
        agents.conversation_agent.generated_plan = None
        agents.conversation_agent.run.return_value = ConversationReply(reply='请补充薪资要求', suggested_roles=['Python 开发'])
        self.app.dependency_overrides[get_agents_factory] = lambda: lambda mode: agents
        self.assertEqual(self.turn(resume_text='Python').status_code, 200)
        result = self.turn(version=1, message='更倾向杭州，薪资 20K')
        self.assertEqual(result.status_code, 200)
        history, resume, count = agents.conversation_agent.run.call_args.args
        self.assertIn('杭州，Python 开发', [item['content'] for item in history])
        self.assertEqual(history[-1]['content'], '更倾向杭州，薪资 20K')
        self.assertEqual(resume, 'Python')
        self.assertEqual(result.json()['session']['suggested_roles'], ['Python 开发'])

    # 普通知识问答保留计划，相关资料变化才使旧计划失效。
    def test_plan_saved_and_preserved_by_unrelated_question(self):
        result = self.turn(action='plan', resume_text='【虚构】Python', jobs=[self.job])
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(len(data['plan']['matches']), 1)
        run_id = data['session']['last_plan_id']
        self.assertEqual(self.client.get('/api/v1/plans/' + run_id).status_code, 200)
        result = self.turn(version=1, message='岗位一般需要哪些技能？')
        self.assertEqual(result.json()['session']['last_plan_id'], run_id)
        result = self.turn(version=2, message='更新简历', resume_text='新简历')
        self.assertIsNone(result.json()['session']['last_plan_id'])

    # 任务资料缺失或请求无效时，不能提交半条会话。
    def test_missing_context_and_invalid_input_do_not_change_history(self):
        for body in [{'action': 'plan'}, {'message': ' '}, {'jobs': [self.job, self.job]}]:
            self.assertEqual(self.turn(**body).status_code, 422)
        self.assertEqual(self.client.get(self.url).json()['version'], 0)

    # 过期版本返回冲突，并且不覆盖已经保存的新消息。
    def test_stale_version_conflict(self):
        self.assertEqual(self.turn().status_code, 200)
        self.assertEqual(self.turn(message='过期消息').status_code, 409)
        self.assertNotIn('过期消息', [m['content'] for m in self.client.get(self.url).json()['messages']])

    # 缺配置失败后会话版本不变，配置完成后仍可基于原版本重试。
    def test_missing_model_configuration_is_retryable(self):
        session = self.client.post('/api/v1/sessions', json={'mode': 'llm'}).json()
        with patch.dict('os.environ', {'BOSS_AGENT_API_KEY': '', 'BOSS_AGENT_MODEL': ''}):
            result = self.client.post('/api/v1/sessions/' + session['id'] + '/messages', json={'version': 0, 'message': '你好'})
        self.assertEqual(result.status_code, 503)
        data = self.client.get('/api/v1/sessions/' + session['id']).json()
        self.assertEqual(data['version'], 0)
        self.assertEqual(len(data['messages']), 1)

    # 不存在的会话无论读取还是发送都应返回 404。
    def test_unknown_session(self):
        self.assertEqual(self.client.get('/api/v1/sessions/missing').status_code, 404)
        response = self.client.post('/api/v1/sessions/missing/messages', json={'version': 0, 'message': '你好'})
        self.assertEqual(response.status_code, 404)

    # 只有持有旧版本的第一个写入可以成功，后续旧快照不能覆盖。
    def test_storage_cas_prevents_lost_update(self):
        store = Store(self.path)
        original = store.get_session(self.session['id'])
        changed = dict(original, version=1, title='first writer')
        store.commit_session(self.session['id'], 0, changed)
        with self.assertRaises(SessionConflict):
            store.commit_session(self.session['id'], 0, dict(original, version=1, title='second writer'))
        self.assertEqual(store.get_session(self.session['id'])['title'], 'first writer')

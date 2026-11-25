# 按用户长期记忆回归：姓名、隔离、证据、时效和跨会话并发。
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote
from fastapi.testclient import TestClient
from boss_agent.main import create_app
from boss_agent.api.dependencies import get_agents_factory
from boss_agent.agents import Agents
from boss_agent.harness.protocol import Decision
from boss_agent.portraits.models import UserPortrait
from boss_agent.portraits.service import apply_fact, active_portrait, now_utc, retrieval_context
from boss_agent.storage import Store, SessionConflict

# 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
class Model:
    def __init__(self, result): self.result=result;self.seen=[]
    # 按脚本返回结构化决策，并可记录模型实际看到的观察内容。
    def ask(self, prompt, data, schema):
        self.seen.append(data)
        return Decision(action='final',result=self.result)

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class PortraitTests(unittest.TestCase):
    # 每个用例独立创建临时状态，避免测试共享业务数据库。
    def setUp(self):
        temp=self.enterContext(tempfile.TemporaryDirectory())
        self.path=Path(temp)/'test.db'
        self.app=create_app(self.path)
        self.client=self.enterContext(TestClient(self.app))
        self.a={'X-User-ID':quote('测试甲')}
        self.b={'X-User-ID':quote('测试乙')}

    def create(self, headers=None, mode='llm'):
        r=self.client.post('/api/v1/sessions',headers=headers or self.a,json={'mode':mode})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()

    def get_portrait(self, headers=None, mode='llm'):
        return self.client.get('/api/v1/users/me/portrait?mode='+mode,headers=headers or self.a).json()

    def model(self, result):
        model=Model(result)
        self.app.dependency_overrides[get_agents_factory]=lambda:lambda mode:Agents(model)
        return model

    def send(self, session, message, headers=None, **data):
        return self.client.post('/api/v1/sessions/'+session['id']+'/messages',headers=headers or self.a,
            json={'version':session['version'],'message':message,**data})

    # 中文姓名编码可用，其他姓名不能在逻辑分区中读取或写入该会话。
    def test_chinese_name_and_session_ownership(self):
        a=self.create();b=self.create(self.b)
        self.assertEqual(a['user_id'],'测试甲')
        listing=self.client.get('/api/v1/sessions',headers=self.a).json()
        self.assertEqual([r['id'] for r in listing],[a['id']])
        self.assertEqual(self.client.get('/api/v1/sessions/'+a['id'],headers=self.b).status_code,404)
        self.assertEqual(self.send(a,'hello',headers=self.b).status_code,404)
        self.assertEqual(self.client.get('/api/v1/sessions',headers={'X-User-ID':'invalid!'}).status_code,422)

    # 同用户新会话继承记忆，不同用户画像保持独立。
    def test_memory_crosses_sessions_and_users_are_isolated(self):
        model=self.model({'reply':'已记录技能和杭州意向。','intent':'preferences',
            'preference_updates':[{'field':'cities','value':['杭州'],'evidence':'我想去杭州'}],
            'portrait_updates':[{'field':'skills','value':'Python','source':'message','evidence':'我会 Python'}]})
        session=self.create()
        r=self.send(session,'我会 Python，我想去杭州')
        self.assertEqual(r.status_code,200,r.text)
        portrait=self.get_portrait()
        self.assertEqual(portrait['revision'],1)
        self.assertEqual(portrait['facts'][0]['source'],'message')
        new=self.create()
        self.assertEqual(new['preferences']['cities'],['杭州'])
        self.assertEqual(new['user_portrait']['facts'][0]['value'],'Python')
        self.assertEqual(self.get_portrait(self.b)['facts'],[])
        model.result={'reply':'继续参考你的 Python 背景。','intent':'question'}
        self.assertEqual(self.send(new,'继续聊聊').status_code,200)
        self.assertEqual(model.seen[-1]['task']['input']['user_portrait']['facts'][0]['value'],'Python')

    # 本轮临时侧重只覆盖会话，不替换下一次新会话的长期偏好。
    def test_session_priority_does_not_replace_long_term_preference(self):
        model=self.model({'reply':'已记录杭州。','intent':'preferences','preference_updates':[{'field':'cities','value':['杭州'],'evidence':'长期想去杭州'}]})
        session=self.create()
        session=self.send(session,'长期想去杭州').json()['session']
        model.result={'reply':'这次先看上海。','intent':'preferences','preference_updates':[{'field':'cities','value':['上海'],'evidence':'这次先看上海','scope':'session'}]}
        session=self.send(session,'这次先看上海').json()['session']
        self.assertEqual(session['preferences']['cities'],['上海'])
        self.assertEqual(self.get_portrait()['preferences']['cities'],['杭州'])
        self.assertEqual(self.create()['preferences']['cities'],['杭州'])
        model.result={'reply':'继续本轮上海方向。','intent':'question'}
        session=self.send(session,'继续').json()['session']
        self.assertEqual(session['preferences']['cities'],['上海'])

    # 近期关注过期后不进入上下文，检索结果数量受限制且保留相关技能。
    def test_focus_expiry_and_context_limit(self):
        portrait=UserPortrait(user_id='a',mode='llm')
        apply_fact(portrait,'current_focus','Agent 应用开发','最近关注 Agent','message')
        apply_fact(portrait,'skills','Python','我会 Python','message')
        self.assertEqual(len(active_portrait(portrait)['facts']),2)
        future=now_utc()+timedelta(days=31)
        self.assertEqual([f['value'] for f in active_portrait(portrait,future)['facts']],['Python'])
        for i in range(30): apply_fact(portrait,'skills',f'技能{i}',f'技能{i}','message')
        self.assertLessEqual(len(retrieval_context(portrait,'Python')['facts']),24)
        self.assertIn('Python',[f['value'] for f in retrieval_context(portrait,'Python')['facts']])

    # 手工纠正、清除与版本冲突可用，演示和模型画像互不污染。
    def test_edit_reset_and_mode_separation(self):
        self.create()
        p=self.get_portrait()
        r=self.client.put('/api/v1/users/me/portrait?mode=llm',headers=self.a,
            json={'revision':p['revision'],'skills':['Python'],'experience':['问答应用开发'],'current_focus':'Agent 岗位'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.get_portrait(mode='demo')['facts'],[])
        self.assertEqual(len(self.create()['user_portrait']['facts']),3)
        reset=self.client.delete('/api/v1/users/me/portrait?mode=llm&revision=1',headers=self.a)
        self.assertEqual(reset.status_code,200)
        self.assertEqual(reset.json()['facts'],[])
        self.assertEqual(self.create()['user_portrait']['facts'],[])
        stale=self.client.put('/api/v1/users/me/portrait?mode=llm',headers=self.a,json={'revision':1,'skills':['Rust']})
        self.assertEqual(stale.status_code,409)

    # 仅询问岗位技能不会自动生成用户职业事实或提高画像版本。
    def test_no_portrait_updates_from_role_question(self):
        self.model({'reply':'通常需要 Python。','intent':'question'})
        session=self.create()
        self.assertEqual(self.send(session,'岗位需要什么技能').status_code,200)
        self.assertEqual(self.get_portrait()['facts'],[])
        self.assertEqual(self.get_portrait()['revision'],0)

    # 无依据更新或在问答中夹带事实更新必须被拒绝。
    def test_invalid_evidence_and_question_memory_are_rejected(self):
        for intent,evidence in [('preferences','不存在的引文'),('question','岗位需要 Python')]:
            with self.subTest(intent=intent):
                self.model({'reply':'test','intent':intent,'portrait_updates':[{'field':'skills','value':'Python','source':'message','evidence':evidence}]})
                session=self.create()
                self.assertEqual(self.send(session,'岗位需要 Python').status_code,429)
                self.assertEqual(self.get_portrait()['facts'],[])

    # 画像版本冲突时，整笔会话写入必须回滚。
    def test_conflicting_cross_session_update_rolls_back_chat(self):
        session=self.create()
        store=Store(self.path)
        portrait=store.get_portrait('测试甲','llm')
        changed=dict(portrait,revision=1)
        store.replace_portrait('测试甲','llm',0,changed)
        with self.assertRaises(SessionConflict):
            store.commit_session(session['id'],0,dict(session,version=1),portrait_record=('测试甲','llm',0,dict(portrait,revision=1)))
        self.assertEqual(store.get_session(session['id'])['version'],0)
        self.assertEqual(store.get_portrait('测试甲','llm')['revision'],1)

    # 旧计划表可补齐用户归属字段，重复初始化也可使用。
    def test_old_database_migration(self):
        path=self.path.parent/'legacy.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE plans(run_id TEXT PRIMARY KEY,mode TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT)')
        Store(path)
        Store(path)
        with sqlite3.connect(path) as db:
            self.assertIn('user_id',{r[1] for r in db.execute('PRAGMA table_info(plans)')})

    # 计划和执行记录的 HTTP 查询均须遵守当前标识归属。
    def test_plans_and_execution_are_user_scoped(self):
        session=self.create(mode='demo')
        r=self.send(session,'生成计划',action='plan',resume_text='虚构 Python',jobs=[dict(id='1',title='demo',company='虚构',city='杭州',description='Python')])
        self.assertEqual(r.status_code,200,r.text)
        state=r.json()['session']
        plan='/api/v1/plans/'+state['last_plan_id']
        trace='/api/v1/sessions/'+state['id']+'/executions/'+state['last_execution']['run_id']
        self.assertEqual(self.client.get(plan,headers=self.a).status_code,200)
        self.assertEqual(self.client.get(plan,headers=self.b).status_code,404)
        self.assertEqual(self.client.get(trace,headers=self.b).status_code,404)

    # 只有观察过简历分析结果的原文事实才能进入长期画像并被新会话读取。
    def test_resume_facts_are_observed_and_persisted(self):
        # 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
        class ResumeModel:
            # 按脚本返回结构化决策，并可记录模型实际看到的观察内容。
            def ask(self, prompt, data, schema):
                if data['task']['agent']=='conversation':
                    if not data['history']:
                        return Decision(action='tool',tool='analyze_resume')
                    return Decision(action='final',result={'reply':'有 Python 项目经历。','intent':'resume_analysis',
                        'portrait_updates':[{'field':'skills','value':'Python','source':'resume','evidence':'Python'}]})
                if not data['history']:
                    return Decision(action='tool',tool='read_resume')
                return Decision(action='final',result={'skills':['Python'],'experience_summary':'Python 问答应用','suggested_roles':['应用开发']})
        self.app.dependency_overrides[get_agents_factory]=lambda:lambda mode:Agents(ResumeModel())
        session=self.create()
        result=self.send(session,'根据简历看看适合什么方向',resume_text='Python 问答应用开发')
        self.assertEqual(result.status_code,200,result.text)
        fact=self.create()['user_portrait']['facts'][0]
        self.assertEqual(fact['source'],'resume')
        self.assertEqual(fact['evidence'],'Python')

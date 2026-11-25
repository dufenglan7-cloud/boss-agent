# 用脚本模型验证自由调度、观察反馈、预算和运行审计。
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import BaseModel
from boss_agent.harness import RunContext, ReActRunner, Tool, EmptyArgs, HarnessLimit
from boss_agent.harness.protocol import Decision
from boss_agent.agents import Agents
from boss_agent.agents.conversation.schemas import Preferences
from boss_agent.main import create_app
from boss_agent.api.dependencies import get_agents_factory

# 测试使用的最小结构化输出，便于隔离验证运行器契约。
class Answer(BaseModel):
    answer: str

# 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
class ScriptModel:
    def __init__(self, fn):
        self.fn = fn
        self.seen = []
    # 按脚本返回结构化决策，并可记录模型实际看到的观察内容。
    def ask(self, prompt, data, schema):
        self.seen.append(data)
        return self.fn(data)

def final(**result):
    return Decision(action='final', result=result)

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class HarnessTests(unittest.TestCase):
    # 检索结果必须进入下一次模型调用，不能直接冒充最终答案。
    def test_observation_must_reach_model_before_final(self):
        # 模拟模型分步决策，让断言验证前一步观察是否进入下一步。
        def fn(data):
            if not data['history']:
                return Decision(action='tool', tool='read')
            self.assertEqual(data['history'][0]['observation']['data'], {'fact': 'retrieved'})
            return final(answer='based on retrieved')
        model=ScriptModel(fn)
        result=ReActRunner(model).run('test', '', {}, Answer,
            tools=[Tool('read','read fact',EmptyArgs,lambda _: {'fact':'retrieved'})])
        self.assertEqual(result.answer, 'based on retrieved')
        self.assertEqual(len(model.seen), 2)

    # 非法工具和多余参数只形成失败观察，处理函数不能实际执行。
    def test_unknown_tool_and_bad_arguments_are_observations(self):
        calls=[]
        # 模拟模型分步决策，让断言验证前一步观察是否进入下一步。
        def fn(data):
            step=len(data['history'])
            if step==0: return Decision(action='tool',tool='shell',arguments={'cmd':'bad'})
            if step==1: return Decision(action='tool',tool='read',arguments={'unexpected':True})
            self.assertTrue(all(x['observation']['status']=='failed' for x in data['history']))
            return final(answer='recovered')
        runner=ReActRunner(ScriptModel(fn))
        result=runner.run('test','',{},Answer,tools=[Tool('read','',EmptyArgs,lambda _: calls.append(1))])
        self.assertEqual(result.answer,'recovered')
        self.assertEqual(calls,[])

    # 模型可在剩余步骤内根据错误观察修正动作或结果结构。
    def test_invalid_decision_and_final_recover(self):
        # 模拟模型分步决策，让断言验证前一步观察是否进入下一步。
        def fn(data):
            step=len(data['history'])
            if step==0: return {'action':'unknown'}
            if step==1: return final(wrong='invalid')
            return final(answer='corrected')
        result=ReActRunner(ScriptModel(fn)).run('test','',{},Answer)
        self.assertEqual(result.answer,'corrected')

    # 持续不结束的 Agent 受共享调用预算终止，且任务栈最终清空。
    def test_budget_stops_nonterminating_agent(self):
        ctx=RunContext(max_calls=2)
        runner=ReActRunner(ScriptModel(lambda _: Decision(action='tool',tool='unknown')),ctx)
        with self.assertRaises(HarnessLimit): runner.run('test','',{},Answer)
        self.assertEqual(ctx.calls,2)
        self.assertEqual(ctx.events[-1]['kind'],'task_failed')
        self.assertEqual(ctx.stack,[])

    # 嵌套过深或已经超时的任务不能继续执行。
    def test_depth_and_deadline(self):
        ctx=RunContext(max_depth=1)
        runner=ReActRunner(None,ctx)
        tool=Tool('child','',EmptyArgs,lambda _: runner.run('child','',{},Answer,demo_result={'answer':'no'}))
        with self.assertRaises(HarnessLimit):
            runner.run('root','',{},Answer,tools=[tool],required=['child'])
        with self.assertRaises(HarnessLimit):
            ReActRunner(None,RunContext(max_seconds=0)).run('expired','',{},Answer)

    # 岗位知识咨询允许直接答复，不强制调用画像等子 Agent。
    def test_question_does_not_dispatch_children_or_require_context(self):
        model=ScriptModel(lambda _: final(reply='一般需要 Python、工具调用与工程实践。',intent='question'))
        agents=Agents(model)
        result=agents.conversation_agent.run([{'role':'user','content':'Agent 岗位需要什么技能？'}],'',0)
        self.assertEqual(result.intent,'question')
        self.assertEqual([e['agent'] for e in agents.context.events if e['kind']=='task_started'],['conversation'])

    # 主 Agent 看到画像子 Agent 返回的事实后再答复，并保留父子任务关系。
    def test_resume_analysis_dispatches_child_and_reobserves_result(self):
        # 模拟模型分步决策，让断言验证前一步观察是否进入下一步。
        def fn(data):
            name=data['task']['agent']
            if name=='conversation':
                if not data['history']: return Decision(action='tool',tool='analyze_resume')
                self.assertEqual(data['history'][-1]['observation']['data']['skills'],['Python'])
                return final(reply='你有 Python 项目经历，可以探索应用开发。',intent='resume_analysis',suggested_roles=['应用开发'])
            if not data['history']: return Decision(action='tool',tool='read_resume')
            self.assertEqual(data['history'][0]['observation']['data']['resume'],'Python 项目')
            return final(skills=['Python'],experience_summary='Python 项目',suggested_roles=['应用开发'])
        agents=Agents(ScriptModel(fn))
        result=agents.conversation_agent.run([{'role':'user','content':'看看我的简历适合什么？'}],'Python 项目',0)
        self.assertEqual(result.suggested_roles,['应用开发'])
        starts=[e for e in agents.context.events if e['kind']=='task_started']
        self.assertEqual([e['agent'] for e in starts],['conversation','profile'])
        self.assertEqual(starts[1]['parent_id'],starts[0]['task_id'])

    # 历史或臆造的引文不能作为本轮明确偏好的证据。
    def test_preference_requires_current_message_evidence(self):
        model=ScriptModel(lambda _: final(reply='已记录',preference_updates=[{'field':'cities','value':['杭州'],'evidence':'我想去杭州'}]))
        with self.assertRaises(HarnessLimit):
            Agents(model).conversation_agent.run([{'role':'user','content':'杭州岗位需要什么技能？'}],'',0)

    # 明确意向与运行审计一起保存，审计不能通过另一会话路径读取。
    def test_preferences_persist_and_audit_is_queryable(self):
        model=ScriptModel(lambda _: final(reply='已记录杭州意向，你更想做哪个方向？',intent='preferences',preference_updates=[{'field':'cities','value':['杭州'],'evidence':'我想去杭州'}]))
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(Path(folder)/'test.db')
            app.dependency_overrides[get_agents_factory]=lambda:lambda mode:Agents(model)
            with TestClient(app) as client:
                session=client.post('/api/v1/sessions',json={'mode':'llm'}).json()
                url='/api/v1/sessions/'+session['id']
                response=client.post(url+'/messages',json={'version':0,'message':'我想去杭州'})
                self.assertEqual(response.status_code,200,response.text)
                state=response.json()['session']
                self.assertEqual(state['preferences']['cities'],['杭州'])
                run=state['last_execution']['run_id']
                trace=client.get(url+'/executions/'+run).json()
                self.assertEqual(trace['status'],'completed')
                self.assertEqual(trace['events'][-1]['kind'],'task_completed')
                self.assertEqual(client.get('/api/v1/sessions/other/executions/'+run).status_code,404)

    # 演示场景也验证自然语言触发组合计划及下级能力调用。
    def test_natural_language_plan_dispatch_in_demo(self):
        from boss_agent.models import Job
        agents=Agents(None)
        result=agents.conversation_agent.run([{'role':'user','content':'帮我生成求职计划'}],'虚构 Python',1,
            jobs=[Job(id='1',title='demo',company='虚构',city='杭州',description='Python')])
        self.assertEqual(result.intent,'planning')
        self.assertIsNotNone(agents.conversation_agent.generated_plan)
        names=[e['agent'] for e in agents.context.events if e['kind']=='task_started']
        self.assertIn('matching',names)
        self.assertIn('drafting',names)

    # 预算失败保留执行记录，但会话版本和消息不应部分提交。
    def test_failed_execution_persists_without_partial_chat(self):
        import sqlite3
        model=ScriptModel(lambda _: Decision(action='tool',tool='not_registered'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db'
            app=create_app(path)
            app.dependency_overrides[get_agents_factory]=lambda:lambda mode:Agents(model,RunContext(max_calls=2))
            with TestClient(app) as client:
                session=client.post('/api/v1/sessions',json={'mode':'llm'}).json()
                url='/api/v1/sessions/'+session['id']
                result=client.post(url+'/messages',json={'version':0,'message':'测试预算'})
                self.assertEqual(result.status_code,429)
                self.assertEqual(client.get(url).json()['version'],0)
                with sqlite3.connect(path) as db:
                    import json
                    trace=json.loads(db.execute('SELECT payload FROM executions').fetchone()[0])
                self.assertEqual(trace['status'],'failed')
                self.assertEqual(trace['calls'],2)

    # 真正的偏好修改会使旧计划关联失效。
    def test_preference_update_invalidates_plan_but_question_does_not(self):
        from boss_agent.models import Job
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(Path(folder)/'test.db')
            with TestClient(app) as client:
                session=client.post('/api/v1/sessions',json={'mode':'demo'}).json()
                url='/api/v1/sessions/'+session['id']
                plan=client.post(url+'/messages',json={'version':0,'message':'生成计划','action':'plan','resume_text':'虚构 Python','jobs':[dict(id='1',title='demo',company='虚构',city='杭州',description='Python')]}).json()
                self.assertIsNotNone(plan['session']['last_plan_id'])
                model=ScriptModel(lambda _: final(reply='已记录',intent='preferences',preference_updates=[{'field':'cities','value':['上海'],'evidence':'改去上海'}]))
                app.dependency_overrides[get_agents_factory]=lambda:lambda mode:Agents(model)
                updated=client.post(url+'/messages',json={'version':1,'message':'改去上海'}).json()
                self.assertEqual(updated['session']['preferences']['cities'],['上海'])
                self.assertIsNone(updated['session']['last_plan_id'])

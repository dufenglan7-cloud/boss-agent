# 监测隔离与真实 SDK 内存导出测试，不把数据发送至真实 Langfuse 项目。
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import httpx
from pydantic import BaseModel
from boss_agent.observability import langfuse as monitoring
from boss_agent.harness import RunContext, ReActRunner, HarnessLimit
from boss_agent.agents import Agents
from boss_agent.llm import JsonModel

# 测试使用的最小结构化输出，便于隔离验证运行器契约。
class Answer(BaseModel):
    answer: str

# 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
class FakeSpan:
    def __init__(self, data):
        self.data = data
    def update(self, **kwargs):
        self.data.setdefault('updates', []).append(kwargs)

# 离线依赖替身：记录请求或按脚本返回，不访问真实外部服务。
class FakeClient:
    def __init__(self):
        self.spans = []
        self.stack = []
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        span = FakeSpan(kwargs)
        kwargs['parent'] = self.stack[-1] if self.stack else None
        self.spans.append(kwargs)
        self.stack.append(kwargs['name'])
        try:
            yield span
        finally:
            self.stack.pop()

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class MonitoringTests(unittest.TestCase):
    # 监测关闭时应为空操作，不要求初始化可选 SDK。
    def test_disabled_does_not_initialize_sdk(self):
        with patch.dict(os.environ, {'BOSS_AGENT_LANGFUSE_ENABLED': 'false'}), patch.object(monitoring,'_initialized',False), patch.object(monitoring,'_client',None):
            self.assertIsNone(monitoring.get_client())
            Agents(None).conversation_agent.run([{'role':'user','content':'需要什么技能'}],'',0)

    # 开启但缺配置只产生警告，不能阻止业务执行。
    def test_missing_configuration_is_noop(self):
        with patch.dict(os.environ, {'BOSS_AGENT_LANGFUSE_ENABLED':'true','LANGFUSE_PUBLIC_KEY':'','LANGFUSE_SECRET_KEY':'','LANGFUSE_BASE_URL':''}), patch.object(monitoring,'_initialized',False), patch.object(monitoring,'_client',None):
            self.assertIsNone(monitoring.get_client())

    # SDK 节点创建失败时，正常业务结果仍然可返回。
    def test_sdk_start_failure_does_not_change_result(self):
        # 故意失败的依赖替身，用于证明错误不会被静默吞掉。
        class Broken:
            def start_as_current_observation(self, **kwargs):
                raise RuntimeError('secret backend failure')
        with patch.object(monitoring,'get_client',return_value=Broken()):
            result=ReActRunner(None).run('demo','',{},Answer,demo_result={'answer':'ok'})
        self.assertEqual(result.answer,'ok')

    # 监测更新或关闭失败不能替换真正的业务异常。
    def test_sdk_update_and_close_failure_preserve_business_error(self):
        # 故意失败的依赖替身，用于证明错误不会被静默吞掉。
        class Broken:
            def start_as_current_observation(self, **kwargs):
                # 故意失败的依赖替身，用于证明错误不会被静默吞掉。
                class Context:
                    def __enter__(self): return self
                    def update(self, **kwargs): raise RuntimeError('backend unavailable')
                    def __exit__(self,*args): raise RuntimeError('backend unavailable')
                return Context()
        with patch.object(monitoring,'get_client',return_value=Broken()):
            with self.assertRaisesRegex(ValueError,'business marker'):
                with monitoring.observation('test'):
                    raise ValueError('business marker')

    # 关闭正文采集时不上传文本；显式开启时仍屏蔽已知凭证。
    def test_content_off_and_redaction(self):
        client=FakeClient()
        with patch.object(monitoring,'get_client',return_value=client), patch.dict(os.environ,{'BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT':'false'}):
            with monitoring.observation('test',input={'resume':'private-resume'}) as span:
                span.update(output='private-response')
        self.assertNotIn('private-resume',json.dumps(client.spans))
        self.assertNotIn('private-response',json.dumps(client.spans))
        with patch.object(monitoring,'get_client',return_value=client), patch.dict(os.environ,{'BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT':'true','BOSS_AGENT_API_KEY':'private-key'}):
            with monitoring.observation('opt-in',input={'message':'example private-key'}) as span:
                span.update(output='sample private-key')
        self.assertIn('example [REDACTED]',json.dumps(client.spans))
        self.assertNotIn('private-key',json.dumps(client.spans))
        with patch.dict(os.environ,{'BOSS_AGENT_API_KEY':'private-key'}):
            result=monitoring.redact({'api_key':'private-key','text':'Bearer private-key','nested':[{'password':'pw'}]})
        self.assertNotIn('private-key',json.dumps(result))
        self.assertNotIn('pw',json.dumps(result))

    # 被委派子 Agent 应嵌套在工具之下，预算失败应被标记。
    def test_agent_tool_nesting_and_budget_error(self):
        client=FakeClient()
        with patch.object(monitoring,'get_client',return_value=client):
            agents=Agents(None)
            agents.context.session_id='test-session'
            agents.conversation_agent.run([{'role':'user','content':'分析简历适合什么方向'}],'虚构 Python',0)
            with self.assertRaises(HarnessLimit):
                ReActRunner(None,RunContext(max_calls=0)).run('limited','',{},Answer,demo_result={'answer':'no'})
        spans=client.spans
        profile=next(s for s in spans if s['name']=='agent.profile')
        self.assertEqual(profile['parent'],'tool.analyze_resume')
        self.assertEqual(sum(s['name']=='boss-agent.run' for s in spans),2)
        root=spans[-1] if spans[-1]['name']=='boss-agent.run' else next(s for s in spans if s['name']=='boss-agent.run' and s is not spans[0])
        errors=[u for s in spans for u in s.get('updates',[]) if u.get('level')=='ERROR']
        self.assertTrue(any(u.get('status_message')=='HarnessLimit' for u in errors))

    # 只把供应商真实 token 数映射到标准字段，不使用演示数值。
    def test_real_usage_is_normalized(self):
        client=FakeClient()
        real_client=httpx.Client
        body={'choices':[{'message':{'content':'{"answer":"ok"}'}}], 'usage':{'prompt_tokens':12,'completion_tokens':4,'total_tokens':16}}
        with patch.dict(os.environ,{'BOSS_AGENT_API_KEY':'fake-key','BOSS_AGENT_MODEL':'test-model'}):
            model=JsonModel()
        mock_http=real_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
        with patch.object(monitoring,'get_client',return_value=client), patch('boss_agent.llm.httpx.Client',return_value=mock_http):
            model.ask('test',{},Answer)
        generation=next(s for s in client.spans if s['as_type']=='generation')
        self.assertEqual(generation['model'],'test-model')
        self.assertEqual(generation['updates'][0]['usage_details'],{'input':12,'output':4,'total':16})

    # 正常关闭刷新一次，多次调用不重复操作已关闭的客户端。
    def test_shutdown_flushes_and_is_idempotent(self):
        from unittest.mock import Mock
        client=Mock()
        with patch.object(monitoring,'_client',client), patch.object(monitoring,'_initialized',True):
            monitoring.shutdown()
            monitoring.shutdown()
        client.flush.assert_called_once()
        client.shutdown.assert_called_once()

    # 真实 SDK 用内存导出器验证同轮 trace、会话传播和默认正文关闭。
    @unittest.skipUnless(importlib.util.find_spec('langfuse'),'optional SDK not installed')
    def test_real_sdk_exports_parent_tree_without_network_or_content(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter=InMemorySpanExporter()
        provider=TracerProvider()
        client=Langfuse(public_key='pk-lf-offline-'+str(uuid4()),secret_key='offline-fake',
            base_url='http://127.0.0.1:1',tracer_provider=provider,span_exporter=exporter,mask=monitoring.redact)
        try:
            with patch.object(monitoring,'get_client',return_value=client), patch.dict(os.environ,{'BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT':'false'}):
                agents=Agents(None)
                agents.context.session_id='offline-test-session'
                agents.conversation_agent.run([{'role':'user','content':'分析简历适合什么方向'}],'PRIVATE_RESUME_MARKER',0)
            client.flush()
            spans=exporter.get_finished_spans()
            self.assertTrue(spans)
            self.assertEqual(len({s.context.trace_id for s in spans}),1)
            parent=next(s for s in spans if s.name=='tool.analyze_resume')
            child=next(s for s in spans if s.name=='agent.profile')
            self.assertEqual(child.parent.span_id,parent.context.span_id)
            self.assertNotIn('PRIVATE_RESUME_MARKER',str([dict(s.attributes) for s in spans]))
            self.assertTrue(any('offline-test-session' in str(dict(s.attributes)) for s in spans))
        finally:
            client.shutdown()
            provider.shutdown()

    # 真实 generation 应挂在 ReAct 步骤下，并关联正确用量及本地 trace ID。
    @unittest.skipUnless(importlib.util.find_spec('langfuse'),'optional SDK not installed')
    def test_real_sdk_generation_parent_usage_and_trace_id(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter=InMemorySpanExporter()
        provider=TracerProvider()
        client=Langfuse(public_key='pk-lf-generation-'+str(uuid4()),secret_key='offline-fake',
            base_url='http://127.0.0.1:1',tracer_provider=provider,span_exporter=exporter)
        real_client=httpx.Client
        body={'choices':[{'message':{'content':json.dumps({'action':'final','result':{'reply':'一般需要 Python 基础。','intent':'question'}})}}],
              'usage':{'prompt_tokens':21,'completion_tokens':9,'total_tokens':30}}
        try:
            with patch.dict(os.environ,{'BOSS_AGENT_API_KEY':'fake-offline','BOSS_AGENT_MODEL':'test-model','BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT':'false'}):
                model=JsonModel()
                http=real_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
                with patch.object(monitoring,'get_client',return_value=client), patch('boss_agent.llm.httpx.Client',return_value=http):
                    agents=Agents(model)
                    agents.context.session_id='generation-session'
                    agents.conversation_agent.run([{'role':'user','content':'需要什么技能'}],'',0)
            client.flush()
            spans=exporter.get_finished_spans()
            generation=next(s for s in spans if s.name=='llm.request')
            step=next(s for s in spans if s.name=='react.step')
            self.assertEqual(generation.parent.span_id,step.context.span_id)
            self.assertEqual(agents.context.langfuse_trace_id,format(generation.context.trace_id,'032x'))
            values=str(dict(generation.attributes))
            self.assertIn('test-model',values)
            usage=json.loads(generation.attributes['langfuse.observation.usage_details'])
            self.assertEqual(usage,{'input':21,'output':9,'total':30})
            self.assertNotIn('fake-offline',values)
        finally:
            client.shutdown()
            provider.shutdown()

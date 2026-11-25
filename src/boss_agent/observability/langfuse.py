# 可选 Langfuse 适配；默认不采集正文，监测异常不得改变业务结果。
"""Fail-open Langfuse v4 adapter. Content capture is opt-in; business errors are never swallowed."""
import hashlib
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_client = None
_initialized = False
_active_run = ContextVar('boss_agent_observation_run', default=None)
_SECRET_KEYS = {'authorization', 'api_key', 'apikey', 'password', 'secret', 'secret_key',
                'access_token', 'refresh_token', 'langfuse_secret_key', 'langfuse_public_key'}

# 将显式环境开关转换为布尔值；缺省不启用额外采集。
def enabled(name):
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes'}

# 递归屏蔽常见凭证及当前环境密钥；这不等于全面匿名化个人资料。
def redact(data, **kwargs):
    if isinstance(data, dict):
        return {str(k): '[REDACTED]' if str(k).lower() in _SECRET_KEYS else redact(v)
                for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    if isinstance(data, str):
        for name in ('BOSS_AGENT_API_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_PUBLIC_KEY'):
            secret = os.environ.get(name)
            if secret:
                data = data.replace(secret, '[REDACTED]')
        data = re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', data, flags=re.IGNORECASE)
        return data[:20000] + ('…[truncated]' if len(data) > 20000 else '')
    return data

# 延迟初始化可选 SDK，并缓存禁用或失败状态，避免每个步骤重复初始化。
def get_client():
    global _client, _initialized
    with _lock:
        if _initialized:
            return _client
        # 禁用、缺配置和初始化失败也会被缓存；更改环境后应重启服务。
        _initialized = True
        if not enabled('BOSS_AGENT_LANGFUSE_ENABLED'):
            return None
        public, secret = os.environ.get('LANGFUSE_PUBLIC_KEY'), os.environ.get('LANGFUSE_SECRET_KEY')
        base = os.environ.get('LANGFUSE_BASE_URL')
        if not public or not secret or not base:
            logger.warning('Langfuse disabled: configure public key, secret key and base URL')
            return None
        try:
            # 可选依赖延迟导入，让未安装监测 SDK 的基础服务仍能启动。
            from langfuse import Langfuse
            _client = Langfuse(public_key=public, secret_key=secret, base_url=base,
                environment=os.environ.get('LANGFUSE_TRACING_ENVIRONMENT', 'development'),
                timeout=5, mask=redact,
                should_export_span=lambda span: span.instrumentation_scope is not None
                    and span.instrumentation_scope.name == 'langfuse-sdk')
        except Exception:
            logger.warning('Langfuse initialization failed; continuing without remote monitoring')
        return _client

# 封装真实监测节点或空节点，业务调用无需分支处理 SDK 是否启用。
class Handle:
    # 保存真实节点或空节点引用，不在此触发远端请求。
    def __init__(self, raw=None):
        self.raw = raw

    # 只有真实节点成功创建后才认为监测上下文可用，不代表远端已经接收。
    @property
    def active(self):
        return self.raw is not None

    # 默认移除输入输出正文，再以尽力方式更新监测；失败不改业务结果。
    def update(self, **values):
        if not self.active:
            return
        if not enabled('BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT'):
            values.pop('input', None)
            values.pop('output', None)
        try:
            self.raw.update(**redact(values))
        except Exception:
            logger.warning('Langfuse observation update failed; continuing')

# 用正常退出关闭 SDK 上下文，不让它自动采集业务异常原文和堆栈。
def close_scope(cm):
    if cm is not None:
        try:
            # Never send arbitrary business exception messages/stacktraces to the SDK.
            cm.__exit__(None, None, None)
        except Exception:
            logger.warning('Langfuse observation close failed; continuing')

# 统一创建可选监测作用域；原业务异常继续抛出，SDK 异常单独隔离。
@contextmanager
def observation(name, kind='span', *, input=None, metadata=None, **options):
    cm, raw = None, None
    try:
        client = get_client()
        if client is not None:
            params = {'name': name, 'as_type': kind, 'metadata': redact(metadata or {}), **options}
            if enabled('BOSS_AGENT_LANGFUSE_CAPTURE_CONTENT'):
                params['input'] = redact(input)
            cm = client.start_as_current_observation(**params)
            raw = cm.__enter__()
    except Exception:
        cm = None
        logger.warning('Langfuse observation start failed; continuing')
    handle = Handle(raw)
    try:
        yield handle
    # 只记录异常类型；随后重新抛出原业务异常，保持原来的控制流。
    except BaseException as exc:
        handle.update(level='ERROR', status_message=type(exc).__name__)
        raise
    finally:
        close_scope(cm)

# 同轮嵌套任务共用一个根 trace，传播会话与匿名化用户关联。
@contextmanager
def run_scope(context):
    if _active_run.get() == context.run_id:
        yield
        return
    # ContextVar 隔离并发执行；同轮子任务复用根 trace，不会另外创建无关根节点。
    token = _active_run.set(context.run_id)
    propagation = None
    # 稳定关联本地运行与监测 trace；生成 ID 并不证明远端已经接收。
    trace_id = hashlib.sha256(context.run_id.encode()).hexdigest()[:32]
    try:
        # Set propagation BEFORE the root so session/run attributes live on every observation.
        if get_client() is not None:
            try:
                from langfuse import propagate_attributes
                propagation = propagate_attributes(user_id=(hashlib.sha256(context.user_id.encode()).hexdigest() if getattr(context, "user_id", None) else None), session_id=getattr(context, 'session_id', None),
                    metadata={'harness_run_id': context.run_id}, trace_name='boss-agent.run')
                propagation.__enter__()
            except Exception:
                propagation = None
                logger.warning('Langfuse attribute propagation failed; continuing')
        with observation('boss-agent.run', 'chain',
            metadata={'run_id': context.run_id, 'max_calls': context.max_calls,
                      'max_steps': context.max_steps, 'max_depth': context.max_depth},
            trace_context={'trace_id': trace_id}) as root:
            # 只在真实节点创建成功时向本地记录写入监测关联 ID。
            if root.active:
                context.langfuse_trace_id = trace_id
            try:
                yield
            finally:
                root.update(metadata={'run_id': context.run_id, 'calls': context.calls,
                    'event_count': len(context.events)})
    finally:
        close_scope(propagation)
        _active_run.reset(token)

# 正常结束时尽力发送尚未导出的监测数据，不假设远端一定可用。
def flush():
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            logger.warning('Langfuse flush failed; continuing')

# 刷新并关闭客户端，再复位初始化标记，使重复关闭安全。
def shutdown():
    global _client, _initialized
    with _lock:
        if _client is not None:
            flush()
            try:
                _client.shutdown()
            except Exception:
                logger.warning('Langfuse shutdown failed; continuing')
        _client = None
        _initialized = False

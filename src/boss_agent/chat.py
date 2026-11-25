# 一轮对话的业务边界：读取记忆、调度 Agent、合并结果并原子提交。
import time
from uuid import uuid4
from .models import Plan
from .observability import run_scope
from .chat_models import Session, TurnRequest, Message
from .storage import Store, SessionConflict
from .agents.conversation.schemas import Preferences
from .portraits.models import UserPortrait
from .portraits.service import active_portrait, update_from_reply, retrieval_context

WELCOME = "你好！可以直接问我岗位需要什么技能，也可以发来简历，我们一起分析适合的方向。求职意向可以边聊边确定。"

# 表示当前任务缺少必要输入，由 HTTP 层转换为可理解的 422 响应。
class ChatInputError(ValueError):
    pass

# 协调单轮会话及长期画像；模型调用发生在数据库事务之外。
class ChatService:
    # 注入存储和 Agent 工厂，便于测试隔离真实模型。
    def __init__(self, store: Store, factory):
        self.store = store
        self.factory = factory

    # 在模型执行前后核对版本，将有依据的更新一次提交。
    def turn(self, session_id: str, payload: TurnRequest, user_id: str = "local-user") -> tuple[Session, Plan | None]:
        data = self.store.get_session(session_id)
        if data is None or data.get("user_id", "local-user") != user_id:
            raise KeyError(session_id)
        session = Session.model_validate(data)
        if session.version != payload.version:
            raise SessionConflict("Session changed; reload before sending")
        if len(session.messages) >= 61:
            raise ChatInputError("本次会话已达到 30 轮上限，请新建会话。")
        portrait = UserPortrait.model_validate(self.store.get_portrait(user_id, session.mode))
        portrait_before = portrait.model_dump()
        expected_portrait = portrait.revision
        # 已有长期画像时以它为基础；旧数据没有画像版本时兼容会话已有偏好。
        base = portrait.preferences.model_dump() if portrait.revision > 0 else session.preferences.model_dump()
        # 本会话临时侧重覆盖长期偏好，但不会因此直接改写长期画像。
        effective = Preferences.model_validate(base | session.preference_overrides)
        context_changed = effective != session.preferences
        session.preferences = effective
        session.user_portrait = active_portrait(portrait)
        session.portrait_revision = portrait.revision
        # 省略或 null 表示保留原资料；空字符串和空数组才是显式清空。
        if payload.resume_text is not None:
            context_changed |= payload.resume_text != session.resume_text
            session.resume_text = payload.resume_text
        if payload.jobs is not None:
            context_changed |= payload.jobs != session.jobs
            session.jobs = payload.jobs
        session.messages.append(Message(role="user", content=payload.message))
        # 完整计划才要求简历与职位；普通咨询仍可从零资料开始。
        if payload.action == "plan" and (not session.resume_text.strip() or not session.jobs):
            raise ChatInputError("生成计划需要简历文本和至少一个导入职位。普通问答和简历方向分析不需要职位。")
        agents = self.factory(session.mode)
        ctx = agents.begin_run()
        ctx.session_id = session_id
        ctx.user_id = user_id
        # 生成可落库的运行审计；本地 trace ID 不代表 Langfuse 已接收。
        def execution(status):
            return {"run_id": ctx.run_id, "session_id": session_id, "status": status,
                    "calls": ctx.calls, "duration_ms": int((time.monotonic() - ctx.started) * 1000),
                    "events": ctx.events, "user_id": user_id, "langfuse_trace_id": ctx.langfuse_trace_id}
        try:
            with run_scope(ctx):
                result = agents.conversation_agent.run(
                    [m.model_dump() for m in session.messages], session.resume_text, len(session.jobs),
                    jobs=session.jobs, preferences=session.preferences, action=payload.action,
                    user_portrait=retrieval_context(portrait, payload.message))
                merged = session.preferences.model_dump()
                for update in result.preference_updates:
                    merged[update.field] = update.value
                    session.preference_evidence[update.field] = update.evidence
                    if update.scope == "session":
                        session.preference_overrides[update.field] = update.value
                    else:
                        session.preference_overrides.pop(update.field, None)
                preferences = Preferences.model_validate(merged)
                context_changed |= preferences != session.preferences
                session.preferences = preferences
                # 模型只返回待更新数据；真正的记忆写入由业务服务完成。
                portrait = update_from_reply(portrait, result, payload.message, session.resume_text, session_id)
                if portrait.model_dump() != portrait_before:
                    portrait.revision += 1
                session.user_portrait = active_portrait(portrait)
                session.portrait_revision = portrait.revision
                # 资料或有效偏好发生变化时，旧计划不再代表当前要求。
                if context_changed:
                    session.last_plan_id = None
                plan = agents.conversation_agent.generated_plan
                record = None
                if plan is not None:
                    run_id = str(uuid4())
                    session.last_plan_id = run_id
                    record = (run_id, session.mode, plan)
                    session.suggested_roles = plan.profile.suggested_roles
                elif result.suggested_roles:
                    session.suggested_roles = result.suggested_roles
                session.missing_preferences = result.missing_preferences
                session.last_intent = result.intent
                session.last_execution = {"run_id": ctx.run_id, "calls": ctx.calls,
                    "langfuse_trace_id": ctx.langfuse_trace_id,
                    "agents": list(dict.fromkeys(e["agent"] for e in ctx.events if e["kind"] == "task_started"))}
                session.messages.append(Message(role="assistant", content=result.reply))
                session.title = next((m.content[:30] for m in session.messages if m.role == "user"), "新的求职对话")
                session.version += 1
                # 会话与画像版本在同一事务中核对，任何并发冲突都不能留下半成品。
                self.store.commit_session(session_id, payload.version, session.model_dump(), record, execution("completed"),
                    (user_id, session.mode, expected_portrait, portrait.model_dump()))
                return session, plan
        except Exception:
            # 失败审计独立保存；本轮模型可能已消耗费用，但消息不会部分写入。
            self.store.save_execution(execution("failed"))
            raise

# 会话及执行记录接口：先检查用户归属，再调用动态调度服务。
import os
from uuid import uuid4
import httpx
from fastapi import APIRouter, Depends, HTTPException
from .chat_schemas import SessionCreate, Session, Message, TurnRequest, TurnResponse
from .dependencies import get_store, get_agents_factory
from ..storage import Store, SessionConflict
from ..harness import HarnessLimit
from .identity import current_user, own_session
from ..portraits.models import UserPortrait
from ..portraits.service import active_portrait
from ..chat import ChatService, ChatInputError, WELCOME

router = APIRouter(prefix="/api/v1", tags=["chat"])

# 仅报告配置是否存在，不通过此接口证明远端服务已连通。
@router.get("/config")
def config():
    return {"model_configured": bool(os.environ.get("BOSS_AGENT_API_KEY") and os.environ.get("BOSS_AGENT_MODEL")),
            "real_submission_enabled": False}

# 按当前用户和模式加载长期画像，创建带欢迎消息的新会话。
@router.post("/sessions", response_model=Session, status_code=201)
def create_session(payload: SessionCreate, store: Store = Depends(get_store), user_id: str = Depends(current_user)):
    portrait = UserPortrait.model_validate(store.get_portrait(user_id, payload.mode))
    session = Session(id=str(uuid4()), mode=payload.mode, user_id=user_id,
                      preferences=portrait.preferences, user_portrait=active_portrait(portrait), portrait_revision=portrait.revision,
                      messages=[Message(role="assistant", content=WELCOME)])
    store.create_session(session.id, session.model_dump())
    return session

# 只返回当前标识的会话摘要，不在列表中展开私人资料。
@router.get("/sessions")
def list_sessions(store: Store = Depends(get_store), user_id: str = Depends(current_user)):
    return store.list_sessions(user_id)

# 先确认会话归属，再返回完整会话快照。
@router.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str, store: Store = Depends(get_store), user_id: str = Depends(current_user)):
    return Session.model_validate(own_session(store, session_id, user_id))

# 将校验后的消息交给业务服务，统一转换预算、配置、并发及模型错误。
@router.post("/sessions/{session_id}/messages", response_model=TurnResponse)
def send_message(session_id: str, payload: TurnRequest, store: Store = Depends(get_store),
                 factory=Depends(get_agents_factory), user_id: str = Depends(current_user)):
    own_session(store, session_id, user_id)
    # Check configuration before invoking the workflow; never expose provider error bodies.
    # 创建 Agent 时将缺少模型配置转为 503，不回显服务商异常原文。
    def checked_factory(mode):
        try:
            return factory(mode)
        except ValueError:
            raise HTTPException(503, "模型尚未配置，请设置服务端环境变量后重试。") from None
    try:
        session, plan = ChatService(store, checked_factory).turn(session_id, payload, user_id)
    except HarnessLimit:
        raise HTTPException(429, "本轮达到执行预算，请缩小任务范围后重试。") from None
    except SessionConflict:
        raise HTTPException(409, "会话已更新，请重新载入后发送。") from None
    except ChatInputError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyError:
        # Distinguish a nonexistent session from malformed upstream model responses.
        if store.get_session(session_id) is None:
            raise HTTPException(404, "Session not found") from None
        raise HTTPException(502, "模型回复无效，请稍后重试。") from None
    except (httpx.HTTPError, ValueError, IndexError, TypeError):
        raise HTTPException(502, "对话处理失败，请检查模型配置或稍后重试。") from None
    return TurnResponse(session=session, plan=plan)


# 通过会话归属和运行标识双重定位审计记录，避免跨会话误取。
@router.get("/sessions/{session_id}/executions/{run_id}")
def get_execution(session_id: str, run_id: str, store: Store = Depends(get_store), user_id: str = Depends(current_user)):
    own_session(store, session_id, user_id)
    record = store.get_execution(session_id, run_id)
    if record is None:
        raise HTTPException(404, "Execution not found")
    return record

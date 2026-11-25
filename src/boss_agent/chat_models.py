# 会话状态与消息契约；旧数据通过默认字段兼容，长期画像与会话临时状态分开。
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from .models import Job, Plan
from .portraits.models import UserPortrait
from .agents.conversation.schemas import Preferences

# 一条已保存的用户或助手消息；工具中间观察保存在执行记录中。
class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str

# 创建会话时只选择运行模式，用户身份来自统一请求依赖。
class SessionCreate(BaseModel):
    mode: Literal["demo", "llm"] = "demo"

# 会话快照包含当前资料与临时覆盖；用户长期画像另表保存。
class Session(BaseModel):
    # 未登录阶段的显式标识；不能用作真实身份凭证。
    user_id: str = "local-user"
    # 本会话看到的长期画像版本，提交时还需校验数据库最新版本。
    portrait_revision: int = 0
    user_portrait: dict = Field(default_factory=dict)
    # 只在本会话生效的临时偏好，不写回长期画像。
    preference_overrides: dict = Field(default_factory=dict)
    id: str
    mode: Literal["demo", "llm"]
    # 客户端读到的会话版本，用于防止消息并发覆盖。
    version: int = 0
    title: str = "新的求职对话"
    messages: list[Message] = Field(default_factory=list)
    resume_text: str = ""
    jobs: list[Job] = Field(default_factory=list)
    suggested_roles: list[str] = Field(default_factory=list)
    missing_preferences: list[str] = Field(default_factory=list)
    # 当前要求对应的计划关联；相关资料改变后应失效。
    last_plan_id: str | None = None
    preferences: Preferences = Field(default_factory=Preferences)
    preference_evidence: dict[str, str] = Field(default_factory=dict)
    last_intent: str | None = None
    # 最近一轮协作摘要，完整工具观察保存于执行记录。
    last_execution: dict | None = None

# 客户端携带看到的版本发送消息，防止两个页面相互覆盖历史。
class TurnRequest(BaseModel):
    version: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=6000)
    action: Literal["chat", "plan"] = "chat"
    # None 表示保留原简历，空字符串表示显式清空。
    resume_text: str | None = Field(default=None, max_length=100000)
    # None 表示保留原职位，空数组表示显式清空。
    jobs: list[Job] | None = Field(default=None, max_length=100)

    # 消息必须含有效文本，空白消息不会形成一次对话。
    @field_validator("message")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value

    # 同一次请求内职位标识必须唯一，避免匹配结果与文案关联错位。
    @model_validator(mode="after")
    def unique_jobs(self):
        if self.jobs is not None and len({j.id for j in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs")
        return self

# 返回新会话及可选计划；实际投递数量固定为零。
class TurnResponse(BaseModel):
    session: Session
    plan: Plan | None = None
    # 对话和草稿均为本地准备，不能冒充已联系招聘者。
    applications_sent: Literal[0] = 0

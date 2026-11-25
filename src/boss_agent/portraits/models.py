# 长期职业画像及手工编辑契约；近期关注与稳定职业事实使用不同保留规则。
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ..agents.conversation.schemas import Preferences

# 一项有证据和时间的职业记忆；近期关注可以过期，稳定事实不自动过期。
class MemoryFact(BaseModel):
    field: Literal["skills", "experience", "current_focus"]
    value: str
    # 保存可回溯的原文依据；原文存在并不保证模型理解了否定或语气。
    evidence: str
    source: Literal["message", "resume", "user_edit"]
    session_id: str | None = None
    updated_at: str
    # 近期关注有期限，技能和经历默认不自动过期。
    expires_at: str | None = None

# 以用户和模式分区的长期记忆，revision 用于跨会话并发控制。
class UserPortrait(BaseModel):
    user_id: str
    # 演示与模型模式分区，防止虚构数据污染真实职业背景。
    mode: Literal["demo", "llm"]
    # 跨会话修改同一画像时的乐观并发版本。
    revision: int = 0
    preferences: Preferences = Field(default_factory=Preferences)
    preference_evidence: dict[str, dict] = Field(default_factory=dict)
    facts: list[MemoryFact] = Field(default_factory=list)
    # 只保留有限次变更轨迹，不会整段重复传给模型。
    history: list[dict] = Field(default_factory=list)
    updated_at: str | None = None

# 手工纠正的白名单字段；省略字段保留，显式清空按服务层规则处理。
class PortraitEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 编辑必须基于读取到的版本，不能无条件覆盖其他会话的更新。
    revision: int = Field(ge=0)
    preferences: Preferences | None = None
    skills: list[str] | None = Field(default=None, max_length=100)
    experience: list[str] | None = Field(default=None, max_length=50)
    current_focus: str | None = Field(default=None, max_length=1000)

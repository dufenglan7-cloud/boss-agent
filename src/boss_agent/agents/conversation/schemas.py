# 约束对话输出、明确偏好及长期画像更新，区分长期记忆与本轮侧重点。
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

# 一项明确偏好更新；证据必须来自本轮原话，scope 决定长期或仅本会话生效。
class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["target_roles", "cities", "minimum_monthly_salary", "excluded_companies"]
    value: list[str] | int | None
    evidence: str = Field(min_length=1)
    # 长期偏好跨会话保存；session 只覆盖当前会话。
    scope: Literal["persistent", "session"] = "persistent"

# 用户明确表达的求职条件；系统推测的岗位方向不写入此结构。
class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_roles: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    minimum_monthly_salary: int | None = Field(default=None, ge=0)
    excluded_companies: list[str] = Field(default_factory=list)

# 有来源的职业事实变更，只允许技能、经历和近期关注。
class PortraitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["skills", "experience", "current_focus"]
    value: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)
    # message 必须引用本轮原话；resume 还要求本轮已分析当前简历。
    source: Literal["message", "resume"]
    # 补充与移除均为结构化提议，最终由服务层在事务中提交。
    operation: Literal["upsert", "remove"] = "upsert"

# 主 Agent 的最终业务输出，既包含答复，也包含待提交的结构化更新。
class ConversationReply(BaseModel):
    reply: str = Field(min_length=1, max_length=10000)
    intent: Literal["question", "resume_analysis", "preferences", "matching", "planning", "clarify"] = "question"
    # 系统建议的可探索方向，不等于用户已确认的目标岗位。
    suggested_roles: list[str] = Field(default_factory=list)
    missing_preferences: list[str] = Field(default_factory=list)
    preference_updates: list[PreferenceUpdate] = Field(default_factory=list)
    # 一轮最多提出 12 项事实变化，避免无限扩充记忆。
    portrait_updates: list[PortraitUpdate] = Field(default_factory=list, max_length=12)

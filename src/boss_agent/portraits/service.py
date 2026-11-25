# 画像更新与检索规则；明确偏好、原文证据、有效期和历史记录分别处理。
from datetime import datetime, timedelta, timezone
from .models import UserPortrait, MemoryFact, PortraitEdit
from ..agents.conversation.schemas import Preferences

# 内部时间统一使用带时区的 UTC，避免过期判断受主机本地时区影响。
def now_utc():
    return datetime.now(timezone.utc)

# 只过滤上下文中的过期关注并移除历史；不会物理删除数据库中的原记录。
def active_portrait(portrait: UserPortrait, now=None) -> dict:
    now = now or now_utc()
    data = portrait.model_dump()
    # 过期仅影响当前上下文；原记录和变化历史仍可用于回顾。
    data["facts"] = [f.model_dump() for f in portrait.facts
                     if not f.expires_at or datetime.fromisoformat(f.expires_at) > now]
    # The model receives relevant current facts, not an unbounded change log.
    data.pop("history")
    return data

# 追加变更前后值及来源，限制历史长度以避免画像持续膨胀。
def record(portrait, field, operation, before, after, session_id, evidence, source):
    timestamp = now_utc().isoformat()
    portrait.history.append(dict(field=field, operation=operation, before=before, after=after,
        session_id=session_id, evidence=evidence, source=source, at=timestamp))
    # 限制变化日志长度，防止每次模型请求或数据库记录无限增长。
    portrait.history = portrait.history[-100:]
    portrait.updated_at = timestamp

# 按字段与值去重更新事实；近期关注是单一当前项，并设置 30 天有效期。
def apply_fact(portrait, field, value, evidence, source, session_id=None, operation="upsert"):
    value = value.strip()
    if not value or len(value) > 1000:
        raise ValueError("Invalid portrait value")
    # 稳定事实按值去重；current_focus 则表示单一当前关注，可由新值替换。
    previous = [f for f in portrait.facts if f.field == field and
                (field == "current_focus" or f.value.casefold() == value.casefold())]
    portrait.facts = [f for f in portrait.facts if f not in previous]
    if operation == "upsert":
        timestamp = now_utc()
        fact = MemoryFact(field=field, value=value, evidence=evidence, source=source,
            session_id=session_id, updated_at=timestamp.isoformat(),
            expires_at=(timestamp + timedelta(days=30)).isoformat() if field == "current_focus" else None)
        portrait.facts.append(fact)
    if len(portrait.facts) > 150:
        raise ValueError("Portrait fact limit reached")
    record(portrait, field, operation, [f.model_dump() for f in previous],
           value if operation == "upsert" else None, session_id, evidence, source)

# 只持久化有本轮原文依据的长期偏好和职业事实，不保存会话临时偏好。
def update_from_reply(portrait, result, latest, resume, session_id):
    for update in result.preference_updates:
        if not update.evidence.strip() or update.evidence not in latest:
            raise ValueError("Preference updates need current user evidence")
        # “这次先看某方向”属于会话覆盖，不能污染下一次新会话的长期条件。
        if update.scope != "persistent":
            continue
        old = getattr(portrait.preferences, update.field)
        if old != update.value:
            values = portrait.preferences.model_dump() | {update.field: update.value}
            portrait.preferences = Preferences.model_validate(values)
            portrait.preference_evidence[update.field] = dict(evidence=update.evidence,
                session_id=session_id, updated_at=now_utc().isoformat(), source="message")
            record(portrait, update.field, "replace", old, update.value, session_id, update.evidence, "message")
    for update in result.portrait_updates:
        source = latest if update.source == "message" else resume
        if result.intent == "question" or not update.evidence.strip() or update.evidence not in source:
            raise ValueError("Portrait updates need grounded evidence and a relevant intent")
        apply_fact(portrait, update.field, update.value, update.evidence, update.source,
                   session_id, update.operation)
    return portrait

# 处理用户手工纠正；省略字段保留，空列表或空关注用于明确清除。
def edit_portrait(portrait: UserPortrait, edit: PortraitEdit):
    if edit.preferences is not None:
        for field, value in edit.preferences.model_dump().items():
            old = getattr(portrait.preferences, field)
            if old != value:
                record(portrait, field, "replace", old, value, None, str(value), "user_edit")
                portrait.preference_evidence[field] = dict(evidence=str(value), source="user_edit",
                    updated_at=now_utc().isoformat(), session_id=None)
        portrait.preferences = edit.preferences
    for field in ("skills", "experience", "current_focus"):
        # 区分未提交字段与明确提交 null/空列表，避免编辑一项时误删其他背景。
        if field not in edit.model_fields_set:
            continue
        values = getattr(edit, field)
        values = [values] if isinstance(values, str) else (values or [])
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("Invalid portrait value")
        old = [f.model_dump() for f in portrait.facts if f.field == field]
        portrait.facts = [f for f in portrait.facts if f.field != field]
        record(portrait, field, "replace", old, values, None, "manual edit", "user_edit")
        for value in dict.fromkeys(values):
            apply_fact(portrait, field, value, value, "user_edit")
    return portrait


# 按时效和英文技能词面关联选择最多 24 条事实，避免把完整历史重复送给模型。
def retrieval_context(portrait, message):
    import re
    context = active_portrait(portrait)
    terms = set(re.findall(r"[A-Za-z0-9_+#.]{2,}", message.casefold()))
    # 优先近期关注，其次关键词相关性，最后用更新时间决定先后。
    def rank(fact):
        return (fact["field"] == "current_focus", any(t in fact["value"].casefold() for t in terms), fact["updated_at"])
    # 这里只做有限词面排序，不能声称已经实现向量或语义检索。
    context["facts"] = sorted(context["facts"], key=rank, reverse=True)[:24]
    context["retrieval_note"] = "Active memory: recent focus, query-related facts, then recent facts; maximum 24 records."
    return context

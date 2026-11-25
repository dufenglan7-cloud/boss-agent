# 当前用户长期画像的读取、纠正与清除接口，使用 revision 防止覆盖并发更新。
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from .identity import current_user
from .dependencies import get_store
from ..storage import Store, SessionConflict
from ..portraits.models import UserPortrait, PortraitEdit
from ..portraits.service import active_portrait, edit_portrait

router = APIRouter(prefix="/api/v1/users/me/portrait", tags=["portrait"])

# 读取当前用户、当前模式的画像，同时给出已过滤过期项的活动事实。
@router.get("")
def get_portrait(mode: Literal["demo", "llm"] = "demo", user_id: str = Depends(current_user), store: Store = Depends(get_store)):
    portrait = UserPortrait.model_validate(store.get_portrait(user_id, mode))
    result = portrait.model_dump()
    result["active_facts"] = active_portrait(portrait)["facts"]
    return result

# 纠正画像前检查 revision，保存前再次通过数据库条件更新防止竞争。
@router.put("", response_model=UserPortrait)
def put_portrait(payload: PortraitEdit, mode: Literal["demo", "llm"] = "demo", user_id: str = Depends(current_user), store: Store = Depends(get_store)):
    portrait = UserPortrait.model_validate(store.get_portrait(user_id, mode))
    if portrait.revision != payload.revision:
        raise HTTPException(409, "画像已更新，请重新读取后编辑。")
    try:
        edited = edit_portrait(portrait, payload)
        edited.revision += 1
        store.replace_portrait(user_id, mode, payload.revision, edited.model_dump())
        return edited
    except SessionConflict:
        raise HTTPException(409, "画像已更新，请重新读取后编辑。") from None
    except ValueError:
        raise HTTPException(422, "画像内容无效，请检查长度和字段。") from None

# 用更高版本的空快照清除长期记忆，阻止运行中的旧画像重新覆盖。
@router.delete("", response_model=UserPortrait)
def reset_portrait(revision: int = Query(ge=0), mode: Literal["demo", "llm"] = "demo", user_id: str = Depends(current_user), store: Store = Depends(get_store)):
    # A versioned empty snapshot prevents in-flight old runs from restoring deleted memory.
    portrait = UserPortrait(user_id=user_id, mode=mode, revision=revision + 1)
    try:
        store.replace_portrait(user_id, mode, revision, portrait.model_dump())
    except SessionConflict:
        raise HTTPException(409, "画像已更新，请重新读取后清除。") from None
    return portrait

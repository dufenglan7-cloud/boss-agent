# 本地开发使用显式姓名或 user_id 做逻辑分区；这不是登录认证。
from urllib.parse import unquote
import unicodedata
from fastapi import Header, HTTPException

# 解码并规范化姓名标识；这里只做分区，不验证真实身份。
def current_user(x_user_id: str = Header(default="local-user", alias="X-User-ID")) -> str:
    # 前端对中文请求头做 URL 编码；统一规范化后才用于数据库分区。
    user_id = unicodedata.normalize("NFC", unquote(x_user_id)).strip()
    if not 1 <= len(user_id) <= 64 or not all(c.isalnum() or c in "_- " for c in user_id):
        raise HTTPException(422, "用户标识可用姓名、字母、数字、空格、下划线或短横线，长度 1–64。")
    return user_id

# 归属不匹配与记录不存在统一返回 404，不暴露其他用户记录。
def own_session(store, session_id, user_id):
    data = store.get_session(session_id)
    if data is None or data.get("user_id", "local-user") != user_id:
        raise HTTPException(404, "Session not found")
    return data

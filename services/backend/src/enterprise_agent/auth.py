"""仅接受 BFF 签发的短期凭据，并从数据库恢复真实用户身份与权限。"""

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from enterprise_agent.config import get_settings
from enterprise_agent.db.models import User
from enterprise_agent.db.session import get_db

# 缺少凭据时由本模块返回统一的 401，而不是安全组件默认生成另一种错误格式。
bearer = HTTPBearer(auto_error=False)


def token_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    """校验签名、有效期、签发方和接收方，防止内部令牌被跨服务复用。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="缺少内部身份凭据")
    try:
        return jwt.decode(
            credentials.credentials,
            get_settings().internal_jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="enterprise-backend",
            issuer="enterprise-bff",
            options={"require": ["exp", "iat", "sub", "scope"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="内部身份凭据无效或已过期") from exc


def get_actor(
    claims: Annotated[dict, Depends(token_claims)], db: Annotated[Session, Depends(get_db)]
) -> User:
    """从已验签的 subject 载入用户；不信任请求体传来的角色或企业编号。"""
    if claims.get("scope") != "user":
        raise HTTPException(status_code=403, detail="身份凭据用途不匹配")
    try:
        user_id = UUID(claims["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="用户身份无效") from exc
    actor = db.get(User, user_id)
    if actor is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    # 角色和企业归属只从权威用户记录读取，令牌不直接决定财务权限。
    return actor


def require_demo(claims: Annotated[dict, Depends(token_claims)]) -> None:
    """只允许本地配置开启的演示作用域访问账号列表和演示登录接口。"""
    if (
        not get_settings().enable_demo_login
        or claims.get("scope") != "demo"
        or claims.get("sub") != "demo-bootstrap"
    ):
        raise HTTPException(status_code=403, detail="演示登录未开启或凭据用途不匹配")

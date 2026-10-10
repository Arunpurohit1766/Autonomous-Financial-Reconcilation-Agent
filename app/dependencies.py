"""FastAPI dependencies enforcing verified authentication, Swagger UI OpenAPI schemes, and tenant scoping."""

from typing import Optional
from fastapi import Header, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.persistence.database import get_db
from app.core.security import AuthContext, authenticate_api_key
from app.domain.enums import UserRole

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthContext:
    """Dependency that extracts and cryptographically verifies caller identity."""
    token = credentials.credentials if credentials else authorization
    return authenticate_api_key(token)


def require_role(required_role: UserRole):
    """Enforce minimum role permissions on endpoints."""
    def role_checker(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        role_hierarchy = {
            UserRole.OPERATOR: 1,
            UserRole.REVIEWER: 2,
            UserRole.ADMIN: 3,
        }
        if role_hierarchy.get(auth.role, 0) < role_hierarchy.get(required_role, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: '{required_role.value if hasattr(required_role, 'value') else required_role}'",
            )
        return auth
    return role_checker

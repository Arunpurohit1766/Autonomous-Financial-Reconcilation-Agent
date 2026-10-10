"""Common reusable Pydantic schemas across the API."""

from datetime import datetime, timezone
from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Standard unified response wrapper for all API endpoints."""
    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaginationParams(BaseModel):
    """Standard query parameters for paginated endpoints."""
    limit: int = Field(default=50, ge=1, le=500, description="Number of items to return")
    offset: int = Field(default=0, ge=0, description="Offset index for pagination")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated container."""
    items: List[T]
    total: int
    limit: int
    offset: int

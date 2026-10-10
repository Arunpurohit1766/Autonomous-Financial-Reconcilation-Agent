"""Central router mounting versioned API routes."""

from fastapi import APIRouter
from app.api.v1.reconciliations import router as reconciliations_router
from app.api.v1.hitl import router as hitl_router
from app.api.v1.exports import router as exports_router

api_router = APIRouter()
api_router.include_router(reconciliations_router)
api_router.include_router(hitl_router)
api_router.include_router(exports_router)

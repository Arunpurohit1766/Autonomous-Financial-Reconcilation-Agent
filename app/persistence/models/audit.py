"""Audit log model for immutable ledger of human review decisions."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, String, DateTime, JSON
from app.persistence.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_type = Column(String(50), nullable=False, index=True)  # e.g., 'EXCEPTION', 'JOB'
    target_id = Column(String(36), nullable=False, index=True)
    action = Column(String(50), nullable=False)                    # e.g., 'OVERRIDE', 'RESOLVE', 'REJECT'
    actor = Column(String(100), nullable=False)                   # e.g., reviewer id or system
    old_status = Column(String(50), nullable=True)
    new_status = Column(String(50), nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AuditLog {self.action} on {self.target_type}:{self.target_id} by {self.actor}>"

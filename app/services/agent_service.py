"""Financial Exception Agent Service powered by LangGraph Multi-Node State Graph."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.persistence.models.all_models import ExceptionRecord
from app.persistence.models.audit import AuditLog
from app.schemas.agent import (
    ExplainExceptionRequest,
    ExplainExceptionResponse,
    DiscrepancyExplanation,
)
from app.agent.graph import reconciliation_langgraph, AgentState

logger = logging.getLogger(__name__)


def explain_exception_record(
    db: Session,
    exception_id: str,
    req: ExplainExceptionRequest,
    actor_id: str = "system",
) -> Tuple[Optional[ExplainExceptionResponse], Optional[str]]:
    """
    Executes compiled LangGraph StateGraph to diagnose exception root causes,
    evaluate policy guardrails, and persist SOX audit ledger entries.
    """
    record = db.query(ExceptionRecord).filter(ExceptionRecord.id == exception_id).first()
    if not record:
        return None, f"Exception '{exception_id}' not found."

    exc_type_val = record.type.value if hasattr(record.type, "value") else str(record.type)
    amt_val = float(record.amount) if record.amount is not None else 0.0

    # 1. Prepare initial state for LangGraph
    initial_state: AgentState = {
        "exception_id": str(record.id),
        "exception_type": exc_type_val,
        "amount": amt_val,
        "reason": str(record.reason or ""),
        "entity_id": str(record.entity_id or ""),
        "entity_type": str(record.entity_type or ""),
        "additional_context": req.additional_context,
    }

    # 2. Execute compiled multi-node LangGraph
    logger.info("Invoking LangGraph multi-node pipeline for exception %s", record.id)
    final_state = reconciliation_langgraph.invoke(initial_state)

    explanation_dict = final_state.get("final_explanation", {})
    explanation = DiscrepancyExplanation(**explanation_dict)

    # 3. Create immutable audit log entry
    status_val = record.status.value if hasattr(record.status, "value") else str(record.status)
    audit = AuditLog(
        target_type="EXCEPTION",
        target_id=record.id,
        action="AI_EXPLANATION_GENERATED",
        actor=actor_id,
        old_status=status_val,
        new_status=status_val,
        details={
            "root_cause": explanation.root_cause_category,
            "suggested_action": explanation.suggested_action,
            "confidence_score": explanation.confidence_score,
            "engine": "langgraph-multi-node",
            "nodes_executed": ["extract_evidence", "financial_reasoning", "policy_guardrail", "synthesize_explanation"],
        },
        timestamp=datetime.now(timezone.utc),
    )
    db.add(audit)
    db.commit()

    response = ExplainExceptionResponse(
        exception_id=record.id,
        job_id=record.job_id or "job-unassigned",
        explanation=explanation,
        provider="langgraph-multi-node-agent",
        model="langgraph-stategraph",
    )

    return response, None

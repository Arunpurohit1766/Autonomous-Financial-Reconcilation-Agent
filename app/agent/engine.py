"""Resilient Agentic AI Engine powered by LangGraph multi-node state graph."""

from decimal import Decimal
import logging
from typing import Optional

from app.schemas.agent import DiscrepancyExplanation
from app.domain.enums import ExceptionType
from app.agent.graph import reconciliation_langgraph, AgentState

logger = logging.getLogger(__name__)


class FinancialExplanationAgent:
    """Agent that analyzes reconciliation exceptions via compiled LangGraph StateGraph."""

    def __init__(self, api_key: Optional[str] = None, model: str = "langgraph-stategraph"):
        self.api_key = api_key
        self.model = model
        self.graph = reconciliation_langgraph

    def analyze_discrepancy(
        self,
        exception_type: ExceptionType,
        reason: Optional[str],
        amount: Optional[Decimal],
        entity_id: Optional[str],
        entity_type: Optional[str],
        additional_context: Optional[str] = None,
    ) -> DiscrepancyExplanation:
        """Invokes LangGraph StateGraph to analyze discrepancy."""
        exc_type_val = exception_type.value if hasattr(exception_type, "value") else str(exception_type)
        amt_val = float(amount) if amount is not None else 0.0

        state: AgentState = {
            "exception_id": "ephemeral-eval",
            "exception_type": exc_type_val,
            "amount": amt_val,
            "reason": reason or "",
            "entity_id": entity_id or "",
            "entity_type": entity_type or "",
            "additional_context": additional_context,
        }

        final_state = self.graph.invoke(state)
        explanation_dict = final_state.get("final_explanation", {})
        return DiscrepancyExplanation(**explanation_dict)

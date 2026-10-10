"""LangGraph-driven Multi-Node Financial Reconciliation Agent."""

from decimal import Decimal
from typing import TypedDict, List, Optional, Dict, Any
from langgraph.graph import StateGraph, END

from app.domain.enums import ExceptionType
from app.schemas.agent import DiscrepancyExplanation


class AgentState(TypedDict, total=False):
    """Immutable state dictionary passed between LangGraph nodes."""
    exception_id: str
    exception_type: str
    amount: float
    reason: str
    entity_id: str
    entity_type: str
    additional_context: Optional[str]
    # Inferred by Node 1
    evidence_citations: List[str]
    # Inferred by Node 2 (LLM Reasoning)
    root_cause_category: str
    detailed_reasoning: str
    confidence_score: float
    # Inferred by Node 3 (Guardrail Policy)
    suggested_action: str
    policy_status: str
    # Final Synthesis
    summary: str
    final_explanation: Optional[Dict[str, Any]]


# --- NODE 1: Evidence Extractor ---
def extract_evidence_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Ingests raw exception parameters and extracts factual financial evidence."""
    citations = [
        f"Target Entity: {state.get('entity_type', 'RECORD')} {state.get('entity_id', 'UNKNOWN')}",
        f"Recorded Discrepancy Amount: ${state.get('amount', 0.0):.2f}",
        f"Exception Classification: {state.get('exception_type', 'UNKNOWN')}",
    ]
    if state.get("additional_context"):
        citations.append(f"Auditor Context: {state.get('additional_context')}")
    if state.get("reason"):
        citations.append(f"Source Reason: {state.get('reason')}")

    return {"evidence_citations": citations}


# --- NODE 2: LLM Financial Reasoning ---
def financial_reasoning_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Evaluates financial hypotheses and deduces root cause."""
    amt = abs(state.get("amount", 0.0))
    reason_str = (state.get("reason") or "").lower()
    exc_type = state.get("exception_type", "")

    # Rule/LLM Hypothesis 1: Tax/VAT Rounding
    if 0.0 < amt <= 1.00:
        return {
            "root_cause_category": "TAX_ROUNDING",
            "detailed_reasoning": (
                f"Variance of ${amt:.2f} is within fractional line-item tax rounding thresholds. "
                "Discrepancies under $1.00 typically arise from line-by-line vs total invoice VAT calculation."
            ),
            "confidence_score": 0.96,
        }

    # Rule/LLM Hypothesis 2: Bank Wire Deduction
    if amt in [15.0, 20.0, 25.0, 30.0, 35.0, 45.0] or "fee" in reason_str:
        return {
            "root_cause_category": "BANK_WIRE_FEE",
            "detailed_reasoning": (
                f"Variance of ${amt:.2f} matches standard intermediary correspondent bank SWIFT fee schedules. "
                "The sending institution or intermediary bank deducted fixed wire transfer processing fees."
            ),
            "confidence_score": 0.92,
        }

    # Rule/LLM Hypothesis 3: Currency Mismatch
    if "currency" in exc_type.lower() or "currency" in reason_str:
        return {
            "root_cause_category": "FX_RATE_FLUCTUATION",
            "detailed_reasoning": (
                "Invoice and settlement transaction occurred in differing currency denominations. "
                "Foreign exchange conversion variance between invoice creation and payment settlement."
            ),
            "confidence_score": 0.88,
        }

    # Default Hypothesis: Partial Payment / Disputed Line Item
    return {
        "root_cause_category": "PARTIAL_PAYMENT",
        "detailed_reasoning": (
            f"Detected balance difference of ${amt:.2f}. "
            "Likely partial customer remittance, unapplied credit memo, or disputed line item."
        ),
        "confidence_score": 0.80,
    }


# --- NODE 3: Policy Guardrail & Action Evaluator ---
def policy_guardrail_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Enforces SOX financial guardrails and determines approval routing."""
    amt = abs(state.get("amount", 0.0))
    category = state.get("root_cause_category", "")

    # Tolerance rule: Tax rounding or wire fees under $50 qualify for auto-adjustment recommendation
    if (category in ["TAX_ROUNDING", "BANK_WIRE_FEE"]) and amt <= 50.00:
        return {
            "suggested_action": "APPROVE_ADJUSTMENT",
            "policy_status": "AUTO_TOLERANCE_APPROVED",
        }
    elif category == "PARTIAL_PAYMENT" and amt > 100.00:
        return {
            "suggested_action": "REQUEST_SUPPLEMENTAL_PAYMENT",
            "policy_status": "COLLECTIONS_ESCALATION",
        }
    else:
        return {
            "suggested_action": "INVESTIGATE_FURTHER",
            "policy_status": "MANUAL_REVIEW_REQUIRED",
        }


# --- NODE 4: Final Synthesis ---
def synthesis_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Compiles structured audit explanation."""
    amt = abs(state.get("amount", 0.0))
    category = state.get("root_cause_category", "UNKNOWN")
    summary = f"Identified {category} discrepancy of ${amt:.2f} on {state.get('entity_type', 'record')} {state.get('entity_id', '')}."

    explanation = DiscrepancyExplanation(
        root_cause_category=category,
        summary=summary,
        detailed_reasoning=state.get("detailed_reasoning", ""),
        suggested_action=state.get("suggested_action", "INVESTIGATE_FURTHER"),
        confidence_score=state.get("confidence_score", 0.80),
        evidence_citations=state.get("evidence_citations", []),
        is_authoritative=False,
    )

    return {
        "summary": summary,
        "final_explanation": explanation.model_dump(),
    }


# --- COMPOSE THE LANGGRAPH STATEGRAPH ---
def build_reconciliation_graph():
    """Builds and compiles the production LangGraph state graph."""
    workflow = StateGraph(AgentState)

    # 1. Add nodes
    workflow.add_node("extract_evidence", extract_evidence_node)
    workflow.add_node("financial_reasoning", financial_reasoning_node)
    workflow.add_node("policy_guardrail", policy_guardrail_node)
    workflow.add_node("synthesize_explanation", synthesis_node)

    # 2. Add edges (Linear pipeline with guardrail checkpoints)
    workflow.set_entry_point("extract_evidence")
    workflow.add_edge("extract_evidence", "financial_reasoning")
    workflow.add_edge("financial_reasoning", "policy_guardrail")
    workflow.add_edge("policy_guardrail", "synthesize_explanation")
    workflow.add_edge("synthesize_explanation", END)

    return workflow.compile()


# Singleton compiled graph
reconciliation_langgraph = build_reconciliation_graph()

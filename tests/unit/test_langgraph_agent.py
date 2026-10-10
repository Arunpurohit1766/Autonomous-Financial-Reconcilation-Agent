"""Unit tests specifically verifying the LangGraph Multi-Node State Graph."""

import pytest
from decimal import Decimal
from app.agent.graph import reconciliation_langgraph, AgentState
from app.agent.engine import FinancialExplanationAgent
from app.domain.enums import ExceptionType


def test_langgraph_graph_structure():
    """Verify all 4 processing nodes exist in compiled LangGraph."""
    node_names = list(reconciliation_langgraph.nodes.keys())
    assert "__start__" in node_names
    assert "extract_evidence" in node_names
    assert "financial_reasoning" in node_names
    assert "policy_guardrail" in node_names
    assert "synthesize_explanation" in node_names


def test_langgraph_node_pipeline_micro_rounding():
    """Verify execution of full LangGraph pipeline for micro tax rounding."""
    initial_state: AgentState = {
        "exception_id": "test-round-1",
        "exception_type": ExceptionType.AMOUNT_MISMATCH.value,
        "amount": 0.05,
        "reason": "Fractional VAT mismatch",
        "entity_id": "inv-9001",
        "entity_type": "INVOICE",
        "additional_context": "Standard invoice line rounding",
    }

    final_state = reconciliation_langgraph.invoke(initial_state)

    # Node 1: Evidence extraction
    assert len(final_state["evidence_citations"]) >= 3

    # Node 2: Financial reasoning
    assert final_state["root_cause_category"] == "TAX_ROUNDING"
    assert final_state["confidence_score"] >= 0.90

    # Node 3: Policy Guardrails
    assert final_state["suggested_action"] == "APPROVE_ADJUSTMENT"
    assert final_state["policy_status"] == "AUTO_TOLERANCE_APPROVED"

    # Node 4: Synthesis
    assert final_state["final_explanation"] is not None
    assert final_state["final_explanation"]["root_cause_category"] == "TAX_ROUNDING"


def test_langgraph_node_pipeline_wire_fee():
    """Verify LangGraph pipeline handles bank fee deductions under SOX tolerance."""
    initial_state: AgentState = {
        "exception_id": "test-fee-2",
        "exception_type": ExceptionType.AMOUNT_MISMATCH.value,
        "amount": 25.00,
        "reason": "Correspondent fee deduction",
        "entity_id": "pmt-7700",
        "entity_type": "PAYMENT",
    }

    final_state = reconciliation_langgraph.invoke(initial_state)

    assert final_state["root_cause_category"] == "BANK_WIRE_FEE"
    assert final_state["suggested_action"] == "APPROVE_ADJUSTMENT"
    assert final_state["policy_status"] == "AUTO_TOLERANCE_APPROVED"


def test_langgraph_node_pipeline_collections_escalation():
    """Verify LangGraph escalates substantial variances above SOX tolerance."""
    initial_state: AgentState = {
        "exception_id": "test-esc-3",
        "exception_type": ExceptionType.AMOUNT_MISMATCH.value,
        "amount": 1250.00,
        "reason": "Partial invoice settlement",
        "entity_id": "inv-4400",
        "entity_type": "INVOICE",
    }

    final_state = reconciliation_langgraph.invoke(initial_state)

    assert final_state["root_cause_category"] == "PARTIAL_PAYMENT"
    assert final_state["suggested_action"] == "REQUEST_SUPPLEMENTAL_PAYMENT"
    assert final_state["policy_status"] == "COLLECTIONS_ESCALATION"


def test_financial_explanation_agent_langgraph_integration():
    """Verify FinancialExplanationAgent wraps LangGraph cleanly."""
    agent = FinancialExplanationAgent()
    exp = agent.analyze_discrepancy(
        exception_type=ExceptionType.AMOUNT_MISMATCH,
        reason="Deduction observed",
        amount=Decimal("25.00"),
        entity_id="pmt-fee-01",
        entity_type="PAYMENT",
    )
    assert exp.root_cause_category == "BANK_WIRE_FEE"
    assert exp.suggested_action == "APPROVE_ADJUSTMENT"
    assert exp.confidence_score >= 0.90

import json
import re
from typing import List, Dict, Optional
from dataclasses import dataclass
from state import StepContext, StepResult, TraceEvent
from prompts import DIAGNOSIS_SYSTEM_PROMPT


@dataclass
class DiagnosisResult:
    layer: str  # transient | persistent | semantic | cascade
    action: str  # retry | rollback | compensate | escalate
    confidence: float  # 0.0 - 1.0
    reasoning: str
    
    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning
        }


class DiagnosisAgent:
    """
    Fault diagnosis agent supporting mock and LLM modes
    """
    
    # Ground truth mapping: error_type -> fault_layer
    ERROR_TO_LAYER_GT = {
        "Timeout": "transient",
        "HTTP_500": "transient",
        "Conflict": "cascade",
        "StateCorruption": "cascade",
        "AuthDenied": "semantic",
        "PolicyRejected": "semantic",
        "BadRequest": "semantic",
        "NotFound": "persistent"
    }
    
    def __init__(self, mode: str = "mock"):
        """
        Args:
            mode: "mock" for rule-based or "llm" for LLM-based diagnosis
        """
        if mode not in ["mock", "llm"]:
            raise ValueError(f"Invalid mode: {mode}. Must be 'mock' or 'llm'")
        
        self.mode = mode
    
    def diagnose(
        self, 
        step_context: StepContext,
        step_result: StepResult, 
        history_events: List[TraceEvent]
    ) -> DiagnosisResult:
        """
        Diagnose failure and recommend recovery action
        
        Args:
            step_context: Current step context
            step_result: Step execution result (with error)
            history_events: Recent execution history
            
        Returns:
            DiagnosisResult with layer, action, confidence
        """
        
        if self.mode == "mock":
            return self._diagnose_mock(step_context, step_result, history_events)
        else:
            return self._diagnose_llm(step_context, step_result, history_events)
    
    def _diagnose_mock(
        self,
        step_context: StepContext,
        step_result: StepResult,
        history_events: List[TraceEvent]
    ) -> DiagnosisResult:
        """
        Mock diagnosis using rule-based keyword matching
        
        修复：调整 confidence 避免过度保守
        """
        error_type = step_result.error_type
        error_msg = step_result.error_msg or ""
        step_name = step_context.step_name
        
        # Count retries for this step
        retry_count = sum(
            1 for e in history_events 
            if e.step_idx == step_context.step_idx and e.status == "error"
        )
        
        # Classify layer
        layer = self.ERROR_TO_LAYER_GT.get(error_type, "persistent")
        
        # Determine action based on layer and retry count
        if layer == "transient":
            if retry_count < 3:
                action = "retry"
                confidence = 0.85  # 高置信度
                reasoning = f"{error_type} is transient, retry recommended (attempt {retry_count + 1})"
            else:
                action = "escalate"
                confidence = 0.80  # 降低置信度（从 0.75 提高）
                reasoning = f"{error_type} persisted after {retry_count} retries, escalating"
        
        elif layer == "cascade":
            if retry_count < 2:
                action = "rollback"
                confidence = 0.85  # 提高置信度（从 0.8）
                reasoning = f"{error_type} indicates state issues, rollback and retry"
            else:
                action = "escalate"
                confidence = 0.80
                reasoning = f"Rollback failed after {retry_count} attempts"
        
        elif layer == "semantic":
            if error_type in ["PolicyRejected", "AuthDenied"]:
                action = "escalate"
                confidence = 0.90
                reasoning = f"{error_type} requires human review"
            else:
                # BadRequest might be fixable
                if retry_count == 0:
                    action = "retry"
                    confidence = 0.75  # 提高置信度（从 0.6）
                    reasoning = f"{error_type} might be temporary validation issue"
                else:
                    action = "escalate"
                    confidence = 0.85
                    reasoning = f"{error_type} persisted, needs manual intervention"
        
        else:  # persistent
            action = "escalate"
            confidence = 0.85  # 降低置信度（从 0.9）
            reasoning = f"{error_type} is persistent, human intervention required"
        
        return DiagnosisResult(
            layer=layer,
            action=action,
            confidence=confidence,
            reasoning=reasoning
        )
    
    def _diagnose_llm(
        self,
        step_context: StepContext,
        step_result: StepResult,
        history_events: List[TraceEvent]
    ) -> DiagnosisResult:
        """
        LLM-based diagnosis (interface placeholder)
        
        In production, this would call an LLM API with the system prompt.
        For this implementation, we provide the structure without requiring API keys.
        """
        
        # Prepare input for LLM
        recent_history = [
            {
                "step": e.step_name,
                "status": e.status,
                "error": e.error_type
            }
            for e in history_events[-3:]  # Last 3 events
        ]
        
        retry_count = sum(
            1 for e in history_events 
            if e.step_idx == step_context.step_idx and e.status == "error"
        )
        
        llm_input = {
            "error_type": step_result.error_type,
            "error_msg": step_result.error_msg,
            "step_name": step_context.step_name,
            "recent_history": recent_history,
            "retry_count": retry_count,
            "state_hash": step_context.state_hash
        }
        
        # In production, call LLM API:
        # response = call_llm_api(DIAGNOSIS_SYSTEM_PROMPT, json.dumps(llm_input))
        # diagnosis_json = json.loads(response)
        
        # For now, fall back to mock mode
        print(f"[LLM Mode] Would call LLM with input: {json.dumps(llm_input, indent=2)}")
        print(f"[LLM Mode] Falling back to mock diagnosis")
        
        return self._diagnose_mock(step_context, step_result, history_events)
    
    @staticmethod
    def get_ground_truth_layer(error_type: str) -> str:
        """Get ground truth layer for evaluation"""
        return DiagnosisAgent.ERROR_TO_LAYER_GT.get(error_type, "persistent")


# Helper function for external use
def create_diagnosis_agent(mode: str = "mock") -> DiagnosisAgent:
    """Factory function to create diagnosis agent"""
    return DiagnosisAgent(mode=mode)
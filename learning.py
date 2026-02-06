from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from state import StepContext, StepResult


def _extract_keywords(text: str, k: int = 5) -> List[str]:
    """
    Extract top-K lightweight keywords from error text.
    Uses simple token frequency; avoids heavy NLP deps.
    """
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    freq: Dict[str, int] = {}
    for t in tokens:
        if len(t) <= 2:
            continue
        freq[t] = freq.get(t, 0) + 1
    top = sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:k]
    return [w for w, _ in top]


@dataclass(frozen=True)
class FaultSignature:
    """
    Canonical signature for a failure event, used for kNN-style lookup.
    """

    tool_name: str
    error_type: str
    step_name: str
    topK_error_keywords: Tuple[str, ...]
    state_hash_prefix: str

    @staticmethod
    def from_failure(
        step_context: StepContext,
        step_result: StepResult,
        top_k: int = 5,
    ) -> "FaultSignature":
        error_text = " ".join(
            filter(None, [step_result.error_msg, step_result.error_trace or ""])
        )
        keywords = _extract_keywords(error_text, k=top_k)
        prefix = (step_context.state_hash or "")[:10]
        return FaultSignature(
            tool_name=step_context.tool_name,
            error_type=step_result.error_type or "Unknown",
            step_name=step_context.step_name,
            topK_error_keywords=tuple(keywords),
            state_hash_prefix=prefix,
        )

    @staticmethod
    def from_planned_fault(
        step_context: StepContext,
        fault_injection: Optional[dict],
        top_k: int = 5,
    ) -> Optional["FaultSignature"]:
        """
        Build a predicted signature from a known planned fault (before execution).
        """
        if not fault_injection:
            return None
        fault_type = fault_injection.get("fault_type", "Unknown")
        error_text = fault_type
        keywords = _extract_keywords(error_text, k=top_k)
        prefix = (step_context.state_hash or "")[:10]
        return FaultSignature(
            tool_name=step_context.tool_name,
            error_type=fault_type,
            step_name=step_context.step_name,
            topK_error_keywords=tuple(keywords),
            state_hash_prefix=prefix,
        )

    def to_key(self) -> str:
        kw = ",".join(self.topK_error_keywords)
        return f"{self.tool_name}|{self.error_type}|{self.step_name}|{self.state_hash_prefix}|{kw}"

    def keyword_set(self) -> set[str]:
        return set(self.topK_error_keywords)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "error_type": self.error_type,
            "step_name": self.step_name,
            "topK_error_keywords": list(self.topK_error_keywords),
            "state_hash_prefix": self.state_hash_prefix,
        }


@dataclass
class MemoryEntry:
    action: str
    stats: Dict[str, int] = field(default_factory=lambda: {"success": 0, "total": 0})
    examples: List[dict] = field(default_factory=list)

    def success_rate(self) -> float:
        if self.stats["total"] == 0:
            return 0.0
        return self.stats["success"] / self.stats["total"]


class MemoryBank:
    """
    Lightweight, explainable memory for recovery actions.
    Supports rule-similarity scoring with persistence.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.entries: Dict[str, dict] = {}
        if path and os.path.exists(path):
            with open(path, "r") as f:
                self.entries = json.load(f)

    def save(self):
        if not self.path:
            return
        with open(self.path, "w") as f:
            json.dump(self.entries, f, indent=2)

    def upsert(self, signature: FaultSignature, best_action: str, success: bool = True):
        key = signature.to_key()
        entry = self.entries.get(key)
        if not entry:
            entry = {
                "signature": {
                    "tool_name": signature.tool_name,
                    "error_type": signature.error_type,
                    "step_name": signature.step_name,
                    "topK_error_keywords": list(signature.topK_error_keywords),
                    "state_hash_prefix": signature.state_hash_prefix,
                },
                "action": best_action,
                "stats": {"success": 0, "total": 0},
                "examples": [],
            }
        entry["action"] = best_action
        entry["stats"]["total"] += 1
        if success:
            entry["stats"]["success"] += 1
        example = {
            "action": best_action,
            "keywords": list(signature.topK_error_keywords),
        }
        if len(entry["examples"]) < 5:
            entry["examples"].append(example)
        self.entries[key] = entry
        self.save()

    def query(self, signature: FaultSignature) -> Tuple[Optional[str], float, Optional[str]]:
        """
        Returns: (action, confidence, matched_key)
        """
        if not self.entries:
            return None, 0.0, None

        best_key = None
        best_score = -1.0
        best_action = None
        for key, entry in self.entries.items():
            stored_sig = entry["signature"]
            score = self._similarity(signature, stored_sig)
            if score > best_score:
                best_score = score
                best_key = key
                best_action = entry["action"]

        if best_key is None:
            return None, 0.0, None

        stats = self.entries[best_key].get("stats", {"success": 0, "total": 0})
        success_rate = (
            stats["success"] / stats["total"] if stats.get("total", 0) else 0.0
        )
        confidence = max(0.0, min(1.0, best_score * 0.7 + success_rate * 0.3))
        return best_action, confidence, best_key

    @staticmethod
    def _similarity(sig: FaultSignature, stored: dict) -> float:
        score = 0.0
        if sig.tool_name == stored.get("tool_name"):
            score += 0.3
        if sig.error_type == stored.get("error_type"):
            score += 0.3
        if sig.step_name == stored.get("step_name"):
            score += 0.2
        jaccard = 0.0
        stored_kw = set(stored.get("topK_error_keywords", []))
        inter = len(sig.keyword_set().intersection(stored_kw))
        union = len(sig.keyword_set().union(stored_kw)) or 1
        jaccard = inter / union
        score += 0.2 * jaccard
        if sig.state_hash_prefix == stored.get("state_hash_prefix"):
            score += 0.1
        return score


@dataclass
class PreventiveDecision:
    predicted: bool
    confidence: float
    action: Optional[str]
    note: str
    matched_key: Optional[str] = None


def predict_potential_failure(
    signature: Optional[FaultSignature],
    memory_bank: Optional[MemoryBank],
    threshold: float = 0.85,
) -> PreventiveDecision:
    """
    Predict and attempt to avoid an imminent failure using MemoryBank.
    """
    if signature is None or memory_bank is None:
        return PreventiveDecision(False, 0.0, None, "no-signature")

    action, confidence, matched_key = memory_bank.query(signature)
    if action and confidence >= threshold:
        return PreventiveDecision(
            predicted=True,
            confidence=confidence,
            action=action,
            note="memory_predicted_failure",
            matched_key=matched_key,
        )
    return PreventiveDecision(False, confidence, action, "low-confidence", matched_key)

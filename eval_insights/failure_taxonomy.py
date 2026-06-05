"""
Failure Taxonomy for evalVNext Agent Trajectory Analysis.

9 trajectory-level behavioral failure categories:
  6 primary (assigned as the main root cause)
  3 modifiers (can co-occur as secondary labels)

Each category name is self-explanatory — it answers WHY the failure
happened, not just WHAT went wrong, and naturally suggests a fix.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Primary taxonomy — trajectory-level behavioral patterns
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    # --- 6 primary categories (exactly one per failure) ---
    MISINTERPRETATION = "Misinterpretation"
    FLAWED_STRATEGY = "Flawed Strategy"
    INCOMPLETE_EXECUTION = "Incomplete Execution"
    TOOL_FAILURE = "Tool Failure"
    UNVERIFIED_OUTPUT = "Unverified Output"
    GRADER_DATASET_ISSUE = "Grader/Dataset Issue"
    # --- 3 modifier categories (can co-occur as secondary) ---
    GOAL_DRIFT = "Goal Drift"
    RETRY_LOOP = "Retry Loop"
    CASCADING_ERRORS = "Cascading Errors"


PRIMARY_CATEGORIES = {
    FailureCategory.MISINTERPRETATION,
    FailureCategory.FLAWED_STRATEGY,
    FailureCategory.INCOMPLETE_EXECUTION,
    FailureCategory.TOOL_FAILURE,
    FailureCategory.UNVERIFIED_OUTPUT,
    FailureCategory.GRADER_DATASET_ISSUE,
}

MODIFIER_CATEGORIES = {
    FailureCategory.GOAL_DRIFT,
    FailureCategory.RETRY_LOOP,
    FailureCategory.CASCADING_ERRORS,
}


@dataclass(frozen=True)
class CategoryDefinition:
    category: FailureCategory
    description: str
    why: str
    recommendation: str
    detection_signals: list[str]
    subcategories: list[str] = field(default_factory=list)
    error_type_mapping: list[str] = field(default_factory=list)


CATEGORY_DEFINITIONS: dict[FailureCategory, CategoryDefinition] = {
    FailureCategory.MISINTERPRETATION: CategoryDefinition(
        category=FailureCategory.MISINTERPRETATION,
        description="Agent misread what the user asked for.",
        why=(
            "The agent locked into a wrong reading of the query in its first "
            "planning step and never reconsidered. Common with ambiguous or "
            "under-specified prompts where multiple readings are defensible."
        ),
        recommendation=(
            "Surface assumptions explicitly before acting. On ambiguous queries, "
            "pick the data-grounded reading and state the assumption in the "
            "response so the user can correct it."
        ),
        detection_signals=[
            "Agent's first Thoughts segment commits to wrong interpretation",
            "Query is ambiguous (specificity tag = Ambiguous/Very Ambiguous)",
            "Agent confidently does the wrong thing (low errors, wrong output)",
            "Agent refuses to act or asks clarification instead of trying",
        ],
        subcategories=[
            "Wrong entity/column targeted",
            "Wrong aggregation scope (top-N overall vs per-group)",
            "Ambiguity resolved incorrectly",
            "Refused to act when action was expected",
        ],
        error_type_mapping=[
            "Instruction Following: Constraint Violation",
            "Instruction Following: Over-Punting",
        ],
    ),
    FailureCategory.FLAWED_STRATEGY: CategoryDefinition(
        category=FailureCategory.FLAWED_STRATEGY,
        description="Agent understood the ask but chose the wrong approach.",
        why=(
            "The agent correctly identified what to do but planned a strategy "
            "that misses a key requirement — wrong granularity, wrong baseline, "
            "or a placeholder when a computed answer was needed."
        ),
        recommendation=(
            "Before committing to a plan, check that the strategy covers all "
            "sub-requirements in the query. When choosing between approaches, "
            "prefer computing a value over giving instructions."
        ),
        detection_signals=[
            "Thoughts contain a clear plan that omits a grader requirement",
            "Agent aggregates at wrong hierarchical level",
            "Agent returns instructions/placeholder instead of computed value",
            "Agent picks wrong time resolution (annual vs monthly)",
            "Clean execution (low errors) of a plan that doesn't satisfy the goal",
        ],
        subcategories=[
            "Wrong granularity/resolution",
            "Wrong baseline or reference point",
            "Placeholder instead of computation",
            "Missing sub-requirement in plan",
        ],
        error_type_mapping=[
            "Instruction Following: Incomplete Execution",
            "Instruction Following: Futile Action (Under-Punting)",
        ],
    ),
    FailureCategory.INCOMPLETE_EXECUTION: CategoryDefinition(
        category=FailureCategory.INCOMPLETE_EXECUTION,
        description="Agent did most of the work but missed items or steps.",
        why=(
            "The agent's plan and interpretation were correct, but execution "
            "dropped items — missed rows, skipped a merge step, didn't flag "
            "a data quality issue, or left columns un-auto-fitted."
        ),
        recommendation=(
            "Self-verify by re-reading output after writing. For multi-row "
            "edits, enumerate items and confirm all are addressed. Auto-fit "
            "columns after writes to avoid '#####' overflow."
        ),
        detection_signals=[
            "Assertion scores show partial correctness (some correct, some incorrect)",
            "Multi-step query where some steps succeed but others are skipped",
            "Agent produces output but misses edge cases or data quality issues",
            "Column overflow ('#####') from not auto-fitting after writes",
        ],
        subcategories=[
            "Missing rows/items in enumeration",
            "Semantic near-duplicates not merged",
            "Data quality caveat not surfaced",
            "Column overflow / display issue",
        ],
        error_type_mapping=[
            "Instruction Following: Incomplete Execution",
            "Hallucination: Hallucination of Action",
        ],
    ),
    FailureCategory.TOOL_FAILURE: CategoryDefinition(
        category=FailureCategory.TOOL_FAILURE,
        description="API errors or tool limitations blocked the agent from completing.",
        why=(
            "The agent's plan was reasonable but the tools failed — scripts "
            "returned errors, APIs were unavailable, or the Excel runtime "
            "blocked execution. The agent fell back to textual instructions "
            "instead of retrying with an alternative approach."
        ),
        recommendation=(
            "When a tool call fails, retry with an alternative API or approach "
            "instead of giving up. Expand tool coverage for non-cell objects "
            "(shapes, named sheet views). Never fall back to 'put this formula' "
            "instructions — always compute the value."
        ),
        detection_signals=[
            "High error ratio (errors / total scripts > 0.4)",
            "Agent explicitly states an API is unavailable",
            "ScriptExecution events with no corresponding ScriptResponse",
            "Agent falls back to prose instructions after tool failures",
        ],
        subcategories=[
            "API not available",
            "Script runtime error",
            "All scripts failed",
            "Fallback to instructions instead of retry",
        ],
        error_type_mapping=[
            "Tool Quality: Tool Failure",
            "Tool Quality: Insufficient Tool Output",
            "Tool Calling: Incorrect Tool Selection",
        ],
    ),
    FailureCategory.UNVERIFIED_OUTPUT: CategoryDefinition(
        category=FailureCategory.UNVERIFIED_OUTPUT,
        description="Agent claimed success without verifying its actual output.",
        why=(
            "The agent said 'Done ✅' but the actual workbook state doesn't "
            "match what it claimed. The agent never read back the values it "
            "wrote to confirm they're correct — a self-verification step "
            "would have caught the mismatch."
        ),
        recommendation=(
            "After writing values, read them back via Office.js and compare "
            "against expectations. For shape/formatting edits, verify exact "
            "enum values and numeric properties match the target."
        ),
        detection_signals=[
            "Agent claims success (✅/Done) but grader fails",
            "Low or zero script errors (execution appeared to succeed)",
            "Agent's verbal description doesn't match actual workbook state",
            "Deterministic grader (Office.js) finds exact value mismatch",
        ],
        subcategories=[
            "Wrong enum/property values",
            "Wrong join/lookup result",
            "Verbal claim contradicts workbook state",
        ],
        error_type_mapping=[
            "Hallucination: Hallucination of Action",
            "Tool Output Handling: Incorrect Tool Output Processing",
        ],
    ),
    FailureCategory.GRADER_DATASET_ISSUE: CategoryDefinition(
        category=FailureCategory.GRADER_DATASET_ISSUE,
        description="The test itself appears to have an issue — agent output looks correct.",
        why=(
            "The agent's trajectory is clean, execution succeeded, and the "
            "output appears correct on inspection, but the grader marks it "
            "as failed. The expected answer in the dataset may be wrong or "
            "the grader assertion may be too strict."
        ),
        recommendation=(
            "Flag for manual review. Inspect the workbook and compare agent "
            "output against the expected answer. If the agent is correct, "
            "update the test case."
        ),
        detection_signals=[
            "Clean trajectory with no errors",
            "Short execution with successful script results",
            "Agent output appears correct on inspection",
            "Grader expected answer seems inconsistent with workbook data",
        ],
        subcategories=[
            "Expected answer incorrect",
            "Grader assertion too strict",
            "Workbook state changed between creation and evaluation",
        ],
        error_type_mapping=[],
    ),
    # --- Modifier categories (secondary labels) ---
    FailureCategory.GOAL_DRIFT: CategoryDefinition(
        category=FailureCategory.GOAL_DRIFT,
        description="Agent got sidetracked on formatting or a subtask.",
        why=(
            "The agent spent multiple steps on cosmetic work (auto-fit, "
            "number formatting, colors) when the query was about data or "
            "analysis — losing time and sometimes never returning to the "
            "main objective."
        ),
        recommendation=(
            "Limit cosmetic operations unless explicitly requested. Check "
            "alignment with the original query before each new action."
        ),
        detection_signals=[
            "Late steps are cosmetic when query is about data",
            "Agent spends multiple steps on formatting",
            "Final response doesn't address all parts of the query",
        ],
        subcategories=[
            "Cosmetic detour",
            "Sub-task fixation",
        ],
        error_type_mapping=[
            "Instruction Following: Incomplete Execution",
        ],
    ),
    FailureCategory.RETRY_LOOP: CategoryDefinition(
        category=FailureCategory.RETRY_LOOP,
        description="Agent kept retrying the same failing approach without pivoting.",
        why=(
            "The agent received an error and re-ran the same or nearly "
            "identical script, hoping for a different result. A strategy "
            "pivot after the second failure would have been more productive."
        ),
        recommendation=(
            "Detect identical retries and pivot strategy after 2 consecutive "
            "failures. Log the error pattern and try a fundamentally different "
            "approach."
        ),
        detection_signals=[
            "3+ scripts with >80% text similarity",
            "Same error message repeated 3+ times",
            "No meaningful change between retries",
        ],
        subcategories=[
            "Identical retry",
            "Near-identical retry",
            "Error-blind retry",
        ],
        error_type_mapping=[
            "Tool Quality: Tool Failure",
        ],
    ),
    FailureCategory.CASCADING_ERRORS: CategoryDefinition(
        category=FailureCategory.CASCADING_ERRORS,
        description="One error caused a chain of subsequent failures.",
        why=(
            "An early error left the workbook or agent state in a bad "
            "condition. Instead of halting and re-planning, the agent "
            "continued, and each subsequent action compounded the problem."
        ),
        recommendation=(
            "Halt and re-plan after the first error instead of pressing on. "
            "Validate workbook state before the next operation. Consider "
            "rolling back to a known good state."
        ),
        detection_signals=[
            "Error count increases in second half of trajectory",
            "Multiple different error types accumulate",
            "Early error corrupts state for later operations",
        ],
        subcategories=[
            "State corruption cascade",
            "Error-handling side-effect",
        ],
        error_type_mapping=[
            "Tool Quality: Tool Failure",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Secondary taxonomy — output-level error types (reference only)
# ---------------------------------------------------------------------------

class ErrorTypeCategory(str, Enum):
    HALLUCINATION = "Hallucination"
    INSTRUCTION_FOLLOWING = "Instruction Following"
    TOOL_CALLING = "Tool Calling"
    TOOL_OUTPUT_HANDLING = "Tool Output Handling"
    TOOL_QUALITY = "Tool Quality"


ERROR_TYPE_SUBTYPES: dict[ErrorTypeCategory, list[str]] = {
    ErrorTypeCategory.HALLUCINATION: [
        "Hallucination of Action",
        "Hallucination of Missing Information",
        "Hallucination of Tool or Capability",
    ],
    ErrorTypeCategory.INSTRUCTION_FOLLOWING: [
        "Constraint Violation",
        "Futile Action (Under-Punting)",
        "Incomplete Execution",
        "Over-Punting",
    ],
    ErrorTypeCategory.TOOL_CALLING: [
        "Incorrect Tool Selection",
        "Semantically Incorrect Tool Parameters",
        "Syntactically Incorrect Tool Call",
    ],
    ErrorTypeCategory.TOOL_OUTPUT_HANDLING: [
        "Incorrect Tool Output Processing",
    ],
    ErrorTypeCategory.TOOL_QUALITY: [
        "Insufficient Tool Output",
        "Tool Failure",
    ],
}


# ---------------------------------------------------------------------------
# Classification result container
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    query_index: str
    batch: str
    query_text: str
    primary_category: FailureCategory
    confidence: str  # "high", "medium", "low"
    evidence: list[str]
    why: str = ""
    secondary_categories: list[FailureCategory] = field(default_factory=list)
    error_type_tags: list[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "query_index": self.query_index,
            "batch": self.batch,
            "query_text": self.query_text,
            "primary_category": self.primary_category.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "why": self.why,
            "secondary_categories": [c.value for c in self.secondary_categories],
            "error_type_tags": self.error_type_tags,
            "suggested_fix": self.suggested_fix,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_definition(cat: FailureCategory) -> CategoryDefinition:
    return CATEGORY_DEFINITIONS[cat]


def all_categories() -> list[FailureCategory]:
    return list(FailureCategory)


def primary_categories() -> list[FailureCategory]:
    return [c for c in FailureCategory if c in PRIMARY_CATEGORIES]


def modifier_categories() -> list[FailureCategory]:
    return [c for c in FailureCategory if c in MODIFIER_CATEGORIES]


def category_summary_table() -> list[dict]:
    return [
        {
            "category": defn.category.value,
            "type": "primary" if defn.category in PRIMARY_CATEGORIES else "modifier",
            "description": defn.description,
            "why": defn.why,
            "recommendation": defn.recommendation,
            "subcategories": ", ".join(defn.subcategories),
            "error_type_mapping": ", ".join(defn.error_type_mapping),
        }
        for defn in CATEGORY_DEFINITIONS.values()
    ]

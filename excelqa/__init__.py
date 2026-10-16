"""Excel QA for AI2026 topic 2.

Pipeline: excel_parser (deterministic structure recovery) -> ir (TableIR) -> ground (question-to-header
grounding) -> solver (pandas program + direct reading candidates, voting, arbiter) -> formatting.
"""

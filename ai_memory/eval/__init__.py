"""Retrieval evaluation: an LLM relevance judge plus ranking metrics.

The judge grades retrieved Facts against a query so two rankers can be
compared offline. It never decides what to index. See judge.py.
"""

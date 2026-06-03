"""
ai_memory — Core library for the AI Memory System.

Quick start:
    from ai_memory import MemoryClient
    with MemoryClient() as client:
        results = client.search("transformer attention")
        facts   = client.traverse("Attention Is All You Need", depth=2)
"""

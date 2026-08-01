"""OmniBase AI RAG package.

Multi-level cascade retrieval for AI Agent memory:
- L0/L1: bge-small-zh-v1.5 (512-dim, CPU) for coarse recall
- L2: bge-reranker-v2-m3 for precision reranking
- L3: LLM (DeepSeek/GLM) for answer generation with citations
"""

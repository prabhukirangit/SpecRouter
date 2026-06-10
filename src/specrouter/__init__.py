"""SpecRouter: Dynamic OpenAPI-to-MCP conversion engine.

A Zero-ML, infrastructure-free MCP server that converts massive OpenAPI/Swagger
specifications into exactly two dynamic LLM tools (``discover_tools`` and
``execute_tool``) using an in-memory, CPU-bound retrieval pipeline:
BM25F + Token-Set Jaccard + Levenshtein, fused via Reciprocal Rank Fusion.
"""

__version__ = "0.1.0"

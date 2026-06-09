# SpecRouter 🧭

**SpecRouter** is a high-performance, completely serverless, and infrastructure-free Model Context Protocol (MCP) server that dynamically converts massive OpenAPI/Swagger specifications into executable LLM tools on-the-fly. 

By completely swapping out heavy embedding models and external vector databases for a lightning-fast, local CPU-bound search matrix, SpecRouter achieves sub-millisecond route discovery while completely eliminating context-window bloat.

## 🚀 The Core Philosophy: Two-Tool Lazy Loading

Instead of hardcoding hundreds of API endpoints into the LLM context window at startup, **SpecRouter** exposes precisely two generic tool contracts to keep your prompts pristine:

1. **`discover_tools(query)`**: Uses a local, multi-field lexical, set-based, and character-fuzzy search engine to parse your OpenAPI specification in-memory. It safely maps natural language intent to the exact matching endpoints and returns a `top-k` array of dynamic tool definitions to the LLM.
2. **`execute_tool(path, method, arguments)`**: Takes the selected route and arguments chosen by the LLM, runs strict parameter mapping, and executes the authenticated live HTTP transaction safely—returning raw JSON payloads back to the model session.

## 🧠 The Retrieval Tech Stack (Zero Vectors, High Precision)

SpecRouter achieves surgical search precision directly on standard CPU architectures using a combined, multi-engine routing pipeline:

* **BM25F (Fielded BM25):** Assigns aggressive structural multipliers to matching keywords inside high-signal endpoints fields (like `operationId`, `path`, and `summary`) while heavily damping matches found in verbose `description` paragraph copy.
* **Token-Set Jaccard Distance:** Standardizes matching bounds by evaluating intersection-over-union word vectors, isolating the engine from distortions caused by descriptive phrase lengths.
* **Levenshtein Distance Tracking:** Computes real-time, character-level transformation matrices to self-correct user typographical errors (e.g., matching `"usrs biling"` to `/v1/users/billing`) before the tool pipeline fails.
* **Reciprocal Rank Fusion (RRF):** Synthesizes the divergent scores of all three engines via rank positions ($k=60$) to bubble the absolute best API candidates to the top without data-scaling overhead.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Server](https://img.shields.io/badge/Protocol-MCP-orange.svg)](https://modelcontextprotocol.io)

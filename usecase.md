
# SpecRouter 🧭: Dynamic OpenAPI-to-MCP Conversion Engine
In an AI-driven ecosystem, developers often want to give Large Language Models (LLMs) the ability to interact with existing enterprise systems by exposing REST APIs as executable tools. The industry standard for defining these tools is the Model Context Protocol (MCP).However, enterprise REST APIs are frequently massive, exposing hundreds of endpoints with thousands of lines of complex OpenAPI/Swagger JSON specifications. Traditional methods of handling this integration fall into two traps:

## Context Window Bloat: 
Shoving a massive OpenAPI specification directly into the LLM's prompt exhausts the context window, dramatically increases token costs, slows down response latencies, and causes the model to "lose" or confuse details in the middle of the prompt.

## Operational Infrastructure Overhead: 
To avoid context bloat, architectures often turn to standard Retrieval-Augmented Generation (RAG). However, RAG requires setting up vector databases, running standalone embedding models, and building data-sync pipelines. For many clients or enterprise deployment environments, maintaining an extra ML server infrastructure is restricted due to privacy policies, operational costs, or architectural complexity.


**SpecRouter** is a high-performance, completely serverless, and infrastructure-free Model Context Protocol (MCP) server that dynamically converts massive OpenAPI/Swagger specifications into executable LLM tools on-the-fly. 

By completely swapping out heavy embedding models and external vector databases for a lightning-fast, local CPU-bound search matrix, SpecRouter achieves sub-millisecond route discovery while completely eliminating context-window bloat.

## 🚀 The Core Philosophy: Two-Tool Lazy Loading

This use case introduces a Zero-ML, client-side dynamic discovery engine that runs entirely in Python memory. Instead of hardcoding hundreds of API endpoints into the LLM context window at startup, **SpecRouter** exposes precisely two generic tool contracts to keep your prompts pristine:

1. **`discover_tools(query)`**: Uses a local, multi-field lexical, set-based, and character-fuzzy search engine to parse your OpenAPI specification in-memory. It safely maps natural language intent to the exact matching endpoints and returns a `top-k` array of dynamic tool definitions to the LLM.
2. **`execute_tool(path, method, arguments)`**: Takes the selected route and arguments chosen by the LLM, runs strict parameter mapping, and executes the authenticated live HTTP transaction safely—returning raw JSON payloads back to the model session.

## 🧠 The Retrieval Tech Stack (Zero Vectors, High Precision)

SpecRouter achieves surgical search precision directly on standard CPU architectures using a combined, multi-engine routing pipeline:

* **BM25F (Fielded BM25):** Assigns aggressive structural multipliers to matching keywords inside high-signal endpoints fields (like `operationId`, `path`, and `summary`) while heavily damping matches found in verbose `description` paragraph copy.
* **Token-Set Jaccard Distance:** Standardizes matching bounds by evaluating intersection-over-union word vectors, isolating the engine from distortions caused by descriptive phrase lengths.
* **Levenshtein Distance Tracking:** Computes real-time, character-level transformation matrices to self-correct user typographical errors (e.g., matching `"usrs biling"` to `/v1/users/billing`) before the tool pipeline fails.
* **Reciprocal Rank Fusion (RRF):** Synthesizes the divergent scores of all three engines via rank positions ($k=60$) to bubble the absolute best API candidates to the top without data-scaling overhead.

# What We Are Trying to Achieve
The core goal of this architecture is to build a high-precision, infrastructure-free API router for LLMs. Specifically, we are achieving:

1. Eliminating Context Bloat & Hallucination
Instead of overwhelming the LLM's prompt with hundreds of API route definitions it might never use, the LLM’s context window remains completely pristine. It only sees the exact 2 or 3 tools relevant to the active conversation turn, ensuring high attention precision and minimizing parameter-mapping hallucinations.

2. Zero-ML Infrastructure and 100% Client-Side Portability
We are completely swapping out heavy embedding models and vector databases for high-performance, deterministic string algorithms. Because the scope of a single OpenAPI specification is bounded (typically ranging from a few dozen to a few thousand endpoints), the search index can easily sit inside the application's RAM. It operates with zero operational server overhead, requires no GPUs, works offline, and runs instantly on standard client CPUs.

3. High-Precision Matching via Multi-Field Weighting (BM25F)
Standard keyword searches look at a document as a flat bag-of-words. We are achieving surgical search precision by implementing BM25F (Fielded BM25). This allows us to structurally weight the components of an API schema differently. For example, a keyword match inside an operationId (like getUserBilling) or an endpoint path is prioritized with a massive multiplier, while match hits inside chatty, verbose paragraph descriptions are heavily dampened.

4. Resiliency Against Typing and Phrasing Anomalies
Human prompts and even automated LLM thoughts are prone to spelling errors and phrasing differences. To ensure the discovery process never breaks down silently, we combine three parallel algorithms using Reciprocal Rank Fusion (RRF):

BM25F: Captures keyword density and structural field importance.

Token-Set Jaccard Distance: Measures unique token overlaps, normalizing variations in phrase lengths.

Levenshtein Distance: Compares character-level transformations via dynamic programming, ensuring that if a user types a typo (e.g., "usrs biling" instead of "users billing"), the engine automatically self-corrects and routes the model to the correct tool.

### Architecture

┌──────────────────────────────────────────────┐
                    │             User Prompt Query                │
                    │        (e.g., "Get usrs biling context")     │
                    └──────────────────────┬───────────────────────┘
                                           │
                               Tokenization & Preprocessing
                            (CamelCase, Snake_Case, API Filter)
                                           │
            ┌──────────────────────────────┼──────────────────────────────┐
            ▼                              ▼                              ▼
┌──────────────────────┐       ┌──────────────────────┐       ┌──────────────────────┐
│     1. Lexical       │       │     2. Set-Based     │       │  3. Fuzzy Character  │
│    Engine: BM25F     │       │   Engine: Jaccard    │       │ Engine: Levenshtein  │
│  Weighted Multi-Field│       │ Set Intersection/Union│       │ Character Edit Matrix│
└──────────┬───────────┘       └──────────┬───────────┘       └──────────┬───────────┘
           │                              │                              │
     Rank Position                  Rank Position                  Rank Position
     [r_bm25f(d)]                   [r_jaccard(d)]                 [r_lev(d)]
           │                              │                              │
           └──────────────────────────────┼──────────────────────────────┘
                                          │
                                          ▼
                        ┌──────────────────────────────────┐
                        │    Reciprocal Rank Fusion (RRF)  │
                        │      RRF(d) = Σ 1 / (60 + r(d))  │
                        └─────────────────┬────────────────┘
                                          │
                                          ▼
                                Top-K Endpoint Tools
                       Injected into LLM Session Modality

### The Three Operational Execution Lifecycles

#### A. Initialization Phase (Single Boot Execution)
When the pythonic MCP server instantiates, it parses the selected OpenAPI raw JSON schema exactly once. It iterates through the nesting maps (`/paths -> [verbs]`), identifies key functional configurations, and separates structural metadata into a clean database row in RAM. It passes names, parameter blocks, and text keys into the structural tokenizers to prime the local inverted indexes instantly.

#### B. Discovery Phase (`discover_tools` Routing)
When called, the system extracts the incoming natural language search phrase and forks it across three algorithmic runtime layers executing inside an isolated local evaluation sequence:
1.  **BM25F** weighs independent components of the schema (e.g., highly valuing hits within the structural `operationId` or `path` while heavily damping matches found in chatty `description` texts).
2.  **Token-Set Jaccard** determines strict structural word matching across the unique lexicon sets of the route, filtering away length distortions.
3.  **Levenshtein Distance Matrices** check character-level alignments against core fields (`operationId`, `path`), capturing typos and spelling variations instantly.

#### C. Fusion & Selection Phase
The raw scores are outputted to independent sorting arrays. Since their score profiles operate on entirely divergent baseline dimensions (unbounded positive decimals, zero-to-one fractions, and inverse distance weights), the engine overlays a **Reciprocal Rank Fusion (RRF)** evaluation step. This combines the ordinal array sequence indices directly, elevating endpoints that ranked consistently high across all three validation techniques to the top-k selection target array.

---

## 3. Micro-Engine Algorithmic Specifications

To fine-tune this platform for custom local codebases, the retrieval engine separates structural fields, tokens, and character distances explicitly using standard arithmetic formulas.

### 1. BM25F: Fielded In-Memory Lexical Indexing
Standard BM25 flattens API texts into a raw, un-fielded bag-of-words. In OpenAPI structures, this causes catastrophic failure: a random phrase matched in a verbose 4-paragraph description could mathematically overwhelm a critical, exact keyword hit inside an `operationId`. 

BM25F rectifies this by processing the frequency saturation parameter across isolated fields independently, applying precise engineering multipliers before computing the composite term score:

$$\text{Score}(D, Q) = \sum_{q \in Q} \frac{\text{IDF}(q) \cdot \tilde{f}(q, D)}{k_1 + \tilde{f}(q, D)}$$

Where the composite term frequency $\tilde{f}(q, D)$ across all weighted document fields is calculated as:

$$\tilde{f}(q, D) = \sum_{c \in \text{fields}} W_c \cdot \frac{f_c(q, D)}{1 + b_c \left( \frac{L_c}{L_{avg, c}} - 1 \right)}$$

#### Field Weight Configurations ($W_c$) and Length Normalization ($b_c$):
* **`operationId`** ($W_c = 4.0, b_c = 0.1$): The explicit, programmatic developer anchor (e.g., `getUserBillingHistory`). Highest signal density; structural length adjustments are strongly suppressed since these names are naturally short and concise.
* **`summary`** ($W_c = 3.5, b_c = 0.5$): Concentrated, human-curated definitions mapping intentionality. Balanced length normalization.
* **`tags`** ($W_c = 3.0, b_c = 0.2$): Categorical resource organizational groups (e.g., `Finance`, `Authentication`), providing broad domain routing.
* **`path`** ($W_c = 2.5, b_c = 0.3$): Evaluates the actual endpoint routing string itself, ensuring component matches surface structural paths accurately.
* **`parameter_names` & `schema_properties`** ($W_c = 2.0, b_c = 0.4$): Catches precise parameter nomenclature targets, directly aligning user intent searches to underlying structural requirements (e.g., matching a search for `routing_number`).
* **`description`** ($W_c = 1.0, b_c = 0.9$): Baseline weighting coupled with an aggressive length penalty. This prevents chatty, paragraph-length text descriptions from introducing statistical noise into the search space.

### 2. Dynamic Regex Tokenization Engine
To ensure proper string analysis without external tokenizing servers, all inputs pass through an explicit four-step regex normalization matrix:
1.  **CamelCase Splitting:** Uses positive lookbehinds and lookahead lookups (`(?<=[a-z])(?=[A-Z])`) to transform strings like `fetchSystemAlerts` into `["fetch", "system", "alerts"]`.
2.  **Snake_Case & Formatting Cleansing:** Translates all boundaries (`_`, `-`, `.`) and URI path framing operators (`/`, `{`, `}`) into blank whitespace.
3.  **Alphanumeric Extraction & Lowercasing:** Lowers the entire text block and drops punctuation symbols.
4.  **API Stop-Word & Protocol Filtering:** Truncates standard conversational text fillers along with routine structural API phrases (`http`, `https`, `api`, `v1`, `v2`, `json`) to prevent term-frequency calculations from distorting technical relevance.

### 3. Set-Based Jaccard Distance Tracking
To guarantee resilience against variations in length or phrasing across multi-parameter APIs, the system tracks token set ratios concurrently. Jaccard views the data purely as single intersections, completely flattening repeated word counts to insulate the query from length variations:

$$\text{Jaccard}(Q, D) = \frac{|Q \cap D|}{|Q \cup D|}$$

### 4. Levenshtein Distance Character-Fuzzy Logic
To catch human errors, spelling variants, or machine parsing mistakes, the engine calculates edit distances using dynamic programming. It builds a localized character transformation cost map between the user input string and high-precision targets (`operationId`, `path` text blocks):

$$\text{Similarity}_{\text{Lev}}(Q, D_{\text{field}}) = \frac{1}{1.0 + \text{Distance}_{\text{Lev}}(\text{Query}, \text{Field})}$$

If a user inputs a typo like `"usrs biling"`, this loop tracks the close character alignment to `"users billing"` automatically, correcting the error before tool orchestration fails.

### 5. Triple-Engine Reciprocal Rank Fusion (RRF)
To natively synthesize the unbounded metrics of BM25F, the 0-1 bounded fractions of Jaccard, and the inverse distances of Levenshtein without complex or lossy score normalization formulas, the architecture overlays a **Reciprocal Rank Fusion** algorithm:

$$RRF(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$

Where:
* $M$ represents the array of matching engines ($M = [\text{BM25F}, \text{Jaccard}, \text{Levenshtein}]$).
* $r_m(d)$ represents the absolute ordinal placement rank integer (1, 2, 3...) of an API route record $d$ inside the specific model $m$'s sorted array.
* $k$ is a constant smoothing hyperparameter (standardized here at `60`) configured to ensure low-ranking outliers don't excessively penalize fields that performed extraordinarily well in an alternate category.

---

## 4. MCP Interface & Tool Contracts

The MCP implementation acts as a strict, stateless middleware server. It handles runtime parameters cleanly via structured JSON schema payloads.

### Tool 1: `discover_tools`
* **Purpose:** Evaluates the internal lexical database to find relevant OpenAPI path configurations matching a human intent.
* **Input Schema Argument:**
    ```json
    {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "The natural language instruction detailing the targeted action (e.g. 'Pull account profile summary')"
        }
      },
      "required": ["query"]
    }
    ```
* **Response Payload Structure:** Returns valid JSON-Schema configurations tailored to the discovered endpoints, mimicking native MCP tool schema requirements.
    ```json
    {
      "tools": [
        {
          "name": "get_v1_users_id_billing",
          "description": "[TAGS: Finance] Summary: Fetch invoicing statements. Path: /v1/users/{id}/billing",
          "inputSchema": {
            "type": "object",
            "properties": {
              "id": { "type": "string", "description": "The unique account identifier mapped from path parameters" },
              "limit": { "type": "integer", "description": "Pagination control length limit window" }
            },
            "required": ["id"]
          }
        }
      ]
    }
    ```

### Tool 2: `execute_tool`
* **Purpose:** Facilitates targeted HTTP/REST execution on behalf of the LLM against the client system.
* **Input Schema Argument:**
    ```json
    {
      "type": "object",
      "properties": {
        "path": { "type": "string", "description": "The exact structural endpoint path syntax matching the selected schema definition" },
        "method": { "type": "string", "description": "Uppercase target HTTP verb protocol: GET, POST, PUT, DELETE" },
        "arguments": { "type": "object", "description": "Key-value JSON tracking object parameters for body payload, query query-string parameters, or path substitutions" }
      },
      "required": ["path", "method"]
    }
    ```
* **Response Payload Structure:** Returns a standard wrapped JSON text response indicating execution status alongside raw payload arrays.
    ```json
    {
      "status": 200,
      "contentType": "application/json",
      "content": "{\"invoices\":[{\"id\":\"INV-982\",\"amount\":1500.00,\"status\":\"paid\"}]}"
    }
    ```

---
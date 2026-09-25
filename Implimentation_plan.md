# PRD — IntelliFile: Intelligent Local File Search & Recommendation System

**Version:** 2.1
**Platform:** Windows + macOS (both are real shipping/grading targets, not dev-only)
**Architecture:** Local-first / Offline-first
**Primary users:** Students, developers, researchers, knowledge workers

## Product Vision

Make every file on a user's computer discoverable by meaning, not just by filename.

IntelliFile is a lightweight Windows and macOS desktop application that continuously indexes the content of user-selected files and provides hybrid keyword + semantic search.

Instead of requiring users to remember *"What was that file called?"*, they can describe what they're looking for:

> "Find the notes where I studied handling traffic spikes using load balancing."

IntelliFile identifies the most relevant files, shows the matching content, explains why each result is relevant, and lets the user open the actual file directly.

## Problem

Traditional filesystem search is optimized primarily for lexical matching:

```
Query → Filename / indexed text / metadata → Results
```

But humans often remember concepts rather than filenames.

For example:

- Query: *"How can I handle sudden increases in traffic?"*
- Relevant file: `Distributed Systems.pdf`, which might contain:

  > "Horizontal scaling allows additional instances to be provisioned when system demand increases."

The exact words don't match, but the concepts do.

## Product Solution

IntelliFile builds a local search index:

```
Local Filesystem
       │
       ▼
  File Watcher
       │
       ▼
  File Indexer
       │
   ┌───┴────┐
   ▼        ▼
Keyword    Vector
 Index      Index
 (BM25)  (Embeddings)
   │        │
   └───┬────┘
       ▼
      RRF
       │
       ▼
 Optional Reranker
       │
       ▼
Ranked File Results
```

Everything runs locally by default.

## Design Principles

The product should follow six principles.

### 1. Local-first
User files remain on the user's machine.

### 2. Zero infrastructure
No PostgreSQL server, Docker, Redis, or cloud database should be required for the desktop application.

### 3. Resource-aware
The application should behave like a desktop utility, not a continuously running AI workload.

### 4. Incremental
Only new or modified files should be processed.

### 5. Explainable
The user should see why a file was returned.

### 6. Search-first
The LLM is not the core of the system. The core is **Information Retrieval**:

- Indexing
- Ranking

## Target User

Primary user: a Windows or macOS user with hundreds/thousands of technical documents who frequently remembers the content of a file but not its filename.

Examples:

- Students
- Software developers
- Researchers
- Engineers
- Interview candidates
- Writers

## Core User Experience

User presses **Ctrl + Space**. A search overlay appears:

```
┌──────────────────────────────────────────────┐
│ 🔍 Search your files...                       │
└──────────────────────────────────────────────┘
```

User types: `notes about Kubernetes autoscaling`

Results:

```
┌──────────────────────────────────────────────┐
│ Kubernetes Interview.pdf                  94% │
│ Documents/Interview/Kubernetes.pdf            │
│                                                │
│ "...Horizontal Pod Autoscaler automatically   │
│  adjusts the number of pods based on CPU..."  │
│                                                │
│ Why: Kubernetes + autoscaling + HPA           │
└──────────────────────────────────────────────┘
```

Actions:

| Key | Action |
|---|---|
| Enter | Open |
| Ctrl + Enter | Show in Explorer |
| ↑ / ↓ | Navigate |

## Supported File Types

**MVP**

| Format | Support |
|---|---|
| PDF | ✅ |
| DOCX | ✅ |
| TXT | ✅ |
| Markdown | ✅ |

**V2**

| Format | Support |
|---|---|
| PPTX | 🔜 |
| XLSX | 🔜 |
| CSV | 🔜 |
| HTML | 🔜 |
| Source code | 🔜 |

**Future** (explicitly outside the MVP)

- Images → OCR
- Scanned PDFs → OCR
- Audio → transcription
- Video → transcription

## Desktop Architecture

This is where I agree strongly with the database pivot: we should not require PostgreSQL.

Instead:

```
IntelliFile.exe
       │
   ┌───┴────┐
   ▼        ▼
Desktop UI  Local Backend
               │
         ┌─────┴──────┐
         ▼            ▼
      Search       Indexer
         │            │
         └─────┬──────┘
               ▼
          Embedded DB
```

The database should be embedded inside the application.

## Embedded Database Decision

For the MVP, I'd choose **LanceDB**, because the application is fundamentally a local AI/search application and we want convenient vector storage without operating a separate database server.

Architecture:

```
IntelliFile Data/
├── database/
├── vectors/
├── indexes/
└── metadata/
```

No database installation. No server. No Docker. No port configuration. The user simply installs `IntelliFile.exe` and it works.

### Alternatives

We should keep these in consideration:

- **SQLite + sqlite-vec** — excellent if we want maximum simplicity and relational metadata.
- **DuckDB** — excellent for analytical workloads.
- **LanceDB** — excellent fit for local vector/document workloads.

For our first implementation, I'd prototype with LanceDB, while keeping the storage abstraction clean enough that we can benchmark SQLite/sqlite-vec later.

## Local AI Architecture

We don't want:

```
PDF → Internet → Embedding API
```

Instead:

```
PDF → Local parser → Local embedding model → Local vector index
```

## Embedding Model

The MVP should use a lightweight embedding model. Candidate: `all-MiniLM-L6-v2`, with an optimized inference format.

Preferred execution pipeline:

```
Embedding Model
      ↓
     ONNX
      ↓
 ONNX Runtime
      ↓
┌───────────┬───────────┐
▼           ▼           ▼
DirectML   CoreML   CPU fallback
(Windows)  (macOS)  (everywhere)
```

This gives us an opportunity to use available GPU/NPU acceleration on either platform while keeping the architecture portable. ONNX Runtime reports which execution providers are actually available at runtime, so the app picks the best one without any OS-specific branching in application code.

## Resource-Aware Inference

The application should detect available hardware (CPU, RAM, GPU) and choose an appropriate execution strategy.

Conceptually:

```
              Hardware Detection
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  Windows + GPU   macOS (Apple    CPU only
        │         Silicon/Intel)      │
        ▼            │                ▼
    DirectML          ▼          Optimized CPU
                    CoreML
```

We should never assume every machine has a discrete GPU (Windows) or that CoreML acceleration is available on every Mac — both paths fall back to CPU cleanly.

## Resource Usage Modes

Provide three modes:

### Balanced (Default)
- Moderate indexing
- Moderate concurrency
- Battery-aware

### Performance
- Faster indexing
- More CPU/GPU usage

### Battery Saver
- Minimal background processing

## Battery Awareness

This should be a first-class feature.

```
Battery unplugged → Pause background indexing
AC power restored → Resume indexing
```

The user should be able to configure:

- ☑ Pause indexing on battery
- ☑ Pause when Battery Saver is active

This prevents IntelliFile from becoming the application that kills someone's battery.

## Indexing Architecture

Initial scan:

```
Selected folders
       ↓
 File discovery
       ↓
   Job queue
       ↓
  Worker pool
       ↓
      Parse
       ↓
      Chunk
       ↓
      Embed
       ↓
      Store
```

The UI should remain responsive throughout.

## Indexing Queue

Files should enter a queue:

```
   File Watcher
        │
        ▼
      Queue
        │
 ┌──────┼──────┐
 ▼      ▼      ▼
Worker 1 Worker 2 Worker 3
 │      │      │
 ▼      ▼      ▼
Parse  Parse  Parse
```

The number of workers should be configurable based on system resources.

## File Watcher

The application continuously watches indexed directories.

Events: `CREATE`, `MODIFY`, `DELETE`, `RENAME`

Example:

```
New file → Watcher → Queue → Indexer
```

## File Modification Debouncing

This is important.

Suppose a user opens `notes.docx` and hits Ctrl+S four times in a row. We don't want:

```
Save 1 → Embed
Save 2 → Embed
Save 3 → Embed
Save 4 → Embed
```

Instead:

```
Modification detected
        ↓
  Wait 30 seconds
        ↓
Any further modification? ──Yes──▶ Reset timer
        │
        No
        ↓
No modification for 30 sec
        ↓
    Process file
```

This dramatically reduces unnecessary work. The debounce duration should eventually be configurable.

## File Identity

Each file should have: `file_id`, `path`, `size`, `modified_time`, `hash`.

The hash allows us to detect whether the actual content changed.

Example:

```
File modified timestamp changed
              ↓
        Compare hash
        /          \
     Same        Different
       │              │
     Skip        Re-index
```

This prevents unnecessary embedding.

## Text Extraction

Pipeline:

```
        File
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
  PDF    DOCX    TXT
   │      │      │
   └──────┼──────┘
          ▼
  Normalized Text
```

For PDF, retain `page_number`.

For DOCX, retain `paragraph`, `heading`, `section` where possible.

## Chunking

We'll use a sliding-window strategy.

Initial target: **512 tokens** with **50-token overlap**.

Conceptually:

```
Chunk 1: [--------------------------------]
         0                              512

Chunk 2:      [--------------------------------]
              462                             974
```

This overlap reduces the risk of splitting related information across chunk boundaries.

However, 512/50 is a starting point, not a sacred value. We should evaluate different chunk sizes against Recall@5/MRR.

## Chunk Metadata

Every chunk stores: `chunk_id`, `file_id`, `content`, `page_number`, `chunk_index`, `start_offset`, `end_offset`.

Potentially `heading` and `section` as well.

## Vector Index

Each chunk receives an embedding:

```
Chunk → Embedding model → Vector → LanceDB
```

Example:

```json
{
  "chunk_id": 381,
  "file_id": 42,
  "vector": [0.13, -0.27, "..."]
}
```

## Keyword Index

We simultaneously maintain a lexical index for BM25.

```
        File
          │
          ▼
         Text
          │
   ┌──────┴──────┐
   ▼             ▼
  BM25       Embedding
   │             │
   ▼             ▼
Keyword Index  Vector Index
```

This is crucial because semantic search isn't always superior. For a query like `K8s HPA`, exact lexical matching can be extremely valuable.

## Search Pipeline

When a user searches `handling traffic spikes`, we perform:

```
                Query
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
      BM25               Semantic
        │                   │
        ▼                   ▼
     Top 20               Top 20
        │                   │
        └─────────┬─────────┘
                   ▼
                  RRF
                   │
                   ▼
          Top candidates
                   │
                   ▼
         Optional reranker
                   │
                   ▼
              Top files
```

## Exact Match Override

This is an excellent UX feature.

If the user enters `"load balancing"`, the quotation marks explicitly indicate an exact phrase search.

Query parser:

```
"load balancing" → Exact-match mode → BM25 heavily prioritized
```

Potential behavior:

- **Exact phrase:** BM25 = primary, semantic = optional/supporting. For a completely exact query, we can bypass semantic retrieval altogether.
- **Normal query** (`load balancing`, no quotes): BM25 + Semantic + RRF.

This gives users control over search behavior without requiring a settings menu.

## Metadata Search

The search engine should understand metadata constraints.

Example: `PDFs about Kubernetes modified last week`

Parse into:

- Content: Kubernetes
- Type: PDF
- Modified: Last week

Then:

```
Metadata filtering → Candidate set → Hybrid retrieval
```

This can dramatically reduce unnecessary vector searches.

## Result Fusion

Different retrieval signals rank the same files differently:

| File | BM25 rank | Semantic rank |
|---|---|---|
| Kubernetes.pdf | #1 | #2 |
| DevOps.pdf | #3 | — |
| Cloud.pdf | #8 | #1 |
| Autoscaling.pdf | — | #4 |

RRF produces a unified ranking. The system should preserve the individual retrieval signals for debugging/evaluation.

## Reranker

Reranking is optional.

Default MVP: BM25 + Vector + RRF. No cross-encoder.

Advanced setting: "Enable high-accuracy reranking"

If enabled:

```
Top 5 candidates → Cross encoder → Final ranking
```

I agree with limiting this to 5 rather than 20 initially. This keeps latency/resource consumption reasonable.

## Search Result Object

Internally:

```json
{
  "file_id": 42,
  "path": "C:\\Documents\\SystemDesign.pdf",
  "filename": "SystemDesign.pdf",
  "score": 0.94,
  "keyword_score": 0.81,
  "semantic_score": 0.92,
  "reranker_score": null,
  "page": 17,
  "matched_chunk": "Horizontal scaling allows..."
}
```

This is useful both for the UI and for evaluation.

## Snippet Preview

This should absolutely be part of the MVP.

Instead of:

```
System Design.pdf   Page 17
```

show:

```
System Design.pdf
"...horizontal scaling allows additional instances to handle increased traffic..."
```

The snippet comes from the chunk that produced the result.

## Keyword Highlighting

For BM25/exact matches, highlight the matched phrase, e.g.:

> "**horizontal scaling** allows additional instances to handle increased traffic"

For pure semantic matches, we don't necessarily have an exact keyword to highlight. In that case, show the most relevant sentence(s).

## File-Level Result Aggregation

Search operates on chunks. The user sees files. Therefore:

```
Chunk 1 → 0.92
Chunk 2 → 0.51
Chunk 3 → 0.87
```

must become:

```
SystemDesign.pdf → 0.94
```

The aggregation strategy should be experimentally evaluated.

Possible strategy: top chunk score + secondary chunk contribution, rather than simply averaging all chunks — one highly relevant section shouldn't be diluted by hundreds of irrelevant chunks.

## Why This File?

Every result should provide an explanation. Example:

> **Why this file?**
> Strong match because this document discusses:
> - Load balancing
> - Horizontal scaling
> - Traffic spikes
> - Distributed systems

For MVP, this can be derived from the retrieved snippet/metadata. We don't need an LLM to generate explanations.

## Related Files — V2

If the user opens `Kubernetes.pdf`, show related files:

- Docker.pdf
- Cloud Architecture.pdf
- System Design.pdf
- DevOps Interview.pdf

These can initially be generated using vector similarity between documents.

## Deleted File Handling

When a file disappears:

```
File deleted → Watcher → Mark file = DELETED
```

The UI immediately removes it from active search results. But vectors don't necessarily need to be physically removed immediately.

Instead: tombstone (`deleted = true`), then:

```
Weekly cleanup → Remove tombstoned vectors → Compact/rebuild index
```

This reduces unnecessary disk I/O during normal usage.

One refinement: we should make the cleanup cadence configurable and trigger it based on tombstone volume as well, rather than blindly rebuilding every week.

## Rename Handling

If `Kubernetes.pdf` becomes `Kubernetes_Interview.pdf`, the system should update metadata without unnecessarily regenerating embeddings if the file content hasn't changed.

```
Rename → Path changed → Content hash unchanged → Update metadata only
```

Excellent optimization.

## Duplicate Files

Use content hashes. If `notes.pdf` and `notes_copy.pdf` have identical hashes (same content), the system can reuse the same indexed representation.

This saves storage and embedding computation.

## Search Context

If the user is searching from a particular folder (e.g. `Documents/Projects/`), we can optionally boost files from that location.

```
Global result score + Current-folder boost
```

But this should be optional so users don't accidentally miss highly relevant files elsewhere.

## Personalization — V2

Record lightweight interaction signals:

```
Search → Result clicked → File opened → File bookmarked
```

Then eventually:

```
Final score = semantic + keyword + reranker + metadata + recency + user preference
```

Personalization should not be part of the initial ranking model. We first need a strong retrieval baseline.

## Privacy Architecture

Default:

```
                    USER MACHINE
                         │
                       Files
                         │
                         ▼
                       Parser
                         │
                         ▼
                      Chunker
                         │
                ┌────────┴────────┐
                ▼                 ▼
              BM25            Embedding
                │                 │
                ▼                 ▼
          Local index      Local vector DB
```

No file content needs to leave the machine.

## Offline Requirement

Core functionality must work without an internet connection:

| Capability | Offline |
|---|---|
| File indexing | ✅ |
| Keyword search | ✅ |
| Semantic search | ✅ |
| Opening files | ✅ |
| Metadata filtering | ✅ |

Internet should not be required.

## Application Storage

Platform-appropriate app data directory:

- Windows: `%LOCALAPPDATA%\IntelliFile\`
- macOS: `~/Library/Application Support/IntelliFile/`

Structure:

```
IntelliFile/
├── database/
├── vector_index/
├── keyword_index/
├── cache/
├── logs/
└── config/
```

The original files remain wherever the user stored them.

## UI Architecture

I'd use Tauri + React + TypeScript.

Why Tauri?

- Native desktop application
- Lightweight
- Good filesystem integration
- React can be reused
- Smaller footprint than Electron

The backend/search engine can be packaged with the application.

## Backend Architecture

Instead of requiring a separate server installation:

```
   Tauri
     │
     ▼
Local application backend
```

The local backend owns:

- File manager
- Indexer
- Search engine
- Metadata manager
- Recommendation engine

Communication can be through local IPC/API depending on the final implementation.

## MVP Screens

### Screen 1 — Onboarding

```
Welcome to IntelliFile

Select folders to index

[ Add Folder ]

☑ Documents
☑ Downloads

        [ Start Indexing ]
```

### Screen 2 — Search

```
🔍 Search your files...
```

### Screen 3 — Index Status

```
Indexed files: 8,245
Processing: 3
Failed: 2

CPU usage: Low
Index size: 1.8 GB
```

### Screen 4 — Settings

```
Indexing
☑ Pause on battery
☑ Pause on Battery Saver

Embedding
○ Balanced
○ Performance
○ Battery Saver

Reranking
☐ Enable high-accuracy reranking
```

## Search Modes

The UI can support three modes without overwhelming the user.

### Smart Search
Default: BM25 + Semantic + RRF

### Exact Search
Automatically triggered by `"exact phrase"`

### Keyword Search
Optional explicit mode. Useful when users know exactly what they're looking for.

## Index Status

Users need to know what's happening. Example:

```
Indexing your files...

██████████████░░░░ 78%

7,820 / 10,000 files

Current: Kubernetes_Notes.pdf

Estimated remaining: 12 minutes
```

And importantly: you can continue using your computer while indexing.

## Performance Requirements

**Search latency** — target P50 < 300ms, P95 < 1 second for a reasonable local index. We'll measure this rather than assuming it.

**Indexing** — should be asynchronous.

**UI** — search UI must remain responsive during indexing.

**Memory** — embedding and reranking workloads should be bounded.

## Power Requirements

When battery = active, default behavior is to pause indexing. When Battery Saver = active, pause as well.

Search itself should remain available.

## Reliability

The application should survive:

- Application crashes
- PC shutdown
- File changes during indexing
- Files being moved
- Files being deleted
- Corrupt files
- Unsupported files

Indexing jobs should be recoverable.

## Crash Recovery

On startup:

```
Load index → Check incomplete jobs → Resume
```

We shouldn't restart the entire initial scan.

## Security

The application must:

- Respect OS filesystem permissions (Windows ACLs, macOS POSIX permissions/sandbox entitlements)
- Never bypass access controls
- Never execute file contents
- Restrict local API/IPC access
- Validate filesystem paths
- Protect against path traversal
- Treat documents as untrusted input

## Evaluation Framework

This is one of the strongest parts of the project. We'll build a benchmark:

```
Dataset
├── Files
└── Queries
```

Example:

- Query: *"How does horizontal scaling handle traffic?"*
- Relevant: `SystemDesign.pdf`, `DistributedSystems.pdf`, `Kubernetes.pdf`

## Retrieval Experiments

Compare:

1. Baseline: BM25
2. Experiment 2: Vector search
3. Experiment 3: BM25 + Vector + RRF
4. Experiment 4: BM25 + Vector + RRF + Reranker

## Metrics

Measure:

- **Recall@5** — did we retrieve a relevant file in the top 5?
- **Recall@10** — did we retrieve a relevant file in the top 10?
- **MRR** — how high was the first relevant result?
- **NDCG@5** — how good was the ranking of relevant results?
- **Latency** — P50, P95
- **Resource consumption** — CPU, RAM, GPU
- **Battery impact**

## Chunking Experiments

We should test:

- 256 tokens / 32 overlap
- 512 tokens / 50 overlap
- 768 tokens / 75 overlap

and determine which configuration gives the best retrieval quality/resource tradeoff. This turns chunking into an experiment, not an arbitrary parameter.

## Embedding Experiments

Compare lightweight models based on: recall, latency, memory, disk, CPU/GPU usage.

The final model should be selected using actual measurements on representative Windows and macOS hardware.

## Acceptance Criteria

MVP is considered successful if:

### Functionality
- User can select folders.
- Files are discovered recursively.
- PDF/DOCX/TXT/MD are indexed.
- New files are detected.
- Modified files are reindexed.
- Deleted files disappear from search.
- Renamed files don't trigger unnecessary re-embedding.
- Search results open the original file.

### Search
- Keyword search works.
- Semantic search works.
- Hybrid retrieval works.
- Exact phrase search is supported.
- Metadata filters work.
- Snippets are shown.

### Performance
- Search remains responsive.
- Background indexing doesn't freeze UI.
- Indexing pauses on battery by default.
- File modifications are debounced.

### Privacy
- Core search works offline.
- No external service is required.

## MVP vs Future

| Feature | MVP | V2 | V3 |
|---|---|---|---|
| PDF | ✅ | | |
| DOCX | ✅ | | |
| TXT/MD | ✅ | | |
| Semantic search | ✅ | | |
| BM25 | ✅ | | |
| RRF | ✅ | | |
| Snippets | ✅ | | |
| Exact match | ✅ | | |
| File watcher | ✅ | | |
| Debouncing | ✅ | | |
| Battery awareness | ✅ | | |
| Tombstones | ✅ | | |
| Cross-encoder | Optional | ✅ | |
| Related files | | ✅ | |
| Search history | | ✅ | |
| Feedback | | ✅ | |
| Personalization | | | ✅ |
| OCR | | | ✅ |
| Image search | | | ✅ |
| Code search | | | ✅ |
| Audio/video | | | ✅ |

## Final Architecture

The architecture I'd settle on now is:

```
                  WINDOWS / macOS
                         │
                         ▼
              ┌────────────────────┐
              │     IntelliFile     │
              │   Tauri + React     │
              └──────────┬──────────┘
                         │
                         ▼
              ┌────────────────────┐
              │   Local Backend     │
              └──────────┬──────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  File Watcher    Search Engine       Metadata
        │                │
        ▼                │
    Debounce             │
        │                │
        ▼                │
    Job Queue            │
        │                │
        ▼                │
    Parsers              │
        │                │
        ▼                │
    Chunking             │
        │                │
    ┌───┴────┐            │
    ▼        ▼            │
  BM25   Embedding        │
    │        │            │
    │        ▼            │
    │   ONNX Runtime      │
    │        │   
    │ DirectML / CoreML   │
    │        │            │
    │        ▼            │
    │    LanceDB          │
    │        │            │
    └───┬────┘            │
        ▼                 │
       RRF                │
        │                 │
        ▼                 │
  Optional Reranker       │
        │                 │
        ▼                 │
  File Aggregation        │
        │                 │
        ▼                 │
  Recommendations         │
        │                 │
        └────────┬────────┘
                 ▼
             Search UI
                 │
        ┌────────┴────────┐
        ▼                 ▼
    Open File      Show in Explorer
```

## Recommended Technology Stack

| Layer | Technology |
|---|---|
| Desktop | Tauri |
| UI | React + TypeScript |
| Backend | Python |
| API/IPC | Local API / IPC |
| Parsing | Python document parsers |
| Chunking | Custom tokenizer-aware chunker |
| Embeddings | Lightweight local embedding model |
| Model runtime | ONNX Runtime |
| Hardware acceleration | DirectML (Windows) / CoreML (macOS), CPU fallback everywhere |
| Vector DB | LanceDB |
| Keyword retrieval | BM25 |
| Fusion | RRF |
| Reranking | Lightweight cross-encoder, optional |
| File monitoring | `watchdog` — ReadDirectoryChangesW (Windows) / FSEvents (macOS) |
| Storage | Local application directory (`%LOCALAPPDATA%` / `~/Library/Application Support`) |
| Packaging | Windows installer (.msi/NSIS) + macOS `.app`/`.dmg`, both via Tauri |

## What Makes This Project Technically Strong

The final project isn't just React + LLM + Vector DB. It covers:

**Operating Systems**
- Filesystem events
- File permissions
- Windows + macOS integration

**Distributed-style processing**
- Job queues
- Workers
- Incremental processing

**Information Retrieval**
- BM25
- Vector retrieval
- RRF
- Reranking

**Machine Learning**
- Embeddings
- ONNX
- DirectML

**Database Engineering**
- Embedded DB
- Indexing
- Tombstones
- Compaction

**Product Engineering**
- Desktop UX
- Battery management
- Privacy
- Performance

This version is much stronger than the original PRD. The biggest improvement is that we're designing around the constraints of a real Windows or Mac laptop: zero setup, local execution, bounded resource usage, incremental indexing, battery awareness, and fast retrieval.

And I would make one architectural rule from day one: **keep the search/indexing layer independent from the UI and database implementation.** That way, if experiments show SQLite/sqlite-vec beats LanceDB for our workload, we can switch without rewriting the entire application.

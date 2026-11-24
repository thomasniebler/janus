# Knowledge graph and wiki

**Status:** Shipped (all phases, JAN-240 – JAN-244)
**Linear:** JAN-239 (phases: JAN-240 – JAN-244)
**Depends on:** Data model & migrations tech spec, background workers runtime tech spec (durable trigger worker), structured topic tracking, LLM provider configuration, `sqlite-vec` (phase 1)

## Problem

Janus's knowledge is a flat list. `AgentMemoryFact` rows are standalone
summaries found by substring match (`MemoryService.list_facts` uses `ILIKE`),
tracked topics hold a one-paragraph `summary` plus evidence rows, and nothing
links a fact to the people, projects, or threads it is about. There are no
embeddings anywhere in the backend, so recall only works when the query shares
words with the stored text. Email attachments are dropped during ingestion
(`imap_service._extract_body` skips `attachment` parts), so documents never
reach the agent at all.

## Goal

A per-user knowledge layer that:

1. ingests **every** Janus source into one searchable store,
2. extracts entities and relations into a graph,
3. maintains an LLM-written, human-editable **wiki** where every tracked topic
   and every significant entity has a page,
4. answers agent queries with hybrid retrieval (vector + keyword + graph
   neighbourhood), always citing the source chunks.

## Non-goals

- A separate graph database (Neo4j, FalkorDB, Apache AGE). Graph traversal uses
  plain relational tables and recursive CTEs.
- Adopting an external memory framework (Hindsight, LightRAG, Graphiti, Mem0,
  Cognee) as the storage layer. They each own their storage, which conflicts
  with Janus's per-user scoping, Fernet encryption, deletion, and tool policy.
  LightRAG's hybrid-retrieval design is the main reference for phase 3.
- Replacing the durable trigger worker; ingestion runs as trigger runs.

## Design

### Database backend

SQLite stays the production backend. The production host (Uberspace) runs
PostgreSQL as a per-user instance on shared binaries, where extensions such as
`pgvector` cannot be installed with `CREATE EXTENSION`, so a PostgreSQL move
would buy little today. Vector search therefore targets SQLite first, through a
small `VectorIndex` adapter so a PostgreSQL implementation can be added later
without touching the services:

| Backend | Vector search | Keyword search | Status |
|---|---|---|---|
| SQLite | `sqlite-vec` (loaded per connection; `vec0` once chunk counts need it) | FTS5 where compiled in, else substring (`ILIKE`) — production has no FTS5 | Built first, production |
| PostgreSQL | `pgvector` column + HNSW index | `tsvector` / `pg_trgm` | Later, only if production moves |

Services only talk to the adapter, so no service code branches on dialect.
PostgreSQL stays a supported backend for everything else (phase 0 verifies the
migration chain in CI), but the knowledge features need not reach feature parity
there until a deployment actually needs it.

### Data model (new tables, all `user_id`-scoped)

- **`knowledge_sources`** — one row per ingested item: `source_type`
  (`email`, `email_attachment`, `whatsapp_message`, `conversation_message`,
  `calendar_event`, `planner_task`, `memory_fact`, `upload`), `source_id`,
  title, mime type, content hash (dedupe/re-ingest), ingestion status, timestamps.
- **`knowledge_chunks`** — ordered text chunks of a source with token count and
  an embedding (dimension fixed per configured embedding model; model name
  stored per row so a model change triggers re-embedding).
- **`knowledge_entities`** — canonical name, type (`person`, `organization`,
  `project`, `place`, `product`, `concept`), aliases, description, embedding.
  Entities are deduplicated by alias match plus embedding similarity, with
  uncertain merges queued for review (reusing the `TopicSuggestion` pattern).
- **`knowledge_relations`** — `(subject_entity, predicate, object_entity)` with
  confidence, validity interval (`valid_from` / `valid_to`, so outdated
  relations are ended rather than deleted), and supporting chunk ids.
- **`knowledge_mentions`** — chunk ↔ entity links (the provenance edges).
- **`wiki_pages`** — slug, title, markdown body, `entity_id` or `topic_id`,
  `last_synthesized_at`, `user_edited` flag, embedding. Page revisions are kept
  in **`wiki_page_revisions`** so LLM rewrites never destroy user edits.

### Existing concepts

- **Tracked topics become wiki pages.** Each `TrackedTopic` gets a
  `wiki_pages` row; the topic keeps its lifecycle fields (status, pinned,
  snooze, briefing bookkeeping) and its `summary` becomes the page lead.
  `TopicEvidence` stays and is complemented by chunk citations.
- **Memory facts attach to entities.** `AgentMemoryFact` gains optional
  entity links and an embedding; facts remain the "short, confirmed
  statement" layer and are also ingested as sources.

### Pipeline

1. **Parse** — plain text sources pass through; attachments and uploads go
   through `markitdown` (docling is the fallback if PDF/table quality is
   insufficient). IMAP ingestion stores attachments instead of discarding them.
2. **Chunk + embed** — structure-aware chunking (headings, mail quoting,
   message boundaries); embeddings through a LangChain `Embeddings` provider
   configured next to the chat LLM (OpenAI, Ollama, Google).
3. **Extract** — an LLM pass per chunk returns entities, relations, and
   candidate facts as structured JSON (reusing the `llm_service` extractor
   pattern). Low-confidence output is dropped or queued, not written.
4. **Synthesize wiki** — affected pages are re-synthesized incrementally from
   their entity neighbourhood and top chunks, with inline citations. Pages
   marked `user_edited` get a proposed revision instead of an overwrite.

Steps 1–2 run on every new source. Steps 3–4 run as a built-in trigger
("Knowledge ingestion") in batches, so cost and rate limits stay bounded and
runs are visible and retryable like any other trigger run. A backfill command
(`janus knowledge backfill`) ingests historical data.

### Retrieval

`knowledge.search(query)` combines, with reciprocal-rank fusion:

1. vector similarity over chunks, entities, and wiki pages,
2. keyword search over chunks,
3. graph expansion: entities matched by 1–2 are expanded one or two hops and
   their top chunks and pages are added.

Results always carry source references. New read-only agent tools:
`knowledge.search`, `wiki.read`, `wiki.list`, `entity.get`. A write tool
`wiki.propose_edit` is `confirmation="required"`, so unattended runs cannot
rewrite pages silently.

### UI

The **Memory & Knowledge** page gains a **Wiki** tab (page list, markdown view
with citations, edit, revision history) and an entity view with its relations.
The existing Topics tab links to each topic's wiki page.

## Phases

Each phase ships on its own and gets its own Linear issue.

0. **PostgreSQL as a first-class backend** — Postgres service in the CI
   `migration-tests` job, migration chain fixed for PostgreSQL (duplicate and
   unwidened native enum types, a 63+ character identifier, a forced table
   rebuild), `janus db copy` data-migration command, updated `database.md`
   and deployment docs. Running the whole test suite on PostgreSQL and fixing
   pre-existing model/migration drift are follow-ups.
1. **Embeddings and semantic memory** (shipped with JAN-241; see "Phase 1
   design" below) — embedding provider config,
   `VectorIndex` adapter with the `sqlite-vec` implementation (plus a
   keyword fallback, FTS5 or substring, and a startup check that extension loading works),
   embeddings for memory facts and topics, semantic
   `list_facts`/`search_topics`.
2. **Sources, parsing, chunks** (shipped with JAN-242; see "Phase 2 design" below) — `knowledge_sources`/`knowledge_chunks`,
   attachment storage in IMAP ingestion, markitdown parsing, file upload
   endpoint, ingestion of all source types, backfill command.
3. **Graph** (shipped with JAN-243 in two parts; see "Phase 3 design" below) — entity/relation extraction trigger, entity dedupe and review
   queue, hybrid retrieval, `knowledge.search` and `entity.get` tools.
4. **Wiki** (shipped with JAN-244; see "Phase 4 design" below) — `wiki_pages` + revisions, topic → page migration, incremental
   synthesis, wiki tools, Wiki tab in all three UI shells.

## Phase 1 design (JAN-241)

Phase 1 adds semantic search over memory facts and tracked topics only. It
has no sources, chunks or graph yet. It was scoped with the owner on
2026-09-28:

- **Embedding provider.** `Settings` gains `embedding_provider` (`ollama`,
  `openai`, `gemini`, or `NULL`), `embedding_model`, and
  `embedding_min_similarity` (default `0.5`). The provider reuses that chat
  provider's stored, Fernet-encrypted API key and base URL; there is no second
  key. `NULL` (the default) disables embeddings, so nothing is sent anywhere
  until an admin picks a model. Suggested models: `nomic-embed-text` (Ollama),
  `text-embedding-3-small` (OpenAI), `gemini-embedding-001` (Google). Choosing
  a cloud provider sends fact and topic text to it.
- **Vector storage.** One dialect-neutral table, `knowledge_embeddings`
  (`user_id`, `owner_type` `fact`/`topic`, `owner_id`, `model` as
  `provider/model`, `dimensions`, `content_hash`, `source_updated_at`, float32
  `vector` blob), unique per `(owner_type, owner_id)`. No dimension is fixed in
  the schema. Only rows whose `model` matches the configured model are
  searched, so a model change makes every row stale and the sweep re-embeds
  it. The `vec0` virtual table is deferred until chunk counts justify it
  (phase 2 did not need it; see below).
- **`VectorIndex` adapter** (`services/vector_index.py`). The SQLite
  implementation loads `sqlite-vec` into each pooled connection on first use
  and ranks by `vec_distance_cosine()` over the user's rows: an exact
  brute-force search, which is fine for thousands of facts and topics. A
  pgvector implementation only has to satisfy the same `search()` contract.
  On PostgreSQL, or when the extension cannot load, the adapter reports itself
  unavailable and no embeddings are computed.
- **Keyword index.** The production Python's SQLite has no FTS5 (checked
  2026-09-28 with the deployment capability workflow). So the keyword ranking
  is FTS5 only where it exists and otherwise a substring (`ILIKE`) match of
  the query or any 3+ character word, run inside the same candidate filter
  and newest first (`knowledge_index.substring_filter`, JAN-245). Where FTS5
  exists: SQLite FTS5 tables `knowledge_fts_fact` and
  `knowledge_fts_topic` (rowid = owner id) are created and backfilled at
  runtime. An ORM flush hook keeps them in sync on every fact or topic
  insert, update and delete, whichever code path writes. They are left out of
  Alembic autogenerate. On PostgreSQL, keyword search stays the previous
  `ILIKE` substring match.
- **Search.** `MemoryService.list_facts(query=…)` and
  `TopicService.list_topics(query=…)`, which back the `search_facts` and
  `search_topics` tools and the API, fuse vector hits and FTS5 hits with
  reciprocal-rank fusion (`k = 60`). Without embeddings they use the keyword
  ranking alone (FTS5 or substring). A failing embedding call
  at query time falls back to keyword search and never fails the request.
- **Filter first, then rank (JAN-245).** Cosine similarity is treated as an
  ordering signal, never as proof of relevance. The scores are not comparable
  across models, and tiny differences carry no meaning.
  - The listing's metadata filters are passed to both indexes as a candidate
    `SELECT`, so similarity only ranks rows that already qualify. The filters
    are the owner, fact status or topic status, and the fact `source`
    (`user`/`agent`/`compaction`, on `search_facts` and
    `GET /api/memory?source=`).
  - At most `VECTOR_ONLY_LIMIT` (5) results per query may come from
    similarity alone, without a keyword hit.
  - `embedding_min_similarity` remains as an expert floor for those
    meaning-only matches. It sits under **Advanced** on the settings card and
    opens there automatically when a non-default value is stored.
  - Entity filters (people, projects, places) arrive with the phase 3 graph.
- **Embedding timing.** Creating or updating a fact or topic through the
  services embeds that one item inline after commit (best effort: a failure
  is logged and the item stays stale). A supervised `knowledge-index` worker
  thread (`knowledge_index_worker_enabled`, default on) sweeps missing and
  stale rows every 60 s in batches of 32 texts per `embed_documents` call.
  It covers every other write path and model changes. `janus knowledge
  reembed [--force]` runs the same sweep from the CLI.
- **Startup and status check.** At startup Janus probes whether Python's
  `sqlite3` allows `enable_load_extension`, whether `sqlite-vec` loads, and
  whether FTS5 is available. It logs a warning when one is missing. System
  status gets a **Knowledge search** service: degraded when an embedding
  model is configured but vector search is unavailable, operational (with the
  reason) otherwise.

## Phase 2 design (JAN-242)

Phase 2 ingests sources and turns them into embedded chunks. It adds no new
agent tools; retrieval over chunks (`knowledge.search`) is phase 3. Scoped
with the owner on 2026-09-28:

- **Tables.** `knowledge_sources` holds one row per ingested item: `user_id`,
  `source_type`, `owner_id` (the source row's id, or `NULL` for uploads),
  `part` (the attachment index, else `0`), title, MIME type, size,
  `content_hash`, `status` (`pending`/`ready`/`skipped`/`failed`),
  `status_detail`, `attempts`, `source_updated_at`, `processed_at`, and a
  nullable `staged_content` blob. `(user_id, source_type, owner_id, part)` is
  unique. `knowledge_chunks` holds `source_id` (cascade), `user_id`,
  `ordinal`, `text`, `content_hash`, `char_count` and `token_estimate`
  (characters / 4). Chunk vectors reuse `knowledge_embeddings` with
  `owner_type = "chunk"`.
- **Source types.**
  - `email`: subject, sender, date and body. Mail filed SPAM or IGNORE is
    skipped, and so is mail still waiting for its `mail.received` trigger run
    (with its attachments) until Janus has classified it.
  - `email_attachment`
  - `conversation`: one source per chat conversation.
  - `whatsapp_conversation`: one per WhatsApp chat.
  - `calendar_event`
  - `planner_task`
  - `upload`

  Memory facts and topics are **not** re-ingested as sources: they already
  have their own embeddings (phase 1), and phase 3 retrieval unions them in.
- **Discovery.** A step in the knowledge-index worker finds new or changed
  rows per type, the same way phase 1 finds stale embeddings: missing source,
  or owner newer than `source_updated_at`. Calendar events are compared by
  text hash instead, because calendar sync edits them in place without
  touching a timestamp. This covers every write path without ORM hooks.
  Sources whose owner row is gone, and mail later filed SPAM or IGNORE
  (with its attachments), are deleted along with their chunks and
  embeddings.
- **Attachments.** `imap_service` no longer just drops attachment parts.
  Documents (PDF, DOCX, XLSX, PPTX, TXT/MD, CSV, HTML, EML) up to 10 MB are
  staged as a `pending` `email_attachment` source holding the bytes in
  `staged_content`; anything else is recorded as `skipped` with the reason.
  When the pending staged bytes exceed 200 MB in total, new attachments are
  skipped ("staging backlog full") so a backlog cannot fill the disk. The
  worker parses them and sets `staged_content` to `NULL` in the same
  transaction. A failed parse keeps the bytes for one retry, then drops them.
  No binary is kept after parsing. Attachments of mail ingested before phase
  2 are not recoverable by backfill.
- **Parsing.** `markitdown[pdf,docx,xlsx,pptx]` converts documents to
  Markdown. It is imported lazily and adds about 220 MB to the virtualenv
  (measured 563 MB → 786 MB). It is the optional `documents` extra because
  its onnxruntime dependency needs glibc 2.27 and the glibc 2.17 production
  host cannot install it. Without the extra, TXT, MD, and CSV are read
  as-is. Other documents are skipped with a reason naming the extra.
  A parse error is recorded in `status_detail`; after the retry the source
  is `failed`.
- **Chunking** (`services/knowledge_chunking.py`). Chunks are structure-aware
  and packed to about 1,500 characters with a 200-character overlap. The
  splits follow Markdown headings and paragraphs, quoted-reply blocks in
  mail, and message boundaries in chats. No tokenizer is needed. Because
  packing is deterministic from the start, appending messages to a
  conversation leaves earlier chunks byte-identical. Re-chunking keeps every
  chunk whose `(ordinal, content_hash)` is unchanged, so only new text is
  re-embedded.
- **Processing.** Each worker tick processes at most 50 pending or stale
  sources, then embeds pending chunks in the existing batched sweep.
  Uploads are parsed immediately in the request so errors show at once.
- **Uploads.** `POST /api/knowledge/uploads` takes one multipart file (same
  types and 10 MB limit). `GET /api/knowledge/sources` lists the caller's
  sources (filter by type and status) and `DELETE
  /api/knowledge/sources/{id}` removes one with its chunks and embeddings.
  The Memory & Knowledge page gets a **Documents** section to upload, list
  and delete. Only uploads and attachments can be deleted there: sources
  built from mail, chats, events, and tasks would be rebuilt by the next
  tick while their owner row exists.
- **Backfill.** `janus knowledge backfill` runs discovery and processing
  until nothing is pending.
- **Vector storage.** Chunk vectors stay in `knowledge_embeddings` with the
  same exact `vec_distance_cosine()` search as phase 1; no `vec0` table yet.
  Phase 3 retrieval decides whether chunk counts need one.
- **Not in phase 2.** Planner events (only Janus-owned tasks and synced
  calendar events are ingested), agent tools over chunks, and entity
  extraction are phase 3.

## Phase 3 design (JAN-243)

Phase 3 ships in two parts, agreed with the owner on 2026-09-28:
**3a** builds the graph (tables, extraction, dedupe with a review queue, the
budget); **3b** adds hybrid retrieval with graph expansion, the
`knowledge.search` and `entity.get` tools, and an entity view.

### 3a: graph, extraction, review queue, budget

- **Tables** (`models_knowledge.py`, all `user_id`-scoped):
  - `knowledge_entities`: `name`, `normalized_name`, `entity_type`
    (`person`, `organization`, `project`, `place`, `product`, `concept`),
    `aliases` (JSON list), `description`. Unique per
    `(user_id, entity_type, normalized_name)`.
  - `knowledge_mentions`: chunk ↔ entity (unique per pair), the provenance
    edges.
  - `knowledge_relations`: `subject_id`, `predicate` (lowercase
    `snake_case`, at most 64 characters), `object_id`, `confidence`
    (`high`/`medium`), `valid_from`, `valid_to`. Unique per
    `(subject, predicate, object)`. Ending outdated relations
    (`valid_to`) is left for later; phase 3a only records them.
  - `knowledge_relation_evidence`: relation ↔ chunk, so each relation knows
    which chunks support it.
  - `knowledge_merge_suggestions`: a proposed merge of two entities with a
    reason and status `pending`/`accepted`/`rejected`. A rejected pair is
    never suggested again.
  - `knowledge_llm_usage`: tokens used per user and month (`YYYY-MM`):
    input, output, calls.
  - `knowledge_preferences`: per-user `monthly_token_budget` (default
    2,000,000).
  - `knowledge_chunks.extracted_hash`: the `content_hash` of the text last
    extracted; a chunk is pending while it differs.
- **Extraction** is the built-in trigger **Knowledge extraction**
  (`builtin.knowledge`). Every 30 minutes, its `prepare` hook makes direct
  structured JSON calls to the utility model (or the definition's own model
  choice) and completes the run with a summary through `skip_output`.
  There is no agent planning and there are no tools. Runs show up in trigger
  history and are retried like any other run.
  - Each call covers up to 8 pending chunks, newest sources first, with the
    untrusted-content warning. A run makes at most 6 calls.
  - The model returns entities (name, type, aliases, a short description,
    and the chunks mentioning them) and relations
    (`subject`, `predicate`, `object`, confidence, the supporting chunk).
  - Low-confidence items, unknown types, entities not mentioned in any chunk
    of the batch, and relations whose ends are not among the returned
    entities are dropped. A chunk rewritten or deleted while the model was
    answering is skipped and stays pending.
  - Candidate memory facts are not extracted; the existing memory pipeline
    covers facts.
- **Opt-in.** The definition is seeded **disabled**, so nothing is sent to a
  model until the user turns it on. The switch on the Knowledge tab names
  the model. When the provider is not Ollama, it warns that the text of all
  ingested sources goes to that provider.
- **Budget.** Before each call, the hook checks the user's usage for the
  current month plus the call's estimate (characters / 4) against
  `monthly_token_budget`. At the cap it stops and reports "monthly
  extraction budget reached". Usage records the provider's reported tokens
  (`usage_metadata`), or the estimate when a provider reports none.
- **Dedupe.** Cosine similarity is not used to merge entities (see "Filter
  first, then rank").
  - An extracted entity is merged automatically only when its normalized
    name or an alias equals an existing entity's name or alias of the same
    type. Normalization is casefolded, with punctuation and whitespace
    collapsed.
  - Otherwise it becomes a new entity. If a same-type entity has a
    similar name, a merge suggestion is queued instead: a string-similarity
    ratio of at least 0.85, or one name's words contained in the other's
    ("Acme" / "Acme GmbH").
  - Accepting a suggestion moves mentions, relations, evidence, and aliases
    to the surviving entity. The duplicate is deleted with every suggestion
    naming it.
- **Deletion.** Deleting or rewriting a chunk removes its mentions and
  evidence. Relations left without evidence are deleted, and so are
  entities left without mentions or relations, together with their
  suggestions.
- **API** (owner-scoped):
  - `GET/PUT /api/knowledge/extraction`: enabled, model, `is_cloud`, the
    budget, and this month's usage.
  - `GET /api/knowledge/entities`: filter by `entity_type` and `q`, with
    mention counts.
  - `GET /api/knowledge/merge-suggestions`, plus
    `POST /api/knowledge/merge-suggestions/{id}/accept` and `/reject`.
- **UI.** A **Knowledge** tab on Memory & Knowledge holds the extraction
  switch with the cloud warning, the budget and this month's usage, the
  merge review queue, and the entity list.

### 3b: retrieval and tools

- **Entity embeddings.** Entities are embedded like facts and topics
  (`knowledge_embeddings.owner_type = "entity"`, text = name and
  description), so a query can reach an entity by meaning. Deleting an
  entity deletes its vector.
- **Keyword side for chunks and entities.** A substring (`ILIKE`) match on
  the chunk text or the entity name/description, everywhere. Production has
  no FTS5, and the FTS tables only cover facts and topics.
- **`knowledge_retrieval.search(query, filters)`**, filters first, then
  ranks (see "Filter first, then rank"):
  1. **Candidates.** Chunks of the user's ready sources, narrowed by the
     optional filters before any ranking: `source_types`, a `since`/`until`
     date on the source, and `entity_ids` (only chunks mentioning one of
     them).
  2. **Direct ranking.** `knowledge_index.search_ids` over those chunks:
     vector similarity plus substring, fused with RRF. At most
     `VECTOR_ONLY_LIMIT` hits come from similarity alone.
  3. **Seed entities.** Up to 5 entities the query matches (the same fused
     ranking over entities). An `entity_ids` filter only narrows the
     candidates; it seeds nothing, so a query that matches nothing returns
     nothing.
  4. **Graph expansion.** One hop by default, at most two: entities related
     to the seeds through `knowledge_relations`, in either direction, up to
     20 per hop.
  5. **Graph ranking.** Candidate chunks that mention seed or expanded
     entities. Chunks mentioning more of them rank higher, then newer
     sources.
  6. **Fusion.** RRF over the direct and graph rankings. The top `limit`
     results (default 8, at most 20) are returned.

  Each hit carries the chunk text (trimmed to 1,200 characters) and its
  source (id, type, title, date). It also lists the entities the chunk
  mentions and why it matched: `query` (direct), or `graph` with the
  linking entity. The result also lists the seed entities with their
  relations. Nothing is sent to a model; only the query is embedded.
- **`knowledge_retrieval.entity_detail(entity_id)`** returns one entity:
  aliases, description, and its relations in both directions, each with
  the other entity, predicate, confidence, and the number of supporting
  chunks. It also returns the sources that mention the entity most, newest
  first.
- **Agent tools.** Tool names are `snake_case`, because dots are not valid
  in tool names. The new read-only `knowledge` toolbox
  (`confirmation="never"`, always granted) holds:
  - `search_knowledge(query, source_types?, since?, until?, entity?, limit?)`,
    where `entity` is a name resolved to entity ids, or an id;
  - `get_entity(entity)`, by id or name.

  The toolbox is allowlisted for chat, the briefing, and digest, and is
  *Allowed* by default for unattended runs (ad-hoc triggers, automations that
  select it, delegated tasks; JAN-255). It only reads the
  owner's own data. Results carry a note that their text is untrusted content.
- **API.** `GET /api/knowledge/search` takes `q`, `source_type` (repeatable),
  `since`, `until`, `entity_id` (repeatable), `limit`, and `hops`.
  `GET /api/knowledge/entities/{id}` returns the entity detail.
- **UI.** The Knowledge tab gains a search box with cited results, and
  clicking an entity opens its detail: relations and the sources that
  mention it.

## Phase 4 design (JAN-244)

Scoped with the owner on 2026-09-29: pages for topics and busy entities, a
built-in trigger that writes them, user edits kept with Janus rewrites filed
as proposals, and agent tools that read and propose.

- **Tables** (`models_knowledge.py`, `user_id`-scoped).
  - `wiki_pages`: `slug` (unique per user), `title`, `subject_type`
    (`topic`/`entity`) and `subject_id` (nullable once the subject is gone),
    the published `body` (Markdown) with its `citations`, `input_hash`,
    `synthesized_at`, `checked_at` (last staleness check), and `user_edited`.
  - `wiki_page_revisions`: every version of a page. Each row has `body`,
    `citations`, `author` (`janus`/`user`/`agent`), `kind` (`published` or
    `proposed`), `status` for proposals (`pending`/`accepted`/`dismissed`),
    `created_at`, and `resolved_at`.
  - `citations` is a JSON list of `{n, chunk_id, source_id, source_type,
    title, date}` snapshots, so a citation still shows its source after the
    chunk changes.
- **Which pages exist.** Before each run, the Wiki synthesis hook creates
  missing pages for:
  - every active or pinned tracked topic;
  - every entity mentioned in at least 5 chunks (`MIN_ENTITY_MENTIONS`).

  A page for any other entity can be created by hand (API/UI).
- **Inputs and staleness.**
  - A topic page reads the chunks of the sources behind its evidence (email
    and calendar-event evidence map to their knowledge sources). It adds the
    direct hits of `knowledge_retrieval.search(topic title)`.
  - An entity page reads the chunks mentioning the entity, newest first, and
    its relations.
  - At most 12 passages go into one page.
  - `input_hash` covers the subject's own text and the passages'
    `(chunk_id, content_hash)`. A page is stale when the hash differs.
  - Each run checks up to 200 pages, least recently checked first, and
    stamps `checked_at` on the current ones, so every page is reached.
  - A stale page with no passages left (its last source was deleted or
    unlinked) gets no model call. Its Janus and agent revisions, pending
    proposals included, are deleted. A page the user never edited goes back
    to "not written"; a user-edited one keeps its text and user revisions,
    with only the citations whose chunk still exists.
- **Synthesis.** The built-in trigger **Wiki synthesis** (`builtin.wiki`) runs
  hourly and is seeded **disabled**. Its `prepare` hook rewrites up to 5 stale
  pages per run, using direct model calls to the definition's model or the
  utility model, with no tools. It completes through `skip_output`.
  - The prompt numbers the passages (HTML-escaped inside `<passage>` tags)
    and puts the subject's own text in a `<subject>` tag. The
    untrusted-content warning covers both.
  - It asks for a short Markdown page that cites them as `[n]`.
  - Citation numbers that do not exist are removed from the text.
  - Calls count against the same per-user monthly token budget as
    extraction, and stop at it.
  - A page that is not `user_edited` is published directly, as a `janus`
    revision. On a `user_edited` page the rewrite is filed as a pending
    proposal, and the published body stays the user's.
- **Edits and review.**
  - A user edit is published as a `user` revision and sets `user_edited`.
  - Accepting a proposal publishes its body. Dismissing it keeps the page as
    it is.
  - Restoring an older revision publishes a copy of it.
  - Pages whose subject disappeared are deleted with their revisions unless
    `user_edited`; those keep their text and lose the subject link.
- **Agent tools.**
  - The `wiki` toolbox (`list_wiki`, `read_wiki`) is read-only and
    `confirmation="never"`. It is allowlisted like `knowledge`.
  - `wiki.propose` (`propose_wiki_edit`) is `confirmation="required"`.
    In chat each call pauses on an approval card until the user approves
    it (JAN-254, see [Tool permissions](tool-permissions.md)).
    It is off for unattended runs by default, so they cannot use it unless
    the user switches it to *Allowed* or selects it on a definition they
    wrote.
  - A proposal lands in the same review queue as Janus's own.
- **API** (owner-scoped): `/api/wiki`.
  - `GET /pages`, filtered by `subject_type`/`subject_id`/`q`.
  - `GET /pages/{id}`, with revisions and pending proposals.
  - `POST /pages`, to create an entity page.
  - `PUT /pages/{id}`, for a user edit.
  - `POST /revisions/{id}/accept`, `/dismiss`, and `/restore`.
  - `GET/PUT /synthesis`: the switch, with the same cloud warning as
    extraction.
- **UI.** A **Wiki** tab on Memory & Knowledge, shared by all three shells,
  with:
  - a page list with search;
  - a page view with rendered Markdown and numbered citations that link to
    their sources;
  - an editor;
  - revision history with restore;
  - proposals shown next to the current text, to accept or dismiss;
  - the synthesis switch.

  The Topics tab links each topic to its page.
- **Wiki links (JAN-331).** `[[Page Title]]` and `[[Page Title|label]]` in a
  wiki page, a chat reply, or the briefing render as links to
  `/memory?tab=wiki&wiki=<title>`, which opens the page whose title
  (case-insensitive) or slug matches exactly; with no match the tab says
  "No wiki page called … yet" and prefills the search. Frontend only: the
  page search (`GET /pages?q=`) finds the candidates. Code spans, fenced
  code, numeric targets (`[[3]]`, which stay free for citation markers), and
  targets over 200 characters are left as written.
- **Provenance and graph (JAN-334).** Synthesis also cites the subject's
  memory facts (`fact_id` citations); each page has a Sources tab resolving
  every `[n]` to its raw passage or fact, and the Wiki tab has a topic/entity
  graph. See [Knowledge stack visualization](knowledge-stack-visualization.md).
- **Not in phase 4.** Embedding wiki pages for `search_knowledge`, and
  splitting pages into sections.

## Risks and open questions

- **SQLite extension loading on the production host.** `sqlite-vec` is a
  loadable extension, so the production Python's `sqlite3` module must allow
  `enable_load_extension`. If it does not, vector search falls back to keyword
  search and the embeddings wait until the runtime is fixed; phase 1
  checks this at startup and reports it in system status.
  **Resolved 2026-09-28:** the production host (uv-managed Python 3.12.14,
  SQLite 3.53.4) loads extensions and `sqlite-vec` correctly but has no FTS5,
  so its keyword side is substring matching (JAN-245).
- **Vector search scale.** `sqlite-vec` does exact (brute-force) search. That
  is fast enough for one user's mail, chat and documents (hundreds of
  thousands of chunks); larger stores would be a reason to revisit PostgreSQL.
- **LLM cost.** Extracting from every chunk of every source is the dominant
  cost. Batching, content-hash dedupe, skipping SPAM/IGNORE mail, and a
  per-user monthly budget cap mitigate it.
- **Extraction quality.** Entity merging is where graph RAG systems degrade;
  the review queue and conservative auto-merge thresholds are deliberate.
- **Deletion.** Deleting a source must cascade to chunks, mentions, and
  relations it alone supports, and mark affected wiki pages for re-synthesis.
- **Encryption.** Chunk text of sensitive sources is stored like the source
  itself; embeddings are not encrypted (they are needed for search) and can
  leak some content — acceptable for a single-tenant server, noted here.

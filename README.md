## SEE the Steps.md, that should be enough to start the code


# Atlas — Customer Support Agent

Production-leaning, minimal-overhead customer support chatbot.

- **One FastAPI service** with native LLM **tool calling** (no regex parsing).
- **Real Postgres** for orders, customers, refunds, tickets, conversation history. No in-memory mocks.
- **Live Qdrant RAG** over the Amazon Products 2023 dataset (~1.4M rows).
- **Web + email channels**: a clean Webflow-inspired chat UI and an inbound email webhook reusing the same agent.
- **Real-time sentiment + automatic escalation** with persisted support tickets.
- **Dockerized** end-to-end. Three services: `api`, `postgres`, `qdrant`.

## Stack

| Layer | Choice | Why |
|------|--------|-----|
| API | FastAPI + uvicorn | Async-friendly, simple |
| LLM | Any OpenAI-compatible API (OpenAI, Azure OpenAI, OpenRouter, Together, Groq, Ollama, vLLM) | Reliable native tool calling |
| DB | PostgreSQL 16 | Transactional truth |
| Vector store | Qdrant 1.12 | Strong filters + simple ops |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (default, configurable) | Free + fast at 1.4M scale |
| UI | Vanilla HTML/CSS/JS | No framework, no AI slop |

## Quick start

```bash
cd "final version"
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY (or your compatible base URL + key)

docker compose up --build
```

Then in another shell, **seed demo data and ingest the catalog**:

```bash
docker compose run --rm --build api python -m app.seed

# Ingest — CSVs in ./data/, mounted at /data. `--build` keeps the api image in sync after code edits.
docker compose run --rm --build api python -m app.ingest --batch-size 256
```

Open `http://localhost:8000/` for the chat UI.

The ingest is **idempotent and checkpointed**: if it crashes or you stop it, rerun the same command and it resumes from the last committed batch.

## Architecture

```
web ─┐
     ├──► FastAPI (/chat) ──► Postgres   (orders, refunds, tickets, sessions)
email┘                   └──► LLM (managed) ◄── tool_calls ──► Qdrant (RAG)
```

Every `/chat` request:

1. Detects sentiment + intent on the new message (keyword rules; cheap, no extra LLM call).
2. Loads the last N turns from Postgres.
3. Calls the managed LLM with the tool catalog. The model decides which tools to call.
4. Each tool runs **live**: real SQL, real vector search, real SMTP. Results feed back into the loop.
5. Once the model emits a final assistant message, we persist user + assistant turns and respond with reply, sentiment, intent, escalated, tool calls, and RAG hits.

## Tools available to the agent

| Name | Implementation |
|------|----------------|
| `lookup_order` | Postgres `SELECT` + customer ownership check |
| `process_refund` | Postgres `INSERT` with idempotency key (`UNIQUE(order_id, request_key)`) |
| `get_account_info` | Postgres aggregate over the authenticated customer |
| `search_product_knowledge` | Live Qdrant cosine search; results returned with score + payload |
| `escalate_to_human` | Postgres `INSERT` into `support_tickets` (open status) |
| `send_customer_email` | SMTP send + audit row in `email_log` (record-only fallback when SMTP unset) |

## Outbound email (Amazon SES SMTP)

Atlas uses **`smtplib` + STARTTLS on port 587**, which aligns with **[SES SMTP](https://docs.aws.amazon.com/ses/latest/dg/send-email-using-smtp.html)**.

**Setup (quick):**

1. In **SES**, complete your wizard step: verify **`SMTP_FROM`** (e.g. `saurabh.suman@sjsu.edu`) via the verification email Amazon sends — unverified identities cannot send reliably.
2. **Region matters:** use the SES console dropdown you’re building in — N. Virginia is **`email-smtp.us-east-1.amazonaws.com`**. Match `SMTP_HOST` in `.env` to **[your region’s endpoint](https://docs.aws.amazon.com/ses/latest/dg/smtp-connect.html)**.
3. Go to **SMTP settings → Create SMTP credentials** and paste the **SMTP username/password** into `SMTP_USERNAME` / `SMTP_PASSWORD` in `.env` (different from arbitrary IAM keys; SES generates these).
4. **Sandbox:** by default SES only delivers to **verified recipient** addresses unless you **[request production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)** — verify testers under **SES → Verified identities**.
5. `docker compose up -d --build` (or restart `api`) after editing `.env`.

No code changes beyond env are needed; **`send_customer_email`** already routes through SMTP.

---

## Identity / authorization

The web UI lets you set a customer ID and email; the chat API treats those as the verified caller. Tools that touch order data **enforce `order.customer_id == authenticated_customer.id` in SQL**. Replace this with your real auth (JWT, cookie session, email verification) before exposing publicly — the contract is already in place inside `tools.py`.

## Configuration (excerpt — full list in `.env.example`)

| Var | Default | Purpose |
|-----|---------|---------|
| `OPENAI_API_KEY` | _(required)_ | Provider API key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Switch to Azure/OpenRouter/Ollama by changing this |
| `LLM_MODEL` | `gpt-4o-mini` | Any tool-calling capable model on your provider |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Must match between ingest and runtime |
| `EMBEDDING_DIM` | `384` | Match the model |
| `AGENT_MAX_STEPS` | `6` | Bounded tool-call loop |
| `LLM_TIMEOUT_SECONDS` | `60` | Per upstream call |
| `RAG_TOP_K` | `6` | Default retrieval depth |
| `SMTP_HOST` | _(empty)_ | **Amazon SES** (example): `email-smtp.us-east-1.amazonaws.com` — matches region in AWS console |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | _(empty)_ | From **SES → SMTP settings → Create SMTP credentials** (SES-specific secrets) |
| `ESCALATION_WEBHOOK_URL` | _(empty)_ | Optional Slack/Teams ping when a ticket opens |

## Health

`GET /health` returns Postgres + Qdrant readiness:

```json
{ "status": "ok", "checks": { "postgres": "ok", "qdrant": "ok" } }
```

## Notes

- **Embedding parity** is enforced: the ingest writes `embedding_model` and `embedding_dim` into the Qdrant collection metadata. Keep `EMBEDDING_MODEL` consistent between ingest runs and the API at query time.
- **Refund integrations** are stubbed at status `pending_manual` until you wire a real PSP (Stripe, Adyen, etc.) — the row is real, the dollars are not moved without that integration.
- **Inbound email webhook** at `POST /webhooks/email/inbound` accepts `{from, subject, text}` and routes the body through the same agent.
- The legacy notebook prototype (`cell3_source.py`, `index.html` at the repo root) is superseded by this folder.

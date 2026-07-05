# StockSage — Build Plan
**Deadline:** July 5, 2026 | **Stack:** Python · Cognee Cloud (REST API) · Anthropic Claude · yfinance · edgartools · Finnhub · Streamlit · uv

---

## Guiding principles
- Agents only do reasoning. Functions do data fetching.
- Prompts are the most important code. Test every prompt manually before wiring it in.
- PDF report is the primary output. Streamlit is just the input form.
- Build vertically — one complete working slice before expanding scope.
- **Reason from facts, not from predictions.** The system finds opportunities from
  what is actually happening or confirmed to happen — contracts awarded, projects
  announced, capacity expansions, regulatory approvals, earnings actuals, insider
  and institutional activity — then reasons independently about who benefits. It
  does not import analyst price targets, buy/sell ratings, or media speculation
  about stock performance. Any data source that surfaces Wall Street opinion or
  "here's what to buy" framing gets filtered out or avoided at the data layer, not
  just downweighted in a prompt — see Phase 1 for the specific fields/filters this
  affects.

---

## Phase 0 — Environment setup (2–3 hours)

### 0.1 Project structure
```
stocksage/
├── agents/
│   ├── meta_orchestrator.py
│   ├── layer_orchestrator.py
│   ├── sector_agent.py
│   ├── fundamental_agent.py
│   ├── market_signal_agent.py
│   ├── risk_agent.py
│   └── synthesis_agent.py
├── data/
│   ├── fetchers.py          # yfinance, edgartools, Finnhub — no LLM
│   └── formatters.py        # structure raw data into clean dicts
├── memory/
│   └── cognee_client.py     # wrapper for Cognee Cloud REST API
├── report/
│   └── pdf_generator.py     # markdown → PDF
├── prompts/
│   ├── sector_agent.txt
│   ├── fundamental_agent.txt
│   ├── market_signal_agent.txt
│   ├── risk_agent.txt
│   └── synthesis_agent.txt
├── app.py                   # Streamlit UI
├── .env
└── pyproject.toml           # uv-managed
```

### 0.2 Environment variables
```
COGNEE_API_KEY=
COGNEE_BASE_URL=https://api.cognee.ai      # override only if yours differs
ANTHROPIC_API_KEY=
FINNHUB_API_KEY=
EDGAR_IDENTITY=Your Name your.email@example.com   # SEC requires this for edgartools
```

### 0.3 Install dependencies
Managed with `uv`, not pip/requirements.txt:
```
uv add httpx anthropic yfinance edgartools requests python-dotenv streamlit reportlab
```
(`asyncio` is part of the Python standard library — no install needed.)

### 0.4 LLM assignment
Anthropic Claude, not OpenAI. Confirmed current model IDs and pricing
(introductory rates in effect through Aug 31, 2026):
- **Claude Sonnet 5** — `claude-sonnet-5` — $2/$10 per MTok
- **Claude Haiku 4.5** — `claude-haiku-4-5-20251001` — $1/$5 per MTok

Assignment is frequency-aware, not just complexity-aware: agents that run
once per sector can absorb Sonnet's cost easily; agents that run once per
*company* (8-9× per sector) multiply that cost, so model choice matters
more there.
- **Sonnet 5** — Meta-orchestrator, Sector Agent, Fundamental Agent, Synthesis
  (each runs once per sector, or the reasoning genuinely needs it)
- **Haiku 4.5** — Market Signal Agent, Risk Agent (extraction/classification-
  flavored work; both run once per company, so this is where the cost
  multiplication is largest)

Web search (Sector Agent, Risk Agent only) costs an additional $0.01/search
on top of tokens — search results themselves can add thousands of input
tokens per call. Estimated real cost: ~$0.55-0.70 per full sector run
(all 6 agents, ~8 candidate companies).

### 0.5 Cognee — REST API, not the Python library
All Cognee interaction goes through `httpx` calls to the Cognee Cloud REST API (`https://api.cognee.ai` by default), authenticated via an `X-Api-Key` header. No `cognee` package dependency. See Phase 2.

### 0.6 Live web search — Sector Agent and Risk Agent only
Anthropic's API has a built-in server-side web search tool — add
`{"type": "web_search_20250305", "name": "web_search"}` to the `tools` list on
an agent's `messages.create()` call and Anthropic executes the search itself;
no custom search-and-loop pipeline needed. Only the Sector Agent and Risk
Agent get this (see Phase 3) — Fundamental Agent, Market Signal Agent, and
Synthesis Agent stay on structured data only, since web search adds noise
without value for what they do. Each search has a real per-call cost on top
of tokens — worth keeping an eye on total searches per report run.

**Important:** the Finnhub opinion/prediction blocklist (Phase 1) does not
touch live search results at all. For any agent with web search access, the
"facts only, not opinions" instruction in its own prompt is the *only*
filter — it must be at least as strict as what's already in Prompt 2.
**No LLM. No agents. Pure data fetching and structuring.**

Build `data/fetchers.py` with these functions. Every function returns a consistent shape so a bad ticker or a down API never crashes a multi-agent run later:
```python
{"ticker" | "query": str, "success": bool, "source": str, "data": dict | list | None, "error": str | None}
```

| Function | Source | Returns |
|---|---|---|
| `get_price_history(ticker)` | yfinance | OHLCV, 1 year |
| `get_fundamentals(ticker)` | yfinance | P/E, margins, debt/equity, FCF, ROE/ROA. **No analyst target price, rating, or analyst count** — those are Wall Street opinion, not fact, and are deliberately excluded per the guiding principle above. |
| `get_earnings_history(ticker)` | yfinance | Quarterly EPS actual vs. estimate, surprise % |
| `get_insider_trades(ticker)` | Finnhub `/stock/insider-transactions` | Recent Form 3/4/5-sourced buy/sell transactions |
| `get_institutional_changes(ticker)` | yfinance institutional holders | Top holders + QoQ `pctChange` per holder (13F-derived) |
| `get_recent_filings(ticker)` | edgartools | Last 3 8-K filings (material events) |
| `get_news_sentiment(ticker)` | Finnhub `stock_insider_sentiment` (MSPR) | **Not real news sentiment** — Finnhub's `/news-sentiment` is premium-gated on our key, so this returns Monthly Share Purchase Ratio (-100..100), an insider-activity-derived proxy. Overlaps conceptually with `get_insider_trades`. **Open decision for Phase 3:** keep as-is and frame the Market Signal prompt around it honestly, or spend ~15 min adding real headlines via `/company-news` so the agent has actual public-sentiment text, not just a second insider-derived number. |
| `get_sector_news(query)` | Finnhub general/merger/crypto feeds, pooled + client-side filtered | Real but noisy — no true sector-news endpoint exists on this API. Filtered two ways: keyword match for relevance, and a blocklist that drops headlines/summaries containing analyst-opinion phrasing ("price target," "rating," "upgrade"/"downgrade," "outperform"/"underperform," "bullish on"/"bearish on," "top picks," "here's why," "should you buy") per the no-predictions principle. The blocklist is a best effort, not a guarantee — some opinion content will still slip through pure keyword filtering. |
| `get_federal_contract_awards(keyword)` | USASpending.gov `/api/v2/search/spending_by_award/` (no API key required) | Prime federal contracts AND grants matching a keyword — the "mega project" / prime-recipient layer. Covers things a general news search won't reliably catch: CHIPS Act grants, defense contracts, DOE/infrastructure awards. Returns recipient name, amount, agency, date, description per award. |
| `get_federal_subawards(keyword)` | USASpending.gov, same endpoint with `subawards=true` | The actual subcontract layer — who a prime contractor/grantee is paying to do the work. This is the direct equivalent of "Layer 3 supplier" in the contract-layer-tracing methodology (see Phase 3, Prompt 2). |

**New for Phase 1.5, added mid-hackathon:** the two USASpending functions above exist specifically to support a more rigorous "trace the supply chain of a real mega-project" methodology (see Phase 3, Prompt 2) rather than relying on general news to guess who benefits from a sector trend. SAM.gov (open solicitations, not-yet-awarded) was considered and rejected — it requires an API key with a ~10 business day approval wait, not viable within the hackathon deadline.

**Known simplification, accepted deliberately:** `get_institutional_changes` uses a yfinance holder snapshot rather than pulling and diffing two separate EDGAR 13F filing periods — edgartools has no per-ticker 13F index (13F-HR filings are filed by managers about their whole portfolio, not searchable by ticker), and yfinance's snapshot already ships the QoQ delta we needed.

**Test checkpoint:** ✅ Verified — every function run against NVDA and PLTR, returning real, spot-checked-correct data (real OHLCV, real P/E, real 8-K text, real insider transaction volumes, real institutional QoQ deltas).

---

## Phase 2 — Cognee memory wrapper (1–2 hours) — VERIFIED against live API
Built as `memory/cognee_client.py` — a clean wrapper so agents never call Cognee's REST API directly.

Real, confirmed REST primitives (superseding earlier guesses):
```
POST   /api/v1/add_text   - ingest text into a dataset: {textData, datasetName, nodeSet}
                             (NOT /api/v1/add — that endpoint is multipart-file-upload
                             only; sending JSON to it silently 409s)
POST   /api/v1/cognify     - build/update the knowledge graph for a dataset
POST   /api/v1/search      - {query, search_type, datasets}. Use search_type=CHUNKS for
                             byte-exact round-tripping of structured JSON — the default
                             GRAPH_COMPLETION paraphrases content through an LLM, which
                             is right for open-ended queries but wrong for "give me back
                             exactly what I wrote."
GET    /api/v1/datasets/   - list datasets → [{id, name, createdAt, updatedAt, ownerId}]
                             (camelCase — inconsistent with search's snake_case response)
DELETE /api/v1/datasets/{id} - hard-delete a dataset
```
There is no literal "improve" endpoint. `improve_memory()` re-runs `cognify` across all
StockSage datasets — the closest real primitive to "refresh/enrich the graph."

Dataset naming: `stocksage_sector_<slug>` / `stocksage_company_<TICKER>`, with
`node_set`/`node_name` tagging (found in the schema, not anticipated in this plan)
used for precise filtering within a dataset instead of relying on semantic
relevance ranking.

### Run isolation — every pipeline execution gets its own memory, cleaned up after

**Decision:** report runs must never share memory with each other. Every full
pipeline execution (one user request → one PDF) gets a fresh `run_id`, and
its data is deleted once that run finishes. Two reasons:
1. **Correctness** — if run #2 started while run #1's data was still around,
   synthesis would silently blend findings from two unrelated runs.
   Different recommendations between runs is fine and expected (the LLM
   reasoning legitimately varies); recommendations contaminated by leftover
   data from a previous run is a bug.
2. **Performance** — an ever-growing shared graph means every later run
   drags more irrelevant context into `read_all_findings`, making things
   slower and noisier for no benefit, since nothing outlives a single report.

**Dataset naming** embeds the run: `stocksage_run<run_id>_sector_<slug>` and
`stocksage_run<run_id>_company_<TICKER>`. `run_id` is generated once, by
whatever kicks off a report (the meta-orchestrator, in Phase 4) — a
timestamp like `20260704T153012` is fine, just needs to be unique per run.

Function signatures (every one now takes `run_id` as the first argument):
```python
async def write_sector_context(run_id, sector_name, data: dict)
async def write_company_findings(run_id, ticker, layer, agent_name, data: dict)
async def read_sector_context(run_id, sector_name) -> dict
async def read_company_findings(run_id, ticker) -> dict
async def read_all_findings(run_id) -> dict   # for synthesis agent — scoped to THIS run only
async def improve_memory(run_id, dataset_name=None)
async def cleanup_run(run_id)                 # deletes every dataset tagged with this run_id
```

`cleanup_run` replaces `forget_sector` as the primary teardown call — it lists
all datasets, filters to ones matching this run's naming pattern, and deletes
every one of them, sector and company data alike. In Phase 4, the orchestrator
calls `cleanup_run(run_id)` in a `finally` block after synthesis, so a crashed
run doesn't leave orphaned datasets behind either.

**Test checkpoint:** Generate a fake run_id, write a dummy sector context
and a dummy company finding under it, read both back, then call
`cleanup_run` and confirm `read_all_findings(run_id)` comes back empty
afterward.

---

## Phase 3 — Prompt engineering (4–6 hours)
**Most important phase. Do not skip or rush this.**

### The testing workflow
1. Write the prompt in `prompts/` as a plain text file
2. Copy it into Claude.ai
3. Paste realistic fake input (e.g. sector = "AI Infrastructure", recent news headlines)
4. Read output critically — is it specific? Non-obvious? Actionable?
5. If generic → add constraints. If too narrow → loosen scope.
6. Repeat until output looks like a junior hedge fund analyst wrote it
7. Only then wire it into code

### Prompt 1 — Meta-orchestrator — Sonnet 5
**Job:** Interpret user input, assign runtime sector briefs to sector agents.

Must produce for each sector:
- Specific domain framing (not just "Technology" — "Semiconductor capital equipment benefiting from CHIPS Act fab buildout")
- The specific catalyst or trend to research
- Starting ticker universe (6–8 relevant companies, not household names unless specifically warranted)
- Budget context signal
- What to look for that is non-obvious

**Anti-patterns to prevent:**
- Do not name Apple, Google, Microsoft, Amazon unless there is a specific non-obvious reason
- Do not describe general sector trends — identify the specific catalyst creating opportunity NOW
- Ticker universe must be directly in the path of the catalyst, not tangential beneficiaries
- Do not cite, repeat, or defer to any analyst price target, rating, or "expert opinion"
  language that appears in source data — reason only from confirmed events (contracts,
  filings, capacity/regulatory changes, earnings actuals)

**Verified against live API, with real limitations that reshape the methodology below:**
- `award_type_codes` can only span one award-type group per call — contracts and
  grants require two separate calls, merged and tagged by `award_category`.
- Keyword search is naive substring matching, not entity-aware — a bare word
  like "Micron" matches unrelated results ("Micronesian," "microneedle"). Only
  ever query with precise multi-word company/project names.
  CHIPS Act-style manufacturing incentive awards (the actual megaprojects this
  was built to trace) did not reliably surface even with correct keywords —
  they're likely booked under a different award-type group (financial
  assistance/other transactions) than standard contracts/grants cover.
- Subaward data, where it exists, is genuinely rich (exact line items) but
  coverage is sparse and skews toward academic/IT subgrants, not industrial
  supply chains. **Expect CONFIRMED tags to be the exception, not the norm** —
  this is the system being honest about what it can verify, not a bug to fix.

**Design implication:** USASpending works best as a *confirmation* step after a
project/company is already identified via news or 8-Ks — not as the primary
discovery engine. The Prompt 2 methodology below reflects this ordering.

### Prompt 2 — Sector agent (Layer 1) — Sonnet 5

**Job:** Given a specific catalyst from the Meta-orchestrator, trace the actual
supply chain of the underlying mega-project(s) to find which listed companies
are structurally positioned to receive order inflow — before the market has
priced it in. This is not sector-trend commentary; it is contract-layer
tracing. Methodology adapted from a proven approach originally developed for
Indian infrastructure equity research:

1. **Identify the mega-project from news/filings first.** Use the catalyst
   and evidence already provided by the Meta-orchestrator, plus
   `get_recent_filings` on any candidate company, to establish what the
   actual project is — total scope, stage, timeline if stated.
2. **Search the web directly for the specific project and its suppliers.**
   You have live web search access — use it. Query for the exact project
   name plus terms like "award," "contract," "supplier," "subcontractor" —
   this is often faster and more precise than USASpending's own keyword
   search, and it's what actually found the megaproject data USASpending's
   award-type taxonomy missed in testing. Use precise, targeted queries
   (the specific project or company name), not broad sector terms.
3. **Attempt confirmation via USASpending as a second pass.** Query
   `get_federal_contract_awards` and `get_federal_subawards` using precise
   multi-word keywords (never a bare generic word). Treat a hit as strong
   confirming evidence, but a miss is not disqualifying — most real
   industrial megaprojects won't surface there, per the verified limitations
   above.
4. **Tag every company CONFIRMED or INFERRED, never blur the two:**
   - **CONFIRMED** — this exact company appears in a real award/subaward
     record, OR a specific news article/press release directly names this
     company in connection with this project. Cite which source.
   - **INFERRED** — no direct record or article names this company; this is
     the agent's own technical reasoning about who plausibly supplies this
     kind of equipment/material to this kind of project. Still useful, but
     must never be presented as confirmed.
5. **Assess timing, not just existence.** Has the relevant award/subaward/
   contract already been made, or is the project still at announcement
   stage? Note the likely order-to-revenue lag, and flag that
   government-anchored project timelines routinely slip 12–24 months — a
   thesis resting entirely on an on-time delivery is fragile.

Input: sector brief from Meta-orchestrator + `get_recent_filings` +
`get_federal_contract_awards` / `get_federal_subawards` (precise keywords only)

Output (structured):

Note: `exposure_type` here is only ever CONFIRMED or INFERRED — this agent
never assigns DIRECT_RECIPIENT. That third tag is added separately by the
orchestrator in Phase 4, when it adds `prime_project.recipient_ticker` to
the Layer 2 fan-out alongside these candidates (see Phase 4). The prime
recipient is never part of this agent's own candidate_companies output —
it's not a Layer 3 supply-chain guess, so it doesn't belong in this list.

```json
{
  "sector_name": "",
  "catalyst": "",
  "prime_project": {
    "description": "", "total_value": "", "awarding_agency": "",
    "recipient": "", "recipient_ticker": "", "award_date": "", "stage": ""
  },
  "opportunity_narrative": "",
  "candidate_companies": [
    {
      "ticker": "",
      "company_name": "",
      "exposure_type": "CONFIRMED | INFERRED",
      "evidence": "the subaward record OR the technical reasoning, depending on exposure_type",
      "contract_layer": "e.g. equipment supplier, subsystem integrator, materials supplier",
      "estimated_order_to_revenue_lag": "",
      "why_positioned": "",
      "specific_angle": ""
    }
  ]
}
```

**Anti-patterns to prevent:**
- Do not pick companies just because they are large in the sector
- Each company must have a specific, non-generic reason for inclusion
- Opportunity narrative must cite specific recent events, not general trends
- Do not repeat or lean on any analyst price target, rating, or forecast language
  found in source news OR in live web search results — the opportunity
  narrative must come from the underlying event itself (the contract, the
  project, the filing), not from someone else's prediction about what it
  means for the stock. The Finnhub blocklist does not filter web search
  results — this instruction is the only filter for that data, so apply it
  just as strictly.
- Do not present INFERRED exposure as CONFIRMED — if there's no award/subaward
  record or specific news article naming the company, it must be tagged
  INFERRED, no exceptions
- Never query USASpending with a single generic word — use precise multi-word
  company or project names only, or risk false CONFIRMED matches on unrelated entities
- Do not treat a USASpending miss as evidence the project isn't real — search
  the web directly and fall back to INFERRED reasoning if neither finds a record
- Do not stop at one project — if a candidate company appears across multiple
  awards/subawards, multiple news-confirmed projects, or multiple search
  results in the sector, that's a materially stronger thesis and should be
  noted as such
- Do not treat an award date as a reliable delivery/production timeline —
  government-anchored projects routinely slip; note this as a risk rather
  than assuming the award date implies near-term revenue
- Prefer under-followed, smaller listed companies genuinely named in
  subaward records or specific project coverage over large, obvious names
  already covered by mainstream financial media — a large well-known company
  winning a contract is far less interesting than a small one doing the same

### Prompt 3 — Fundamental agent (Layer 2) — Sonnet 5
**Job:** Validate whether the financial reality backs up the sector opportunity for a specific company.

Input: company ticker + pre-fetched financial data dict + sector context from Cognee
Output (structured):
```json
{
  "ticker": "",
  "financial_metrics": {
    "revenue_cagr_3y": "", "gross_margin_trend": "", "fcf_vs_earnings": "",
    "debt_equity": "", "roic_vs_wacc": "", "valuation_vs_sector": ""
  },
  "moat_assessment": "",
  "earnings_quality": "",
  "valuation_view": "",
  "fundamental_signal": "STRONG | MIXED | WEAK",
  "key_insight": ""
}
```

**Anti-patterns to prevent:**
- Do not hallucinate numbers — all metrics come from pre-fetched data passed in prompt
- key_insight must be something non-obvious that the numbers reveal
- Do not just describe what the numbers are — interpret what they mean

### Prompt 4 — Market signal agent (Layer 2) — Haiku 4.5
**Job:** Determine what smart money and recent sentiment are saying about this company.

Input: ticker + insider trades dict + institutional changes dict + news sentiment score
Output (structured):
```json
{
  "ticker": "",
  "institutional_signal": "ADDING | REDUCING | NEUTRAL",
  "insider_signal": "BUYING | SELLING | NEUTRAL",
  "sentiment_score": "",
  "key_institutional_moves": "",
  "news_catalyst": "",
  "market_signal": "BULLISH | BEARISH | NEUTRAL",
  "key_insight": ""
}
```

**Anti-patterns to prevent:**
- Do not interpret neutral data as positive
- If institutions are reducing, say so clearly — do not soften it
- key_insight must explain WHY smart money is moving, not just that they are

### Prompt 5 — Risk agent (Layer 2) — Haiku 4.5
**Job:** Find reasons NOT to be interested in this company. Governance, regulatory, financial, competitive risks.

This agent has live web search access — use it to find specific, recent risk
events beyond what the last 3 8-Ks capture: lawsuits, regulatory actions,
safety incidents, executive departures, competitive threats. Search with the
company name plus specific terms ("lawsuit," "investigation," "recall,"
"resigns") rather than a broad company-name-only search.

Input: ticker + recent 8-K filings + live web search
Output (structured):
```json
{
  "ticker": "",
  "governance_flags": [],
  "regulatory_risks": [],
  "financial_risks": [],
  "competitive_risks": [],
  "veto_flag": true/false,
  "veto_reason": "",
  "overall_risk_level": "LOW | MEDIUM | HIGH | VETO",
  "key_insight": ""
}
```

**Anti-patterns to prevent:**
- Do not be optimistic — this agent's job is to find problems
- veto_flag = true means synthesis agent must flag this prominently regardless of other signals
- Do not fabricate risks — only flag what the 8-Ks or search results actually support
- Do not repeat or lean on any analyst price target, rating, or "this stock could
  fall" opinion found in search results — extract only the underlying factual
  event (the lawsuit, the departure, the recall itself), never someone else's
  prediction about what it means for the stock. The Finnhub blocklist does not
  apply to web search results — this instruction is the only filter here.

### Prompt 6 — Synthesis agent (Layer 3) — Sonnet 5
**Job:** Read all findings across all companies and sectors, resolve conflicts, apply budget context, produce the final report narrative.

Input: full Cognee graph recall (`read_all_findings`) + user budget + horizon
Output: structured markdown report per the report template

**Critical instructions:**
- Where fundamental signal and market signal conflict → state both, explain the conflict, let user decide
- Where risk agent raised veto flag → must be prominently disclosed, cannot be buried
- Budget context → flag any stock where price per share exceeds 20% of budget
- Company classification → READY / WATCH / SPECULATIVE based on combined signal assessment
- Cross-sector observations → identify any patterns found by traversing across sectors
- Every claim must cite which agent's finding it came from

---

## Phase 4 — Agent implementation (4–5 hours)

### Build order
1. `sector_agent.py` — simplest, test Layer 1 end to end first
2. `fundamental_agent.py` — most data-heavy, verify data pipeline works
3. `market_signal_agent.py`
4. `risk_agent.py` — test 8-K parsing here
5. `synthesis_agent.py` — build last, needs all others working

### Agent template — structured-data-only agents (Fundamental, Market Signal, Synthesis)
```python
async def run(input_data: dict, cognee_client) -> dict:
    context = await cognee_client.read_sector_context(run_id, input_data["sector"])
    raw_data = await fetchers.get_fundamentals(input_data["ticker"])
    prompt = build_prompt("fundamental_agent.txt", context, raw_data)
    response = await call_llm(prompt, model="claude-sonnet-5")
    findings = parse_json_response(response)
    await cognee_client.write_company_findings(
        run_id, input_data["ticker"], "layer2", "fundamental", findings
    )
    return findings
```

### Agent template — web-search-enabled agents (Sector, Risk)
Same shape, but the `messages.create()` call includes Anthropic's built-in
web search tool. No custom search-and-fetch loop needed — Anthropic executes
the search server-side and returns grounded results as part of the response.
```python
async def run(input_data: dict, cognee_client) -> dict:
    context = await cognee_client.read_sector_context(run_id, input_data["sector"])
    prompt = build_prompt("risk_agent.txt", context, input_data)
    response = await call_llm(
        prompt,
        model="claude-haiku-4-5-20251001",
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    findings = parse_json_response(response)
    await cognee_client.write_company_findings(
        run_id, input_data["ticker"], "layer2", "risk", findings
    )
    return findings
```

### Orchestrator structure
```python
# meta_orchestrator.py
async def run(user_input: dict):
    run_id = generate_run_id()  # e.g. datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    try:
        sector_briefs = await interpret_user_input(user_input)
        await layer_orchestrator.run_layer1(run_id, sector_briefs)
        for sector in sector_briefs:
            candidates = await cognee_client.read_sector_context(run_id, sector["name"])
            await layer_orchestrator.run_layer2(run_id, candidates["candidate_companies"])
        report = await synthesis_agent.run(run_id, user_input)
        return report
    finally:
        await cognee_client.cleanup_run(run_id)  # always tear down, success or failure

# layer_orchestrator.py
async def run_layer1(run_id: str, sector_briefs: list):
    tasks = [sector_agent.run(run_id, brief) for brief in sector_briefs]
    await asyncio.gather(*tasks)

async def run_layer2(run_id: str, sector_brief: dict):
    # Layer 3 candidates get the normal CONFIRMED/INFERRED treatment.
    # The prime project recipient (sector_brief["prime_project"]["recipient"])
    # also runs through the same three agents — tagged DIRECT_RECIPIENT, not
    # CONFIRMED/INFERRED, since it's not a supply-chain exposure guess, it's
    # the known project owner. Included for baseline/comparison context in
    # the final report, not because it's a new discovery.
    companies = sector_brief["candidate_companies"] + [
        {"ticker": sector_brief["prime_project"]["recipient_ticker"],
         "exposure_type": "DIRECT_RECIPIENT"}
    ]
    for company in companies:
        tasks = [
            fundamental_agent.run(run_id, company),
            market_signal_agent.run(run_id, company),
            risk_agent.run(run_id, company),
        ]
        await asyncio.gather(*tasks)
```

**Rate limit note:** Layer 2 fans out 3 agents × N companies against Finnhub (free tier ~60 calls/min) and EDGAR (fair-access limits) with no cap as written above. Add a concurrency limit (e.g. `asyncio.Semaphore`) once you're running more than 2–3 companies at once, rather than debugging silent 429s later.

---

## Phase 5 — Report generation (2–3 hours)

### PDF structure
```
Cover page — title, date, user inputs
━━━━━━━━━━━━━━━━━━━━━━
Section per sector
  - Sector opportunity narrative
  - Company entries (READY / WATCH / SPECULATIVE)
    - Opportunity angle
    - Financial snapshot (key metrics only)
    - Smart money signal
    - Risk flags
    - Key news
━━━━━━━━━━━━━━━━━━━━━━
Cross-sector observations
Budget context notes
━━━━━━━━━━━━━━━━━━━━━━
Disclaimer
```

Use `reportlab` for PDF generation. Synthesis agent outputs structured markdown → `pdf_generator.py` converts to clean PDF.

---

## Phase 6 — Streamlit UI (1–2 hours)

Keep it minimal. Three components only:

**Input form**
- Sector selector (multi-select, 6 options)
- Budget input (number field)
- Horizon selector (dropdown: 6 months / 1 year / 2 years)
- Generate button

**Progress display**
```python
with st.status("Generating your report...") as status:
    status.update(label="Researching sectors...")
    # run layer 1
    status.update(label="Analyzing companies...")
    # run layer 2
    status.update(label="Building report...")
    # run layer 3
```

**Output**
- Download PDF button. Done. Nothing else.

---

## Phase 7 — Testing and iteration (ongoing)

### Test each layer independently before connecting
- Layer 1 test: single sector, verify Cognee has correct sector node
- Layer 2 test: single company, verify all three agent findings in Cognee
- Layer 3 test: hardcode some Cognee data, verify report quality
- End to end test: full run with 2 sectors

### What good output looks like
- Sector narrative cites specific recent events, not general trends
- Company picks are non-obvious — not just the top 5 by market cap
- Financial findings interpret numbers, not just describe them
- Report clearly distinguishes READY vs WATCH vs SPECULATIVE
- PDF reads like a research brief, not a chatbot output

### Red flags in output
- Any mention of Apple, Google, Meta without specific non-obvious reason
- Generic phrases like "strong fundamentals" without specific numbers
- Conflicts between agents not acknowledged in final report
- Risk flags buried or softened in synthesis

---

## Phase 8 — Submission prep (2–3 hours)

### README must include
- What the system does (one paragraph)
- Architecture diagram
- How Cognee is used — specifically which APIs and why
- How to run it locally (setup + .env + `uv run streamlit run app.py`)
- Demo video link
- Tech stack
- Disclosure: AI assistants used in development (required by rules)

### Demo video (2–3 minutes)
1. Show the simple Streamlit form
2. Enter inputs live — e.g. "$5,000, Tech + Energy, 1 year"
3. Watch progress indicators run
4. Open the downloaded PDF
5. Scroll through — highlight one READY company, one WATCH, one SPECULATIVE
6. Show a cross-sector observation the system found
7. Point out a specific non-obvious company pick and explain why the system found it

---

## Priority order if time runs short

1. **Never cut:** Prompts, Cognee integration, Layer 1, Layer 2, PDF report
2. **Cut first:** Streamlit styling, multiple sectors (demo with 1 sector if needed)
3. **Cut second:** Market signal agent (run with Fundamental + Risk only)
4. **Last resort:** Pre-run the system and demo with cached output rather than live run

---

## Daily targets

| Day | Target |
|---|---|
| Today (Jul 3) | Phase 0 + Phase 1 + Phase 2 + start Phase 3 prompts |
| Jul 4 | Finish Phase 3 prompts + Phase 4 agents + Phase 5 PDF |
| Jul 5 (morning) | Phase 6 UI + Phase 7 testing + Phase 8 submission |
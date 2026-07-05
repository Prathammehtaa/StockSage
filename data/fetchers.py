"""Pure data-fetching functions for StockSage. No LLM calls, no agents.

Sources: yfinance (price/fundamentals/earnings), edgartools (SEC filings),
Finnhub (news). Every function returns plain dicts/lists so results can be
JSON-serialized straight into Cognee or a prompt.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import edgar
import finnhub
import httpx
import yfinance as yf
from dotenv import load_dotenv
from edgar import Company, set_identity

load_dotenv()

_EDGAR_IDENTITY_SET = False


def _ensure_edgar_identity():
    global _EDGAR_IDENTITY_SET
    if not _EDGAR_IDENTITY_SET:
        identity = os.environ.get("EDGAR_IDENTITY") or os.environ.get("SEC_EDGAR_EMAIL")
        if not identity:
            raise RuntimeError(
                "EDGAR_IDENTITY (or SEC_EDGAR_EMAIL) env var must be set to a real "
                "name/email for SEC EDGAR's fair-access policy."
            )
        set_identity(identity)
        # edgartools defaults to a 30s read timeout; tightened explicitly to
        # 20s so a single slow EDGAR response can't stall a whole company's
        # research for longer than that -- get_insider_trades/get_recent_filings
        # can each make many sequential filing fetches, so this bounds the
        # worst case per sub-request, not just the first one.
        edgar.configure_http(timeout=20.0)
        _EDGAR_IDENTITY_SET = True


def _safe_float(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f  # NaN != NaN


_finnhub_client = None


def _get_finnhub_client():
    global _finnhub_client
    if _finnhub_client is None:
        api_key = os.environ["FINNHUB_API_KEY"]
        _finnhub_client = finnhub.Client(api_key=api_key)
    return _finnhub_client


def get_price_history(ticker: str) -> list[dict]:
    """OHLCV for the last 1 year via yfinance."""
    history = yf.Ticker(ticker).history(period="1y")
    records = []
    for date, row in history.iterrows():
        records.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            }
        )
    return records


def get_fundamentals(ticker: str) -> dict:
    """P/E, EV/EBITDA, debt/equity, margins, FCF, ROIC via yfinance."""
    t = yf.Ticker(ticker)
    info = t.info

    roic = None
    try:
        income_stmt = t.income_stmt
        balance_sheet = t.balance_sheet
        col = income_stmt.columns[0]
        ebit = income_stmt.loc["EBIT", col]
        tax_rate = income_stmt.loc["Tax Rate For Calcs", col]
        invested_capital = balance_sheet.loc["Invested Capital", balance_sheet.columns[0]]
        if invested_capital:
            roic = float(ebit * (1 - tax_rate) / invested_capital)
    except (KeyError, IndexError):
        roic = None

    return {
        "ticker": ticker,
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "debt_to_equity": info.get("debtToEquity"),
        "gross_margin": info.get("grossMargins"),
        "operating_margin": info.get("operatingMargins"),
        "profit_margin": info.get("profitMargins"),
        "free_cash_flow": info.get("freeCashflow"),
        "roic": roic,
        "return_on_equity": info.get("returnOnEquity"),
        "return_on_assets": info.get("returnOnAssets"),
        "market_cap": info.get("marketCap"),
    }


def get_earnings_history(ticker: str) -> list[dict]:
    """Quarterly earnings vs estimates via yfinance."""
    t = yf.Ticker(ticker)
    dates = t.earnings_dates
    if dates is None or dates.empty:
        return []
    reported = dates.dropna(subset=["Reported EPS"]).sort_index(ascending=False)
    records = []
    for date, row in reported.iterrows():
        records.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "eps_estimate": row.get("EPS Estimate"),
                "eps_reported": row.get("Reported EPS"),
                "surprise_pct": row.get("Surprise(%)"),
            }
        )
    return records


def get_insider_trades(ticker: str) -> dict:
    """Net buy/sell from Form 4 filings over the last 2 quarters via edgartools."""
    _ensure_edgar_identity()
    company = Company(ticker)
    cutoff = datetime.now(timezone.utc) - timedelta(days=183)

    filings = company.get_filings(form="4").head(80)
    total_bought_shares = 0.0
    total_sold_shares = 0.0
    total_bought_value = 0.0
    total_sold_value = 0.0
    transactions = []

    for filing in filings:
        filing_date = datetime.combine(filing.filing_date, datetime.min.time(), tzinfo=timezone.utc)
        if filing_date < cutoff:
            continue
        try:
            df = filing.obj().to_dataframe()
        except Exception:
            continue
        if df.empty or "Transaction Type" not in df.columns:
            continue
        for _, row in df.iterrows():
            txn_type = str(row.get("Transaction Type", ""))
            shares = _safe_float(row.get("Shares"))
            price = _safe_float(row.get("Price"))
            value = shares * price
            direction = None
            if "sale" in txn_type.lower():
                direction = "sell"
                total_sold_shares += shares
                total_sold_value += value
            elif "purchase" in txn_type.lower():
                direction = "buy"
                total_bought_shares += shares
                total_bought_value += value
            transactions.append(
                {
                    "filing_date": filing.filing_date.isoformat(),
                    "insider": row.get("Position") or None,
                    "transaction_type": txn_type,
                    "direction": direction,
                    "shares": shares,
                    "price": price,
                    "value": value,
                }
            )

    return {
        "ticker": ticker,
        "period_days": 183,
        "net_shares": total_bought_shares - total_sold_shares,
        "total_bought_shares": total_bought_shares,
        "total_sold_shares": total_sold_shares,
        "total_bought_value": total_bought_value,
        "total_sold_value": total_sold_value,
        "transaction_count": len(transactions),
        "transactions": transactions,
    }


def get_institutional_changes(ticker: str) -> dict:
    """Top institutional holders and QoQ change.

    Note: edgartools has no per-security 13F aggregation (13F-HR filings are
    filed by managers, not indexed by the security they hold), so this uses
    yfinance's institutional_holders view, which is itself derived from 13F
    filings and already includes a QoQ pctChange field per holder.
    """
    t = yf.Ticker(ticker)
    holders_df = t.institutional_holders
    top_holders = []
    if holders_df is not None and not holders_df.empty:
        for _, row in holders_df.iterrows():
            top_holders.append(
                {
                    "holder": row.get("Holder"),
                    "shares": int(row["Shares"]) if row.get("Shares") is not None else None,
                    "pct_held": row.get("pctHeld"),
                    "value": row.get("Value"),
                    "pct_change_qoq": row.get("pctChange"),
                    "date_reported": row["Date Reported"].strftime("%Y-%m-%d")
                    if hasattr(row.get("Date Reported"), "strftime")
                    else str(row.get("Date Reported")),
                }
            )

    major = t.major_holders
    institutions_pct_held = None
    institutions_count = None
    if major is not None and not major.empty and "Value" in major.columns:
        if "institutionsPercentHeld" in major.index:
            institutions_pct_held = major.loc["institutionsPercentHeld", "Value"]
        if "institutionsCount" in major.index:
            institutions_count = major.loc["institutionsCount", "Value"]

    return {
        "ticker": ticker,
        "top_holders": top_holders,
        "institutions_pct_held": institutions_pct_held,
        "institutions_count": institutions_count,
    }


def get_recent_filings(ticker: str) -> list[dict]:
    """Last 3 material events from 8-K filings via edgartools."""
    _ensure_edgar_identity()
    company = Company(ticker)
    filings = company.get_filings(form="8-K").head(3)

    results = []
    for filing in filings:
        snippet = ""
        try:
            text = " ".join(filing.obj().text().split())
            item_start = text.find("Item ")
            snippet = text[item_start if item_start != -1 else 0 :][:500]
        except Exception:
            snippet = ""
        results.append(
            {
                "filing_date": filing.filing_date.isoformat(),
                "accession_no": filing.accession_no,
                "items": filing.items.split(",") if getattr(filing, "items", None) else [],
                "snippet": snippet,
            }
        )
    return results


def get_news_sentiment(ticker: str) -> dict:
    """Last 30 days of news plus a sentiment score via Finnhub.

    Note: Finnhub's dedicated /news-sentiment endpoint returns 403 on this
    API plan (premium-only). Insider-sentiment (mspr, Monthly Share Purchase
    Ratio, -100..100) is available on the free tier and used as the
    real, working sentiment score instead.
    """
    client = _get_finnhub_client()
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=30)

    news = client.company_news(ticker, _from=start.isoformat(), to=today.isoformat())
    news_items = [
        {
            "headline": item.get("headline"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "url": item.get("url"),
            "datetime": datetime.fromtimestamp(item["datetime"], tz=timezone.utc).isoformat()
            if item.get("datetime")
            else None,
        }
        for item in news
    ]

    sentiment_start = today - timedelta(days=183)
    sentiment_detail = []
    sentiment_score = None
    try:
        insider_sentiment = client.stock_insider_sentiment(
            ticker, sentiment_start.isoformat(), today.isoformat()
        )
        sentiment_detail = insider_sentiment.get("data", [])
        if sentiment_detail:
            sentiment_score = sum(d["mspr"] for d in sentiment_detail) / len(sentiment_detail)
    except Exception:
        sentiment_detail = []
        sentiment_score = None

    return {
        "ticker": ticker,
        "period_days": 30,
        "news_count": len(news_items),
        "news": news_items,
        "sentiment_score": sentiment_score,
        "sentiment_score_note": "avg monthly share purchase ratio (mspr), -100..100, insider-trading based",
        "sentiment_detail": sentiment_detail,
    }


_ANALYST_OPINION_BLOCKLIST = [
    "price target",
    "rating",
    "upgrade",
    "downgrade",
    "outperform",
    "underperform",
    "bullish on",
    "bearish on",
    "top picks",
    "here's why",
    "should you buy",
]


def get_sector_news(sector_query: str) -> list[dict]:
    """Sector-level news for the last 30 days via Finnhub.

    Note: Finnhub's general_news only supports fixed categories
    (general/forex/crypto/merger), not arbitrary sector queries. This pools
    those categories and filters client-side for articles whose headline or
    summary mention any keyword from sector_query, then drops any article
    whose headline or summary contains analyst-opinion phrasing (price
    targets, ratings, upgrade/downgrade, etc.) per the reason-from-facts
    principle -- this system reasons from confirmed events, not Wall
    Street's predictions.
    """
    client = _get_finnhub_client()
    keywords = [w.lower() for w in sector_query.split() if len(w) > 2]
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    seen_ids = set()
    matches = []
    for category in ("general", "merger", "crypto"):
        try:
            articles = client.general_news(category, min_id=0)
        except Exception:
            continue
        for article in articles:
            article_id = article.get("id")
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            published = article.get("datetime")
            if not published:
                continue
            published_dt = datetime.fromtimestamp(published, tz=timezone.utc)
            if published_dt < cutoff:
                continue

            haystack = f"{article.get('headline', '')} {article.get('summary', '')}".lower()
            if not any(keyword in haystack for keyword in keywords):
                continue
            if any(phrase in haystack for phrase in _ANALYST_OPINION_BLOCKLIST):
                continue
            matches.append(
                    {
                        "headline": article.get("headline"),
                        "summary": article.get("summary"),
                        "source": article.get("source"),
                        "url": article.get("url"),
                        "datetime": published_dt.isoformat(),
                        "category": category,
                    }
                )

    matches.sort(key=lambda a: a["datetime"], reverse=True)
    return matches


_USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_USASPENDING_EARLIEST_DATE = "2007-10-01"

# USASpending's award_type_codes filter only accepts codes from a single
# award-type group per request (mixing a contract code with a grant code
# 400s: "'award_type_codes' must only contain types from one group"),
# confirmed against the live API. Contracts and grants each need their own call.
_CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]
_GRANT_TYPE_CODES = ["02", "03", "04", "05"]

_AWARD_FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "End Date", "Description"]
_SUBAWARD_FIELDS = [
    "Sub-Award ID",
    "Sub-Awardee Name",
    "Sub-Award Amount",
    "Sub-Award Date",
    "Sub-Award Description",
    "Prime Award ID",
    "Prime Recipient Name",
    "Awarding Agency",
]


_USASPENDING_RETRY_STATUS_CODES = {502, 504}
_USASPENDING_MAX_RETRIES = 2
_USASPENDING_RETRY_DELAY_SECONDS = 1


def _usaspending_search(payload: dict) -> list[dict]:
    for attempt in range(_USASPENDING_MAX_RETRIES + 1):
        try:
            resp = httpx.post(_USASPENDING_URL, json=payload, timeout=30)
            if resp.status_code in _USASPENDING_RETRY_STATUS_CODES and attempt < _USASPENDING_MAX_RETRIES:
                time.sleep(_USASPENDING_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json().get("results", [])
        except (httpx.HTTPError, ValueError):
            return []
    return []


def _usaspending_time_period(days_back: int) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days_back)
    start_str = max(start.isoformat(), _USASPENDING_EARLIEST_DATE)
    return {"start_date": start_str, "end_date": today.isoformat()}


def get_federal_contract_awards(keyword: str, days_back: int = 365, limit: int = 20) -> dict:
    """Prime federal contracts and grants matching a keyword via USASpending.gov
    (no API key required) -- the mega-project / prime-recipient layer for
    contract-layer tracing (see BUILD_PLAN Phase 3, Prompt 2). Covers things a
    general news search won't reliably catch: CHIPS Act grants, defense
    contracts, DOE/infrastructure awards.

    Queries contract-type and grant-type awards separately (see
    _CONTRACT_TYPE_CODES note) and merges them, tagged by award_category,
    sorted by award amount descending so the largest -- most likely to be the
    actual mega-project -- surfaces first.
    """
    time_period = [_usaspending_time_period(days_back)]
    awards = []
    for category, type_codes in (("contract", _CONTRACT_TYPE_CODES), ("grant", _GRANT_TYPE_CODES)):
        payload = {
            "filters": {
                "keywords": [keyword],
                "time_period": time_period,
                "award_type_codes": type_codes,
            },
            "fields": _AWARD_FIELDS,
            "limit": limit,
            "page": 1,
        }
        for result in _usaspending_search(payload):
            awards.append(
                {
                    "award_category": category,
                    "award_id": result.get("Award ID"),
                    "recipient_name": result.get("Recipient Name"),
                    "award_amount": result.get("Award Amount"),
                    "awarding_agency": result.get("Awarding Agency"),
                    "start_date": result.get("Start Date"),
                    "end_date": result.get("End Date"),
                    "description": result.get("Description"),
                }
            )

    awards.sort(key=lambda a: a.get("award_amount") or 0, reverse=True)
    awards = awards[:limit]
    return {
        "keyword": keyword,
        "period_days": days_back,
        "award_count": len(awards),
        "awards": awards,
    }


def get_federal_subawards(keyword: str, days_back: int = 365, limit: int = 20) -> dict:
    """Subcontract-level awards matching a keyword via USASpending.gov (no API
    key required) -- the actual supplier layer beneath a prime contract/grant
    (BUILD_PLAN Phase 3, Prompt 2, step 2).

    Confirmed live: subawards are directly keyword-searchable on this same
    endpoint (`"subawards": true` + the same `keywords` filter) -- no need to
    look up a prime award ID first. Same contract/grant type-code split and
    merge as get_federal_contract_awards.
    """
    time_period = [_usaspending_time_period(days_back)]
    subawards = []
    for category, type_codes in (("contract", _CONTRACT_TYPE_CODES), ("grant", _GRANT_TYPE_CODES)):
        payload = {
            "subawards": True,
            "filters": {
                "keywords": [keyword],
                "time_period": time_period,
                "award_type_codes": type_codes,
            },
            "fields": _SUBAWARD_FIELDS,
            "limit": limit,
            "page": 1,
        }
        for result in _usaspending_search(payload):
            subawards.append(
                {
                    "award_category": category,
                    "subaward_id": result.get("Sub-Award ID"),
                    "subawardee_name": result.get("Sub-Awardee Name"),
                    "subaward_amount": result.get("Sub-Award Amount"),
                    "subaward_date": result.get("Sub-Award Date"),
                    "description": result.get("Sub-Award Description"),
                    "prime_award_id": result.get("Prime Award ID"),
                    "prime_recipient_name": result.get("Prime Recipient Name"),
                    "awarding_agency": result.get("Awarding Agency"),
                }
            )

    subawards.sort(key=lambda a: a.get("subaward_amount") or 0, reverse=True)
    subawards = subawards[:limit]
    return {
        "keyword": keyword,
        "period_days": days_back,
        "subaward_count": len(subawards),
        "subawards": subawards,
    }

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from sqlalchemy.orm import Session
from fastapi import FastAPI, HTTPException, Response, status
from fastapi import Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from .config import ConfigurationError, require_moralis_api_key
from .database import SessionLocal, get_db, init_db
from .adapters import MoralisApiError
from .logging_config import configure_logging
from .models import (
    CreateExchangeSourceRequest,
    CreateWalletSourceRequest,
    DefiCoverageResponse,
    DefiPortfolio,
    HealthResponse,
    PortfolioHistory,
    PortfolioHoldings,
    PortfolioSummary,
    Source,
    SyncRunResponse,
)
from .store import (
    create_exchange_source,
    create_wallet_source,
    delete_source,
    get_defi_portfolio,
    get_summary,
    list_history,
    list_holdings,
    list_sources,
    query_monad_defi_coverage,
    run_manual_sync,
    seed_database,
)

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    try:
        require_moralis_api_key()
    except ConfigurationError:
        logger.error("Portfolio Tracker API startup aborted: MORALIS_API_KEY is not configured")
        raise
    init_db()
    with SessionLocal() as session:
        seed_database(session)
    logger.info("Portfolio Tracker API started")
    yield


# The backend stays thin: routes expose typed portfolio data while persistence
# and mutation behavior live in the SQLite-backed store layer.
app = FastAPI(title="Portfolio Tracker API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Vite serves the React app from 5173 during local development.
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error. See data/logs/errors.log for details."},
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/sources", response_model=list[Source])
def api_list_sources(db: Session = Depends(get_db)) -> list[Source]:
    return list_sources(db)


@app.post("/api/sources/wallets", response_model=Source, status_code=status.HTTP_201_CREATED)
def add_wallet_source(payload: CreateWalletSourceRequest, db: Session = Depends(get_db)) -> Source:
    try:
        return create_wallet_source(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.post("/api/sources/exchanges", response_model=Source, status_code=status.HTTP_201_CREATED)
def add_exchange_source(payload: CreateExchangeSourceRequest, db: Session = Depends(get_db)) -> Source:
    try:
        return create_exchange_source(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.delete("/api/sources/{source_id}", response_class=Response, status_code=status.HTTP_204_NO_CONTENT)
def remove_source(source_id: str, db: Session = Depends(get_db)) -> Response:
    if not delete_source(db, source_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/sync/run", response_model=SyncRunResponse)
def manual_sync(db: Session = Depends(get_db)) -> SyncRunResponse:
    return run_manual_sync(db)


@app.get("/api/defi/coverage/monad", response_model=DefiCoverageResponse)
def monad_defi_coverage() -> DefiCoverageResponse:
    try:
        return query_monad_defi_coverage()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except MoralisApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@app.get("/api/defi/positions", response_model=DefiPortfolio)
def defi_positions(db: Session = Depends(get_db)) -> DefiPortfolio:
    return get_defi_portfolio(db)


# Portfolio endpoints return already-normalized data so the frontend can focus
# on filtering, aggregation, and presentation.
@app.get("/api/portfolio/summary", response_model=PortfolioSummary)
def portfolio_summary(db: Session = Depends(get_db)) -> PortfolioSummary:
    return get_summary(db)


@app.get("/api/portfolio/history", response_model=PortfolioHistory)
def portfolio_history(db: Session = Depends(get_db)) -> PortfolioHistory:
    return PortfolioHistory(points=list_history(db))


@app.get("/api/portfolio/holdings", response_model=PortfolioHoldings)
def portfolio_holdings(db: Session = Depends(get_db)) -> PortfolioHoldings:
    return PortfolioHoldings(holdings=list_holdings(db))

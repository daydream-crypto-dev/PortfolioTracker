import type {
  CreateWalletSourcePayload,
  DefiPortfolioResponse,
  PortfolioHistoryResponse,
  PortfolioHoldingsResponse,
  PortfolioSummary,
  Source,
  SyncRunResponse
} from "../types";

// All paths are relative so Vite can proxy `/api` to FastAPI in development,
// while a future production build can be served from the same backend origin.
async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

async function sendJson<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? message;
    } catch {
      // Keep the status text when the server returns an empty body.
    }
    throw new Error(`Request failed: ${message}`);
  }

  if (response.status === 204) {
    // DELETE returns no JSON body, but callers still want a resolved promise.
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function getSources(): Promise<Source[]> {
  return fetchJson<Source[]>("/api/sources");
}

export function getPortfolioSummary(): Promise<PortfolioSummary> {
  return fetchJson<PortfolioSummary>("/api/portfolio/summary");
}

export function getPortfolioHistory(): Promise<PortfolioHistoryResponse> {
  return fetchJson<PortfolioHistoryResponse>("/api/portfolio/history");
}

export function getPortfolioHoldings(): Promise<PortfolioHoldingsResponse> {
  return fetchJson<PortfolioHoldingsResponse>("/api/portfolio/holdings");
}

export function getDefiPortfolio(): Promise<DefiPortfolioResponse> {
  return fetchJson<DefiPortfolioResponse>("/api/defi/positions");
}

export function createWalletSource(payload: CreateWalletSourcePayload): Promise<Source> {
  return sendJson<Source>("/api/sources/wallets", "POST", payload);
}

export function deleteSource(sourceId: string): Promise<void> {
  return sendJson<void>(`/api/sources/${sourceId}`, "DELETE");
}

export function runManualSync(): Promise<SyncRunResponse> {
  return sendJson<SyncRunResponse>("/api/sync/run", "POST");
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, FormEvent, useMemo, useState } from "react";

import {
  createWalletSource,
  deleteSource,
  getDefiPortfolio,
  getPortfolioHistory,
  getPortfolioHoldings,
  getPortfolioSummary,
  getSources,
  runManualSync
} from "./api/client";
import { AllocationPieChart } from "./components/AllocationPieChart";
import { PortfolioChart } from "./components/PortfolioChart";
import type {
  ChartPoint,
  DefiPosition,
  DefiProtocolPositionGroup,
  Holding,
  Source,
  VenueOption
} from "./types";

type TimeRangeId = "1d" | "30d" | "90d" | "1y" | "all";
type AppPage = "dashboard" | "connections";
type AddConnectionType = "wallet" | "exchange";
type WalletNetwork = "evm" | "solana" | "sui";
type ExchangeProvider = "okx" | "binance";

interface AggregatedHolding {
  id: string;
  asset_symbol: string;
  asset_name: string;
  logo_url?: string | null;
  chain_id: string | null;
  chain_name: string | null;
  venue_id: string;
  venue_label: string;
  venues: Array<{ id: string; label: string }>;
  quantity: number;
  price_usd: number;
  value_usd: number;
  rows: Holding[];
}

interface AggregatedDefiPosition {
  id: string;
  category: DefiPosition["category"];
  asset_symbol: string;
  asset_name: string;
  logo_url?: string | null;
  quantity: number;
  price_usd: number;
  value_usd: number;
  display_value_usd: number;
  apy?: number | null;
  unlock_time?: string | null;
  rows: DefiPosition[];
}

const TIME_RANGES: Array<{ id: TimeRangeId; label: string; hours: number | null }> = [
  { id: "1d", label: "1D", hours: 24 },
  { id: "30d", label: "30D", hours: 30 * 24 },
  { id: "90d", label: "90D", hours: 90 * 24 },
  { id: "1y", label: "1Y", hours: 365 * 24 },
  { id: "all", label: "All", hours: null }
];

const PORTFOLIO_REFETCH_INTERVAL_MS = 30_000;
const LOW_VALUE_ASSET_THRESHOLD_USD = 1;
const LIVE_QUERY_OPTIONS = {
  refetchInterval: PORTFOLIO_REFETCH_INTERVAL_MS,
  refetchOnWindowFocus: "always" as const
};
const PROTOCOL_LOGOS: Record<string, { src: string; badgeClassName: string; imageClassName: string }> = {
  neverland: {
    src: "/monad/protocols/neverland.svg",
    badgeClassName: "border-violet-950 bg-[#27002f]",
    imageClassName: "h-5 w-5"
  },
  leverup: {
    src: "/monad/protocols/leverup.png",
    badgeClassName: "border-lime-300 bg-lime-300",
    imageClassName: "h-full w-full"
  },
  townsquare: {
    src: "/monad/protocols/townsquare.svg",
    badgeClassName: "border-violet-200 bg-white",
    imageClassName: "h-full w-full"
  },
  curvance: {
    src: "/monad/protocols/curvance.svg",
    badgeClassName: "border-violet-200 bg-white",
    imageClassName: "h-full w-full"
  },
  perpl: {
    src: "/monad/protocols/perpl.svg",
    badgeClassName: "border-slate-300 bg-white",
    imageClassName: "h-4 w-4"
  }
};
const COMMON_ASSET_LOGOS: Record<string, string> = {
  BNB: "/assets/crypto/bnb.png",
  BONK: "/assets/crypto/bonk.jpg",
  BTC: "/assets/crypto/btc.png",
  FLOKI: "/assets/crypto/floki.png",
  MON: "/monad/assets/mon.png",
  PEPE: "/assets/crypto/pepe.png",
  SHIB: "/assets/crypto/shib.png",
  USDC: "/assets/crypto/usdc.png",
  USDT: "/assets/crypto/usdt.png"
};
const MONAD_ASSET_LOGOS: Record<string, string> = {
  AUSD: "/monad/assets/ausd.png",
  CWMON: "/monad/assets/mon.png",
  DUST: "/monad/assets/dust.svg",
  LVMON: "/monad/assets/lvmon.png",
  MON: "/monad/assets/mon.png",
  NPAUSD_8OCT2026: "/monad/assets/ausd.png",
  USDC: "/assets/crypto/usdc.png",
  USDT0: "/assets/crypto/usdt.png",
  VARIABLEDEBTNPAUSD: "/monad/assets/ausd.png",
  WBNB: "/assets/crypto/bnb.png",
  WMON: "/monad/assets/mon.png"
};

const WALLET_NETWORKS: Array<{
  id: WalletNetwork;
  label: string;
  description: string;
  supported: boolean;
}> = [
  {
    id: "evm",
    label: "EVM",
    description: "Scans Monad and Ethereum for now.",
    supported: true
  },
  {
    id: "solana",
    label: "Solana",
    description: "Planned for a future adapter.",
    supported: false
  },
  {
    id: "sui",
    label: "Sui",
    description: "Planned for a future adapter.",
    supported: false
  }
];

const EXCHANGE_PROVIDERS: Array<{
  id: ExchangeProvider;
  label: string;
  description: string;
  supported: boolean;
}> = [
  {
    id: "okx",
    label: "OKX",
    description: "Read-only API balance import.",
    supported: true
  },
  {
    id: "binance",
    label: "Binance",
    description: "Planned for a future adapter.",
    supported: false
  }
];

function formatCurrency(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits
  }).format(value);
}

function formatQuantity(value: number): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: value >= 100 ? 2 : 6
  }).format(value);
}

function shortAddress(address: string): string {
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

function normalizedAssetSymbol(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "");
}

function assetFallbackText(symbol: string): string {
  const normalized = normalizedAssetSymbol(symbol);
  return (normalized || symbol.trim()).slice(0, 3).toUpperCase();
}

function resolveAssetLogoSrc({
  symbol,
  logoUrl,
  chainId,
  venueId,
  sourceType
}: {
  symbol: string;
  logoUrl?: string | null;
  chainId?: string | null;
  venueId?: string | null;
  sourceType?: Source["type"];
}): string | null {
  const normalized = normalizedAssetSymbol(symbol);
  const isMonadAsset = chainId === "monad" || venueId === "monad";
  if (normalized === "MON" && (isMonadAsset || venueId === "okx")) {
    return MONAD_ASSET_LOGOS.MON;
  }

  if (logoUrl) {
    return logoUrl;
  }

  if (isMonadAsset) {
    if (MONAD_ASSET_LOGOS[normalized]) {
      return MONAD_ASSET_LOGOS[normalized];
    }
    if (normalized.includes("AUSD")) {
      return MONAD_ASSET_LOGOS.AUSD;
    }
  }

  const isExchangeAsset = sourceType === "exchange" || venueId === "okx" || (!chainId && venueId !== "monad");
  if (isExchangeAsset) {
    return COMMON_ASSET_LOGOS[normalized] ?? null;
  }

  if (normalized === "USDC" || normalized === "USDT") {
    return COMMON_ASSET_LOGOS[normalized];
  }

  return null;
}

function getAssetLogoBadgeClass(symbol: string, logoSrc: string | null, chainId?: string | null, venueId?: string | null): string {
  const normalized = normalizedAssetSymbol(symbol);
  const isMonadAsset = chainId === "monad" || venueId === "monad";
  if (isMonadAsset && normalized === "DUST" && logoSrc === MONAD_ASSET_LOGOS.DUST) {
    return "border-violet-950 bg-[#27002f] p-1";
  }

  return "border-slate-200 bg-white";
}

function sumHoldings(holdings: Holding[]): number {
  return holdings.reduce((total, holding) => total + holding.value_usd, 0);
}

function sumDefiPositions(positions: DefiPosition[]): number {
  return positions.reduce((total, position) => total + position.value_usd, 0);
}

function weightedPrice(quantity: number, valueUsd: number): number {
  return quantity === 0 ? 0 : Math.abs(valueUsd) / Math.abs(quantity);
}

function groupValues(
  holdings: Holding[],
  getKey: (holding: Holding) => string
): Array<{ label: string; value: number }> {
  const grouped = new Map<string, number>();

  holdings.forEach((holding) => {
    const key = getKey(holding);
    grouped.set(key, (grouped.get(key) ?? 0) + holding.value_usd);
  });

  return Array.from(grouped.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((left, right) => right.value - left.value);
}

function getSelectedIds(current: Set<string> | null, fallbackIds: string[]): Set<string> {
  // Null means "use the backend/default enabled set"; a Set means the user has
  // manually changed the filters during this session.
  return current ?? new Set(fallbackIds);
}

function getExchangeVenueId(source?: Source): string {
  return source?.exchange ?? source?.provider.toLowerCase() ?? "exchange";
}

function getExchangeVenueLabel(source?: Source): string {
  return source?.provider ?? "Exchange";
}

function getHoldingVenueId(holding: Holding, sourceById: Map<string, Source>): string {
  // Wallet holdings carry a chain_id; exchange holdings instead inherit their
  // venue from the source, e.g. OKX.
  if (holding.chain_id) {
    return holding.chain_id;
  }

  return getExchangeVenueId(sourceById.get(holding.source_id));
}

function getHoldingVenueLabel(holding: Holding, sourceById: Map<string, Source>): string {
  if (holding.chain_name) {
    return holding.chain_name;
  }

  return getExchangeVenueLabel(sourceById.get(holding.source_id));
}

function getDefiVenueLabel(position: DefiPosition): string {
  return position.chain_name || position.chain_id;
}

function getVenueLogoText(venueId: string, label: string): string {
  const logoTextById: Record<string, string> = {
    monad: "MON",
    ethereum: "ETH",
    okx: "OKX"
  };

  return logoTextById[venueId] ?? label.slice(0, 3).toUpperCase();
}

function getVenueLogoClass(venueId: string): string {
  const classById: Record<string, string> = {
    monad: "border-violet-200 bg-violet-50 text-violet-700",
    ethereum: "border-slate-300 bg-slate-100 text-slate-700",
    okx: "border-zinc-300 bg-zinc-900 text-white"
  };

  return classById[venueId] ?? "border-slate-200 bg-slate-50 text-slate-700";
}

function VenuePill({ venueId, label }: { venueId: string; label: string }) {
  return (
    <span
      aria-label={label}
      title={label}
      className={`inline-flex h-8 min-w-12 items-center justify-center rounded-full border px-2 text-xs font-bold ${getVenueLogoClass(venueId)}`}
    >
      {getVenueLogoText(venueId, label)}
    </span>
  );
}

function AssetLogo({
  symbol,
  name,
  logoUrl,
  chainId,
  venueId,
  sourceType,
  size = "md"
}: {
  symbol: string;
  name: string;
  logoUrl?: string | null;
  chainId?: string | null;
  venueId?: string | null;
  sourceType?: Source["type"];
  size?: "sm" | "md";
}) {
  const logoSrc = resolveAssetLogoSrc({ symbol, logoUrl, chainId, venueId, sourceType });
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const sizeClass = size === "sm" ? "h-7 w-7" : "h-9 w-9";
  const textClass = size === "sm" ? "text-[10px]" : "text-xs";
  const shouldShowImage = logoSrc && failedSrc !== logoSrc;
  const badgeClass = getAssetLogoBadgeClass(symbol, logoSrc, chainId, venueId);

  return (
    <span
      aria-label={`${name} logo`}
      className={`flex ${sizeClass} shrink-0 items-center justify-center overflow-hidden rounded-full border text-slate-500 shadow-sm ${badgeClass}`}
    >
      {shouldShowImage ? (
        <img
          src={logoSrc}
          alt=""
          className="h-full w-full object-contain"
          referrerPolicy="no-referrer"
          onError={() => setFailedSrc(logoSrc)}
        />
      ) : (
        <span className={`font-bold ${textClass}`}>{assetFallbackText(symbol)}</span>
      )}
    </span>
  );
}

function ProtocolLogo({
  protocolSlug,
  protocolName,
  size = "md"
}: {
  protocolSlug: string;
  protocolName: string;
  size?: "sm" | "md";
}) {
  const logo = PROTOCOL_LOGOS[protocolSlug.toLowerCase()];
  const sizeClass = size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const fallbackTextClass = size === "sm" ? "text-[10px]" : "text-xs";

  return (
    <span
      className={`flex ${sizeClass} shrink-0 items-center justify-center overflow-hidden rounded-full border shadow-sm ${
        logo?.badgeClassName ?? "border-violet-200 bg-violet-700"
      }`}
    >
      {logo ? (
        <img src={logo.src} alt={`${protocolName} logo`} className={`object-contain ${logo.imageClassName}`} />
      ) : (
        <span className={`font-bold text-white ${fallbackTextClass}`}>{protocolName.slice(0, 1)}</span>
      )}
    </span>
  );
}

function categoryLabel(category: DefiPosition["category"]): string {
  const labels: Record<DefiPosition["category"], string> = {
    deposit: "Deposited",
    borrow: "Borrowed",
    reward: "Rewards",
    locked: "Locked",
    staked: "Staked",
    perp: "Perpetuals"
  };

  return labels[category];
}

function categoryOrder(category: DefiPosition["category"]): number {
  const order: Record<DefiPosition["category"], number> = {
    deposit: 0,
    borrow: 1,
    reward: 2,
    locked: 3,
    staked: 4,
    perp: 5
  };

  return order[category];
}

function positionDisplaySymbol(position: DefiPosition): string {
  if (position.category === "locked" && position.token_id) {
    return `ve${position.asset_symbol}#${position.token_id}`;
  }

  return position.asset_symbol;
}

function aggregateDefiDisplaySymbol(position: AggregatedDefiPosition): string {
  if (position.category === "locked") {
    return `ve${position.asset_symbol}`;
  }

  return position.asset_symbol;
}

function sourceBreakdownLabel(sourceId: string, fallbackLabel: string, sourceById?: Map<string, Source>): string {
  const source = sourceById?.get(sourceId);
  if (source?.address) {
    return `${source.label} (${shortAddress(source.address)})`;
  }

  return source?.label ?? fallbackLabel;
}

function uniqueSourceCount(rows: Array<{ source_id: string }>): number {
  return new Set(rows.map((row) => row.source_id)).size;
}

function formatUnlockTime(position: DefiPosition): string {
  if (!position.unlock_time) {
    return "N/A";
  }

  return new Date(position.unlock_time).toLocaleString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatAggregateDefiDetail(position: AggregatedDefiPosition): string {
  if (position.category === "locked") {
    return position.unlock_time ? formatUnlockTime({ ...position.rows[0], unlock_time: position.unlock_time }) : "Mixed";
  }

  return position.apy === null || position.apy === undefined ? "N/A" : `${position.apy.toFixed(2)}%`;
}

function isCanonicalMonadNativeHolding(holding: Holding, venueId: string): boolean {
  return (
    normalizedAssetSymbol(holding.asset_symbol) === "MON" &&
    holding.asset_name.trim().toUpperCase() === "MON" &&
    (venueId === "monad" || venueId === "okx")
  );
}

function holdingAggregationKey(holding: Holding, sourceById: Map<string, Source>): string {
  const venueId = getHoldingVenueId(holding, sourceById);
  if (isCanonicalMonadNativeHolding(holding, venueId)) {
    return "canonical|monad-native-mon";
  }

  return [venueId, holding.chain_id ?? "exchange", holding.asset_symbol, holding.asset_name].join("|");
}

function mergeHoldingVenue(
  venues: Array<{ id: string; label: string }>,
  venueId: string,
  venueLabel: string
): Array<{ id: string; label: string }> {
  if (venues.some((venue) => venue.id === venueId)) {
    return venues;
  }

  return [...venues, { id: venueId, label: venueLabel }].sort((left, right) => {
    if (left.id === "monad") {
      return -1;
    }
    if (right.id === "monad") {
      return 1;
    }
    return left.label.localeCompare(right.label);
  });
}

function aggregateHoldings(holdings: Holding[], sourceById: Map<string, Source>): AggregatedHolding[] {
  const grouped = new Map<string, AggregatedHolding>();

  holdings.forEach((holding) => {
    const venueId = getHoldingVenueId(holding, sourceById);
    const venueLabel = getHoldingVenueLabel(holding, sourceById);
    const key = holdingAggregationKey(holding, sourceById);
    const existing = grouped.get(key);

    if (existing) {
      existing.logo_url = existing.logo_url ?? holding.logo_url;
      existing.chain_id = existing.chain_id ?? holding.chain_id;
      existing.chain_name = existing.chain_name ?? holding.chain_name;
      existing.venues = mergeHoldingVenue(existing.venues, venueId, venueLabel);
      existing.quantity += holding.quantity;
      existing.value_usd += holding.value_usd;
      existing.price_usd = weightedPrice(existing.quantity, existing.value_usd);
      existing.rows.push(holding);
      return;
    }

    grouped.set(key, {
      id: key,
      asset_symbol: holding.asset_symbol,
      asset_name: holding.asset_name,
      logo_url: holding.logo_url,
      chain_id: holding.chain_id,
      chain_name: holding.chain_name,
      venue_id: venueId,
      venue_label: venueLabel,
      venues: [{ id: venueId, label: venueLabel }],
      quantity: holding.quantity,
      price_usd: holding.price_usd,
      value_usd: holding.value_usd,
      rows: [holding]
    });
  });

  return Array.from(grouped.values()).map((holding) => ({
    ...holding,
    price_usd: weightedPrice(holding.quantity, holding.value_usd),
    rows: [...holding.rows].sort((left, right) => Math.abs(right.value_usd) - Math.abs(left.value_usd))
  }));
}

function aggregateDefiPositions(positions: DefiPosition[], protocolSlug: string): AggregatedDefiPosition[] {
  const grouped = new Map<string, AggregatedDefiPosition>();

  positions.forEach((position) => {
    const key = [protocolSlug, position.category, position.asset_symbol, position.asset_name].join("|");
    const existing = grouped.get(key);

    if (existing) {
      existing.logo_url = existing.logo_url ?? position.logo_url;
      existing.quantity += position.quantity;
      existing.value_usd += position.value_usd;
      existing.display_value_usd = Math.abs(existing.value_usd);
      existing.price_usd = weightedPrice(existing.quantity, existing.value_usd);
      existing.rows.push(position);
      return;
    }

    grouped.set(key, {
      id: key,
      category: position.category,
      asset_symbol: position.asset_symbol,
      asset_name: position.asset_name,
      logo_url: position.logo_url,
      quantity: position.quantity,
      price_usd: position.price_usd,
      value_usd: position.value_usd,
      display_value_usd: position.display_value_usd,
      apy: position.apy,
      unlock_time: position.unlock_time,
      rows: [position]
    });
  });

  return Array.from(grouped.values()).map((position) => ({
    ...position,
    price_usd: weightedPrice(position.quantity, position.value_usd),
    display_value_usd: Math.abs(position.value_usd),
    apy: position.rows.every((row) => row.apy === position.rows[0]?.apy) ? position.rows[0]?.apy : null,
    unlock_time: position.rows.every((row) => row.unlock_time === position.rows[0]?.unlock_time)
      ? position.rows[0]?.unlock_time
      : null,
    rows: [...position.rows].sort((left, right) => Math.abs(right.value_usd) - Math.abs(left.value_usd))
  }));
}

function filterChartRange(points: ChartPoint[], range: TimeRangeId): ChartPoint[] {
  const rangeConfig = TIME_RANGES.find((item) => item.id === range);
  if (!rangeConfig?.hours || points.length === 0) {
    return points;
  }

  const lastTimestamp = new Date(points[points.length - 1].timestamp).getTime();
  const cutoff = lastTimestamp - rangeConfig.hours * 60 * 60 * 1000;
  return points.filter((point) => new Date(point.timestamp).getTime() >= cutoff);
}

function StatusBadge({ status }: { status: Source["status"] }) {
  const classes = {
    ok: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    error: "border-red-200 bg-red-50 text-red-700"
  };

  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${classes[status]}`}>
      {status}
    </span>
  );
}

function MetricCard({
  label,
  value,
  helper,
  helperClassName = "text-slate-500"
}: {
  label: string;
  value: string;
  helper?: string;
  helperClassName?: string;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
      {helper ? <p className={`mt-1 text-sm ${helperClassName}`}>{helper}</p> : null}
    </section>
  );
}

function PageNav({
  activePage,
  onChange
}: {
  activePage: AppPage;
  onChange: (page: AppPage) => void;
}) {
  const pages: Array<{ id: AppPage; label: string }> = [
    { id: "dashboard", label: "Dashboard" },
    { id: "connections", label: "Connections" }
  ];

  return (
    <nav className="inline-flex rounded-lg bg-slate-100 p-1">
      {pages.map((page) => (
        <button
          key={page.id}
          type="button"
          onClick={() => onChange(page.id)}
          className={`rounded-md px-3 py-1.5 text-sm font-semibold transition ${
            activePage === page.id ? "bg-white text-slate-950 shadow-sm" : "text-slate-500 hover:text-slate-800"
          }`}
        >
          {page.label}
        </button>
      ))}
    </nav>
  );
}

function OptionButton<T extends string>({
  option,
  selected,
  onSelect
}: {
  option: { id: T; label: string; description: string; supported: boolean };
  selected: boolean;
  onSelect: (id: T) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(option.id)}
      className={`rounded-lg border p-4 text-left transition ${
        selected ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"
      }`}
    >
      <span className="flex items-center justify-between gap-3">
        <span className="font-semibold text-slate-950">{option.label}</span>
        {!option.supported ? (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">
            Later
          </span>
        ) : null}
      </span>
      <span className="mt-1 block text-sm text-slate-500">{option.description}</span>
    </button>
  );
}

function DefiProtocolCard({
  group,
  sourceById
}: {
  group: DefiProtocolPositionGroup;
  sourceById: Map<string, Source>;
}) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const aggregatedPositions = useMemo(
    () => aggregateDefiPositions(group.positions, group.protocol_slug),
    [group.positions, group.protocol_slug]
  );
  const categories = Array.from(new Set(aggregatedPositions.map((position) => position.category))).sort(
    (left, right) => categoryOrder(left) - categoryOrder(right)
  );
  const toggleExpandedRow = (rowId: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  };

  return (
    <article className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-2 border-b border-violet-100 bg-violet-50/70 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <ProtocolLogo protocolSlug={group.protocol_slug} protocolName={group.protocol_name} />
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-slate-950">{group.protocol_name}</h3>
              <a
                href={group.protocol_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs font-semibold text-violet-700 hover:text-violet-900"
              >
                Open
              </a>
            </div>
            {group.health_factor ? (
              <p className="mt-1 text-xs font-medium text-slate-600">
                Health factor: <span className="text-amber-700">{group.health_factor.toFixed(2)}</span>
              </p>
            ) : null}
          </div>
        </div>
        <p
          className={`text-lg font-semibold tabular-nums ${
            group.total_value_usd >= 0 ? "text-violet-700" : "text-red-600"
          }`}
        >
          {formatCurrency(group.total_value_usd)}
        </p>
      </div>

      <div className="divide-y divide-slate-100">
        {categories.map((category) => {
          const positions = aggregatedPositions
            .filter((position) => position.category === category)
            .sort((left, right) => Math.abs(right.value_usd) - Math.abs(left.value_usd));

          return (
            <div key={category}>
              <div className="grid grid-cols-[1.1fr_1fr_0.8fr_1fr] gap-3 border-b border-slate-100 bg-slate-50 px-5 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <span>{categoryLabel(category)}</span>
                <span>Balance</span>
                <span>{category === "locked" ? "Unlock time" : "APY"}</span>
                <span className="text-right">USD value</span>
              </div>
              {positions.map((position) => {
                const isExpanded = expandedRows.has(position.id);

                return (
                  <Fragment key={position.id}>
                    <button
                      type="button"
                      onClick={() => toggleExpandedRow(position.id)}
                      className="grid w-full grid-cols-[1.1fr_1fr_0.8fr_1fr] items-center gap-3 px-5 py-4 text-left text-sm hover:bg-slate-50"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <AssetLogo
                          symbol={position.asset_symbol}
                          name={position.asset_name}
                          logoUrl={position.logo_url}
                          chainId={position.rows[0]?.chain_id}
                          size="sm"
                        />
                        <div className="min-w-0">
                          <p className="font-semibold text-slate-900">
                            {aggregateDefiDisplaySymbol(position)}
                          </p>
                          <p className="truncate text-xs text-slate-500">
                            {position.asset_name} · {uniqueSourceCount(position.rows)} source
                            {uniqueSourceCount(position.rows) === 1 ? "" : "s"}
                          </p>
                        </div>
                      </div>
                      <p className="tabular-nums text-slate-700">
                        {formatQuantity(position.quantity)} {position.asset_symbol}
                      </p>
                      <p className="tabular-nums text-slate-500">{formatAggregateDefiDetail(position)}</p>
                      <p
                        className={`text-right font-semibold tabular-nums ${
                          position.value_usd < 0 ? "text-red-600" : "text-slate-950"
                        }`}
                      >
                        {position.value_usd < 0
                          ? `-${formatCurrency(position.display_value_usd)}`
                          : formatCurrency(position.display_value_usd)}
                      </p>
                    </button>
                    {isExpanded ? (
                      <div className="border-t border-slate-100 bg-slate-50 px-5 py-3">
                        <div className="space-y-2">
                          {position.rows.map((row) => (
                            <div
                              key={row.id}
                              className="grid grid-cols-[1.1fr_1fr_0.8fr_1fr] items-center gap-3 rounded-md bg-white px-3 py-2 text-xs"
                            >
                              <div className="min-w-0">
                                <p className="font-semibold text-slate-800">
                                  {sourceBreakdownLabel(row.source_id, row.source_label, sourceById)}
                                </p>
                                <div className="mt-1 flex items-center gap-2 text-slate-500">
                                  <AssetLogo
                                    symbol={row.asset_symbol}
                                    name={row.asset_name}
                                    logoUrl={row.logo_url}
                                    chainId={row.chain_id}
                                    size="sm"
                                  />
                                  <p className="truncate">{positionDisplaySymbol(row)}</p>
                                </div>
                              </div>
                              <p className="tabular-nums text-slate-600">
                                {formatQuantity(row.quantity)} {row.asset_symbol}
                              </p>
                              <p className="tabular-nums text-slate-500">
                                {row.category === "locked"
                                  ? formatUnlockTime(row)
                                  : row.apy === null || row.apy === undefined
                                    ? "N/A"
                                    : `${row.apy.toFixed(2)}%`}
                              </p>
                              <p
                                className={`text-right font-semibold tabular-nums ${
                                  row.value_usd < 0 ? "text-red-600" : "text-slate-900"
                                }`}
                              >
                                {row.value_usd < 0
                                  ? `-${formatCurrency(row.display_value_usd)}`
                                  : formatCurrency(row.display_value_usd)}
                              </p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </Fragment>
                );
              })}
            </div>
          );
        })}
      </div>
    </article>
  );
}

function DefiSection({
  totalUsd,
  protocols,
  sourceById
}: {
  totalUsd: number;
  protocols: DefiProtocolPositionGroup[];
  sourceById: Map<string, Source>;
}) {
  const distributionTotal = protocols.reduce(
    (total, protocol) => total + Math.abs(protocol.total_value_usd),
    0
  );

  return (
    <section className="space-y-5">
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-2 border-b border-violet-100 bg-violet-50/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-lg font-semibold text-slate-950">DeFi distribution</h2>
          <p className={`text-lg font-semibold tabular-nums ${totalUsd >= 0 ? "text-violet-700" : "text-red-600"}`}>
            {formatCurrency(totalUsd)}
          </p>
        </div>

        {protocols.length > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {protocols.map((protocol) => {
              const percentage = distributionTotal
                ? (Math.abs(protocol.total_value_usd) / distributionTotal) * 100
                : 0;

              return (
                <div key={protocol.protocol_slug} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex items-center gap-2">
                    <ProtocolLogo
                      protocolSlug={protocol.protocol_slug}
                      protocolName={protocol.protocol_name}
                      size="sm"
                    />
                    <p className="font-semibold text-slate-900">{protocol.protocol_name}</p>
                  </div>
                  <p className="mt-3 text-xl font-semibold tabular-nums text-slate-950">
                    {formatCurrency(protocol.total_value_usd)}
                  </p>
                  <p className="mt-1 text-sm text-slate-500">{percentage.toFixed(2)}% of displayed DeFi</p>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="mt-4 rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-500">
            No DeFi positions found in the current filters. Run Sync now after adding a wallet.
          </p>
        )}
      </div>

      {protocols.map((protocol) => (
        <DefiProtocolCard key={protocol.protocol_slug} group={protocol} sourceById={sourceById} />
      ))}
    </section>
  );
}

function LoadingState() {
  return (
    <main className="min-h-screen bg-slate-50 p-6 text-slate-950">
      <div className="mx-auto max-w-7xl">
        <div className="rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
          <p className="text-sm font-medium text-slate-500">Loading portfolio data...</p>
        </div>
      </div>
    </main>
  );
}

function ErrorState({ error }: { error: unknown }) {
  return (
    <main className="min-h-screen bg-slate-50 p-6 text-slate-950">
      <div className="mx-auto max-w-7xl">
        <div className="rounded-lg border border-red-200 bg-red-50 p-8 text-red-900">
          <p className="text-sm font-semibold">Could not load portfolio data</p>
          <p className="mt-2 text-sm">{error instanceof Error ? error.message : String(error)}</p>
        </div>
      </div>
    </main>
  );
}

export default function App() {
  const queryClient = useQueryClient();
  // Iteration 1 reads four simple endpoints. Later iterations can change the
  // backend implementation without changing these query boundaries.
  const sourcesQuery = useQuery({ queryKey: ["sources"], queryFn: getSources, ...LIVE_QUERY_OPTIONS });
  const summaryQuery = useQuery({
    queryKey: ["portfolio-summary"],
    queryFn: getPortfolioSummary,
    ...LIVE_QUERY_OPTIONS
  });
  const historyQuery = useQuery({
    queryKey: ["portfolio-history"],
    queryFn: getPortfolioHistory,
    ...LIVE_QUERY_OPTIONS
  });
  const holdingsQuery = useQuery({
    queryKey: ["portfolio-holdings"],
    queryFn: getPortfolioHoldings,
    ...LIVE_QUERY_OPTIONS
  });
  const defiQuery = useQuery({ queryKey: ["defi-portfolio"], queryFn: getDefiPortfolio, ...LIVE_QUERY_OPTIONS });

  const [activeSourceIds, setActiveSourceIds] = useState<Set<string> | null>(null);
  const [activeVenueIds, setActiveVenueIds] = useState<Set<string> | null>(null);
  const [activeTimeRange, setActiveTimeRange] = useState<TimeRangeId>("1d");
  const [activePage, setActivePage] = useState<AppPage>("dashboard");
  const [addConnectionType, setAddConnectionType] = useState<AddConnectionType>("wallet");
  const [walletNetwork, setWalletNetwork] = useState<WalletNetwork>("evm");
  const [exchangeProvider, setExchangeProvider] = useState<ExchangeProvider>("okx");
  const [walletForm, setWalletForm] = useState({ label: "", address: "" });
  const [expandedHoldingRows, setExpandedHoldingRows] = useState<Set<string>>(new Set());
  const [hideLowValueAssets, setHideLowValueAssets] = useState(true);
  const [formMessage, setFormMessage] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  // Empty arrays let the derived UI state compute during the initial load.
  const sources = sourcesQuery.data ?? [];
  const holdings = holdingsQuery.data?.holdings ?? [];
  const history = historyQuery.data?.points ?? [];
  const defiProtocols = defiQuery.data?.protocols ?? [];

  const sourceById = useMemo(() => {
    return new Map(sources.map((source) => [source.id, source]));
  }, [sources]);

  const walletSources = useMemo(() => {
    // Source filters are wallet-only. Exchange inclusion is controlled by the
    // venue filter so OKX sits next to Monad and Ethereum.
    return sources.filter((source) => source.type === "wallet");
  }, [sources]);

  const venueOptions = useMemo<VenueOption[]>(() => {
    // Always show the MVP venues, even if current holdings are filtered down or
    // a user has not connected every provider yet.
    const venues = new Map<string, VenueOption>([
      ["monad", { id: "monad", label: "Monad", kind: "chain" }],
      ["ethereum", { id: "ethereum", label: "Ethereum", kind: "chain" }],
      ["okx", { id: "okx", label: "OKX", kind: "exchange" }]
    ]);

    holdings.forEach((holding) => {
      if (holding.chain_id) {
        venues.set(holding.chain_id, {
          id: holding.chain_id,
          label: holding.chain_name ?? holding.chain_id,
          kind: "chain"
        });
        return;
      }

      const source = sourceById.get(holding.source_id);
      const venueId = getExchangeVenueId(source);
      venues.set(venueId, {
        id: venueId,
        label: getExchangeVenueLabel(source),
        kind: "exchange"
      });
    });

    return Array.from(venues.values());
  }, [holdings, sourceById]);

  const selectedSourceIds = useMemo(
    () => getSelectedIds(activeSourceIds, walletSources.filter((source) => source.enabled).map((source) => source.id)),
    [activeSourceIds, walletSources]
  );

  const selectedVenueIds = useMemo(
    () => getSelectedIds(activeVenueIds, venueOptions.map((venue) => venue.id)),
    [activeVenueIds, venueOptions]
  );

  const chainVenueOptions = useMemo(() => {
    return venueOptions.filter((venue) => venue.kind === "chain");
  }, [venueOptions]);

  const exchangeVenueOptions = useMemo(() => {
    return venueOptions.filter((venue) => venue.kind === "exchange");
  }, [venueOptions]);

  const selectedChainVenueCount = useMemo(() => {
    return chainVenueOptions.filter((venue) => selectedVenueIds.has(venue.id)).length;
  }, [chainVenueOptions, selectedVenueIds]);

  const selectedExchangeVenueCount = useMemo(() => {
    return exchangeVenueOptions.filter((venue) => selectedVenueIds.has(venue.id)).length;
  }, [exchangeVenueOptions, selectedVenueIds]);

  const activeHoldings = useMemo(() => {
    // Apply both filter layers: wallet source selection, then chain/exchange
    // venue selection. Exchange holdings skip the wallet source filter.
    return holdings.filter((holding) => {
      const source = sourceById.get(holding.source_id);
      const sourceIsActive = source?.type === "wallet" ? selectedSourceIds.has(holding.source_id) : true;
      const venueIsActive = selectedVenueIds.has(getHoldingVenueId(holding, sourceById));
      return sourceIsActive && venueIsActive;
    });
  }, [holdings, selectedSourceIds, selectedVenueIds, sourceById]);

  const activeDefiProtocols = useMemo<DefiProtocolPositionGroup[]>(() => {
    return defiProtocols
      .map((protocol) => {
        const positions = protocol.positions.filter((position) => {
          const source = sourceById.get(position.source_id);
          const sourceIsActive = source?.type === "wallet" ? selectedSourceIds.has(position.source_id) : true;
          const venueIsActive = selectedVenueIds.has(position.chain_id);
          return sourceIsActive && venueIsActive;
        });
        const totalValue = Number(sumDefiPositions(positions).toFixed(2));
        const healthFactors = positions
          .map((position) => position.health_factor)
          .filter((value): value is number => value !== null && value !== undefined);

        return {
          ...protocol,
          positions,
          total_value_usd: totalValue,
          health_factor: healthFactors.length ? Math.min(...healthFactors) : null
        };
      })
      .filter((protocol) => protocol.positions.length > 0)
      .sort((left, right) => Math.abs(right.total_value_usd) - Math.abs(left.total_value_usd));
  }, [defiProtocols, selectedSourceIds, selectedVenueIds, sourceById]);

  const activeDefiPositions = useMemo(
    () => activeDefiProtocols.flatMap((protocol) => protocol.positions),
    [activeDefiProtocols]
  );

  const visibleHoldings = useMemo(() => {
    if (!hideLowValueAssets) {
      return activeHoldings;
    }

    return activeHoldings.filter((holding) => Math.abs(holding.value_usd) >= LOW_VALUE_ASSET_THRESHOLD_USD);
  }, [activeHoldings, hideLowValueAssets]);

  const visibleDefiProtocols = useMemo<DefiProtocolPositionGroup[]>(() => {
    return activeDefiProtocols
      .map((protocol) => {
        const positions = hideLowValueAssets
          ? protocol.positions.filter(
              (position) => Math.abs(position.value_usd) >= LOW_VALUE_ASSET_THRESHOLD_USD
            )
          : protocol.positions;
        const totalValue = Number(sumDefiPositions(positions).toFixed(2));
        const healthFactors = positions
          .map((position) => position.health_factor)
          .filter((value): value is number => value !== null && value !== undefined);

        return {
          ...protocol,
          positions,
          total_value_usd: totalValue,
          health_factor: healthFactors.length ? Math.min(...healthFactors) : null
        };
      })
      .filter((protocol) => protocol.positions.length > 0)
      .sort((left, right) => Math.abs(right.total_value_usd) - Math.abs(left.total_value_usd));
  }, [activeDefiProtocols, hideLowValueAssets]);

  const visibleDefiPositions = useMemo(
    () => visibleDefiProtocols.flatMap((protocol) => protocol.positions),
    [visibleDefiProtocols]
  );

  const allVisibleHistory = useMemo<ChartPoint[]>(() => {
    const totals = new Map<string, number>();

    // Stored history is per source plus chain/exchange. Summing after filters
    // lets the chart react instantly without refetching.
    history.forEach((point) => {
      const source = sourceById.get(point.source_id);
      if (source?.type === "wallet" && !selectedSourceIds.has(point.source_id)) {
        return;
      }

      const venueId = point.chain_id ?? getExchangeVenueId(source);
      if (!selectedVenueIds.has(venueId)) {
        return;
      }

      totals.set(point.timestamp, (totals.get(point.timestamp) ?? 0) + point.value_usd);
    });

    return Array.from(totals.entries())
      .map(([timestamp, totalUsd]) => ({ timestamp, totalUsd: Number(totalUsd.toFixed(2)) }))
      .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  }, [history, selectedSourceIds, selectedVenueIds, sourceById]);

  const chartHistory = useMemo(
    () => filterChartRange(allVisibleHistory, activeTimeRange),
    [activeTimeRange, allVisibleHistory]
  );

  const totalUsd = sumHoldings(activeHoldings) + sumDefiPositions(activeDefiPositions);
  const firstHistoryValue = chartHistory[0]?.totalUsd ?? totalUsd;
  const changeUsd = totalUsd - firstHistoryValue;
  const changePct = firstHistoryValue > 0 ? (changeUsd / firstHistoryValue) * 100 : 0;
  const trendIsPositive = changeUsd >= 0;
  const activeTimeRangeLabel = TIME_RANGES.find((range) => range.id === activeTimeRange)?.label ?? "1D";
  const selectedWalletNetwork = WALLET_NETWORKS.find((network) => network.id === walletNetwork);
  const selectedExchangeProvider = EXCHANGE_PROVIDERS.find((provider) => provider.id === exchangeProvider);
  const canSubmitWallet = selectedWalletNetwork?.supported === true;

  const byVenueSlices = useMemo(() => {
    const grouped = new Map<string, number>();

    visibleHoldings.forEach((holding) => {
      const label = getHoldingVenueLabel(holding, sourceById);
      grouped.set(label, (grouped.get(label) ?? 0) + holding.value_usd);
    });
    visibleDefiPositions.forEach((position) => {
      const label = getDefiVenueLabel(position);
      grouped.set(label, (grouped.get(label) ?? 0) + position.value_usd);
    });

    return Array.from(grouped.entries())
      .map(([label, value]) => ({ label, value }))
      .filter((item) => item.value > 0)
      .sort((left, right) => right.value - left.value);
  }, [sourceById, visibleDefiPositions, visibleHoldings]);
  const byAsset = groupValues(visibleHoldings, (holding) => holding.asset_symbol);
  const aggregatedHoldings = useMemo(
    () =>
      aggregateHoldings(visibleHoldings, sourceById).sort((left, right) => right.value_usd - left.value_usd),
    [sourceById, visibleHoldings]
  );
  const hiddenLowValueCount =
    activeHoldings.length - visibleHoldings.length + activeDefiPositions.length - visibleDefiPositions.length;

  const toggleExpandedHoldingRow = (rowId: string) => {
    setExpandedHoldingRows((current) => {
      const next = new Set(current);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  };

  const invalidatePortfolioQueries = async () => {
    // Source mutations affect every portfolio endpoint because holdings,
    // history, and summary are derived from the same local database.
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["sources"] }),
      queryClient.invalidateQueries({ queryKey: ["portfolio-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["portfolio-history"] }),
      queryClient.invalidateQueries({ queryKey: ["portfolio-holdings"] }),
      queryClient.invalidateQueries({ queryKey: ["defi-portfolio"] })
    ]);
  };

  const addWalletMutation = useMutation({
    mutationFn: createWalletSource,
    onSuccess: (source) => {
      setWalletForm({ label: "", address: "" });
      setFormMessage(`Added wallet ${source.address ? shortAddress(source.address) : source.label}`);
      setActiveSourceIds((current) => {
        // If the user has already customized filters, include the new wallet in
        // that custom set. Otherwise leave the default/null behavior intact.
        if (!current) {
          return current;
        }
        const next = new Set(current);
        next.add(source.id);
        return next;
      });
      void invalidatePortfolioQueries();
    }
  });

  const deleteSourceMutation = useMutation({
    mutationFn: deleteSource,
    onSuccess: (_data, sourceId) => {
      setFormMessage("Removed connection");
      setActiveSourceIds((current) => {
        if (!current) {
          return current;
        }
        const next = new Set(current);
        next.delete(sourceId);
        return next;
      });
      void invalidatePortfolioQueries();
    }
  });

  const syncMutation = useMutation({
    mutationFn: runManualSync,
    onSuccess: async (result) => {
      setSyncMessage(
        `Synced ${result.positions_written} positions at ${new Date(result.timestamp).toLocaleString()}`
      );
      await invalidatePortfolioQueries();
    }
  });

  const isLoading =
    sourcesQuery.isLoading ||
    summaryQuery.isLoading ||
    historyQuery.isLoading ||
    holdingsQuery.isLoading ||
    defiQuery.isLoading;
  const error =
    sourcesQuery.error ??
    summaryQuery.error ??
    historyQuery.error ??
    holdingsQuery.error ??
    defiQuery.error;

  if (isLoading) {
    return <LoadingState />;
  }

  if (error) {
    return <ErrorState error={error} />;
  }

  const submitWallet = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmitWallet) {
      return;
    }
    setFormMessage(null);
    addWalletMutation.mutate({
      address: walletForm.address.trim(),
      label: walletForm.label.trim() || undefined
    });
  };

  const toggleSource = (sourceId: string) => {
    setActiveSourceIds((current) => {
      const next = new Set(getSelectedIds(current, walletSources.map((source) => source.id)));
      if (next.has(sourceId)) {
        next.delete(sourceId);
      } else {
        next.add(sourceId);
      }
      return next;
    });
  };

  const toggleVenue = (venueId: string) => {
    setActiveVenueIds((current) => {
      const next = new Set(getSelectedIds(current, venueOptions.map((venue) => venue.id)));
      if (next.has(venueId)) {
        next.delete(venueId);
      } else {
        next.add(venueId);
      }
      return next;
    });
  };

  const connectionError = addWalletMutation.error ?? deleteSourceMutation.error;
  const syncError = syncMutation.error;

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-6 lg:px-8">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.14em] text-blue-700">
              Local portfolio tracker
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-950">
              {activePage === "dashboard" ? "Portfolio dashboard" : "Connections"}
            </h1>
          </div>
          <div className="flex flex-col gap-3 sm:items-end">
            <PageNav activePage={activePage} onChange={setActivePage} />
            <div className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 shadow-sm">
              Snapshot: {new Date(summaryQuery.data?.updated_at ?? "").toLocaleString()}
            </div>
          </div>
        </header>

        {activePage === "connections" ? (
          <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">Add connection</h2>
                  <p className="text-sm text-slate-500">
                    Choose what to add first, then pick the wallet ecosystem or exchange.
                  </p>
                </div>
                {formMessage ? (
                  <p className="rounded-full bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-700">
                    {formMessage}
                  </p>
                ) : null}
              </div>

              {connectionError ? (
                <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                  {connectionError instanceof Error ? connectionError.message : String(connectionError)}
                </div>
              ) : null}

              <div className="mt-5 space-y-6">
                <div>
                  <p className="mb-3 text-sm font-semibold text-slate-700">1. What do you want to add?</p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <OptionButton
                      option={{
                        id: "wallet",
                        label: "Wallet address",
                        description: "Track balances held by an onchain wallet.",
                        supported: true
                      }}
                      selected={addConnectionType === "wallet"}
                      onSelect={setAddConnectionType}
                    />
                    <OptionButton
                      option={{
                        id: "exchange",
                        label: "Exchange",
                        description: "Track balances with credentials configured in .env.",
                        supported: true
                      }}
                      selected={addConnectionType === "exchange"}
                      onSelect={setAddConnectionType}
                    />
                  </div>
                </div>

                {addConnectionType === "wallet" ? (
                  <form className="space-y-4" onSubmit={submitWallet}>
                    <div>
                      <p className="mb-3 text-sm font-semibold text-slate-700">2. Choose wallet ecosystem</p>
                      <div className="grid gap-3 sm:grid-cols-3">
                        {WALLET_NETWORKS.map((network) => (
                          <OptionButton
                            key={network.id}
                            option={network}
                            selected={walletNetwork === network.id}
                            onSelect={setWalletNetwork}
                          />
                        ))}
                      </div>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-[minmax(0,0.75fr)_minmax(0,1.25fr)]">
                      <input
                        value={walletForm.label}
                        onChange={(event) =>
                          setWalletForm((current) => ({ ...current, label: event.target.value }))
                        }
                        placeholder="Label, optional"
                        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                      />
                      <input
                        required
                        value={walletForm.address}
                        onChange={(event) =>
                          setWalletForm((current) => ({ ...current, address: event.target.value }))
                        }
                        placeholder={walletNetwork === "evm" ? "0x wallet address" : "Wallet address"}
                        pattern={walletNetwork === "evm" ? "^0x[0-9a-fA-F]{40}$" : undefined}
                        title="EVM addresses must be 0x followed by 40 hex characters."
                        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                      />
                    </div>
                    {!canSubmitWallet ? (
                      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
                        {selectedWalletNetwork?.label} wallets are planned, but only EVM wallets are enabled
                        right now.
                      </p>
                    ) : null}
                    <button
                      type="submit"
                      disabled={addWalletMutation.isPending || !canSubmitWallet}
                      className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
                    >
                      {addWalletMutation.isPending ? "Adding wallet..." : "Add wallet"}
                    </button>
                  </form>
                ) : (
                  <div className="space-y-4">
                    <div>
                      <p className="mb-3 text-sm font-semibold text-slate-700">2. Choose exchange</p>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {EXCHANGE_PROVIDERS.map((provider) => (
                          <OptionButton
                            key={provider.id}
                            option={provider}
                            selected={exchangeProvider === provider.id}
                            onSelect={setExchangeProvider}
                          />
                        ))}
                      </div>
                    </div>

                    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
                      <p className="font-medium text-slate-800">
                        Exchange connections are configured only in your local `.env` file.
                      </p>
                      <p className="mt-1">
                        For OKX, add `OKX_ACCOUNT_LABEL` if you want a custom display name, plus
                        `OKX_API_KEY`, `OKX_API_SECRET`, and `OKX_API_PASSPHRASE`.
                      </p>
                      <p className="mt-1">
                        Restart the backend after editing `.env`; the OKX source will appear here automatically.
                      </p>
                    </div>
                    {selectedExchangeProvider?.supported ? null : (
                      <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-500">
                        {selectedExchangeProvider?.label} support is planned, but only OKX is enabled right
                        now.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>

            <aside className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Current connections</h2>
              <p className="mt-1 text-sm text-slate-500">Remove wallets or exchange connections here.</p>
              <div className="mt-5 space-y-3">
                {sources.map((source) => (
                  <div
                    key={source.id}
                    className="flex items-start justify-between gap-3 rounded-md border border-slate-200 p-3"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold text-slate-900">{source.label}</p>
                        <StatusBadge status={source.status} />
                      </div>
                      <p className="mt-1 truncate text-xs text-slate-500">
                        {source.address
                          ? `${shortAddress(source.address)} · ${source.chain_ids.join(", ")}`
                          : `${source.provider} from .env ${source.api_key_label ?? ""}`}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => deleteSourceMutation.mutate(source.id)}
                      disabled={deleteSourceMutation.isPending}
                      className="rounded-md border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-600 hover:border-red-200 hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </aside>
          </section>
        ) : (
          <>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="Total value"
            value={formatCurrency(totalUsd)}
            helper={`${changeUsd >= 0 ? "+" : ""}${formatCurrency(changeUsd)} (${changePct.toFixed(2)}%) over ${activeTimeRangeLabel}`}
            helperClassName={trendIsPositive ? "font-medium text-emerald-600" : "font-medium text-red-600"}
          />
          <MetricCard
            label="Active sources"
            value={`${selectedSourceIds.size}/${walletSources.length}`}
            helper="Wallet addresses"
          />
          <MetricCard
            label="Supported chains"
            value={`${selectedChainVenueCount}/${chainVenueOptions.length}`}
            helper="Monad and Ethereum"
          />
          <MetricCard
            label="Supported exchanges"
            value={`${selectedExchangeVenueCount}/${exchangeVenueOptions.length}`}
            helper="OKX"
          />
        </section>

        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Portfolio value</h2>
                <p
                  className={`mt-1 text-sm font-medium ${
                    trendIsPositive ? "text-emerald-600" : "text-red-600"
                  }`}
                >
                  {changeUsd >= 0 ? "+" : ""}
                  {formatCurrency(changeUsd)} ({changePct.toFixed(2)}%) over {activeTimeRangeLabel}
                </p>
                <p className="mt-1 text-sm text-slate-500">Hourly snapshots with auto-scaled y-axis</p>
                {syncError ? (
                  <p className="mt-2 text-sm font-medium text-red-600">
                    {syncError instanceof Error ? syncError.message : String(syncError)}
                  </p>
                ) : null}
              </div>
              <div className="flex flex-col gap-2 sm:items-end">
                <button
                  type="button"
                  onClick={() => {
                    setSyncMessage(null);
                    syncMutation.mutate();
                  }}
                  disabled={syncMutation.isPending}
                  className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
                >
                  {syncMutation.isPending ? "Syncing..." : "Sync now"}
                </button>
                {syncMessage ? <p className="text-xs text-slate-500">{syncMessage}</p> : null}
                <div className="inline-flex rounded-lg bg-slate-100 p-1">
                  {TIME_RANGES.map((range) => (
                    <button
                      key={range.id}
                      type="button"
                      onClick={() => setActiveTimeRange(range.id)}
                      className={`rounded-md px-3 py-1.5 text-sm font-semibold transition ${
                        activeTimeRange === range.id
                          ? "bg-white text-slate-950 shadow-sm"
                          : "text-slate-500 hover:text-slate-800"
                      }`}
                    >
                      {range.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <PortfolioChart points={chartHistory} />
          </div>

          <aside className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-950">Filters</h2>
            <div className="mt-5 space-y-6">
              <div>
                <p className="mb-3 text-sm font-semibold text-slate-700">Sources</p>
                <div className="space-y-3">
                  {walletSources.map((source) => (
                    <div
                      key={source.id}
                      className="flex items-start gap-3 rounded-md border border-slate-200 p-3 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedSourceIds.has(source.id)}
                        onChange={() => toggleSource(source.id)}
                        className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-3">
                          <p className="truncate font-medium text-slate-900">
                            {source.address ? shortAddress(source.address) : source.label}
                          </p>
                          <StatusBadge status={source.status} />
                        </div>
                        <p className="mt-1 truncate text-xs text-slate-500">
                          {source.label} · scans Monad and Ethereum
                        </p>
                      </div>
                    </div>
                  ))}
                  {walletSources.length === 0 ? (
                    <p className="rounded-md border border-dashed border-slate-300 p-3 text-sm text-slate-500">
                      Add a wallet address to use source filters.
                    </p>
                  ) : null}
                </div>
              </div>

              <div>
                <p className="mb-3 text-sm font-semibold text-slate-700">Chains</p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-1 2xl:grid-cols-3">
                  {chainVenueOptions.map((venue) => (
                    <label
                      key={venue.id}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 py-2 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedVenueIds.has(venue.id)}
                        onChange={() => toggleVenue(venue.id)}
                        className="h-4 w-4 rounded border-slate-300 text-blue-600"
                      />
                      <span className="text-sm font-medium text-slate-700">{venue.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-3 text-sm font-semibold text-slate-700">Exchanges</p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-1 2xl:grid-cols-3">
                  {exchangeVenueOptions.map((venue) => (
                    <label
                      key={venue.id}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 py-2 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedVenueIds.has(venue.id)}
                        onChange={() => toggleVenue(venue.id)}
                        className="h-4 w-4 rounded border-slate-300 text-blue-600"
                      />
                      <span className="text-sm font-medium text-slate-700">{venue.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-3 text-sm font-semibold text-slate-700">Display</p>
                <label className="flex cursor-pointer items-start gap-3 rounded-md border border-slate-200 p-3 hover:bg-slate-50">
                  <input
                    type="checkbox"
                    checked={hideLowValueAssets}
                    onChange={(event) => setHideLowValueAssets(event.target.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-slate-900">
                      Hide assets below {formatCurrency(LOW_VALUE_ASSET_THRESHOLD_USD)}
                    </span>
                    <span className="mt-1 block text-xs text-slate-500">
                      {hideLowValueAssets
                        ? `${hiddenLowValueCount} low-value positions hidden from tables and allocation charts.`
                        : "Low-value positions are shown in tables and allocation charts."}
                    </span>
                  </span>
                </label>
              </div>
            </div>
          </aside>
        </section>

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-950">Allocation by chain/exchange</h2>
            <p className="mt-1 text-sm text-slate-500">Hover each slice to see USD value.</p>
            <AllocationPieChart slices={byVenueSlices} />
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-950">Top assets</h2>
            <p className="mt-1 text-sm text-slate-500">Largest visible assets by value.</p>
            <AllocationPieChart slices={byAsset.slice(0, 5)} />
          </div>
        </section>

        <DefiSection
          totalUsd={sumDefiPositions(visibleDefiPositions)}
          protocols={visibleDefiProtocols}
          sourceById={sourceById}
        />

        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-5 py-4">
            <h2 className="text-lg font-semibold text-slate-950">Holdings</h2>
            <p className="text-sm text-slate-500">Balances normalized across connected sources</p>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-3">Asset</th>
                  <th className="px-5 py-3">Source</th>
                  <th className="px-5 py-3">Chain / exchange</th>
                  <th className="px-5 py-3 text-right">Quantity</th>
                  <th className="px-5 py-3 text-right">Price</th>
                  <th className="px-5 py-3 text-right">Value</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {aggregatedHoldings.map((holding) => {
                  const isExpanded = expandedHoldingRows.has(holding.id);

                  return (
                    <Fragment key={holding.id}>
                      <tr
                        className="cursor-pointer hover:bg-slate-50"
                        onClick={() => toggleExpandedHoldingRow(holding.id)}
                      >
                        <td className="px-5 py-4">
                          <div className="flex items-center gap-3">
                            <AssetLogo
                              symbol={holding.asset_symbol}
                              name={holding.asset_name}
                              logoUrl={holding.logo_url}
                              chainId={holding.chain_id}
                              venueId={holding.venues[0]?.id ?? holding.venue_id}
                            />
                            <div className="min-w-0">
                              <p className="font-semibold text-slate-900">
                                {holding.asset_symbol}
                              </p>
                              <p className="truncate text-xs text-slate-500">{holding.asset_name}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-4 text-slate-700">
                          {uniqueSourceCount(holding.rows)} source
                          {uniqueSourceCount(holding.rows) === 1 ? "" : "s"}
                        </td>
                        <td className="px-5 py-4">
                          <div className="flex flex-wrap gap-2">
                            {holding.venues.map((venue) => (
                              <VenuePill key={venue.id} venueId={venue.id} label={venue.label} />
                            ))}
                          </div>
                        </td>
                        <td className="px-5 py-4 text-right tabular-nums text-slate-700">
                          {formatQuantity(holding.quantity)}
                        </td>
                        <td className="px-5 py-4 text-right tabular-nums text-slate-700">
                          {formatCurrency(holding.price_usd, holding.price_usd >= 10 ? 2 : 4)}
                        </td>
                        <td className="px-5 py-4 text-right tabular-nums font-semibold text-slate-950">
                          {formatCurrency(holding.value_usd)}
                        </td>
                      </tr>
                      {isExpanded ? (
                        <tr>
                          <td colSpan={6} className="bg-slate-50 px-5 py-3">
                            <div className="space-y-2">
                              {holding.rows.map((row) => (
                                <div
                                  key={row.id}
                                  className="grid grid-cols-[1.2fr_0.8fr_0.8fr_0.8fr] items-center gap-3 rounded-md bg-white px-3 py-2 text-xs"
                                >
                                  <div className="flex min-w-0 items-center gap-2">
                                    <AssetLogo
                                      symbol={row.asset_symbol}
                                      name={row.asset_name}
                                      logoUrl={row.logo_url}
                                      chainId={row.chain_id}
                                      venueId={getHoldingVenueId(row, sourceById)}
                                      sourceType={row.source_type}
                                      size="sm"
                                    />
                                    <div className="min-w-0">
                                      <p className="font-semibold text-slate-800">
                                        {sourceBreakdownLabel(row.source_id, row.source_label, sourceById)}
                                      </p>
                                      <p className="truncate text-slate-500">
                                        {getHoldingVenueLabel(row, sourceById)}
                                      </p>
                                    </div>
                                  </div>
                                  <p className="text-right tabular-nums text-slate-600">
                                    {formatQuantity(row.quantity)} {row.asset_symbol}
                                  </p>
                                  <p className="text-right tabular-nums text-slate-600">
                                    {formatCurrency(row.price_usd, row.price_usd >= 10 ? 2 : 4)}
                                  </p>
                                  <p className="text-right font-semibold tabular-nums text-slate-900">
                                    {formatCurrency(row.value_usd)}
                                  </p>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
          </>
        )}
      </div>
    </main>
  );
}

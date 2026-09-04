// Direct Alpaca REST client for server-side routes only. Never imported
// from client components -- these calls carry the secret key and must run
// on the server (Vercel serverless function), never ship to the browser.
//
// Paper trading is hardcoded, not env-configurable -- mirrors the main
// vol-desk system's own rule (docs/INTEGRATIONS.md: "ALPACA_PAPER is a
// constant, never configurable... a typo in an environment file must not
// be able to point this system at a live account"). This dashboard is
// read-only (GET requests only, never places or cancels anything), but
// the same discipline applies: it should only ever be capable of showing
// paper-account data.
const ALPACA_BASE_URL = "https://paper-api.alpaca.markets";

function authHeaders(): HeadersInit {
  const apiKey = process.env.ALPACA_API_KEY;
  const secretKey = process.env.ALPACA_SECRET_KEY;
  if (!apiKey || !secretKey) {
    throw new Error(
      "ALPACA_API_KEY / ALPACA_SECRET_KEY are not set on this deployment. " +
        "Add them in Vercel project settings -> Environment Variables."
    );
  }
  return {
    "APCA-API-KEY-ID": apiKey,
    "APCA-API-SECRET-KEY": secretKey,
  };
}

async function alpacaGet<T>(path: string): Promise<T> {
  const res = await fetch(`${ALPACA_BASE_URL}${path}`, {
    headers: authHeaders(),
    // never cache account/position data
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Alpaca ${path} -> HTTP ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export interface AlpacaAccount {
  id: string;
  account_number: string;
  status: string;
  currency: string;
  cash: string;
  portfolio_value: string;
  equity: string;
  last_equity: string;
  buying_power: string;
  long_market_value: string;
  short_market_value: string;
  trading_blocked: boolean;
  account_blocked: boolean;
  created_at: string;
  shorting_enabled: boolean;
}

export interface AlpacaPosition {
  asset_id: string;
  symbol: string;
  asset_class: string;
  qty: string;
  side: "long" | "short";
  avg_entry_price: string;
  current_price: string;
  market_value: string;
  cost_basis: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  unrealized_intraday_pl: string;
  unrealized_intraday_plpc: string;
}

export interface AlpacaPortfolioHistory {
  timestamp: number[];
  equity: number[];
  profit_loss: number[];
  profit_loss_pct: number[];
  base_value: number;
  timeframe: string;
}

export interface AlpacaOrder {
  id: string;
  client_order_id: string;
  created_at: string;
  submitted_at: string;
  filled_at: string | null;
  canceled_at: string | null;
  expired_at: string | null;
  failed_at: string | null;
  asset_class: string;
  symbol: string;
  order_class: string;
  qty: string | null;
  filled_qty: string;
  filled_avg_price: string | null;
  order_type: string;
  side: string;
  time_in_force: string;
  limit_price: string | null;
  status: string;
  legs: AlpacaOrder[] | null;
}

export function getAccount(): Promise<AlpacaAccount> {
  return alpacaGet<AlpacaAccount>("/v2/account");
}

export function getPositions(): Promise<AlpacaPosition[]> {
  return alpacaGet<AlpacaPosition[]>("/v2/positions");
}

// period: e.g. "1M", "3M", "1A"; timeframe: e.g. "1D"
export function getPortfolioHistory(
  period: string = "1M",
  timeframe: string = "1D"
): Promise<AlpacaPortfolioHistory> {
  return alpacaGet<AlpacaPortfolioHistory>(
    `/v2/account/portfolio/history?period=${period}&timeframe=${timeframe}`
  );
}

export function getOrders(
  status: "open" | "closed" | "all" = "closed",
  limit: number = 100
): Promise<AlpacaOrder[]> {
  return alpacaGet<AlpacaOrder[]>(
    `/v2/orders?status=${status}&limit=${limit}&direction=desc&nested=true`
  );
}

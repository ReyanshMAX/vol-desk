import { StatCard } from "@/components/StatCard";
import { EquityChart } from "@/components/EquityChart";
import { PositionsTable } from "@/components/PositionsTable";
import { ActivityTable } from "@/components/ActivityTable";
import {
  getAccount,
  getPositions,
  getPortfolioHistory,
  getOrders,
  AlpacaAccount,
  AlpacaPosition,
  AlpacaPortfolioHistory,
  AlpacaOrder,
} from "@/lib/alpaca";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function fmtMoney(n: number) {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function settle<T>(r: PromiseSettledResult<T>): T | null {
  return r.status === "fulfilled" ? r.value : null;
}

export default async function DashboardPage() {
  const [accountR, positionsR, historyR, ordersR] = await Promise.allSettled([
    getAccount(),
    getPositions(),
    getPortfolioHistory("1M", "1D"),
    getOrders("closed", 50),
  ]);

  const account = settle<AlpacaAccount>(accountR);
  const positions = settle<AlpacaPosition[]>(positionsR) ?? [];
  const history = settle<AlpacaPortfolioHistory>(historyR);
  const orders = settle<AlpacaOrder[]>(ordersR) ?? [];

  const configError =
    accountR.status === "rejected" &&
    accountR.reason instanceof Error &&
    accountR.reason.message.includes("not set on this deployment");

  const equity = account ? parseFloat(account.equity) : null;
  const lastEquity = account ? parseFloat(account.last_equity) : null;
  const todayPl = equity !== null && lastEquity !== null ? equity - lastEquity : null;
  const todayPlPct =
    todayPl !== null && lastEquity ? (todayPl / lastEquity) * 100 : null;

  const periodPl =
    history && history.profit_loss.length > 0
      ? history.profit_loss[history.profit_loss.length - 1]
      : null;
  const periodPlPct =
    history && history.profit_loss_pct.length > 0
      ? history.profit_loss_pct[history.profit_loss_pct.length - 1] * 100
      : null;

  const points =
    history && history.timestamp.length > 0
      ? history.timestamp.map((ts, i) => ({ ts, equity: history.equity[i] }))
      : [];

  return (
    <div>
      <h1 className="page-title">Overview</h1>
      <p className="page-subtitle">
        Live from Alpaca's paper trading API &middot; refreshed on every page
        load
      </p>

      {configError && (
        <div className="error-banner">
          <strong>Not connected to Alpaca yet.</strong> Add{" "}
          <code>ALPACA_API_KEY</code> and <code>ALPACA_SECRET_KEY</code> in
          this project&apos;s Vercel settings (Settings → Environment
          Variables), then redeploy.
        </div>
      )}
      {!configError && accountR.status === "rejected" && (
        <div className="error-banner">
          Couldn&apos;t reach Alpaca right now:{" "}
          {accountR.reason instanceof Error
            ? accountR.reason.message
            : "unknown error"}
        </div>
      )}

      <div className="stat-grid">
        <StatCard
          label="Equity"
          value={equity !== null ? fmtMoney(equity) : "—"}
        />
        <StatCard
          label="Cash"
          value={account ? fmtMoney(parseFloat(account.cash)) : "—"}
        />
        <StatCard
          label="Buying Power"
          value={account ? fmtMoney(parseFloat(account.buying_power)) : "—"}
        />
        <StatCard
          label="Today's P/L"
          value={todayPl !== null ? fmtMoney(todayPl) : "—"}
          sub={todayPlPct !== null ? `${todayPlPct >= 0 ? "+" : ""}${todayPlPct.toFixed(2)}%` : undefined}
          tone={todayPl === null ? "neutral" : todayPl >= 0 ? "positive" : "negative"}
        />
        <StatCard
          label="P/L (30d)"
          value={periodPl !== null ? fmtMoney(periodPl) : "—"}
          sub={periodPlPct !== null ? `${periodPlPct >= 0 ? "+" : ""}${periodPlPct.toFixed(2)}%` : undefined}
          tone={periodPl === null ? "neutral" : periodPl >= 0 ? "positive" : "negative"}
        />
        <StatCard
          label="Open Positions"
          value={String(positions.length)}
          sub={`of 6 max concurrent`}
        />
        <StatCard
          label="Closed Orders"
          value={String(orders.length)}
          sub="most recent 50"
        />
        <StatCard
          label="Account Status"
          value={account ? account.status : "—"}
          sub={
            account
              ? account.trading_blocked || account.account_blocked
                ? "trading blocked"
                : "trading enabled"
              : undefined
          }
          tone={
            !account
              ? "neutral"
              : account.trading_blocked || account.account_blocked
              ? "negative"
              : "positive"
          }
        />
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Equity curve</div>
          <div className="panel-hint">last 30 days, daily</div>
        </div>
        <EquityChart points={points} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Open positions</div>
          <div className="panel-hint">live from Alpaca</div>
        </div>
        <PositionsTable positions={positions} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Recent activity</div>
          <div className="panel-hint">closed orders, most recent first</div>
        </div>
        <ActivityTable orders={orders} />
      </div>
    </div>
  );
}

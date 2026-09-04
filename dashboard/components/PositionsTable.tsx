import { AlpacaPosition } from "@/lib/alpaca";

function fmtMoney(v: string | number) {
  const n = typeof v === "string" ? parseFloat(v) : v;
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function fmtPct(v: string | number) {
  const n = (typeof v === "string" ? parseFloat(v) : v) * 100;
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export function PositionsTable({ positions }: { positions: AlpacaPosition[] }) {
  if (positions.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">No open positions</div>
        The entry gate only trades when a symbol's IV rank clears the
        threshold and every risk check passes -- most scans open nothing.
        That's expected behavior, not an error.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Avg Entry</th>
            <th>Current</th>
            <th>Market Value</th>
            <th>Unrealized P/L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const pl = parseFloat(p.unrealized_pl);
            return (
              <tr key={p.asset_id}>
                <td>{p.symbol}</td>
                <td>
                  <span className={`side-badge side-${p.side}`}>
                    {p.side.toUpperCase()}
                  </span>
                </td>
                <td>{p.qty}</td>
                <td>{fmtMoney(p.avg_entry_price)}</td>
                <td>{fmtMoney(p.current_price)}</td>
                <td>{fmtMoney(p.market_value)}</td>
                <td className={pl >= 0 ? "positive" : "negative"}>
                  {fmtMoney(p.unrealized_pl)} ({fmtPct(p.unrealized_plpc)})
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

import { AlpacaOrder } from "@/lib/alpaca";

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusClass(status: string) {
  if (status === "filled") return "status-badge status-filled";
  if (["new", "accepted", "pending_new", "partially_filled"].includes(status))
    return "status-badge status-active";
  return "status-badge";
}

export function ActivityTable({ orders }: { orders: AlpacaOrder[] }) {
  if (orders.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">No closed orders yet</div>
        Order history will appear here once the system places and closes its
        first trade.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Type</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Fill Price</th>
            <th>Status</th>
            <th>Filled</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td>{o.symbol}</td>
              <td>
                {o.order_class === "mleg" && o.legs
                  ? `multi-leg (${o.legs.length})`
                  : o.order_type}
              </td>
              <td>
                <span
                  className={`side-badge side-${
                    o.side === "buy" ? "long" : "short"
                  }`}
                >
                  {o.side.toUpperCase()}
                </span>
              </td>
              <td>{o.filled_qty || o.qty || "—"}</td>
              <td>
                {o.filled_avg_price
                  ? `$${parseFloat(o.filled_avg_price).toFixed(2)}`
                  : "—"}
              </td>
              <td>
                <span className={statusClass(o.status)}>{o.status}</span>
              </td>
              <td>{fmtDate(o.filled_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

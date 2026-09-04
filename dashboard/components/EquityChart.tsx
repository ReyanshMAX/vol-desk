"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Point {
  ts: number;
  equity: number;
}

export function EquityChart({ points }: { points: Point[] }) {
  if (points.length < 2) {
    return (
      <div className="empty-state">
        <div className="empty-state-title">Not enough history yet</div>
        Alpaca's portfolio history builds up as the account trades and days
        pass. Check back after the system has been running a while.
      </div>
    );
  }

  const values = points.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.1 || max * 0.02 || 1;

  const data = points.map((p) => ({
    date: new Date(p.ts * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    }),
    equity: p.equity,
  }));

  const isUp = values[values.length - 1] >= values[0];

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop
              offset="5%"
              stopColor={isUp ? "#34d399" : "#f87171"}
              stopOpacity={0.35}
            />
            <stop
              offset="95%"
              stopColor={isUp ? "#34d399" : "#f87171"}
              stopOpacity={0}
            />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#232b3d" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          stroke="#5b6478"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          minTickGap={30}
        />
        <YAxis
          domain={[min - pad, max + pad]}
          stroke="#5b6478"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${Math.round(v).toLocaleString()}`}
          width={70}
        />
        <Tooltip
          contentStyle={{
            background: "#161d2b",
            border: "1px solid #232b3d",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: "#8a94a8" }}
          formatter={(value: number) => [
            `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
            "Equity",
          ]}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={isUp ? "#34d399" : "#f87171"}
          strokeWidth={2}
          fill="url(#equityFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

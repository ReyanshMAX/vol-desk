import { NextRequest, NextResponse } from "next/server";
import { getPortfolioHistory } from "@/lib/alpaca";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const period = request.nextUrl.searchParams.get("period") ?? "1M";
  const timeframe = request.nextUrl.searchParams.get("timeframe") ?? "1D";
  try {
    const history = await getPortfolioHistory(period, timeframe);
    return NextResponse.json(history);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

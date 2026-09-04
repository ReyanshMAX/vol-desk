import { NextResponse } from "next/server";
import { getOrders } from "@/lib/alpaca";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const orders = await getOrders("closed", 50);
    return NextResponse.json(orders);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

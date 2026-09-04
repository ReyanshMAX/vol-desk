import { NextResponse } from "next/server";
import { getPositions } from "@/lib/alpaca";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const positions = await getPositions();
    return NextResponse.json(positions);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

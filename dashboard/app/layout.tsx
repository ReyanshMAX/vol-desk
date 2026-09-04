import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "vol-desk",
  description: "Autonomous options-trading agent on Alpaca paper trading",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="topbar">
            <div className="topbar-inner">
              <a href="/" className="brand">
                vol<span className="brand-accent">-desk</span>
              </a>
              <nav className="nav">
                <a href="/">Dashboard</a>
                <a href="/how-it-works">How it works</a>
                <a href="/deck">Slide Deck</a>
              </nav>
              <span className="paper-badge">PAPER TRADING</span>
            </div>
          </header>
          <main className="content">{children}</main>
          <footer className="footer">
            Volatility-selling options agent &middot; deterministic risk
            enforcement &middot; Alpaca paper account &middot; not
            investment advice
          </footer>
        </div>
      </body>
    </html>
  );
}

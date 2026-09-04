"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-display",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

function FootMark() {
  return (
    <div className="foot-mark">
      <Link href="/" className="brand">
        vol<b>-desk</b>
      </Link>{" "}
      &middot; hackathon overview
    </div>
  );
}

export default function DeckPage() {
  const deckRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const curRef = useRef<HTMLElement>(null);
  const totRef = useRef<HTMLSpanElement>(null);
  const dotnavRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prevTitle = document.title;
    document.title = "Slide Deck · vol-desk";

    const container = deckRef.current;
    const rail = railRef.current;
    const curEl = curRef.current;
    const totEl = totRef.current;
    const dotnav = dotnavRef.current;
    if (!container || !rail || !curEl || !totEl || !dotnav) return;

    const slides = Array.from(container.querySelectorAll<HTMLElement>(".slide"));
    totEl.textContent = String(slides.length).padStart(2, "0");

    dotnav.innerHTML = "";
    slides.forEach((s, i) => {
      const b = document.createElement("button");
      b.setAttribute("aria-label", "Go to slide " + (i + 1));
      b.addEventListener("click", () => s.scrollIntoView({ behavior: "smooth", block: "start" }));
      dotnav.appendChild(b);
    });
    const dots = Array.from(dotnav.children) as HTMLElement[];

    let activeIndex = 0;
    function setActive(i: number) {
      activeIndex = i;
      curEl!.textContent = String(i + 1).padStart(2, "0");
      rail!.style.width = ((i + 1) / slides.length) * 100 + "%";
      dots.forEach((d, di) => d.classList.toggle("active", di === i));
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting && e.intersectionRatio > 0.5) {
            setActive(slides.indexOf(e.target as HTMLElement));
          }
        });
      },
      { root: container, threshold: 0.5 }
    );
    slides.forEach((s) => io.observe(s));
    setActive(0);

    function onKeyDown(e: KeyboardEvent) {
      if (["ArrowDown", "PageDown", " "].includes(e.key)) {
        e.preventDefault();
        slides[Math.min(activeIndex + 1, slides.length - 1)].scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (["ArrowUp", "PageUp"].includes(e.key)) {
        e.preventDefault();
        slides[Math.max(activeIndex - 1, 0)].scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (e.key === "Home") {
        e.preventDefault();
        slides[0].scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (e.key === "End") {
        e.preventDefault();
        slides[slides.length - 1].scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
    window.addEventListener("keydown", onKeyDown);

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      io.disconnect();
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      document.title = prevTitle;
    };
  }, []);

  return (
    <div className={`deck-shell ${plexSans.variable} ${plexMono.variable}`}>
      <div className="progress-rail" ref={railRef} style={{ width: "0%" }} />
      <div className="counter">
        <b ref={curRef}>01</b> / <span ref={totRef}>10</span>
      </div>
      <div className="dotnav" ref={dotnavRef} />

      <div className="deck" ref={deckRef}>
        {/* TITLE */}
        <section className="slide slide-title">
          <div className="slide-body">
            <h1 className="wordmark">
              vol<span>&#8209;</span>desk
            </h1>
            <p className="tagline">
              An autonomous options-trading agent that <strong>sells volatility risk on purpose</strong> &mdash; with
              judgment confined to two decisions, and a deterministic risk engine holding veto over every order.
            </p>
          </div>
        </section>

        {/* 01 : EDGE */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">01</span> The Edge<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">
              Implied volatility tends to price <em>richer</em> than what actually happens.
            </h2>
            <div className="edge-grid">
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <p className="lede">
                  vol-desk systematically harvests that spread &mdash; selling <strong>defined-risk premium</strong>{" "}
                  (credit spreads, iron condors) on a fixed universe of liquid ETFs, with occasional debit spreads on
                  high-conviction directional signals.
                </p>
                <p className="callout-line">
                  <strong>Every position has a computable max loss before it&apos;s ever placed.</strong> No naked or
                  undefined-risk trades anywhere in the system.
                </p>
              </div>
              <div className="ticker-panel">
                <div className="panel-label">Fixed universe &mdash; 7 tickers</div>
                <div className="ticker-grid">
                  <div className="ticker">SPY</div>
                  <div className="ticker">QQQ</div>
                  <div className="ticker">IWM</div>
                  <div className="ticker">GLD</div>
                  <div className="ticker">TLT</div>
                  <div className="ticker">XLE</div>
                  <div className="ticker">HYG</div>
                </div>
              </div>
            </div>
          </div>
          <FootMark />
        </section>

        {/* 02 : THE PROCESS */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">02</span> The Process<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">
              Four agents, one pipeline. Judgment at <em>exactly two</em> points.
            </h2>
            <p className="lede">
              A single long-lived Python process, no cron, no web service. Position sizing, risk enforcement, and
              management triggers never touch an LLM &mdash; a risk check that can hallucinate isn&apos;t a risk
              check.
            </p>
            <div className="flow">
              <div className="flow-node">
                <div className="fn-label">Signal</div>
                <div className="fn-tag">deterministic</div>
                <p className="fn-desc">IV rank, realized vol, trend score. Gates the LLM call.</p>
              </div>
              <div className="flow-arrow">&rarr;</div>
              <div className="flow-node llm">
                <div className="fn-label">Regime</div>
                <div className="fn-tag">LLM</div>
                <p className="fn-desc">Labels 1 of 7 regimes from a mechanical rule.</p>
              </div>
              <div className="flow-arrow">&rarr;</div>
              <div className="flow-node llm">
                <div className="fn-label">Strategy</div>
                <div className="fn-tag">LLM</div>
                <p className="fn-desc">Picks contracts; every number recomputed after.</p>
              </div>
              <div className="flow-arrow">&rarr;</div>
              <div className="flow-node">
                <div className="fn-label">Risk</div>
                <div className="fn-tag">deterministic</div>
                <p className="fn-desc">8 checks. Vetoes, sizes down, or halts &mdash; never widens.</p>
              </div>
              <div className="flow-arrow">&rarr;</div>
              <div className="flow-node">
                <div className="fn-label">Execution</div>
                <div className="fn-tag">Alpaca MCP</div>
                <p className="fn-desc">Price-ladder order, or abandoned. Sole path to a fill.</p>
              </div>
            </div>
          </div>
          <FootMark />
        </section>

        {/* 03 : REGIMES */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">03</span> Regimes<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">One label decides what&apos;s even allowed.</h2>
            <div className="regime-matrix">
              <div className="regime-box">
                <div className="rx-head">
                  <span className="rx-name">Range &middot; High IV</span>
                  <span className="badge badge-det">premium sell</span>
                </div>
                <div className="rx-struct">Iron condor, put &amp; call credit spread</div>
              </div>
              <div className="regime-box stand-down">
                <div className="rx-head">
                  <span className="rx-name">Range &middot; Low IV</span>
                </div>
                <div className="rx-struct">Stand down &mdash; cheap premium isn&apos;t worth defined-risk capital</div>
              </div>
              <div className="regime-box">
                <div className="rx-head">
                  <span className="rx-name">Trend Up &middot; High IV</span>
                  <span className="badge badge-det">premium sell</span>
                </div>
                <div className="rx-struct">Put credit spread</div>
              </div>
              <div className="regime-box">
                <div className="rx-head">
                  <span className="rx-name">Trend Down &middot; High IV</span>
                  <span className="badge badge-det">premium sell</span>
                </div>
                <div className="rx-struct">Call credit spread</div>
              </div>
              <div className="regime-box">
                <div className="rx-head">
                  <span className="rx-name">Trend Up &middot; Low IV</span>
                  <span className="badge badge-flag">directional</span>
                </div>
                <div className="rx-struct">Call debit spread</div>
              </div>
              <div className="regime-box">
                <div className="rx-head">
                  <span className="rx-name">Trend Down &middot; Low IV</span>
                  <span className="badge badge-flag">directional</span>
                </div>
                <div className="rx-struct">Put debit spread</div>
              </div>
            </div>
            <div className="regime-stress">
              <span className="rs-name">Stress &mdash; override</span>
              <span className="rs-detail">
                IV rank &ge; 0.90 or 20-day realized vol &ge; 0.45 preempts range/trend entirely. No new entries;
                existing positions still managed.
              </span>
            </div>
          </div>
          <FootMark />
        </section>

        {/* 04 : STRATEGIES */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">04</span> Strategies<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">Three structures. All defined-risk, none of them guesses.</h2>
            <div className="strategy-grid">
              <div className="strategy-card">
                <div className="strategy-head">
                  <span className="strategy-name">Credit Spread</span>
                  <span className="badge badge-det">premium sell</span>
                </div>
                <p className="strategy-desc">
                  Short one leg, long another further out. Collects part of the width as premium; max loss is width
                  minus credit received.
                </p>
                <div className="strategy-regime">
                  Unlocked by: <b>range or trend &middot; high IV</b>
                </div>
              </div>
              <div className="strategy-card">
                <div className="strategy-head">
                  <span className="strategy-name">Iron Condor</span>
                  <span className="badge badge-det">premium sell</span>
                </div>
                <p className="strategy-desc">
                  Two credit spreads at once &mdash; a short strangle wrapped in long wings on both sides. Profits if
                  price stays inside the range.
                </p>
                <div className="strategy-regime">
                  Unlocked by: <b>range &middot; high IV</b>
                </div>
              </div>
              <div className="strategy-card">
                <div className="strategy-head">
                  <span className="strategy-name">Debit Spread</span>
                  <span className="badge badge-flag">directional</span>
                </div>
                <p className="strategy-desc">
                  A directional bet, still defined-risk: buy one leg, sell another to cap the cost. Only used once IV
                  is too cheap to be worth selling.
                </p>
                <div className="strategy-regime">
                  Unlocked by: <b>trend &middot; low IV</b>
                </div>
              </div>
            </div>
            <p className="callout-line">
              Range + low IV unlocks <strong>nothing</strong>. Standing down is a normal, expected outcome &mdash;
              not a failure.
            </p>
          </div>
          <FootMark />
        </section>

        {/* 05 : PIPELINE */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">05</span> Entry Pipeline<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">Six gates. Any one can stop the trade.</h2>
            <div className="pipeline">
              <div className="pstep">
                <div className="pnum">01</div>
                <div>
                  <div className="ptitle">Signal gate</div>
                  <div className="pdetail">
                    Skip unless IV rank clears <code>0.35</code> &mdash; a near-zero-cost pre-filter.
                  </div>
                </div>
              </div>
              <div className="pstep">
                <div className="pnum">02</div>
                <div>
                  <div className="ptitle">Regime classification</div>
                  <div className="pdetail">
                    Cached <code>30 min</code> per symbol; only recomputed when the gate passes.
                  </div>
                </div>
              </div>
              <div className="pstep">
                <div className="pnum">03</div>
                <div>
                  <div className="ptitle">Structure eligibility</div>
                  <div className="pdetail">The regime unlocks a fixed menu. Standing down is a normal, expected outcome.</div>
                </div>
              </div>
              <div className="pstep">
                <div className="pnum">04</div>
                <div>
                  <div className="ptitle">Contract selection</div>
                  <div className="pdetail">
                    Chain filtered to <code>7&ndash;14 DTE</code>, within 15% of spot, targeting{" "}
                    <code>0.16&Delta;</code> short legs.
                  </div>
                </div>
              </div>
              <div className="pstep">
                <div className="pnum">05</div>
                <div>
                  <div className="ptitle">Risk evaluation</div>
                  <div className="pdetail">
                    All eight checks run regardless of outcome &mdash; halt state, defined-risk, caps, sizing, cash,
                    DTE.
                  </div>
                </div>
              </div>
              <div className="pstep">
                <div className="pnum">06</div>
                <div>
                  <div className="ptitle">Price-ladder execution</div>
                  <div className="pdetail">
                    Three rungs toward the market over <code>90s</code>. No fill &rarr; abandoned. Never widened,
                    never chased.
                  </div>
                </div>
              </div>
            </div>
          </div>
          <FootMark />
        </section>

        {/* 06 : RISK */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">06</span> Risk Enforcement<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">Risk holds veto authority over every order.</h2>
            <div className="table-wrap">
              <table className="risk">
                <tbody>
                  <tr>
                    <td>Max risk per trade</td>
                    <td>1% of equity</td>
                  </tr>
                  <tr>
                    <td>Soft drawdown halt &mdash; blocks new entries</td>
                    <td>&minus;5% HWM</td>
                  </tr>
                  <tr className="hard">
                    <td>Hard drawdown halt &mdash; flattens everything</td>
                    <td>&minus;10% HWM</td>
                  </tr>
                  <tr>
                    <td>Max concurrent positions</td>
                    <td>6</td>
                  </tr>
                  <tr>
                    <td>Max positions per underlying</td>
                    <td>1</td>
                  </tr>
                  <tr>
                    <td>Equity-beta cluster cap (SPY+QQQ+IWM)</td>
                    <td>3</td>
                  </tr>
                  <tr>
                    <td>Take-profit</td>
                    <td>50% of credit</td>
                  </tr>
                  <tr>
                    <td>Stop-loss</td>
                    <td>2&times; entry credit</td>
                  </tr>
                  <tr>
                    <td>Force-close</td>
                    <td>2 DTE, any P/L</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="callout-line">
              A hard drawdown halt is <strong>terminal and manual to clear</strong>, by design &mdash; the point of a
              kill switch is that it stays pulled until a human looks at why.
            </p>
          </div>
          <FootMark />
        </section>

        {/* 07 : STATE */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">07</span> State<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">The process holds no truth of its own.</h2>
            <p className="lede">
              It can be restarted safely at any moment &mdash; on boot, it reconciles against Alpaca before doing
              anything else.
            </p>
            <div className="sot">
              <div className="sot-node">
                <div className="sn-title">Alpaca</div>
                <div className="sn-desc">Source of truth for positions &amp; cash.</div>
              </div>
              <div className="sot-link">
                <span className="arrows">&harr;</span>reconciled on boot
              </div>
              <div className="sot-node process">
                vol-desk process
                <br />
                stateless &middot; restartable
              </div>
              <div className="sot-link">
                <span className="arrows">&harr;</span>written continuously
              </div>
              <div className="sot-node">
                <div className="sn-title">SQLite (WAL)</div>
                <div className="sn-desc">Source of truth for entry credit, management plan, IV history, high-water mark.</div>
              </div>
            </div>
          </div>
          <FootMark />
        </section>

        {/* 08 : STACK */}
        <section className="slide">
          <div className="eyebrow">
            <span className="num">08</span> Stack<span className="rule" />
          </div>
          <div className="slide-body">
            <h2 className="headline">The Tech Stack</h2>
            <div className="stack-grid">
              <div className="stack-box">
                <div className="sk-label">Language</div>
                <div className="sk-value">
                  Python <b>3.11+</b>, stdlib plus a small dependency set
                </div>
              </div>
              <div className="stack-box">
                <div className="sk-label">Runtime</div>
                <div className="sk-value">
                  Single long-lived process, <b>systemd</b>-managed, in-process scheduler
                </div>
              </div>
              <div className="stack-box">
                <div className="sk-label">Market data</div>
                <div className="sk-value">
                  Alpaca REST via <b>alpaca-py</b>, indicative feed, 15-min delayed
                </div>
              </div>
              <div className="stack-box">
                <div className="sk-label">Execution</div>
                <div className="sk-value">
                  Alpaca <b>MCP server</b> exclusively &mdash; no order placed any other way
                </div>
              </div>
              <div className="stack-box">
                <div className="sk-label">Inference</div>
                <div className="sk-value">
                  <b>Groq</b> free tier, OpenAI-compatible client, model-agnostic
                </div>
              </div>
              <div className="stack-box">
                <div className="sk-label">Data store</div>
                <div className="sk-value">
                  <b>SQLite</b>, single file, WAL mode, no network dependency
                </div>
              </div>
            </div>
            <p className="callout-line">
              If Groq is unreachable, the system degrades to <strong>hold-and-manage</strong> &mdash; existing
              positions keep being managed, no new entries open, nothing silently substitutes weaker judgment.
            </p>
          </div>
          <FootMark />
        </section>

        {/* THANKS */}
        <section className="slide slide-title">
          <div className="slide-body" style={{ alignItems: "center", textAlign: "center" }}>
            <h2 className="headline" style={{ maxWidth: "34ch" }}>
              Thanks for <em>watching</em>.
            </h2>
            <p className="tagline">vol-desk</p>
          </div>
        </section>
      </div>

      <style jsx>{`
        .deck-shell {
          color-scheme: dark;
          --bg: #0a0e14;
          --surface: #10151f;
          --surface-2: #161d2b;
          --border: #232b3d;
          --border-strong: #303c56;
          --text: #e9edf7;
          --text-dim: #8d97ac;
          --text-faint: #5b6478;
          --accent: #7c9eff;
          --accent-soft: #26304d;
          --good: #34d399;
          --good-soft: #12291f;
          --risk: #f8776f;
          --risk-soft: #331a19;
          --flag: #f2b94d;
          --flag-soft: #332708;
          --font-body: var(--font-display);

          position: fixed;
          inset: 0;
          z-index: 100;
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: var(--font-body), system-ui, -apple-system, "Segoe UI", sans-serif;
          -webkit-font-smoothing: antialiased;
          overflow: hidden;
        }

        .deck-shell :global(*) {
          box-sizing: border-box;
        }

        .deck-shell :global(h1),
        .deck-shell :global(h2),
        .deck-shell :global(h3) {
          font-family: var(--font-display), system-ui, -apple-system, "Segoe UI", sans-serif;
          font-weight: 700;
          text-wrap: balance;
          margin: 0;
          letter-spacing: -0.01em;
        }

        .deck-shell :global(p) {
          margin: 0;
        }

        .deck-shell :global(::selection) {
          background: var(--accent-soft);
          color: var(--text);
        }

        .deck {
          height: 100vh;
          overflow-y: scroll;
          scroll-snap-type: y mandatory;
          scroll-behavior: smooth;
          scrollbar-width: none;
        }
        @media (prefers-reduced-motion: reduce) {
          .deck {
            scroll-behavior: auto;
          }
        }
        .deck::-webkit-scrollbar {
          display: none;
        }

        .slide {
          height: 100vh;
          width: 100%;
          scroll-snap-align: start;
          scroll-snap-stop: always;
          display: flex;
          flex-direction: column;
          padding: 40px 76px 36px;
          position: relative;
          border-bottom: 1px solid var(--border);
          overflow: hidden;
        }

        @media (max-width: 860px) {
          .slide {
            padding: 28px 22px 64px;
          }
        }

        .eyebrow {
          font-family: var(--font-mono);
          font-size: 12px;
          font-weight: 500;
          letter-spacing: 0.11em;
          text-transform: uppercase;
          color: var(--text-dim);
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 16px;
          flex: none;
        }
        .eyebrow .num {
          color: var(--accent);
        }
        .eyebrow .rule {
          height: 1px;
          flex: 1;
          background: var(--border);
          max-width: 120px;
        }

        .slide-body {
          flex: 1;
          min-height: 0;
          display: flex;
          flex-direction: column;
          justify-content: center;
          gap: 20px;
          max-width: 1180px;
        }

        .headline {
          font-size: clamp(26px, 3.1vw, 40px);
          line-height: 1.16;
          max-width: 18ch;
          flex: none;
        }
        .headline :global(em) {
          font-style: normal;
          color: var(--accent);
        }

        .lede {
          font-size: 15.5px;
          line-height: 1.55;
          color: var(--text-dim);
          max-width: 64ch;
          flex: none;
        }
        .lede :global(strong) {
          color: var(--text);
          font-weight: 600;
        }

        .foot-mark {
          position: absolute;
          left: 76px;
          bottom: 14px;
          display: flex;
          align-items: center;
          gap: 10px;
          font-family: var(--font-mono);
          font-size: 11.5px;
          color: var(--text-faint);
        }
        .foot-mark :global(.brand) {
          color: var(--text-dim);
          letter-spacing: 0.02em;
          text-decoration: none;
        }
        .foot-mark :global(.brand b) {
          color: var(--accent);
          font-weight: 600;
        }
        @media (max-width: 860px) {
          .foot-mark {
            left: 22px;
          }
        }

        .progress-rail {
          position: fixed;
          top: 0;
          left: 0;
          height: 2px;
          background: var(--accent);
          z-index: 30;
          transition: width 0.25s ease;
        }

        .counter {
          position: fixed;
          right: 30px;
          bottom: 16px;
          font-family: var(--font-mono);
          font-size: 12px;
          color: var(--text-faint);
          z-index: 30;
          letter-spacing: 0.04em;
        }
        .counter :global(b) {
          color: var(--text-dim);
        }

        .dotnav {
          position: fixed;
          right: 30px;
          top: 50%;
          transform: translateY(-50%);
          display: flex;
          flex-direction: column;
          gap: 10px;
          z-index: 30;
        }
        .dotnav :global(button) {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          border: 1px solid var(--border-strong);
          background: transparent;
          padding: 0;
          cursor: pointer;
          transition: background 0.2s, border-color 0.2s, transform 0.2s;
        }
        .dotnav :global(button:hover) {
          border-color: var(--accent);
        }
        .dotnav :global(button:focus-visible) {
          outline: 2px solid var(--accent);
          outline-offset: 3px;
        }
        .dotnav :global(button.active) {
          background: var(--accent);
          border-color: var(--accent);
          transform: scale(1.3);
        }
        @media (max-width: 860px) {
          .dotnav {
            display: none;
          }
        }

        .badge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          padding: 3px 9px;
          border-radius: 999px;
          border: 1px solid transparent;
          white-space: nowrap;
        }
        .badge-llm {
          color: var(--accent);
          background: var(--accent-soft);
          border-color: color-mix(in srgb, var(--accent) 35%, transparent);
        }
        .badge-det {
          color: var(--good);
          background: var(--good-soft);
          border-color: color-mix(in srgb, var(--good) 35%, transparent);
        }
        .badge-flag {
          color: var(--flag);
          background: var(--flag-soft);
          border-color: color-mix(in srgb, var(--flag) 35%, transparent);
        }

        .slide-title .slide-body {
          justify-content: center;
          gap: 22px;
        }
        .wordmark {
          font-size: clamp(46px, 7.4vw, 84px);
          line-height: 1;
          letter-spacing: -0.03em;
          flex: none;
        }
        .wordmark :global(span) {
          color: var(--accent);
        }
        .tagline {
          font-size: 18px;
          color: var(--text-dim);
          max-width: 46ch;
          line-height: 1.5;
          flex: none;
        }
        .tagline :global(strong) {
          color: var(--text);
          font-weight: 600;
        }

        .edge-grid {
          display: grid;
          grid-template-columns: 1.15fr 0.85fr;
          gap: 40px;
          align-items: start;
          flex: none;
        }
        @media (max-width: 900px) {
          .edge-grid {
            grid-template-columns: 1fr;
          }
        }

        .ticker-panel {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 18px;
        }
        .ticker-panel .panel-label {
          font-family: var(--font-mono);
          font-size: 11px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: var(--text-faint);
          margin-bottom: 12px;
        }
        .ticker-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 8px;
        }
        @media (max-width: 480px) {
          .ticker-grid {
            grid-template-columns: repeat(3, 1fr);
          }
        }
        .ticker {
          font-family: var(--font-mono);
          font-size: 13px;
          font-weight: 600;
          text-align: center;
          padding: 9px 4px;
          border: 1px solid var(--border);
          border-radius: 8px;
          color: var(--text);
          background: var(--surface-2);
        }

        .callout-line {
          font-size: 14px;
          color: var(--text-dim);
          border-left: 2px solid var(--good);
          padding-left: 14px;
          line-height: 1.55;
          flex: none;
        }
        .callout-line :global(strong) {
          color: var(--text);
          font-weight: 600;
        }

        .flow {
          display: flex;
          align-items: stretch;
          gap: 0;
          flex-wrap: wrap;
          flex: none;
        }
        .flow-node {
          background: var(--surface);
          border: 1px solid var(--border);
          border-top: 2px solid var(--good);
          border-radius: 10px;
          padding: 13px 15px;
          min-width: 148px;
          max-width: 172px;
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .flow-node.llm {
          border-top-color: var(--accent);
        }
        .flow-node .fn-label {
          font-weight: 600;
          font-size: 14px;
        }
        .flow-node .fn-tag {
          font-family: var(--font-mono);
          font-size: 9.5px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: var(--text-faint);
        }
        .flow-node.llm .fn-tag {
          color: var(--accent);
        }
        .flow-node .fn-desc {
          font-size: 11px;
          color: var(--text-dim);
          line-height: 1.4;
          margin-top: 2px;
        }
        .flow-arrow {
          display: flex;
          align-items: center;
          padding: 0 8px;
          color: var(--border-strong);
          font-size: 16px;
          font-family: var(--font-mono);
          flex: none;
        }
        @media (max-width: 760px) {
          .flow {
            flex-direction: column;
            align-items: flex-start;
          }
          .flow-arrow {
            transform: rotate(90deg);
            padding: 3px 0 3px 20px;
          }
        }

        .regime-matrix {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 9px;
          flex: none;
        }
        @media (max-width: 640px) {
          .regime-matrix {
            grid-template-columns: 1fr;
          }
        }
        .regime-box {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 9px;
          padding: 11px 14px;
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .regime-box .rx-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .regime-box .rx-name {
          font-weight: 600;
          font-size: 13px;
        }
        .regime-box .rx-struct {
          font-size: 12px;
          color: var(--text-dim);
          line-height: 1.4;
        }
        .regime-box.stand-down .rx-struct {
          color: var(--text-faint);
          font-style: italic;
        }

        .regime-stress {
          background: var(--risk-soft);
          border: 1px solid color-mix(in srgb, var(--risk) 35%, transparent);
          border-radius: 9px;
          padding: 11px 16px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 14px;
          flex-wrap: wrap;
          flex: none;
        }
        .regime-stress .rs-name {
          font-weight: 600;
          font-size: 13px;
          color: var(--risk);
          white-space: nowrap;
        }
        .regime-stress .rs-detail {
          font-size: 12px;
          color: var(--text-dim);
        }

        .strategy-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 14px;
          flex: none;
        }
        @media (max-width: 900px) {
          .strategy-grid {
            grid-template-columns: 1fr;
          }
        }
        .strategy-card {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 16px 18px;
          display: flex;
          flex-direction: column;
          gap: 9px;
        }
        .strategy-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .strategy-name {
          font-weight: 600;
          font-size: 15px;
        }
        .strategy-desc {
          font-size: 12.5px;
          color: var(--text-dim);
          line-height: 1.5;
        }
        .strategy-regime {
          font-family: var(--font-mono);
          font-size: 10.5px;
          color: var(--text-faint);
          margin-top: auto;
          padding-top: 6px;
          border-top: 1px solid var(--border);
        }
        .strategy-regime :global(b) {
          color: var(--text-dim);
          font-weight: 600;
        }

        .pipeline {
          display: flex;
          flex-direction: column;
          gap: 0;
          flex: none;
        }
        .pstep {
          display: grid;
          grid-template-columns: 40px 1fr;
          gap: 16px;
          padding: 9px 0;
          border-bottom: 1px solid var(--border);
        }
        .pstep:last-child {
          border-bottom: none;
        }
        .pstep .pnum {
          font-family: var(--font-mono);
          font-size: 12.5px;
          color: var(--accent);
          padding-top: 1px;
        }
        .pstep .ptitle {
          font-weight: 600;
          font-size: 14px;
          margin-bottom: 2px;
        }
        .pstep .pdetail {
          font-size: 12.5px;
          color: var(--text-dim);
          line-height: 1.45;
        }
        .pstep .pdetail :global(code) {
          font-family: var(--font-mono);
          font-size: 11.5px;
          color: var(--text);
          background: var(--surface-2);
          padding: 1px 5px;
          border-radius: 4px;
        }

        .table-wrap {
          overflow-x: auto;
          border: 1px solid var(--border);
          border-radius: 12px;
          flex: none;
        }
        .table-wrap :global(table.risk) {
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
          background: var(--surface);
        }
        .table-wrap :global(table.risk td) {
          padding: 8px 18px;
          border-bottom: 1px solid var(--border);
        }
        .table-wrap :global(table.risk tr:last-child td) {
          border-bottom: none;
        }
        .table-wrap :global(table.risk td:first-child) {
          color: var(--text-dim);
        }
        .table-wrap :global(table.risk td:last-child) {
          text-align: right;
          font-family: var(--font-mono);
          font-variant-numeric: tabular-nums;
          font-weight: 600;
          color: var(--text);
        }
        .table-wrap :global(table.risk tr.hard td:last-child) {
          color: var(--risk);
        }

        .sot {
          display: flex;
          align-items: center;
          gap: 0;
          flex-wrap: wrap;
          flex: none;
        }
        .sot-node {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 18px 20px;
          flex: 1;
          min-width: 200px;
        }
        .sot-node .sn-title {
          font-weight: 600;
          font-size: 15px;
          margin-bottom: 7px;
        }
        .sot-node .sn-desc {
          font-size: 12.5px;
          color: var(--text-dim);
          line-height: 1.5;
        }
        .sot-node.process {
          background: var(--surface-2);
          text-align: center;
          color: var(--text-faint);
          font-family: var(--font-mono);
          font-size: 12px;
          max-width: 220px;
        }
        .sot-link {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 0 14px;
          color: var(--text-faint);
          font-family: var(--font-mono);
          font-size: 9.5px;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          gap: 4px;
          text-align: center;
        }
        .sot-link .arrows {
          font-size: 16px;
          color: var(--border-strong);
        }
        @media (max-width: 860px) {
          .sot {
            flex-direction: column;
            align-items: stretch;
          }
          .sot-link {
            padding: 8px 0;
          }
        }

        .stack-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 12px;
          flex: none;
        }
        @media (max-width: 760px) {
          .stack-grid {
            grid-template-columns: 1fr 1fr;
          }
        }
        @media (max-width: 480px) {
          .stack-grid {
            grid-template-columns: 1fr;
          }
        }
        .stack-box {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 15px 17px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .stack-box .sk-label {
          font-family: var(--font-mono);
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: var(--text-faint);
        }
        .stack-box .sk-value {
          font-size: 13px;
          color: var(--text-dim);
          line-height: 1.45;
        }
        .stack-box .sk-value :global(b) {
          font-weight: 600;
          color: var(--text);
        }
      `}</style>
    </div>
  );
}

export default function HowItWorksPage() {
  return (
    <div>
      <h1 className="page-title">How it works</h1>
      <p className="page-subtitle">
        vol-desk is an autonomous options-trading agent that runs unattended
        against an Alpaca paper account.
      </p>

      <div className="hiw-section">
        <h2>The edge</h2>
        <p>
          Implied volatility tends to price richer than the volatility that
          actually gets realized. vol-desk sells defined-risk premium
          structures &mdash; credit spreads and iron condors &mdash; on a
          fixed universe of seven liquid ETFs (SPY, QQQ, IWM, GLD, TLT, XLE,
          HYG) to harvest that spread, and occasionally buys debit spreads on
          high-conviction directional signals. Every position has a
          computable maximum loss before it&apos;s ever placed &mdash; there
          are no naked or undefined-risk trades anywhere in the system.
        </p>
      </div>

      <div className="hiw-section">
        <h2>Deterministic control loop, judgment at two points only</h2>
        <p>
          The system is a single Python process with an in-process
          scheduler. Almost everything is ordinary, deterministic code.
          Two specific decisions are handed to an LLM (via Groq): reading
          the volatility/trend regime, and picking exact contracts once a
          structure is unlocked. Position sizing, risk enforcement, and
          management triggers never touch an LLM &mdash; a check that can
          hallucinate isn&apos;t a check.
        </p>
        <div className="agent-grid">
          <div className="agent-card">
            <div className="agent-card-head">
              <span className="agent-name">Signal</span>
              <span className="agent-llm-tag llm-no">deterministic</span>
            </div>
            <p>
              Computes IV rank (percentile within a 60-day trailing window),
              20-day realized volatility, trend and range scores from price
              action. Gates whether the LLM even gets called &mdash; a
              quiet scan costs zero tokens.
            </p>
          </div>
          <div className="agent-card">
            <div className="agent-card-head">
              <span className="agent-name">Regime</span>
              <span className="agent-llm-tag llm-yes">LLM</span>
            </div>
            <p>
              Labels each symbol into one of seven regimes (range/trend
              &times; high/low IV, or stress) from a mechanical rule, with
              latitude for the model to deviate if it states a reason. Falls
              back to the mechanical label if the LLM is unavailable or
              malformed &mdash; never fabricates a default.
            </p>
          </div>
          <div className="agent-card">
            <div className="agent-card-head">
              <span className="agent-name">Strategy</span>
              <span className="agent-llm-tag llm-yes">LLM</span>
            </div>
            <p>
              Given the regime&apos;s permitted structures and a filtered
              option chain, picks contracts or declines. Every number it
              returns &mdash; width, credit, deltas &mdash; is independently
              recomputed from the chain afterward; the model&apos;s own
              arithmetic is never trusted.
            </p>
          </div>
          <div className="agent-card">
            <div className="agent-card-head">
              <span className="agent-name">Risk</span>
              <span className="agent-llm-tag llm-no">deterministic</span>
            </div>
            <p>
              The last gate before any order reaches Alpaca. Eight checks,
              every one logged whether it passes or fails. Can only reduce
              exposure &mdash; sizes down, vetoes, or halts. Never widens a
              trade to make it happen.
            </p>
          </div>
        </div>
      </div>

      <div className="hiw-section">
        <h2>Entry pipeline</h2>
        <ol className="flow-list">
          <li>
            <strong>Signal gate.</strong> Skip the symbol unless IV rank is
            available and above 0.35 &mdash; a cheap pre-filter that keeps
            token spend near zero on quiet days.
          </li>
          <li>
            <strong>Regime classification.</strong> Cached 30 minutes per
            symbol; only recomputed when the entry gate passes.
          </li>
          <li>
            <strong>Structure eligibility.</strong> The regime unlocks a
            fixed menu &mdash; e.g. range + high IV allows iron condors and
            credit spreads; range + low IV allows nothing at all (standing
            down is a normal, expected outcome).
          </li>
          <li>
            <strong>Contract selection.</strong> The strategy LLM picks
            exact strikes from a chain filtered to 7&ndash;14 DTE and within
            15% of spot, targeting 0.16&Delta; short legs.
          </li>
          <li>
            <strong>Risk evaluation.</strong> All eight checks run
            regardless of outcome: halt state, defined-risk structure,
            independently recomputed max loss, position/symbol/cluster
            caps, daily churn cap, sizing, cash headroom, DTE window.
          </li>
          <li>
            <strong>Price-ladder execution.</strong> A limit order steps
            through three rungs toward the market over 90 seconds. If it
            still doesn&apos;t fill, the order is abandoned &mdash; never
            widened, never chased.
          </li>
        </ol>
      </div>

      <div className="hiw-section">
        <h2>Risk management</h2>
        <p>
          Every position is sized to risk a fixed 1% of account equity at
          most, floored down (never rounded up) to whole contracts.
          Drawdown is measured continuously against the account&apos;s
          high-water mark:
        </p>
        <table className="param-table">
          <tbody>
            <tr>
              <td>Max risk per trade</td>
              <td>1% of equity</td>
            </tr>
            <tr>
              <td>Soft drawdown halt (blocks new entries)</td>
              <td>5% from high-water mark</td>
            </tr>
            <tr>
              <td>Hard drawdown halt (flattens everything)</td>
              <td>10% from high-water mark</td>
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
              <td>Equity-beta cluster cap (SPY+QQQ+IWM combined)</td>
              <td>3</td>
            </tr>
            <tr>
              <td>Take-profit</td>
              <td>50% of credit captured</td>
            </tr>
            <tr>
              <td>Stop-loss</td>
              <td>2&times; entry credit</td>
            </tr>
            <tr>
              <td>Force-close</td>
              <td>2 DTE, regardless of P/L</td>
            </tr>
          </tbody>
        </table>
        <p style={{ marginTop: 14 }}>
          A hard drawdown halt is terminal and manual to clear by design
          &mdash; the whole point of a kill switch is that it stays pulled
          until a human looks at why.
        </p>
      </div>

      <div className="hiw-section">
        <h2>Stack</h2>
        <p>
          Alpaca&apos;s MCP server is the sole path for orders, positions,
          and account state &mdash; no order is ever placed outside a
          dedicated execution module, and every one passes the risk check
          immediately before submission. Market data comes from Alpaca REST
          directly. Inference runs on Groq&apos;s free tier behind a
          model-agnostic client, so if Groq is unreachable the system
          degrades to hold-and-manage: existing positions keep being
          managed by deterministic rules, no new entries open, and nothing
          silently substitutes a weaker fallback for judgment it can&apos;t
          get.
        </p>
        <p>
          The full decision trail &mdash; every regime label, every
          strategy construction attempt, every risk veto with its exact
          reason &mdash; is logged to a local SQLite database on the host
          running the process. This dashboard reads live account and
          position data directly from Alpaca&apos;s API; it doesn&apos;t
          have access to that local decision log, so the running
          process&apos;s own internal halt state isn&apos;t reflected here
          &mdash; the &quot;Account Status&quot; shown on the overview page
          is Alpaca&apos;s own account-level status, not vol-desk&apos;s
          internal risk state.
        </p>
      </div>
    </div>
  );
}

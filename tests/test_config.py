from src import config as config_module


def test_load_succeeds_with_env_and_seeded_config(db_conn):
    cfg = config_module.load()
    assert cfg.symbol_tickers() == ["SPY", "QQQ", "IWM", "GLD", "TLT", "XLE", "HYG"]
    assert cfg.risk["max_concurrent_positions"] == 6
    assert cfg.strategy["dte_min"] == 7
    assert cfg.strategy["dte_max"] == 14


def test_symbol_lookup_returns_cluster():
    cfg = config_module.load()
    assert cfg.symbol("SPY").cluster == "equity_beta"
    assert cfg.symbol("GLD").cluster == "metals"


def test_unresolved_strike_increment_is_none_not_fabricated():
    cfg = config_module.load()
    # Q-003 is unresolved; config.py must not invent a value (CLAUDE.md rule 3)
    assert cfg.symbol("SPY").strike_increment is None

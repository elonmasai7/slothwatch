"""Single config object. Sources, lowest to highest priority:
defaults < slothwatch.toml < data_dir/ignore.txt < SLOTHWATCH_<FIELD> env vars < overrides."""
from __future__ import annotations
import hashlib, json, os, tomllib
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


@dataclass
class Config:
    data_dir: str = ".slothwatch"
    currency: str = "$"
    # ingest
    columns: dict = field(default_factory=dict)      # {"date": "Txn Date", "description": "...", "amount": "..."}
    amount_sign: str = "auto"                        # auto | negative_is_spend | positive_is_spend
    dayfirst: bool = False
    first_window_days: int = 31                      # first real run looks at the last N days
    # rung 0
    dupe_window_days: int = 3
    dupe_min_amount: float = 15.0
    same_cycle_tolerance: float = 0.005
    large_new_merchant: float = 300.0
    ignore_merchants: list = field(default_factory=list)
    allow_merchants: list = field(default_factory=list)
    # rung 1
    use_tabpfn: bool = True
    min_history: int = 4
    interval_lo: float = 0.05
    interval_hi: float = 0.95
    interval_floor_pct: float = 0.05
    mad_k: float = 3.5
    creep_len: int = 4
    creep_total_pct: float = 8.0
    # rung 1.5
    learning: bool = True
    care_min_decisions: int = 15
    care_threshold: float = 0.2
    never_suppress_amount: float = 500.0
    # rung 2
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout: float = 60.0
    # observability / notifications
    sentry_dsn: str = ""
    desktop_notify: bool = True
    # benchmark assumption: closed-API pricing per 1M tokens (an ESTIMATE, change freely)
    price_in_per_m: float = 0.50
    price_out_per_m: float = 1.50

    def path(self, *parts: str) -> Path:
        p = Path(self.data_dir).joinpath(*parts)
        (p.parent if parts and "." in parts[-1] else p).mkdir(parents=True, exist_ok=True)
        return p


def _cast(value: str, current):
    if isinstance(current, bool):
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


def load_config(path: str | None = None, **overrides) -> Config:
    cfg = Config()
    p = Path(path or os.environ.get("SLOTHWATCH_CONFIG", "slothwatch.toml"))
    names = {f.name for f in fields(Config)}
    if p.exists():
        for k, v in tomllib.loads(p.read_text()).items():
            if k in names:
                setattr(cfg, k, v)
    for f in fields(Config):
        env = os.environ.get(f"SLOTHWATCH_{f.name.upper()}")
        if env is not None and not isinstance(getattr(cfg, f.name), (dict, list)):
            setattr(cfg, f.name, _cast(env, getattr(cfg, f.name)))
    for k, v in overrides.items():
        if k in names and v is not None:
            setattr(cfg, k, v)
    ign = Path(cfg.data_dir) / "ignore.txt"
    if ign.exists():
        extra = [l.strip().upper() for l in ign.read_text().splitlines() if l.strip()]
        cfg.ignore_merchants = sorted(set(map(str.upper, cfg.ignore_merchants)) | set(extra))
    if not cfg.sentry_dsn:
        cfg.sentry_dsn = os.environ.get("SENTRY_DSN", "")
    return cfg


def config_hash(cfg: Config) -> str:
    d = asdict(cfg)
    for k in ("data_dir", "sentry_dsn"):
        d.pop(k, None)
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]

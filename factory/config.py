"""Central settings. Read from environment / .env (see .env.example)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent
# Data folders can live outside the repo (e.g. a Unity Catalog Volume on Databricks: see docs/DATABRICKS.md).
DATA_RAW = Path(os.getenv("MG_DATA_RAW", ROOT / "data" / "raw"))
DATA_GEN = Path(os.getenv("MG_DATA_GEN", ROOT / "data" / "generated"))
LAKEHOUSE = Path(os.getenv("MG_LAKEHOUSE", ROOT / "lakehouse"))
LEGACY_JOBS = ROOT / "legacy_jobs"
JOBS_YAML = LEGACY_JOBS / "jobs.yaml"
PROMPTS = ROOT / "factory" / "prompts"
DB_PATH = Path(os.getenv("FACTORY_DB", ROOT / "factory.db"))

LEGACY_MODE = os.getenv("LEGACY_MODE", "emulator")   # emulator | sqlserver
NEW_MODE = os.getenv("NEW_MODE", "spark")            # spark (falls back to duckdb, loudly) | duckdb
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "offline")  # offline | gemini | openai | azure | ollama
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))  # seconds per call; local models are slow on first load

MAX_ATTEMPTS = 3
# Relative tolerance for float comparison: engines sum floats in different orders.
# Finance-grade exactness is still enforced at 2 decimal places (see reconcile.py).
FLOAT_TOLERANCE = 1e-9
MONEY_DECIMALS = 2

TABLES = ["customers", "policies", "claims", "payments"]

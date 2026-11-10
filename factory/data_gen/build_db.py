"""M1 — build the insurance database both engines run on.

Base: freMTPL2 (real, public motor policies + claims) if data/raw/freMTPL2freq.csv and
freMTPL2sev.csv exist; otherwise a fully synthetic motor book. Then Faker adds customers,
HOME/TRAVEL lines and payments. Output: data/generated/*.csv + lakehouse/bronze/*.parquet.

By default the base data is CLEAN: the breaker agent injects the mess per job, which is the
point of the demo. `messy=True` adds legacy-style mess everywhere (for stress tests).
Same seed → identical data for the whole team.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pandas as pd
from faker import Faker

from factory import config

LINES_EXTRA = ["HOME", "TRAVEL"]
STATUSES = ["OPEN", "CLOSED", "CANCELLED", "REOPENED"]


def _messy(v: str, p: float) -> str:
    return random.choice([v.lower(), v + " ", v.title()]) if random.random() < p else v


def _rand_date(start: date, span_days: int) -> date:
    return start + timedelta(days=random.randint(0, span_days))


def _motor_from_fremtpl2(max_policies: int) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    freq_p, sev_p = config.DATA_RAW / "freMTPL2freq.csv", config.DATA_RAW / "freMTPL2sev.csv"
    if not (freq_p.exists() and sev_p.exists()):
        return None
    freq = pd.read_csv(freq_p)
    sev = pd.read_csv(sev_p)
    freq = freq[freq["IDpol"].isin(sev["IDpol"])].head(max_policies)  # keep policies that have claims
    policies = pd.DataFrame({
        "policy_id": "P" + freq["IDpol"].astype(int).astype(str),
        "line_of_business": "MOTOR",
        "region": freq["Region"].astype(str),
        "annual_premium": (freq["BonusMalus"] * 6.5).round(2),
    })
    sev = sev[sev["IDpol"].isin(freq["IDpol"])]
    claims = pd.DataFrame({
        "policy_id": "P" + sev["IDpol"].astype(int).astype(str),
        "line_of_business": "MOTOR",
        "claimed_amount": sev["ClaimAmount"].round(2),
    })
    return policies, claims


def _motor_synthetic(n_policies: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    regions = ["Dublin", "Cork", "Galway", "Limerick", "Belfast", "Manchester", "London"]
    policies = pd.DataFrame({
        "policy_id": [f"P{i:05d}" for i in range(1, n_policies + 1)],
        "line_of_business": "MOTOR",
        "region": [random.choice(regions) for _ in range(n_policies)],
        "annual_premium": [round(random.uniform(300, 2500), 2) for _ in range(n_policies)],
    })
    n_claims = int(n_policies * 1.5)
    claims = pd.DataFrame({
        "policy_id": [random.choice(policies.policy_id) for _ in range(n_claims)],
        "line_of_business": "MOTOR",
        "claimed_amount": [round(random.uniform(100, 20000), 2) for _ in range(n_claims)],
    })
    return policies, claims


def build_db(messy: bool = False, seed: int = 42, max_policies: int = 3000) -> dict[str, pd.DataFrame]:
    random.seed(seed)
    Faker.seed(seed)
    fake = Faker("en_IE")
    p = 0.08 if messy else 0.0

    real = _motor_from_fremtpl2(max_policies)
    source = "freMTPL2" if real else "synthetic"
    pol_m, clm_m = real if real else _motor_synthetic(max_policies)

    # extra lines of business (synthetic)
    n_extra = max(len(pol_m) // 2, 200)
    pol_x = pd.DataFrame({
        "policy_id": [f"PX{i:05d}" for i in range(1, n_extra + 1)],
        "line_of_business": [random.choice(LINES_EXTRA) for _ in range(n_extra)],
        "region": [random.choice(pol_m.region.unique()) for _ in range(n_extra)],
        "annual_premium": [round(random.uniform(150, 1800), 2) for _ in range(n_extra)],
    })
    policies = pd.concat([pol_m, pol_x], ignore_index=True)
    n_cust = max(len(policies) // 2, 100)
    customers = pd.DataFrame({
        "customer_id": [f"C{i:05d}" for i in range(1, n_cust + 1)],
        "full_name": [fake.name() for _ in range(n_cust)],
        "county": [fake.county() if hasattr(fake, "county") else fake.city() for _ in range(n_cust)],
    })
    policies["customer_id"] = [random.choice(customers.customer_id) for _ in range(len(policies))]
    policies["start_date"] = [_rand_date(date(2018, 1, 1), 2000).isoformat() for _ in range(len(policies))]

    clm_x = pd.DataFrame({
        "policy_id": [random.choice(pol_x.policy_id) for _ in range(len(pol_x))],
        "claimed_amount": [round(random.uniform(100, 15000), 2) for _ in range(len(pol_x))],
    })
    clm_x["line_of_business"] = clm_x.policy_id.map(pol_x.set_index("policy_id").line_of_business)
    claims = pd.concat([clm_m, clm_x], ignore_index=True)
    claims.insert(0, "claim_id", [f"CL{i:06d}" for i in range(1, len(claims) + 1)])
    claims["claim_date"] = [_rand_date(date(2019, 1, 1), 2000).isoformat() for _ in range(len(claims))]
    claims["status"] = [random.choice(STATUSES) for _ in range(len(claims))]
    claims["paid_amount"] = (claims.claimed_amount * [random.uniform(0.4, 1.0) for _ in range(len(claims))]).round(2)
    claims.loc[claims.sample(frac=0.03, random_state=seed).index, "paid_amount"] = None  # realistic NULLs
    if messy:
        claims["line_of_business"] = [_messy(v, p) for v in claims.line_of_business]
        claims["status"] = [_messy(v, p) for v in claims.status]

    payments = pd.DataFrame({
        "payment_id": [f"PY{i:07d}" for i in range(1, len(claims) + 1)],
        "claim_id": claims.claim_id.sample(frac=1.0, random_state=seed).values,
        "amount": [round(random.uniform(10, 8000), 4) for _ in range(len(claims))],  # 4dp → rounding cases
    })

    tables = {"customers": customers, "policies": policies, "claims": claims, "payments": payments}
    config.DATA_GEN.mkdir(parents=True, exist_ok=True)
    (config.LAKEHOUSE / "bronze").mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(config.DATA_GEN / f"{name}.csv", index=False)
        df.to_parquet(config.LAKEHOUSE / "bronze" / f"{name}.parquet", index=False)
        print(f"  {name:10s} {len(df):>7,} rows")
    print(f"  base motor data: {source}")
    return tables


def load_tables() -> dict[str, pd.DataFrame]:
    """Load the generated tables (build them first if missing)."""
    if not (config.DATA_GEN / "claims.csv").exists():
        return build_db()
    return {t: pd.read_csv(config.DATA_GEN / f"{t}.csv") for t in config.TABLES}

"""Generate 50 T-SQL and 50 SAS triage cases, ordered from simpler to harder."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def tsql_case(i: int) -> str:
    if i <= 10:
        filters = [
            f"c.claim_id = 'CL{i:06d}'", f"c.paid_amount > {100 + i * 10}",
            f"c.claim_date >= '2020-0{(i % 9) + 1}-01'", f"c.status = '{['OPEN','CLOSED','CANCELLED','REOPENED'][i % 4]}'",
            f"c.line_of_business = '{['MOTOR','HOME','TRAVEL'][i % 3]}'",
        ]
        return (f"SELECT c.policy_id, c.claim_id, c.paid_amount FROM claims AS c "
                f"WHERE {filters[(i - 1) % len(filters)]};")

    joins = 1 if i <= 18 else (2 if i <= 29 else (2 + (i % 2)))
    join_sql = " INNER JOIN policies AS p ON p.policy_id = c.policy_id"
    if joins >= 2:
        join_sql += " INNER JOIN customers AS u ON u.customer_id = p.customer_id"
    if joins >= 3:
        join_sql += " LEFT JOIN payments AS pay ON pay.claim_id = c.claim_id"
    where = f"c.claim_id >= 'CL{i:06d}'"
    if i % 2:
        where += f" AND c.paid_amount > {50 + i * 7}"

    select = ["c.policy_id", "p.region", "SUM(CASE WHEN c.status = 'CLOSED' "
              "THEN COALESCE(c.paid_amount, 0) ELSE 0 END) AS closed_paid"]
    group = " GROUP BY c.policy_id, p.region"
    if joins >= 2:
        select.append("COUNT(DISTINCT c.claim_id) AS claim_count")
        group += ", u.county" if joins >= 2 and i % 3 == 0 else ""
        if joins >= 2 and i % 3 == 0:
            select.append("u.county")
    if i >= 25:
        select.append("ROW_NUMBER() OVER (PARTITION BY c.policy_id ORDER BY c.claim_date DESC) AS recency")
    if i >= 32:
        select.append("SUM(c.paid_amount) OVER (PARTITION BY p.region ORDER BY c.claim_date "
                      "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_paid")
    if joins >= 3:
        select.append("COUNT(pay.payment_id) AS payment_rows")
        group += ", pay.payment_id"
    return ("SELECT " + ", ".join(select) + " FROM claims AS c" + join_sql
            + " WHERE " + where + group + ";")


def sas_case(i: int) -> str:
    out = f"sasb_{i:02d}"
    if i <= 10:
        return (f"proc sql; create table {out} as select policy_id, "
                f"sum(paid_amount) as paid_total from claims "
                f"where paid_amount > {50 + i * 10} group by policy_id; quit;")
    if i <= 20:
        return (f"data {out}; set claims; where paid_amount > {50 + i * 10}; "
                f"net_paid = coalesce(paid_amount, 0) * {1 + (i % 5) / 10:.1f}; "
                "keep policy_id claim_id net_paid claim_date; run;")
    if i <= 28:
        return (f"proc sort data=claims out=sorted_{i:02d}; by policy_id claim_date; run;\n"
                f"data {out}; set sorted_{i:02d}; by policy_id; retain running_paid 0; "
                "if first.policy_id then running_paid=0; running_paid+coalesce(paid_amount,0); "
                "if last.policy_id; keep policy_id running_paid; run;")
    if i <= 36:
        return (f"data {out}; merge claims(in=inc) policies(in=inp); by policy_id; "
                f"if inc and inp; if first.policy_id then sequence=0; retain sequence; "
                f"sequence+1; band=ifn(paid_amount>{500 + i * 50}, 'LARGE', 'OTHER'); run;")
    if i <= 44:
        return (f"%macro build_{i:02d}(threshold=500); proc sql; create table {out} as "
                f"select c.policy_id, count(*) as claim_count, sum(c.paid_amount) as paid_total "
                "from claims as c inner join policies as p on c.policy_id=p.policy_id "
                "where c.paid_amount > &threshold group by c.policy_id; quit; %mend; "
                f"%build_{i:02d}(threshold={300 + i * 25});")
    return (f"%macro portfolio_{i:02d}(source=claims, cutoff=2020-01-01); "
            f"proc sort data=&source out=ordered_{i:02d}; by policy_id claim_date; run; "
            f"data {out}; merge ordered_{i:02d}(in=has_claim) policies(in=has_policy); "
            "by policy_id; retain running_total prior_status; "
            "if first.policy_id then do; running_total=0; prior_status=''; end; "
            "if has_claim and has_policy and claim_date >= \"&cutoff\"d then do; "
            "running_total + coalesce(paid_amount,0); prior_status=status; end; "
            "if last.policy_id and has_claim then output; "
            "array amounts[3] amount1-amount3; do idx=1 to dim(amounts); "
            "amounts[idx]=running_total/idx; end; drop idx; run; "
            f"%portfolio_{i:02d}(source=claims, cutoff=2021-01-01);")


def main() -> None:
    tsql_dir, sas_dir = ROOT / "tsql", ROOT / "sas"
    tsql_dir.mkdir(parents=True, exist_ok=True)
    sas_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i in range(1, 51):
        job_id = f"BENCH_TSQL_{i:02d}"
        rel = f"fixtures/triage_benchmark/tsql/{i:02d}_case.sql"
        (ROOT.parents[1] / rel).write_text(tsql_case(i) + "\n")
        jobs.append({"job_id": job_id, "lang": "tsql", "source_path": rel,
                     "keys": ["policy_id"], "output": f"out_tsql_{i:02d}",
                     "feeds_report": f"Benchmark T-SQL case {i:02d}", "risk": "internal",
                     "wave": 1 if i <= 10 else (2 if i <= 24 else 3)})
    for i in range(1, 51):
        job_id = f"BENCH_SAS_{i:02d}"
        rel = f"fixtures/triage_benchmark/sas/{i:02d}_case.sas"
        (ROOT.parents[1] / rel).write_text(sas_case(i).replace("; ", ";\n") + "\n")
        jobs.append({"job_id": job_id, "lang": "sas", "source_path": rel,
                     "keys": ["policy_id"], "output": f"out_sas_{i:02d}",
                     "feeds_report": f"Benchmark SAS case {i:02d}", "risk": "internal",
                     "wave": 1 if i <= 10 else (2 if i <= 24 else 3)})
    manifest = ["# Generated synthetic benchmark jobs for triage; not production migrations.", "jobs:"]
    for job in jobs:
        manifest.append(f"  - job_id: {job['job_id']}")
        for key in ("lang", "source_path"):
            manifest.append(f"    {key}: {job[key]}")
        manifest.append("    keys: [policy_id]")
        for key in ("output", "feeds_report", "risk"):
            manifest.append(f"    {key}: \"{job[key]}\"")
        manifest.append(f"    wave: {job['wave']}")
    (ROOT / "jobs.yaml").write_text("\n".join(manifest) + "\n")


if __name__ == "__main__":
    main()

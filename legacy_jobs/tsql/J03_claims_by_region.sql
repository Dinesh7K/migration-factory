-- J03: Claims count by region. Its output table is not read by any report or job.
SELECT p.region, COUNT(*) AS n_claims
FROM claims AS c
JOIN policies AS p ON c.policy_id = p.policy_id
GROUP BY p.region

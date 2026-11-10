-- J06: Open exposure per region (open + reopened claims). Claims ops weekly exposure report.
SELECT p.region,
       COUNT(*) AS n_open,
       SUM(ISNULL(c.paid_amount, 0)) AS paid_open
FROM claims AS c
JOIN policies AS p ON c.policy_id = p.policy_id
WHERE c.status IN ('OPEN', 'REOPENED')
GROUP BY p.region

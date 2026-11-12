-- Outstanding reserve on open claims, by region. Feeds Finance reserving.
SELECT p.region,
       COUNT(*) AS open_claims,
       SUM(c.claimed_amount - ISNULL(c.paid_amount, 0)) AS outstanding
FROM claims c
JOIN policies p ON p.policy_id = c.policy_id
WHERE c.status IN ('OPEN', 'REOPENED')
GROUP BY p.region
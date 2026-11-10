-- J13: Days from policy start to its first claim (negative = claim dated before start; data
-- quality flag for underwriting).
SELECT c.policy_id,
       MIN(DATEDIFF(day, p.start_date, c.claim_date)) AS days_to_first_claim
FROM claims AS c
JOIN policies AS p ON c.policy_id = p.policy_id
GROUP BY c.policy_id

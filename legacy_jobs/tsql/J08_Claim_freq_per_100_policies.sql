SELECT p.region,
       COUNT(c.claim_id) * 100 / COUNT(DISTINCT p.policy_id) AS claims_per_100
FROM policies p
LEFT JOIN claims c ON c.policy_id = p.policy_id
GROUP BY p.region
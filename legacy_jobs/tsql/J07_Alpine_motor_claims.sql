SELECT p.region, SUM(c.paid_amount) AS total_paid
FROM claims c
JOIN policies p ON p.policy_id = c.policy_id
WHERE p.region LIKE '%alpes%'
GROUP BY p.region
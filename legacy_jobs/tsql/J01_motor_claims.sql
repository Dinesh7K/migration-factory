-- J01: Motor claims paid per policy. Feeds the Finance motor reserves report (regulatory).
SELECT policy_id, SUM(paid_amount) AS total_paid
FROM claims
WHERE line_of_business = 'MOTOR'
GROUP BY policy_id

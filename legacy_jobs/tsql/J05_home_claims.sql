-- J05: Home claims paid per policy. Same text-filter pattern as J01 (wave 2).
SELECT policy_id, SUM(paid_amount) AS total_paid
FROM claims
WHERE line_of_business = 'HOME'
GROUP BY policy_id

-- J14: Home claims that are closed or cancelled, paid per policy. Finance home settlement report.
SELECT policy_id,
       COUNT(*) AS n_settled,
       SUM(ISNULL(paid_amount, 0)) AS paid_settled
FROM claims
WHERE status IN ('CLOSED', 'CANCELLED')
  AND line_of_business = 'HOME'
GROUP BY policy_id

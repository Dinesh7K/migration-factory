-- J02: Written by another team years later. Same logic as J01, different aliases.
SELECT c.policy_id AS policy_id, SUM(c.paid_amount) AS total_paid
FROM claims AS c
WHERE c.line_of_business = 'MOTOR'
GROUP BY c.policy_id

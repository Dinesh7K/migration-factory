-- J11: Largest paid travel claim per policy (window function, ties broken by claim_id).
SELECT policy_id, claim_id, paid_amount
FROM (
    SELECT policy_id, claim_id, paid_amount,
           ROW_NUMBER() OVER (PARTITION BY policy_id ORDER BY paid_amount DESC, claim_id) AS rn
    FROM claims
    WHERE line_of_business = 'TRAVEL'
) AS t
WHERE rn = 1

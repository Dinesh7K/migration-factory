-- Several nested query features: CTEs, joins, conditional aggregation and windows.
WITH policy_claims AS (
    SELECT
        p.policy_id,
        p.line_of_business,
        c.claim_id,
        c.status,
        c.paid_amount,
        c.claim_date,
        ROW_NUMBER() OVER (
            PARTITION BY p.policy_id
            ORDER BY c.claim_date DESC, c.claim_id DESC
        ) AS recency_rank
    FROM policies AS p
    INNER JOIN claims AS c ON c.policy_id = p.policy_id
    WHERE c.status LIKE 'C%'
),
policy_rollup AS (
    SELECT
        policy_id,
        line_of_business,
        SUM(CASE WHEN status = 'CLOSED' THEN paid_amount ELSE 0 END) AS closed_paid,
        COUNT(DISTINCT claim_id) AS claim_count,
        MAX(CASE WHEN recency_rank = 1 THEN claim_date END) AS latest_claim_date
    FROM policy_claims
    GROUP BY policy_id, line_of_business
)
SELECT
    policy_id,
    line_of_business,
    closed_paid / NULLIF(claim_count, 0) AS average_paid_per_claim,
    latest_claim_date,
    DENSE_RANK() OVER (PARTITION BY line_of_business ORDER BY closed_paid DESC) AS paid_rank
FROM policy_rollup
WHERE claim_count > 0;

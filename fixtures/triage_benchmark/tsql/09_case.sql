SELECT c.policy_id, c.claim_id, c.paid_amount FROM claims AS c WHERE c.status = 'CLOSED';

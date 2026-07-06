// queries/pending_review.cypher
// Inferred edges awaiting human review.
MATCH (a:Device)-[r]->(b:Device)
WHERE r.inferred = true AND r.review_status IN ['pending', 'flagged']
RETURN a.device_id AS src, type(r) AS rel, b.device_id AS dst,
       r.confidence AS confidence, r.source AS source, r.review_status AS status
ORDER BY confidence DESC
LIMIT 200;

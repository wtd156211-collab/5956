SELECT region, status, count(*) AS n FROM logs WHERE latency_ms > 1000 GROUP BY region, status ORDER BY region, status

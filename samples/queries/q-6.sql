SELECT region, count(*) AS total, count(region) AS with_region, sum(latency_ms) AS total_latency FROM logs WHERE level = 'WARN' AND NOT status = 200 GROUP BY region ORDER BY region

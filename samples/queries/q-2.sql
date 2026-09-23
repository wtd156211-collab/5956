SELECT service, count(*) AS n, avg(latency_ms) AS avg_latency FROM logs WHERE level = 'ERROR' GROUP BY service ORDER BY n DESC LIMIT 3

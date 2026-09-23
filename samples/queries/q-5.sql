SELECT max(latency_ms) AS worst, min(latency_ms) AS best, avg(latency_ms) AS mean FROM logs WHERE region = 'cn-east'

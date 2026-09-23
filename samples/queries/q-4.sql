SELECT ts, service, latency_ms FROM logs WHERE service = 'pay' AND status = 500 AND latency_ms >= 3000 LIMIT 5

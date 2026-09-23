SCHEMA: dict[str, str] = {
    "ts": "string",
    "level": "string",
    "service": "string",
    "region": "string",
    "status": "int",
    "latency_ms": "int",
}

FIELDNAMES = tuple(SCHEMA)

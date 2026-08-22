import math
import os
import time
from flask import Flask, jsonify, request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

REQUESTS = Counter("arm_http_requests_total", "HTTP requests", ["path", "status"])
LATENCY = Histogram(
    "arm_http_request_seconds",
    "Application request latency",
    ["path"],
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)


def burn_cpu(milliseconds: float) -> None:
    """Consume approximately `milliseconds` of CPU time in the current worker."""
    target = max(0.0, milliseconds) / 1000.0
    start = time.process_time()
    x = 0.123456789
    while time.process_time() - start < target:
        # Arithmetic keeps the loop CPU-bound while avoiding optimizer shortcuts.
        x = math.sin(x) * math.cos(x) + 1.0000001
    if x == -1.0:  # unreachable; prevents lint complaints about x being unused.
        raise RuntimeError("unreachable")


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.get("/")
def index():
    return jsonify(
        service="agentic-resource-manager-stage1",
        hostname=os.getenv("HOSTNAME", "unknown"),
        endpoints=["/work?cpu_ms=8", "/healthz", "/metrics"],
    )


@app.get("/work")
def work():
    cpu_ms = float(request.args.get("cpu_ms", os.getenv("DEFAULT_CPU_MS", "8")))
    started = time.perf_counter()
    status = "200"
    try:
        burn_cpu(cpu_ms)
        return jsonify(ok=True, cpu_ms=cpu_ms, hostname=os.getenv("HOSTNAME", "unknown"))
    except Exception:
        status = "500"
        raise
    finally:
        elapsed = time.perf_counter() - started
        REQUESTS.labels(path="/work", status=status).inc()
        LATENCY.labels(path="/work").observe(elapsed)


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

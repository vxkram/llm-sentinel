"""Load test comparing gateway overhead against the mock it proxies to.

    locust -f tests/load/locustfile.py --headless -u 200 -r 50 -t 60s

Both user classes hardcode their own host (gateway vs mock directly) so
they run concurrently in one invocation - no --host flag needed. GatewayUser
hits the real /v1/chat/completions endpoint (auth, rate limiting, budget
check, circuit breaker check, fallback resolution, then the actual
provider call). DirectMockUser sends the identical payload straight to the
mock server, skipping the gateway entirely. Comparing the two Locust "Name"
rows in the same run's results isolates the gateway's own added latency
from the underlying model call's latency.
"""

import os

from locust import HttpUser, between, task

GATEWAY_HOST = os.environ.get("LOCUST_GATEWAY_HOST", "http://localhost:8010")
MOCK_HOST = os.environ.get("LOCUST_MOCK_HOST", "http://localhost:8011")

API_KEY = "sk-loadtest-demo-777888"
PAYLOAD = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is 2+2? Answer in one word."}],
}


class GatewayUser(HttpUser):
    host = GATEWAY_HOST
    wait_time = between(0, 0)

    @task
    def chat_via_gateway(self):
        self.client.post(
            "/v1/chat/completions",
            json=PAYLOAD,
            headers={"Authorization": f"Bearer {API_KEY}"},
            name="/v1/chat/completions (via gateway)",
        )


class DirectMockUser(HttpUser):
    host = MOCK_HOST
    wait_time = between(0, 0)

    @task
    def chat_direct(self):
        self.client.post(
            "/v1/chat/completions",
            json=PAYLOAD,
            headers={"Authorization": "Bearer mock-key"},
            name="/v1/chat/completions (direct to mock, no gateway)",
        )

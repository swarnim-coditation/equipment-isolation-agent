import json
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:  # Keep the local runner usable in the Gremlin-only venv.
    requests = None


class Plant360Client:
    def __init__(self, api_config):
        self.base_url = api_config.base_url.rstrip("/")
        self.auth_token = api_config.auth_token
        self.verify_ssl = api_config.verify_ssl

    def _headers(self, accept="application/json"):
        headers = {"Accept": accept}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def get_json(self, path):
        url = f"{self.base_url}{path}"
        if requests is not None:
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=60,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            return response.json()

        context = None if self.verify_ssl else ssl._create_unverified_context()
        request = Request(url, headers=self._headers(), method="GET")
        try:
            with urlopen(request, timeout=60, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {body}") from exc

    def get_bytes(self, path):
        url = f"{self.base_url}{path}"
        if requests is not None:
            response = requests.get(
                url,
                headers=self._headers(accept="*/*"),
                timeout=60,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            return response.content, response.headers.get("Content-Type", "")

        context = None if self.verify_ssl else ssl._create_unverified_context()
        request = Request(url, headers=self._headers(accept="*/*"), method="GET")
        try:
            with urlopen(request, timeout=60, context=context) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {body}") from exc

    def job_details(self, job_id):
        return self.get_json(f"/jobs/get_job_details/{job_id}")

    def hilt_graph(self, job_id):
        return self.get_json(f"/jobs/get_job_hilt_graph/{job_id}")

    def stlm_symbols(self, job_id):
        return self.get_json(f"/symbol_text_line_master/get_stl_master_by_job_id/{job_id}")

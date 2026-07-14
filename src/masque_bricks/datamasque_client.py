"""DataMasque API client for file masking operations."""

import time
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import DataMasqueConfig


class DataMasqueError(Exception):
    """Exception raised for DataMasque API errors."""

    pass


class DataMasqueClient:
    """Client for interacting with the DataMasque API."""

    def __init__(self, config: DataMasqueConfig, verify_ssl: bool | None = None):
        """Initialize the DataMasque client.

        Authentication is deferred until the first API call so that constructing the client
        against a misconfigured / unreachable host does not abort other commands that don't
        actually use DataMasque.

        Args:
            config: DataMasque API configuration.
            verify_ssl: Whether to verify SSL certificates. Defaults to ``config.verify_ssl``
                (default secure). Pass False only for self-signed certs you trust.
        """
        self.config = config
        self.base_url = config.host.rstrip("/")
        self.verify_ssl = config.verify_ssl if verify_ssl is None else verify_ssl
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        # Retry only idempotent calls on transient network / 5xx / 429 errors with backoff.
        # POST/PATCH are excluded: a 5xx after DataMasque has already processed the request
        # would otherwise re-send it and launch duplicate masking runs / connections / rulesets.
        retry = Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._authenticated = False

        # Only suppress the insecure-request warning when the user has explicitly
        # opted out of verification — otherwise leave warnings intact.
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _ensure_authenticated(self) -> None:
        """Authenticate on first use and cache the token on the session."""
        if self._authenticated:
            return
        token = self._login(self.config.username, self.config.password)
        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            }
        )
        self._authenticated = True

    def _login(self, username: str, password: str) -> str:
        """Authenticate with username/password and get API token.

        Args:
            username: DataMasque username.
            password: DataMasque password.

        Returns:
            API token string.

        Raises:
            DataMasqueError: If authentication fails.
        """
        url = f"{self.base_url}/api/auth/token/login/"
        response = self.session.post(
            url,
            json={"username": username, "password": password},
        )

        if not response.ok:
            raise DataMasqueError(
                f"Authentication failed: {response.status_code} - {response.text}"
            )

        return response.json()["key"]

    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Make an API request.

        Args:
            method: HTTP method.
            endpoint: API endpoint (without base URL).
            **kwargs: Additional arguments for requests.

        Returns:
            JSON response as a dictionary.

        Raises:
            DataMasqueError: If the request fails.
        """
        self._ensure_authenticated()
        url = f"{self.base_url}{endpoint}"
        response = self.session.request(method, url, **kwargs)

        if not response.ok:
            raise DataMasqueError(
                f"API request failed: {response.status_code} - {response.text}"
            )

        if response.content:
            return response.json()
        return {}

    def _paginate(self, endpoint: str) -> list[dict[str, Any]]:
        """Walk a paginated DRF-style endpoint and return all results.

        Tolerates both bare-list and ``{"results": [...], "next": "..."}`` responses.
        """
        results: list[dict[str, Any]] = []
        url = endpoint
        while url:
            page = self._request("GET", url)
            if isinstance(page, list):
                return results + page
            results.extend(page.get("results", []))
            next_url = page.get("next")
            if not next_url:
                break
            # ``next`` is an absolute URL; strip the base so _request can re-prepend it.
            url = next_url[len(self.base_url):] if next_url.startswith(self.base_url) else next_url
        return results

    def list_connections(self) -> list[dict[str, Any]]:
        """List all connections."""
        return self._paginate("/api/connections/")

    def get_connection_by_name(self, name: str) -> dict[str, Any] | None:
        """Get a connection by name.

        Args:
            name: Connection name.

        Returns:
            Connection object or None if not found.
        """
        connections = self.list_connections()
        for conn in connections:
            if conn.get("name") == name:
                return conn
        return None

    def create_s3_connection(
        self,
        name: str,
        bucket: str,
        base_directory: str,
        is_source: bool = True,
        is_destination: bool = False,
        iam_role_arn: str | None = None,
    ) -> dict[str, Any]:
        """Create an S3 file masking connection.

        Args:
            name: Connection name.
            bucket: S3 bucket name.
            base_directory: Base directory (prefix) in the bucket.
            is_source: Whether this can be used as a file masking source.
            is_destination: Whether this can be used as a file masking destination.
            iam_role_arn: Optional IAM role ARN for cross-account access.

        Returns:
            Created connection object.
        """
        payload = {
            "version": "1.0",
            "name": name,
            "type": "s3_connection",
            "mask_type": "file",
            "bucket": bucket,
            "base_directory": base_directory,
            "is_file_mask_source": is_source,
            "is_file_mask_destination": is_destination,
        }

        if iam_role_arn:
            payload["iam_role_arn"] = iam_role_arn

        return self._request("POST", "/api/connections/", json=payload)

    def get_or_create_connection(
        self,
        name: str,
        bucket: str,
        base_directory: str,
        is_source: bool = True,
        is_destination: bool = False,
        iam_role_arn: str | None = None,
    ) -> dict[str, Any]:
        """Get an existing connection by name or create a new one.

        Args:
            name: Connection name.
            bucket: S3 bucket name.
            base_directory: Base directory (prefix) in the bucket.
            is_source: Whether this can be used as a source.
            is_destination: Whether this can be used as a destination.
            iam_role_arn: Optional IAM role ARN for cross-account access.

        Returns:
            Connection object.
        """
        existing = self.get_connection_by_name(name)
        if existing:
            return existing

        return self.create_s3_connection(
            name=name,
            bucket=bucket,
            base_directory=base_directory,
            is_source=is_source,
            is_destination=is_destination,
            iam_role_arn=iam_role_arn,
        )

    def list_rulesets(self) -> list[dict[str, Any]]:
        """List all rulesets."""
        return self._paginate("/api/rulesets/")

    def get_ruleset_by_name(self, name: str) -> dict[str, Any] | None:
        """Get a ruleset by name.

        Args:
            name: Ruleset name.

        Returns:
            Ruleset object or None if not found.
        """
        rulesets = self.list_rulesets()
        for ruleset in rulesets:
            if ruleset.get("name") == name:
                return ruleset
        return None

    def create_ruleset(self, name: str, config_yaml: str) -> dict[str, Any]:
        """Create a new ruleset.

        Args:
            name: Ruleset name.
            config_yaml: Ruleset configuration in YAML format.

        Returns:
            Created ruleset object.
        """
        payload = {
            "name": name,
            "config_yaml": config_yaml,
            "mask_type": "file",
        }
        return self._request("POST", "/api/rulesets/", json=payload)

    def update_ruleset(self, ruleset_id: int, config_yaml: str) -> dict[str, Any]:
        """Update an existing ruleset.

        Args:
            ruleset_id: Ruleset ID.
            config_yaml: New ruleset configuration in YAML format.

        Returns:
            Updated ruleset object.
        """
        payload = {
            "config_yaml": config_yaml,
        }
        return self._request("PATCH", f"/api/rulesets/{ruleset_id}/", json=payload)

    def get_or_create_ruleset(
        self,
        name: str,
        config_yaml: str,
        update_if_exists: bool = False,
    ) -> dict[str, Any]:
        """Get an existing ruleset by name or create a new one.

        Args:
            name: Ruleset name.
            config_yaml: Ruleset configuration in YAML format.
            update_if_exists: If True, update the ruleset if it exists.

        Returns:
            Ruleset object.
        """
        existing = self.get_ruleset_by_name(name)
        if existing:
            if update_if_exists:
                return self.update_ruleset(existing["id"], config_yaml)
            return existing

        return self.create_ruleset(name=name, config_yaml=config_yaml)

    def start_masking_run(
        self,
        name: str,
        source_connection_id: str,
        destination_connection_id: str,
        ruleset_id: str | int,
    ) -> dict[str, Any]:
        """Start a file masking run.

        Args:
            name: Run name.
            source_connection_id: Source connection UUID.
            destination_connection_id: Destination connection UUID.
            ruleset_id: Ruleset ID.

        Returns:
            Created run object.
        """
        payload = {
            "name": name,
            "mask_type": "file",
            "source_connection": source_connection_id,
            "destination_connection": destination_connection_id,
            "ruleset": ruleset_id,
            "options": {},
        }
        return self._request("POST", "/api/runs/", json=payload)

    def get_run(self, run_id: int) -> dict[str, Any]:
        """Get a run by ID.

        Args:
            run_id: Run ID.

        Returns:
            Run object.
        """
        return self._request("GET", f"/api/runs/{run_id}/")

    def wait_for_run(
        self,
        run_id: int,
        timeout: int = 3600,
        poll_interval: int = 10,
    ) -> dict[str, Any]:
        """Wait for a run to complete.

        Args:
            run_id: Run ID.
            timeout: Maximum time to wait in seconds.
            poll_interval: Time between status checks in seconds.

        Returns:
            Final run object.

        Raises:
            DataMasqueError: If the run fails or times out.
        """
        start_time = time.time()

        while True:
            try:
                run = self.get_run(run_id)
            except (DataMasqueError, requests.RequestException) as exc:
                # A transient blip while polling shouldn't kill a long-running wait;
                # keep polling until the overall timeout is hit.
                if time.time() - start_time > timeout:
                    raise DataMasqueError(
                        f"Masking run polling failed after {timeout} seconds: {exc}"
                    ) from exc
                time.sleep(poll_interval)
                continue

            status = run.get("status")

            if status == "finished":
                return run

            if status == "failed":
                raise DataMasqueError(f"Masking run failed: {run.get('error_message')}")

            if status == "cancelled":
                raise DataMasqueError("Masking run was cancelled")

            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise DataMasqueError(f"Masking run timed out after {timeout} seconds")

            time.sleep(poll_interval)

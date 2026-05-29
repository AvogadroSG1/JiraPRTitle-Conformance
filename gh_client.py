"""Thin wrapper around `gh api graphql` with rate limiting and retry."""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RateLimitExhausted(Exception):
    pass


class GraphQLError(Exception):
    pass


class GitHubGraphQLClient:
    def __init__(self, rate_limit_buffer: int = 500, max_retries: int = 3):
        self.rate_limit_buffer = rate_limit_buffer
        self.max_retries = max_retries
        self.remaining = None
        self.reset_at = None

    def query(self, graphql: str, variables: dict | None = None) -> dict:
        """Execute GraphQL query with retry and rate limit awareness."""
        for attempt in range(self.max_retries):
            try:
                result = self._execute(graphql, variables)
                if 'errors' in result:
                    error_msg = result['errors'][0].get('message', 'Unknown error')
                    if 'rate limit' in error_msg.lower():
                        self._wait_for_reset()
                        continue
                    raise GraphQLError(error_msg)
                self._update_rate_limit(result)
                return result
            except subprocess.TimeoutExpired:
                logger.warning("Request timed out (attempt %d/%d)", attempt + 1, self.max_retries)
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except subprocess.CalledProcessError as e:
                logger.warning("gh CLI error (attempt %d/%d): %s", attempt + 1, self.max_retries, e.stderr[:200] if e.stderr else "")
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        raise GraphQLError("Max retries exceeded")

    def _execute(self, graphql: str, variables: dict | None) -> dict:
        cmd = ['gh', 'api', 'graphql', '-f', f'query={graphql}']
        if variables:
            for key, value in variables.items():
                cmd.extend(['-f', f'{key}={value}'])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout)

    def _update_rate_limit(self, response: dict):
        rate_limit = response.get('data', {}).get('rateLimit')
        if rate_limit:
            self.remaining = rate_limit.get('remaining')
            self.reset_at = rate_limit.get('resetAt')
            logger.debug("Rate limit: %d remaining, resets at %s", self.remaining, self.reset_at)
            if self.remaining is not None and self.remaining < self.rate_limit_buffer:
                logger.warning("Rate limit low (%d remaining), waiting for reset", self.remaining)
                self._wait_for_reset()

    def _wait_for_reset(self):
        if not self.reset_at:
            time.sleep(60)
            return
        reset_time = datetime.fromisoformat(self.reset_at.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        wait_seconds = max(0, (reset_time - now).total_seconds()) + 5
        logger.info("Waiting %.0f seconds for rate limit reset", wait_seconds)
        time.sleep(wait_seconds)

    def check_rate_limit(self) -> tuple[int, str]:
        """Query current rate limit status. Returns (remaining, resetAt)."""
        result = self.query("""
            query { rateLimit { remaining resetAt limit } }
        """)
        rl = result['data']['rateLimit']
        self.remaining = rl['remaining']
        self.reset_at = rl['resetAt']
        return rl['remaining'], rl['resetAt']

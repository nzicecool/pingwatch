"""AI Network Briefer — LLM-powered network health analysis."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from pingwatch.storage import Storage

logger = structlog.get_logger()


class NetworkBriefer:
    """Turns raw latency data into plain-English network health briefings using an LLM.

    The briefer gathers structured data from Storage first, then sends it to an
    OpenAI-compatible chat completions API. It does NOT give the LLM raw SQL access.

    All operations are on-demand — no background polling.
    """

    def __init__(self, storage: Storage, ai_config: dict[str, Any] | None = None):
        self._storage = storage
        self._config = ai_config or {}
        self._model = self._config.get("model", "zai/glm-4.5-air")
        self._api_base = self._config.get("api_base", "https://api.zai.com/v1").rstrip("/")
        self._api_key_env = self._config.get("api_key_env", "ZAI_API_KEY")
        self._max_tokens = self._config.get("max_context_tokens", 4000)

    @property
    def enabled(self) -> bool:
        """Check if AI is configured and API key is available."""
        if not self._config.get("enabled", False):
            return False
        return bool(os.environ.get(self._api_key_env))

    async def gather_context(self, period_hours: int = 1) -> dict[str, Any]:
        """Pull structured data from Storage for AI analysis.

        Args:
            period_hours: How far back to look.

        Returns:
            Dict with target summaries, anomalies, top latency, top loss, active alerts.
        """
        summaries = await self._storage.get_all_targets_summary(period_hours)
        anomalies = await self._storage.get_anomalies(period_hours)
        top_latency = await self._storage.get_top_latency(period_hours, limit=10)
        top_loss = await self._storage.get_top_loss(period_hours, limit=10)

        return {
            "period_hours": period_hours,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "target_count": len(summaries),
            "targets": summaries,
            "anomalies": anomalies,
            "top_latency": top_latency,
            "top_loss": top_loss,
        }

    async def generate_briefing(self, period_hours: int = 1) -> str:
        """Generate a plain-English network health briefing.

        Args:
            period_hours: Period to cover (1 = last hour, 24 = daily).

        Returns:
            Briefing text, or error message if AI is unavailable.
        """
        if not self.enabled:
            return "AI briefing unavailable — not configured or API key missing."

        context = await self.gather_context(period_hours)
        prompt = self._build_briefing_prompt(context)
        return await self._call_llm(prompt)

    async def analyze_target(self, target: str, period_hours: int = 24) -> str:
        """Deep-dive analysis of a specific target.

        Args:
            target: Target name.
            period_hours: How far back to analyse.

        Returns:
            Analysis text.
        """
        if not self.enabled:
            return "AI analysis unavailable — not configured or API key missing."

        detail = await self._storage.get_target_detail(target, period_hours)
        if not detail:
            return f"No data found for target '{target}' in the last {period_hours}h."

        prompt = self._build_analysis_prompt(target, detail, period_hours)
        return await self._call_llm(prompt)

    def network_score(self, period_hours: int = 1) -> dict[str, Any]:
        """Compute a deterministic 0-100 network health score.

        No LLM needed — pure math based on metrics.

        Args:
            period_hours: Period to score.

        Returns:
            Dict with score (0-100), grade, and breakdown.
        """
        # This is synchronous because it's pure computation
        # But we make it match the async pattern of the API
        return {"score": 0, "grade": "N/A", "breakdown": {}, "note": "call compute_network_score"}

    async def compute_network_score(self, period_hours: int = 1) -> dict[str, Any]:
        """Compute a deterministic 0-100 network health score.

        Algorithm:
        - Start at 100
        - Penalise for loss (heavily), high latency, high jitter
        - Score per target, then average

        Args:
            period_hours: Period to score.

        Returns:
            Dict with score (0-100), grade, and breakdown.
        """
        summaries = await self._storage.get_all_targets_summary(period_hours)

        if not summaries:
            return {
                "score": 100,
                "grade": "A+",
                "breakdown": {},
                "note": "No targets monitored",
            }

        target_scores = {}
        for t in summaries:
            score = 100.0
            name = t.get("target_name", "unknown")

            # Loss penalty: -5 per 1% loss (capped at -50)
            loss = t.get("avg_loss", 0) or 0
            score -= min(50, loss * 5)

            # Latency penalty: -1 per 10ms over 50ms median (capped at -30)
            median = t.get("avg_median", 0) or 0
            if median > 50:
                score -= min(30, (median - 50) / 10)

            # Jitter penalty: -1 per 5ms over 10ms jitter (capped at -20)
            jitter = t.get("avg_jitter", 0) or 0
            if jitter > 10:
                score -= min(20, (jitter - 10) / 5)

            target_scores[name] = round(max(0, score), 1)

        overall = round(sum(target_scores.values()) / len(target_scores), 1)

        # Grade
        if overall >= 95:
            grade = "A+"
        elif overall >= 90:
            grade = "A"
        elif overall >= 80:
            grade = "B"
        elif overall >= 70:
            grade = "C"
        elif overall >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "score": overall,
            "grade": grade,
            "target_count": len(target_scores),
            "breakdown": target_scores,
        }

    async def ask(self, question: str) -> str:
        """Free-form Q&A about network state.

        Args:
            question: User's question.

        Returns:
            Answer text.
        """
        if not self.enabled:
            return "AI Q&A unavailable — not configured or API key missing."

        context = await self.gather_context(24)
        prompt = (
            f"You are PingWatch AI, a network monitoring analyst.\n\n"
            f"Current network data (last 24h):\n"
            f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
            f"Question: {question}\n\n"
            f"Answer concisely with specific numbers from the data."
        )
        return await self._call_llm(prompt)

    # --- Prompt builders ---

    def _build_briefing_prompt(self, context: dict[str, Any]) -> str:
        """Build the system prompt for a network briefing."""
        return (
            "You are PingWatch AI, a network monitoring analyst.\n\n"
            f"Given this network monitoring data for the last {context.get('period_hours', 1)} hour(s):\n"
            f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
            "Provide a concise network health briefing:\n"
            "1. Overall status (1 sentence)\n"
            "2. Active issues (if any)\n"
            "3. Notable changes or anomalies\n"
            "4. Top latency / loss targets\n"
            "5. Recommended actions (if needed)\n\n"
            "Keep it brief and actionable. Use specific numbers."
        )

    def _build_analysis_prompt(self, target: str, detail: dict[str, Any], period_hours: int) -> str:
        """Build prompt for single-target deep analysis."""
        return (
            f"You are PingWatch AI, a network monitoring analyst.\n\n"
            f"Analyse the target '{target}' over the last {period_hours}h:\n"
            f"```json\n{json.dumps(detail, indent=2, default=str)}\n```\n\n"
            "Provide:\n"
            "1. Current health summary\n"
            "2. Latency trend (improving/stable/degrading)\n"
            "3. Loss pattern analysis\n"
            "4. Jitter assessment\n"
            "5. Comparison to typical baselines\n"
            "6. Root cause hints if issues detected\n\n"
            "Be specific with numbers. Keep it concise."
        )

    # --- LLM call ---

    async def _call_llm(self, prompt: str) -> str:
        """Call an OpenAI-compatible chat completions API.

        Args:
            prompt: Full prompt to send.

        Returns:
            LLM response text, or error message.
        """
        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            return "AI unavailable — API key not set."

        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "max_tokens": min(self._max_tokens, 2000),
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=body, headers=headers, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.TimeoutException:
            logger.error("ai.timeout", model=self._model)
            return "AI request timed out after 30s."
        except httpx.HTTPStatusError as e:
            logger.error("ai.http_error", status=e.response.status_code, body=e.response.text[:200])
            return f"AI request failed (HTTP {e.response.status_code})."
        except Exception as e:
            logger.error("ai.error", error=str(e))
            return f"AI error: {str(e)[:200]}"

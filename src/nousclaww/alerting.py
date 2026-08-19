"""Threshold-based alert engine for monitoring metrics.

Evaluates monitoring metrics against user-defined declarative threshold rules.
When a threshold is breached, an Alert is generated and logged to bounded
history. Suppression prevents alert storms — the same rule won't re-fire
within a configurable window.

Architecture:
  1. AlertRule — declarative data structure (serializable, loadable from config)
  2. Alert — a triggered alert with timestamp, value, and severity
  3. AlertEngine — evaluates rules against a metrics dict, maintains history

Invariants:
  1. Alerting is local — no notifications leave the machine
  2. Alerting is read-only — evaluates metrics, never modifies them
  3. Alert history is bounded — circular buffer, oldest evicted
  4. Alert rules are declarative — data, not code
  5. Suppression — repeated alerts for the same rule are deduplicated
  6. Severity is ordered — CRITICAL > WARNING > INFO

SYNTH:
    purpose: Declarative threshold-based alert engine that evaluates metrics dicts and generates bounded, suppressed alerts on breach
    axioms: [local_first, evidence_over_intuition, honest_failure_over_fake_success, open_process]
    objective: Any monitoring stats dict can be evaluated against serializable rules; alerts are suppressed within a window, history is bounded, and severity is ordered
    anti_patterns:
        - Using callbacks that could modify monitoring state (read-only evaluation only)
        - Unbounded alert history (must use circular buffer)
        - Importing the monitoring module directly (accept stats dict, stay decoupled)
        - Allowing alert storms (suppression window must be enforced)
        - Evaluating disabled rules
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/alerting.py

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any


# ── Constants ───────────────────────────────────────────────────────────────

ALERT_HISTORY_MAX = 500
SUPPRESSION_WINDOW_SECONDS = 300.0  # 5 minutes — same rule won't re-fire


# ── Enums ───────────────────────────────────────────────────────────────────

class AlertSeverity(Enum):
    """Alert severity levels (ordered: CRITICAL > WARNING > INFO)."""

    INFO = 1
    WARNING = 2
    CRITICAL = 3

    def __lt__(self, other: "AlertSeverity") -> bool:
        return self.value < other.value

    def __le__(self, other: "AlertSeverity") -> bool:
        return self.value <= other.value

    def __gt__(self, other: "AlertSeverity") -> bool:
        return self.value > other.value

    def __ge__(self, other: "AlertSeverity") -> bool:
        return self.value >= other.value


class ComparisonOperator(Enum):
    """Comparison operators for alert rules."""

    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    EQUAL = "=="
    NOT_EQUAL = "!="

    def evaluate(self, value: float, threshold: float) -> bool:
        """Evaluate the comparison against a threshold.

        Args:
            value: The actual metric value
            threshold: The threshold to compare against

        Returns:
            True if the comparison holds
        """
        if self == ComparisonOperator.GREATER_THAN:
            return value > threshold
        elif self == ComparisonOperator.LESS_THAN:
            return value < threshold
        elif self == ComparisonOperator.GREATER_EQUAL:
            return value >= threshold
        elif self == ComparisonOperator.LESS_EQUAL:
            return value <= threshold
        elif self == ComparisonOperator.EQUAL:
            return abs(value - threshold) < 1e-10
        elif self == ComparisonOperator.NOT_EQUAL:
            return abs(value - threshold) >= 1e-10
        return False


# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class AlertRule:
    """A declarative alert rule.

    Defines a condition: when the metric at ``metric_path`` satisfies
    ``operator`` compared to ``threshold``, an alert is generated.

    Attributes:
        name: Unique rule name (used for suppression and history)
        metric_path: Dot-separated path into stats dict
        operator: Comparison operator
        threshold: Threshold value to compare against
        severity: Alert severity (INFO, WARNING, CRITICAL)
        description: Human-readable description of what this rule checks
        enabled: Whether the rule is active
    """

    name: str
    metric_path: str
    operator: ComparisonOperator
    threshold: float
    severity: AlertSeverity = AlertSeverity.WARNING
    description: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON output or config storage."""
        return {
            "name": self.name,
            "metric_path": self.metric_path,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "severity": self.severity.name,
            "description": self.description,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertRule":
        """Deserialize from a dict (e.g., loaded from config).

        Args:
            data: Dict with rule fields

        Returns:
            A new AlertRule instance
        """
        return cls(
            name=data["name"],
            metric_path=data["metric_path"],
            operator=ComparisonOperator(data["operator"]),
            threshold=float(data["threshold"]),
            severity=AlertSeverity[data.get("severity", "WARNING")],
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
        )


@dataclass
class Alert:
    """A triggered alert.

    Attributes:
        rule_name: Name of the rule that triggered
        severity: Alert severity
        metric_path: Path to the metric that triggered
        metric_value: Actual value of the metric
        threshold: Threshold that was breached
        operator: Comparison operator (string representation)
        timestamp: When the alert was triggered (Unix timestamp)
        message: Human-readable alert message
    """

    rule_name: str
    severity: AlertSeverity
    metric_path: str
    metric_value: float
    threshold: float
    operator: str
    timestamp: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON output."""
        return {
            "rule_name": self.rule_name,
            "severity": self.severity.name,
            "metric_path": self.metric_path,
            "metric_value": round(self.metric_value, 6),
            "threshold": self.threshold,
            "operator": self.operator,
            "timestamp": self.timestamp,
            "message": self.message,
        }


# ── Default Rules ───────────────────────────────────────────────────────────

def default_rules() -> list[AlertRule]:
    """Return a set of default alert rules for the NoUsClaWW agent stack.

    These are initial values — thresholds may need tuning based on real usage.

    Returns:
        List of default AlertRule instances
    """
    return [
        AlertRule(
            name="high_error_count",
            metric_path="counters.errors",
            operator=ComparisonOperator.GREATER_EQUAL,
            threshold=10,
            severity=AlertSeverity.WARNING,
            description="Error count >= 10 — system instability",
        ),
        AlertRule(
            name="critical_error_count",
            metric_path="counters.errors",
            operator=ComparisonOperator.GREATER_EQUAL,
            threshold=50,
            severity=AlertSeverity.CRITICAL,
            description="Error count >= 50 — severe system instability",
        ),
        AlertRule(
            name="high_latency_p99",
            metric_path="histograms.latency_ms.p99",
            operator=ComparisonOperator.GREATER_THAN,
            threshold=5000.0,
            severity=AlertSeverity.WARNING,
            description="p99 latency > 5000ms — performance degraded",
        ),
        AlertRule(
            name="high_latency_p95",
            metric_path="histograms.latency_ms.p95",
            operator=ComparisonOperator.GREATER_THAN,
            threshold=2000.0,
            severity=AlertSeverity.INFO,
            description="p95 latency > 2000ms — performance warning",
        ),
        AlertRule(
            name="low_success_rate",
            metric_path="extra.success_rate",
            operator=ComparisonOperator.LESS_THAN,
            threshold=0.8,
            severity=AlertSeverity.WARNING,
            description="Success rate < 80% — quality degraded",
        ),
    ]


# ── Alert Engine ────────────────────────────────────────────────────────────

class AlertEngine:
    """Evaluates alert rules against monitoring metrics.

    Usage::

        engine = AlertEngine()
        engine.add_rule(my_rule)
        alerts = engine.evaluate(tracker.snapshot())
        active = engine.get_active_alerts()
        history = engine.get_history()

    Invariants:
        1. Read-only — never modifies the input metrics dict
        2. Bounded history — circular buffer, oldest evicted
        3. Suppression — same rule won't re-fire within suppression window
        4. Severity ordering — CRITICAL alerts returned first
    """

    def __init__(
        self,
        rules: list[AlertRule] | None = None,
        history_max: int = ALERT_HISTORY_MAX,
        suppression_window: float = SUPPRESSION_WINDOW_SECONDS,
    ) -> None:
        """Initialize the alert engine.

        Args:
            rules: Initial rules to register (defaults used if None)
            history_max: Maximum alerts to keep in history
            suppression_window: Seconds before a rule can re-fire
        """
        self._rules: dict[str, AlertRule] = {}
        self._history: deque[Alert] = deque(maxlen=history_max)
        self._suppression_window = suppression_window
        self._last_alerted: dict[str, float] = {}
        self._active_alerts: dict[str, Alert] = {}

        if rules is None:
            rules = default_rules()
        for rule in rules:
            self.add_rule(rule)

    # ── Rule Management ────────────────────────────────────────────────

    def add_rule(self, rule: AlertRule) -> None:
        """Register an alert rule. Replaces existing rule with same name.

        Args:
            rule: The AlertRule to register
        """
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name.

        Args:
            name: Rule name to remove

        Returns:
            True if the rule existed and was removed
        """
        if name in self._rules:
            del self._rules[name]
            self._active_alerts.pop(name, None)
            return True
        return False

    def get_rules(self) -> list[AlertRule]:
        """Return all registered rules."""
        return list(self._rules.values())

    def get_rule(self, name: str) -> AlertRule | None:
        """Return a specific rule by name.

        Args:
            name: Rule name

        Returns:
            The AlertRule, or None if not found
        """
        return self._rules.get(name)

    def enable_rule(self, name: str) -> bool:
        """Enable a rule.

        Args:
            name: Rule name

        Returns:
            True if the rule existed
        """
        if name in self._rules:
            self._rules[name].enabled = True
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule.

        Args:
            name: Rule name

        Returns:
            True if the rule existed
        """
        if name in self._rules:
            self._rules[name].enabled = False
            self._active_alerts.pop(name, None)
            return True
        return False

    def load_rules_from_config(self, rules_data: list[dict[str, Any]]) -> None:
        """Load rules from a list of config dicts.

        Args:
            rules_data: List of dicts, each parseable by AlertRule.from_dict()
        """
        for rd in rules_data:
            self.add_rule(AlertRule.from_dict(rd))

    # ── Evaluation ─────────────────────────────────────────────────────

    def evaluate(self, metrics: dict[str, Any]) -> list[Alert]:
        """Evaluate all enabled rules against the given metrics.

        Args:
            metrics: Monitoring metrics dict (e.g., from MetricsTracker.snapshot())

        Returns:
            List of newly triggered alerts (sorted by severity, CRITICAL first)
        """
        new_alerts: list[Alert] = []
        now = time.time()

        for rule in self._rules.values():
            if not rule.enabled:
                continue

            value = self._extract_metric(metrics, rule.metric_path)
            if value is None:
                # Metric not found — clear any active alert for this rule
                self._active_alerts.pop(rule.name, None)
                continue

            if rule.operator.evaluate(value, rule.threshold):
                # Check suppression
                last_fired = self._last_alerted.get(rule.name, 0)
                if now - last_fired < self._suppression_window:
                    # Update active alert value but don't create a new one
                    self._update_active_alert(rule, value, now)
                    continue

                # Create new alert
                alert = Alert(
                    rule_name=rule.name,
                    severity=rule.severity,
                    metric_path=rule.metric_path,
                    metric_value=value,
                    threshold=rule.threshold,
                    operator=rule.operator.value,
                    timestamp=now,
                    message=self._format_message(rule, value),
                )
                new_alerts.append(alert)
                self._history.append(alert)
                self._last_alerted[rule.name] = now
                self._active_alerts[rule.name] = alert
            else:
                # Condition cleared — remove from active alerts
                self._active_alerts.pop(rule.name, None)

        # Sort by severity (CRITICAL first)
        new_alerts.sort(key=lambda a: -a.severity.value)
        return new_alerts

    def _update_active_alert(self, rule: AlertRule, value: float, now: float) -> None:
        """Update an existing active alert with the latest value.

        Args:
            rule: The rule whose alert is being updated
            value: Latest metric value
            now: Current timestamp
        """
        if rule.name in self._active_alerts:
            alert = self._active_alerts[rule.name]
            alert.metric_value = value
            alert.timestamp = now

    @staticmethod
    def _extract_metric(metrics: dict[str, Any], path: str) -> float | None:
        """Extract a nested metric value using a dot-separated path.

        Args:
            metrics: The metrics dict to search
            path: Dot-separated path (e.g., "histograms.latency.p99")

        Returns:
            Float value if found and numeric, else None
        """
        parts = path.split(".")
        current: Any = metrics
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        if isinstance(current, (int, float)):
            return float(current)
        return None

    @staticmethod
    def _format_message(rule: AlertRule, value: float) -> str:
        """Format a human-readable alert message.

        Args:
            rule: The rule that triggered
            value: The metric value that breached the threshold

        Returns:
            Formatted message string
        """
        return (
            f"[{rule.severity.name}] {rule.name}: "
            f"{rule.metric_path} = {value:.4f} {rule.operator.value} {rule.threshold} "
            f"— {rule.description}"
        )

    # ── Query ──────────────────────────────────────────────────────────

    def get_active_alerts(self) -> list[Alert]:
        """Return currently active alerts (sorted by severity, CRITICAL first)."""
        alerts = list(self._active_alerts.values())
        alerts.sort(key=lambda a: -a.severity.value)
        return alerts

    def get_history(self, last_n: int = 0) -> list[Alert]:
        """Return alert history.

        Args:
            last_n: Number of most recent alerts (0 = all)

        Returns:
            List of Alert objects from history
        """
        if last_n == 0 or last_n >= len(self._history):
            return list(self._history)
        return list(self._history)[-last_n:]

    def get_history_by_severity(self, severity: AlertSeverity) -> list[Alert]:
        """Return alert history filtered by severity.

        Args:
            severity: Severity level to filter by

        Returns:
            List of matching Alert objects
        """
        return [a for a in self._history if a.severity == severity]

    def get_history_by_rule(self, rule_name: str) -> list[Alert]:
        """Return alert history filtered by rule name.

        Args:
            rule_name: Rule name to filter by

        Returns:
            List of matching Alert objects
        """
        return [a for a in self._history if a.rule_name == rule_name]

    # ── Summary ────────────────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of alert engine state."""
        active = self.get_active_alerts()
        return {
            "total_rules": len(self._rules),
            "enabled_rules": sum(1 for r in self._rules.values() if r.enabled),
            "active_alerts": len(active),
            "active_critical": sum(
                1 for a in active if a.severity == AlertSeverity.CRITICAL
            ),
            "active_warning": sum(
                1 for a in active if a.severity == AlertSeverity.WARNING
            ),
            "active_info": sum(
                1 for a in active if a.severity == AlertSeverity.INFO
            ),
            "history_count": len(self._history),
            "suppression_window_seconds": self._suppression_window,
        }

    # ── Reset ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset alert history and active alerts. Rules are preserved."""
        self._history.clear()
        self._last_alerted.clear()
        self._active_alerts.clear()

    # ── Representation ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"AlertEngine(rules={len(self._rules)}, "
            f"active={len(self._active_alerts)}, "
            f"history={len(self._history)})"
        )

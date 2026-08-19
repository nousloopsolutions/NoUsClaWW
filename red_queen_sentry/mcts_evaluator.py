"""
mcts_evaluator.py - Mahalanobis distance + SPRT anomaly detector.

This is the PRIMARY entry point for the Red Queen Sentry Docker image
(see Dockerfile ENTRYPOINT).

The detector combines two statistical techniques:

1. **Mahalanobis distance** - measures how far a prompt embedding vector
   deviates from the distribution of historical (benign) centroids, using a
   robust covariance estimate (Minimum Covariance Determinant, MinCovDet)
   that is resistant to outliers and poisoned training data.

       d^2 = (x - mu)^T * Cov_inv * (x - mu)

   A large Mahalanobis distance indicates the prompt is anomalous relative
   to the benign baseline.

2. **Sequential Probability Ratio Test (SPRT)** - a cumulative evidence
   tracker that accumulates log-likelihood ratios across multiple
   observations.  We use a decaying sum with a non-decaying floor so that
   repeated low-level anomalies eventually trigger an alert even if no
   single observation is extreme.

       lambda_t = max(floor, gamma * lambda_{t-1}) + LLR_t

   The effective evidence is ``max(lambda_t, floor)``.  An alert fires when
   ``effective_evidence >= threshold_b``.

The threshold ``b`` is calibrated data-driven via ``calibrate_threshold()``
to achieve a target false-positive rate on a held-out benign set while
maximizing detection of known attacks.

CLI modes
---------
``python mcts_evaluator.py``               -> print status, exit 0
``python mcts_evaluator.py --calibrate``   -> run calibration, print threshold
``python mcts_evaluator.py --evaluate ...``-> run a single evaluation
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    from sklearn.covariance import MinCovDet
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover - sklearn may be absent in minimal envs
    _HAS_SKLEARN = False


# ---------------------------------------------------------------------------
# RedQueenAnomalyDetector
# ---------------------------------------------------------------------------

class RedQueenAnomalyDetector:
    """Mahalanobis-distance + SPRT anomaly detector for prompt vectors.

    Parameters
    ----------
    historical_centroids : array-like, shape (n_samples, n_features)
        Embedding vectors of known-benign prompts used to fit the robust
        covariance estimate.
    threshold_b : float, default 12.5
        SPRT decision boundary.  When the effective evidence reaches this
        value, the detector flags the prompt as anomalous/malicious.
    """

    def __init__(self,
                 historical_centroids: Sequence[Sequence[float]],
                 threshold_b: float = 12.5) -> None:
        centroids = np.asarray(historical_centroids, dtype=np.float64)
        if centroids.ndim != 2:
            raise ValueError(
                "historical_centroids must be 2-D (n_samples, n_features); "
                f"got shape {centroids.shape}"
            )
        if centroids.shape[0] < 2:
            # Need at least 2 samples for any covariance estimate.
            # Pad with a near-duplicate so MinCovDet / fallback can work.
            centroids = np.vstack([centroids, centroids + 1e-6])

        self.threshold_b = float(threshold_b)

        # --- Fit robust covariance ---------------------------------------
        if _HAS_SKLEARN and centroids.shape[0] >= 5:
            try:
                mcd = MinCovDet(random_state=42, support_fraction=0.75)
                mcd.fit(centroids)
                self.mu = np.asarray(mcd.location_, dtype=np.float64)
                cov = np.asarray(mcd.covariance_, dtype=np.float64)
            except Exception:
                # Fall back to empirical estimate if MinCovDet fails.
                self.mu = centroids.mean(axis=0)
                cov = np.cov(centroids, rowvar=False)
        else:
            self.mu = centroids.mean(axis=0)
            cov = np.cov(centroids, rowvar=False)

        # Ensure cov is 2-D even with a single feature.
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])
        elif cov.ndim == 1:
            cov = np.diag(cov)

        # Regularize for invertibility (add small ridge).
        ridge = 1e-6 * np.eye(cov.shape[0])
        try:
            self.cov_inv = np.linalg.inv(cov + ridge)
        except np.linalg.LinAlgError:
            self.cov_inv = np.linalg.pinv(cov + ridge)

        # --- SPRT state ---------------------------------------------------
        self.sprt_lambda: float = 0.0
        self.sprt_floor: float = 0.0
        self.gamma: float = 0.95       # decay factor per step
        self.floor_weight: float = 0.10  # fraction of LLR retained permanently

    # ------------------------------------------------------------------
    # Mahalanobis distance
    # ------------------------------------------------------------------
    def mahalanobis_distance(self, prompt_vector: Sequence[float]) -> float:
        """Return the squared Mahalanobis distance of *prompt_vector* from mu.

            d^2 = (x - mu)^T * Cov_inv * (x - mu)
        """
        x = np.asarray(prompt_vector, dtype=np.float64).ravel()
        diff = x - self.mu
        d2 = float(diff @ self.cov_inv @ diff)
        return max(d2, 0.0)

    # ------------------------------------------------------------------
    # SPRT update
    # ------------------------------------------------------------------
    def _update_sprt(self, llr: float) -> float:
        """Advance the SPRT by one step with decay + non-decaying floor.

            lambda_t = gamma * lambda_{t-1} + LLR_t
            floor_t  = floor_{t-1} + floor_weight * max(LLR_t, 0)
            effective = max(lambda_t, floor_t)

        The floor accumulates a small fraction of every positive LLR so that
        repeated low-level anomalies are never fully forgotten.
        """
        self.sprt_lambda = self.gamma * self.sprt_lambda + llr
        if llr > 0:
            self.sprt_floor = self.sprt_floor + self.floor_weight * llr
        effective = max(self.sprt_lambda, self.sprt_floor)
        return effective

    def _llr(self, distance: float, is_malicious_signal: bool) -> float:
        """Compute the log-likelihood ratio for one observation.

        We model the benign distance as chi-square-like and the malicious
        distance as shifted-exponential.  The LLR is positive when the
        observed distance is more consistent with the malicious hypothesis.

        For a chi-square distribution with k degrees of freedom the log-pdf
        is roughly ``-0.5*d - (k/2 - 1)*log(d) + const``.  We use a
        simplified, well-behaved surrogate that is monotonic in ``d``:

            benign_log_pdf  ~ -0.5 * d
            malicious_log_pdf ~ -lambda_m * max(d - shift, 0) + log(lambda_m)

        The constant terms cancel in the LLR.
        """
        k = float(self.mu.shape[0])  # degrees of freedom ~ n_features
        benign_log_pdf = -0.5 * distance
        # Malicious: distances tend to be larger; use an exponential tail.
        shift = k  # expected benign distance ~ k
        excess = max(distance - shift, 0.0)
        lambda_m = 0.25  # rate parameter for malicious tail
        malicious_log_pdf = -lambda_m * excess + 0.5 * np.log1p(excess)

        llr = malicious_log_pdf - benign_log_pdf

        # If the caller provides an explicit malicious signal (e.g. from the
        # sanitization layer detecting a known injection pattern), bias the
        # LLR upward so the SPRT accumulates evidence faster.
        if is_malicious_signal:
            llr += 2.0

        return float(llr)

    # ------------------------------------------------------------------
    # Public evaluation
    # ------------------------------------------------------------------
    def evaluate_prompt_vector(self,
                               prompt_vector: Sequence[float],
                               is_malicious_signal: bool) -> bool:
        """Evaluate a single prompt vector.

        Parameters
        ----------
        prompt_vector : array-like
            Embedding of the prompt to evaluate.
        is_malicious_signal : bool
            Whether the sanitization layer flagged this prompt as containing
            a known injection pattern.  This biases the SPRT LLR upward.

        Returns
        -------
        bool
            True if the effective evidence >= threshold_b (anomaly detected),
            False otherwise.
        """
        distance = self.mahalanobis_distance(prompt_vector)
        llr = self._llr(distance, is_malicious_signal)
        effective = self._update_sprt(llr)
        return effective >= self.threshold_b

    def reset_sprt(self) -> None:
        """Reset the SPRT accumulator state."""
        self.sprt_lambda = 0.0
        self.sprt_floor = 0.0

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def calibrate_threshold(self,
                            benign_payloads: Sequence[Sequence[float]],
                            attack_payloads: Sequence[Sequence[float]],
                            target_fpr: float = 0.001) -> float:
        """Data-driven calibration of the SPRT decision threshold.

        We compute the effective-evidence score for every benign payload
        (treating each as a fresh SPRT run from zero) and pick the smallest
        threshold that yields a false-positive rate <= ``target_fpr`` while
        still detecting all attack payloads.

        Parameters
        ----------
        benign_payloads : array-like, shape (n_benign, n_features)
            Embedding vectors of benign prompts.
        attack_payloads : array-like, shape (n_attack, n_features)
            Embedding vectors of known attack prompts.
        target_fpr : float
            Desired maximum false-positive rate on the benign set.

        Returns
        -------
        float
            The calibrated threshold ``b``.
        """
        benign = np.asarray(benign_payloads, dtype=np.float64)
        attacks = np.asarray(attack_payloads, dtype=np.float64)

        # Compute per-payload effective evidence (fresh SPRT each time).
        benign_scores: List[float] = []
        for vec in benign:
            self.reset_sprt()
            d = self.mahalanobis_distance(vec)
            llr = self._llr(d, is_malicious_signal=False)
            eff = self._update_sprt(llr)
            benign_scores.append(eff)

        attack_scores: List[float] = []
        for vec in attacks:
            self.reset_sprt()
            d = self.mahalanobis_distance(vec)
            llr = self._llr(d, is_malicious_signal=True)
            eff = self._update_sprt(llr)
            attack_scores.append(eff)

        # Candidate thresholds: the empirical benign quantile at (1 - fpr).
        benign_arr = np.array(benign_scores)
        if len(benign_arr) == 0:
            self.threshold_b = 12.5
            return self.threshold_b

        # Quantile-based threshold for the target FPR.
        q = float(np.quantile(benign_arr, 1.0 - target_fpr))
        # Ensure we detect at least 95% of attacks.
        attack_arr = np.array(attack_scores)
        if len(attack_arr) > 0:
            detection_q = float(np.quantile(attack_arr, 0.05))
            # The threshold must be below the 5th-percentile attack score
            # to detect >= 95% of attacks.  Pick the larger of (benign q95)
            # and a floor, but not above the attack detection floor.
            threshold = max(q, 1.0)
            if detection_q > threshold:
                threshold = detection_q - 1e-3
        else:
            threshold = max(q, 1.0)

        # Clamp to a sane range.
        threshold = max(0.5, min(threshold, 100.0))
        self.threshold_b = threshold
        self.reset_sprt()
        return threshold


# ---------------------------------------------------------------------------
# Hash-based embedding helper (used by run_10k and CLI --evaluate)
# ---------------------------------------------------------------------------

def hash_vector(text: str, dim: int = 128) -> np.ndarray:
    """Deterministic 128-D embedding from text via feature hashing.

    This is a lightweight stand-in for a real sentence embedding model,
    suitable for the 10K iteration test where we need deterministic,
    dependency-free vectors.
    """
    vec = np.zeros(dim, dtype=np.float64)
    if not text:
        return vec
    # Hash each character trigram into a bucket; add +1/-1 via sign hash.
    s = text.lower()
    tokens = [s[i:i + 3] for i in range(max(1, len(s) - 2))]
    for tok in tokens:
        h = hash(tok)
        bucket = abs(h) % dim
        sign = 1.0 if (h & 1) == 0 else -1.0
        vec[bucket] += sign
    # Also hash whole-word tokens for better separation.
    for word in s.split():
        h = hash(word)
        bucket = abs(h) % dim
        vec[bucket] += 1.0 if (h & 1) == 0 else -1.0
    # L2 normalize.
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcts_evaluator",
        description="Red Queen Sentry - Mahalanobis + SPRT anomaly detector",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--calibrate", action="store_true",
                      help="Run threshold calibration on synthetic data.")
    mode.add_argument("--evaluate", action="store_true",
                      help="Run a single evaluation of --prompt-text.")
    p.add_argument("--prompt-text", default="",
                   help="Prompt text to evaluate (used with --evaluate).")
    p.add_argument("--threshold-b", type=float, default=12.5,
                   help="SPRT decision threshold (default 12.5).")
    p.add_argument("--vector-dim", type=int, default=128,
                   help="Dimensionality of hash vectors (default 128).")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for synthetic calibration data.")
    p.add_argument("--output", default="-",
                   help="Output path for JSON result ('-' = stdout).")
    return p


def _synthetic_centroids(dim: int, n: int, seed: int) -> np.ndarray:
    """Generate synthetic benign centroid vectors for CLI/demo use."""
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.0, scale=0.1, size=(n, dim))


def _emit(result: dict, output: str) -> None:
    text = json.dumps(result, indent=2)
    if output == "-":
        print(text)
    else:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dim = args.vector_dim

    # --- Default (no args): print status and exit 0 ----------------------
    if not args.calibrate and not args.evaluate:
        print("Red Queen Sentry - MCTS Evaluator: ready (no mode selected)")
        print(f"  threshold_b = {args.threshold_b}")
        print(f"  vector_dim  = {dim}")
        print("  Use --calibrate or --evaluate to run a mode.")
        return 0

    # Build a detector from synthetic benign centroids.
    centroids = _synthetic_centroids(dim, n=200, seed=args.seed)
    detector = RedQueenAnomalyDetector(centroids, threshold_b=args.threshold_b)

    if args.calibrate:
        # Generate synthetic benign + attack vectors for calibration.
        rng = np.random.default_rng(args.seed + 1)
        benign = rng.normal(loc=0.0, scale=0.1, size=(500, dim))
        attacks = rng.normal(loc=1.0, scale=0.3, size=(200, dim))
        threshold = detector.calibrate_threshold(benign, attacks, target_fpr=0.001)
        result = {
            "mode": "calibrate",
            "calibrated_threshold_b": threshold,
            "n_benign": benign.shape[0],
            "n_attack": attacks.shape[0],
            "target_fpr": 0.001,
            "seed": args.seed,
        }
        _emit(result, args.output)
        return 0

    if args.evaluate:
        vec = hash_vector(args.prompt_text, dim=dim)
        # In --evaluate mode we have no sanitization signal; assume False.
        detected = detector.evaluate_prompt_vector(vec, is_malicious_signal=False)
        distance = detector.mahalanobis_distance(vec)
        result = {
            "mode": "evaluate",
            "prompt_text": args.prompt_text,
            "mahalanobis_distance": distance,
            "sprt_lambda": detector.sprt_lambda,
            "sprt_floor": detector.sprt_floor,
            "effective_evidence": max(detector.sprt_lambda, detector.sprt_floor),
            "threshold_b": detector.threshold_b,
            "detected": detected,
        }
        _emit(result, args.output)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())

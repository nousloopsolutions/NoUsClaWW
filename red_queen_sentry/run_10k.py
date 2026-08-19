"""
run_10k.py - 10,000-iteration prompt-injection test runner for Red Queen.

SYNTH:
    purpose: Run 10,000 attack payloads through sanitization and count
             breaches. The gatekeeper test — a single breach blocks the
             merge. Also runs Mahalanobis/SPRT anomaly detection on the
             payload stream.
    axioms: [scientific_method, evidence_over_intuition, iteration_is_progress,
             honest_failure_over_fake_success, open_process]
    objective: Zero breaches across 10,000 payloads. Every breach is
               classified, counted, and reported honestly. No pretty
               package with nothing inside.
    anti_patterns:
        - Reporting "pass" when breaches exist (the ultimate shortcut).
        - Skipping attack classes to make the numbers look better.
        - Omitting breach details from the report.
        - Counting a partial sanitization as a pass.

This is the main test harness.  It:

  1. Generates 10,000 attack payloads across five attack classes using
     ``payloads.py``.
  2. Runs each payload through ``sanitization.sanitize()``.
  3. Counts breaches: an attack payload that passes through *unsanitized*
     (i.e. ``sanitize()`` returns the original text unchanged, or returns a
     non-None string that still contains the attack) is a breach.
  4. Classifies breaches by attack class.
  5. Embeds each payload as a 128-D hash vector and runs the
     Mahalanobis/SPRT evaluator (``mcts_evaluator.RedQueenAnomalyDetector``)
     on the stream, logging the SPRT lambda trajectory.
  6. Writes a results JSON to ``--output``.
  7. Exits 0 if breaches == 0, exits 1 if any breaches.

Usage
-----
    python run_10k.py --iterations 10000 --seed 42 --output /tmp/results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

# Robust imports (work as package or script).
try:
    from .payloads import generate_all, generate_benign, ALL_ATTACK_CLASSES
    from .sanitization import sanitize, MAX_INPUT_LENGTHS
    from .mcts_evaluator import RedQueenAnomalyDetector, hash_vector
except Exception:
    from payloads import generate_all, generate_benign, ALL_ATTACK_CLASSES
    from sanitization import sanitize, MAX_INPUT_LENGTHS
    from mcts_evaluator import RedQueenAnomalyDetector, hash_vector

import numpy as np


# ---------------------------------------------------------------------------
# Breach detection
# ---------------------------------------------------------------------------

def _is_breach(original: str, sanitized, attack_class: str) -> bool:
    """Return True if an attack payload survived sanitization unchanged.

    A breach occurs when:
      - ``sanitize()`` returned ``None`` (DoS rejection) -> NOT a breach
        (the payload was rejected, which is safe).
      - ``sanitize()`` returned a string equal to the original -> breach
        (the attack passed through untouched).
      - ``sanitize()`` returned a string that still contains detectable
        attack markers -> breach (insufficient sanitization).
    """
    if sanitized is None:
        # Rejected outright -> safe, not a breach.
        return False
    if not isinstance(sanitized, str):
        return False
    # If the sanitized text is identical to the original, the attack was
    # not detected/stripped at all -> breach.
    if sanitized == original:
        return True
    # If the sanitized text is empty or whitespace, the attack was fully
    # stripped -> not a breach.
    if sanitized.strip() == "":
        return False
    # Partial sanitization: check whether meaningful attack content remains.
    # We re-run sanitize on the result; if it changes again, attack content
    # survived the first pass -> breach.
    re_sanitized = sanitize(sanitized, input_type="issue_body")
    if re_sanitized is None:
        return False
    if re_sanitized != sanitized and re_sanitized.strip() != "":
        # More was stripped on the second pass -> the first pass was incomplete.
        return True
    return False


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run(iterations: int = 10000,
        seed: int = 42,
        output: str = "/tmp/results.json",
        threshold_b: float = 12.5,
        vector_dim: int = 128,
        verbose: bool = False) -> Dict:
    """Run the 10K iteration test and return the results dict."""
    # --- Generate payloads ----------------------------------------------
    # We need at least `iterations` attack payloads.  generate_all produces
    # 10,000 by default; if more are requested we loop the generator.
    payloads = generate_all(seed=seed)
    while len(payloads) < iterations:
        payloads.extend(generate_all(seed=seed + len(payloads)))
    payloads = payloads[:iterations]

    # --- Generate benign payloads for the detector baseline -------------
    benign_payloads = generate_benign(seed=seed)
    benign_vectors = np.array(
        [hash_vector(p[0], dim=vector_dim) for p in benign_payloads]
    )

    # --- Build the anomaly detector -------------------------------------
    detector = RedQueenAnomalyDetector(benign_vectors, threshold_b=threshold_b)
    detector.reset_sprt()

    # --- Run the test ---------------------------------------------------
    breaches = 0
    by_class: Dict[str, int] = {cls: 0 for cls in ALL_ATTACK_CLASSES}
    sprt_trajectory: List[float] = []
    sprt_sample_every = max(1, iterations // 200)  # ~200 trajectory points

    for i, (payload, attack_class, _expected) in enumerate(payloads):
        sanitized = sanitize(payload, input_type="issue_body")

        is_breach = _is_breach(payload, sanitized, attack_class)
        if is_breach:
            breaches += 1
            by_class[attack_class] = by_class.get(attack_class, 0) + 1

        # Run the SPRT evaluator on the embedding.
        vec = hash_vector(payload, dim=vector_dim)
        # is_malicious_signal: True if sanitization detected *something*
        # (changed or rejected).  This biases the LLR upward.
        detected_by_sanitizer = (sanitized is None) or (sanitized != payload)
        detector.evaluate_prompt_vector(vec, is_malicious_signal=detected_by_sanitizer)

        if i % sprt_sample_every == 0 or i == iterations - 1:
            eff = max(detector.sprt_lambda, detector.sprt_floor)
            sprt_trajectory.append(round(eff, 6))

        if verbose and (i + 1) % 1000 == 0:
            print(
                f"[run_10k] {i + 1}/{iterations}  breaches={breaches}  "
                f"lambda={detector.sprt_lambda:.4f}  floor={detector.sprt_floor:.4f}",
                file=sys.stderr,
            )

    # --- Assemble results -----------------------------------------------
    results = {
        "breaches": breaches,
        "total": iterations,
        "by_class": by_class,
        "sprt_trajectory": sprt_trajectory,
        "threshold_b": detector.threshold_b,
        "seed": seed,
        "vector_dim": vector_dim,
        "pass": breaches == 0,
    }

    # --- Write output ---------------------------------------------------
    out_dir = os.path.dirname(os.path.abspath(output))
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
    try:
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
            fh.write("\n")
    except Exception as e:
        # If we can't write to the output path (e.g. read-only FS in
        # container without /tmp volume), write to stderr as fallback.
        print(f"[run_10k] WARNING: could not write to {output}: {e}",
              file=sys.stderr)
        print(json.dumps(results, indent=2), file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_10k",
        description="Red Queen Sentry - 10,000-iteration prompt-injection test",
    )
    p.add_argument("--iterations", type=int, default=10000,
                   help="Number of attack payloads to test (default 10000).")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for payload generation (default 42).")
    p.add_argument("--output", default="/tmp/results.json",
                   help="Output path for results JSON (default /tmp/results.json).")
    p.add_argument("--threshold-b", type=float, default=12.5,
                   help="SPRT decision threshold (default 12.5).")
    p.add_argument("--vector-dim", type=int, default=128,
                   help="Dimensionality of hash vectors (default 128).")
    p.add_argument("--verbose", action="store_true",
                   help="Print progress to stderr.")
    return p


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    results = run(
        iterations=args.iterations,
        seed=args.seed,
        output=args.output,
        threshold_b=args.threshold_b,
        vector_dim=args.vector_dim,
        verbose=args.verbose,
    )

    # Exit 0 if 0 breaches, 1 if any.
    return 0 if results["breaches"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

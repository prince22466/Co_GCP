import argparse
import csv
import random
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phases", default="scenarios/daily_phases.csv")
    p.add_argument("--output", default="scenarios/daily_24m.csv")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    rng = random.Random(args.seed)
    rows = []
    elapsed = 0
    with open(args.phases, newline="") as f:
        for phase in csv.DictReader(f):
            duration = int(phase["duration_seconds"])
            start = float(phase["start_rps"])
            end = float(phase["end_rps"])
            noise = float(phase["noise_fraction"])
            for i in range(duration):
                frac = 0.0 if duration <= 1 else i / (duration - 1)
                base = start + (end - start) * frac
                value = base * (1.0 + rng.uniform(-noise, noise))
                rows.append((elapsed, phase["phase"], max(1, int(round(value)))))
                elapsed += 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["second", "phase", "rps"])
        w.writerows(rows)

    print(f"wrote {len(rows)} seconds ({len(rows)/60:.1f} min) to {out}")


if __name__ == "__main__":
    main()

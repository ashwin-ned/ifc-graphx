"""Build the folder an annotator points the tool at.

The browser opens one directory and works out of it: the plans drive the
annotation, the IFC files back the 3D check, and the annotation file is written
back beside them. This assembles such a folder from a corpus and the plans
exported from it.

The IFC files dominate the size -- about 10 MB each against 45 KB for a plan --
so `--no-ifc` produces a plans-only folder for annotators who do not need the
3D view, and `--models` cuts the set down to the buildings you actually want
annotated.

    # everything, with the IFC files, ready to hand over
    python annotator/prepare_dataset.py --ifc dataset/test_set \\
        --plans annotator/data --out ~/bimsg-annotation-set

    # just five buildings, no IFC files
    python annotator/prepare_dataset.py --ifc dataset/test_set \\
        --plans annotator/data --out ~/small --no-ifc \\
        --models model_9 model_20 model_12 model_1 model_6
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plans", default="annotator/data",
                    help="directory of *.plan.json")
    ap.add_argument("--ifc", help="directory of the matching *.ifc files")
    ap.add_argument("--out", required=True, help="folder to create")
    ap.add_argument("--models", nargs="*", help="only these models")
    ap.add_argument("--no-ifc", action="store_true",
                    help="plans only; the 3D view will be unavailable")
    args = ap.parse_args()

    plans = sorted(glob.glob(os.path.join(args.plans, "*.plan.json")))
    if not plans:
        print(f"no plans in {args.plans}")
        print("  run: python main/export_plans.py 'dataset/test_set/*.ifc' "
              "--out annotator/data")
        return 1

    wanted = set(args.models) if args.models else None
    os.makedirs(args.out, exist_ok=True)

    copied, with_ifc, missing = 0, 0, []
    for p in plans:
        name = os.path.basename(p)[:-len(".plan.json")]
        if wanted and name not in wanted:
            continue
        shutil.copy2(p, os.path.join(args.out, os.path.basename(p)))
        copied += 1
        if args.no_ifc or not args.ifc:
            continue
        src = os.path.join(args.ifc, f"{name}.ifc")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(args.out, f"{name}.ifc"))
            with_ifc += 1
        else:
            missing.append(name)

    if wanted:
        found = {os.path.basename(p)[:-len(".plan.json")] for p in plans}
        for m in sorted(wanted - found):
            print(f"  ! no plan for {m}")

    guide = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "ANNOTATION_GUIDE.md")
    if os.path.exists(guide):
        shutil.copy2(guide, os.path.join(args.out, "ANNOTATION_GUIDE.md"))

    size = sum(os.path.getsize(os.path.join(args.out, f))
               for f in os.listdir(args.out))
    print(f"annotation folder -> {args.out}")
    print(f"  {copied} plans, {with_ifc} IFC files, {size / 1e6:.1f} MB")
    if missing:
        print(f"  {len(missing)} plan(s) have no IFC beside them "
              f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''});"
              f" the 3D view will be unavailable for those")
    if not copied:
        print("  nothing matched --models")
        return 1
    print("\n  Hand this folder over. In the tool: Open dataset folder… and "
          "pick it.\n  Work is written back to bimsg-annotations.json inside it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

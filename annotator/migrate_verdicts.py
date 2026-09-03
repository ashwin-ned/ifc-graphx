"""One-off: rewrite annotations that use the pre-2026-09-03 verdict words.

Verdicts used to be "correct"/"spurious" for both rooms and links, which made a
saved file ambiguous about what was meant -- "spurious" said either "there is no
room here" or "you cannot walk between these", recoverable only from which
dictionary the entry sat in. They are specific now, and the reader deliberately
carries no aliases: an unrecognised word is ignored rather than guessed at.

That is the right shape for the code and the wrong outcome for files already on
disk, which is what this is for. Run it once over anything annotated before the
change; delete the script when nothing old is left.

    python annotator/migrate_verdicts.py annotations/*.json        # in place
    python annotator/migrate_verdicts.py --dry-run annotations/*.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

# The mapping is unambiguous because a room and a link were already stored in
# separate dictionaries; only the word was shared.
ROOM = {"correct": "real", "spurious": "not_a_room"}
LINK = {"correct": "passable", "spurious": "not_passable"}


def migrate_annotation(a: dict) -> int:
    """Rewrite one annotation in place. Returns how many verdicts changed."""
    n = 0
    for v in (a.get("rooms") or {}).values():
        if v.get("verdict") in ROOM:
            v["verdict"] = ROOM[v["verdict"]]
            n += 1
    for key in ("edges", "vertical"):
        for v in (a.get(key) or {}).values():
            if v.get("verdict") in LINK:
                v["verdict"] = LINK[v["verdict"]]
                n += 1
    # Links added before the annotator was asked door-or-passage recorded no
    # kind. compose defaults those to a door, so make that explicit rather than
    # leaving it to a default that could later change.
    for e in a.get("added_edges") or []:
        if not e.get("kind"):
            e["kind"] = "connected_by_door"
            n += 1
    return n


def migrate_doc(doc):
    """A bundle or a single annotation. Returns (doc, changes)."""
    if isinstance(doc, dict) and doc.get("format") == "bimsg-annotation-bundle":
        return doc, sum(migrate_annotation(a) for a in doc.get("annotations") or [])
    if isinstance(doc, dict) and "rooms" in doc:
        return doc, migrate_annotation(doc)
    return doc, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="annotation or bundle JSON files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not leave a .bak beside each rewritten file")
    args = ap.parse_args()

    paths = []
    for f in args.files:
        paths.extend(sorted(glob.glob(f)) if any(c in f for c in "*?[") else [f])

    total = 0
    for p in paths:
        try:
            with open(p) as fh:
                doc = json.load(fh)
        except Exception as e:
            print(f"  ! {os.path.basename(p)}: not readable JSON ({e})")
            continue
        # A composed building graph is not an annotation.
        if isinstance(doc, dict) and doc.get("source") == "annotated" and "nodes" in doc:
            continue
        doc, n = migrate_doc(doc)
        if not n:
            print(f"  {os.path.basename(p)}: already current")
            continue
        total += n
        if args.dry_run:
            print(f"  {os.path.basename(p)}: would update {n} verdict(s)")
            continue
        if not args.no_backup:
            shutil.copy2(p, p + ".bak")
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, p)
        print(f"  {os.path.basename(p)}: updated {n} verdict(s)"
              f"{'' if args.no_backup else ' (.bak kept)'}")

    print(f"\n{total} verdict(s) {'would be ' if args.dry_run else ''}rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())

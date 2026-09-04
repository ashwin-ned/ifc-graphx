"""Build the annotators' checklist: one row per IFC, with what to expect from it.

A corpus is a pile of files with no indication of which are worth someone's
afternoon and which will waste it. This writes a spreadsheet that says, per
model, how big the job is and what is wrong with it before anyone opens it --
so a model with no storeys is skipped deliberately rather than puzzled over,
and two people do not annotate the same building by accident.

Notes are derived from the file and the exported plan, never guessed. Every
flag here is something that changes what an annotator should do.

    python annotator/make_checklist.py --ifc dataset/<corpus> \
        --plans dataset/<corpus>/plans --out dataset/<corpus>/annotation_checklist.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys

# Rough thresholds, chosen from this corpus rather than in the abstract: the
# point is to warn about the tail, not to grade every building.
BIG_FILE_MB = 25.0        # the 3D view gets slow to load past roughly here
MANY_ROOMS = 60           # more than an hour of judging
FEW_ROOMS = 3             # almost certainly a bad extraction
HUGE_ROOM_M2 = 250.0      # above the corpus p95 of 72 m²; whole-floor spaces
TINY_ROOM_M2 = 3.0        # below the corpus minimum of 3.8 m²
THIN_STOREY_M = 2.0       # no occupiable floor is this close to the next


def scan_ifc(path: str) -> dict:
    """Entity counts straight off the STEP text.

    Deliberately not IfcOpenShell: opening 200 models to count lines takes
    minutes, and every question asked here is answerable from the text.
    """
    counts = {"storey": 0, "space": 0, "door": 0, "wall": 0}
    pat = re.compile(rb"^#\d+\s*=\s*(IFC[A-Z0-9]+)", re.I)
    want = {b"IFCBUILDINGSTOREY": "storey", b"IFCSPACE": "space",
            b"IFCDOOR": "door", b"IFCWALL": "wall",
            b"IFCWALLSTANDARDCASE": "wall"}
    with open(path, "rb") as fh:
        for line in fh:
            m = pat.match(line)
            if not m:
                continue
            key = want.get(m.group(1).upper())
            if key:
                counts[key] += 1
    return counts


def read_plan(path: str):
    """What the exported plan says about this model, or None if unreadable."""
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception:
        return None
    st = d.get("storeys") or []
    areas = [r["area"] for s in st for r in s["rooms"] if r.get("area")]
    elevs = sorted(s.get("elevation") or 0.0 for s in st)
    gaps = [b - a for a, b in zip(elevs, elevs[1:]) if b - a > 0.01]
    return {
        "storeys": len(st),
        "rooms": sum(len(s["rooms"]) for s in st),
        "links": sum(len(s["edges"]) for s in st),
        "floor_links": len(d.get("vertical") or []),
        "doors": sum(len(s["doors"]) for s in st),
        "median_area": statistics.median(areas) if areas else 0.0,
        "min_gap": min(gaps) if gaps else None,
    }


def already_done(paths, plans_dir: str) -> dict:
    """model -> {annotator: complete?}, from annotation files or bundles.

    The point of a shared checklist is that two people do not annotate the
    same building, so anything already returned starts ticked rather than
    waiting for someone to notice. Whether it is *finished* is asked of
    compose, not assumed from the file existing: a half-judged building ticked
    as done is one nobody ever goes back to.
    """
    done = {}
    for pat in paths or []:
        for p in (sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]):
            try:
                with open(p) as fh:
                    doc = json.load(fh)
            except Exception:
                print(f"  ! could not read {p}, ignoring it")
                continue
            annos = (doc.get("annotations") if isinstance(doc, dict) else None) or \
                    ([doc] if isinstance(doc, dict) and "rooms" in doc else [])
            for a in annos:
                m, who = a.get("model"), a.get("annotator")
                if not (m and who):
                    continue
                # {annotator: (complete?, "what is left")}
                done.setdefault(m, {})[who] = _is_complete(m, a, plans_dir)
    return done


def _is_complete(model: str, anno: dict, plans_dir: str):
    """(complete?, what is left) against the current plan.

    "Against the current plan" is the important part: an annotation finished
    some time ago can read as unfinished because the plan has since been
    re-exported and gained rooms. Saying exactly what is outstanding tells
    someone whether that is five minutes of work or an afternoon, which
    a bare "not finished" does not.
    """
    path = os.path.join(plans_dir, f"{model}.plan.json")
    if not os.path.exists(path):
        return (None, "")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import compose
        with open(path) as fh:
            g = compose.compose(json.load(fh), anno)
    except Exception as e:
        return (None, f"could not be checked against the plan ({type(e).__name__})")
    c = g.get("counts") or {}
    if g.get("complete"):
        return (True, "")
    left = []
    for label, a, b in (("room", "rooms_judged", "rooms_total"),
                        ("link", "links_judged", "links_total"),
                        ("floor link", "vertical_judged", "vertical_total")):
        n = (c.get(b) or 0) - (c.get(a) or 0)
        if n > 0:
            left.append(f"{n} {label}{'s' if n != 1 else ''}")
    return (False, " and ".join(left) + " still to judge" if left else "")


def notes_for(ifc: dict, plan, mb: float) -> tuple[str, str]:
    """(status, notes). Status is what to do with the model, notes say why."""
    if plan is None:
        return ("skip", "no plan was exported for this model — "
                        "re-run main/export_plans.py before annotating")

    storeys, rooms = plan["storeys"], plan["rooms"]

    # --- reasons not to open it at all ---
    if ifc["storey"] == 0:
        return ("skip", f"the IFC declares no IfcBuildingStorey, so the "
                        f"{ifc['space']} spaces in it cannot be placed on a "
                        f"floor — nothing to annotate")
    if storeys == 0:
        return ("skip", "the plan came out with no storeys — report this one")
    if rooms == 0:
        return ("skip", f"no rooms were extracted ({ifc['space']} IfcSpace in "
                        f"the file) — report this one")

    # --- reasons to be careful with it ---
    notes = []
    if rooms <= FEW_ROOMS:
        notes.append(f"only {rooms} room(s) for {storeys} storey(s) — check "
                     f"against the 3D view before trusting it")
    # Not a units check: millimetres is the normal IFC export and the scale is
    # inferred from the geometry anyway. What is worth saying is when the
    # result came out an implausible size.
    if plan["median_area"] > HUGE_ROOM_M2:
        notes.append(f"rooms are very large (median {plan['median_area']:.0f} "
                     f"m²) — the file may model a whole floor as one space "
                     f"rather than as rooms")
    elif 0 < plan["median_area"] < TINY_ROOM_M2:
        notes.append(f"rooms are very small (median {plan['median_area']:.1f} "
                     f"m²) — check the scale looks right in the 3D view")
    gap = plan["min_gap"]
    if gap is not None and gap < THIN_STOREY_M:
        notes.append(f"two storeys are only {gap:.1f} m apart — one is "
                     f"probably a mezzanine or a structural level rather than "
                     f"an occupiable floor")
    if plan["doors"] == 0:
        notes.append("no doors found, so every link was inferred from geometry "
                     "alone — expect more wrong links than usual")
    if storeys > 1 and plan["floor_links"] == 0:
        notes.append(f"no floor-to-floor links were proposed across "
                     f"{storeys} storeys — you will need to chain the floors "
                     f"by hand with V")
    if plan["links"] == 0:
        notes.append("no links were proposed at all — the whole connectivity "
                     "is yours to add")
    if rooms >= MANY_ROOMS:
        notes.append(f"large: {rooms} rooms over {storeys} storeys, budget time")
    if mb >= BIG_FILE_MB:
        notes.append(f"{mb:.0f} MB file — the 3D view will take a while to load")

    return ("check" if notes else "ok", "; ".join(notes))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ifc", required=True, help="directory of *.ifc files")
    ap.add_argument("--plans", help="directory of *.plan.json "
                                    "(default: <ifc>/plans)")
    ap.add_argument("--out", help="CSV to write "
                                  "(default: <ifc>/annotation_checklist.csv)")
    ap.add_argument("--annotations", nargs="*",
                    help="annotation files or bundles already returned; those "
                         "models start ticked, with the annotator's name filled in")
    args = ap.parse_args()

    plans_dir = args.plans or os.path.join(args.ifc, "plans")
    out = args.out or os.path.join(args.ifc, "annotation_checklist.csv")

    ifcs = sorted(glob.glob(os.path.join(args.ifc, "*.ifc")),
                  key=lambda p: _natural(os.path.basename(p)))
    if not ifcs:
        print(f"no *.ifc files in {args.ifc}")
        return 1

    done_by = already_done(args.annotations, plans_dir)
    if done_by:
        print(f"{len(done_by)} model(s) already annotated, starting ticked")

    rows = []
    tally = {"ok": 0, "check": 0, "skip": 0}
    for i, p in enumerate(ifcs, 1):
        name = os.path.basename(p)
        model = name[:-len(".ifc")]
        mb = os.path.getsize(p) / 1e6
        ifc = scan_ifc(p)
        plan = read_plan(os.path.join(plans_dir, f"{model}.plan.json"))
        who = done_by.get(model, {})
        finished = sorted(w for w, (c, _) in who.items() if c)
        partial = sorted(w for w, (c, _) in who.items() if not c)
        left = "; ".join(sorted({t for c, t in who.values() if not c and t}))
        status, notes = notes_for(ifc, plan, mb)
        if partial and not finished:
            head = f"{', '.join(partial)} has this one"
            head += f": {left}" if left else " but it is not finished"
            head += " — pick it up rather than starting again"
            notes = head + ("; " + notes if notes else "")
        tally[status] += 1
        pl = plan or {}
        rows.append({
            # An unchecked box that reads as one in a plain text editor, in
            # Excel, in Google Sheets and in GitHub's CSV view. Change it to
            # [x]. (In Sheets, select the column and Insert > Tick box for a
            # real one.)
            # [x] finished, [~] started and not finished, [ ] untouched.
            "done": "" if status == "skip"
                    else "[x]" if finished else "[~]" if partial else "[ ]",
            "ifc_file": name,
            "annotator": ", ".join(sorted(who)),
            "status": status,
            "storeys": pl.get("storeys", 0),
            "rooms": pl.get("rooms", 0),
            "links": pl.get("links", 0),
            "floor_links": pl.get("floor_links", 0),
            "size_mb": f"{mb:.1f}",
            "notes": notes,
        })
        if i % 25 == 0:
            print(f"  scanned {i}/{len(ifcs)}")

    cols = ["done", "ifc_file", "annotator", "status", "storeys", "rooms",
            "links", "floor_links", "size_mb", "notes"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print(f"\n{len(rows)} model(s) -> {out}")
    print(f"  ok     {tally['ok']:3d}  nothing unusual")
    print(f"  check  {tally['check']:3d}  annotatable, with a caveat in notes")
    print(f"  skip   {tally['skip']:3d}  not annotatable, box left blank")
    ticked = sum(1 for r in rows if r["done"] == "[x]")
    started = sum(1 for r in rows if r["done"] == "[~]")
    if ticked or started:
        print(f"\n  [x] {ticked} finished, [~] {started} started and unfinished")
    return 0


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


if __name__ == "__main__":
    sys.exit(main())

"""Turn collected annotations into ground-truth files, and report agreement.

Run this once annotators have returned their work. It composes each annotation
into a hierarchical building graph (via `compose.py`, the same code path the
tool's download button uses, so nothing can drift between them) and writes the
pair form the connectivity evaluator reads.

Where two people annotated the same building it also reports inter-annotator
agreement. That number is the ceiling on any score measured against this data:
if two humans agree on only 80% of links, a method cannot meaningfully be shown
to be better than 80% here, and a paper that omits it is overclaiming.

    python annotator/build_gt.py --out dataset/annotated_gt

Annotators using the statically hosted build have no server to save to and send
their work back as files. Point `--inbox` at wherever those land; single
annotations and whole bundles are both accepted, and are read for the identity
inside them rather than trusting the filename:

    python annotator/build_gt.py --inbox ~/returned --out dataset/annotated_gt
    python annotator/build_gt.py --agreement-only
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compose import compose, connectivity_gt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ANNO = os.path.join(HERE, "annotations")


def _ingest_inbox(inbox: str) -> list:
    """Unpack files returned by annotators using the browser-only build.

    That build has no server to save to, so annotators send back either a
    single annotation or a bundle of everything they did. Both arrive here as
    ordinary JSON files with no naming convention we control, so the identity
    is read from inside the file rather than from its name.
    """
    out = []
    for p in sorted(glob.glob(os.path.join(inbox, "*.json"))):
        try:
            with open(p) as fh:
                doc = json.load(fh)
        except Exception as e:
            print(f"  ! {os.path.basename(p)}: not readable JSON ({e})")
            continue
        items = (doc.get("annotations") or []
                 if isinstance(doc, dict) and doc.get("format") == "bimsg-annotation-bundle"
                 else [doc])
        for a in items:
            if not isinstance(a, dict) or not a.get("model") or not a.get("annotator"):
                print(f"  ! {os.path.basename(p)}: entry without model/annotator, skipped")
                continue
            out.append(a)
    return out


def load_pairs(inbox: str | None = None):
    """(model, annotator) -> (plan, annotation), for everything on disk."""
    out = {}
    sources = [(os.path.basename(p)[:-5], p) for p in
               sorted(glob.glob(os.path.join(ANNO, "*.json")))]

    extra = _ingest_inbox(inbox) if inbox else []
    for a in extra:
        model, who = a["model"], a["annotator"]
        plan_p = os.path.join(DATA, f"{model}.plan.json")
        if not os.path.exists(plan_p):
            print(f"  ! {model}__{who}: no plan for {model}, skipped")
            continue
        key = (model, who)
        if key in out:
            print(f"  ! {model}__{who}: appears twice in the inbox, keeping the later")
        with open(plan_p) as fh:
            out[key] = (json.load(fh), a)

    for base, p in sources:
        if "__" not in base:
            continue
        model, who = base.split("__", 1)
        plan_p = os.path.join(DATA, f"{model}.plan.json")
        if not os.path.exists(plan_p):
            print(f"  ! {base}: no plan for {model}, skipped")
            continue
        try:
            with open(plan_p) as fh:
                plan = json.load(fh)
            with open(p) as fh:
                anno = json.load(fh)
        except Exception as e:
            print(f"  ! {base}: unreadable ({e})")
            continue
        out[(model, who)] = (plan, anno)
    return out


def _judged(anno):
    """Every judgement as key -> verdict, for agreement scoring."""
    out = {}
    for rid, v in (anno.get("rooms") or {}).items():
        if v.get("verdict"):
            out[("room", rid)] = v["verdict"]
    for k, v in (anno.get("edges") or {}).items():
        if v.get("verdict"):
            out[("link", k)] = v["verdict"]
    for k, v in (anno.get("vertical") or {}).items():
        if v.get("verdict"):
            out[("vertical", k)] = v["verdict"]
    return out


def agreement(a, b):
    """Raw agreement and Cohen's kappa over the items both people judged."""
    ja, jb = _judged(a), _judged(b)
    shared = set(ja) & set(jb)
    if not shared:
        return None
    same = sum(1 for k in shared if ja[k] == jb[k])
    po = same / len(shared)

    labels = {ja[k] for k in shared} | {jb[k] for k in shared}
    fa = {L: sum(1 for k in shared if ja[k] == L) / len(shared) for L in labels}
    fb = {L: sum(1 for k in shared if jb[k] == L) / len(shared) for L in labels}
    pe = sum(fa[L] * fb[L] for L in labels)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    disagreed = [{"item": list(k), "a": ja[k], "b": jb[k]}
                 for k in sorted(shared) if ja[k] != jb[k]]

    # Kappa collapses towards 0 when one annotator uses essentially one label,
    # because chance agreement then equals observed agreement. That is exactly
    # the situation here -- the pipeline is usually right, so "correct" will
    # dominate -- and a kappa of 0.0 beside a raw agreement of 0.9 reads as a
    # disaster when it only means the marginals are skewed. Say so explicitly
    # rather than let the number be quoted on its own.
    skew = max(max(fa.values()), max(fb.values()))
    return {"shared": len(shared), "agree": same, "raw": round(po, 3),
            "kappa": round(kappa, 3), "skew": round(skew, 3),
            "kappa_unreliable": skew >= 0.90,
            "disagreements": disagreed}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="dataset/annotated_gt")
    ap.add_argument("--agreement-only", action="store_true")
    ap.add_argument("--include-incomplete", action="store_true",
                    help="write partially annotated buildings too")
    ap.add_argument("--inbox",
                    help="directory of annotation/bundle files returned by "
                         "annotators using the browser-only build")
    args = ap.parse_args()

    pairs = load_pairs(args.inbox)
    if not pairs:
        where = "annotator/annotations/"
        if args.inbox:
            where += f" or {args.inbox}"
        print(f"no annotations found in {where}")
        return

    by_model = defaultdict(list)
    for (model, who) in pairs:
        by_model[model].append(who)

    print(f"{len(pairs)} annotation file(s) across {len(by_model)} model(s)\n")

    # ---- inter-annotator agreement ------------------------------------
    agree_rows = []
    for model, whos in sorted(by_model.items()):
        for x, y in itertools.combinations(sorted(whos), 2):
            r = agreement(pairs[(model, x)][1], pairs[(model, y)][1])
            if not r:
                continue
            agree_rows.append({"model": model, "a": x, "b": y, **r})
            print(f"  agreement {model}: {x} vs {y} — "
                  f"{r['agree']}/{r['shared']} = {r['raw']:.3f}, kappa {r['kappa']:.3f}")
            if r["kappa_unreliable"]:
                print(f"      kappa is not meaningful here: one annotator used the "
                      f"same verdict for {r['skew']:.0%} of items, so chance "
                      f"agreement ≈ observed. Quote raw agreement instead.")
    if not agree_rows:
        print("  (no building annotated twice — agreement cannot be measured yet,\n"
              "   so any score from this data has an unknown ceiling)")
    print()

    if args.agreement_only:
        return

    # ---- ground truth ------------------------------------------------
    os.makedirs(args.out, exist_ok=True)
    written = skipped = 0
    for (model, who), (plan, anno) in sorted(pairs.items()):
        anno.setdefault("annotator", who)
        g = compose(plan, anno)
        if not g["complete"] and not args.include_incomplete:
            c = g["counts"]
            missing = []
            if c["rooms_judged"] < c["rooms_total"]:
                missing.append(f"rooms {c['rooms_judged']}/{c['rooms_total']}")
            if c["links_judged"] < c["links_total"]:
                missing.append(f"links {c['links_judged']}/{c['links_total']}")
            if c["vertical_judged"] < c["vertical_total"]:
                missing.append(
                    f"floor links {c['vertical_judged']}/{c['vertical_total']}")
            print(f"  {model}__{who}: incomplete ({', '.join(missing)}) — skipped")
            if c.get("rooms_labelled_only"):
                print(f"      {c['rooms_labelled_only']} room(s) were relabelled "
                      f"but never given a verdict, so they are dropped; ask for "
                      f"1-5 on those")
            skipped += 1
            continue
        json.dump(g, open(os.path.join(args.out, f"{model}__{who}.graph.json"), "w"),
                  indent=1)
        json.dump(connectivity_gt(g),
                  open(os.path.join(args.out, f"{model}__{who}.gt.json"), "w"), indent=1)
        written += 1
        c = g["counts"]
        print(f"  {model}__{who}: {c['rooms_kept']} rooms, {c['links_kept']} links, "
              f"{c['vertical_kept']} floor links, {len(g['held_out'])} held out")

    summary = {"files": len(pairs), "written": written, "skipped": skipped,
               "agreement": agree_rows}
    json.dump(summary, open(os.path.join(args.out, "_summary.json"), "w"), indent=2)
    print(f"\n{written} building graph(s) -> {args.out}")
    if skipped:
        print(f"{skipped} skipped as incomplete (use --include-incomplete to force)")


if __name__ == "__main__":
    main()

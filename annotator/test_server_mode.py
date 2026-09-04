"""Check the Flask backend, in the layout a real corpus actually has.

This is the third storage backend and the only one that had no test. What it
missed: `send_from_directory` resolves a relative directory against the app's
root path (`annotator/`), not the working directory, so a relative `--plans`
or `BIMSG_PLANS` pointing anywhere else returned 404 for every plan while
`os.path.exists` in the same handler said the file was right there.

So the layout here is the awkward one on purpose: plans in a `plans/`
subfolder, IFC files in the parent beside each other, and the path handed to
the server **relative**. That is how a corpus is kept -- a directory of models
with the plans exported underneath it -- and both halves have to work, the
plan route and the 3D view's IFC route.

    python annotator/test_server_mode.py
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

failures = 0


def ok(msg: str, cond: bool) -> None:
    global failures
    if not cond:
        failures += 1
    print(("  PASS " if cond else "  FAIL ") + msg)


def main() -> int:
    src = [os.path.join(HERE, "data", f"{m}.plan.json") for m in ("model_0", "model_9")]
    for p in src:
        if not os.path.exists(p):
            print(f"missing {p} — run main/export_plans.py first")
            return 1

    try:
        import flask  # noqa: F401
    except ImportError:
        print("flask not installed; skipping the server backend test")
        return 0

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        corpus = os.path.join(td, "corpus")
        plans = os.path.join(corpus, "plans")
        annos = os.path.join(td, "annotations")
        os.makedirs(plans)
        for p in src:
            shutil.copy2(p, plans)
        # IFC files sit in the parent, beside nothing else -- the normal shape
        # of a downloaded corpus. Stand-ins: only the lookup is under test.
        for m in ("model_0", "model_9"):
            with open(os.path.join(corpus, f"{m}.ifc"), "w") as fh:
                fh.write("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n"
                         "END-ISO-10303-21;\n")

        # Relative, from a directory that is not annotator/. This is the case
        # that failed; an absolute path would have passed all along.
        os.chdir(td)
        os.environ["BIMSG_PLANS"] = os.path.relpath(plans, td)
        os.environ["BIMSG_ANNOTATIONS"] = os.path.relpath(annos, td)
        sys.path.insert(0, HERE)
        try:
            import app as A
            importlib.reload(A)
            c = A.app.test_client()

            models = c.get("/api/models").get_json()
            ok(f"lists the plans from a subfolder ({len(models)})", len(models) == 2)
            ok("models are in natural order", models[0]["model"] == "model_0")

            name = models[0]["model"]
            r = c.get(f"/api/plan/{name}")
            ok(f"serves the plan from a relative --plans ({r.status_code})",
               r.status_code == 200)
            plan = r.get_json() if r.status_code == 200 else {}
            ok("the plan parses", isinstance(plan.get("storeys"), list))

            ok("an IFC in the parent directory is found",
               all(m["hasIfc"] for m in models))
            ok("the listing reports the download size",
               all(m["ifcBytes"] > 0 for m in models))
            r = c.get(f"/api/ifc/{name}")
            ok(f"serves the IFC for the 3D view ({r.status_code})",
               r.status_code == 200 and r.get_data()[:13] == b"ISO-10303-21;")

            # Annotating, which is the part that loses work if it is wrong.
            a = {"rooms": {}, "edges": {}, "vertical": {},
                 "added_edges": [], "added_vertical": [], "missing_rooms": []}
            for st in plan.get("storeys") or []:
                for room in st["rooms"]:
                    a["rooms"][room["id"]] = {"verdict": "real"}
            ok("saving an annotation succeeds",
               c.post(f"/api/annotation/{name}/tester", json=a).status_code == 200)
            ok("it lands in the annotations directory",
               os.path.exists(os.path.join(annos, f"{name}__tester.json")))
            back = c.get(f"/api/annotation/{name}/tester").get_json()
            ok("it reads back intact",
               len(back.get("rooms") or {}) == len(a["rooms"]))
            listed = c.get("/api/models").get_json()
            ok("progress shows in the model list",
               (next(m for m in listed if m["model"] == name)
                ["annotated"].get("tester") or 0) == len(a["rooms"]))

            # The parent-directory lookup must not become a way out of it.
            ok("an unknown model is 404, not a traversal",
               c.get("/api/plan/nope").status_code == 404)
            for evil in ("..%2f..%2fetc%2fpasswd", "..%2fmodel_0", "%2e%2e%2fmodel_0"):
                ok(f"path traversal is refused ({evil[:18]})",
                   c.get(f"/api/plan/{evil}").status_code in (400, 404))
                ok(f"path traversal is refused on the IFC route ({evil[:18]})",
                   c.get(f"/api/ifc/{evil}").status_code in (400, 404))
        finally:
            os.chdir(cwd)
            for k in ("BIMSG_PLANS", "BIMSG_ANNOTATIONS"):
                os.environ.pop(k, None)

    print()
    return failures


if __name__ == "__main__":
    n = main()
    print(f"FAIL the server backend has {n} problem(s)" if n else
          "PASS the server backend serves plans and IFC files from a real "
          "corpus layout, over a relative path")
    sys.exit(1 if n else 0)

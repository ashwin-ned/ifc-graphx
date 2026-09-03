# Annotation guide

**Read this once before you start. It takes five minutes and will save you an
hour.**

You are looking at a computer's attempt to read a building. Your job is to say
where it got the building right and where it got it wrong. You are **not**
drawing the building from scratch, and you do not need to know anything about
IFC, BIM or robotics to do this well.

---

## 1. What you are judging, and why it matters

A building model (an IFC file) usually says *which rooms exist*. It almost
never says *which rooms connect to which* — that a door in this wall joins the
kitchen to the hallway. Our pipeline tries to work that out from the geometry.
Nobody knows how often it is right, because nobody has ever checked. That is
what you are doing.

So the single most valuable thing you produce is a verdict on a **link**: is
this passage real? Room labels matter too, but connectivity is the part no
existing dataset has.

---

## 2. Getting started

1. Open the tool in your browser (the person who sent you this will give you a
   link, usually `http://<machine>:8000`).
2. **Type your name in the top-right box before you touch anything else.**
   Your work is saved under that name. If you leave it blank, nothing saves.
3. Pick a model from the dropdown on the left.
4. Press <kbd>?</kbd> at any time for a short reminder of the keys.

Your work saves automatically every 20 seconds, and whenever you press
<kbd>Ctrl</kbd>+<kbd>S</kbd>. The tab warns you if you try to close it with
unsaved changes.

### If you were given a folder of files, read this (best option)

If you were sent a folder containing `.ifc` files and `.plan.json` files:

1. Open the tool in **Chrome or Edge** (this part does not work in Firefox or
   Safari).
2. Press **Open dataset folder…** — it is the first thing in the left-hand
   panel, above the model list — and pick that folder.
3. Allow the browser to edit files in it when it asks.

Now your work is written straight back into that folder, into a file called
`bimsg-annotations.json`, every time it saves. Nothing to download and nothing
to lose — when you are done, send that one file back. If you reopen the same
folder later, everything you did is still there.

The models load one after another; use **Next ›** or <kbd>Ctrl</kbd>+<kbd>→</kbd>
to move through them.

### If you were sent a web link (github.io) without a folder, read this

That version runs entirely in your browser. **There is no server, so "saved"
means saved in this browser on this computer — and nowhere else.** It is gone
if you clear your browsing data, if you use a private window, or if you switch
to another machine.

So there is one extra step, and it is the one that actually delivers your work:

> When you finish a session, press **Download all my work** — near the bottom
> of the left-hand panel, under Save — and send that file to whoever asked
> you to do this.

The sidebar tells you what has not been downloaded yet. Do it at the end of
every session, not just at the very end of the job — a downloaded file is safe,
a browser is not.

**Load work from file…** puts a downloaded file back, which is how you carry on
from a different computer.

If you were instead given an address like `http://<something>:8000`, your work
goes straight to that machine and none of the above applies.

---

## 3. Checking against the real model

If your folder has the `.ifc` files (or you are using the hosted link), the
buttons at the top of the plan give you three layouts:

| Button | Shows |
|---|---|
| **Plan** | the floor plan alone |
| **Split** | plan and 3D model side by side — drag the divider to rebalance |
| **IFC model** | the 3D model alone |

<kbd>T</kbd> cycles through them. **Split** is the one to work in when you are
unsure about a floor: you judge on the plan and check against the model without
losing your place in either.

### Moving around the model

Drag to orbit, right-drag (or shift-drag) to pan, scroll to zoom — the zoom
goes toward your cursor, so point at a room and scroll to get into it.
**Double-click anything to centre on it**, which is the quickest way to inspect
one corner. **Rooms** frames the floor you are annotating, which is where the view starts;
**Fit** pulls back to the whole model including the site around it; **Top**
looks straight down, which is the view to compare against the plan.

### Slicing through the floors

The slider along the bottom cuts the model vertically, and the label tells you
which storey you are at. Two checkboxes control it:

- **follow floor** (on by default) — the section jumps to whichever storey you
  are annotating, so switching floors on the left switches the model too and
  you never set it by hand. Touching the slider turns this off, so you can look
  around freely; tick it again to re-link.
- **single floor** — show only that storey, rather than everything below it.
  Turn it off to see the floor sitting on the ones underneath.

The plan's storey list and the model's are matched by **height**, not by
position in the list, because the pipeline sometimes re-bands storeys. If they
disagree the label says which model storey it picked and how far off it was —
worth a note, since it usually means the extraction got the floor wrong.

The 3D view is only for checking. All your judgements are made on the plan.

It loads the 3D libraries from the internet the first time you open it, so it
needs a connection, and a large building can take a few seconds. If it fails,
everything else keeps working.

## 4. How to read the plan

**Colour tells you what a thing is.** Your verdict is drawn as the *outline*,
so it never hides the thing underneath.

| On the plan | Means |
|---|---|
| **Blue** room | The building file explicitly says this room exists. |
| **Violet** dashed room | The file did *not* say this room exists — our pipeline worked it out from the walls. Treat these with extra suspicion. |
| **Grey** shapes | Walls. |
| **Orange** dots | Doors. |
| **Green** line | A link the model thinks is a doorway. |
| **Amber** dashed line | A link the model thinks is an open passage (an archway, or an opening with no door). |
| **Violet** dotted line / dot above a room | A floor-to-floor link — a stair or lift. |
| **Pink** line | A link *you* added. |

The outline of a room or link shows your verdict: green = correct, red =
spurious, amber dashed = unsure.

---

## 5. The main loop

Click a room or a link, then press a number.

The two keys mean different things depending on what you selected, so the
buttons say which.

**On a room:**

| Key | Button | Means |
|---|---|---|
| <kbd>1</kbd> | Real room | A real room, and its outline is about right. |
| <kbd>2</kbd> | Not a room | There is no room here — a shaft, a void, or an artefact of the recovery. |
| <kbd>3</kbd> | Unsure | You genuinely cannot tell. |
| <kbd>4</kbd> | Merge | This and a neighbour are really one room. |
| <kbd>5</kbd> | Split | This one shape covers several real rooms. |
| <kbd>0</kbd> | — | Clear the verdict, leave it unjudged. |

**On a link (or a floor-to-floor link):**

| Key | Button | Means |
|---|---|---|
| <kbd>1</kbd> | Can walk | You can go straight between these two rooms. |
| <kbd>2</kbd> | Cannot walk | There is no way through — a wall, a window, or the route actually goes via a third room. |
| <kbd>3</kbd> | Unsure | You genuinely cannot tell. |

So key <kbd>2</kbd> always means **"this is not real"** — either the room does
not exist, or you cannot walk that way. It is not a judgement about the room's
*name*: to fix a name, edit the label box and still give the room a verdict.

### Accepting the rest of a floor

The pipeline gets most links right, and judging seventy of them one at a time is
punishing. So the intended rhythm is: **mark the wrong ones first, then accept
the rest.**

> **Rest of this floor is correct** (in the left panel, under Progress, or
> <kbd>Shift</kbd>+<kbd>1</kbd>) marks every room and link on the floor you have
> *not* judged as correct.

It never touches a verdict you have already given, so anything you marked
spurious or unsure stays exactly as it is. <kbd>Ctrl</kbd>+<kbd>Z</kbd> undoes
the whole sweep in one go.

It does not touch floor-to-floor links. There are only a handful per building
and they are what joins the storeys together, so each is worth a look.

Please still *look* at the floor before sweeping it. Verdicts made this way are
recorded separately from ones you considered individually, because a building
that was swept end to end is weaker evidence than one that was checked — and we
would rather know which is which than have a dataset that quietly overstates
itself.

### Please use "unsure" freely

This is the most important instruction in this document. **A guessed verdict is
worse than an honest "unsure."** An "unsure" gets routed to a second person; a
wrong "correct" silently corrupts the dataset and we may never catch it. There
is no prize for judging every item. If you are hesitating, that *is* the
answer — press <kbd>3</kbd> and move on.

---

## 6. Deciding whether a link is real

The question is always: **could a person walk directly from one room to the
other, without passing through a third room?**

- A door between them → yes, real. Even if the door is shut. A door that can be
  opened is a connection.
- An open archway or a gap in the wall → yes, real.
- A window between them → **no**. Not a passage.
- A solid wall, however thin → **no**.
- They only touch at a corner → **no**.
- The line passes *through* a third room on its way → **no**, mark it spurious.
  The link should be room-to-room directly.

If you cannot see any opening on the plan but the model claims a link, look
closely at the wall between them — doors are drawn as small orange dots, and
the opening is sometimes narrow. If there is still nothing there, it is
spurious.

---

## 7. Adding what the model missed

The model only proposes; it also *omits*. Two tools for that:

- **<kbd>A</kbd> — Link rooms.** Click one room, then another, to add a link
  the model failed to find. Both rooms must be on the floor you are looking at.
- **<kbd>M</kbd> — Mark missing room.** Click on a spot where a real room
  exists that the model never found. You will be asked for a name.

**Anything you add, you can remove.** Click it and press <kbd>Del</kbd>, or use
the Delete button in the right-hand panel. And <kbd>Ctrl</kbd>+<kbd>Z</kbd>
undoes anything at all — verdicts, additions, deletions, label edits.

You cannot delete something the *model* proposed. That is deliberate: the
record that the pipeline proposed a link and was wrong is exactly the data we
need. Mark it spurious (<kbd>2</kbd>) instead.

---

## 8. Chaining the floors together

**Do not skip this section — it is the part most people miss.**

Judging each floor separately gives us a stack of disconnected floor plans. A
robot needs one building: it has to know that the stair in the ground-floor
hallway arrives in the first-floor landing. Those floor-to-floor links are
listed in the panel on the bottom right.

**To judge a predicted floor link:** click it in the list. The view jumps to
the floor it starts on. Ask the same question as before — can you actually get
between these two floors here? — and press <kbd>1</kbd>, <kbd>2</kbd> or
<kbd>3</kbd>.

**To add a floor link the model missed:**

1. Press <kbd>V</kbd>.
2. Click the room containing the stair or lift.
3. Switch floor with <kbd>[</kbd> or <kbd>]</kbd> (or click a storey on the
   left). Your first room stays selected — the banner reminds you.
4. Click the room the stair arrives in.

A common real case: a stairwell that the file records as a separate room on
each floor, with nothing joining them. Those need linking by hand, and without
them the building falls into disconnected pieces.

---

## 9. When a floor is done

A storey shows a **✓** in the left-hand list once every room and every link on
it has a verdict. The progress bars show where you are.

A building counts as complete only when every room, every link **and every
floor-to-floor link** is judged. Incomplete buildings are not used as ground
truth — partial work is not wasted, but it does not enter the dataset until it
is finished, so it is better to finish one building than to half-do three.

---

## 10. Keys

| Key | Does |
|---|---|
| <kbd>1</kbd>–<kbd>5</kbd> | Set verdict on what is selected |
| <kbd>0</kbd> | Clear the verdict |
| <kbd>Shift</kbd>+<kbd>1</kbd> | Accept the rest of this floor as correct |
| <kbd>Esc</kbd> | Back to judging mode |
| <kbd>A</kbd> | Link two rooms on this floor |
| <kbd>V</kbd> | Link two rooms across floors |
| <kbd>M</kbd> | Mark a missing room |
| <kbd>Del</kbd> | Delete the thing you added |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> | Undo |
| <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> | Redo |
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | Save now |
| <kbd>[</kbd> <kbd>]</kbd> | Previous / next floor |
| <kbd>F</kbd> | Fit the plan to the window |
| <kbd>T</kbd> | Cycle plan / split / model |
| <kbd>Ctrl</kbd>+<kbd>→</kbd> / <kbd>Ctrl</kbd>+<kbd>←</kbd> | Next / previous building |
| <kbd>?</kbd> | Quick reference |

Scroll to zoom, drag to pan.

---

## 11. Room labels

When you select a room there is a label box. Two cases:

- The room **has a name from the file** ("KITCHEN"). Only change it if the name
  is plainly wrong.
- The room is shown with a **`?`** ("storage?"). It had no name and the model
  guessed one. Confirm the guess by leaving it, or type the correct one. This
  is ground truth for the labelling half of the project.

Use ordinary words — `kitchen`, `bedroom`, `corridor`, `stair`, `bathroom`,
`office`, `storage`. Lowercase is fine. If you cannot tell what a room is for,
leave the label and mark the room <kbd>3</kbd> unsure.

---

## 12. Common questions

**A room looks wrong but I'm not sure it's "spurious".**
If the room exists but has the wrong shape, mark it <kbd>4</kbd> merge or
<kbd>5</kbd> split if that describes it, otherwise <kbd>3</kbd> unsure with a
note. Reserve spurious for rooms that are not there at all.

**Two rooms are connected, but through a third room.**
That is not a direct link. Mark the proposed link spurious.

**The plan is empty.**
Some storeys genuinely have no rooms (roof levels, plant spaces). Move on.

**I made a mess.**
<kbd>Ctrl</kbd>+<kbd>Z</kbd> repeatedly. The tool keeps the last 120 actions.

**Can two of us annotate the same building?**
Yes, and please do for at least a couple of buildings — put different names in
the box. Comparing two independent annotations tells us how reliable the data
is, which sets the ceiling on every result we can claim from it.

**Something is broken.**
Your work is saved server-side every 20 seconds. Note the model and what you
were doing, and report it — do not try to work around it.

---

## 13. For whoever collects the results

Annotations land in `annotator/annotations/<model>__<name>.json`.

```bash
# build ground truth and report inter-annotator agreement
python annotator/build_gt.py --out dataset/annotated_gt

# agreement only, no files written
python annotator/build_gt.py --agreement-only
```

This writes, per finished building, a hierarchical `*.graph.json` (building →
storeys → rooms, with the floors chained) and a `*.gt.json` in the pair form
`main/eval_connectivity.py` reads.

Note on agreement: Cohen's kappa collapses toward 0 when one annotator uses
essentially a single verdict, which happens whenever the pipeline is mostly
right. `build_gt.py` detects this and says so — in that case quote **raw
agreement**, not kappa.

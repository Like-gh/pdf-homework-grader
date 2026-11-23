# PDF Homework Grader

> 🌐 Language / 语言：[中文](README.md) · [**English**](README_EN.md)

A **desktop app for teachers**: grade students' PDF / image homework in bulk — click to add checkmarks, crosses, comments and scores directly on each page, then export everything with one click:

- A ZIP of annotated PDFs (upload it back to Canvas / Moodle so students can see your feedback)
- An updated Canvas gradebook CSV (scores filled into the right assignment column)

It runs entirely **on your own computer** — no server, no internet, student data never leaves your machine. Built with PyQt6 + PyMuPDF.

---

## What it can do

| Situation | How it helps |
|-----------|--------------|
| Students submit PDFs or phone photos | Auto-sorted, opened one by one; images are converted to clean PDFs first |
| You need to check/cross, comment and score each one | One click = one mark; total score updates live |
| You can't finish a batch today | Progress and marks are saved automatically — pick up where you left off |
| Many assignments, worried about mixing them up | Work is separated by "batch"; each assignment stays independent |
| Done grading, need to return files + record scores | One click exports the ZIP and writes scores into the Canvas gradebook |

---

## 1. Installation

Requires Python 3.9 or newer. Open a terminal in the project folder and run:

```bash
pip install -r requirements.txt
```

This installs two dependencies:

- `PyMuPDF` — reading and exporting PDFs
- `PyQt6` — the graphical interface

---

## 2. Prepare the homework folder (unzip first!)

Student submissions downloaded from Canvas / Moodle are usually **a single ZIP — it cannot be graded directly**. Prepare it like this:

**Step 1: Download and unzip.** Download the submissions archive (e.g. `submissions.zip`) and **extract it** (right-click → Extract). A good habit is to work inside the bundled `work/downloads/` folder and make a subfolder for each assignment:

```
project folder/
└── work/
    └── downloads/
        └── homework4/          ← extract into this folder
            ├── 20230001_StudentA_hw4.pdf
            ├── 20230002_StudentB_hw4.pdf
            └── 20230003_StudentC_hw4.jpg   ← images work too
```

**Step 2: Check the folder depth.** The tool **only reads the first level** of the folder you select — it does not look into subfolders. When you open the chosen folder, you should see the student PDFs **directly**. If extraction added an extra layer (e.g. `homework4/submissions/student.pdf`), move the PDFs up one level into `homework4/`, or select the folder that actually contains the PDFs when setting the path.

**Step 3: Select it in the app.** After launching, click **Set Path** and choose the folder containing the student work — **not** the ZIP file itself.

Three key rules:

1. **It must be an extracted folder** — a ZIP archive cannot be graded.
2. **Filenames must start with a student identifier** (student number / SIS Login ID / ID all work). The app uses the leading number to match scores to students. Use underscores, e.g. `20230001_StudentA_hw4.pdf`.
3. PDFs are supported, as well as JPG / PNG / BMP / WEBP / GIF images (auto-converted to single-page PDFs).

> Submissions exported from Canvas / Moodle already carry student identifiers in the filenames, so no renaming is usually needed.

---

## 3. Launch and use

In the project folder, run:

```bash
python src/main.py
```

### 3.1 Choose the homework folder

Click **Set Path** on the toolbar and select the folder you prepared. The app lists all PDFs / images inside, sorted by filename.

### 3.2 Grade one by one

Click on the page to place marks. The toolbar offers:

| Button | Shortcut | Action |
|--------|----------|--------|
| Check | `T` | Checkmark (correct) |
| Cross | `C` | Cross (wrong) |
| Half | `B` | Half-right (check with a slash) |
| Score | `S` | Drop a score at the chosen value (e.g. +5) |
| Erase | `E` | Remove the last mark or comment (right-click also works) |
| Comment | — | Write a comment where you click; pick a preset or type your own |

- **Next student:** `Ctrl+N`; previous `Ctrl+P`; first `Ctrl+F`; last `Ctrl+L`
- **Jump to next ungraded student:** `Ctrl+G`
- **Turn page:** `Ctrl+,` previous, `Ctrl+.` next
- **Zoom:** `Ctrl+-` / `Ctrl++`
- **Undo / Redo:** `Ctrl+Z` / `Ctrl+R`

Switching students **auto-saves** the current one, so nothing is lost.

Mark and comment sizes scale with the page — whether a student submits A4 or a large scan, on-screen and exported results look consistent.

### 3.3 Set the full score

The "Full score" field on the right defaults to 100; change it per assignment. An over-score total is highlighted in red.

---

## 4. Export results (important)

When done, click **Export Canvas** on the toolbar. A series of dialogs appears:

### Step 1: Choose the gradebook CSV

Export the gradebook CSV from Canvas Grades and select it here.

### Step 2: Choose the assignment column

The app detects assignment columns from the CSV header (e.g. `Homework 1 (135353)`, with the assignment ID in parentheses). Pick the right one from the dropdown — no manual ID typing.

### Step 3: Missing submissions (you'll be asked)

If some students have no score, a dialog lists two kinds:

- **Not graded yet**: the file exists, but you haven't graded it in this batch
- **No submission**: no file found for that student

⚠️ **If you're still grading, choose "No"** — otherwise "not graded yet" students get written as 0. Choose "Yes" only when grading is complete and you really want to record zeros.

### Step 4: Choose where to save the ZIP

Finally, choose where to save the annotated PDF archive. The app pre-fills a sensible name and location (same as the homework folder, next to the gradebook) — usually just confirm.

When finished:

- You get a **ZIP**: all annotated PDFs from the current batch, filenames unchanged → upload it to Canvas / Moodle's "bulk upload submissions" so students see your comments.
- **The gradebook CSV is updated in place**: scores are filled into the column you chose; re-import that CSV into Grades if you want to sync them to Canvas.

> The export uses a Moodle / Canvas-compatible ZIP layout (UTF-8 flags on Chinese filenames), avoiding the "problem with the submission file" upload error.

---

## 5. Where results are stored

The app creates a `done/` folder inside your homework directory, separated by batch:

```
homework folder/
├── StudentA.pdf
├── StudentB.pdf
└── done/
    ├── _batches.json              # batch list
    └── batch_xxx/                 # one assignment
        ├── StudentA.pdf           # annotated result
        ├── StudentA.marks.json    # mark coordinates + comments (for restore)
        └── scores.csv             # score summary for this assignment
```

`done/` holds your real grading data and is excluded by `.gitignore` — it will never enter the code repo. To move your progress to another computer, copy this folder along.

---

## 6. Multiple assignments stay separate

The app detects "is this the same assignment":

- Folder contents unchanged → treated as a continuation of the same batch
- New submissions (filenames/sizes changed) → a new batch is created automatically

You can also use the **Batch** button on the toolbar to switch or create batches manually.

Each `marks.json` also records the source file's size and modification time, so even if the batch name is the same, replacing the source PDF won't wrongly restore old marks.

---

## 7. FAQ

**Q: `ModuleNotFoundError` (can't find canvas_export, etc.)?**
Run `python src/main.py` from the **project root folder** (not from inside `src/` or elsewhere).

**Q: The app opens empty / finds no students?**
Make sure you used **Set Path** to select the **extracted folder** (not the ZIP), that the PDFs sit directly in that folder's first level, and that filenames start with a student number or ID.

**Q: Scores don't match students on export?**
The leading number in each filename must match the SIS Login ID / ID in the Canvas gradebook. Name matching is also supported. The post-export dialog lists unmatched students — adjust filenames accordingly.

**Q: Chinese comments show as garbled text or boxes?**
The app uses a built-in CJK font, so this shouldn't happen. Unsupported symbols (e.g. emoji) are silently skipped.

**Q: I changed the source code — do already-exported PDFs update?**
No. Mark shapes are rendering logic; code changes only affect future exports. To refresh an existing result, click "Done" again for that student.

**Q: Moodle says "problem re-uploading submission"?**
Use the ZIP exported by this app (encoding handled). Don't re-zip it with Windows "Compress as ZIP" — that mishandles Chinese filenames.

---

## 8. Privacy

- The app runs entirely on your machine and **never uploads student data**.
- This repository contains no real homework, gradebooks, student info or personal paths.
- `src/config.json` is an empty template; your personal settings are generated locally at runtime and should not be committed to a public repo.

---

## License

MIT License — see [LICENSE](LICENSE). Free to use, modify and distribute; keep the copyright notice.

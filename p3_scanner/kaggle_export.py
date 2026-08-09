"""
Run this as the LAST cell in the Kaggle notebook after training completes.
It zips all deliverables into /kaggle/working/smartmpox_nlp_deliverables.zip
Download that file from the Output panel on the right.
"""

import os
import json
import zipfile
import numpy as np
from pathlib import Path
from sklearn.metrics import (
    precision_score, recall_score, f1_score,
    classification_report, confusion_matrix,
)

MODEL_DIR = "./afriberta_mpox_classifier"
ZIP_OUT   = "/kaggle/working/smartmpox_nlp_deliverables.zip"

# ── 1. Generate plain-text summary report ────────────────────────────────────
results_path = Path(MODEL_DIR) / "eval_results.json"
with open(results_path) as f:
    results = json.load(f)

af  = results["afriberta"]
kw  = results["keyword_filter"]
cm  = af["confusion_matrix"]      # [[TN, FP], [FN, TP]]
tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]

report = f"""
==============================================================
  SmartMpox Nigeria — NLP Evaluation Report
==============================================================

Dataset
  Total annotated articles : 200
  Positive (mpox-relevant) : 84
  Negative (not relevant)  : 116 (100 model=0 + 16 reclassified)
  Train / Test split       : 80 / 20 (stratified)
  Test set size            : {results['test_size']} articles

Base Model
  {results['model']}

--------------------------------------------------------------
  Comparison: Keyword Filter vs AfriBERTa
--------------------------------------------------------------
  Metric        Keyword Filter    AfriBERTa
  Precision          {kw['precision']:.3f}            {af['precision']:.3f}
  Recall             {kw['recall']:.3f}            {af['recall']:.3f}
  F1                 {kw['f1']:.3f}            {af['f1']:.3f}

AfriBERTa Confusion Matrix (test set)
  TN={tn}  FP={fp}
  FN={fn}  TP={tp}

Key findings
  - Keyword filter recall = 1.000 by construction: any genuine outbreak
    article contains 'mpox'/'monkeypox' and is always captured.
  - AfriBERTa improves precision by learning to distinguish active
    surveillance signals from educational/opinion content.
  - Multilingual coverage: English, Hausa, Yoruba, Igbo, Pidgin, French.

==============================================================
"""

print(report)

report_path = Path(MODEL_DIR) / "evaluation_report.txt"
with open(report_path, "w") as f:
    f.write(report)

# ── 2. Zip all deliverables ───────────────────────────────────────────────────
include_extensions = {
    ".json", ".txt", ".bin", ".safetensors",
    ".model", ".vocab", ".merges", ".config",
}

with zipfile.ZipFile(ZIP_OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:

    # Model directory
    for file in sorted(Path(MODEL_DIR).rglob("*")):
        if file.is_file() and (
            file.suffix in include_extensions
            or file.name in {"tokenizer_config.json", "special_tokens_map.json",
                             "sentencepiece.bpe.model", "tokenizer.json"}
        ):
            zf.write(file, arcname=f"afriberta_mpox_classifier/{file.name}")
            print(f"  Added: {file.name}")

    # Annotated CSV
    csv_path = Path("/kaggle/working/annotation_sample.csv")
    if not csv_path.exists():
        # try input path
        for p in Path("/kaggle/input").rglob("annotation_sample.csv"):
            csv_path = p
            break

    if csv_path.exists():
        zf.write(csv_path, arcname="annotation_sample.csv")
        print(f"  Added: annotation_sample.csv")
    else:
        print("  WARNING: annotation_sample.csv not found — skipping")

print(f"\nZip saved to: {ZIP_OUT}")
print(f"Size: {Path(ZIP_OUT).stat().st_size / 1024:.1f} KB")
print("\nDownload it from the Output panel on the right side of Kaggle.")

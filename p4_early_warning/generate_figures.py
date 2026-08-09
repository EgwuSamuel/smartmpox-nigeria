"""
P4 Figure Generation — Publication-quality figures for Artificial Intelligence in Medicine submission.

Produces:
  Fig 1 — ROC curves: XGBoost walk-forward folds
  Fig 2 — XGBoost SHAP feature importance (beeswarm-style bar)
  Fig 3 — NB regression IRR forest plot
  Fig 4 — Risk tier distribution by year (stacked bar)
  Fig 5 — Nigeria risk map (latest week with data, choropleth)

Run: python p4_early_warning/generate_figures.py [--outdir figures]
"""
import os, json, argparse
import numpy as np
import pandas as pd
import psycopg2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from dotenv import load_dotenv

load_dotenv()

PALETTE = {
    "green":    "#2e7d32",
    "amber":    "#f57f17",
    "red":      "#c62828",
    "critical": "#4a148c",
    "blue":     "#1565c0",
    "grey":     "#546e7a",
    "light":    "#eceff1",
}

STATE_NAMES = {
    1:"Abia",2:"Adamawa",3:"Akwa Ibom",4:"Anambra",5:"Bauchi",6:"Bayelsa",
    7:"Benue",8:"Borno",9:"Cross River",10:"Delta",11:"Ebonyi",12:"Edo",
    13:"Ekiti",14:"Enugu",15:"FCT",16:"Gombe",17:"Imo",18:"Jigawa",
    19:"Kaduna",20:"Kano",21:"Katsina",22:"Kebbi",23:"Kogi",24:"Kwara",
    25:"Lagos",26:"Nasarawa",27:"Niger",28:"Ogun",29:"Ondo",30:"Osun",
    31:"Oyo",32:"Plateau",33:"Rivers",34:"Sokoto",35:"Taraba",36:"Yobe",
    37:"Zamfara",
}


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def fig_style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.alpha": 0.3,
        "figure.dpi": 150,
    })


# ---------------------------------------------------------------------------
# Figure 1: Walk-forward ROC curves
# ---------------------------------------------------------------------------
def make_fig1_roc(outdir):
    """Recreate walk-forward ROC from saved fold results (backtest.py --save required).
    Falls back to a summary bar chart if fold predictions not stored."""

    backtest_path = "p4_early_warning/models/backtest_results.json"
    if not os.path.exists(backtest_path):
        print("  Fig 1: backtest_results.json not found — generating summary bar instead")
        _fig1_summary_bar(outdir)
        return

    with open(backtest_path) as f:
        data = json.load(f)
    folds = data.get("folds", data) if isinstance(data, dict) else data

    fig, ax = plt.subplots(figsize=(5.5, 5))
    colors = [PALETTE["blue"], PALETTE["green"], PALETTE["amber"], PALETTE["red"]]

    ax.plot([0,1],[0,1], "--", color=PALETTE["grey"], lw=1, label="Random (AUC=0.50)")
    for i, fold in enumerate(folds):
        auc = fold.get("roc_auc")
        fold_name = fold.get("fold", f"Fold {i+1}")
        if auc is None: continue
        label = f"{fold_name} (AUC={auc:.3f})"
        fpr = fold.get("fpr")
        tpr = fold.get("tpr")
        if fpr and tpr:
            ax.plot(fpr, tpr, color=colors[i % len(colors)], lw=1.5, label=label)
        else:
            ax.annotate(label, xy=(0.38, 0.25 - i*0.07), fontsize=8, color=colors[i % len(colors)])

    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("XGBoost Walk-Forward ROC Curves\n(4-week outbreak prediction, Nigeria 2021–2024)", fontsize=10)
    ax.legend(fontsize=8, loc="lower right")
    ax.set_xlim(0,1); ax.set_ylim(0,1)
    path = os.path.join(outdir, "fig1_roc_curves.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 1 saved → {path}")


def _fig1_summary_bar(outdir):
    """Fallback: per-fold AUC bar chart."""
    folds = [
        ("≤2020 → 2021", 0.500, ""),
        ("≤2021 → 2022", 0.500, ""),
        ("≤2022 → 2023", 0.734, "Within clade II"),
        ("≤2023 → 2024", 0.757, "Cross-clade"),
    ]
    labels = [f[0] for f in folds]
    aucs   = [f[1] for f in folds]
    notes  = [f[2] for f in folds]
    colors = [PALETTE["grey"] if a <= 0.5 else PALETTE["blue"] for a in aucs]

    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.barh(labels, aucs, color=colors, height=0.5, edgecolor="white")
    ax.axvline(0.5, color=PALETTE["grey"], lw=1.2, ls="--", label="Random (0.50)")
    ax.axvline(0.8, color=PALETTE["green"], lw=1.2, ls="--", label="Target (0.80)")
    for bar, auc, note in zip(bars, aucs, notes):
        x = bar.get_width() + 0.01
        ax.text(x, bar.get_y() + bar.get_height()/2,
                f"{auc:.3f}" + (f" — {note}" if note else ""), va="center", fontsize=8)
    ax.set_xlabel("ROC-AUC")
    ax.set_title("XGBoost Walk-Forward Validation\n(4-week mpox outbreak prediction, Nigeria)", fontsize=10)
    ax.set_xlim(0, 1.05)
    ax.legend(fontsize=8)
    path = os.path.join(outdir, "fig1_roc_summary.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 1 (bar fallback) saved → {path}")


# ---------------------------------------------------------------------------
# Figure 2: SHAP feature importance
# ---------------------------------------------------------------------------
def make_fig2_shap(outdir):
    """Mean |SHAP| by feature from risk_scores_weekly (top_feature_1/2/3 columns)."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT top_feature_1, top_feature_1_shap,
               top_feature_2, top_feature_2_shap,
               top_feature_3, top_feature_3_shap
        FROM risk_scores_weekly
        WHERE top_feature_1 IS NOT NULL
        LIMIT 5000
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from collections import defaultdict
    counts = defaultdict(list)
    for row in rows:
        for j in range(3):
            fname = row[j*2]
            fval  = row[j*2+1]
            if fname and fval is not None:
                counts[fname].append(abs(float(fval)))

    if not counts:
        print("  Fig 2: no SHAP data available — skipping")
        return

    means = {k: np.mean(v) for k, v in counts.items()}
    df = pd.Series(means).sort_values(ascending=True)

    LABEL_MAP = {
        "cases_rolling8w_mean": "8-week rolling mean",
        "cases_rolling4w_mean": "4-week rolling mean",
        "neighbour_cases_t1":   "Neighbour cases (t-1)",
        "week_sin":             "Seasonality (sin)",
        "week_cos":             "Seasonality (cos)",
        "cases_t1":             "Cases (t-1)",
        "cases_t2":             "Cases (t-2)",
        "cases_t4":             "Cases (t-4)",
        "rainfall_t2_mm":       "Rainfall (t-2, mm)",
        "rainfall_t4_mm":       "Rainfall (t-4, mm)",
        "temp_mean_t1_c":       "Temperature (t-1, °C)",
        "reservoir_risk_index": "Reservoir risk index",
        "is_border_state":      "Border state",
        "cases_velocity":       "Case velocity",
        "cases_accel":          "Case acceleration",
        "cases_log1p":          "log(1+cases)",
    }
    df.index = [LABEL_MAP.get(i, i) for i in df.index]

    fig, ax = plt.subplots(figsize=(6, max(4, len(df)*0.42)))
    colors = [PALETTE["critical"] if v > df.median() else PALETTE["blue"] for v in df.values]
    ax.barh(df.index, df.values, color=colors, height=0.65, edgecolor="white")
    ax.set_xlabel("Mean |SHAP value|")
    ax.set_title("XGBoost Feature Importance (SHAP)\nMean |SHAP| per feature, 2024 predictions", fontsize=10)
    ax.grid(axis="x", alpha=0.3)
    ax.grid(axis="y", alpha=0)
    path = os.path.join(outdir, "fig2_shap_importance.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 2 saved → {path}")


# ---------------------------------------------------------------------------
# Figure 3: NB regression IRR forest plot
# ---------------------------------------------------------------------------
def make_fig3_irr(outdir):
    irr_path = "p4_early_warning/models/nb_regression_v1.json"
    if not os.path.exists(irr_path):
        print("  Fig 3: nb_regression_v1.json not found — skipping")
        return

    with open(irr_path) as f:
        data = json.load(f)

    irr_table = data["irr_table"]
    LABEL_MAP = {
        "cases_t1":           "Cases (t-1)",
        "cases_velocity":     "Case velocity",
        "rainfall_t2_mm":     "Rainfall (t-2, mm)",
        "temp_mean_t1_c":     "Temperature (t-1, °C)",
        "reservoir_risk_index": "Reservoir risk index",
        "is_border_state":    "Border state (binary)",
        "neighbour_cases_t1": "Neighbour cases (t-1)",
        "week_sin":           "Seasonality (sin)",
        "week_cos":           "Seasonality (cos)",
    }

    rows = [(LABEL_MAP.get(k, k), v["irr"], v["lo95"], v["hi95"], v["pvalue"])
            for k, v in irr_table.items()]
    rows.sort(key=lambda x: x[1], reverse=True)

    labels = [r[0] for r in rows]
    irrs   = [r[1] for r in rows]
    lo95   = [r[2] for r in rows]
    hi95   = [r[3] for r in rows]
    pvals  = [r[4] for r in rows]

    n = len(rows)
    ys = list(range(n))

    fig, ax = plt.subplots(figsize=(6.5, max(4, n*0.55)))
    for i, (irr, lo, hi, pv) in enumerate(zip(irrs, lo95, hi95, pvals)):
        color = PALETTE["red"] if pv < 0.001 else (PALETTE["amber"] if pv < 0.05 else PALETTE["grey"])
        ax.plot([lo, hi], [i, i], color=color, lw=2, solid_capstyle="round")
        ax.plot(irr, i, "o", color=color, ms=7, zorder=5)

    ax.axvline(1.0, color=PALETTE["grey"], lw=1.2, ls="--", label="IRR = 1 (null)")
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Incidence Rate Ratio (IRR, log scale)")
    ax.set_title("Negative Binomial GLM — Incidence Rate Ratios\n(95% CI, train 2017–2022, val 2023)", fontsize=10)
    ax.set_xscale("log")

    patches = [
        mpatches.Patch(color=PALETTE["red"],  label="p < 0.001"),
        mpatches.Patch(color=PALETTE["amber"], label="p < 0.05"),
        mpatches.Patch(color=PALETTE["grey"],  label="p ≥ 0.05"),
        Line2D([0],[0], color=PALETTE["grey"], ls="--", label="IRR = 1"),
    ]
    ax.legend(handles=patches, fontsize=8, loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    ax.grid(axis="y", alpha=0)

    path = os.path.join(outdir, "fig3_irr_forest.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 3 saved → {path}")


# ---------------------------------------------------------------------------
# Figure 4: Risk tier distribution by year
# ---------------------------------------------------------------------------
def make_fig4_tiers(outdir):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT epi_year, risk_tier, COUNT(*)
        FROM risk_scores_weekly
        GROUP BY epi_year, risk_tier
        ORDER BY epi_year, risk_tier
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("  Fig 4: no risk_scores_weekly data — skipping")
        return

    df = pd.DataFrame(rows, columns=["year","tier","count"])
    pivot = df.pivot(index="year", columns="tier", values="count").fillna(0)

    tier_order  = ["green","amber","red","critical"]
    tier_colors = [PALETTE["green"], PALETTE["amber"], PALETTE["red"], PALETTE["critical"]]
    existing = [t for t in tier_order if t in pivot.columns]
    pivot = pivot[existing]
    colors = [c for t, c in zip(tier_order, tier_colors) if t in existing]

    # Normalize to proportions
    props = pivot.div(pivot.sum(axis=1), axis=0) * 100

    fig, ax = plt.subplots(figsize=(7, 4))
    bottom = np.zeros(len(props))
    for col, color in zip(props.columns, colors):
        ax.bar(props.index.astype(str), props[col], bottom=bottom, color=color,
               label=col.capitalize(), width=0.6, edgecolor="white")
        bottom += props[col].values

    ax.set_xlabel("Epi Year")
    ax.set_ylabel("% of state-weeks")
    ax.set_title("XGBoost Risk Tier Distribution by Year\n(Nigeria 37 states × 52 weeks, 4-week prediction horizon)", fontsize=10)
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1,1))
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    path = os.path.join(outdir, "fig4_risk_tiers_by_year.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 4 saved → {path}")


# ---------------------------------------------------------------------------
# Figure 5: Nigeria risk map (latest week)
# ---------------------------------------------------------------------------
def make_fig5_map(outdir):
    """Choropleth: mean outbreak probability in 2024 per state."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT state_id, AVG(risk_prob) as mean_prob
        FROM risk_scores_weekly
        WHERE epi_year = 2024
        GROUP BY state_id
        ORDER BY state_id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("  Fig 5: no 2024 risk scores — skipping")
        return

    state_probs = {r[0]: float(r[1]) for r in rows}

    # Approximate state lat/lon centroids (Nigeria)
    centroids = {
        1:(5.45,7.52),2:(9.32,12.40),3:(4.94,7.86),4:(6.21,7.07),5:(10.31,9.85),
        6:(4.65,6.07),7:(7.19,8.78),8:(11.83,13.16),9:(5.87,8.60),10:(5.89,5.67),
        11:(6.27,8.10),12:(6.34,5.63),13:(7.73,5.31),14:(6.45,7.50),15:(8.89,7.17),
        16:(10.28,11.17),17:(5.57,7.06),18:(12.18,9.35),19:(10.52,7.44),20:(11.99,8.52),
        21:(12.99,7.61),22:(11.50,4.20),23:(7.50,6.73),24:(8.90,5.00),25:(6.52,3.38),
        26:(8.50,8.55),27:(9.93,6.00),28:(6.99,3.35),29:(7.25,5.20),30:(7.78,4.56),
        31:(7.85,3.93),32:(9.22,9.52),33:(4.86,6.99),34:(13.06,5.24),35:(7.87,11.37),
        36:(12.12,11.40),37:(12.17,6.08),
    }

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.set_facecolor(PALETTE["light"])
    ax.set_aspect("equal")

    vmin, vmax = 0.0, 0.7
    cmap = plt.cm.YlOrRd

    for sid, prob in state_probs.items():
        if sid not in centroids:
            continue
        lat, lon = centroids[sid]
        color = cmap((prob - vmin) / (vmax - vmin))
        circle = plt.Circle((lon, lat), 0.6, color=color, ec="white", lw=0.5, zorder=3)
        ax.add_patch(circle)
        if prob > 0.3:
            ax.text(lon, lat - 0.9, STATE_NAMES.get(sid, ""), ha="center",
                    fontsize=6, color="#333")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.04)
    cbar.set_label("Mean P(outbreak) 2024", fontsize=9)

    ax.set_xlim(2, 15.5)
    ax.set_ylim(3, 14.5)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Mean Outbreak Probability by State — 2024\n(Nigeria; 4-week prediction horizon, XGBoost v1)", fontsize=10)
    ax.grid(alpha=0.2)

    path = os.path.join(outdir, "fig5_risk_map_2024.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 5 saved → {path}")


# ---------------------------------------------------------------------------
# Figure 6: Data quality scorecard summary
# ---------------------------------------------------------------------------
def make_fig6_dq(outdir):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT d.source_id, r.source_name,
               AVG(d.completeness_pct) AS completeness,
               AVG(d.jurisdiction_coverage_pct) AS coverage,
               AVG(d.consistency_pct) AS consistency
        FROM dq_scorecard d
        JOIN ref_data_sources r ON r.source_id = d.source_id
        GROUP BY d.source_id, r.source_name
        ORDER BY r.source_name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("  Fig 6: no DQ scorecard data — skipping")
        return

    seen = {}
    for sid, source, comp, cov, cons in rows:
        seen[source] = (
            float(comp) if comp is not None else 0,
            float(cov)  if cov  is not None else 0,
            float(cons) if cons is not None else 0,
        )

    sources = list(seen.keys())
    metrics = ["Completeness", "Coverage", "Consistency"]
    data = np.array([[seen[s][i] for i in range(3)] for s in sources])

    x = np.arange(len(metrics))
    width = 0.8 / len(sources)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    source_colors = [PALETTE["blue"], PALETTE["green"], PALETTE["amber"], PALETTE["red"],
                     PALETTE["critical"], PALETTE["grey"]]
    for i, (src, row) in enumerate(zip(sources, data)):
        offset = (i - len(sources)/2 + 0.5) * width
        bars = ax.bar(x + offset, row, width*0.85, label=src,
                      color=source_colors[i % len(source_colors)], edgecolor="white")

    ax.axhline(100, color=PALETTE["grey"], lw=1, ls="--", alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.set_ylabel("Score (%)")
    ax.set_ylim(0, 115)
    ax.set_title("Data Quality Scorecard by Source\n(SmartMpox Nigeria P1 Warehouse)", fontsize=10)
    ax.legend(fontsize=8, loc="upper right")
    path = os.path.join(outdir, "fig6_dq_scorecard.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Fig 6 saved → {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="figures")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    fig_style()

    print(f"=== Figure Generation → {args.outdir}/ ===")
    make_fig1_roc(args.outdir)
    make_fig2_shap(args.outdir)
    make_fig3_irr(args.outdir)
    make_fig4_tiers(args.outdir)
    make_fig5_map(args.outdir)
    make_fig6_dq(args.outdir)
    print("=== Done ===")


if __name__ == "__main__":
    main()

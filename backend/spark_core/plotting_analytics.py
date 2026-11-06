"""Ported from SPARK utils/plotting.py: Analytics (clusters, PCA, insights, admissions)
and Course Director figures.

Same rules as spark_core/plotting.py: every function returns a go.Figure and takes its
settings as arguments (no Streamlit). Visual design is kept from the original: risk
colours, hover templates, the highlighted selected student (magenta diamond), convex
hulls in 3D.

Changes:
  * The admissions "vs grade" trendline is fitted with numpy (no statsmodels dependency).
  * Lower-is-better section metrics sort ascending in the ranked bar chart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sklearn.decomposition import PCA

from spark_core.ml.clustering import FEATURE_COLS, RISK_COLORS, RISK_ORDER
from spark_core.plotting import PlotSettings

try:
    from scipy.spatial import ConvexHull
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    _HAS_SCIPY = False

HOVER = dict(align="left", font=dict(family="Courier New, monospace", size=12))
SELECTED = dict(color="#FF00C8", symbol="diamond", line=dict(width=2, color="#111111"))
CUSTOM = ["STUDENT_NAME", "cluster_label", "grade_ratio", "avg_percentage", "std_percentage",
          "late_rate", "on_time_rate", "missed_rate", "STUDENT_ID"]
HOVER_TEMPLATE = (
    "<b>%{customdata[0]} (%{customdata[8]})</b><br>"
    "Risk:           %{customdata[1]}<br>"
    "Grade Ratio:    %{customdata[2]:.2f}<br>"
    "Average %:      %{customdata[3]:.2f}<br>"
    "Std.Dev %:      %{customdata[4]:.2f}<br>"
    "Late Rate:      %{customdata[5]:.2f}<br>"
    "On-Time Rate:   %{customdata[6]:.2f}<br>"
    "Missed Rate:    %{customdata[7]:.2f}<br><extra></extra>"
)


def _clean(fig: go.Figure, s: PlotSettings, height: int | None = None) -> go.Figure:
    _, grid, axis, _ = s.colors()
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hoverlabel=HOVER,
                      margin=dict(l=40, r=20, t=50, b=40), legend=dict(itemsizing="constant"))
    if height:
        fig.update_layout(height=height)
    fig.update_xaxes(showgrid=False, showline=True, ticks="outside", tickcolor=axis, linecolor=axis,
                     tickfont=dict(color=axis))
    fig.update_yaxes(showgrid=True, gridcolor=grid, linecolor=axis, tickfont=dict(color=axis))
    return fig


def _labelled(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(subset=["cluster_label"]).copy()


# ---------------------------------------------------------------- PCA
def _pca(df: pd.DataFrame, n: int) -> tuple[pd.DataFrame, list[str]]:
    pca = PCA(n_components=n)
    pcs = pca.fit_transform(df[FEATURE_COLS].astype(float))
    for i in range(n):
        df[f"pc{i + 1}"] = pcs[:, i]
    var = pca.explained_variance_ratio_ * 100
    return df, [f"PC{i + 1} ({var[i]:.0f}%)" for i in range(n)]


def clusters_2d(clusters: pd.DataFrame, selected: str | None, s: PlotSettings = PlotSettings()) -> go.Figure:
    df, (xl, yl) = _pca(_labelled(clusters), 2)
    fig = px.scatter(df, x="pc1", y="pc2", color="cluster_label", color_discrete_map=RISK_COLORS, opacity=0.85,
                     custom_data=CUSTOM, category_orders={"cluster_label": RISK_ORDER},
                     labels={"pc1": xl, "pc2": yl, "cluster_label": "Risk cluster"})
    fig.update_traces(marker=dict(size=12), hovertemplate=HOVER_TEMPLATE, selector=dict(mode="markers"))
    row = df[df["STUDENT_ID"] == selected]
    if not row.empty:
        fig.add_trace(go.Scatter(x=row["pc1"], y=row["pc2"], mode="markers", name="Selected student",
                                 marker=dict(size=18, **SELECTED), customdata=row[CUSTOM].to_numpy(),
                                 hovertemplate=HOVER_TEMPLATE, showlegend=False))
    fig.update_layout(title="Student clusters in 2D PCA space", xaxis_title=xl, yaxis_title=yl)
    return _clean(fig, s, 560)


def clusters_3d(clusters: pd.DataFrame, selected: str | None, s: PlotSettings = PlotSettings()) -> go.Figure:
    df, (xl, yl, zl) = _pca(_labelled(clusters), 3)
    fig = go.Figure()
    if _HAS_SCIPY:
        for label in RISK_ORDER:
            pts = df.loc[df["cluster_label"] == label, ["pc1", "pc2", "pc3"]].to_numpy(dtype=float)
            if pts.shape[0] < 4:
                continue
            try:
                hull = ConvexHull(pts, qhull_options="QJ")
            except Exception:
                continue
            i, j, k = hull.simplices.T
            fig.add_trace(go.Mesh3d(x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], i=i, j=j, k=k, color=RISK_COLORS[label],
                                    opacity=0.12, name=f"{label} region", hoverinfo="skip", showscale=False))
    for label in RISK_ORDER:
        sub = df[df["cluster_label"] == label]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter3d(x=sub["pc1"], y=sub["pc2"], z=sub["pc3"], mode="markers", name=label,
                                   marker=dict(size=5, color=RISK_COLORS[label], opacity=0.8),
                                   customdata=sub[CUSTOM].to_numpy(), hovertemplate=HOVER_TEMPLATE))
    row = df[df["STUDENT_ID"] == selected]
    if not row.empty:
        fig.add_trace(go.Scatter3d(x=row["pc1"], y=row["pc2"], z=row["pc3"], mode="markers", name="Selected student",
                                   marker=dict(size=10, **SELECTED), customdata=row[CUSTOM].to_numpy(),
                                   hovertemplate=HOVER_TEMPLATE, showlegend=False))
    cams = {"Front": dict(eye=dict(x=0, y=2.2, z=0), up=dict(x=0, y=0, z=1)),
            "Side": dict(eye=dict(x=2.2, y=0, z=0), up=dict(x=0, y=0, z=1)),
            "Top": dict(eye=dict(x=0, y=0, z=2.2), up=dict(x=0, y=1, z=0)),
            "Reset": dict(eye=dict(x=1.6, y=1.6, z=1.1), up=dict(x=0, y=0, z=1))}
    fig.update_layout(
        title="Student clusters in 3D PCA space (convex hull overlay)", height=640,
        scene=dict(xaxis_title=xl, yaxis_title=yl, zaxis_title=zl, aspectmode="data"),
        scene_camera=cams["Reset"], legend_title="Risk cluster", paper_bgcolor="rgba(0,0,0,0)", hoverlabel=HOVER,
        margin=dict(l=10, r=10, t=50, b=10),
        updatemenus=[dict(type="buttons", direction="left", x=0.5, y=1.06, xanchor="left", yanchor="top",
                          buttons=[dict(label=k, method="relayout", args=[{"scene.camera": v}]) for k, v in cams.items()])],
    )
    return fig


# ---------------------------------------------------------------- cluster insights
def _pct(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ("grade_ratio", "late_rate", "on_time_rate", "missed_rate", "avg_percentage", "std_percentage"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce") * 100.0
    return out


def _highlight(fig, df, selected, x, y, label_fmt):
    row = df[df["STUDENT_ID"] == selected]
    if not row.empty and row[[x, y]].notna().all(axis=None):
        fig.add_trace(go.Scatter(x=row[x], y=row[y], mode="markers", marker=dict(size=12, **SELECTED),
                                 name="Selected student", showlegend=False,
                                 hovertemplate=f"<b>{row['STUDENT_NAME'].iat[0]}</b><br>{label_fmt}<extra></extra>"))


def insight_figure(name: str, clusters: pd.DataFrame, selected: str | None,
                   s: PlotSettings = PlotSettings()) -> go.Figure:
    df = _pct(_labelled(clusters))
    co = {"cluster_label": ["Low", "Medium", "High", "Critical"]}
    common = dict(color="cluster_label", category_orders=co, color_discrete_map=RISK_COLORS)
    labels = {"cluster_label": "Risk cluster", "grade_ratio": "Grade %", "avg_percentage": "Average score %",
              "std_percentage": "Std. score %", "missed_rate": "Missed %", "late_rate": "Late % (timed items)",
              "days_since_last_submission": "Days", "pace": "Submissions per week"}
    if name == "grade_violin":
        fig = px.violin(df, x="cluster_label", y="grade_ratio", box=True, points="outliers", labels=labels,
                        title="Grade distribution by risk cluster", **common)
    elif name == "avg_violin":
        fig = px.violin(df, x="cluster_label", y="avg_percentage", box=True, points="outliers", labels=labels,
                        title="Average assignment score by risk cluster", **common)
    elif name == "missed_late":
        fig = px.scatter(df, x="missed_rate", y="late_rate", labels=labels, hover_name="STUDENT_NAME",
                         title="Missed vs late (behaviour profile)", **common)
        fig.update_traces(marker=dict(size=8), opacity=0.75)
        _highlight(fig, df, selected, "missed_rate", "late_rate", "Missed %{x:.1f}%  Late %{y:.1f}%")
    elif name == "perf_consistency":
        fig = px.scatter(df, x="avg_percentage", y="std_percentage", labels=labels, hover_name="STUDENT_NAME",
                         title="Performance vs consistency", **common)
        fig.update_traces(marker=dict(size=8), opacity=0.75)
        _highlight(fig, df, selected, "avg_percentage", "std_percentage", "Avg %{x:.1f}%  SD %{y:.1f}%")
    elif name == "days_since":
        fig = px.histogram(df, x="days_since_last_submission", barmode="overlay", labels=labels,
                           title="Days since last submission (as of export)", **common)
        fig.update_yaxes(title="Students")
    elif name == "pace":
        d = df.assign(pace=df["pace"].clip(upper=20))
        fig = px.box(d, x="cluster_label", y="pace", labels=labels, title="Pace by cluster", **common)
    else:
        raise KeyError(name)
    fig.update_layout(showlegend=name in {"missed_late", "perf_consistency", "days_since"})
    return _clean(fig, s, 360)


INSIGHT_FIGURES = ["grade_violin", "avg_violin", "missed_late", "perf_consistency", "days_since", "pace"]


# ---------------------------------------------------------------- admissions context
def admissions_numeric(df: pd.DataFrame, metric: str, label: str, selected: str | None,
                       s: PlotSettings = PlotSettings()) -> tuple[go.Figure, go.Figure]:
    d = _pct(_labelled(df))
    d[metric] = pd.to_numeric(d[metric], errors="coerce")
    if metric != "CMPAC":
        d = d[d[metric].notna() & (d[metric] != 0)]
    co = {"cluster_label": ["Low", "Medium", "High", "Critical"]}
    box = px.box(d, x="cluster_label", y=metric, color="cluster_label", category_orders=co,
                 color_discrete_map=RISK_COLORS, labels={"cluster_label": "Risk cluster", metric: label},
                 title=f"{label} by risk cluster")
    box.update_layout(showlegend=False)
    sc = px.scatter(d, x=metric, y="grade_ratio", color="cluster_label", category_orders=co,
                    color_discrete_map=RISK_COLORS, hover_name="STUDENT_NAME",
                    labels={"grade_ratio": "Grade %", metric: label, "cluster_label": "Risk cluster"},
                    title=f"{label} vs grade")
    fit = d[[metric, "grade_ratio"]].dropna()
    if len(fit) >= 3 and fit[metric].nunique() > 1:
        m, b = np.polyfit(fit[metric], fit["grade_ratio"], 1)
        xs = np.linspace(fit[metric].min(), fit[metric].max(), 50)
        sc.add_trace(go.Scatter(x=xs, y=m * xs + b, mode="lines", name="Linear fit",
                                line=dict(color="#334155", dash="dot")))
    _highlight(sc, d, selected, metric, "grade_ratio", f"{label} %{{x}}  Grade %{{y:.1f}}%")
    return _clean(box, s, 360), _clean(sc, s, 360)


def admissions_yesno(df: pd.DataFrame, fields: dict[str, str], s: PlotSettings = PlotSettings()) -> go.Figure:
    d = _labelled(df)
    rows = []
    for cl, g in d.groupby("cluster_label"):
        for f, lab in fields.items():
            if f in g:
                yes = g[f].fillna("").astype(str).str.strip().str.upper().isin({"YES", "Y", "TRUE", "T", "1"})
                rows.append({"cluster_label": cl, "flag": lab, "pct_yes": float(yes.mean() * 100)})
    comp = pd.DataFrame(rows)
    fig = px.bar(comp, x="cluster_label", y="pct_yes", color="flag", barmode="group",
                 category_orders={"cluster_label": ["Low", "Medium", "High", "Critical"]},
                 labels={"cluster_label": "Risk cluster", "pct_yes": "% Yes", "flag": "Flag"},
                 title="Percent 'Yes' by cluster (admissions flags)")
    return _clean(fig, s, 380)


def admissions_category(df: pd.DataFrame, field: str, label: str, stacked: bool,
                        s: PlotSettings = PlotSettings()) -> go.Figure:
    d = _labelled(df)
    d[field] = d[field].fillna("").astype(str).str.strip().replace({"": "None", "nan": "None"})
    counts = d.groupby(["cluster_label", field]).size().reset_index(name="count")
    co = {"cluster_label": ["Low", "Medium", "High", "Critical"]}
    if stacked:
        counts["pct"] = counts["count"] / counts.groupby("cluster_label")["count"].transform("sum") * 100
        fig = px.bar(counts, x="cluster_label", y="pct", color=field, barmode="stack", category_orders=co,
                     labels={"cluster_label": "Risk cluster", "pct": "% of cluster", field: label},
                     title=f"{label} distribution within each risk cluster")
    else:
        fig = px.bar(counts, x="cluster_label", y="count", color=field, barmode="group", category_orders=co,
                     labels={"cluster_label": "Risk cluster", "count": "Students", field: label},
                     title=f"{label} counts by risk cluster")
    return _clean(fig, s, 380)


# ---------------------------------------------------------------- course director
def section_colors(names: list[str]) -> dict[str, str]:
    pal = px.colors.qualitative.Plotly
    return {n: pal[i % len(pal)] for i, n in enumerate(sorted(names))}


def section_metric_bar(ranked: pd.DataFrame, metric: str, label: str, s: PlotSettings = PlotSettings()) -> go.Figure:
    d = ranked.dropna(subset=[metric])
    order = d["SECTION_NAME"].tolist()          # already in rank order (best first)
    fig = px.bar(d, x="SECTION_NAME", y=metric, color="SECTION_NAME",
                 color_discrete_map=section_colors(ranked["SECTION_NAME"].tolist()),
                 category_orders={"SECTION_NAME": order}, text=metric,
                 labels={"SECTION_NAME": "Section", metric: label})
    fig.update_traces(texttemplate="%{text:.2f}", textposition="outside", cliponaxis=False)
    fig.update_layout(showlegend=False, xaxis_title="Section (best first)", yaxis_title=label)
    return _clean(fig, s, 360)


def section_grade_box(roster: pd.DataFrame, s: PlotSettings = PlotSettings()) -> go.Figure:
    d = roster[["SECTION_NAME", "GRADE_PCT"]].dropna()
    names = sorted(d["SECTION_NAME"].unique().tolist())
    fig = px.box(d, x="SECTION_NAME", y="GRADE_PCT", color="SECTION_NAME", points="all",
                 color_discrete_map=section_colors(names), category_orders={"SECTION_NAME": names},
                 labels={"SECTION_NAME": "Section", "GRADE_PCT": "Grade (%)"})
    fig.update_traces(jitter=0.35, marker=dict(size=5, opacity=0.6))
    fig.update_layout(showlegend=False)
    fig.update_yaxes(range=[0, 100])
    if s.show_cutoff:
        fig.add_hline(y=s.cutoff_percentage, line_dash="dash", line_color="red",
                      annotation_text=f"{s.cutoff_percentage:.0f}% cutoff", annotation_position="top left")
    return _clean(fig, s, 360)


# ---------------------------------------------------------------- zyBooks
def zybooks_combo(df: pd.DataFrame, s: PlotSettings = PlotSettings()) -> go.Figure:
    """One student: scores per chapter (lines, left axis) vs minutes (bars, right axis)."""
    chapters = df["CHAPTER"].astype(str).tolist()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=chapters, y=df["Minutes"], name="Time spent (mins)", opacity=0.3,
                         marker=dict(color="#8c8c8c"), yaxis="y2",
                         hovertemplate="Chapter: %{x}<br>Minutes: %{y:.0f}<extra></extra>"))
    for col in ("Participation", "zyLab"):
        if col in df:
            fig.add_trace(go.Scatter(x=chapters, y=df[col], name=col, mode="lines+markers", line=dict(width=3),
                                     hovertemplate=f"Chapter: %{{x}}<br>{col}: %{{y:.1f}}%<extra></extra>"))
    fig.update_layout(
        title="Effort (time) vs mastery (scores)", barmode="overlay",
        xaxis=dict(title="Chapter", type="category", categoryorder="array", categoryarray=chapters),
        yaxis=dict(title="Score (%)", range=[0, 105]),
        yaxis2=dict(title="Time spent (mins)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return _clean(fig, s, 380)


def zybooks_matrix(df: pd.DataFrame, names: dict[str, str], s: PlotSettings = PlotSettings()) -> go.Figure:
    """Class matrix: chapter x student, colour = zyLab %, size = minutes. Lowest lab average on top."""
    d = df.copy()
    d["Student"] = d["STUDENT_ID"].map(names).fillna(d["STUDENT_ID"]).astype(str)
    order = d.groupby("Student")["zyLab"].mean().sort_values(ascending=False).index.tolist()
    chapters = (d.groupby("CHAPTER")["SortOrder"].min().sort_values().index.astype(str).tolist())
    d["CHAPTER"] = d["CHAPTER"].astype(str)
    fig = px.scatter(d, x="CHAPTER", y="Student", color="zyLab", size="Minutes", size_max=20 if len(order) < 100 else 40,
                     category_orders={"CHAPTER": chapters, "Student": order}, color_continuous_scale="RdYlGn",
                     range_color=(0, 100), hover_data={"zyLab": ":.1f", "Minutes": ":.0f", "Participation": ":.1f"},
                     title="Class matrix (size = time, colour = lab score)")
    h = max(420, min(12000, len(order) * 18))
    fig.update_traces(marker=dict(opacity=0.6, line=dict(width=0)))
    fig.update_layout(xaxis_title=None, yaxis_title=None, coloraxis_colorbar=dict(title="Lab %", thickness=12),
                      margin=dict(l=180, r=30, t=90, b=30))
    fig.update_xaxes(side="top")
    fig.update_yaxes(categoryorder="array", categoryarray=order, range=[-0.5, len(order) - 0.5])
    return _clean(fig, s, h)

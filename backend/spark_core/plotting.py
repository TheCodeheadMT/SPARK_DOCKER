"""Ported from SPARK utils/plotting.py (student performance figures).

Pattern for every plotting port:
  * No Streamlit: functions RETURN a go.Figure; the API serialises it with
    fig.to_json() and the browser renders it with plotly.js.
  * Settings that used to come from st.session_state (cutoff line, smoothing,
    light/dark theme) are explicit arguments.

Cluster/PCA, section, zyBooks and admissions figures follow in Phase 4.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go

MAX_PERCENT = 110
BIN_ORDER = ["0–9 pts", "10-20 pts", "21–49 pts", "50+ pts", "Unknown"]
POINT_BIN_SIZE_MAP = {"0–9 pts": 8, "10-20 pts": 10, "21–49 pts": 15, "50+ pts": 20, "Unknown": 10}
POINT_BIN_SYMBOL_MAP = {"0–9 pts": "circle", "10-20 pts": "square", "21–49 pts": "diamond",
                        "50+ pts": "triangle-up", "Unknown": "triangle-down"}
POINT_BIN_COLOR_MAP = {"0–9 pts": "#60A5FA", "10-20 pts": "#D4F63B", "21–49 pts": "#D97CEE",
                       "50+ pts": "#EC3459", "Unknown": "#6B7280"}
HOVER_FONT = dict(family="Courier New, monospace", size=12)


@dataclass(frozen=True)
class PlotSettings:
    cutoff_percentage: float = 60.0
    show_cutoff: bool = True
    smooth_curve: bool = False
    theme: str = "light"            # "light" | "dark"

    def colors(self) -> tuple[str, str, str, str]:
        if self.theme == "dark":
            return ("rgba(255,255,255,0.85)", "rgba(255,255,255,0.10)",
                    "rgba(255,255,255,0.65)", "rgba(255,255,255,0.25)")
        return ("rgba(17,24,39,0.85)", "rgba(0,0,0,0.10)", "rgba(0,0,0,0.55)", "rgba(0,0,0,0.25)")


def bin_points_possible(pts) -> str:
    if pd.isna(pts):
        return "Unknown"
    if 0 <= pts <= 9:
        return "0–9 pts"
    if 10 <= pts <= 20:
        return "10-20 pts"
    if 21 <= pts <= 49:
        return "21–49 pts"
    return "50+ pts"


def _style(fig: go.Figure, s: PlotSettings, x_title: str, y_title: str) -> go.Figure:
    _, grid, axis, _ = s.colors()
    fig.update_layout(
        xaxis_title=x_title, yaxis_title=y_title, yaxis=dict(range=[0, MAX_PERCENT]),
        margin=dict(l=40, r=20, t=30, b=40), hovermode="x unified",
        hoverlabel=dict(align="left", font=HOVER_FONT),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.2),
    )
    fig.update_xaxes(showgrid=True, gridcolor=grid, linecolor=axis, tickfont=dict(color=axis))
    fig.update_yaxes(showgrid=True, gridcolor=grid, linecolor=axis, tickfont=dict(color=axis))
    if s.show_cutoff:
        fig.add_hline(y=s.cutoff_percentage, line_dash="dash", line_color="red",
                      annotation_text=f"{s.cutoff_percentage:.0f}% cutoff", annotation_position="top left")
    return fig


def _event_date(perf: pd.DataFrame) -> pd.Series:
    """X position: submission time, else due date, else graded time.

    Blackboard items graded by override have no submission time; plotting them at
    their due date keeps the cumulative line in course order.
    """
    return perf["SUBMITTED_AT"].fillna(perf["DUE_AT"]).fillna(perf["GRADED_AT"])


def cumulative_grade_figure(perf: pd.DataFrame, s: PlotSettings = PlotSettings()) -> go.Figure:
    """perf = STUDENT_PERFORMANCE rows for one student."""
    df = perf.copy()
    df["Date"] = _event_date(df)
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df["Cum Earned"] = df["POINTS_EARNED"].cumsum()
    df["Cum Possible"] = df["POINTS_POSSIBLE"].cumsum()
    df["Cumulative Percent"] = (df["Cum Earned"] / df["Cum Possible"].where(df["Cum Possible"] > 0)) * 100
    df["Grade"] = df["PERCENTAGE"] * 100
    df["Points Bin"] = df["POINTS_POSSIBLE"].apply(bin_points_possible)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["Date"], y=df["Cumulative Percent"], mode="lines", name="Cumulative Grade",
                             line=dict(shape="spline" if s.smooth_curve else "linear"), hoverinfo="skip"))
    for b in BIN_ORDER:
        sub = df[df["Points Bin"] == b]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["Date"], y=sub["Cumulative Percent"], mode="markers", name=b, legendgroup=f"bin_{b}",
            marker=dict(size=POINT_BIN_SIZE_MAP[b], symbol=POINT_BIN_SYMBOL_MAP[b], color=POINT_BIN_COLOR_MAP[b],
                        line=dict(width=0.4, color="black")),
            customdata=sub[["ASSIGNMENT", "POINTS_EARNED", "POINTS_POSSIBLE", "Cum Earned", "Cum Possible", "Grade"]],
            hovertemplate=("<b>%{customdata[0]}</b><br><b>Date</b>:       %{x|%Y-%m-%d}<br>"
                           "<b>Grade</b>:      %{customdata[1]:.2f} / %{customdata[2]:.2f} (%{customdata[5]:.1f}%)<br>"
                           "<b>Cum Grade</b>:  %{customdata[3]:.2f} / %{customdata[4]:.2f} (%{y:.1f}%)<extra></extra>"),
        ))
    return _style(fig, s, "Date (submitted, else due)", "Cumulative Grade (%)")


def assignment_scores_figure(perf: pd.DataFrame, s: PlotSettings = PlotSettings()) -> go.Figure:
    """Lollipop chart: per-item score, stems for items under the cutoff, late markers."""
    df = perf.copy()
    df["Date"] = _event_date(df)
    df["Pct"] = df["PERCENTAGE"] * 100
    df["Points Bin"] = df["POINTS_POSSIBLE"].apply(bin_points_possible)
    df["Late_Display"] = df["LATE"].map(lambda v: "Yes" if bool(v) else "No")
    df.loc[df["SUBMITTED_AT"].isna(), "Late_Display"] = "n/a (no submit time)"
    df["Due_Display"] = df["DUE_AT"].dt.strftime("%Y-%m-%d").fillna("None")
    df["Submitted_Display"] = df["SUBMITTED_AT"].dt.strftime("%Y-%m-%d %H:%M").fillna("None")
    df = df.dropna(subset=["Date"]).sort_values("Date")

    fig = go.Figure()
    for b in BIN_ORDER:
        sub = df[df["Points Bin"] == b]
        if sub.empty:
            continue
        xs, ys = [], []
        for _, r in sub.iterrows():
            if pd.notna(r["Pct"]) and r["Pct"] < s.cutoff_percentage:
                xs += [r["Date"], r["Date"], None]
                ys += [0, r["Pct"], None]
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=POINT_BIN_COLOR_MAP[b], width=2),
                                     showlegend=False, hoverinfo="skip", legendgroup=f"bin_{b}"))
        fig.add_trace(go.Scatter(
            x=sub["Date"], y=sub["Pct"], mode="markers", name=b, legendgroup=f"bin_{b}",
            marker=dict(size=POINT_BIN_SIZE_MAP[b], symbol=POINT_BIN_SYMBOL_MAP[b], color=POINT_BIN_COLOR_MAP[b],
                        line=dict(width=0.4, color="black")),
            customdata=sub[["ASSIGNMENT", "POINTS_EARNED", "POINTS_POSSIBLE", "Pct", "Late_Display",
                            "Due_Display", "Submitted_Display"]],
            hovertemplate=("<b>%{customdata[0]}</b><br>"
                           "<b>Score</b>:      %{customdata[1]:.2f} / %{customdata[2]:.2f} (%{customdata[3]:.1f}%)<br>"
                           "<b>Submitted</b>:  %{customdata[6]}<br><b>Late?</b>:      %{customdata[4]}<br>"
                           "<b>Due</b>:        %{customdata[5]}<extra></extra>"),
        ))
    late = df[df["Late_Display"] == "Yes"]
    if not late.empty:
        fig.add_trace(go.Scatter(x=late["Date"], y=late["Pct"], mode="markers", name="Late Submission",
                                 marker=dict(opacity=0.6, symbol="x", size=10, color="#FF0000"), hoverinfo="skip"))
    return _style(fig, s, "Date (submitted, else due)", "Assignment Score (%)")


FIGURES = {
    "cumulative": cumulative_grade_figure,
    "scores": assignment_scores_figure,
}

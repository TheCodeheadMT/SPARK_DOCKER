"""Ported from SPARK ml/cluster_students.py.

Same algorithm (StandardScaler + KMeans k=4, random_state=42, or GMM) on the same six
features, and the same risk ordering (clusters sorted by mean grade_ratio, lowest =
Critical). Differences:
  * Runs once per ingest; results are stored in the course DB as STUDENT_CLUSTERS, so
    every user sees the same labels and page views cost nothing. No pickles per course.
  * A student's risk label is a lookup, not a second model.predict() call.
  * Returns None (with a reason) instead of raising when a course is too small.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

FEATURE_COLS = ["grade_ratio", "avg_percentage", "std_percentage", "late_rate", "on_time_rate", "missed_rate"]
RISK_ORDER = ["Critical", "High", "Medium", "Low"]
RISK_COLORS = {"Critical": "#DC2626", "High": "#F97316", "Medium": "#FBBF24", "Low": "#10B981"}


def risk_labels(n_clusters: int) -> list[str]:
    if n_clusters == 4:
        return RISK_ORDER
    if n_clusters == 3:
        return ["High", "Medium", "Low"]
    raise ValueError("Only 3 or 4 clusters are supported.")


def train_clusters(features: pd.DataFrame, n_clusters: int = 4, mode: str = "kmeans",
                   seed: int = 42) -> tuple[pd.DataFrame | None, str]:
    """Return (features + cluster + cluster_label + risk_rank, note)."""
    df = features.copy()
    X = df[FEATURE_COLS].astype(float)
    valid = X.notna().all(axis=1)
    if valid.sum() < max(n_clusters * 2, 8):
        return None, f"Not enough students with grades to form {n_clusters} risk groups (need {max(n_clusters * 2, 8)})."
    if X[valid].drop_duplicates().shape[0] < n_clusters:
        return None, "Students are too similar to separate into risk groups yet."

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[valid])
    if mode == "gmm":
        model = GaussianMixture(n_components=n_clusters, covariance_type="full", random_state=seed)
        labels = model.fit_predict(Xs)
    else:
        model = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
        labels = model.fit_predict(Xs)

    df["cluster"] = pd.Series(np.nan, index=df.index)
    df.loc[valid, "cluster"] = labels
    order = df[valid].groupby("cluster")["grade_ratio"].mean().sort_values().index.tolist()
    names = risk_labels(n_clusters)
    label_of = {int(c): names[i] for i, c in enumerate(order)}
    df["cluster"] = df["cluster"].astype("Int64")
    df["cluster_label"] = df["cluster"].map(lambda c: label_of.get(int(c)) if pd.notna(c) else None)
    df["risk_rank"] = df["cluster_label"].map({n: i for i, n in enumerate(names)})
    return df, f"{mode} k={n_clusters} on {int(valid.sum())} students"


def cluster_summary(clusters: pd.DataFrame) -> pd.DataFrame:
    g = clusters.dropna(subset=["cluster_label"]).groupby("cluster_label")
    out = g.agg(students=("STUDENT_ID", "count"), **{f"mean_{c}": (c, "mean") for c in FEATURE_COLS}).reset_index()
    out["order"] = out["cluster_label"].map({n: i for i, n in enumerate(RISK_ORDER)})
    return out.sort_values("order").drop(columns="order")

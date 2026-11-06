"""Ported from SPARK ml/success_model.py.

Kept: the same three classifiers and hyper-parameters (HistGradientBoosting / RandomForest /
ExtraTrees behind a median imputer), the same feature list, the same label
(CURRENT_SCORE, else grade_ratio * 100, compared to a success threshold), the
"both classes required" guard and its messages, and cross-course reuse (aliases).

Fixed / added:
  * Progress features (progress_assignments_pct, progress_points_pct) are computed the
    same way at training AND prediction time. The Streamlit predict step skipped them,
    so every prediction silently imputed the median for both.
  * Cross-validated accuracy and ROC AUC are reported for every training run (stratified
    k-fold, k = min(5, minority class size)). Training-set fit alone overstates quality.
  * Works on DataFrames (no DB paths, no Streamlit); the API decides where artefacts live.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import Pipeline

ClassifierType = Literal["hgb", "rf", "extra_trees"]
CLASSIFIER_LABELS = {"hgb": "Gradient-Boosted Trees (recommended)", "rf": "Random Forest", "extra_trees": "Extra Trees"}

BASE_FEATURES = [
    "late_rate", "missed_rate", "on_time_rate", "avg_percentage", "std_percentage", "num_assignments", "pace",
    "weeks_active", "days_since_last_submission", "mean_submission_delta_days", "has_due_dates",
    "progress_assignments_pct", "progress_points_pct",
]


@dataclass
class SuccessModelConfig:
    success_threshold: float = 72.0
    include_grade_features: bool = False
    classifier: ClassifierType = "hgb"

    def feature_cols(self) -> list[str]:
        return (["grade_ratio"] if self.include_grade_features else []) + BASE_FEATURES


class TrainingError(ValueError):
    pass


def build_estimator(cfg: SuccessModelConfig) -> Pipeline:
    if cfg.classifier == "hgb":
        clf = HistGradientBoostingClassifier(max_depth=6, learning_rate=0.08, max_iter=300, random_state=42)
    elif cfg.classifier == "rf":
        clf = RandomForestClassifier(n_estimators=400, min_samples_leaf=2, class_weight="balanced",
                                     random_state=42, n_jobs=-1)
    elif cfg.classifier == "extra_trees":
        clf = ExtraTreesClassifier(n_estimators=600, min_samples_leaf=2, class_weight="balanced",
                                   random_state=42, n_jobs=-1)
    else:
        raise ValueError(f"Unsupported classifier: {cfg.classifier}")
    return Pipeline([("imputer", SimpleImputer(strategy="median")), ("clf", clf)])


def course_totals(perf: pd.DataFrame) -> dict[str, float]:
    """Course-level denominators: distinct items, and sum of max points per item."""
    if perf.empty:
        return {"total_assignments": 0.0, "course_points_possible": 0.0}
    per_item = perf.groupby("ASSIGNMENT_ID")["POINTS_POSSIBLE"].max()
    return {"total_assignments": float(per_item.size), "course_points_possible": float(per_item.sum())}


def add_progress_features(feats: pd.DataFrame, totals: dict[str, float]) -> pd.DataFrame:
    df = feats.copy()
    ta, cp = totals["total_assignments"], totals["course_points_possible"]
    df["progress_assignments_pct"] = (df["num_assignments"] / ta * 100.0) if ta > 0 else np.nan
    df["progress_points_pct"] = (df["total_possible"] / cp * 100.0) if cp > 0 else np.nan
    return df


def training_frame(feats: pd.DataFrame, grades: pd.DataFrame, totals: dict[str, float],
                   cfg: SuccessModelConfig) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """grades: STUDENT_ID, CURRENT_SCORE (latest per student)."""
    df = add_progress_features(feats, totals).merge(grades, on="STUDENT_ID", how="left")
    df["CURRENT_SCORE"] = pd.to_numeric(df["CURRENT_SCORE"], errors="coerce")
    label_score = df["CURRENT_SCORE"].fillna(df["grade_ratio"] * 100.0)
    y = (label_score >= float(cfg.success_threshold)).astype(int)
    X = df[cfg.feature_cols()].astype(float).replace([np.inf, -np.inf], np.nan)
    keep = X.notna().any(axis=1) & label_score.notna()
    return X[keep], y[keep], label_score[keep]


def _class_guard(y: pd.Series, label_score: pd.Series, thr: float) -> None:
    classes = sorted(pd.unique(y))
    if len(classes) >= 2:
        return
    n = len(y)
    stats = (f"Observed scores (used for labeling): min={label_score.min():.1f}%, "
             f"median={label_score.median():.1f}%, max={label_score.max():.1f}%.") if n else ""
    if classes and int(classes[0]) == 1:
        raise TrainingError(f"Cannot train: all {n} students are labeled SUCCESS at threshold {thr:.1f}%. {stats} "
                            "Increase the threshold (or wait until grades diversify).")
    raise TrainingError(f"Cannot train: all {n} students are labeled NOT SUCCESS at threshold {thr:.1f}%. {stats} "
                        "Decrease the threshold (or wait until grades diversify).")


def cross_validate(model: Pipeline, X: pd.DataFrame, y: pd.Series) -> dict:
    minority = int(y.value_counts().min())
    if minority < 2:
        return {"folds": 0, "note": f"Only {minority} student(s) in the smaller class; cross-validation not possible. "
                                    "Treat this model as unvalidated."}
    k = min(5, minority)
    cv = StratifiedKFold(n_splits=k, shuffle=True, random_state=42)
    proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")[:, 1]
    out = {"folds": k, "accuracy": float(accuracy_score(y, (proba >= 0.5).astype(int))),
           "roc_auc": float(roc_auc_score(y, proba)), "baseline_accuracy": float(max(y.mean(), 1 - y.mean()))}
    if minority < 5:
        out["note"] = f"Only {minority} students in the smaller class; metrics are unstable."
    return out


def train(feats: pd.DataFrame, grades: pd.DataFrame, totals: dict[str, float], cfg: SuccessModelConfig) -> dict:
    X, y, label_score = training_frame(feats, grades, totals, cfg)
    _class_guard(y, label_score, cfg.success_threshold)
    model = build_estimator(cfg)
    metrics = cross_validate(build_estimator(cfg), X, y)
    model.fit(X, y)
    return {
        "payload": {"model": model, "feature_cols": cfg.feature_cols(), "config": asdict(cfg)},
        "n_students": int(len(X)),
        "n_success": int(y.sum()),
        "success_rate": float(y.mean()),
        "metrics": metrics,
    }


def predict(payload: dict, feats: pd.DataFrame, totals: dict[str, float]) -> pd.Series:
    """P(success) per STUDENT_ID for every row in feats (target course's features + totals)."""
    X = add_progress_features(feats, totals).reindex(columns=payload["feature_cols"]).astype(float)
    X = X.replace([np.inf, -np.inf], np.nan)
    proba = payload["model"].predict_proba(X)[:, 1]
    return pd.Series(proba, index=feats["STUDENT_ID"].values, name="success_prob")


def feature_importance(payload: dict) -> list[dict] | None:
    clf = payload["model"].named_steps["clf"]
    imp = getattr(clf, "feature_importances_", None)
    if imp is None:
        return None
    pairs = sorted(zip(payload["feature_cols"], imp), key=lambda t: -t[1])
    return [{"feature": f, "importance": float(v)} for f, v in pairs]

"""Metrics use incident-type/source-identity pairs, never guessed natural labels."""
from app.analytics.health import TYPES

def score(expected, predicted):
    expected, predicted = set(expected), set(predicted)
    tp, fp, fn = len(expected & predicted), len(predicted-expected), len(expected-predicted)
    return {"true_positives":tp,"false_positives":fp,"false_negatives":fn,
        "precision":tp/(tp+fp) if tp+fp else None,
        "recall":tp/(tp+fn) if tp+fn else None,
        "f1":2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
        "undefined_reasons":{"precision":None if tp+fp else "NO_POSITIVE_PREDICTIONS",
            "recall":None if tp+fn else "NO_POSITIVE_GROUND_TRUTH",
            "f1":None if 2*tp+fp+fn else "NO_POSITIVE_LABELS_OR_PREDICTIONS"}}

def metrics(expected, predicted):
    return {**score(expected,predicted),"per_type":{kind:score([r for r in expected if r[0]==kind],[r for r in predicted if r[0]==kind]) for kind in TYPES},
        "unit":"incident_type + source_identity per isolated case","production_accuracy_claim":False}

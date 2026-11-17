"""Experimental, offline object classifier with image-held-out promotion checks.

This classifier changes acceptance suggestions, never segmentation or geometry.
Only explicit human reviews are labels. Model files contain numeric JSON, not code.
"""
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

FEATURE_NAMES = ('log_area', 'log_aspect', 'occupancy', 'elongation', 'contrast',
                 'intensity_sd', 'log_length', 'log_diameter', 'has_length', 'has_diameter',
                 'crossing_merge', 'border_touch', 'excluded_region')
FEATURE_SCHEMA = 1
MIN_CLASS_EXAMPLES = 5
MIN_IMAGES_PER_ROLE = 2


def object_features(mask, gray, measurement):
    yy, xx = np.nonzero(mask)
    if not len(yy):
        raise ValueError('A training object must contain foreground pixels.')
    points = np.column_stack((yy, xx)).astype(float)
    variance = np.linalg.eigvalsh(np.cov(points, rowvar=False, bias=True)) if len(points) > 1 else np.zeros(2)
    inside, outside = gray[mask], gray[~mask]

    def length(name):
        value = measurement.get(name)
        return float(value) if value is not None and np.isfinite(value) and value > 0 else 0.

    return [float(v) for v in (
        np.log1p(len(yy)), np.log(max(mask.shape) / min(mask.shape)), len(yy) / mask.size,
        np.log1p(np.sqrt((variance[-1] + .25) / (max(variance[0], 0) + .25))),
        inside.mean() - (outside.mean() if outside.size else inside.mean()), inside.std(),
        np.log1p(length('length_px')), np.log1p(length('mean_diameter_px')),
        float(length('length_px') > 0), float(length('mean_diameter_px') > 0),
        bool(measurement['crossing_merge']), bool(measurement['border_touch']),
        bool(measurement['excluded_region']))]


def validate_model(payload):
    if payload.get('schema') != FEATURE_SCHEMA or payload.get('feature_names') != list(FEATURE_NAMES):
        raise ValueError('The saved model uses an unsupported feature schema.')
    n = len(FEATURE_NAMES)
    for name in ('mean', 'scale', 'weights'):
        values = np.asarray(payload[name], float)
        if values.shape != (n,) or not np.isfinite(values).all():
            raise ValueError('The saved model has invalid numeric parameters.')
    if np.any(np.asarray(payload['scale']) <= 0) or not np.isfinite(payload['intercept']):
        raise ValueError('The saved model has invalid scaling or intercept.')


def predict(payload, features):
    validate_model(payload)
    x = np.asarray(features, float).reshape(-1, len(FEATURE_NAMES))
    if not np.isfinite(x).all():
        raise ValueError('Classifier features must be finite.')
    return expit(np.clip((x - payload['mean']) / payload['scale'], -20, 20) @ np.asarray(payload['weights'])
                 + payload['intercept']) >= .5


def fit_classifier(samples):
    x = np.array([s['features'] for s in samples], float)
    y = np.array([s['label'] == 'accepted' for s in samples], float)
    if x.shape != (len(samples), len(FEATURE_NAMES)) or not np.isfinite(x).all() or len(np.unique(y)) != 2:
        raise ValueError('Training requires finite features and both reviewed classes.')
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    x = np.clip((x - mean) / scale, -20, 20)
    # Images contribute equally within each class; hundreds of fragments from
    # one image must not overwhelm the other reviewed images.
    image_class_counts = {}
    for s in samples:
        key = (s['image_key'], s['label'])
        image_class_counts[key] = image_class_counts.get(key, 0) + 1
    weights = np.array([1 / image_class_counts[(s['image_key'], s['label'])] for s in samples])
    for label in (0, 1):
        selected = y == label
        weights[selected] *= .5 / weights[selected].sum()

    def objective(parameters):
        w, intercept = parameters[:-1], parameters[-1]
        logits = x @ w + intercept
        residual = weights * (expit(logits) - y)
        loss = np.sum(weights * (np.logaddexp(0, logits) - y * logits)) + .05 * (w @ w)
        return loss, np.r_[x.T @ residual + .1 * w, residual.sum()]

    result = minimize(objective, np.zeros(x.shape[1] + 1), jac=True, method='L-BFGS-B',
                      options={'maxiter': 500, 'ftol': 1e-10})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('Classifier fitting did not converge; the previous model is unchanged.')
    return dict(schema=FEATURE_SCHEMA, feature_names=list(FEATURE_NAMES), mean=mean.tolist(),
                scale=scale.tolist(), weights=result.x[:-1].tolist(), intercept=float(result.x[-1]),
                algorithm='image/class-weighted L2 logistic regression', threshold=.5)


def classification_metrics(labels, decisions):
    y, p = np.asarray(labels, bool), np.asarray(decisions, bool)
    if y.shape != p.shape or y.ndim != 1 or not y.any() or y.all():
        raise ValueError('Evaluation requires both reviewed classes and matching predictions.')
    tp, tn = int((y & p).sum()), int((~y & ~p).sum())
    fp, fn = int((~y & p).sum()), int((y & ~p).sum())
    recall, specificity = tp / (tp + fn), tn / (tn + fp)
    return dict(examples=len(y), true_accepted=tp, true_rejected=tn, false_accepted=fp,
                false_rejected=fn, balanced_accuracy=(recall + specificity) / 2,
                accepted_precision=tp / (tp + fp) if tp + fp else 0.,
                accepted_recall=recall, rejected_recall=specificity)


def validate_samples(samples):
    for role in ('training', 'evaluation'):
        selected = [s for s in samples if s['role'] == role]
        if len({s['image_key'] for s in selected}) < MIN_IMAGES_PER_ROLE:
            raise ValueError('Review at least two training images and two separate evaluation images before retraining.')
        for label in ('accepted', 'rejected'):
            if sum(s['label'] == label for s in selected) < MIN_CLASS_EXAMPLES:
                raise ValueError('Each dataset role needs at least five explicitly reviewed accepted and five rejected examples.')
    training = {s['image_key'] for s in samples if s['role'] == 'training'}
    evaluation = {s['image_key'] for s in samples if s['role'] == 'evaluation'}
    if training & evaluation:
        raise ValueError('An image cannot be used for both fitting and evaluation.')


def retrain(store):
    revision, samples = store.training_snapshot()
    validate_samples(samples)
    training = [s for s in samples if s['role'] == 'training']
    evaluation = [s for s in samples if s['role'] == 'evaluation']
    payload = fit_classifier(training)  # Evaluation features/labels never enter fitting/scaling.
    x = [s['features'] for s in evaluation]
    labels = [s['label'] == 'accepted' for s in evaluation]
    baseline = [s['baseline'] == 'accepted' for s in evaluation]
    active = store.active_model()
    incumbent = predict(active['payload'], x) if active else baseline
    candidate_metrics = classification_metrics(labels, predict(payload, x))
    incumbent_metrics = classification_metrics(labels, incumbent)
    baseline_metrics = classification_metrics(labels, baseline)
    approved = (candidate_metrics['balanced_accuracy'] >= max(incumbent_metrics['balanced_accuracy'],
                                                            baseline_metrics['balanced_accuracy']) + .01
                and candidate_metrics['accepted_precision'] >= max(incumbent_metrics['accepted_precision'],
                                                                   baseline_metrics['accepted_precision']))
    report = dict(promoted=approved, incumbent_model=active['model_id'] if active else 'baseline',
                  training_examples=len(training), evaluation_examples=len(evaluation),
                  training_images=sorted({s['image_key'] for s in training}),
                  evaluation_images=sorted({s['image_key'] for s in evaluation}),
                  candidate=candidate_metrics, incumbent=incumbent_metrics, baseline=baseline_metrics,
                  reason=('Passed: balanced accuracy improved by at least 1 percentage point without lower accepted precision.'
                          if approved else 'Not promoted: improvement and accepted-precision safeguards were not both met.'),
                  scope='Experimental classification agreement on reviewed objects; not segmentation recall or scientific validation.')
    return store.save_model(payload, report, samples, revision, approved)

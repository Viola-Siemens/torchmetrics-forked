# Copyright The Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from functools import partial

import numpy as np
import pytest
import torch
from scipy.special import expit as sigmoid
from sklearn.metrics import confusion_matrix as sk_confusion_matrix
from sklearn.metrics import precision_score as sk_precision_score
from sklearn.metrics import recall_score as sk_recall_score
from torch import Tensor, tensor
from torchmetrics.classification.precision_recall import (
    BinaryPrecision,
    BinaryRecall,
    MulticlassPrecision,
    MulticlassRecall,
    MultilabelPrecision,
    MultilabelRecall,
    Precision,
    Recall,
)
from torchmetrics.functional.classification.precision_recall import (
    binary_precision,
    binary_recall,
    multiclass_precision,
    multiclass_recall,
    multilabel_precision,
    multilabel_recall,
)
from torchmetrics.metric import Metric

from unittests import NUM_CLASSES, THRESHOLD
from unittests.classification.inputs import _binary_cases, _multiclass_cases, _multilabel_cases
from unittests.helpers import seed_all
from unittests.helpers.testers import MetricTester, inject_ignore_index, remove_ignore_index

seed_all(42)


def _sklearn_precision_recall_binary(preds, target, sk_fn, ignore_index, multidim_average):
    if multidim_average == "global":
        preds = preds.view(-1).numpy()
        target = target.view(-1).numpy()
    else:
        preds = preds.numpy()
        target = target.numpy()

    if np.issubdtype(preds.dtype, np.floating):
        if not ((preds > 0) & (preds < 1)).all():
            preds = sigmoid(preds)
        preds = (preds >= THRESHOLD).astype(np.uint8)

    if multidim_average == "global":
        target, preds = remove_ignore_index(target, preds, ignore_index)
        return sk_fn(target, preds)

    res = []
    for pred, true in zip(preds, target):
        pred = pred.flatten()
        true = true.flatten()
        true, pred = remove_ignore_index(true, pred, ignore_index)
        res.append(sk_fn(true, pred))
    return np.stack(res)


@pytest.mark.parametrize("inputs", _binary_cases)
@pytest.mark.parametrize(
    "module, functional, compare",
    [
        (BinaryPrecision, binary_precision, sk_precision_score),
        (BinaryRecall, binary_recall, sk_recall_score),
    ],
    ids=["precision", "recall"],
)
class TestBinaryPrecisionRecall(MetricTester):
    """Test class for `BinaryPrecisionRecall` metric."""

    @pytest.mark.parametrize("ignore_index", [None, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("ddp", [False, True])
    def test_binary_precision_recall(self, ddp, inputs, module, functional, compare, ignore_index, multidim_average):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=module,
            reference_metric=partial(
                _sklearn_precision_recall_binary,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
            ),
            metric_args={"threshold": THRESHOLD, "ignore_index": ignore_index, "multidim_average": multidim_average},
        )

    @pytest.mark.parametrize("ignore_index", [None, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    def test_binary_precision_recall_functional(
        self, inputs, module, functional, compare, ignore_index, multidim_average
    ):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=functional,
            reference_metric=partial(
                _sklearn_precision_recall_binary,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
            ),
            metric_args={
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
            },
        )

    def test_binary_precision_recall_differentiability(self, inputs, module, functional, compare):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"threshold": THRESHOLD},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_binary_precision_recall_half_cpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"threshold": THRESHOLD},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_binary_precision_recall_half_gpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"threshold": THRESHOLD},
            dtype=dtype,
        )


def _sklearn_precision_recall_multiclass(preds, target, sk_fn, ignore_index, multidim_average, average):
    if preds.ndim == target.ndim + 1:
        preds = torch.argmax(preds, 1)

    if multidim_average == "global":
        preds = preds.numpy().flatten()
        target = target.numpy().flatten()
        target, preds = remove_ignore_index(target, preds, ignore_index)
        return sk_fn(target, preds, average=average, labels=list(range(NUM_CLASSES)) if average is None else None)

    preds = preds.numpy()
    target = target.numpy()
    res = []
    for pred, true in zip(preds, target):
        pred = pred.flatten()
        true = true.flatten()
        true, pred = remove_ignore_index(true, pred, ignore_index)
        r = sk_fn(true, pred, average=average, labels=list(range(NUM_CLASSES)) if average is None else None)
        res.append(0.0 if np.isnan(r).any() else r)

    return np.stack(res, 0)


@pytest.mark.parametrize("inputs", _multiclass_cases)
@pytest.mark.parametrize(
    "module, functional, compare",
    [
        (MulticlassPrecision, multiclass_precision, sk_precision_score),
        (MulticlassRecall, multiclass_recall, sk_recall_score),
    ],
    ids=["precision", "recall"],
)
class TestMulticlassPrecisionRecall(MetricTester):
    """Test class for `MulticlassPrecisionRecall` metric."""

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", "weighted", None])
    @pytest.mark.parametrize("ddp", [True, False])
    def test_multiclass_precision_recall(
        self, ddp, inputs, module, functional, compare, ignore_index, multidim_average, average
    ):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and target.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=module,
            reference_metric=partial(
                _sklearn_precision_recall_multiclass,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
                "num_classes": NUM_CLASSES,
            },
        )

    @pytest.mark.parametrize("ignore_index", [None, 0, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", "weighted", None])
    def test_multiclass_precision_recall_functional(
        self, inputs, module, functional, compare, ignore_index, multidim_average, average
    ):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and target.ndim < 3:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=functional,
            reference_metric=partial(
                _sklearn_precision_recall_multiclass,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
                "num_classes": NUM_CLASSES,
            },
        )

    def test_multiclass_precision_recall_differentiability(self, inputs, module, functional, compare):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_classes": NUM_CLASSES},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multiclass_precision_recall_half_cpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_classes": NUM_CLASSES},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multiclass_precision_recall_half_gpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_classes": NUM_CLASSES},
            dtype=dtype,
        )


_mc_k_target = tensor([0, 1, 2])
_mc_k_preds = tensor([[0.35, 0.4, 0.25], [0.1, 0.5, 0.4], [0.2, 0.1, 0.7]])


@pytest.mark.parametrize(
    ("metric_class", "metric_fn"), [(MulticlassPrecision, multiclass_precision), (MulticlassRecall, multiclass_recall)]
)
@pytest.mark.parametrize(
    ("k", "preds", "target", "average", "expected_prec", "expected_recall"),
    [
        (1, _mc_k_preds, _mc_k_target, "micro", tensor(2 / 3), tensor(2 / 3)),
        (2, _mc_k_preds, _mc_k_target, "micro", tensor(1 / 2), tensor(1.0)),
    ],
)
def test_top_k(
    metric_class,
    metric_fn,
    k: int,
    preds: Tensor,
    target: Tensor,
    average: str,
    expected_prec: Tensor,
    expected_recall: Tensor,
):
    """A simple test to check that top_k works as expected."""
    class_metric = metric_class(top_k=k, average=average, num_classes=3)
    class_metric.update(preds, target)

    result = expected_prec if metric_class.__name__ == "MulticlassPrecision" else expected_recall

    assert torch.equal(class_metric.compute(), result)
    assert torch.equal(metric_fn(preds, target, top_k=k, average=average, num_classes=3), result)


def _sklearn_precision_recall_multilabel_global(preds, target, sk_fn, ignore_index, average):
    if average == "micro":
        preds = preds.flatten()
        target = target.flatten()
        target, preds = remove_ignore_index(target, preds, ignore_index)
        return sk_fn(target, preds)

    precision_recall, weights = [], []
    for i in range(preds.shape[1]):
        pred, true = preds[:, i].flatten(), target[:, i].flatten()
        true, pred = remove_ignore_index(true, pred, ignore_index)
        precision_recall.append(sk_fn(true, pred))
        confmat = sk_confusion_matrix(true, pred, labels=[0, 1])
        weights.append(confmat[1, 1] + confmat[1, 0])
    res = np.stack(precision_recall, axis=0)

    if average == "macro":
        return res.mean(0)
    if average == "weighted":
        weights = np.stack(weights, 0).astype(float)
        weights_norm = weights.sum(-1, keepdims=True)
        weights_norm[weights_norm == 0] = 1.0
        return ((weights * res) / weights_norm).sum(-1)
    if average is None or average == "none":
        return res
    return None


def _sklearn_precision_recall_multilabel_local(preds, target, sk_fn, ignore_index, average):
    precision_recall, weights = [], []
    for i in range(preds.shape[0]):
        if average == "micro":
            pred, true = preds[i].flatten(), target[i].flatten()
            true, pred = remove_ignore_index(true, pred, ignore_index)
            precision_recall.append(sk_fn(true, pred))
            confmat = sk_confusion_matrix(true, pred, labels=[0, 1])
            weights.append(confmat[1, 1] + confmat[1, 0])
        else:
            scores, w = [], []
            for j in range(preds.shape[1]):
                pred, true = preds[i, j], target[i, j]
                true, pred = remove_ignore_index(true, pred, ignore_index)
                scores.append(sk_fn(true, pred))
                confmat = sk_confusion_matrix(true, pred, labels=[0, 1])
                w.append(confmat[1, 1] + confmat[1, 0])
            precision_recall.append(np.stack(scores))
            weights.append(np.stack(w))
    if average == "micro":
        return np.array(precision_recall)
    res = np.stack(precision_recall, 0)
    if average == "macro":
        return res.mean(-1)
    if average == "weighted":
        weights = np.stack(weights, 0).astype(float)
        weights_norm = weights.sum(-1, keepdims=True)
        weights_norm[weights_norm == 0] = 1.0
        return ((weights * res) / weights_norm).sum(-1)
    if average is None or average == "none":
        return res
    return None


def _sklearn_precision_recall_multilabel(preds, target, sk_fn, ignore_index, multidim_average, average):
    preds = preds.numpy()
    target = target.numpy()
    if np.issubdtype(preds.dtype, np.floating):
        if not ((preds > 0) & (preds < 1)).all():
            preds = sigmoid(preds)
        preds = (preds >= THRESHOLD).astype(np.uint8)
    preds = preds.reshape(*preds.shape[:2], -1)
    target = target.reshape(*target.shape[:2], -1)
    if ignore_index is None and multidim_average == "global":
        return sk_fn(
            target.transpose(0, 2, 1).reshape(-1, NUM_CLASSES),
            preds.transpose(0, 2, 1).reshape(-1, NUM_CLASSES),
            average=average,
        )
    if multidim_average == "global":
        return _sklearn_precision_recall_multilabel_global(preds, target, sk_fn, ignore_index, average)
    return _sklearn_precision_recall_multilabel_local(preds, target, sk_fn, ignore_index, average)


@pytest.mark.parametrize("inputs", _multilabel_cases)
@pytest.mark.parametrize(
    "module, functional, compare",
    [
        (MultilabelPrecision, multilabel_precision, sk_precision_score),
        (MultilabelRecall, multilabel_recall, sk_recall_score),
    ],
    ids=["precision", "recall"],
)
class TestMultilabelPrecisionRecall(MetricTester):
    """Test class for `MultilabelPrecisionRecall` metric."""

    @pytest.mark.parametrize("ddp", [True, False])
    @pytest.mark.parametrize("ignore_index", [None, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", "weighted", None])
    def test_multilabel_precision_recall(
        self, ddp, inputs, module, functional, compare, ignore_index, multidim_average, average
    ):
        """Test class implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 4:
            pytest.skip("samplewise and non-multidim arrays are not valid")
        if multidim_average == "samplewise" and ddp:
            pytest.skip("samplewise and ddp give different order than non ddp")

        self.run_class_metric_test(
            ddp=ddp,
            preds=preds,
            target=target,
            metric_class=module,
            reference_metric=partial(
                _sklearn_precision_recall_multilabel,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "num_labels": NUM_CLASSES,
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
            },
        )

    @pytest.mark.parametrize("ignore_index", [None, -1])
    @pytest.mark.parametrize("multidim_average", ["global", "samplewise"])
    @pytest.mark.parametrize("average", ["micro", "macro", "weighted", None])
    def test_multilabel_precision_recall_functional(
        self, inputs, module, functional, compare, ignore_index, multidim_average, average
    ):
        """Test functional implementation of metric."""
        preds, target = inputs
        if ignore_index == -1:
            target = inject_ignore_index(target, ignore_index)
        if multidim_average == "samplewise" and preds.ndim < 4:
            pytest.skip("samplewise and non-multidim arrays are not valid")

        self.run_functional_metric_test(
            preds=preds,
            target=target,
            metric_functional=functional,
            reference_metric=partial(
                _sklearn_precision_recall_multilabel,
                sk_fn=compare,
                ignore_index=ignore_index,
                multidim_average=multidim_average,
                average=average,
            ),
            metric_args={
                "num_labels": NUM_CLASSES,
                "threshold": THRESHOLD,
                "ignore_index": ignore_index,
                "multidim_average": multidim_average,
                "average": average,
            },
        )

    def test_multilabel_precision_recall_differentiability(self, inputs, module, functional, compare):
        """Test the differentiability of the metric, according to its `is_differentiable` attribute."""
        preds, target = inputs
        self.run_differentiability_test(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
        )

    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multilabel_precision_recall_half_cpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on CPU."""
        preds, target = inputs
        if (preds < 0).any() and dtype == torch.half:
            pytest.xfail(reason="torch.sigmoid in metric does not support cpu + half precision")
        self.run_precision_test_cpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
            dtype=dtype,
        )

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="test requires cuda")
    @pytest.mark.parametrize("dtype", [torch.half, torch.double])
    def test_multilabel_precision_recall_half_gpu(self, inputs, module, functional, compare, dtype):
        """Test dtype support of the metric on GPU."""
        preds, target = inputs
        self.run_precision_test_gpu(
            preds=preds,
            target=target,
            metric_module=module,
            metric_functional=functional,
            metric_args={"num_labels": NUM_CLASSES, "threshold": THRESHOLD},
            dtype=dtype,
        )


def test_corner_case():
    """Issue: https://github.com/Lightning-AI/torchmetrics/issues/1692."""
    # simulate the output of a perfect predictor (i.e. preds == target)
    target = torch.tensor([0, 1, 2, 0, 1, 2])
    preds = target.clone()

    metric = MulticlassPrecision(num_classes=3, average="none", ignore_index=0)
    res = metric(preds, target)
    assert torch.allclose(res, torch.tensor([0.0, 1.0, 1.0]))

    metric = MulticlassRecall(num_classes=3, average="none", ignore_index=0)
    res = metric(preds, target)
    assert torch.allclose(res, torch.tensor([0.0, 1.0, 1.0]))

    metric = MulticlassPrecision(num_classes=3, average="macro", ignore_index=0)
    res = metric(preds, target)
    assert res == 1.0

    metric = MulticlassRecall(num_classes=3, average="macro", ignore_index=0)
    res = metric(preds, target)
    assert res == 1.0


@pytest.mark.parametrize(
    ("metric", "kwargs", "base_metric"),
    [
        (BinaryPrecision, {"task": "binary"}, Precision),
        (MulticlassPrecision, {"task": "multiclass", "num_classes": 3}, Precision),
        (MultilabelPrecision, {"task": "multilabel", "num_labels": 3}, Precision),
        (None, {"task": "not_valid_task"}, Precision),
        (BinaryRecall, {"task": "binary"}, Recall),
        (MulticlassRecall, {"task": "multiclass", "num_classes": 3}, Recall),
        (MultilabelRecall, {"task": "multilabel", "num_labels": 3}, Recall),
        (None, {"task": "not_valid_task"}, Recall),
    ],
)
def test_wrapper_class(metric, kwargs, base_metric):
    """Test the wrapper class."""
    assert issubclass(base_metric, Metric)
    if metric is None:
        with pytest.raises(ValueError, match=r"Invalid *"):
            base_metric(**kwargs)
    else:
        instance = base_metric(**kwargs)
        assert isinstance(instance, metric)
        assert isinstance(instance, Metric)

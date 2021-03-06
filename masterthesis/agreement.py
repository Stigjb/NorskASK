import argparse
from collections import Counter, defaultdict
import logging
from math import sqrt
from pathlib import Path
import pickle
import sys
from typing import Any, DefaultDict, Iterable, List  # noqa: F401

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error

try:
    import seaborn as sns

    sns.set()
except ImportError:
    pass


logger = logging.getLogger(__name__)
logging.basicConfig()


def macro_rmse(true, pred) -> float:
    """Calculate the macro averaged root mean squared error."""
    groups = defaultdict(list)  # type: DefaultDict[int, List[int]]
    for t, p in zip(true, pred):
        groups[t].append((t - p) ** 2)  # Collect squared error for each class
    rmses = [
        sqrt(sum(group) / len(group)) for group in groups.values()
    ]  # RMSE for each group
    return sum(rmses) / len(rmses)  # Macro average


def macro_mae(true, pred) -> float:
    """Calculate the macro averaged mean absolute error."""
    groups = defaultdict(list)  # type: DefaultDict[int, List[int]]
    for t, p in zip(true, pred):
        groups[t].append(abs(t - p))  # Collect absolute error for each class
    maes = [sum(group) / len(group) for group in groups.values()]  # MAE for each group
    return sum(maes) / len(maes)  # Macro average


def get_type(name: str) -> str:
    if name.startswith("rnn") or name.startswith("taghipour"):
        return "RNN"
    elif name.startswith("cnn"):
        return "CNN"
    elif name.startswith("mlp"):
        return "MLP"
    elif name.startswith("linear_logreg"):
        return "LogReg"
    elif name.startswith("linear_svc"):
        return "SVC"
    elif name.startswith("linear_svr"):
        return "SVR"
    print("UNKOWN TYPE " + name)
    return "UNK"


def files_to_dataframe(files: Iterable[str]) -> pd.DataFrame:
    data = defaultdict(list)  # type: DefaultDict[str, Any]
    for filename in files:
        results_file = Path(filename)
        try:
            res = pickle.load(results_file.open("rb"))
        except Exception as e:
            logger.warn(e)
            logger.warn("Could not read file %s" % results_file)
            continue
        try:
            gold = res.true
            pred = res.predictions.ravel()
        except AttributeError:
            logger.warn("Could not find gold and pred for file %s" % results_file)
            continue
        data["filename"].append(results_file.name)
        data["n_class"].append(max(max(gold), max(pred)) + 1)

        data["nli"].append(res.config.get("nli", False))
        data["pearson"].append(pearsonr(gold, pred)[0])
        data["spearman"].append(spearmanr(gold, pred)[0])
        data["macro F1"].append(f1_score(gold, pred, average="macro"))
        data["micro F1"].append(f1_score(gold, pred, average="micro"))
        data["weighted F1"].append(f1_score(gold, pred, average="weighted"))
        rmse = sqrt(mean_squared_error(gold, pred))
        data["RMSE"].append(rmse)
        data["MAE"].append(mean_absolute_error(gold, pred))
        data["macro MAE"].append(macro_mae(gold, pred))
        data["macro RMSE"].append(macro_rmse(gold, pred))
        data["type"].append(get_type(results_file.stem))
    return pd.DataFrame.from_dict(data)


def pi_k(a: List[int], b: List[int]) -> float:
    n_a = len(a)
    n_b = len(b)
    count_a = Counter(a)
    count_b = Counter(b)
    ks = list(range(min(a + b), max(a + b)))
    pis = []
    for k in ks:
        pis.append((count_a[k] / n_a + count_b[k] / n_b) / 2)
    num_classes = len(pis)
    ac = (1 / (num_classes - 1)) * sum(p * (1 - p) for p in pis)
    return ac


def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    return parser.parse_args()


def get_corr_mask(n: int):
    mask = np.ones((n, n), dtype=bool)
    return np.triu(mask, 1)


def print_top_by_metric(data: pd.DataFrame, metrics: Iterable[str], n: int = 5) -> None:
    for metric in metrics:
        ascending = metric in {"MAE", "RMSE", "macro MAE", "macro RMSE"}
        print("\nTop %s:" % metric)
        print(
            data.sort_values(metric, ascending=ascending)
            .loc[:, ["filename", metric]]
            .head(n)
        )


def plot_corrs(data: pd.DataFrame):
    corr_matrix = data.drop(columns=["filename", "nli", "n_class"]).corr()
    sns.heatmap(
        corr_matrix,
        center=0,
        mask=get_corr_mask(len(corr_matrix)),
        annot=True,
        fmt=".3f",
    )
    plt.tight_layout()


def plot_reg(data, x, y, ax):
    sns.regplot(x=x, y=y, data=data, ax=ax, scatter=False, truncate=True, color="C0")
    sns.regplot(
        x=x,
        y=y,
        data=data[data.type == "CNN"],
        ax=ax,
        scatter=False,
        truncate=True,
        color="C1",
        line_kws={"linestyle": "--"},
    )
    sns.regplot(
        x=x,
        y=y,
        data=data[data.type == "RNN"],
        ax=ax,
        scatter=False,
        truncate=True,
        color="C2",
        line_kws={"linestyle": "--"},
    )


def plot_regs(data: pd.DataFrame):
    fig, (ax1, ax2) = plt.subplots(1, 2)
    fig.set_size_inches(5, 3.5)

    plot_reg(data, "macro F1", "micro F1", ax1)
    sns.scatterplot("macro F1", "micro F1", data=data, style="type", ax=ax1)
    print("Correlation of macro F₁ and micro F₁")
    print(
        "Pearson corr. = %.3f, p = %.2e" % pearsonr(data["macro F1"], data["micro F1"])
    )
    print(
        "Spearman corr. = %.3f, p = %.2e" % pearsonr(data["macro F1"], data["micro F1"])
    )

    plot_reg(data, "macro F1", "MAE", ax2)
    sns.scatterplot("macro F1", "MAE", data=data, style="type", ax=ax2)
    print("Correlation of macro F₁ and MAE")
    print(
        "Pearson corr. = %.3f, p = %.2e" % pearsonr(data["macro F1"], data["MAE"])
    )
    print(
        "Spearman corr. = %.3f, p = %.2e" % spearmanr(data["macro F1"], data["MAE"])
    )
    plt.tight_layout()


def main():
    args = parse_args()
    df = files_to_dataframe(args.files)
    df.to_csv("metrics.csv", index=False)

    print("== All labels ==")
    lim_df = df.query("n_class == 7 and not nli").dropna()
    print(lim_df.head())
    print_top_by_metric(lim_df, ["macro F1", "micro F1", "spearman", "RMSE", "MAE"])
    if "seaborn" in sys.modules:
        plot_corrs(lim_df)
        plt.show()
        plot_regs(lim_df)
        plt.show()

    print("== Collapsed labels ==")
    lim_df = df.query("n_class == 4").dropna()
    print_top_by_metric(lim_df, ["spearman", "RMSE", "MAE"])
    if "seaborn" in sys.modules:
        plot_corrs(lim_df)
        plt.show()
        plot_regs(lim_df)
        plt.show()


if __name__ == "__main__":
    main()

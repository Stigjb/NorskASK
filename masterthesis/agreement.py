import argparse
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path
import pickle
from typing import Any, DefaultDict, Iterable, List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
import seaborn as sns
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error
sns.set()


def macro_rmse(true, pred):
    groups = defaultdict(list)
    for t, p in zip(true, pred):
        groups[t].append((t - p) ** 2)
    rmses = []
    for group in groups.values():
        rmses.append(sqrt(sum(group) / len(group)))
    return sum(rmses) / len(rmses)


def macro_mae(true, pred):
    groups = defaultdict(list)
    for t, p in zip(true, pred):
        groups[t].append(abs(t - p))  # Collect absolute error for each class
    maes = [sum(group) / len(group) for group in groups.values()]  # MAE for each group
    return sum(maes) / len(maes)  # Macro average


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
    parser.add_argument('files', nargs='+')
    return parser.parse_args()


def get_corr_mask(n: int):
    mask = np.ones((n, n), dtype=bool)
    return np.triu(mask, 1)


def files_to_dataframe(files: Iterable[str]) -> pd.DataFrame:
    data = defaultdict(list)  # type: DefaultDict[str, Any]
    for filename in files:
        results_file = Path(filename)
        try:
            res = pickle.load(results_file.open('rb'))
        except Exception as e:
            print(e)
            print('Could not read file %s' % results_file)
            continue
        try:
            gold = res.true
            pred = res.predictions
        except AttributeError:
            print('Could not find gold and pred for file %s' % results_file)
            continue
        data['filename'].append(results_file.name)
        data['n_class'].append(max(max(gold), max(pred)) + 1)

        data['pearson'].append(pearsonr(gold, pred)[0])
        data['spearman'].append(spearmanr(gold, pred)[0])
        data['macro F1'].append(f1_score(gold, pred, average='macro'))
        data['micro F1'].append(f1_score(gold, pred, average='micro'))
        data['weighted F1'].append(f1_score(gold, pred, average='weighted'))
        rmse = sqrt(mean_squared_error(gold, pred))
        data['RMSE'].append(rmse)
        data['MAE'].append(mean_absolute_error(gold, pred))
        data['macro MAE'].append(macro_mae(gold, pred))
        data['macro RMSE'].append(macro_rmse(gold, pred))
    return pd.DataFrame.from_dict(data)


def print_top_by_metric(data: pd.DataFrame, metrics: Iterable[str], n: int = 5) -> None:
    for metric in metrics:
        print('\nTop %s:' % metric)
        print(data.sort_values(metric, ascending=False)
                  .loc[:, ['filename', metric]]
                  .head(n))


def plot_corrs(data: pd.DataFrame):
    corr_matrix = data.drop(columns=['filename', 'n_class']).corr()
    sns.heatmap(corr_matrix, center=0, mask=get_corr_mask(len(corr_matrix)), annot=True, fmt='.3f')
    plt.tight_layout()


def plot_regs(data: pd.DataFrame):
    fig, (ax1, ax2) = plt.subplots(1, 2)
    sns.regplot('macro F1', 'micro F1', data=data, ax=ax1)
    ax1.set_title('Pearson corr. = %.3f, p = %.4f'
                  % pearsonr(data['macro F1'], data['micro F1']))
    sns.regplot('macro F1', 'macro MAE', data=data, ax=ax2)
    ax2.set_title('Pearson corr. = %.3f, p = %.4f'
                  % pearsonr(data['macro F1'], data['macro MAE']))
    plt.tight_layout()


def main():
    args = parse_args()
    df = files_to_dataframe(args.files)
    df.to_csv('metrics.csv', index=False)

    print('== All labels ==')
    lim_df = df[df.n_class == 7].dropna()
    print_top_by_metric(lim_df, ['spearman', 'RMSE', 'MAE'])
    plot_corrs(lim_df)
    plt.show()
    plot_regs(lim_df)
    plt.show()

    print('== Collapsed labels ==')
    lim_df = df[df.n_class == 4].dropna()
    print_top_by_metric(lim_df, ['spearman', 'RMSE', 'MAE'])
    plot_corrs(lim_df)
    plt.show()
    plot_regs(lim_df)
    plt.show()


if __name__ == '__main__':
    main()

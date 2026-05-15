"""Headless matplotlib chart generators for HTML reports."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # must be before pyplot import

import matplotlib.pyplot as plt


def histogram(
    values: list[float],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: Path,
    bins: int = 20,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4))
    if values:
        ax.hist(values, bins=bins, edgecolor="black", color="#4C72B0")
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)
    return output_path


def bar(
    labels: list[str],
    values: list[float],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.6), 4))
    if labels:
        x = range(len(labels))
        ax.bar(x, values, edgecolor="black", color="#4C72B0")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)
    return output_path


def scatter_or_line(
    x_values: list[float],
    y_values: list[float],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    output_path: Path,
    mode: str = "line",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4))
    if x_values and y_values:
        if mode == "scatter":
            ax.scatter(x_values, y_values, s=20, alpha=0.6, color="#4C72B0")
        else:
            ax.plot(x_values, y_values, color="#4C72B0")
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)
    return output_path

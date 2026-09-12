from pathlib import Path
from uuid import uuid4

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

from app.config import get_settings
from app.providers.goodinfo import goodinfo_provider
from app.providers.finmind import FinMindError, finmind_provider
from app.providers.market import market_catalog


def _configure_fonts() -> None:
    bundled_font = Path(__file__).with_name("assets") / "NotoSansTC-StockSubset.ttf"
    if bundled_font.exists():
        font_manager.fontManager.addfont(str(bundled_font))
        selected = font_manager.FontProperties(fname=str(bundled_font)).get_name()
        plt.rcParams["font.family"] = selected
        plt.rcParams["axes.unicode_minus"] = False
        return
    preferred_fonts = (
        "Noto Sans TC",
        "Noto Sans CJK TC",
        "Microsoft JhengHei",
        "PingFang TC",
        "Arial Unicode MS",
    )
    installed = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((font for font in preferred_fonts if font in installed), "DejaVu Sans")
    plt.rcParams["font.family"] = selected
    plt.rcParams["axes.unicode_minus"] = False


_configure_fonts()


def _new_path(prefix: str) -> Path:
    directory = get_settings().resolved_artifact_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{prefix}-{uuid4().hex}.png"


def _set_labels(ax: plt.Axes, title: str, ylabel: str) -> None:
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)


def _label_indices(length: int, max_labels: int) -> list[int]:
    if length <= 0:
        return []
    if max_labels < 2 or length <= max_labels:
        return list(range(length))
    step = (length - 1) / (max_labels - 1)
    return sorted({round(position * step) for position in range(max_labels)})


def _annotate_points(
    ax: plt.Axes,
    dates: pd.Series,
    values: pd.Series,
    *,
    max_labels: int,
    suffix: str = "",
    color: str = "#343a40",
) -> list[int]:
    indices = _label_indices(len(values), max_labels)
    for order, index in enumerate(indices):
        value = values.iloc[index]
        if pd.isna(value):
            continue
        offset = 9 if order % 2 == 0 else -14
        ax.annotate(
            f"{value:.1f}{suffix}",
            (dates.iloc[index], value),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va="bottom" if offset > 0 else "top",
            fontsize=8,
            color=color,
            bbox={
                "boxstyle": "round,pad=0.18",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.75,
            },
        )
    return indices


async def gross_margin_chart(stock_code: str) -> tuple[Path, str]:
    source = "Goodinfo"
    frame = await goodinfo_provider.gross_margin_history(stock_code)
    if frame is None or frame.empty:
        try:
            frame = await finmind_provider.gross_margin_history(stock_code)
        except FinMindError as exc:
            raise ValueError("Goodinfo 查無毛利率資料，FinMind 備援也無可用資料") from exc
        source = "FinMind 備援"
    company = await market_catalog.get(stock_code)
    stock_label = f"{stock_code} {company.name}" if company else stock_code
    path = _new_path(f"gross-margin-{stock_code}")
    fig, ax = plt.subplots(figsize=(10, 5.4))
    ax.plot(frame["date"], frame["gross_margin"], marker="o", linewidth=2, color="#1c7ed6")
    _annotate_points(
        ax,
        frame["date"],
        frame["gross_margin"],
        max_labels=16,
        suffix="%",
        color="#1864ab",
    )
    _set_labels(
        ax,
        f"{stock_label} Gross margin history · Source: {source}",
        "Gross margin (%)",
    )
    ax.axhline(frame["gross_margin"].median(), linestyle="--", color="#868e96", alpha=0.8)
    ax.margins(x=0.05, y=0.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, source


async def pe_river_chart(stock_code: str) -> tuple[Path, str]:
    source = "Goodinfo"
    frame = await goodinfo_provider.valuation_history(stock_code)
    if frame is None or len(frame) < 20:
        try:
            frame = await finmind_provider.valuation_history(stock_code)
        except FinMindError as exc:
            raise ValueError("Goodinfo 查無估值資料，FinMind 備援也無足夠資料") from exc
        source = "FinMind 備援"
    company = await market_catalog.get(stock_code)
    stock_label = f"{stock_code} {company.name}" if company else stock_code
    quantiles = frame["pe"].quantile([0.1, 0.3, 0.5, 0.7, 0.9])
    frame = frame.copy()
    frame["eps_proxy"] = frame["close"] / frame["pe"]
    colors = ["#d3f9d8", "#b2f2bb", "#ffec99", "#ffd8a8", "#ffc9c9"]
    labels = [f"PE {quantiles.loc[q]:.1f}x" for q in quantiles.index]
    bands = [frame["eps_proxy"] * quantiles.loc[q] for q in quantiles.index]

    path = _new_path(f"pe-river-{stock_code}")
    fig, ax = plt.subplots(figsize=(11, 6))
    baseline = pd.Series(0.0, index=frame.index)
    for band, color, label in zip(bands, colors, labels, strict=True):
        ax.fill_between(frame["date"], baseline, band, color=color, alpha=0.55, label=label)
        baseline = band
    close_label_indices = _label_indices(len(frame), 10)
    ax.plot(
        frame["date"],
        frame["close"],
        color="#212529",
        linewidth=1.6,
        marker="o",
        markevery=close_label_indices,
        markersize=4,
        label="Close",
    )
    _annotate_points(
        ax,
        frame["date"],
        frame["close"],
        max_labels=10,
        color="#212529",
    )
    _set_labels(
        ax,
        f"{stock_label} PE river (historical quantiles) · Source: {source}",
        "Price",
    )
    ax.legend(ncol=3, fontsize=8, loc="upper left")
    ax.margins(x=0.04, y=0.16)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path, source

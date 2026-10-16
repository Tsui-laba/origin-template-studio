#!/usr/bin/env python3
"""Render user data with the formatting stored in Origin OPJU templates.

The program intentionally edits data only. Graph pages, plot styles, axes,
labels, legends, fonts, colors, and page dimensions remain template-owned.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


APP_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_DIR = APP_DIR.parent / "templates"
DEFAULT_CONFIG = APP_DIR / "template_configs.json"
ANALYSIS_WORDS = (
    "fit", "result", "report", "analysis", "stat", "peak", "smooth",
    "baseline", "integral", "summary", "anova", "nlfit", "linearfit",
)
SOURCE_WORDS = ("data", "raw", "source", "input", "original", "sample")
LOOSE_DATASET_PREFIX = "[??]!"


class PlotterError(RuntimeError):
    """A user-actionable plotting error."""


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_id(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def template_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(config.get("templates", []))


def resolve_template(
    value: str,
    template_dir: Path,
    config: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    direct = Path(value).expanduser()
    if direct.is_file():
        return direct.resolve(), {
            "id": direct.stem,
            "filename": direct.name,
            **config.get("defaults", {}),
        }

    target = normalize_id(value)
    matches = []
    for row in template_rows(config):
        aliases = [row.get("id", ""), row.get("filename", "")]
        aliases.extend(row.get("aliases", []))
        if target in {normalize_id(str(x)) for x in aliases}:
            matches.append(row)
    if not matches:
        available = ", ".join(row["id"] for row in template_rows(config))
        raise PlotterError(f"找不到模板 {value!r}。可用圖型：{available}")
    row = {**config.get("defaults", {}), **matches[0]}
    path = (template_dir / row["filename"]).resolve()
    if not path.is_file():
        raise PlotterError(f"模板不存在：{path}")
    return path, row


def import_originpro():
    try:
        import originpro as op  # type: ignore
    except ImportError as exc:
        raise PlotterError(
            "尚未安裝 originpro。請先執行：\n"
            f'  python -m pip install -r "{APP_DIR / "requirements.txt"}"'
        ) from exc
    return op


class OriginSession:
    """Own or attach to an Origin session and always release it safely."""

    def __init__(self, *, hidden: bool, attach: bool, keep_open: bool):
        self.hidden = hidden
        self.attach_existing = attach
        self.keep_open = keep_open
        self.op = None

    def __enter__(self):
        self.op = import_originpro()
        if self.op.oext:
            if self.attach_existing:
                self.op.attach()
            if not self.attach_existing:
                # This call lazily starts Origin's Automation Server.
                self.op.set_show(not self.hidden)
        return self.op

    def __exit__(self, exc_type, exc, tb):
        if self.op is None or not self.op.oext:
            return False
        try:
            if self.attach_existing or self.keep_open:
                self.op.detach()
            else:
                self.op.exit()
        except Exception:
            # Preserve the original plotting exception, if any.
            if exc is None:
                raise
        return False


def page_name(page) -> str:
    try:
        return page.name
    except Exception:
        return str(page)


def page_long_name(page) -> str:
    try:
        return page.lname
    except Exception:
        return ""


def collect_worksheets(op) -> list[tuple[Any, Any]]:
    sheets: list[tuple[Any, Any]] = []
    for book in op.pages("w"):
        for sheet in book:
            sheets.append((book, sheet))
    return sheets


def collect_matrices(op) -> list[tuple[Any, Any]]:
    sheets: list[tuple[Any, Any]] = []
    for book in op.pages("m"):
        for sheet in book:
            sheets.append((book, sheet))
    return sheets


def collect_graphs(op) -> list[Any]:
    return list(op.pages("g"))


def plot_dataset(plot, layer, plot_index: int) -> str:
    candidates = []
    try:
        candidates.append(plot.obj.GetDatasetName())
    except Exception:
        pass
    for prop in ("name", "dataset"):
        try:
            candidates.append(plot.get_str(prop))
        except Exception:
            pass
    try:
        candidates.append(layer.get_str(f"plot{plot_index + 1}.name"))
    except Exception:
        pass
    return next((str(v) for v in candidates if v), "")


def graph_plot_count(graph) -> int:
    total = 0
    try:
        for layer in graph:
            total += len(layer.plot_list())
    except Exception:
        pass
    return total


def select_graph(graphs: Sequence[Any], selector: str | None) -> Any:
    if not graphs:
        raise PlotterError("此 OPJU 沒有可匯出的圖頁。")
    if not selector or selector.lower() == "auto":
        # Prefer the graph that carries the most actual data plots.
        return max(graphs, key=lambda g: (graph_plot_count(g), len(g)))
    wanted = selector.casefold()
    exact = [
        g for g in graphs
        if page_name(g).casefold() == wanted or page_long_name(g).casefold() == wanted
    ]
    if len(exact) == 1:
        return exact[0]
    options = ", ".join(page_name(g) for g in graphs)
    raise PlotterError(f"找不到圖頁 {selector!r}。可用圖頁：{options}")


def datasets_in_graph(graph) -> list[str]:
    values: list[str] = []
    for layer in graph:
        for index, plot in enumerate(layer.plot_list()):
            value = plot_dataset(plot, layer, index)
            if value and value not in values:
                values.append(value)
    return values


def plot_source_ranges(op, plot, serial: int) -> dict[str, str]:
    """Resolve a graph DataPlot to its worksheet or loose-dataset X/Y ranges."""
    graph_range = plot.lt_range()
    base = f"__cdx_{serial}"
    x_range_var = f"{base}_gx"
    y_range_var = f"{base}_gy"
    x_string_var = f"{base}_xs"
    y_string_var = f"{base}_ys"
    op.lt_exec(
        f"range -wx {x_range_var}={graph_range};"
        f"range -wy {y_range_var}={graph_range};"
        f"string {x_string_var}$=%({x_range_var});"
        f"string {y_string_var}$=%({y_range_var});"
    )
    result = {"graph_range": graph_range, "x_range": "", "y_range": ""}
    try:
        result["x_range"] = op.get_lt_str(x_string_var)
    except Exception:
        pass
    try:
        result["y_range"] = op.get_lt_str(y_string_var)
    except Exception:
        pass
    op.lt_exec(
        f"del -ra {x_range_var}; del -ra {y_range_var};"
        f"del -al {x_string_var}; del -al {y_string_var};"
    )
    return result


def loose_dataset_name(range_string: str) -> str:
    if range_string.startswith(LOOSE_DATASET_PREFIX):
        return range_string[len(LOOSE_DATASET_PREFIX):]
    return ""


def graph_plot_sources(op, graph) -> list[dict[str, Any]]:
    sources = []
    serial = 0
    for layer_index, layer in enumerate(graph, 1):
        for plot_index, plot in enumerate(layer.plot_list(), 1):
            serial += 1
            ranges = plot_source_ranges(op, plot, serial)
            sources.append({
                "layer": layer_index,
                "plot": plot_index,
                "dataset": plot_dataset(plot, layer, plot_index - 1),
                **ranges,
                "x_dataset": loose_dataset_name(ranges["x_range"]),
                "y_dataset": loose_dataset_name(ranges["y_range"]),
            })
    return sources


def graph_loose_dataset_names(op, graph) -> list[str]:
    names = []
    for source in graph_plot_sources(op, graph):
        for key in ("x_dataset", "y_dataset"):
            name = source[key]
            if name and name not in names:
                names.append(name)
    return names


def qualified_sheet_name(book, sheet) -> str:
    return f"[{page_name(book)}]{page_name(sheet)}"


def sheet_candidates_text(sheets: Sequence[tuple[Any, Any]]) -> str:
    return ", ".join(qualified_sheet_name(book, sheet) for book, sheet in sheets)


def _sheet_aliases(book, sheet) -> set[str]:
    bn, sn = page_name(book), page_name(sheet)
    bln, sln = page_long_name(book), page_long_name(sheet)
    raw = {
        sn, sln,
        f"[{bn}]{sn}", f"{bn}/{sn}", f"{bn}::{sn}", f"{bn}!{sn}",
        f"[{bln}]{sln}", f"{bln}/{sln}", f"{bln}::{sln}",
    }
    return {x.casefold() for x in raw if x}


def score_sheet(book, sheet, datasets: Sequence[str], order: int) -> int:
    bn = page_name(book)
    text = " ".join(
        (bn, page_long_name(book), page_name(sheet), page_long_name(sheet))
    ).casefold()
    score = max(0, 6 - order)
    try:
        rows, cols = sheet.shape
        score += 10 if rows > 0 else 0
        score += 8 if cols >= 2 else 0
    except Exception:
        pass
    score += 16 * sum(word in text for word in SOURCE_WORDS)
    score -= 24 * sum(word in text for word in ANALYSIS_WORDS)
    prefix = f"{bn}_".casefold()
    for dataset in datasets:
        ds = dataset.casefold()
        if ds.startswith(prefix):
            score += 35
            try:
                sheet_order = sheet.index() + 1
                if re.search(rf"@{sheet_order}(?:\D|$)", ds):
                    score += 15
            except Exception:
                pass
    return score


def select_sheet(
    sheets: Sequence[tuple[Any, Any]],
    selector: str | None,
    graph: Any | None,
) -> tuple[Any, Any]:
    if not sheets:
        raise PlotterError("此 OPJU 沒有工作表。")
    if selector and selector.lower() != "auto":
        wanted = selector.casefold()
        found = [(b, s) for b, s in sheets if wanted in _sheet_aliases(b, s)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise PlotterError(
                f"工作表名稱 {selector!r} 不唯一，請用 [Book]Sheet 格式指定。"
            )
        raise PlotterError(
            f"找不到工作表 {selector!r}。可用工作表：{sheet_candidates_text(sheets)}"
        )
    datasets = datasets_in_graph(graph) if graph is not None else []
    scored = [
        (score_sheet(book, sheet, datasets, order), -order, book, sheet)
        for order, (book, sheet) in enumerate(sheets)
    ]
    _, _, book, sheet = max(scored, key=lambda row: (row[0], row[1]))
    return book, sheet


def column_info(sheet) -> list[dict[str, str]]:
    result = []
    try:
        count = sheet.cols
    except Exception:
        count = sheet.shape[1]
    for index in range(count):
        item = {}
        for key, label in (("short_name", "G"), ("long_name", "L"),
                           ("units", "U"), ("comments", "C")):
            try:
                item[key] = sheet.get_label(index, label)
            except Exception:
                item[key] = ""
        result.append(item)
    return result


def inventory_current_project(op, template_name: str) -> dict[str, Any]:
    books = []
    for book in op.pages("w"):
        sheets = []
        for sheet in book:
            sheets.append({
                "name": page_name(sheet),
                "long_name": page_long_name(sheet),
                "qualified_name": qualified_sheet_name(book, sheet),
                "shape": list(sheet.shape),
                "columns": column_info(sheet),
            })
        books.append({
            "name": page_name(book),
            "long_name": page_long_name(book),
            "sheets": sheets,
        })
    matrices = []
    for book in op.pages("m"):
        matrices.append({
            "name": page_name(book),
            "long_name": page_long_name(book),
            "sheets": [
                {
                    "name": page_name(sheet),
                    "long_name": page_long_name(sheet),
                    "shape": list(sheet.shape),
                }
                for sheet in book
            ],
        })
    graphs = []
    for graph in op.pages("g"):
        layers = []
        serial = 0
        loose_names = []
        for layer_index, layer in enumerate(graph):
            plots = []
            for plot_index, plot in enumerate(layer.plot_list()):
                serial += 1
                ranges = plot_source_ranges(op, plot, serial)
                x_dataset = loose_dataset_name(ranges["x_range"])
                y_dataset = loose_dataset_name(ranges["y_range"])
                for name in (x_dataset, y_dataset):
                    if name and name not in loose_names:
                        loose_names.append(name)
                plots.append({
                    "index": plot_index + 1,
                    "dataset": plot_dataset(plot, layer, plot_index),
                    **ranges,
                    "x_dataset": x_dataset,
                    "y_dataset": y_dataset,
                })
            layers.append({"index": layer_index + 1, "plots": plots})
        graphs.append({
            "name": page_name(graph),
            "long_name": page_long_name(graph),
            "layers": layers,
            "loose_datasets": loose_names,
        })
    return {
        "template": template_name,
        "workbooks": books,
        "matrixbooks": matrices,
        "graphs": graphs,
    }


def open_project(
    op,
    path: Path,
    *,
    readonly: bool,
    attach: bool,
    attempts: int = 4,
) -> None:
    """Open an OPJU, tolerating Origin's brief Automation startup race."""
    last_error: Exception | None = None
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            if op.open(str(path), readonly=readonly, asksave=attach):
                return
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.75 * (attempt + 1))
    if last_error is not None:
        raise PlotterError(f"Origin 無法開啟：{path}") from last_error
    raise PlotterError(f"Origin 無法開啟：{path}")


def read_table(path: Path, excel_sheet: str | int | None, no_header: bool):
    try:
        import pandas as pd
    except ImportError as exc:
        raise PlotterError("缺少 pandas；請執行 pip install -r requirements.txt") from exc
    header = None if no_header else 0
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        selected: str | int = 0 if excel_sheet is None else excel_sheet
        try:
            selected = int(selected)
        except (TypeError, ValueError):
            pass
        df = pd.read_excel(path, sheet_name=selected, header=header)
    elif suffix in {".csv", ".txt", ".tsv", ".dat"}:
        if suffix == ".csv":
            sep, engine = ",", "c"
        elif suffix == ".tsv":
            sep, engine = "\t", "c"
        else:
            sep, engine = None, "python"
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                df = pd.read_csv(
                    path,
                    header=header,
                    sep=sep,
                    engine=engine,
                    encoding=encoding,
                )
                break
            except UnicodeDecodeError as exc:
                last_error = exc
        else:
            raise PlotterError(f"無法辨識文字編碼：{path}") from last_error
    elif suffix == ".json":
        df = pd.read_json(path)
    else:
        raise PlotterError("輸入資料僅支援 CSV、TSV、TXT、DAT、XLSX、XLS、JSON。")
    # Keep blank columns because Origin graphs are linked by worksheet column
    # position.  Dropping an intentionally blank spacer column would shift all
    # later input columns onto the wrong plots (for example K/L -> B/C).
    df = df.dropna(axis=0, how="all")
    if no_header:
        df.columns = [f"Column{i + 1}" for i in range(df.shape[1])]
    if df.empty or df.shape[1] == 0:
        raise PlotterError(f"輸入資料沒有可繪製的內容：{path}")
    return df


def parse_start_column(sheet, value: str | int) -> int:
    if isinstance(value, int) or str(value).isdigit():
        result = int(value)
    else:
        try:
            result = sheet.lt_col_index(str(value)) - 1
        except Exception as exc:
            raise PlotterError(f"找不到起始欄位 {value!r}") from exc
    if result < 0:
        raise PlotterError("起始欄位必須是 0 以上的索引或有效欄名。")
    return result


def replace_worksheet_data(
    sheet,
    df,
    *,
    start_col: str | int,
    header_mode: str,
    allow_extra_columns: bool,
) -> dict[str, Any]:
    first = parse_start_column(sheet, start_col)
    needed = first + df.shape[1]
    original_cols = sheet.cols
    if needed > original_cols and not allow_extra_columns:
        raise PlotterError(
            f"輸入有 {df.shape[1]} 欄，但模板從第 {first} 欄起只剩 "
            f"{original_cols - first} 欄。為維持圖型相似度，請保持相同欄數；"
            "或明確加上 --allow-extra-columns。"
        )
    if needed > original_cols:
        sheet.cols = needed
    original_long_names = []
    for col in range(first, needed):
        try:
            original_long_names.append(sheet.get_label(col, "L"))
        except Exception:
            original_long_names.append("")
    # Clear only the overwritten input block. Formula/result columns outside
    # this block are deliberately preserved for Origin recalculation.
    sheet.clear(first, ncols=df.shape[1])
    sheet.from_df(df, c1=first)
    if header_mode == "keep":
        for offset, label in enumerate(original_long_names):
            sheet.set_label(first + offset, label, "L")
    return {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "start_column": first,
        "template_columns": original_cols,
    }


def materialize_loose_datasets(op, dataset_names: Sequence[str]):
    """Copy project-level loose datasets to a temporary worksheet DataFrame."""
    if not dataset_names:
        raise PlotterError("此圖頁沒有可匯出的 worksheet 或 loose dataset。")
    sheet = op.new_sheet(lname="CODEX_LOOSE_DATA_EXPORT", hidden=True)
    book = sheet.get_book()
    try:
        sheet.cols = len(dataset_names)
        for index, name in enumerate(dataset_names):
            src = f"__cdx_es_{index + 1}"
            dst = f"__cdx_ed_{index + 1}"
            # Origin's LT_execute may report False for a compound range command
            # even when every assignment succeeds, so validate by reading back.
            sheet.lt_exec(
                f"range {src}={LOOSE_DATASET_PREFIX}{name};"
                f"range {dst}=col({index + 1});"
                f"{dst}={src};"
                f"del -ra {src}; del -ra {dst};"
            )
            sheet.set_label(index, name, "L")
        df = sheet.to_df()
        if df.empty or not any(df[column].notna().any() for column in df.columns):
            raise PlotterError("Origin 無法把圖層內嵌資料還原成輸入表格。")
        return df
    finally:
        book.destroy()


def map_loose_input_columns(
    df,
    dataset_names: Sequence[str],
    *,
    allow_extra_columns: bool,
) -> list[tuple[str, Any]]:
    """Map CSV/Excel columns to loose datasets by header, or by full position."""
    wanted = {name.casefold(): name for name in dataset_names}
    matched: dict[str, Any] = {}
    unknown = []
    for column in df.columns:
        key = str(column).strip().casefold()
        if key in wanted:
            matched[wanted[key]] = column
        else:
            unknown.append(column)
    if matched:
        if unknown and not allow_extra_columns:
            raise PlotterError(
                "輸入欄位同時包含 loose dataset 名稱與無法識別的欄位："
                + ", ".join(str(value) for value in unknown)
                + "。請從 export-input 匯出的檔案開始修改。"
            )
        return [(name, matched[name]) for name in dataset_names if name in matched]
    if len(df.columns) == len(dataset_names):
        return list(zip(dataset_names, df.columns))
    expected = ", ".join(dataset_names)
    raise PlotterError(
        f"此圖需要 {len(dataset_names)} 個資料欄，或以 dataset 名稱指定要更新的欄位。"
        f"目前輸入有 {len(df.columns)} 欄。預期欄名：{expected}"
    )


def _series_values_without_trailing_missing(series) -> list[Any]:
    values = series.tolist()
    valid = series.notna().tolist()
    while values and not valid[-1]:
        values.pop()
        valid.pop()
    return values


def replace_loose_dataset_data(
    op,
    df,
    dataset_names: Sequence[str],
    *,
    allow_extra_columns: bool,
) -> dict[str, Any]:
    """Replace graph-owned loose datasets while leaving every plot object intact."""
    mapping = map_loose_input_columns(
        df,
        dataset_names,
        allow_extra_columns=allow_extra_columns,
    )
    sheet = op.new_sheet(lname="CODEX_LOOSE_DATA_INPUT", hidden=True)
    book = sheet.get_book()
    try:
        sheet.cols = len(mapping)
        lengths = []
        cleared = []
        for index, (dataset_name, input_column) in enumerate(mapping):
            values = _series_values_without_trailing_missing(df[input_column])
            if not values:
                # A deliberately empty column clears an unused template curve.
                # Origin still needs one missing value in the transfer sheet so
                # the loose dataset assignment is executed.
                cleared.append(dataset_name)
                transfer_values = [float("nan")]
            else:
                transfer_values = values
            sheet.from_list(index, transfer_values, lname=dataset_name)
            lengths.append(len(values))
        for index, (dataset_name, _input_column) in enumerate(mapping):
            src = f"__cdx_is_{index + 1}"
            dst = f"__cdx_id_{index + 1}"
            sheet.lt_exec(
                f"range {src}=col({index + 1});"
                f"range {dst}={LOOSE_DATASET_PREFIX}{dataset_name};"
                f"{dst}.SetSize({max(1, lengths[index])});"
                f"{dst}={src};"
                f"{dst}.SetSize({max(1, lengths[index])});"
                f"del -ra {src}; del -ra {dst};"
            )
        return {
            "rows": max(lengths, default=0),
            "columns": len(mapping),
            "data_mode": "loose_dataset",
            "datasets_updated": [name for name, _ in mapping],
            "datasets_cleared": cleared,
            "template_columns": len(dataset_names),
        }
    finally:
        book.destroy()


def force_recalculation(op) -> None:
    # Wait until all pending operations/analysis descendants are updated.
    op.lt_exec("run -p au;")


def replace_graph_text(graph, replacements: dict[str, str] | None) -> list[dict[str, Any]]:
    """Replace existing graph text while preserving each object's formatting."""
    if not replacements:
        return []
    wanted = [
        (str(old), str(new))
        for old, new in replacements.items()
        if str(old).strip() and str(new).strip()
    ]
    wanted.sort(key=lambda item: len(item[0]), reverse=True)
    applied: list[dict[str, Any]] = []
    for layer_index, layer in enumerate(graph, 1):
        try:
            objects = layer.obj.GraphObjects
            count = int(objects.Count)
        except Exception:
            continue
        try:
            graph_objects = list(objects)
        except (TypeError, AttributeError):
            # Small mocks and a few older COM wrappers expose numeric access
            # instead of OriginExt's normal iterator.
            graph_objects = []
            for object_index in range(count):
                graph_object = None
                for accessor in (
                    lambda i=object_index: objects(i),
                    lambda i=object_index: objects.Item(i),
                    lambda i=object_index: objects(i + 1),
                    lambda i=object_index: objects.Item(i + 1),
                ):
                    try:
                        graph_object = accessor()
                        if graph_object is not None:
                            break
                    except Exception:
                        continue
                if graph_object is not None:
                    graph_objects.append(graph_object)
        for object_index, graph_object in enumerate(graph_objects):
            try:
                current = str(graph_object.Text)
            except Exception:
                continue
            for old, new in wanted:
                if old.casefold() not in current.casefold():
                    continue
                updated = re.sub(re.escape(old), lambda _match: new, current, count=1, flags=re.IGNORECASE)
                old_geometry = None
                try:
                    old_left = float(graph_object.Left)
                    old_width = float(graph_object.Width)
                    old_geometry = (old_left, old_width, old_left + old_width)
                except Exception:
                    pass
                try:
                    graph_object.Text = updated
                except Exception:
                    continue
                placement_adjustment = None
                if old_geometry is not None:
                    try:
                        new_width = float(graph_object.Width)
                        desired_left = max(0.0, old_geometry[2] - new_width)
                        if abs(desired_left - old_geometry[0]) > 0.5:
                            graph_object.Left = int(round(desired_left))
                            placement_adjustment = {
                                "old_left": old_geometry[0],
                                "old_width": old_geometry[1],
                                "new_left": desired_left,
                                "new_width": new_width,
                                "right_edge_preserved": abs(
                                    desired_left + new_width - old_geometry[2]
                                ) <= 1.0,
                            }
                    except Exception:
                        pass
                change = {
                    "layer": layer_index,
                    "object": object_index + 1,
                    "from": current,
                    "to": updated,
                }
                if placement_adjustment is not None:
                    change["placement_adjustment"] = placement_adjustment
                applied.append(change)
                break
    return applied


def update_named_graph_text(
    graph,
    updates: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Replace complete text in explicitly named Origin graph objects."""
    if not updates:
        return []
    applied: list[dict[str, Any]] = []
    for update in updates:
        try:
            layer_index = int(update["layer"])
            object_name = str(update["object"])
            new_text = str(update["text"])
            layer = graph[layer_index - 1]
            graph_object = layer.obj.GraphObjects(object_name)
        except Exception as exc:
            raise PlotterError(
                f"找不到第 {update.get('layer')} 層文字物件 "
                f"{update.get('object')!r}。"
            ) from exc
        try:
            old_text = str(graph_object.Text)
        except Exception as exc:
            raise PlotterError(
                f"無法讀取第 {layer_index} 層文字物件 {object_name!r}。"
            ) from exc

        preserve_right_edge = bool(update.get("preserve_right_edge", False))
        old_geometry = None
        if preserve_right_edge:
            try:
                old_left = float(graph_object.Left)
                old_width = float(graph_object.Width)
                old_geometry = (old_left, old_width, old_left + old_width)
            except Exception:
                pass
        try:
            graph_object.Text = new_text
        except Exception as exc:
            raise PlotterError(
                f"無法更新第 {layer_index} 層文字物件 {object_name!r}。"
            ) from exc

        change: dict[str, Any] = {
            "layer": layer_index,
            "object": object_name,
            "from": old_text,
            "to": new_text,
        }
        if old_geometry is not None:
            try:
                new_width = float(graph_object.Width)
                desired_left = max(0.0, old_geometry[2] - new_width)
                if abs(desired_left - old_geometry[0]) > 0.5:
                    graph_object.Left = int(round(desired_left))
                change["placement_adjustment"] = {
                    "old_left": old_geometry[0],
                    "old_width": old_geometry[1],
                    "new_left": desired_left,
                    "new_width": new_width,
                    "right_edge_preserved": abs(
                        desired_left + new_width - old_geometry[2]
                    ) <= 1.0,
                }
            except Exception:
                pass
        applied.append(change)
    return applied


def graph_axis_profiles(graph) -> dict[int, dict[str, dict[str, Any]]]:
    """Capture template-owned axis limits, tick spacing, and scale type."""
    profiles: dict[int, dict[str, dict[str, Any]]] = {}
    for layer_index, layer in enumerate(graph, 1):
        try:
            if not layer.plot_list():
                continue
        except Exception:
            pass
        axes: dict[str, dict[str, Any]] = {}
        for axis_name in ("x", "y"):
            try:
                axis = layer.axis(axis_name)
                begin, end, step = axis.limits
                axes[axis_name] = {
                    "begin": float(begin),
                    "end": float(end),
                    "step": float(step),
                    "scale": int(axis.scale),
                }
            except Exception:
                continue
        if axes:
            profiles[layer_index] = axes
    return profiles


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def graph_layer_data_extents(op, graph) -> dict[int, dict[str, tuple[float, float]]]:
    """Read pairwise-valid X/Y extents for every plotted data layer.

    Origin ranges can contain X values on rows where Y is missing.  Native
    column min/max would then include points that are not actually plotted, so
    each plot is copied into a temporary two-column sheet and filtered as X/Y
    pairs before its extent contributes to the axis fit.
    """
    sources = graph_plot_sources(op, graph)
    if not sources:
        return {}
    scan = op.new_sheet(lname="CODEX_AXIS_SCAN", hidden=True)
    book = scan.get_book()
    extents: dict[int, dict[str, tuple[float, float]]] = {}
    try:
        scan.cols = 2
        for serial, source in enumerate(sources, 1):
            x_range = source.get("x_range", "")
            y_range = source.get("y_range", "")
            if not x_range or not y_range:
                continue
            scan.clear(0, ncols=2)
            sx, sy = f"__cdx_ax_sx_{serial}", f"__cdx_ax_sy_{serial}"
            dx, dy = f"__cdx_ax_dx_{serial}", f"__cdx_ax_dy_{serial}"
            scan.lt_exec(
                f"range {sx}={x_range}; range {sy}={y_range};"
                f"range {dx}=col(1); range {dy}=col(2);"
                f"{dx}={sx}; {dy}={sy};"
                f"del -ra {sx}; del -ra {sy}; del -ra {dx}; del -ra {dy};"
            )
            xs = scan.to_list(0)
            ys = scan.to_list(1)
            valid_x: list[float] = []
            valid_y: list[float] = []
            for raw_x, raw_y in zip(xs, ys):
                x_value = _finite_float(raw_x)
                y_value = _finite_float(raw_y)
                if x_value is None or y_value is None:
                    continue
                valid_x.append(x_value)
                valid_y.append(y_value)
            if not valid_x:
                continue
            layer_index = int(source["layer"])
            current = extents.setdefault(layer_index, {})
            for axis_name, values in (("x", valid_x), ("y", valid_y)):
                low, high = min(values), max(values)
                if axis_name in current:
                    old_low, old_high = current[axis_name]
                    low, high = min(low, old_low), max(high, old_high)
                current[axis_name] = (low, high)
        return extents
    finally:
        book.destroy()


def _nice_step(value: float) -> float:
    """Return a readable major-tick step close to *value*."""
    if not math.isfinite(value) or value <= 0:
        return value
    exponent = math.floor(math.log10(value))
    power = 10.0 ** exponent
    fraction = value / power
    factors = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0)
    factor = min(factors, key=lambda item: abs(math.log(item / fraction)))
    return factor * power


def _clean_number(value: float) -> float:
    return float(f"{value:.12g}")


def fit_axis_to_template(
    profile: dict[str, Any],
    old_extent: tuple[float, float] | None,
    new_extent: tuple[float, float] | None,
) -> tuple[float, float, float] | None:
    """Fit new data while retaining the template's margins and tick density."""
    if not old_extent or not new_extent:
        return None
    begin = float(profile["begin"])
    end = float(profile["end"])
    template_step = float(profile["step"])
    if not all(math.isfinite(value) for value in (begin, end, template_step)):
        return None
    old_low, old_high = sorted(float(value) for value in old_extent)
    new_low, new_high = sorted(float(value) for value in new_extent)
    old_span = old_high - old_low
    new_span = new_high - new_low
    if old_span <= 0 or new_span <= 0:
        return None

    # Unchanged input should retain the exact template axes, not merely an
    # equivalent rounded range.
    tolerance = max(1.0, abs(old_low), abs(old_high)) * 1e-10
    if abs(old_low - new_low) <= tolerance and abs(old_high - new_high) <= tolerance:
        return begin, end, template_step

    template_low, template_high = sorted((begin, end))
    # Negative "margins" are meaningful: some templates intentionally show
    # only one region from a worksheet that also stores unrelated data farther
    # down the same X/Y columns.  Preserve that crop as an affine proportion
    # instead of expanding to every stored value.
    lower_margin = (old_low - template_low) / old_span
    upper_margin = (template_high - old_high) / old_span
    desired_low = new_low - lower_margin * new_span
    desired_high = new_high + upper_margin * new_span
    desired_span = desired_high - desired_low
    if desired_span <= 0:
        return None

    # Non-linear scales are safely rescaled by Origin itself.  The affine
    # template-margin model below is intentionally limited to linear axes.
    if int(profile.get("scale", 1)) != 1:
        return None
    if template_step == 0 or begin == end:
        mapped_begin = desired_high if begin > end else desired_low
        mapped_end = desired_low if begin > end else desired_high
        return _clean_number(mapped_begin), _clean_number(mapped_end), template_step

    target_intervals = abs((end - begin) / template_step)
    target_intervals = min(10.0, max(2.0, target_intervals))
    step = _nice_step(desired_span / target_intervals)
    if not math.isfinite(step) or step <= 0:
        return None
    epsilon = step * 1e-10
    snapped_low = math.floor((desired_low + epsilon) / step) * step
    snapped_high = math.ceil((desired_high - epsilon) / step) * step
    if snapped_high <= snapped_low:
        snapped_high = snapped_low + step
    reversed_axis = begin > end
    fitted_begin = snapped_high if reversed_axis else snapped_low
    fitted_end = snapped_low if reversed_axis else snapped_high
    fitted_step = -step if reversed_axis or template_step < 0 else step
    return tuple(_clean_number(value) for value in (fitted_begin, fitted_end, fitted_step))


def apply_template_axis_fit(
    graph,
    profiles: dict[int, dict[str, dict[str, Any]]],
    old_extents: dict[int, dict[str, tuple[float, float]]],
    new_extents: dict[int, dict[str, tuple[float, float]]],
) -> list[dict[str, Any]]:
    """Apply template-aware automatic limits after Origin's native rescale."""
    adjustments: list[dict[str, Any]] = []
    for layer_index, axes in profiles.items():
        try:
            layer = graph[layer_index - 1]
        except Exception:
            continue
        row: dict[str, Any] = {"layer": layer_index}
        for axis_name, profile in axes.items():
            limits = fit_axis_to_template(
                profile,
                old_extents.get(layer_index, {}).get(axis_name),
                new_extents.get(layer_index, {}).get(axis_name),
            )
            if limits is None:
                continue
            try:
                layer.axis(axis_name).set_limits(*limits)
                row[axis_name] = list(limits)
            except Exception:
                continue
        if len(row) > 1:
            adjustments.append(row)
    return adjustments


def rescale_graph(graph) -> list[int]:
    """Use Origin's native auto-rescale on every layer that has data plots.

    Empty overlay/annotation layers are deliberately left alone so their
    template geometry and linked-axis setup are not disturbed.
    """
    rescaled_layers = []
    for layer_index, layer in enumerate(graph, 1):
        try:
            has_plots = bool(layer.plot_list())
        except Exception:
            has_plots = True
        if not has_plots:
            continue
        layer.rescale()
        rescaled_layers.append(layer_index)
    return rescaled_layers


def apply_fixed_axes(
    graph,
    fixed_axes: dict[str, Sequence[float]] | None,
) -> list[dict[str, Any]]:
    """Apply template-declared absolute limits after all automatic fitting."""
    if not fixed_axes:
        return []
    adjustments: list[dict[str, Any]] = []
    for layer_index, layer in enumerate(graph, 1):
        try:
            has_plots = bool(layer.plot_list())
        except Exception:
            has_plots = True
        if not has_plots:
            continue
        row: dict[str, Any] = {"layer": layer_index}
        for axis_name, raw_limits in fixed_axes.items():
            limits = [float(value) for value in raw_limits]
            if len(limits) not in {2, 3}:
                raise PlotterError(
                    f"fixed_axes.{axis_name} 必須是 [起點, 終點] 或 [起點, 終點, 間距]。"
                )
            try:
                layer.axis(str(axis_name)).set_limits(*limits)
                row[str(axis_name)] = limits
            except Exception as exc:
                raise PlotterError(
                    f"無法固定第 {layer_index} 層 {axis_name} 軸。"
                ) from exc
        if len(row) > 1:
            adjustments.append(row)
    return adjustments


def apply_output_style(graph) -> dict[str, Any]:
    """Use explicit opaque white fills and bold text for every template."""
    white = 18  # Origin's White index; 0 means transparent.
    graph.lt_exec("page.basecolor=color(white);page.gradcolor=color(white);")
    graph.obj.GradCntrl = 0
    labels = 0
    layers = 0
    for layer in graph:
        layer.set_int("color", white)
        for axis in ("x", "x2", "y", "y2", "z", "z2"):
            layer.set_int(f"{axis}.label.bold", 1)
        for obj in layer.obj.GraphObjects:
            text = str(obj.Text)
            if not text.strip():
                continue
            # Native Origin formatting retains font, color, symbols and units.
            lines = text.splitlines(keepends=True)
            bold_lines = []
            for line in lines:
                content = line.rstrip("\r\n")
                ending = line[len(content):]
                if content.strip() and not (content.startswith(r"\b(") and content.endswith(")")):
                    content = r"\b(" + content + ")"
                bold_lines.append(content + ending)
            updated = "".join(bold_lines)
            if updated != text:
                old_right = float(obj.Left) + float(obj.Width)
                obj.Text = updated
                if str(obj.Name).upper() not in {"XB", "XT", "YL", "YR", "ZF", "ZB"}:
                    obj.Left = int(round(max(0.0, old_right - float(obj.Width))))
            labels += 1
        layers += 1
    return {"page_background": "opaque_white", "layer_background": "opaque_white",
            "text": "bold", "layers": layers, "text_objects": labels}


def export_graph(graph, output: Path, width: int) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    if not suffix:
        output = output.with_suffix(".png")
        suffix = ".png"
    image_type = suffix.lstrip(".")
    if image_type not in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "emf", "svg", "pdf"}:
        raise PlotterError(f"Origin 圖片輸出格式不支援：{suffix}")
    result = graph.save_fig(
        str(output),
        type="jpg" if image_type == "jpeg" else image_type,
        replace=True,
        width=width if image_type not in {"emf", "svg", "pdf"} else 0,
    )
    final = Path(result) if result else output
    if not final.is_file() and not output.is_file():
        raise PlotterError(f"Origin 匯出失敗：{output}")
    return final if final.is_file() else output


@dataclass
class RenderJob:
    template: str
    data: Path
    output: Path
    sheet: str = "auto"
    graph: str = "auto"
    input_sheet: str | int | None = None
    start_col: str | int = 0
    header_mode: str = "keep"
    axis_mode: str = "auto"
    width: int = 2400
    allow_extra_columns: bool = False
    no_header: bool = False
    save_project: Path | None = None
    text_replacements: dict[str, str] | None = None
    named_text_updates: list[dict[str, Any]] | None = None
    template_axis_fit: bool = True


def render_job(
    op,
    job: RenderJob,
    *,
    template_dir: Path,
    config: dict[str, Any],
    attach: bool,
) -> dict[str, Any]:
    template_path, template_cfg = resolve_template(job.template, template_dir, config)
    data_path = job.data.expanduser().resolve()
    if not data_path.is_file():
        raise PlotterError(f"資料檔不存在：{data_path}")
    output = job.output.expanduser().resolve()
    project_only = output.suffix.lower() == ".opju"
    save_project = output if project_only else job.save_project
    if job.axis_mode not in {"auto", "rescale", "template"}:
        raise PlotterError(
            f"不支援的 axis_mode：{job.axis_mode!r}；請用 auto、rescale 或 template。"
        )

    project_to_open = template_path
    readonly = True
    if save_project:
        saved = save_project.expanduser().resolve()
        if saved == template_path:
            raise PlotterError("輸出 OPJU 不可覆寫原始模板。")
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, saved)
        project_to_open = saved
        readonly = False

    open_project(op, project_to_open, readonly=readonly, attach=attach)
    graphs = collect_graphs(op)
    graph_selector = job.graph or template_cfg.get("graph", "auto")
    graph = select_graph(graphs, graph_selector)
    for layer_index in template_cfg.get("hidden_layers", []):
        graph[int(layer_index) - 1].set_int("show", 0)
    axis_profiles: dict[int, dict[str, dict[str, Any]]] = {}
    old_extents: dict[int, dict[str, tuple[float, float]]] = {}
    axis_scan_warning = None
    if job.axis_mode in {"auto", "rescale"}:
        axis_profiles = graph_axis_profiles(graph)
        try:
            old_extents = graph_layer_data_extents(op, graph)
        except Exception as exc:
            # Native Origin rescaling remains a safe fallback if a rare plot
            # type cannot be materialized as X/Y ranges.
            axis_scan_warning = f"{type(exc).__name__}: {exc}"
    sheets = collect_worksheets(op)
    sheet_selector = job.sheet or template_cfg.get("sheet", "auto")
    df = read_table(data_path, job.input_sheet, job.no_header)
    if sheets:
        book, sheet = select_sheet(sheets, sheet_selector, graph)
        write_info = replace_worksheet_data(
            sheet,
            df,
            start_col=job.start_col,
            header_mode=job.header_mode,
            allow_extra_columns=job.allow_extra_columns,
        )
        data_source = qualified_sheet_name(book, sheet)
        write_info["data_mode"] = "worksheet"
    else:
        if sheet_selector and str(sheet_selector).lower() != "auto":
            raise PlotterError(
                "此 PPT 內嵌 OPJU 使用 loose datasets，沒有可指定的 worksheet。"
            )
        dataset_names = graph_loose_dataset_names(op, graph)
        write_info = replace_loose_dataset_data(
            op,
            df,
            dataset_names,
            allow_extra_columns=job.allow_extra_columns,
        )
        data_source = "[??] loose datasets"
    force_recalculation(op)
    text_replacements_applied = replace_graph_text(graph, job.text_replacements)
    named_text_updates_applied = update_named_graph_text(
        graph, list(template_cfg.get("graph_text_updates", [])) + list(job.named_text_updates or [])
    )
    rescaled_layers = []
    axis_adjustments: list[dict[str, Any]] = []
    if job.axis_mode in {"auto", "rescale"}:
        new_extents: dict[int, dict[str, tuple[float, float]]] = {}
        try:
            new_extents = graph_layer_data_extents(op, graph)
        except Exception as exc:
            if axis_scan_warning is None:
                axis_scan_warning = f"{type(exc).__name__}: {exc}"
        rescaled_layers = rescale_graph(graph)
        if job.template_axis_fit:
            axis_adjustments = apply_template_axis_fit(
                graph, axis_profiles, old_extents, new_extents
            )
    fixed_axis_adjustments = apply_fixed_axes(
        graph, template_cfg.get("fixed_axes")
    )
    output_style = apply_output_style(graph)
    exported = output if project_only else export_graph(graph, output, job.width)
    if save_project:
        if not op.save(str(project_to_open)):
            raise PlotterError(f"更新後 OPJU 儲存失敗：{project_to_open}")
    return {
        "template_id": template_cfg.get("id", template_path.stem),
        "template": str(template_path),
        "data": str(data_path),
        "worksheet": data_source,
        "graph": page_name(graph),
        "axis_mode": job.axis_mode,
        "axis_rescaled": bool(rescaled_layers),
        "rescaled_layers": rescaled_layers,
        "axis_strategy": (
            "fixed_template_axes_after_auto_fit"
            if fixed_axis_adjustments and rescaled_layers else
            "fixed_template_axes"
            if fixed_axis_adjustments else
            "origin_rescale_plus_template_fit"
            if axis_adjustments else
            "origin_native_rescale" if rescaled_layers else
            "template_locked"
        ),
        "axis_adjustments": axis_adjustments,
        "fixed_axis_adjustments": fixed_axis_adjustments,
        "output_style": output_style,
        "axis_scan_warning": axis_scan_warning,
        "text_replacements_applied": text_replacements_applied,
        "named_text_updates_applied": named_text_updates_applied,
        "header_mode": job.header_mode,
        "output": str(exported),
        "saved_project": str(project_to_open) if save_project else None,
        **write_info,
    }


def export_dataframe(df, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".xlsx", ".xlsm"}:
        df.to_excel(output, index=False)
    else:
        if not output.suffix:
            output = output.with_suffix(".csv")
        df.to_csv(output, index=False, encoding="utf-8-sig")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", value).strip(" .")
    return cleaned or "sheet"


def cmd_list(args) -> int:
    config = load_config(args.config)
    template_dir = args.template_dir.resolve()
    print("ID\t投影片\tOPJU\t狀態")
    for row in template_rows(config):
        path = template_dir / row["filename"]
        print(f"{row['id']}\t{row.get('slide', '')}\t{row['filename']}\t"
              f"{'OK' if path.is_file() else 'MISSING'}")
    return 0


def cmd_doctor(args) -> int:
    config = load_config(args.config)
    failures = 0
    checks = []
    origin_exe = Path(args.origin_exe)
    checks.append(("Origin 執行檔", origin_exe.is_file(), str(origin_exe)))
    checks.append(("originpro 套件", importlib.util.find_spec("originpro") is not None,
                   "pip install originpro"))
    checks.append(("pandas 套件", importlib.util.find_spec("pandas") is not None,
                   "pip install pandas"))
    checks.append(("openpyxl 套件", importlib.util.find_spec("openpyxl") is not None,
                   "pip install openpyxl"))
    for row in template_rows(config):
        path = args.template_dir / row["filename"]
        checks.append((f"模板 {row['id']}", path.is_file(), str(path)))
    try:
        raw_count = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "@(Get-Process Origin64 -ErrorAction SilentlyContinue).Count",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        ).stdout.strip()
        count = int(raw_count) if raw_count.isdigit() else -1
    except Exception:
        count = -1
    for label, ok, detail in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")
        failures += 0 if ok else 1
    if count >= 0:
        print(f"[INFO] 目前 Origin64.exe 程序數：{count}")
        if count:
            print("       若自動化卡住，請先在 Origin 儲存工作後正常關閉所有 Origin 視窗/程序。")
    return 1 if failures else 0


def cmd_inspect(args) -> int:
    config = load_config(args.config)
    requested = [row["id"] for row in template_rows(config)] if args.all else [args.template]
    inventories = []
    with OriginSession(hidden=args.hidden, attach=args.attach,
                       keep_open=args.keep_origin_open) as op:
        for value in requested:
            path, row = resolve_template(value, args.template_dir, config)
            open_project(op, path, readonly=True, attach=args.attach)
            inv = inventory_current_project(op, path.name)
            graphs = collect_graphs(op)
            sheets = collect_worksheets(op)
            if graphs and sheets:
                graph = select_graph(graphs, row.get("graph", "auto"))
                book, sheet = select_sheet(sheets, row.get("sheet", "auto"), graph)
                inv["auto_selection"] = {
                    "worksheet": qualified_sheet_name(book, sheet),
                    "graph": page_name(graph),
                }
            elif graphs:
                graph = select_graph(graphs, row.get("graph", "auto"))
                inv["auto_selection"] = {
                    "worksheet": "[??] loose datasets",
                    "graph": page_name(graph),
                    "datasets": graph_loose_dataset_names(op, graph),
                }
            inventories.append(inv)
            print(f"已檢查 {path.name}")
    payload: Any = inventories if args.all else inventories[0]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"結構檔：{args.out.resolve()}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_export_input(args) -> int:
    config = load_config(args.config)
    path, row = resolve_template(args.template, args.template_dir, config)
    with OriginSession(hidden=args.hidden, attach=args.attach,
                       keep_open=args.keep_origin_open) as op:
        open_project(op, path, readonly=True, attach=args.attach)
        graph = select_graph(collect_graphs(op), row.get("graph", "auto"))
        sheets = collect_worksheets(op)
        if not sheets:
            dataset_names = graph_loose_dataset_names(op, graph)
            df = materialize_loose_datasets(op, dataset_names)
            if args.all_sheets:
                out_dir = args.out.resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                output = out_dir / safe_filename(
                    f"{page_name(graph)}__loose_datasets"
                )
                output = output.with_suffix(".csv")
            else:
                output = args.out.resolve()
            export_dataframe(df, output)
            print(
                f"已匯出 {page_name(graph)} 的 loose datasets "
                f"({', '.join(dataset_names)}) -> {output}"
            )
        elif args.all_sheets:
            out_dir = args.out.resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            for book, sheet in sheets:
                df = sheet.to_df()
                name = safe_filename(f"{page_name(book)}__{page_name(sheet)}") + ".csv"
                export_dataframe(df, out_dir / name)
                print(f"已匯出 {qualified_sheet_name(book, sheet)} -> {out_dir / name}")
        else:
            book, sheet = select_sheet(sheets, args.sheet or row.get("sheet", "auto"), graph)
            df = sheet.to_df()
            export_dataframe(df, args.out.resolve())
            print(f"已匯出 {qualified_sheet_name(book, sheet)} -> {args.out.resolve()}")
    return 0


def job_from_args(args) -> RenderJob:
    return RenderJob(
        template=args.template,
        data=args.data,
        output=args.out,
        sheet=args.sheet,
        graph=args.graph,
        input_sheet=args.input_sheet,
        start_col=args.start_col,
        header_mode=args.header_mode,
        axis_mode=args.axis_mode,
        width=args.width,
        allow_extra_columns=args.allow_extra_columns,
        no_header=args.no_header,
        save_project=args.save_project,
    )


def cmd_render(args) -> int:
    config = load_config(args.config)
    with OriginSession(hidden=args.hidden, attach=args.attach,
                       keep_open=args.keep_origin_open) as op:
        result = render_job(
            op,
            job_from_args(args),
            template_dir=args.template_dir,
            config=config,
            attach=args.attach,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _relative_path(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def cmd_batch(args) -> int:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    config = load_config(args.config)
    jobs = []
    for raw in manifest.get("jobs", []):
        jobs.append(RenderJob(
            template=raw["template"],
            data=_relative_path(base, raw["data"]),  # type: ignore[arg-type]
            output=_relative_path(base, raw["out"]),  # type: ignore[arg-type]
            sheet=raw.get("sheet", "auto"),
            graph=raw.get("graph", "auto"),
            input_sheet=raw.get("input_sheet"),
            start_col=raw.get("start_col", 0),
            header_mode=raw.get("header_mode", "keep"),
            axis_mode=raw.get("axis_mode", "auto"),
            width=int(raw.get("width", 2400)),
            allow_extra_columns=bool(raw.get("allow_extra_columns", False)),
            no_header=bool(raw.get("no_header", False)),
            save_project=_relative_path(base, raw.get("save_project")),
        ))
    if not jobs:
        raise PlotterError("批次 manifest 沒有 jobs。")
    results = []
    with OriginSession(hidden=args.hidden, attach=args.attach,
                       keep_open=args.keep_origin_open) as op:
        for index, job in enumerate(jobs, 1):
            print(f"[{index}/{len(jobs)}] {job.template}", flush=True)
            results.append(render_job(
                op,
                job,
                template_dir=args.template_dir,
                config=config,
                attach=args.attach,
            ))
    report = args.report or manifest_path.with_name(manifest_path.stem + ".report.json")
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"批次報告：{report.resolve()}")
    return 0


def add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)


def add_session_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hidden", action="store_true", help="隱藏 Origin（第一次請勿使用）")
    parser.add_argument("--attach", action="store_true", help="連到已開啟的 Origin")
    parser.add_argument(
        "--keep-origin-open", action="store_true",
        help="完成後只中斷連線，不關閉 Origin",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="以 OPJU 為母版，用輸入資料產生高度相似的 Origin 圖。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出 11 種圖型")
    add_paths(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("doctor", help="檢查 Origin、Python 套件與模板")
    add_paths(p)
    p.add_argument(
        "--origin-exe",
        default=r"C:\Program Files\OriginLab\Origin2021\Origin64.exe",
    )
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("inspect", help="列出 OPJU 工作表、欄位、圖頁與曲線")
    add_paths(p)
    add_session_flags(p)
    p.add_argument("--template", help="圖型 ID 或 OPJU 路徑")
    p.add_argument("--all", action="store_true", help="檢查全部模板")
    p.add_argument("--out", type=Path, help="輸出 JSON 結構檔")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("export-input", help="匯出模板目前資料，作為輸入格式範本")
    add_paths(p)
    add_session_flags(p)
    p.add_argument("--template", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sheet", default="auto")
    p.add_argument("--all-sheets", action="store_true")
    p.set_defaults(func=cmd_export_input)

    p = sub.add_parser("render", help="替換資料並由 Origin 匯出圖片")
    add_paths(p)
    add_session_flags(p)
    p.add_argument("--template", required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sheet", default="auto", help="auto 或 [Book]Sheet")
    p.add_argument("--graph", default="auto", help="auto、圖頁短名或長名")
    p.add_argument("--input-sheet", help="Excel 工作表名稱或 0-based 索引")
    p.add_argument("--start-col", default=0, help="0-based 索引、短名或長名")
    p.add_argument("--header-mode", choices=("keep", "replace"), default="keep")
    p.add_argument(
        "--axis-mode",
        choices=("auto", "template", "rescale"),
        default="auto",
        help=(
            "auto（預設）由 Origin 自動縮放所有有資料的圖層；"
            "template 保留母版範圍；rescale 是 auto 的相容別名"
        ),
    )
    p.add_argument("--width", type=int, default=2400, help="PNG/JPG 寬度（像素）")
    p.add_argument("--allow-extra-columns", action="store_true")
    p.add_argument("--no-header", action="store_true")
    p.add_argument("--save-project", type=Path, help="另存更新後 OPJU，絕不覆寫模板")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("batch", help="依 JSON manifest 批次出圖")
    add_paths(p)
    add_session_flags(p)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--report", type=Path)
    p.set_defaults(func=cmd_batch)
    return parser


def validate_args(args) -> None:
    if getattr(args, "command", None) == "inspect" and not args.all and not args.template:
        raise PlotterError("inspect 必須指定 --template，或使用 --all。")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        return int(args.func(args))
    except PlotterError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"未預期錯誤：{exc}", file=sys.stderr)
        if os.environ.get("ORIGIN_PLOTTER_DEBUG") == "1":
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

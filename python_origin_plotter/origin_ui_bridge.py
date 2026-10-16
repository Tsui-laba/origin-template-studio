#!/usr/bin/env python3
"""Bridge between the Windows UI's slot manifest and origin_plotter.py."""

from __future__ import annotations

import argparse
import json
import os
import math
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import origin_plotter as plotter  # noqa: E402


def _columns_look_numeric(columns: Sequence[Any]) -> bool:
    if not columns:
        return False
    numeric = 0
    for column in columns:
        try:
            float(str(column).strip())
            numeric += 1
        except (TypeError, ValueError):
            pass
    return numeric == len(columns)


def read_rigaku_xrd(path: Path):
    """Read Rigaku RAS_RAW text by content, never by fixed header line numbers."""
    if path.suffix.lower() not in {".txt", ".dat", ".ras"}:
        return None
    text = None
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None or not re.search(r'^\*FILE_TYPE\s+"RAS_RAW"', text, re.MULTILINE):
        return None
    import pandas as pd
    points = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", "#")):
            continue
        fields = stripped.split()
        try:
            if len(fields) != 2:
                raise ValueError("expected two columns")
            point = tuple(float(value) for value in fields)
            if not all(math.isfinite(value) for value in point):
                raise ValueError("non-finite value")
        except ValueError as exc:
            raise plotter.PlotterError(f"XRD 第 {line_number} 行不是有效的角度／強度資料：{path}") from exc
        points.append(point)
    count = re.search(r'^\*MEAS_DATA_COUNT\s+"(\d+)"', text, re.MULTILINE)
    if not points or (count and len(points) != int(count.group(1))):
        raise plotter.PlotterError(f"XRD 資料點數與檔頭不符或沒有數據：{path}（讀取 {len(points)} 點）")
    return pd.DataFrame(points, columns=["TwoTheta", "Intensity"])


def read_pdf_reference(path: Path):
    """Read exported PDF peak tables: 2-Theta, d(A), I(f), then hkl."""
    if path.suffix.lower() not in {".txt", ".dat"}:
        return None
    text = None
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    lines = text.splitlines()
    header = next((index for index, line in enumerate(lines)
                   if re.match(r'^\s*2-Theta\s+d\(A\)\s+I\(f\)', line, re.IGNORECASE)), None)
    if header is None:
        return None
    import pandas as pd
    points = []
    for index, line in enumerate(lines[header + 1:], header + 2):
        if not line.strip():
            continue
        fields = line.split()
        try:
            angle, spacing, intensity = map(float, fields[:3])
            if not all(math.isfinite(value) for value in (angle, spacing, intensity)):
                raise ValueError("non-finite peak")
            if not 0 < angle < 180 or spacing <= 0 or intensity < 0:
                raise ValueError("invalid peak")
        except ValueError as exc:
            raise plotter.PlotterError(f"參考圖譜第 {index} 行無法讀取 2-Theta／I(f)：{path}") from exc
        points.append((angle, intensity))
    if not points:
        raise plotter.PlotterError(f"參考圖譜沒有峰位資料：{path}")
    return pd.DataFrame(points, columns=["TwoTheta", "Intensity"])


def read_numeric_source(path: Path, required_columns: int) -> dict[str, Any]:
    """Read the first N usable numeric columns without collapsing pairwise gaps."""
    import pandas as pd

    frame = read_rigaku_xrd(path)
    if frame is None:
        frame = read_pdf_reference(path)
    if frame is None:
        frame = plotter.read_table(path, None, False)
        if _columns_look_numeric(list(frame.columns)):
            frame = plotter.read_table(path, None, True)

    numeric: list[tuple[str, Any]] = []
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().any():
            numeric.append((str(column), values))
    if len(numeric) < required_columns:
        raise plotter.PlotterError(
            f"資料檔至少需要 {required_columns} 個可用數值欄：{path}"
        )

    selected = numeric[:required_columns]
    last_valid = max(
        int(values.last_valid_index())
        for _name, values in selected
        if values.last_valid_index() is not None
    )
    values = [
        series.iloc[: last_valid + 1].reset_index(drop=True)
        for _name, series in selected
    ]
    return {
        "path": str(path),
        "source_columns": [name for name, _series in selected],
        "rows": max(len(series) for series in values),
        "values": values,
    }


def read_xy_source(path: Path) -> dict[str, Any]:
    """Backward-compatible X/Y reader used by older integrations and tests."""
    source = read_numeric_source(path, 2)
    x_values, y_values = source["values"]
    valid = x_values.notna() & y_values.notna()
    source["x"] = x_values.loc[valid].reset_index(drop=True)
    source["y"] = y_values.loc[valid].reset_index(drop=True)
    source["rows"] = int(valid.sum())
    if source["rows"] == 0:
        raise plotter.PlotterError(f"資料檔沒有成對的 X/Y 數值：{path}")
    return source


def template_config(template_id: str, config: dict[str, Any]) -> dict[str, Any]:
    target = plotter.normalize_id(template_id)
    for row in plotter.template_rows(config):
        if plotter.normalize_id(str(row.get("id", ""))) == target:
            return row
    raise plotter.PlotterError(f"找不到 UI 模板設定：{template_id}")


def _active_slot_count(manifest: dict[str, Any], row: dict[str, Any]) -> int:
    slots = list(row.get("ui_slots", []))
    if not slots:
        return 0
    if not row.get("ui_variable", False):
        return len(slots)
    minimum = max(1, int(row.get("ui_min_slots", 1)))
    default = int(row.get("ui_default_slots", len(slots)))
    try:
        count = int(manifest.get("slot_count", default))
    except (TypeError, ValueError) as exc:
        raise plotter.PlotterError("合併資料數量必須是整數。") from exc
    if count < minimum or count > len(slots):
        raise plotter.PlotterError(
            f"此圖可合併 {minimum}–{len(slots)} 份資料，目前指定 {count} 份。"
        )
    return count


def _interpolate_to_base_x(base_x, source_x, source_y):
    import numpy as np
    import pandas as pd

    base = np.asarray(base_x, dtype=float)
    x = np.asarray(source_x, dtype=float)
    y = np.asarray(source_y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]
    if x.size == 0:
        return pd.Series([float("nan")] * len(base))

    order = np.argsort(x, kind="mergesort")
    x = x[order]
    y = y[order]
    unique_x, unique_indices = np.unique(x, return_index=True)
    unique_y = y[unique_indices]
    if unique_x.size == 1:
        result = np.full(base.shape, np.nan, dtype=float)
        result[np.isclose(base, unique_x[0])] = unique_y[0]
    else:
        result = np.interp(base, unique_x, unique_y, left=np.nan, right=np.nan)
    return pd.Series(result)


def _format_named_text(target: dict[str, Any], name: str) -> dict[str, Any]:
    return {
        "layer": int(target["layer"]),
        "object": str(target["object"]),
        "text": str(target.get("format", "{name}")).format(name=name),
        "preserve_right_edge": bool(target.get("preserve_right_edge", False)),
    }


def prepare_battery_input(manifest, row, output):
    """Extract three cycles from one channel; signs determine charge/discharge."""
    import pandas as pd
    slots = manifest.get("slots", [])
    if len(slots) != 1 or not slots[0].get("data"):
        raise plotter.PlotterError("充放電請選擇一個含 Current、Voltage、Capacity、Cycle_Index 的資料檔。")
    path = Path(slots[0]["data"]).expanduser().resolve()
    try:
        electrode = float(manifest["electrode_weight_mg"])
        foil = float(manifest["foil_weight_mg"])
    except (KeyError, ValueError, TypeError) as exc:
        raise plotter.PlotterError("請輸入極片重量及鋁箔重量（mg）。") from exc
    if not all(math.isfinite(v) for v in (electrode, foil)) or foil < 0 or electrode <= foil:
        raise plotter.PlotterError("極片重量必須大於鋁箔重量，且鋁箔重量不可為負。")
    mass_g = (electrode - foil) * 0.8 / 1000.0
    unit = str(manifest.get("capacity_unit", "Ah"))
    if unit not in {"Ah", "mAh"}:
        raise plotter.PlotterError("容量單位請選 Ah 或 mAh。")
    factor = 1000.0 if unit == "Ah" else 1.0
    sheet = manifest.get("excel_sheet") or 0
    frame = plotter.read_table(path, sheet, False)
    required = ["Cycle_Index", "Current", "Voltage", "Charge_Capacity", "Discharge_Capacity"]
    frame.columns = [str(c).strip() for c in frame.columns]
    missing = [c for c in required if c not in frame]
    if missing:
        raise plotter.PlotterError("充放電資料缺少欄位：" + ", ".join(missing))
    for c in required:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    if frame[required].isna().any().any():
        raise plotter.PlotterError("充放電必要欄位含空白或非數字，請檢查資料。")
    if not frame[required].map(math.isfinite).all().all():
        raise plotter.PlotterError("充放電資料含無限值。")
    active = frame.loc[frame.Current != 0]
    cycles = list(dict.fromkeys(active.Cycle_Index.tolist()))[:3]
    if len(cycles) != 3:
        raise plotter.PlotterError("資料需要至少三圈具有充電及放電的數據。")
    targets = row["ui_compose"]["target_columns"]
    columns = {c: pd.Series(dtype=float) for c in targets}
    summaries = []
    for index, (cycle, cycle_columns) in enumerate(zip(cycles, [("A", "B", "C", "D"), ("I", "J", "K", "L"), ("E", "F", "G", "H")]), 1):
        group = frame.loc[frame.Cycle_Index == cycle]
        charge = group.loc[group.Current > 0]
        discharge = group.loc[group.Current < 0]
        if charge.empty or discharge.empty:
            raise plotter.PlotterError(f"第 {cycle:g} 圈缺少充電或放電數據。")
        for direction, data, capacity, x_col, y_col in [
            ("charge", charge, "Charge_Capacity", cycle_columns[0], cycle_columns[1]),
            ("discharge", discharge, "Discharge_Capacity", cycle_columns[2], cycle_columns[3])]:
            if (data[capacity] < 0).any():
                raise plotter.PlotterError(f"第 {cycle:g} 圈容量為負，無法計算比容量。")
            columns[x_col] = (data[capacity] * factor / mass_g).reset_index(drop=True)
            columns[y_col] = data.Voltage.reset_index(drop=True)
        summaries.append({"cycle": cycle, "charge_points": len(charge),
                          "discharge_points": len(discharge),
                          "rest_points_excluded": int((group.Current == 0).sum()),
                          "charge_capacity_mAh_g": float(charge.Charge_Capacity.max() * factor / mass_g),
                          "discharge_capacity_mAh_g": float(discharge.Discharge_Capacity.max() * factor / mass_g)})
    plotter.export_dataframe(pd.DataFrame(columns, columns=targets), output)
    sample_name = str(slots[0].get("sample_name", "")).strip() or path.stem
    updates = [{"layer": 1, "object": "Legend", "text": sample_name + "\r\n" + r"\l(1) Cycle 1" + "\r\n" + r"\l(5) Cycle 2" + "\r\n" + r"\l(3) Cycle 3"}]
    details = [{"id": "battery_file", "data": str(path), "sheet": sheet,
                "sample_name": sample_name,
                "active_mass_g": mass_g, "electrode_weight_mg": electrode, "foil_weight_mg": foil,
                "capacity_unit": unit, "cycles": summaries,
                "rule": "Current > 0: charge; Current < 0: discharge; Current = 0: excluded",
                "x": "Specific Capacity (mAh/g)", "y": "Voltage (V)"}]
    return output, {}, updates, details


def prepare_slot_input(
    manifest: dict[str, Any],
    config: dict[str, Any],
    output: Path,
) -> tuple[
    Path,
    dict[str, str],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Compose selected files into the exact physical columns used by a template."""
    import pandas as pd

    template_id = str(manifest["template"])
    row = template_config(template_id, config)
    if row.get("ui_battery_cycles"):
        return prepare_battery_input(manifest, row, output)
    configured_slots = list(row.get("ui_slots", []))
    provided = {
        str(item.get("id", "")): item
        for item in manifest.get("slots", [])
    }
    if not configured_slots:
        slots = list(manifest.get("slots", []))
        if not slots or not slots[0].get("data"):
            raise plotter.PlotterError("請選擇輸入資料檔。")
        data_path = Path(slots[0]["data"]).expanduser().resolve()
        sample_name = str(slots[0].get("sample_name", "")).strip() or data_path.stem
        replacements = {"ZSM-5": sample_name} if template_id == "PARTICLE_SIZE" else {}
        return data_path, replacements, [], [{"id": slots[0].get("id", "data"), "data": str(data_path), "sample_name": sample_name}]

    active_count = _active_slot_count(manifest, row)
    active_slots = configured_slots[:active_count]
    compose = dict(row.get("ui_compose", {}))
    target_columns = [str(value) for value in compose.get("target_columns", [])]
    if not target_columns:
        raise plotter.PlotterError(f"{template_id} 缺少 ui_compose.target_columns。")

    columns: dict[str, Any] = {
        target: pd.Series(dtype=float) for target in target_columns
    }
    replacements: dict[str, str] = {}
    named_updates: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    loaded: list[tuple[dict[str, Any], dict[str, Any], str, Path]] = []

    for slot in active_slots:
        slot_id = str(slot["id"])
        selected = provided.get(slot_id, {})
        raw_path = str(selected.get("data", "")).strip()
        if not raw_path:
            raise plotter.PlotterError(f"尚未選擇「{slot['title']}」的資料檔。")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise plotter.PlotterError(f"資料檔不存在：{path}")
        required_columns = int(slot.get("source_columns", 2))
        source = read_numeric_source(path, required_columns)
        name = str(selected.get("sample_name", "")).strip()
        if not name:
            name = path.stem
        loaded.append((slot, source, name, path))

        text_target = slot.get("text_target")
        if name and isinstance(text_target, dict):
            named_updates.append(_format_named_text(text_target, name))
        else:
            label_match = str(slot.get("label_match", "")).strip()
            if name and label_match:
                replacements[label_match] = name

    index_x_target = str(compose.get("index_x_target", "")).strip()
    if index_x_target:
        if index_x_target not in columns:
            raise plotter.PlotterError(
                f"{template_id} 的點序 X 欄 {index_x_target!r} 不在 target_columns。"
            )
        index_start = int(compose.get("index_start", 1))
        max_points = 0
        for slot, source, _name, _path in loaded:
            for mapping in slot.get("targets", []):
                if str(mapping["target"]) == index_x_target:
                    continue
                source_index = int(mapping["source"])
                point_values = pd.to_numeric(
                    source["values"][source_index], errors="coerce"
                )
                max_points = max(max_points, int(point_values.notna().sum()))
        if max_points == 0:
            raise plotter.PlotterError("所選資料沒有可用的 C-rate Y 數值。")
        columns[index_x_target] = pd.Series(
            range(index_start, index_start + max_points), dtype=int
        )

    shared_x_target = str(compose.get("shared_x_target", "")).strip()
    if shared_x_target:
        shared_x_source = int(compose.get("shared_x_source", 0))
        first_values = loaded[0][1]["values"]
        base_x = pd.to_numeric(first_values[shared_x_source], errors="coerce")
        base_x = base_x.loc[base_x.notna()].reset_index(drop=True)
        if base_x.empty:
            raise plotter.PlotterError("第一份資料沒有可用的共用 X 欄。")
        columns[shared_x_target] = base_x

    for slot, source, name, path in loaded:
        targets = list(slot.get("targets", []))
        target_names: list[str] = []
        for mapping in targets:
            source_index = int(mapping["source"])
            target = str(mapping["target"])
            if target not in columns:
                raise plotter.PlotterError(
                    f"{template_id} 的目標欄 {target!r} 不在 target_columns。"
                )
            if index_x_target and target != index_x_target:
                point_values = pd.to_numeric(
                    source["values"][source_index], errors="coerce"
                )
                columns[target] = point_values.loc[
                    point_values.notna()
                ].reset_index(drop=True)
            elif shared_x_target and source_index != int(compose.get("shared_x_source", 0)):
                source_x = source["values"][int(compose.get("shared_x_source", 0))]
                source_y = source["values"][source_index]
                columns[target] = _interpolate_to_base_x(
                    columns[shared_x_target], source_x, source_y
                )
            elif target != shared_x_target:
                columns[target] = source["values"][source_index]
            target_names.append(target)
        details.append({
            "id": str(slot["id"]),
            "title": slot.get("title", slot["id"]),
            "data": str(path),
            "sample_name": name,
            "source_columns": source["source_columns"],
            "rows": source["rows"],
            "point_count": max(
                (
                    int(pd.to_numeric(source["values"][int(mapping["source"])], errors="coerce").notna().sum())
                    for mapping in targets
                    if str(mapping["target"]) != index_x_target
                ),
                default=0,
            ) if index_x_target else source["rows"],
            "target_columns": target_names,
        })

    legend = row.get("ui_legend")
    if isinstance(legend, dict):
        line_templates = list(legend.get("line_templates", []))
        if len(line_templates) < active_count:
            raise plotter.PlotterError(f"{template_id} 的圖例格式數量不足。")
        lines = [
            str(line_templates[index]).format(name=loaded[index][2])
            for index in range(active_count)
        ]
        named_updates.append({
            "layer": int(legend["layer"]),
            "object": str(legend["object"]),
            "text": "\r\n".join(lines),
            "preserve_right_edge": bool(legend.get("preserve_right_edge", False)),
        })

    frame = pd.DataFrame(columns, columns=target_columns)
    plotter.export_dataframe(frame, output)
    return output, replacements, named_updates, details


def run_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    config = plotter.load_config()
    template_id = str(manifest.get("template", "")).strip()
    if not template_id:
        raise plotter.PlotterError("UI job 缺少 template。")
    raw_output = str(manifest.get("output", "")).strip()
    if not raw_output:
        raise plotter.PlotterError("UI job 缺少 output。")
    output = Path(raw_output).expanduser().resolve()
    hidden = bool(manifest.get("hidden", True))
    row = template_config(template_id, config)

    with tempfile.TemporaryDirectory(prefix="origin_plotter_ui_") as temp:
        composed = Path(temp) / f"{template_id}_slots.csv"
        data_path, replacements, named_updates, slot_details = prepare_slot_input(
            manifest, config, composed
        )
        job = plotter.RenderJob(
            template=template_id,
            data=data_path,
            output=output,
            text_replacements=replacements,
            named_text_updates=named_updates,
            template_axis_fit=bool(row.get("ui_template_axis_fit", True)),
        )
        with plotter.OriginSession(
            hidden=hidden,
            attach=False,
            keep_open=False,
        ) as op:
            rendered = plotter.render_job(
                op,
                job,
                template_dir=APP_DIR.parent / "templates",
                config=config,
                attach=False,
            )
    return {
        **rendered,
        "ui_slots": slot_details,
        "ui_slot_count": len(slot_details),
        "requested_text_replacements": replacements,
        "requested_named_text_updates": named_updates,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Origin UI slot bridge")
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_manifest(args.job.expanduser().resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        if __debug__ and os.environ.get("ORIGIN_PLOTTER_DEBUG") == "1":
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

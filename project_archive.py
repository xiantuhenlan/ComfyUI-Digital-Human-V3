"""Portable V3 scenes, prompts and their source media; no generated videos."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
import zipfile

from .storage import ROOT, normalize_state, project_dir, read_state, safe_media


FORMAT = "xiantu-digital-human-v3"


def _records(state):
    for scene in state.get("segments") or []:
        yield from ((item, "refs") for item in scene.get("refs") or [])
    yield from ((item, "audio") for item in state.get("audio_tracks") or [])
    if state.get("audio"):
        yield state["audio"], "audio"
    yield from ((item, "refs") for item in state.get("assets") or [])


def _digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .") or "素材"


def write_project_archive(project_id, target, function_marks=None):
    state = copy.deepcopy(read_state(project_id))
    used_refs = {item["path"] for scene in state["segments"] for item in scene.get("refs") or []}
    state["assets"] = [item for item in state.get("assets") or [] if item.get("path") in used_refs]
    state["results"] = []
    files, paths, content = [], {}, {}
    records = [(item, kind, item.get("path")) for item, kind in _records(state)]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item, kind, original in records:
            if not original:
                continue
            if original not in paths:
                source = safe_media(project_id, original)
                digest = _digest(source)
                key = (kind, digest)
                if key not in content:
                    directory = "参考图" if kind == "refs" else "音频"
                    filename = _name(item.get("name") or source.name)
                    if not Path(filename).suffix:
                        filename += source.suffix
                    portable = f"{directory}/{len(files)+1:02d}-{filename}"
                    archive.write(source, portable)
                    files.append({"path": portable, "kind": kind, "sha256": digest, "size": source.stat().st_size})
                    content[key] = portable
                paths[original] = content[key]
            item["path"] = paths[original]
            item.pop("shared_asset_id", None)

        table = io.StringIO(newline="")
        writer = csv.writer(table)
        writer.writerow(["分镜", "名称", "开始秒", "结束秒", "时长秒", "原始提示词", "优化后提示词", "参考图"])
        for index, scene in enumerate(state["segments"], 1):
            start, end = float(scene.get("start") or 0), float(scene.get("end") or 0)
            writer.writerow([index, scene.get("name", ""), round(start, 3), round(end, 3), round(end-start, 3),
                             scene.get("original_prompt", ""), scene.get("optimized_prompt", ""),
                             "\n".join(item["path"] for item in scene.get("refs") or [])])
        archive.writestr("分镜提示词.csv", table.getvalue().encode("utf-8-sig"))
        archive.writestr("项目.json", json.dumps({"format": FORMAT, "version": 1, "project": state,
                            "files": files, "function_marks": function_marks or {}}, ensure_ascii=False, indent=2))
        archive.writestr("使用说明.txt", "在数字人V3点击“导入项目”，选择整个 ZIP，可恢复分镜、两版提示词、参考图、原音频和项目设置。\n"
                         "导入会创建新项目，原项目和已生成的视频保留。项目包不包含生成视频及未应用的公共资产库素材。\n"
                         "分镜提示词.csv 用于查看；参考图、音频可单独取用。标记按当前工作流中匹配的节点恢复。\n")
    return {"segments": len(state["segments"]), "references": sum(len(s.get("refs") or []) for s in state["segments"]),
            "files": len(files)}


def import_project_archive(source):
    with zipfile.ZipFile(source) as archive:
        package = json.loads(archive.read("项目.json").decode("utf-8-sig"))
        if package.get("format") != FORMAT or package.get("version") != 1:
            raise ValueError("不是数字人 V3 项目包")
        state = copy.deepcopy(package["project"])
        if not isinstance(state.get("segments"), list) or not state["segments"]:
            raise ValueError("项目包没有分镜")
        entries = {item["path"]: item for item in package["files"]}
        for item, _kind in _records(state):
            if item.get("path") and item["path"] not in entries:
                raise ValueError(f"项目包缺少素材：{item['path']}")
        project_id = time.strftime("dhv3_%Y%m%d_") + uuid.uuid4().hex[:10]
        destination = project_dir(project_id)
        ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".项目导入-", dir=ROOT) as temporary:
            staging = Path(temporary)
            paths = {}
            for portable, item in entries.items():
                kind = "audio" if item["kind"] == "audio" else "refs"
                relative = f"{kind}/{uuid.uuid4().hex}{Path(portable).suffix}"
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(portable) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output, length=1024*1024)
                if target.stat().st_size != item["size"] or _digest(target) != item["sha256"]:
                    raise ValueError(f"项目包素材校验失败：{portable}")
                paths[portable] = relative
            records = [(item, item.get("path")) for item, _kind in _records(state)]
            for item, portable in records:
                if portable:
                    item["path"] = paths[portable]
                item.pop("shared_asset_id", None)
            state.update(id=project_id, created=int(time.time()), results=[])
            state = normalize_state(state)
            (staging / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            staging.rename(destination)
    return state, package.get("function_marks") or {}

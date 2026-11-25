from __future__ import annotations

import asyncio
import mimetypes
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import folder_paths
from aiohttp import web
from server import PromptServer

from .storage import (
    create_project,
    project_dir,
    read_state,
    reference_records,
    safe_media,
    save_state,
    save_state_preserving_generated_prompts,
)


routes = PromptServer.instance.routes


def ok(**payload):
    return web.json_response({"ok": True, **payload})


def error(exc, status=400):
    return web.json_response({"ok": False, "error": str(exc)}, status=status)


def qwen_h3_model_catalog():
    from .local_llm import model_catalog
    return model_catalog()


async def multipart_fields(request):
    reader = await request.multipart()
    values, uploads = {}, {}
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.filename:
            uploads[part.name] = (part.filename, await part.read(decode=False))
        else:
            values[part.name] = await part.text()
    return values, uploads


@routes.get("/xiantu/dhv3/qwen-models")
async def qwen_models(_request):
    try:
        return ok(**qwen_h3_model_catalog())
    except Exception as exc:
        return error(exc, 500)


@routes.post("/xiantu/dhv3/projects")
async def create(_request):
    try:
        return ok(project=create_project())
    except Exception as exc:
        return error(exc, 500)


@routes.get("/xiantu/dhv3/projects/{project_id}")
async def get_project(request):
    try:
        return ok(project=read_state(request.match_info["project_id"]))
    except FileNotFoundError as exc:
        return error(exc, 404)
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/export")
async def export_project(request):
    from .project_archive import write_project_archive
    try:
        payload = await request.json()
        with tempfile.TemporaryDirectory(prefix="dhv3-export-") as temporary:
            target = Path(temporary) / "数字人V3项目.zip"
            await asyncio.to_thread(write_project_archive, request.match_info["project_id"], target,
                                    payload.get("function_marks"))
            response = web.StreamResponse(headers={"Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="DigitalHumanV3.zip"',
                "Content-Length": str(target.stat().st_size)})
            await response.prepare(request)
            with target.open("rb") as source:
                while chunk := await asyncio.to_thread(source.read, 1024*1024):
                    await response.write(chunk)
            await response.write_eof()
            return response
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/import")
async def import_project(request):
    from .project_archive import import_project_archive
    try:
        reader = await request.multipart()
        part = await reader.next()
        if part is None:
            raise ValueError("请选择数字人 V3 项目包")
        with tempfile.TemporaryDirectory(prefix="dhv3-import-") as temporary:
            target = Path(temporary) / "project.zip"
            with target.open("wb") as output:
                while chunk := await part.read_chunk():
                    output.write(chunk)
            state, marks = await asyncio.to_thread(import_project_archive, target)
        return ok(project=state, function_marks=marks)
    except Exception as exc:
        return error(exc)


@routes.put("/xiantu/dhv3/projects/{project_id}")
async def put_project(request):
    try:
        state = await request.json()
        if state.get("id") != request.match_info["project_id"]:
            raise ValueError("项目编号不一致")
        explicit_optimized_ids = {
            str(value)
            for value in state.pop("_write_optimized_prompt_ids", [])
            if str(value)
        }
        return ok(
            project=save_state_preserving_generated_prompts(
                state, explicit_optimized_ids
            )
        )
    except Exception as exc:
        return error(exc)


@routes.get("/xiantu/dhv3/projects/{project_id}/audio/mix")
async def mixed_audio(request):
    """Return the project's single sequential audio lane as a playable WAV."""
    try:
        import numpy as np
        import soundfile as sf

        project_id = request.match_info["project_id"]
        state = read_state(project_id)
        tracks = state.get("audio_tracks") or []
        if not tracks:
            raise FileNotFoundError("项目还没有音频")
        # The common case is one untouched track.  Serving the uploaded file
        # directly lets aiohttp handle byte ranges without rebuilding a large
        # PCM WAV for every browser range request (which made long MP3 files
        # appear as 00:00 and left the waveform blank).
        active_tracks = [item for item in tracks if not item.get("muted")]
        if len(active_tracks) == 1:
            item = active_tracks[0]
            source = project_dir(project_id) / str(item.get("path") or "")
            if (source.is_file()
                    and abs(float(item.get("offset") or 0)) < 0.001
                    and abs(float(item.get("volume", 1.0)) - 1.0) < 0.001):
                return web.FileResponse(
                    path=source,
                    headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
                )
        sample_rate = max((int(item.get("sample_rate") or 0) for item in tracks), default=0) or 44100
        total_length = max(1, round(float(state.get("duration") or 0) * sample_rate))
        mixed = np.zeros((total_length, 2), dtype=np.float32)
        base = project_dir(project_id)
        for item in tracks:
            if item.get("muted"):
                continue
            path = base / str(item.get("path") or "")
            if not path.is_file():
                continue
            samples, source_rate = sf.read(str(path), always_2d=True, dtype="float32")
            if samples.shape[1] == 1:
                samples = np.repeat(samples, 2, axis=1)
            elif samples.shape[1] > 2:
                samples = samples[:, :2]
            source_rate = int(source_rate)
            if source_rate != sample_rate and len(samples) > 1:
                count = max(1, round(len(samples) * sample_rate / source_rate))
                source_axis = np.linspace(0.0, 1.0, len(samples), endpoint=False)
                target_axis = np.linspace(0.0, 1.0, count, endpoint=False)
                samples = np.column_stack([
                    np.interp(target_axis, source_axis, samples[:, channel])
                    for channel in range(samples.shape[1])
                ]).astype(np.float32)
            left = max(0, round(float(item.get("offset") or 0) * sample_rate))
            count = min(len(samples), total_length - left)
            if count > 0:
                volume = max(0.0, min(2.0, float(item.get("volume", 1.0))))
                mixed[left:left + count] += samples[:count] * volume
        stream = io.BytesIO()
        sf.write(stream, np.clip(mixed, -1.0, 1.0), sample_rate, format="WAV", subtype="PCM_16")
        payload = stream.getvalue()
        total = len(payload)
        headers = {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}
        requested = request.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip()) if requested else None
        if match:
            start_text, end_text = match.groups()
            if start_text:
                start = int(start_text)
                end = min(total - 1, int(end_text)) if end_text else total - 1
            elif end_text:
                length = min(total, int(end_text))
                start, end = total - length, total - 1
            else:
                start, end = 0, total - 1
            if start >= total or start > end:
                return web.Response(status=416, headers={**headers, "Content-Range": f"bytes */{total}"})
            body = payload[start:end + 1]
            headers.update({"Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(len(body))})
            return web.Response(status=206, body=body, content_type="audio/wav", headers=headers)
        headers["Content-Length"] = str(total)
        return web.Response(body=payload, content_type="audio/wav", headers=headers)
    except FileNotFoundError as exc:
        return error(exc, 404)
    except Exception as exc:
        return error(exc, 500)


@routes.post("/xiantu/dhv3/projects/{project_id}/audio")
async def upload_audio(request):
    try:
        project_id = request.match_info["project_id"]
        values, uploads = await multipart_fields(request)
        if "file" not in uploads:
            raise ValueError("没有收到音频文件")
        filename, content = uploads["file"]
        suffix = Path(filename).suffix.lower() or ".wav"
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}:
            raise ValueError("不支持的音频格式")
        directory = project_dir(project_id) / "audio"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(content)
        duration, sample_rate = 0.0, 0
        try:
            import soundfile as sf
            info = sf.info(str(target))
            duration, sample_rate = float(info.duration), int(info.samplerate)
        except Exception:
            pass
        state = read_state(project_id)
        tracks = state.setdefault("audio_tracks", [])
        mode = str(values.get("mode") or "add").strip().lower()
        track_id = str(values.get("track_id") or "").strip()
        record = {
            "id": uuid.uuid4().hex[:12],
            "name": Path(filename).name,
            "path": target.relative_to(project_dir(project_id)).as_posix(),
            "duration": round(duration, 3),
            "sample_rate": sample_rate,
            "offset": 0.0,
            "volume": 1.0,
            "muted": False,
        }
        had_tracks = bool(tracks)
        if mode == "replace" and tracks:
            index = next((i for i, item in enumerate(tracks) if str(item.get("id")) == track_id),
                         int(state.get("selected_audio_track") or 0))
            index = max(0, min(index, len(tracks) - 1))
            previous = tracks[index]
            record["id"] = str(previous.get("id") or record["id"])
            record["offset"] = max(0.0, float(previous.get("offset") or 0))
            record["volume"] = max(0.0, min(2.0, float(previous.get("volume", 1.0))))
            record["muted"] = bool(previous.get("muted", False))
            tracks[index] = record
            old_path = project_dir(project_id) / str(previous.get("path") or "")
            if old_path.is_file() and old_path.resolve().parent == directory.resolve():
                old_path.unlink(missing_ok=True)
        else:
            tracks.append(record)
            index = len(tracks) - 1
        state["selected_audio_track"] = index
        state["sample_rate"] = max((int(item.get("sample_rate") or 0) for item in tracks), default=0)
        state["duration"] = round(max((float(item.get("offset") or 0) + float(item.get("duration") or 0)
                                       for item in tracks), default=0.0), 3)
        if state.get("segments") and (not had_tracks or (len(tracks) == 1 and len(state["segments"]) == 1)):
            state["segments"][0]["start"] = 0.0
            state["segments"][0]["end"] = state["duration"]
        return ok(project=save_state(state))
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/audio/remove")
async def remove_audio_track(request):
    try:
        project_id = request.match_info["project_id"]
        payload = await request.json()
        track_id = str(payload.get("track_id") or "").strip()
        state = read_state(project_id)
        tracks = state.get("audio_tracks") or []
        index = next((i for i, item in enumerate(tracks) if str(item.get("id")) == track_id), -1)
        if index < 0:
            raise ValueError("音轨不存在")
        removed = tracks.pop(index)
        path = project_dir(project_id) / str(removed.get("path") or "")
        audio_root = (project_dir(project_id) / "audio").resolve()
        if path.is_file() and path.resolve().parent == audio_root:
            path.unlink(missing_ok=True)
        state["selected_audio_track"] = max(0, min(index, len(tracks) - 1))
        # Clear the legacy alias as well, or normalization recreates the last
        # removed track from state['audio'] and points it at the deleted file.
        state["audio_tracks"] = tracks
        state["audio"] = tracks[state["selected_audio_track"]] if tracks else None
        state["duration"] = round(sum(float(item.get("duration") or 0) for item in tracks), 3)
        state["sample_rate"] = max((int(item.get("sample_rate") or 0) for item in tracks), default=0)
        duration = state["duration"]
        state["audio_cuts"] = [cut for cut in state.get("audio_cuts", []) if 0 < float(cut) < duration]
        for segment in state.get("segments", []):
            segment["start"] = max(0.0, min(float(segment.get("start") or 0), duration))
            segment["end"] = max(segment["start"], min(float(segment.get("end") or 0), duration))
        return ok(project=save_state(state))
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/refs")
async def upload_refs(request):
    try:
        project_id = request.match_info["project_id"]
        values, uploads = await multipart_fields(request)
        index = int(values.get("segment_index", 0))
        state = read_state(project_id)
        segment = state["segments"][index]
        scope = values.get("reference_scope")
        target_refs = reference_records(state, segment, None if scope is None else scope == "global")
        total_refs = len(reference_records(state, segment))
        if total_refs >= 9:
            raise ValueError("当前分镜最多上传 9 张参考图")
        directory = project_dir(project_id) / "refs"
        directory.mkdir(parents=True, exist_ok=True)
        added = []
        for _field, (filename, content) in uploads.items():
            if total_refs + len(added) >= 9:
                break
            suffix = Path(filename).suffix.lower() or ".png"
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            target = directory / f"{uuid.uuid4().hex}{suffix}"
            target.write_bytes(content)
            record = {"name": Path(filename).name, "path": target.relative_to(project_dir(project_id)).as_posix()}
            target_refs.append(record)
            added.append(record)
        if not added:
            raise ValueError("没有收到可用图片")
        return ok(project=save_state(state))
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/assets")
async def upload_assets(request):
    try:
        project_id = request.match_info["project_id"]
        values, uploads = await multipart_fields(request)
        category = str(values.get("category") or "other")
        from .shared_assets import publish_file
        state = read_state(project_id)
        assets = state.setdefault("assets", [])
        directory = project_dir(project_id) / "assets"
        directory.mkdir(parents=True, exist_ok=True)
        added = []
        for _field, (filename, content) in uploads.items():
            suffix = Path(filename).suffix.lower() or ".png"
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            target = directory / f"{uuid.uuid4().hex}{suffix}"
            target.write_bytes(content)
            record = {
                "id": uuid.uuid4().hex[:12],
                "name": Path(filename).name,
                "path": target.relative_to(project_dir(project_id)).as_posix(),
                "category": category,
            }
            assets.append(record)
            shared = await asyncio.to_thread(publish_file, target, record["name"], category)
            record["shared_asset_id"] = shared["id"]
            added.append(record)
        if not added:
            raise ValueError("没有收到可用图片")
        _remember_shared_imports(project_id, [item["path"] for item in added])
        return ok(project=save_state(state), added=added)
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/assets/delete")
async def delete_assets(request):
    try:
        project_id = request.match_info["project_id"]
        values = await request.json()
        selected_ids = {str(value) for value in values.get("asset_ids") or []}
        if not selected_ids:
            raise ValueError("请先选择要删除的资产")
        state = read_state(project_id)
        removed = [item for item in state.get("assets") or [] if str(item.get("id")) in selected_ids]
        state["assets"] = [item for item in state.get("assets") or [] if str(item.get("id")) not in selected_ids]
        referenced_paths = {
            str(item.get("path"))
            for segment in state.get("segments") or []
            for item in segment.get("refs") or []
            if isinstance(item, dict) and item.get("path")
        }
        referenced_paths.update(str(item["path"]) for item in state.get("global_refs") or [] if isinstance(item, dict) and item.get("path"))
        asset_root = (project_dir(project_id) / "assets").resolve()
        for item in removed:
            path = project_dir(project_id) / str(item.get("path") or "")
            if str(item.get("path") or "") in referenced_paths:
                continue
            resolved = path.resolve()
            if resolved.is_file() and resolved.parent == asset_root:
                resolved.unlink()
        return ok(project=save_state(state), deleted=len(removed))
    except Exception as exc:
        return error(exc)


def _output_file(record: dict) -> Path:
    roots = {
        "output": Path(folder_paths.get_output_directory()),
        "temp": Path(folder_paths.get_temp_directory()),
        "input": Path(folder_paths.get_input_directory()),
    }
    root = roots.get(str(record.get("type") or "output"), roots["output"]).resolve()
    path = (root / str(record.get("subfolder") or "") / str(record.get("filename") or "")).resolve()
    if root != path and root not in path.parents:
        raise ValueError("视频路径越界")
    if not path.is_file():
        raise FileNotFoundError(f"视频不存在：{path.name}")
    return path


def _remember_shared_imports(project_id, paths):
    receipt = project_dir(project_id) / "shared-library-imports.json"
    imported = set(json.loads(receipt.read_text(encoding="utf-8"))) if receipt.is_file() else set()
    imported.update(paths)
    temporary = receipt.with_suffix(f".tmp-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(sorted(imported), ensure_ascii=False), encoding="utf-8")
    temporary.replace(receipt)
    return imported


@routes.get("/xiantu/dhv3/projects/{project_id}/library")
async def shared_library(request):
    try:
        from .shared_assets import library_snapshot, publish_file
        project_id = request.match_info["project_id"]
        state = read_state(project_id)
        # Migrate each legacy project asset once. Shared deletions must not be
        # undone by opening the project again. Project references stay intact.
        imported = _remember_shared_imports(project_id, [item["path"] for segment in state.get("segments") or []
                                                       for item in segment.get("refs") or []
                                                       if item.get("shared_asset_id") and item.get("path")])
        imported = _remember_shared_imports(project_id, [item["path"] for item in state.get("global_refs") or []
                                                       if item.get("shared_asset_id") and item.get("path")])
        for item in state.get("assets") or []:
            relative = str(item.get("path") or "")
            if relative in imported:
                continue
            try:
                source = safe_media(project_id, relative)
            except FileNotFoundError:
                continue
            await asyncio.to_thread(publish_file, source, item.get("name"), item.get("category", "other"))
            imported.add(relative)
        _remember_shared_imports(project_id, imported)
        return ok(library=await asyncio.to_thread(library_snapshot))
    except Exception as exc:
        return error(exc)


@routes.get("/xiantu/dhv3/library/content")
async def shared_library_content(request):
    try:
        from .shared_assets import asset_source
        path, _item = await asyncio.to_thread(asset_source, request.query.get("id", ""))
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})
    except Exception as exc:
        return error(exc, 404)


@routes.post("/xiantu/dhv3/projects/{project_id}/library/apply")
async def shared_library_apply(request):
    try:
        from .shared_assets import asset_source
        project_id = request.match_info["project_id"]
        payload = await request.json()
        state = read_state(project_id)
        index = int(payload.get("segment_index", state.get("selected_segment", 0)))
        scope = payload.get("reference_scope")
        refs = reference_records(state, state["segments"][index], None if scope is None else scope == "global")
        effective_refs = reference_records(state, state["segments"][index])
        known = {str(item.get("shared_asset_id") or "") for item in effective_refs}
        effective_count = len(effective_refs)
        directory = project_dir(project_id) / "refs"
        directory.mkdir(exist_ok=True)
        for asset_id in payload.get("asset_ids") or []:
            if str(asset_id) in known or effective_count >= 9:
                continue
            source, item = await asyncio.to_thread(asset_source, asset_id)
            if not item["mimeType"].startswith("image/"):
                continue
            target = directory / f"{uuid.uuid4().hex}{source.suffix}"
            await asyncio.to_thread(shutil.copy2, source, target)
            refs.append({"name": item["name"], "path": target.relative_to(project_dir(project_id)).as_posix(),
                         "shared_asset_id": item["id"]})
            known.add(item["id"])
            effective_count += 1
        state["selected_segment"] = index
        _remember_shared_imports(project_id, [item["path"] for item in refs if item.get("shared_asset_id")])
        return ok(project=save_state(state))
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/library/update")
async def shared_library_update(request):
    try:
        from .shared_assets import move_asset
        read_state(request.match_info["project_id"])
        payload = await request.json()
        await asyncio.to_thread(move_asset, payload.get("asset_id"), payload.get("category"))
        return ok()
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/library/delete")
async def shared_library_delete(request):
    try:
        from .shared_assets import delete_assets as delete_shared
        read_state(request.match_info["project_id"])
        payload = await request.json()
        return ok(deleted=await asyncio.to_thread(delete_shared, payload.get("asset_ids") or []))
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/results/finalize")
async def finalize_results(request):
    try:
        from .output_trim import finalize_video
        project_id = request.match_info["project_id"]
        state = read_state(project_id)
        payload = await request.json()
        prompt_id = str(payload.get("prompt_id") or "")
        history = PromptServer.instance.prompt_queue.get_history(prompt_id)
        entry = history.get(prompt_id) or {}
        status = entry.get("status") or {}
        if not status.get("completed") or status.get("status_str") == "error":
            raise ValueError("工作流尚未完成，保留原视频，等待完成后裁剪补帧")
        graph = (entry.get("prompt") or [None, None, {}])[2]
        inputs = (graph.get(str(payload.get("node_id"))) or {}).get("inputs") or {}
        if str(inputs.get("project_id") or "") != project_id:
            raise ValueError("生成记录不属于当前数字人项目")
        index = int(inputs.get("segment_index", 0))
        segment = state["segments"][index]
        seconds = float(segment["end"]) - float(segment["start"])
        def collect(value):
            if isinstance(value, list):
                return [item for part in value for item in collect(part)]
            if not isinstance(value, dict):
                return []
            if value.get("filename"):
                return [value]
            return [item for part in value.values() for item in collect(part)]
        def key(item):
            return (item.get("type", "output"), item.get("subfolder", ""), item.get("filename"))
        known = {key(item) for item in collect(entry.get("outputs") or {})}
        items = []
        for record in payload.get("items") or []:
            if key(record) not in known:
                continue
            result = await asyncio.to_thread(finalize_video, _output_file(record), seconds,
                                             project_dir(project_id) / "padded-video-backups")
            items.append({**record, **result})
        return ok(items=items)
    except Exception as exc:
        return error(exc)


def _result_kind(record: dict) -> str:
    path = f"/{record.get('subfolder') or ''}/{record.get('filename') or ''}/".replace("\\", "/")
    if "/合并成品/" in path or str(record.get("segment_id")) == "merged":
        return "merged"
    if "/放大前/" in path or str(record.get("filename") or "").startswith("H3_Director_Final"):
        return "before"
    return "after"


def _prepare_merge_source(source, target):
    import av
    from .output_trim import _presentation_cut

    # A hidden reference before a visible packet must remain in decode order.
    # Only hidden packets AFTER the last visible packet are unused tail data.
    with av.open(str(source)) as container:
        video = container.streams.video[0]
        last_visible_dts = None
        unused_tail = 0
        duration = container.duration / av.time_base
        for packet in container.demux(video):
            if not packet.size or packet.dts is None:
                continue
            if packet.is_discard:
                unused_tail += 1
            else:
                last_visible_dts = packet.dts
                unused_tail = 0
        video_index = video.index
    if not unused_tail or last_visible_dts is None:
        return source

    with av.open(str(source)) as container, av.open(str(target), "w") as output:
        output.metadata.update(container.metadata)
        streams = {stream.index: output.add_stream_from_template(stream)
                   for stream in container.streams}
        for packet in container.demux():
            if not packet.size or packet.dts is None:
                continue
            if (packet.stream.index == video_index and packet.is_discard
                    and packet.dts > last_visible_dts):
                continue
            packet.stream = streams[packet.stream.index]
            output.mux(packet)
    _presentation_cut(target, duration)
    return target


def _merge_video_files(ffmpeg, sources, target, concat_file, prepare_after):
    with tempfile.TemporaryDirectory(prefix=".合并临时-", dir=target.parent) as temporary:
        if prepare_after:
            sources = [_prepare_merge_source(path, Path(temporary) / f"{index}.mp4")
                       for index, path in enumerate(sources)]
        try:
            concat_file.write_text("\n".join(
                "file '" + path.as_posix().replace("'", "'\\'" ) + "'"
                for path in sources), encoding="utf-8")
            command = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                       "-c", "copy", str(target)]
            return subprocess.run(command, capture_output=True, text=True, timeout=3600)
        finally:
            concat_file.unlink(missing_ok=True)


_merge_lock = asyncio.Lock()


@routes.post("/xiantu/dhv3/projects/{project_id}/merge")
async def merge_results(request):
    async with _merge_lock:
        return await _merge_results(request)


async def _merge_results(request):
    try:
        project_id = request.match_info["project_id"]
        payload = await request.json()
        wanted = {str(value) for value in payload.get("result_ids") or []}
        kind = payload.get("result_kind", "after")
        if kind not in ("before", "after"):
            raise ValueError("请选择放大前或放大后的视频合并")
        state = read_state(project_id)
        records = [item for item in state.get("results") or []
                   if str(item.get("id")) in wanted and _result_kind(item) == kind]
        records.sort(key=lambda item: (
            int(item.get("segment_index")) if str(item.get("segment_index", "")).lstrip("-").isdigit() else 10**9,
            str(item.get("filename") or ""),
        ))
        if len(records) < 2:
            raise ValueError(f"请至少选择两个{'放大前' if kind == 'before' else '放大后'}视频")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                ffmpeg = None
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，无法合并视频")
        sources = [_output_file(item) for item in records]
        directory = Path(folder_paths.get_output_directory()) / "数字人V3" / "合并成品"
        directory.mkdir(parents=True, exist_ok=True)
        counters = []
        for path in directory.glob("成品-*.mp4"):
            match = re.fullmatch(r"成品-(\d+)\.mp4", path.name, re.IGNORECASE)
            if match:
                counters.append(int(match.group(1)))
        counter = max(counters, default=0) + 1
        concat_file = directory / f".合并清单-{uuid.uuid4().hex}.txt"
        target = directory / f"成品-{counter:03d}.mp4"
        first = await asyncio.to_thread(_merge_video_files, ffmpeg, sources, target,
                                        concat_file, kind == "after")
        if first.returncode != 0:
            raise RuntimeError((first.stderr or "视频合并失败")[-1200:])
        subfolder = target.parent.relative_to(Path(folder_paths.get_output_directory())).as_posix()
        result = {
            "id": uuid.uuid4().hex[:12],
            "fingerprint": f"output/{subfolder}/{target.name}",
            "filename": target.name,
            "subfolder": subfolder,
            "type": "output",
            "format": "video/mp4",
            "segment_id": "merged",
            "segment_index": -1,
            "segment_name": f"成品-{counter:03d}（{len(records)} 段）",
            "duration": round(sum(float(item.get("duration") or 0) for item in records), 3),
            "created": int(time.time() * 1000),
            "selected": False,
        }
        state = read_state(project_id)
        state.setdefault("results", []).append(result)
        return ok(project=save_state(state), result=result)
    except Exception as exc:
        return error(exc)


@routes.post("/xiantu/dhv3/projects/{project_id}/results/open-folder")
async def open_results_folder(request):
    try:
        directory = Path(folder_paths.get_output_directory()) / "数字人V3"
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(directory)], creationflags=0x08000000)
        return ok(path=str(directory))
    except Exception as exc:
        return error(exc, 500)


@routes.get("/xiantu/dhv3/projects/{project_id}/media/{path:.*}")
async def media(request):
    try:
        path = safe_media(request.match_info["project_id"], request.match_info["path"])
        return web.FileResponse(path, headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
    except FileNotFoundError as exc:
        return error(exc, 404)
    except Exception as exc:
        return error(exc)


@routes.get("/xiantu/dhv3/hardware")
async def hardware(_request):
    from .runtime_status import hardware_snapshot
    return ok(**(await asyncio.to_thread(hardware_snapshot)))


@routes.post("/xiantu/dhv3/cleanup")
async def cleanup(request):
    try:
        settings = await request.json()
        if not isinstance(settings, dict):
            return error("资源清理参数必须是 JSON 对象")
        from .runtime_status import cleanup_idle_resources
        def check_connection():
            if request.transport is None or request.transport.is_closing():
                raise RuntimeError("资源清理已取消")
        result = await asyncio.to_thread(cleanup_idle_resources, PromptServer.instance, settings, check_connection)
        return ok(**result)
    except Exception as exc:
        return error(exc, 500)

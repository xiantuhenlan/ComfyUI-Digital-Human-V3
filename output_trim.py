"""Finalize padded H3 clips by lossless MP4 remux and presentation edits.

The model's padded frames remain valid decoding references. MP4 edit lists hide
them beyond the requested cut, including fractional video/audio frame endings.
No model, sampler, pixel codec, frame rate or audio codec is changed.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import uuid


def probe_duration(path):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("文件没有视频轨道")
        return float(container.duration or 0) / av.time_base


def _atoms(data, start, end):
    while start + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, start)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, start + 8)[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError("MP4 容器结构不完整")
        yield kind, start + header, start + size
        start += size


def _presentation_cut(path, seconds):
    # Only the moov atom is loaded. Large video payloads remain on disk.
    with Path(path).open("r+b") as handle:
        total = handle.seek(0, 2)
        offset = 0
        while offset + 8 <= total:
            handle.seek(offset)
            size, kind = struct.unpack(">I4s", handle.read(8))
            header = 8
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
                header = 16
            elif size == 0:
                size = total - offset
            if size < header or offset + size > total:
                raise ValueError("MP4 容器结构不完整")
            if kind == b"moov":
                break
            offset += size
        else:
            raise ValueError("MP4 缺少 moov")
        handle.seek(offset + header)
        data = bytearray(handle.read(size - header))
        top = list(_atoms(data, 0, len(data)))
        movie = next((entry for entry in top if entry[0] == b"mvhd"), None)
        if not movie:
            raise ValueError("MP4 缺少时间信息")
        pos = movie[1]
        version = data[pos]
        scale_at = pos + (20 if version == 1 else 12)
        scale = struct.unpack_from(">I", data, scale_at)[0]
        ticks = round(seconds * scale)
        fmt = ">Q" if version == 1 else ">I"
        struct.pack_into(fmt, data, scale_at + 4, ticks)
        edits = 0
        for kind, start, end in top:
            if kind != b"trak":
                continue
            children = list(_atoms(data, start, end))
            track = next((entry for entry in children if entry[0] == b"tkhd"), None)
            if not track:
                continue
            track_pos = track[1]
            track_fmt = ">Q" if data[track_pos] == 1 else ">I"
            duration_at = track_pos + (28 if data[track_pos] == 1 else 20)
            old_duration = struct.unpack_from(track_fmt, data, duration_at)[0]
            limit = min(ticks, old_duration)
            for child_kind, child_start, child_end in children:
                if child_kind != b"edts":
                    continue
                for edit_kind, edit_start, edit_end in _atoms(data, child_start, child_end):
                    if edit_kind != b"elst":
                        continue
                    edit_version = data[edit_start]
                    width, entry_size = (8, 20) if edit_version == 1 else (4, 12)
                    edit_fmt = ">Q" if width == 8 else ">I"
                    count = struct.unpack_from(">I", data, edit_start + 4)[0]
                    if edit_start + 8 + count * entry_size > edit_end:
                        raise ValueError("MP4 编辑时间表不完整")
                    remaining = limit
                    for index in range(count):
                        entry = edit_start + 8 + index * entry_size
                        duration = struct.unpack_from(edit_fmt, data, entry)[0]
                        kept = min(duration, remaining)
                        struct.pack_into(edit_fmt, data, entry, kept)
                        remaining -= kept
                    edits += 1
            struct.pack_into(track_fmt, data, duration_at, limit)
        if not edits:
            raise ValueError("MP4 缺少编辑时间表，原视频已保留")
        handle.seek(offset + header)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def finalize_video(path, seconds, backup_directory):
    path = Path(path)
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("分镜时长无效")
    actual = probe_duration(path)
    if actual <= seconds + .001:
        return {"duration": actual, "trimmed": False}
    if path.suffix.lower() not in {".mp4", ".mov"}:
        return {"duration": actual, "trimmed": False, "warning": "当前容器不支持无损时长裁剪，原视频已保留"}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    temporary = path.with_name(f".{path.stem}.trim-{uuid.uuid4().hex}{path.suffix}")
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(path), "-t", f"{seconds:.9f}",
             "-map", "0", "-map_metadata", "0", "-c", "copy", "-use_editlist", "1",
             "-movflags", "+faststart", str(temporary)],
            capture_output=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace")[-1000:])
        _presentation_cut(temporary, seconds)
        duration = probe_duration(temporary)
        if abs(duration - seconds) > .002:
            raise RuntimeError("封装时长校验失败，原视频已保留")
        key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
        backup = Path(backup_directory) / key / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(path, backup)
        os.replace(temporary, path)
        return {"duration": duration, "trimmed": True}
    finally:
        temporary.unlink(missing_ok=True)

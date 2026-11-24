"""Private local GGUF runtime for the standalone digital-human V3 workbench."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
import urllib.request
import folder_paths
from server import PromptServer

RUNTIME_DIRECTORY = Path(__file__).resolve().parent / "runtime" / "video-prompt" / "b10689"
_OPTIMIZER_LOCK = threading.Lock()
_PROCESS_LOCK = threading.Lock()
_OPTIMIZER_CANCELLED = threading.Event()
_process = None
_projector_hashes = {}


DEFAULT_MODEL = "Qwen3VL-4B-Instruct-Q4_K_M.gguf"

DEFAULT_PROJECTOR = "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"

MODEL_REPO = "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/"

MODEL_DOWNLOADS = {
    "model": (DEFAULT_MODEL, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"),
    "projector": (DEFAULT_PROJECTOR, "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d"),
}

VERIFIED_PROJECTORS = {
    "qwen3.8-27b-q4_k_m.gguf": (927607488, "cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e"),
    "qwen3.8-27b-abliterated-q4_k_m.gguf": (931145888, "c9a09064683620bea3d3bfed5d4462e1a97a7d2fff7e5045d6862a0a85eeb5b5"),
    "qwen3.8-9b-distill-heretic_nvfp4_q8_0.gguf": (921705024, "853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383"),
    DEFAULT_MODEL.casefold(): (453974304, MODEL_DOWNLOADS["projector"][1]),
}

def _runtime_executable():
    return RUNTIME_DIRECTORY / "llama-server.exe"

def _storyboard_progress(task_id, stage, percent, detail=""):
    if task_id:
        PromptServer.instance.send_sync("digital-human-v3-prompt-progress", {
            "task_id": task_id, "stage": stage, "percent": percent, "detail": detail,
        })

def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def _projector_matches(model, projector):
    expected = VERIFIED_PROJECTORS.get(model.name.casefold())
    if expected is None:
        return False
    stat = projector.stat()
    if stat.st_size != expected[0]:
        return False
    cached = _projector_hashes.get(projector)
    state = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if not cached or cached[0] != state:
        cached = _projector_hashes[projector] = (state, _sha256(projector))
    return cached[1] == expected[1]

def _catalog(*, verify_projectors=True):
    models_dir = Path(folder_paths.models_dir).resolve()
    roots = {models_dir / "llm", models_dir / "LLM"}
    for group in ("llm", "LLM"):
        if group in folder_paths.folder_names_and_paths:
            roots.update(Path(item) for item in folder_paths.get_folder_paths(group))
    files = {path.resolve() for root in roots if root.is_dir()
             for path in root.rglob("*.gguf") if path.is_file()}
    ordered = sorted(files, key=lambda path: str(path).casefold())
    projectors = [path for path in ordered if any(part in path.name.casefold() for part in ("mmproj", "projector", "mm-project"))]
    projector_set = set(projectors)
    models = []
    pairings = {}
    for path in ordered:
        if path in projector_set or path.name.casefold().startswith(("mtp-", "dflash-", "draft-")):
            continue
        relative = path.relative_to(models_dir) if path.is_relative_to(models_dir) else path
        display = f"[本地] {relative.as_posix()}"
        models.append({"display": display, "name": path.name, "path": str(path)})
        if verify_projectors:
            pairings[display] = [str(item) for item in projectors if _projector_matches(path, item)]
    selected = next((entry["display"] for entry in models if entry["name"] == DEFAULT_MODEL), "")
    selected_projector = next((str(path) for path in projectors if path.name == DEFAULT_PROJECTOR), "")
    ready = _runtime_executable().is_file()
    return {
        "ok": True, "dependency_installed": ready,
        "dependency_error": "" if ready else "尚未安装导演台本地 llama.cpp 运行时。",
        "runtime_package_available": sys.platform == "win32",
        "selected_model": selected, "selected_mmproj": selected_projector,
        "preferred_filename": DEFAULT_MODEL, "preferred_mmproj_filename": DEFAULT_PROJECTOR,
        "installed": bool(selected), "mmproj_installed": bool(selected_projector),
        "model_url": MODEL_REPO + DEFAULT_MODEL, "mmproj_url": MODEL_REPO + DEFAULT_PROJECTOR,
        "models": models,
        "mmproj_models": [{"name": path.name, "path": str(path)} for path in projectors],
        "projectors_by_model": pairings,
        "pairings_verified": verify_projectors,
    }

def _story_free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]

def _story_server_request(url, payload=None, timeout=5):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)

def _release_story_llm_runtime():
    global _process
    with _PROCESS_LOCK:
        process, _process = _process, None
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

def _start_story_llm_server(model_path, context_length, task_id, mmproj_path, gpu_layers):
    global _process
    executable = _runtime_executable()
    if not executable.is_file():
        raise ValueError("请先安装导演台本地 llama.cpp 运行时。")
    port = _story_free_port()
    command = [
        str(executable), "--offline", "-m", str(model_path), "--host", "127.0.0.1", "--port", str(port),
        "-ngl", "auto" if gpu_layers < 0 else str(gpu_layers), "-c", str(context_length),
        "-b", str(min(2048, context_length)), "-ub", str(min(512, context_length)),
        "-fa", "on", "--jinja", "--no-webui",
    ]
    if mmproj_path:
        command.extend(["--mmproj", str(mmproj_path)])
    with tempfile.TemporaryFile(mode="w+b") as log:
        with _PROCESS_LOCK:
            if _OPTIMIZER_CANCELLED.is_set():
                raise InterruptedError("提示词优化已取消。")
            process = _process = subprocess.Popen(
                command, cwd=executable.parent, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        started = time.monotonic()
        last_update = 0
        while (elapsed := time.monotonic() - started) < 180:
            if _OPTIMIZER_CANCELLED.is_set():
                raise InterruptedError("提示词优化已取消。")
            if process.poll() is not None:
                log.seek(max(0, os.fstat(log.fileno()).st_size - 6000))
                lines = log.read().decode("utf-8", "replace").strip().splitlines()
                raise RuntimeError("本地模型加载失败：" + "；".join(lines[-10:]))
            try:
                if _story_server_request(f"http://127.0.0.1:{port}/health", timeout=1).get("status") == "ok":
                    return port
            except (URLError, TimeoutError, ConnectionError):
                pass
            if elapsed >= last_update:
                _storyboard_progress(task_id, "加载本地模型", 20 + min(30, int(elapsed / 3)), f"{model_path.name} · {elapsed:.0f} 秒")
                last_update += 4
            time.sleep(0.2)
    raise RuntimeError("本地模型加载超时（180 秒）。")

def _official_h3_prompt_contract(mode, duration, output_language="en"):
    mode = str(mode or "T2VA").upper()
    duration_text = f"{float(duration):.2f}"
    prompt_language = "English" if str(output_language).lower() == "en" else "Simplified Chinese"
    chinese_output = prompt_language == "Simplified Chinese"
    shared = (
        f"The final prompt is {prompt_language}, while dialogue, lyrics, and visible scene text stay verbatim in their original language. "
        "Every prompt must contain [Shot 1] without a timestamp. Later cuts use sequential [Shot N] At 00:SS.mmm timestamps that strictly increase and remain inside the requested duration. "
        "Each shot describes composition, subject appearance and position, environment, lighting, observable action progression, reactions, camera movement, and synchronized diegetic sound. "
        "Camera movement is natural prose using motion type plus meaningful amplitude and speed. Use stable (S1), (S2) speaker IDs; keep only the untouched utterance inside <d>[Language] ...</d>. "
        "Use <scenetrans> only for speech crossing a cut and <cutoff> only for intentional truncation at the video end. Preserve visible text exactly inside double quotes. "
        "overall_soundscape is one paragraph of ambience, physical sounds, and non-verbal human sounds without repeating dialogue. non_diegetic_music describes only audience-only score instrumentation, tempo, rhythm, and dynamics, or N/A."
    )
    if mode == "T2VA":
        specific = (
            "T2VA has no image-alignment preamble and starts directly with integrated_multimodal_description:. "
            "Build the complete audiovisual timeline from the supplied text."
        )
    elif mode == "I2VA":
        specific = (
            "首行用中文明确说明目标视频在 0.00 秒完整参考 <Picture 1>（来自 [Shot 1]），然后空一行。将 Picture 1 作为真实首帧，保持身份、服装、色彩、构图、场景锚点、关键物体与空间关系，再向后发展动作起始、连续过程和可见结果或反应。"
            if chinese_output else
            "The exact first line must be: For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced. Then leave one blank line. Establish Picture 1 as the actual first frame, preserve identity, clothing, colors, composition, scene anchors, key objects, and spatial relationships, then develop forward through action onset, continuous development, and a visible result or reaction."
        )
    elif mode == "FL2VA":
        specific = (
            f"首行用中文明确说明参考图与目标视频的时间对齐：<Picture 1>（来自 [Shot 1]）对应 0.00 秒，<Picture 2> 对应 {duration_text} 秒，然后空一行。除非明确要求切镜，否则优先使用连续镜头；描述从首帧状态经过中间姿态、物体、构图、摄影机、场景和光线变化，直至末帧准确落到 Picture 2 的可见路径。"
            if chinese_output else
            f"The first line must use this exact template, replacing N with the actual final shot number: How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the {duration_text}-second mark of the target video. Then leave one blank line. Prefer one continuous shot unless cuts were explicitly requested. Describe the observable path from the first-frame state through intermediate pose, object, composition, camera, scene, and lighting changes until the final state lands exactly on Picture 2."
        )
    else:
        specific = (
            "Use exactly six sections in this order: subject_definitions:, summary:, retention_analysis:, detailed_description:, overall_soundscape:, non_diegetic_music:. "
            "Define reusable visible people, objects, environments, clothing, styles, actions, expressions, and poses as stable <Subject N> units sourced from the supplied media. "
            "Use standalone <Picture N> only for a concrete keyframe/composition/storyboard anchor; <Video N> for editing, continuation, camera/cut/rhythm/temporal structure; and <Audio N> only for an explicitly enabled copied or referenced audio role. "
            "summary begins with the actual bracketed task combination chosen from keyframe completion, reference generation, video editing, video continuation, audio reuse, and audio reference. "
            "retention_analysis covers every used label with only the official relations fully_preserved, partially_preserved, attribute_transfer, weak_reference, fully_copy, partially_copy, or reference. "
            "detailed_description provides the full playback-order audiovisual timeline and cites each reference exactly where its role takes effect."
        )
    core = (
        "Use exactly three core sections in this order: integrated_multimodal_description:, overall_soundscape:, non_diegetic_music:. "
        if mode != "REF2VA" else ""
    )
    return f"{specific} {core}{shared}"

def _optimizer_image_url(image):
    from PIL import Image

    image = image.convert("RGB")
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

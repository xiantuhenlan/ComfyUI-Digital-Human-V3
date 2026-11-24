"""Independent V3 implementation of the shared Xiantu asset-library disk format."""
import ctypes,hashlib,json,mimetypes,os,shutil,time,uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import folder_paths
_ASSET_ROOT_NAME="闲兔资产库"
_ASSET_METADATA="metadata.json"
_ASSET_LOCK="metadata.lock"
_ASSET_CATEGORIES={"character":"角色资产","scene":"场景资产","prop":"道具资产","other":"其他资产","camera":"运镜资产","blocking":"站位资产","audio":"音频资产"}
_REPARSE_POINT=0x400
class _AssetLibraryError(RuntimeError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status

def _is_reparse(path: Path) -> bool:
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return False

def _safe_asset_category_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 60 or name in {".", ".."} or name.startswith(".") or any(char in name for char in '<>:"/\\|?*') or name.endswith((" ", ".")):
        raise _AssetLibraryError("asset_category_name_invalid", "资产分类名称无效")
    return name

def _asset_categories(root: Path):
    categories = dict(_ASSET_CATEGORIES)
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink() or _is_reparse(path) or path.name in _ASSET_CATEGORIES.values():
                continue
            categories[f"custom:{path.name}"] = path.name
    return categories

def _asset_category_directory(root: Path, category: str) -> str:
    directory = _asset_categories(root).get(str(category or ""))
    if not directory:
        raise _AssetLibraryError("asset_category_invalid", "未知资产分类")
    return directory

def _asset_root() -> Path:
    configured = str(os.environ.get("XIANTU_ASSET_LIBRARY_ROOT") or "").strip()
    candidates = [Path(configured)] if configured else []
    comfy_root = Path(folder_paths.base_path).resolve()
    candidates.extend(parent / _ASSET_ROOT_NAME for parent in (comfy_root, *comfy_root.parents))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_dir() and not resolved.is_symlink() and not _is_reparse(resolved):
            return resolved

    # First-run initialization: the launcher normally injects the canonical
    # shared-library path.  Standalone ComfyUI users do not have that
    # environment variable, so create the library beside the ComfyUI folder.
    # Never create it inside custom_nodes or silently choose an arbitrary
    # ancestor.
    target = Path(configured).expanduser() if configured else comfy_root.parent / _ASSET_ROOT_NAME
    try:
        target = target.resolve()
        parent = target.parent.resolve()
        if not parent.is_dir() or parent.is_symlink() or _is_reparse(parent):
            raise OSError("资产库父目录不可用")
        if target.exists() and (not target.is_dir() or target.is_symlink() or _is_reparse(target)):
            raise OSError("资产库路径不是普通目录")
        target.mkdir(exist_ok=True)
        return target
    except OSError as error:
        raise _AssetLibraryError(
            "asset_root_create_failed",
            f"公共资产库自动创建失败：{target}（{error}）",
        ) from error

def _ensure_asset_categories(root: Path, selection: str) -> None:
    categories = tuple(_ASSET_CATEGORIES) if selection == "all" else (selection,)
    for category in categories:
        if category in _ASSET_CATEGORIES:
            directory = _ASSET_CATEGORIES[category]
        else:
            directory = _asset_category_directory(root, category)
        category_dir = root / directory
        try:
            category_dir.mkdir(exist_ok=True)
        except OSError as error:
            raise _AssetLibraryError(
                "asset_category_create_failed",
                f"资产分类目录自动创建失败：{category_dir}（{error}）",
            ) from error
        if not category_dir.is_dir() or category_dir.is_symlink() or _is_reparse(category_dir):
            raise _AssetLibraryError(
                "asset_category_create_failed",
                f"资产分类目录不可用：{category_dir}",
            )

def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _asset_relative(root: Path, category, filename):
    return f"{_asset_category_directory(root, category)}/{filename}".replace("\\", "/")

def _checked_asset_path(root: Path, relative_path: str, must_exist=True) -> Path:
    normalized = str(relative_path or "").replace("\\", "/")
    parts = Path(normalized).parts
    if len(parts) != 2 or parts[0] not in _asset_categories(root).values() or any(part in {"", ".", ".."} for part in parts):
        raise _AssetLibraryError("asset_path_outside_root", "资产相对路径无效")
    parent = (root / parts[0]).resolve()
    try:
        parent.relative_to(root)
    except ValueError as error:
        raise _AssetLibraryError("asset_path_outside_root", "资产路径越过公共资产库") from error
    if not parent.is_dir() or parent.is_symlink() or _is_reparse(parent):
        raise _AssetLibraryError("asset_reparse_point_rejected", "资产分类目录不是普通目录")
    candidate = parent / parts[1]
    if must_exist:
        if not candidate.is_file():
            raise _AssetLibraryError("asset_source_missing", f"公共资产不存在：{normalized}", 404)
        if candidate.is_symlink() or _is_reparse(candidate):
            raise _AssetLibraryError("asset_reparse_point_rejected", "拒绝读取链接或重解析点资产")
    return candidate

def _sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()

def _mime_type(path: Path):
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

def _read_asset_index(root: Path):
    path = root / _ASSET_METADATA
    if not path.is_file():
        return {"schemaVersion": 1, "assets": []}, True, "未找到 metadata.json，已根据六类文件夹重建"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
            raise ValueError("metadata.json 结构无效")
        return {"schemaVersion": 1, "assets": payload["assets"]}, False, ""
    except (OSError, ValueError, json.JSONDecodeError) as error:
        backup = root / f"metadata.corrupt-{int(time.time())}.json"
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        return {"schemaVersion": 1, "assets": []}, True, f"metadata.json 已损坏，已保留副本并重建：{error}"

def _write_asset_index(root: Path, assets):
    destination = root / _ASSET_METADATA
    temporary = root / f"metadata.json.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    payload = json.dumps({"schemaVersion": 1, "assets": assets}, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt":
            move_file = ctypes.windll.kernel32.MoveFileExW
            move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            move_file.restype = ctypes.c_int
            result = move_file(str(temporary), str(destination), 0x1 | 0x8)
            if not result:
                raise ctypes.WinError()
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

@contextmanager
def _asset_metadata_lock(root: Path):
    lock_path = root / _ASSET_LOCK
    for _ in range(100):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}:{int(time.time())}\n")
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(.02)
    else:
        raise _AssetLibraryError("asset_metadata_locked", "资产库正在被其他窗口修改，请稍后重试", 409)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)

def _reconcile_asset_library(root: Path):
    index, recovered, recovery_message = _read_asset_index(root)
    indexed_by_path = {}
    indexed_by_hash = {}
    for item in index["assets"]:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("relativePath") or "").replace("\\", "/")
        indexed_by_path[relative] = item
        content_hash = str(item.get("contentHash") or "").upper()
        if content_hash:
            indexed_by_hash.setdefault(content_hash, []).append(item)
    used_ids = set()
    assets = []
    category_map = _asset_categories(root)
    for category, directory in category_map.items():
        category_root = root / directory
        if not category_root.is_dir() or category_root.is_symlink() or _is_reparse(category_root):
            continue
        for path in category_root.iterdir():
            if not path.is_file() or path.is_symlink() or _is_reparse(path):
                continue
            relative = _asset_relative(root, category, path.name)
            content_hash = _sha256_file(path)
            prior = indexed_by_path.get(relative)
            if prior is None:
                prior = next((item for item in indexed_by_hash.get(content_hash, []) if str(item.get("id")) not in used_ids), None)
            now = _utc_now()
            asset_id = str(prior.get("id") if prior else "").strip() or f"asset-{uuid.uuid4().hex[:24]}"
            used_ids.add(asset_id)
            name = str(prior.get("name") if prior else "").strip() or path.stem or "未命名资产"
            item = {
                "id": asset_id,
                "relativePath": relative,
                "name": name,
                "note": str(prior.get("note") if prior else "").strip(),
                "category": category,
                "mimeType": _mime_type(path),
                "contentHash": content_hash,
                "createdAt": str(prior.get("createdAt") if prior else "").strip() or now,
                "updatedAt": str(prior.get("updatedAt") if prior else "").strip() or now,
                "sizeBytes": path.stat().st_size,
            }
            assets.append(item)
    assets.sort(key=lambda item: (item["updatedAt"], item["name"]), reverse=True)
    _write_asset_index(root, [{key: value for key, value in item.items() if key != "sizeBytes"} for item in assets])
    return {
        "schemaVersion": 1,
        "root": str(root),
        "categories": [{"id": category, "name": directory} for category, directory in category_map.items()],
        "assets": assets,
        "recoveredMetadata": recovered,
        "recoveryMessage": recovery_message,
    }

def _asset_snapshot():
    root = _asset_root()
    with _asset_metadata_lock(root):
        return root, _reconcile_asset_library(root)

def _safe_file_name(value):
    value = Path(str(value or "").strip()).name
    if not value or value in {".", ".."} or any(char in value for char in '<>:"/\\|?*') or value.endswith((" ", ".")):
        raise _AssetLibraryError("asset_invalid_filename", "资产文件名无效")
    return value

def _unique_asset_path(parent: Path, requested):
    requested = Path(_safe_file_name(requested))
    for index in range(10000):
        name = requested.name if index == 0 else f"{requested.stem} ({index}){requested.suffix}"
        candidate = parent / name
        if not candidate.exists():
            return candidate
    raise _AssetLibraryError("asset_duplicate_limit", "同名资产过多，无法保存")

def _snapshot_asset(snapshot, asset_id):
    asset = next((item for item in snapshot["assets"] if item["id"] == str(asset_id)), None)
    if asset is None:
        raise _AssetLibraryError("asset_source_missing", "没有找到公共资产", 404)
    return asset


def publish_file(source, name, category="other"):
    """Copy a project image into the common library, deduplicating by bytes."""
    source = Path(source)
    root = _asset_root()
    if category not in _asset_categories(root):
        category = "other"
    _ensure_asset_categories(root, category)
    digest = _sha256_file(source)
    with _asset_metadata_lock(root):
        # Legacy project imports usually already exist in the common index.
        # Verify only the matching file instead of hashing the whole library
        # again for every reference in a multi-scene project.
        index, recovered, _message = _read_asset_index(root)
        if not recovered:
            for item in index["assets"]:
                if not isinstance(item, dict) or str(item.get("contentHash") or "").upper() != digest:
                    continue
                try:
                    existing = _checked_asset_path(root, item.get("relativePath"))
                except _AssetLibraryError:
                    continue
                if _sha256_file(existing) == digest:
                    return dict(item, sizeBytes=existing.stat().st_size)
        snapshot = _reconcile_asset_library(root)
        existing = next((item for item in snapshot["assets"] if item["contentHash"] == digest), None)
        if existing:
            return existing
        filename = Path(str(name or source.name)).name
        if Path(filename).suffix.lower() != source.suffix.lower():
            filename += source.suffix
        target = _unique_asset_path(root / _asset_category_directory(root, category), filename)
        shutil.copy2(source, target)
        snapshot = _reconcile_asset_library(root)
        return next(item for item in snapshot["assets"] if item["relativePath"] == target.relative_to(root).as_posix())


def _indexed_library_snapshot(root):
    index, recovered, _message = _read_asset_index(root)
    if recovered:
        return None
    categories = _asset_categories(root)
    indexed = {str(item.get("relativePath") or "").replace("\\", "/")
               for item in index["assets"] if isinstance(item, dict)}
    present = {}
    for category, directory in categories.items():
        parent = root / directory
        if not parent.is_dir() or parent.is_symlink() or _is_reparse(parent):
            continue
        for path in parent.iterdir():
            if path.is_file() and not path.is_symlink() and not _is_reparse(path):
                relative = f"{directory}/{path.name}"
                if relative not in indexed:
                    # Preserve discovery of images copied into a category by hand.
                    return None
                present[relative] = (category, path)
    assets = []
    for item in index["assets"]:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("relativePath") or "").replace("\\", "/")
        record = present.get(relative)
        if record is None:
            continue
        category, path = record
        assets.append(dict(item, relativePath=relative, category=category,
                           mimeType=_mime_type(path), sizeBytes=path.stat().st_size))
    assets.sort(key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("name") or "")), reverse=True)
    return {"schemaVersion": 1, "root": str(root),
            "categories": [{"id": key, "name": value} for key, value in categories.items()],
            "assets": assets, "recoveredMetadata": False, "recoveryMessage": ""}


def library_snapshot():
    root = _asset_root()
    # A launcher/director-maintained index can be displayed without rewriting it
    # or reading every image's bytes. Mutations still use full reconciliation.
    snapshot = _indexed_library_snapshot(root)
    if snapshot is None:
        root, snapshot = _asset_snapshot()
    snapshot["assets"] = [dict(item, url=f"/xiantu/dhv3/library/content?id={item['id']}")
                          for item in snapshot["assets"] if item["mimeType"].startswith("image/")]
    return snapshot


def asset_source(asset_id):
    root = _asset_root()
    with _asset_metadata_lock(root):
        snapshot, _recovered, _message = _read_asset_index(root)
        item = _snapshot_asset(snapshot, asset_id)
    return _checked_asset_path(root, item["relativePath"]), item


def move_asset(asset_id, category):
    root = _asset_root()
    _ensure_asset_categories(root, category)
    with _asset_metadata_lock(root):
        snapshot = _reconcile_asset_library(root)
        item = _snapshot_asset(snapshot, asset_id)
        if item["category"] != category:
            source = _checked_asset_path(root, item["relativePath"])
            target = _unique_asset_path(root / _asset_category_directory(root, category), source.name)
            source.rename(target)
            item["relativePath"] = target.relative_to(root).as_posix()
            item["category"] = category
            item["updatedAt"] = _utc_now()
            _write_asset_index(root, [{key: value for key, value in record.items() if key != "sizeBytes"}
                                     for record in snapshot["assets"]])


def delete_assets(asset_ids):
    root = _asset_root()
    wanted = {str(value) for value in asset_ids}
    with _asset_metadata_lock(root):
        snapshot = _reconcile_asset_library(root)
        removed = [item for item in snapshot["assets"] if item["id"] in wanted]
        for item in removed:
            _checked_asset_path(root, item["relativePath"]).unlink()
        _write_asset_index(root, [item for item in snapshot["assets"] if item["id"] not in wanted])
        return len(removed)

"""Small data-only local character packs. No downloads, scripts, or plugins."""
from pathlib import Path
import json

POSES = ("idle_alt", "full", "low", "empty", "frame_1", "frame_2", "frame_3")


def local_file(root, relative, suffix):
    if not isinstance(relative, str) or not relative or len(relative) > 240:
        raise ValueError("Invalid pack path")
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts or ":" in relative:
        raise ValueError("Pack paths must stay inside the pack")
    root = Path(root).resolve(strict=True)
    path = (root / candidate).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() != suffix or not path.is_file():
        raise ValueError("Unsupported pack file")
    return path


def builtin_pack(base):
    base = Path(base)
    names = {pose: f"{pose}.png" for pose in POSES}
    names.update(full="success.png", low="fail.png", empty="fail.png")
    return {"id": "sena", "name": "Sena", "root": base / "assets", "images": names}


def discover_packs(base, resources):
    packs = [builtin_pack(resources)]
    folder = Path(base) / "packs"
    if not folder.is_dir():
        return packs
    # Optional packs are local, bounded, and never executed.
    for child in sorted(folder.iterdir())[:20]:
        try:
            if (not child.is_dir() or child.is_symlink() or child.name == 'sena'
                    or len(child.name) > 80 or not all(c.isalnum() or c in '-_' for c in child.name)):
                continue
            manifest = local_file(child, 'pack.json', '.json')
            if manifest.stat().st_size > 8192:
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or set(data) != {'version', 'name', 'images'}
                    or type(data['version']) is not int or data['version'] != 1):
                continue
            name, images = data["name"], data["images"]
            if not isinstance(name, str) or not name.strip() or len(name) > 40 or any(ord(c) < 32 for c in name):
                continue
            if not isinstance(images, dict) or "idle_alt" not in images or set(images) - set(POSES):
                continue
            for path in images.values():
                image = local_file(child, path, ".png")
                if image.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Image too large")
            packs.append({"id": child.name, "name": name, "root": child, "images": images})
        except (OSError, ValueError, TypeError):
            continue
    return packs


def image_path(pack, pose):
    images = pack["images"]
    return local_file(pack["root"], images.get(pose, images["idle_alt"]), ".png")


def voice_path(pack, language, event):
    if language not in ("en", "ko", "ja", "zh") or event not in ("full", "low", "empty"):
        raise ValueError("Unsupported voice slot")
    return local_file(pack["root"], f"voices/{language}/{event}.wav", ".wav")


def animation_interval(pack):
    # All packs follow the original Sena timing; changes require owner approval.
    return 750

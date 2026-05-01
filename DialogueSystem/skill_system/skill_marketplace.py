"""Skill marketplace: export, import, validate, and browse shared skills."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile

try:
    from ..config.paths import SKILLS_DIR
    from ..config.resources import _parse_skill_markdown, SKILL_RESOURCE_DIRS
except ImportError:
    from DialogueSystem.config.paths import SKILLS_DIR
    from DialogueSystem.config.resources import _parse_skill_markdown, SKILL_RESOURCE_DIRS


logger = logging.getLogger(__name__)

SKILL_MARKDOWN_FILE = "SKILL.md"
AGENTSKILLS_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$")
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def validate_skill_dir(skill_dir: str) -> dict:
    """Validate a skill directory against the agentskills.io spec."""
    errors = []
    warnings = []
    skill_md_path = os.path.join(skill_dir, SKILL_MARKDOWN_FILE)

    if not os.path.isdir(skill_dir):
        return {"ok": False, "errors": [f"Directory does not exist: {skill_dir}"], "warnings": []}

    if not os.path.exists(skill_md_path):
        errors.append("Missing required SKILL.md file.")
        return {"ok": False, "errors": errors, "warnings": warnings}

    parsed = _parse_skill_markdown(skill_md_path)
    frontmatter = parsed.get("frontmatter") or {}

    name = str(frontmatter.get("name") or "").strip()
    if not name:
        errors.append("SKILL.md frontmatter is missing required 'name' field.")
    elif len(name) > MAX_SKILL_NAME_LENGTH:
        errors.append(f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters.")
    elif not AGENTSKILLS_NAME_PATTERN.match(name):
        errors.append(f"Skill name '{name}' does not match agentskills.io pattern (lowercase alphanumeric and hyphens).")

    description = str(frontmatter.get("description") or "").strip()
    if not description:
        errors.append("SKILL.md frontmatter is missing required 'description' field.")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        warnings.append(f"Description exceeds recommended {MAX_DESCRIPTION_LENGTH} characters.")

    folder_name = os.path.basename(os.path.normpath(skill_dir))
    if name and name != folder_name:
        warnings.append(f"Skill name '{name}' does not match directory name '{folder_name}'.")

    compatibility = str(frontmatter.get("compatibility") or "").strip()
    if compatibility and len(compatibility) > 500:
        warnings.append("Compatibility field exceeds 500 characters.")

    body = parsed.get("body") or ""
    if not body.strip():
        warnings.append("SKILL.md body is empty. Add instructions for best results.")

    return {
        "ok": len(errors) == 0,
        "name": name,
        "description": description,
        "errors": errors,
        "warnings": warnings,
        "frontmatter": frontmatter,
    }


def export_skill(skill_name: str, output_path: str = "") -> dict:
    """Export a skill directory as a portable .zip archive."""
    normalized_name = str(skill_name or "").strip().lower().replace("_", "-")
    if not normalized_name:
        return {"ok": False, "error": "SkillName is required."}

    skill_dir = None
    if os.path.isdir(os.path.join(SKILLS_DIR, normalized_name)):
        skill_dir = os.path.join(SKILLS_DIR, normalized_name)
    else:
        for folder in os.listdir(SKILLS_DIR) if os.path.isdir(SKILLS_DIR) else []:
            if folder.lower().replace("_", "-") == normalized_name:
                skill_dir = os.path.join(SKILLS_DIR, folder)
                break

    if not skill_dir or not os.path.isdir(skill_dir):
        return {"ok": False, "error": f"Skill not found: {skill_name}"}

    validation = validate_skill_dir(skill_dir)
    if not validation["ok"]:
        return {"ok": False, "error": "Skill validation failed.", "validation": validation}

    if not output_path:
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"{normalized_name}.skill.zip",
        )

    output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(skill_dir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for file_name in sorted(files):
                    if file_name.endswith(".pyc"):
                        continue
                    file_path = os.path.join(root, file_name)
                    arc_name = os.path.join(
                        normalized_name,
                        os.path.relpath(file_path, skill_dir).replace("\\", "/"),
                    )
                    zf.write(file_path, arc_name)
    except Exception as error:
        return {"ok": False, "error": f"Failed to create archive: {error}"}

    return {
        "ok": True,
        "skill_name": normalized_name,
        "output_path": output_path,
        "file_size": os.path.getsize(output_path),
    }


def _find_skill_md_root(extract_dir: str) -> str:
    """Find the directory containing SKILL.md within an extracted archive."""
    if os.path.exists(os.path.join(extract_dir, SKILL_MARKDOWN_FILE)):
        return extract_dir
    for entry in os.listdir(extract_dir):
        candidate = os.path.join(extract_dir, entry)
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, SKILL_MARKDOWN_FILE)):
            return candidate
    return ""


def _download_to_temp(url: str) -> str:
    """Download a URL to a temporary file. Returns the file path."""
    import requests

    response = requests.get(url, timeout=120, stream=True)
    response.raise_for_status()
    suffix = ".zip" if ".zip" in url.split("?")[0].lower() else ""
    fd, tmp_path = tempfile.mkstemp(prefix="skill_dl_", suffix=suffix or ".zip")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except BaseException:
        os.unlink(tmp_path)
        raise
    return tmp_path


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def import_skill(source: str, *, overwrite: bool = False) -> dict:
    """Import a skill from a local path, .zip archive, or URL into the skills directory."""
    normalized_source = str(source or "").strip()
    if not normalized_source:
        return {"ok": False, "error": "Source path is required."}

    downloaded_path = ""
    if _is_url(normalized_source):
        try:
            downloaded_path = _download_to_temp(normalized_source)
            normalized_source = downloaded_path
        except Exception as error:
            return {"ok": False, "error": f"Download failed: {error}"}

    cleanup_dir = ""
    try:
        if not os.path.exists(normalized_source):
            return {"ok": False, "error": f"Source not found: {normalized_source}"}

        if os.path.isdir(normalized_source):
            source_dir = normalized_source
        elif zipfile.is_zipfile(normalized_source):
            cleanup_dir = tempfile.mkdtemp(prefix="skill_import_")
            with zipfile.ZipFile(normalized_source, "r") as zf:
                zf.extractall(cleanup_dir)
            source_dir = _find_skill_md_root(cleanup_dir)
            if not source_dir:
                return {"ok": False, "error": "Archive does not contain a SKILL.md file."}
        else:
            return {"ok": False, "error": "Source must be a directory, a .zip archive, or a URL."}

        validation = validate_skill_dir(source_dir)
        if not validation["ok"]:
            return {"ok": False, "error": "Imported skill failed validation.", "validation": validation}

        skill_name = validation["name"]
        target_dir = os.path.join(SKILLS_DIR, skill_name)

        if os.path.exists(target_dir):
            if not overwrite:
                return {
                    "ok": False,
                    "error": f"Skill '{skill_name}' already exists. Set Overwrite=true to replace.",
                    "existing_skill": skill_name,
                }
            shutil.rmtree(target_dir)

        os.makedirs(SKILLS_DIR, exist_ok=True)
        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

        manifest_path = os.path.join(target_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            manifest = {
                "name": skill_name,
                "version": "1.0.0",
                "enabled": True,
                "description": validation.get("description", ""),
                "runtime_mode": "disabled",
                "trusted_runtime": False,
                "source_format": "agent_skill",
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
                f.write("\n")

        return {
            "ok": True,
            "skill_name": skill_name,
            "skill_dir": target_dir,
            "validation": validation,
        }
    except Exception as error:
        return {"ok": False, "error": f"Import failed: {error}"}
    finally:
        if cleanup_dir and os.path.isdir(cleanup_dir):
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        if downloaded_path and os.path.exists(downloaded_path):
            os.unlink(downloaded_path)


def list_exportable_skills() -> dict:
    """List all installed skills with their validation status."""
    if not os.path.isdir(SKILLS_DIR):
        return {"ok": True, "count": 0, "skills": []}

    skills = []
    for folder_name in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, folder_name)
        if not os.path.isdir(skill_dir):
            continue
        skill_md = os.path.join(skill_dir, SKILL_MARKDOWN_FILE)
        manifest_path = os.path.join(skill_dir, "manifest.json")
        if not os.path.exists(skill_md) and not os.path.exists(manifest_path):
            continue

        validation = validate_skill_dir(skill_dir)
        file_count = sum(
            len(files)
            for _, _, files in os.walk(skill_dir)
            if "__pycache__" not in _
        )

        skills.append({
            "name": validation.get("name") or folder_name,
            "folder_name": folder_name,
            "description": validation.get("description", ""),
            "valid": validation["ok"],
            "errors": validation.get("errors", []),
            "warnings": validation.get("warnings", []),
            "file_count": file_count,
        })

    return {"ok": True, "count": len(skills), "skills": skills}


def browse_skill_registry(registry_url: str, *, query: str = "") -> dict:
    """Fetch and optionally filter a remote skill registry index."""
    import requests

    normalized_url = str(registry_url or "").strip()
    if not normalized_url:
        return {"ok": False, "error": "RegistryUrl is required."}

    if not normalized_url.endswith("/"):
        normalized_url += "/"
    index_url = normalized_url + "index.json"

    try:
        response = requests.get(index_url, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        return {"ok": False, "error": f"Failed to fetch registry: {error}"}

    all_skills = data.get("skills") or []
    if not isinstance(all_skills, list):
        return {"ok": False, "error": "Invalid registry format: 'skills' must be an array."}

    normalized_query = str(query or "").strip().lower()
    if normalized_query:
        filtered = [
            s for s in all_skills
            if normalized_query in str(s.get("name", "")).lower()
            or normalized_query in str(s.get("description", "")).lower()
        ]
    else:
        filtered = all_skills

    return {
        "ok": True,
        "registry_url": normalized_url,
        "total": len(all_skills),
        "matched": len(filtered),
        "skills": [
            {
                "name": str(s.get("name", "")).strip(),
                "description": str(s.get("description", "")).strip(),
                "version": str(s.get("version", "")).strip(),
                "download_url": str(s.get("download_url", "")).strip(),
            }
            for s in filtered
        ],
    }


def install_from_registry(registry_url: str, skill_name: str, *, overwrite: bool = False) -> dict:
    """Download and install a skill from a remote registry."""
    import requests

    browse_result = browse_skill_registry(registry_url, query=skill_name)
    if not browse_result["ok"]:
        return browse_result

    matched = [
        s for s in browse_result.get("skills", [])
        if s.get("name", "").lower() == skill_name.lower()
    ]
    if not matched:
        return {
            "ok": False,
            "error": f"Skill '{skill_name}' not found in registry.",
            "available": [s["name"] for s in browse_result.get("skills", [])[:10]],
        }

    download_url = matched[0].get("download_url", "")
    if not download_url:
        return {"ok": False, "error": f"Skill '{skill_name}' has no download URL in the registry."}

    tmp_path = os.path.join(tempfile.gettempdir(), f"{skill_name}.skill.zip")
    try:
        response = requests.get(download_url, timeout=60)
        response.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(response.content)
    except Exception as error:
        return {"ok": False, "error": f"Download failed: {error}"}

    try:
        return import_skill(tmp_path, overwrite=overwrite)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

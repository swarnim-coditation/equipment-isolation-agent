from api_client import Plant360Client


def resolve_pid_image(config, output_dir, stem):
    job_id = config.resolved_job_id
    if not job_id:
        return "", {"pid_image_error": "missing_job_id"}

    client = Plant360Client(config.api)
    try:
        job = client.get_json(f"/jobs/{job_id}")
    except Exception as exc:
        return "", {"pid_image_error": str(exc)}

    file_id = job.get("input_file_image")
    if not file_id:
        return "", {"pid_image_error": "missing_input_file_image"}

    try:
        content, content_type = client.get_bytes(f"/uploads/{file_id}")
    except Exception as exc:
        return "", {"pid_image_error": str(exc), "pid_image_file_id": file_id}

    extension = _extension(content_type, job.get("input_file_type"))
    image_path = output_dir / f"{stem}_pid{extension}"
    image_path.write_bytes(content)
    return image_path.resolve().as_uri(), {
        "pid_image_file_id": file_id,
        "pid_image_path": str(image_path),
        "pid_image_content_type": content_type,
        "pid_image_bytes": len(content),
    }


def _extension(content_type, fallback):
    content_type = str(content_type or "").lower()
    if "png" in content_type:
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    fallback = str(fallback or "").strip().lower().lstrip(".")
    if fallback in {"png", "jpg", "jpeg", "webp"}:
        return f".{fallback}"
    return ".png"

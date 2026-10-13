from config.settings import LESSONS_PATH

PLACEHOLDER_MARKER = "<!-- sin lecciones aprobadas todavía -->"


def load() -> str | None:
    """Lee las lecciones aprobadas por un humano (ver state/lessons.md). Devuelve
    None si el archivo no existe o todavía es el placeholder, para que el CEO no
    reciba un campo vacío sin sentido."""
    if not LESSONS_PATH.exists():
        return None
    text = LESSONS_PATH.read_text().strip()
    if not text or PLACEHOLDER_MARKER in text:
        return None
    return text

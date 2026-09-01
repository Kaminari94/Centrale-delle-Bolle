def normalize_article_code(code):
    """Normalizza le eccezioni di codifica usate dai flussi rapidi."""
    code = (code or "").strip()

    if code == "31103":
        return "031103"
    if code == "31163":
        return "031163"
    if len(code) == 3:
        return "600" + code
    if len(code) == 5:
        return "6" + code
    return code

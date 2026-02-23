# Costanti per 58mm
WIDTH = 32
COL_CODE = 6
COL_DESC = 18
COL_QTY = 6

def separator():
    return "-" * WIDTH

def pad_right(text, width):
    text = str(text)
    return text[:width].ljust(width)

def pad_left(text, width):
    text = str(text)
    return text[:width].rjust(width)

def wrap_text(text, width):
    """
    Wrap semplice senza spezzare brutalmente parole.
    """
    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        if len(current) + len(word) + 1 <= width:
            if current:
                current += " "
            current += word
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines
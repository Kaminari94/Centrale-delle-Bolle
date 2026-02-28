from .formatters import (
    WIDTH,
    COL_CODE,
    COL_DESC,
    COL_QTY,
    BOLD_ON,
    BOLD_OFF,
)
from django.utils import timezone

def pad_right(s, n):
    s = "" if s is None else str(s)
    return (s + " " * n)[:n]

def pad_left(s, n):
    s = "" if s is None else str(s)
    return (" " * n + s)[-n:]

def separator(ch="*"):
    return "[b]" + ch * WIDTH + "[/b]"

def wrap_text(text, width):
    """Wrap semplice su parole (safe)."""
    text = "" if text is None else str(text).strip()
    if not text:
        return [""]
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # clamp
    return [ln[:width] for ln in lines] or [""]

def render_ddt(bolla):
    """
    Render testuale monospazio (ESC/POS-friendly) ispirato al template HTML:
    - intestazione concessionario (sx) / destinazione (dx)
    - seconda intestazione con ramo CLS/non-CLS
    - tabella articoli con descrizione + lotto
    - timbro e firma
    """

    def fix_line(s: str) -> str:
        return pad_right("" if s is None else str(s), WIDTH)

    def join_cols(left: str, right: str) -> str:
        """Due colonne: sinistra + destra, ciascuna metà WIDTH (sx) e resto (dx)."""
        # una divisione bilanciata, con 1 spazio di separazione
        gap = 1
        w_left = (WIDTH - gap) // 2
        w_right = WIDTH - gap - w_left
        return pad_right(left, w_left) + (" " * gap) + pad_right(right, w_right)

    def block_two_cols(left_lines, right_lines):
        """Stampa un blocco a due colonne con numero righe = max(len(left), len(right))."""
        n = max(len(left_lines), len(right_lines))
        for i in range(n):
            l = left_lines[i] if i < len(left_lines) else ""
            r = right_lines[i] if i < len(right_lines) else ""
            out.append(join_cols(l, r))

    def table_header():
        out.append(
            "[b]" +
            pad_right("Cod.", COL_CODE) + " " +
            pad_right("Descrizione", COL_DESC) + " " +
            pad_left("Qnt.", COL_QTY)
            + "[/b]"
        )
        out.append(separator())

    def table_row(code: str, desc: str, qty: str) -> str:
        code_s = pad_right(code, COL_CODE)
        desc_s = pad_right(desc, COL_DESC)
        qty_s = pad_left(qty, COL_QTY)
        return fix_line(f"{code_s} {desc_s} {qty_s}")

    out = []
    out.append(separator())

    # === HEADER (prima "tabella") ===
    conc = getattr(bolla.tipo_documento, "concessionario", None)
    conc_name = getattr(conc, "nome", "") if conc else ""
    conc_addr = getattr(conc, "indirizzo", "") if conc else ""
    conc_piva = getattr(conc, "partita_iva", "") if conc else ""
    conc_tel = getattr(conc, "telefono", "") if conc else ""

    tipo_nome = getattr(bolla.tipo_documento, "nome", "") or ""
    is_cls = "CLS" in tipo_nome

    cliente = bolla.cliente
    dest_nome = getattr(cliente, "nome", "") or ""
    dest_via = getattr(cliente, "via", "") or ""
    dest_cap = getattr(cliente, "cap", "") or ""
    dest_citta = getattr(cliente, "citta", "") or ""
    dest_piva = getattr(cliente, "piva", "") or ""
    dest_cod = getattr(cliente, "codice", "") or ""

    prop = getattr(cliente, "proprietario", None)
    prop_nome = getattr(prop, "nome", "") if prop else ""
    prop_cod = getattr(prop, "codice", "") if prop else ""

    # Colonna sinistra: Concessionario
    left = [
        conc_name,
        conc_addr,
        f"P. IVA: {conc_piva}" if conc_piva else "P. IVA:",
        f"Tel: {conc_tel}" if conc_tel else "Tel:",
    ]

    # Colonna destra: Spett.le (solo CLS) + Destinazione
    right = []
    if is_cls:
        right.append(f"Spett.le: {prop_nome}" if prop_nome else "Spett.le:")
        if prop_cod:
            right.append(f"Codice: {prop_cod}")
    right.append(f"Destinazione: {dest_nome}")
    right.append(f"{dest_via}")
    cap_citta = (f"{dest_cap} {dest_citta}").strip()
    if cap_citta:
        right.append(cap_citta)
    right.append(f"P. IVA: {dest_piva}" if dest_piva else "P. IVA:")
    if is_cls and dest_cod:
        right.append(f"Cod. Dest.: {dest_cod}")

    # Spezziamo le righe lunghe in wrap dentro le metà-colonne
    # (riusiamo wrap_text sulla larghezza di colonna)
    gap = 1
    w_left = (WIDTH - gap) // 2
    w_right = WIDTH - gap - w_left

    def wrap_block(lines, w):
        res = []
        for ln in lines:
            res.extend(wrap_text(ln, w))
        return res

    block_two_cols(wrap_block(left, w_left), wrap_block(right, w_right))
    out.append(separator())

    # === Seconda "tabella" intestazione ===
    numero = getattr(bolla, "numero", "")
    tipo_descr = getattr(bolla.tipo_documento, "descrizione", "") or ""
    data = getattr(bolla, "data", None)
    
    # Convertiamo in local time prima di formattare
    data_str = timezone.localtime(data).strftime("%d/%m/%Y %H:%M") if data else ""

    if is_cls:
        # SX: Bolla N°, Tipo, Data
        left2 = [
            f"Bolla N:",
            f"{numero} / {tipo_nome}",
            f"Tipo: {tipo_descr}",
            f"Data: {data_str}",
        ]
        # DX: Consegna per conto di (se presente)
        cons_conto = getattr(conc, "cons_conto", None) if conc else None
        cc_nome = getattr(cons_conto, "nome", "") if cons_conto else ""
        cc_ind = getattr(cons_conto, "indirizzo", "") if cons_conto else ""
        right2 = ["Consegna per conto di:"]
        if cc_nome:
            right2.append(cc_nome)
        if cc_ind:
            right2.append(cc_ind)

        block_two_cols(wrap_block(left2, w_left), wrap_block(right2, w_right))
    else:
        left2 = [f"[b]   Data:[/b] {data_str}"]
        right2 = [f"Bolla N. {numero} /{tipo_nome}", f"Tipo: {tipo_descr}"]
        block_two_cols(wrap_block(left2, w_left), wrap_block(right2, w_right))

    out.append(separator())

    # === ARTICOLI (tabella) ===
    table_header()

    righe = bolla.righe.all()
    for r in righe:
        codice = getattr(r.articolo, "nome", "") or ""
        descr = getattr(r.articolo, "descrizione", "") or ""
        qty = str(getattr(r, "quantita", "") or "")
        lotto = getattr(r, "lotto", "") or ""

        descr_lines = wrap_text(descr, COL_DESC)
        # prima riga con qty
        out.append(table_row(codice, descr_lines[0] if descr_lines else "", qty))
        # eventuali righe descrizione extra
        for extra in (descr_lines[1:] if descr_lines else []):
            out.append(table_row("", extra, ""))
        # lotto sempre sotto
        if lotto:
            lotto_lines = wrap_text(f"Lotto: {lotto}", COL_DESC)
            for i, ln in enumerate(lotto_lines):
                out.append(table_row("", ln, ""))

    out.append(separator())

    # === Timbro e Firma (come nel template) ===
    out.append(fix_line("Timbro e Firma:".center(WIDTH)))
    # spazi per firma (6 righe come il tuo HTML)
    for _ in range(6):
        out.append(fix_line(""))
    out.append(fix_line(("_" * 16).center(WIDTH)))
    print("\n".join(out))
    return "\n".join(out)
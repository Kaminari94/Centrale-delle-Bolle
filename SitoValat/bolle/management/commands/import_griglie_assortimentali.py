"""Import safely the *Stampa referenze per cliente* supplied by Centrale Bolle.

The source document is a scan.  This command deliberately has a two step
workflow: OCR/extraction is persisted first, then the report is calculated.
No database write is made unless both --apply and --backup-db are supplied.
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import fitz
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bolle.models import Articolo, ArticoliConcessi, Categoria, Cliente, Proprietario


DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
CLIENT_RE = re.compile(r"^\s*(\d{6,})\s+(.+?)\s*$")
ARTICLE_RE = re.compile(r"^\s*(\d{6,})\s+(.+?)\s*$")
HEADER_WORDS = ("PRODOTTO", "DESCRIZ", "CLIENTE", "STAMPA REFERENZE", "Tech-id")
INTERNAL_CODES = ("027110/S", "027110/R")
# These records may be internal wording/administrative articles.  They require
# an explicit review before a complete owner synchronization can be applied.


def parse_date(value):
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Command(BaseCommand):
    help = "Estrae e anteprima l'import sicuro delle griglie assortimentali OCR. Default: dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", help="PDF sorgente scansionato (nuovo OCR)")
        parser.add_argument("--extraction", help="estrazione.json esistente da riutilizzare senza OCR")
        parser.add_argument("--valid-on", help="Data di validita, YYYY-MM-DD")
        parser.add_argument("--output-dir", default="exports/griglie_assortimentali_ocr")
        parser.add_argument("--mapping", help="JSON esplicito: {codice_pdf: {proprietario_id: N, note: '...'}}")
        parser.add_argument("--article-config", help="JSON esplicito per creazioni: {codice: {categoria_id: N}}")
        parser.add_argument("--tesseract", default=r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        parser.add_argument("--dpi", type=int, default=300)
        parser.add_argument("--apply-plan", help="Applica esattamente questo piano_modifiche.json")
        parser.add_argument("--backup-db", help="Copia di backup del DB, da creare e verificare prima dell'applicazione")

    def handle(self, *args, **opts):
        if opts["apply_plan"]:
            if opts["pdf"] or opts["extraction"] or opts["mapping"] or opts["article_config"] or opts["valid_on"]:
                raise CommandError("Con --apply-plan non passare PDF, estrazione, mapping, configurazione articoli o data: il piano e immutabile.")
            if not opts["backup_db"]:
                raise CommandError("--apply-plan richiede obbligatoriamente --backup-db.")
            plan = self._read_json(Path(opts["apply_plan"]), "Piano")
            self._validate_plan(plan)
            backup = self._backup(Path(opts["backup_db"]))
            self._apply(plan)
            self.stdout.write(self.style.SUCCESS(f"Piano applicato in transazione. Backup verificato: {backup}"))
            return
        if opts["backup_db"]:
            raise CommandError("--backup-db si usa solo con --apply-plan; il dry-run non scrive il DB.")
        try:
            valid_on = date.fromisoformat(opts["valid_on"])
        except (ValueError, TypeError) as exc:
            raise CommandError("--valid-on deve essere YYYY-MM-DD") from exc
        if bool(opts["pdf"]) == bool(opts["extraction"]):
            raise CommandError("Indicare esattamente uno fra --pdf (nuovo OCR) e --extraction (riuso OCR).")
        out = Path(opts["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        self._current_output_dir = out
        mapping = self._load_mapping(opts["mapping"])
        if opts["extraction"]:
            extraction_path = Path(opts["extraction"])
            extraction = self._read_json(extraction_path, "Estrazione")
        else:
            pdf, tess = Path(opts["pdf"]), Path(opts["tesseract"])
            if not pdf.is_file(): raise CommandError(f"PDF non trovato: {pdf}")
            if not tess.is_file(): raise CommandError(f"Tesseract non trovato: {tess}")
            extraction = self._extract(pdf, tess, opts["dpi"])
            extraction_path = out / "estrazione.json"
            extraction_path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
        report = self._report(extraction, mapping, valid_on, self._load_mapping(opts["article_config"]))
        report["extraction_file"] = str(extraction_path)
        report["database_fingerprint"] = self._database_fingerprint()
        report["created_at"] = datetime.now().isoformat()
        report["plan_sha256"] = canonical_hash({key: value for key, value in report.items() if key != "plan_sha256"})
        report_path = out / "piano_modifiche.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Legacy compatibility file: deliberately byte-for-byte the same plan,
        # so it can never disagree with the HTML derived from it.
        (out / "anteprima.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path = out / "dry_run.html"
        html_path.write_text(self._html_report(report), encoding="utf-8")
        self.stdout.write(self.style.WARNING("DRY-RUN: database non modificato"))
        self.stdout.write(f"VaLat protetto: Proprietario id={report['protected_valat_id']}")
        self.stdout.write(f"Estrazione: {extraction_path}")
        self.stdout.write(f"Piano JSON: {report_path}\nReport HTML: {html_path}")
        self.stdout.write(self._human_summary(report))

    def _load_mapping(self, filename):
        if not filename:
            return {}
        try:
            raw = json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Mapping non leggibile: {exc}") from exc
        if not isinstance(raw, dict):
            raise CommandError("Il mapping deve essere un oggetto JSON.")
        return raw

    def _read_json(self, filename, label):
        try:
            return json.loads(filename.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"{label} non leggibile: {exc}") from exc

    def _ocr_page(self, page, tess, dpi, number):
        scale = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
        # Keep a reviewable page image alongside the raw text, without relying on a temp file.
        image = Path(self._output_dir) / f"pagina_{number:02d}.png"
        pix.save(image)
        result = subprocess.run(
            [str(tess), str(image), "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode:
            raise CommandError(f"OCR pagina {number} fallito: {result.stderr.strip()}")
        return result.stdout

    def _extract(self, pdf_file, tess, dpi):
        self._output_dir = self._current_output_dir
        document = fitz.open(pdf_file)
        entries, pages, current = [], [], None
        for page_no, page in enumerate(document, start=1):
            text = self._ocr_page(page, tess, dpi, page_no)
            lines = [line.rstrip() for line in text.splitlines() if line.strip()]
            page_record = {"page": page_no, "raw_text": text, "status": "ok", "anomalies": []}
            found_article = False
            for line in lines:
                if any(word.lower() in line.lower() for word in HEADER_WORDS):
                    continue
                match = CLIENT_RE.match(line)
                if match and not DATE_RE.search(line):
                    code, label = match.groups()
                    # An article line always has at least a date; a client heading never does.
                    current = {"source_code": code, "name": label, "page": page_no}
                    page_record.setdefault("clients", []).append(current)
                    entries.append({"type": "client", "page": page_no, "client_code": code,
                                    "name": label, "raw_line": line, "anomalies": []})
                    continue
                match = ARTICLE_RE.match(line)
                if not match:
                    continue
                found_article = True
                code, rest = match.groups()
                dates = DATE_RE.findall(rest)
                anomaly = []
                if not current:
                    anomaly.append("articolo_senza_intestazione_cliente")
                if not dates:
                    anomaly.append("data_inizio_mancante_o_non_ocr")
                parsed = [parse_date(d) for d in dates[:2]]
                if any(x is None for x in parsed):
                    anomaly.append("data_non_valida")
                description = DATE_RE.sub("", rest).strip()
                entries.append({
                    "type": "article", "page": page_no, "client_code": current["source_code"] if current else None,
                    "article_code": code, "description": description,
                    "valid_from": parsed[0] if parsed else None,
                    "valid_to": parsed[1] if len(parsed) > 1 else None,
                    "raw_line": line, "anomalies": anomaly,
                })
            if page_no > 1 and not found_article:
                page_record["anomalies"].append("nessuna_riga_articolo_riconosciuta")
            pages.append(page_record)
        document.close()
        return {"source_pdf": str(pdf_file), "created_at": datetime.now().isoformat(), "pages": pages, "records": entries}

    def _report(self, extraction, mapping, valid_on, article_config):
        valat = Proprietario.objects.filter(nome__iexact="VaLat").first()
        if not valat: raise CommandError("Proprietario VaLat non trovato: impossibile garantire la protezione.")
        grouped = defaultdict(list)
        for item in extraction["records"]:
            if item["type"] == "article": grouped[item["client_code"]].append(item)
        by_code = defaultdict(list)
        for article in Articolo.objects.all().only("id", "nome", "descrizione"): by_code[article.nome].append(article)
        internal_ids = {a.id for code in INTERNAL_CODES for a in by_code[code]}
        try:
            diciture_category = Categoria.objects.get(nome="Diciture")
        except Categoria.DoesNotExist as exc:
            raise CommandError("Categoria Diciture non trovata: impossibile garantire la protezione.") from exc
        diciture_ids = set(Articolo.objects.filter(categoria=diciture_category).values_list("id", flat=True))
        source_clients, seen_owners = [], set()
        for source_code, rows in grouped.items():
            source_name = next((p["name"] for p in extraction["pages"] for p in p.get("clients", []) if p["source_code"] == source_code), "")
            base = {"source_code": source_code, "source_name": source_name, "pages": sorted({r["page"] for r in rows}), "status": "skipped", "anomalies": []}
            mapping_value = mapping.get(source_code)
            if not mapping_value:
                base["reason"] = "cliente_pdf_non_abbinato__richiede_mapping_esplicito"; source_clients.append(base); continue
            try: owner = Proprietario.objects.get(pk=int(mapping_value["proprietario_id"]))
            except (KeyError, TypeError, ValueError, Proprietario.DoesNotExist):
                base["reason"] = "mapping_non_valido_o_proprietario_inesistente"; source_clients.append(base); continue
            base.update(proprietario_id=owner.id, proprietario=owner.nome)
            if owner.id == valat.id:
                base["reason"] = "VaLat_protetto__nessuna_modifica"; source_clients.append(base); continue
            if owner.id in seen_owners:
                base["reason"] = "proprietario_mappato_da_piu_clienti_pdf__ambiguita"; source_clients.append(base); continue
            seen_owners.add(owner.id); anomalies = [a for row in rows for a in row["anomalies"]]
            desired, missing = set(), []
            for row in rows:
                if row["anomalies"]: continue
                start = date.fromisoformat(row["valid_from"]); end = date.fromisoformat(row["valid_to"]) if row["valid_to"] else None
                if start <= valid_on and (end is None or valid_on <= end):
                    matches = by_code[row["article_code"]]
                    if len(matches) == 1 and matches[0].id not in internal_ids and matches[0].id not in diciture_ids: desired.add(matches[0].id)
                    elif len(matches) != 1:
                        missing.append({"code": row["article_code"], "description": row["description"], "page": row["page"], "source_client": source_code, "reason": "articolo_assente" if not matches else "codice_articolo_non_univoco"})
            existing = set(ArticoliConcessi.objects.filter(proprietario=owner).values_list("articolo_id", flat=True)); preserved = existing & internal_ids; diciture = existing & diciture_ids
            base["validity_rows"] = [{"code": r["article_code"], "valid_from": r["valid_from"], "valid_to": r["valid_to"], "page": r["page"]} for r in rows]
            base.update({"add_article_ids": sorted(desired-existing), "remove_article_ids": sorted((existing-desired)-preserved-diciture), "unchanged_article_ids": sorted((existing & desired)-internal_ids), "preserved_article_ids": sorted(preserved), "diciture_article_ids": sorted(diciture), "missing_article_codes": missing, "anomalies": anomalies})
            creatable = [x for x in missing if x["reason"] == "articolo_assente" and self._creation_ready(x["code"], article_config)]
            base["add_article_codes_to_create"] = sorted({x["code"] for x in creatable})
            if anomalies: base["reason"] = "estrazione_ambigua__nessuna_modifica_parziale"
            elif missing and len(creatable) != len(missing): base["reason"] = "articoli_mancanti__sincronizzazione_completa_non_sicura"
            else: base["status"] = "ready"
            source_clients.append(base)
        article_map = {a.id: {"id": a.id, "code": a.nome, "description": a.descrizione or ""} for a in Articolo.objects.all().only("id", "nome", "descrizione")}
        by_owner = {x.get("proprietario_id"): x for x in source_clients if x.get("proprietario_id")}
        owners = []
        for owner in Proprietario.objects.order_by("nome"):
            card = {"proprietario_id": owner.id, "proprietario": owner.nome, "clients": list(Cliente.objects.filter(proprietario=owner).values("id", "codice", "nome").order_by("nome"))}
            if owner.id == valat.id: card.update(status="protected", reason="VaLat escluso esplicitamente: nessuna modifica")
            elif owner.id in by_owner: card.update(by_owner[owner.id])
            else: card.update(status="unchanged", reason="assente_dal_PDF__invariato")
            for key in ("add", "remove", "unchanged", "preserved", "diciture"):
                card[key] = [dict(article_map[x]) for x in card.pop(f"{key}_article_ids", [])]
            for key in ("add", "remove", "unchanged"):
                for article in card[key]:
                    article["reason"] = self._validity_reason(article["code"], card.get("validity_rows", []), valid_on, key)
            card["add"] += [{"id": None, "code": code, "description": next((x["description"] for x in card.get("missing_article_codes", []) if x["code"] == code), ""), "reason": "articolo da creare e associare"} for code in card.pop("add_article_codes_to_create", [])]
            owners.append(card)
        creations = self._creation_proposals(source_clients, article_config)
        return {"valid_on": valid_on.isoformat(), "protected_valat_id": valat.id, "internal_article_codes": list(INTERNAL_CODES), "diciture_category": {"id": diciture_category.id, "name": diciture_category.nome}, "source_clients": source_clients, "owners": owners, "articles_to_create": creations, "unmapped_pdf_clients": [x["source_code"] for x in source_clients if x.get("reason", "").startswith("cliente_pdf_non_abbinato")]}

    def _creation_ready(self, code, config):
        try:
            category_id = int(config[code]["categoria_id"])
            return category_id > 0
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _validity_reason(code, rows, valid_on, action):
        italian = lambda value: date.fromisoformat(value).strftime("%d/%m/%Y")
        relevant = [r for r in rows if r["code"] == code]
        if action in ("add", "unchanged"):
            valid = next((r for r in relevant if r["valid_from"] and date.fromisoformat(r["valid_from"]) <= valid_on and (not r["valid_to"] or valid_on <= date.fromisoformat(r["valid_to"]))), None)
            return f"intervallo valido dal {italian(valid['valid_from'])}" + (f" al {italian(valid['valid_to'])}" if valid and valid["valid_to"] else " senza scadenza") if valid else "intervallo valido"
        if not relevant: return "assente dall'assortimento"
        expired = [r["valid_to"] for r in relevant if r["valid_to"] and date.fromisoformat(r["valid_to"]) < valid_on]
        if expired: return f"validita scaduta il {italian(max(expired))}"
        future = [r["valid_from"] for r in relevant if r["valid_from"] and date.fromisoformat(r["valid_from"]) > valid_on]
        if future: return f"validita non ancora iniziata il {italian(min(future))}"
        return "assente dall'assortimento valido"

    def _creation_proposals(self, source_clients, config):
        grouped = defaultdict(list)
        for item in source_clients:
            if not item.get("proprietario_id") or item.get("anomalies"): continue
            for missing in item.get("missing_article_codes", []):
                if missing["reason"] == "articolo_assente": grouped[missing["code"]].append((item, missing))
        proposals = []
        for code, occurrences in sorted(grouped.items()):
            descriptions = {x[1]["description"] for x in occurrences}
            ready = len(descriptions) == 1 and self._creation_ready(code, config)
            category_id = config.get(code, {}).get("categoria_id") if ready else None
            category_name = Categoria.objects.filter(pk=category_id).values_list("nome", flat=True).first() if ready else None
            if ready and not category_name:
                ready = False
            diciture = category_name == "Diciture"
            if diciture: ready = False
            if ready:
                blocking_reasons = []
            elif diciture:
                blocking_reasons = ["categoria_Diciture_esclusa_dalla_sincronizzazione"]
            elif category_id:
                blocking_reasons = ["categoria_id_inesistente"]
            elif len(descriptions) != 1:
                blocking_reasons = ["descrizioni_OCR_discordanti"]
            else:
                blocking_reasons = ["categoria_obbligatoria_mancante: fornire categoria_id esplicito"]
            proposals.append({"code": code, "description": next(iter(descriptions)) if len(descriptions) == 1 else None, "pages": sorted({x[1]["page"] for x in occurrences}), "proprietari_interessati": sorted({x[0]["proprietario"] for x in occurrences}), "status": "ready" if ready else "blocked", "blocking_reasons": blocking_reasons, "model_defaults_verified": {"iva": 4, "prezzo": "0.0", "prezzo_tr": "0.0", "costo": "0.0"}, "categoria_id": category_id, "categoria_nome": category_name})
        return proposals

    def _backup(self, destination):
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.is_file(): raise CommandError("Backup supportato solo per DB SQLite esistente.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
            raise CommandError("Backup non verificato: dimensione differente.")
        return f"{destination} sha256={hashlib.sha256(destination.read_bytes()).hexdigest()}"

    def _database_fingerprint(self):
        data = {
            "owners": list(Proprietario.objects.order_by("id").values("id", "codice", "nome")),
            "clients": list(Cliente.objects.order_by("id").values("id", "codice", "nome", "proprietario_id")),
            "articles": list(Articolo.objects.order_by("id").values("id", "nome", "descrizione")),
            "grants": list(ArticoliConcessi.objects.order_by("proprietario_id", "articolo_id").values("proprietario_id", "articolo_id")),
        }
        return canonical_hash(data)

    def _validate_plan(self, plan):
        supplied = plan.get("plan_sha256")
        payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
        if not supplied or supplied != canonical_hash(payload):
            raise CommandError("Piano alterato o incompleto: hash non valido.")
        valat = Proprietario.objects.filter(nome__iexact="VaLat").first()
        if not valat or valat.id != plan.get("protected_valat_id"):
            raise CommandError("Protezione VaLat non verificabile: creare una nuova anteprima.")
        if plan.get("database_fingerprint") != self._database_fingerprint():
            raise CommandError("Dati rilevanti cambiati dopo il dry-run: creare una nuova anteprima.")

    def _apply(self, report):
        with transaction.atomic():
            diciture_id = Categoria.objects.get(nome="Diciture").id
            created = {}
            for proposal in report.get("articles_to_create", []):
                if proposal["status"] == "ready" and proposal["categoria_id"] != diciture_id:
                    created[proposal["code"]] = Articolo.objects.create(nome=proposal["code"], descrizione=proposal["description"], categoria_id=proposal["categoria_id"])
            for item in report["owners"]:
                if item["status"] != "ready": continue
                owner = Proprietario.objects.select_for_update().get(pk=item["proprietario_id"])
                ArticoliConcessi.objects.filter(proprietario=owner, articolo_id__in=[x["id"] for x in item["remove"]]).exclude(articolo__categoria_id=diciture_id).exclude(articolo__nome__in=INTERNAL_CODES).delete()
                ids = [x["id"] for x in item["add"] if x["id"] is not None and x["code"] not in INTERNAL_CODES] + [created[x["code"]].id for x in item["add"] if x["id"] is None and x["code"] in created]
                ids = list(Articolo.objects.filter(id__in=ids).exclude(categoria_id=diciture_id).values_list("id", flat=True))
                ArticoliConcessi.objects.bulk_create([ArticoliConcessi(proprietario=owner, articolo_id=x) for x in ids], ignore_conflicts=True)

    @staticmethod
    def _articles(items):
        if not items: return "<p>Nessuno.</p>"
        return "<ul>" + "".join(f"<li><code>{html.escape(x['code'])}</code> — {html.escape(x['description'])}" + (f" <em>({html.escape(x['reason'])})</em>" if x.get("reason") else "") + "</li>" for x in items) + "</ul>"

    def _html_report(self, report):
        totals = {key: sum(len(x[key]) for x in report["owners"]) for key in ("add", "remove", "unchanged", "preserved", "diciture")}
        ignored = ", ".join(report["unmapped_pdf_clients"]) or "nessuno"
        creations = "".join(f"<li><code>{html.escape(x['code'])}</code> — {html.escape(x.get('description') or 'DESCRIZIONE DISCORDANTE')} — categoria proposta: {x.get('categoria_id') or '—'} {html.escape(x.get('categoria_nome') or '')} — pagine {x['pages']} — proprietari {html.escape(', '.join(x['proprietari_interessati']))} — <b>{x['status']}</b> {html.escape('; '.join(x['blocking_reasons']))}</li>" for x in report.get("articles_to_create", [])) or "<li>Nessuno.</li>"
        cards = []
        for owner in report["owners"]:
            clients = ", ".join(f"{html.escape(c['nome'])} ({html.escape(c['codice'])})" for c in owner["clients"]) or "nessun cliente collegato"
            body = f"<h2>{html.escape(owner['proprietario'])} <small>(id {owner['proprietario_id']})</small></h2><p><b>Clienti collegati:</b> {clients}</p><p><b>Stato:</b> {html.escape(owner['status'])} — {html.escape(owner.get('reason',''))}</p>"
            if owner["status"] == "ready": body += f"<h3 class='add'>Da aggiungere</h3>{self._articles(owner['add'])}<h3 class='remove'>Da rimuovere</h3>{self._articles(owner['remove'])}"
            body += f"<h3 class='preserved'>Articoli interni preservati</h3>{self._articles(owner['preserved'])}<h3 class='preserved'>Diciture preservate</h3>{self._articles(owner['diciture'])}<details><summary>Articoli invariati ({len(owner['unchanged'])})</summary>{self._articles(owner['unchanged'])}</details>"
            if owner.get("missing_article_codes") or owner.get("anomalies"):
                block = json.dumps({"articoli_mancanti": owner.get("missing_article_codes", []), "anomalie": owner.get("anomalies", [])}, ensure_ascii=False, indent=2)
                body += f"<h3 class='blocked'>Anomalie e blocchi</h3><pre>{html.escape(block)}</pre>"
            cards.append(f"<section>{body}</section>")
        return f"""<!doctype html><html lang='it'><meta charset='utf-8'><title>Dry-run griglie assortimentali</title><style>body{{font:16px system-ui;margin:2rem;max-width:1100px}}section{{border:1px solid #ddd;padding:1rem;margin:1rem 0}}.add{{color:#087f23}}.remove{{color:#b71c1c}}.preserved{{color:#8a5a00}}.blocked{{color:#8a1c1c}}code{{font-weight:bold}}small{{color:#666}}pre{{white-space:pre-wrap}}</style><h1>Dry-run — sola revisione</h1><p>Nessun pulsante e nessuna azione modificano il database.</p><p>Piano sorgente SHA-256: <code>{report['plan_sha256']}</code></p><p>Data di riferimento: <b>{report['valid_on']}</b>. VaLat (id {report['protected_valat_id']}) è escluso esplicitamente.</p><p>Categoria esclusa permanentemente: <b>{html.escape(report['diciture_category']['name'])}</b> (id {report['diciture_category']['id']}).</p><p>Conteggi: aggiunte {totals['add']}, rimozioni {totals['remove']}, invariati {totals['unchanged']}, articoli interni preservati {totals['preserved']}, Diciture preservate {totals['diciture']}.</p><p>Clienti PDF ignorati perché assenti/non abbinati nel database: {html.escape(ignored)}</p><h2>Articoli da creare</h2><ul>{creations}</ul>{''.join(cards)}</html>"""

    def _human_summary(self, report):
        lines = ["Abbinamenti/proposte:"]
        owners = {owner["proprietario_id"]: owner for owner in report["owners"]}
        for item in report["source_clients"]:
            if item["status"] == "ready":
                owner = owners[item["proprietario_id"]]
                lines.append(f"- PDF {item['source_code']} -> {item['proprietario']} (id {item['proprietario_id']}): +{len(owner['add'])} / -{len(owner['remove'])} / preservati {len(owner['preserved'])}")
            else:
                lines.append(f"- PDF {item['source_code']} ({item.get('source_name','')}): SALTATO — {item.get('reason','anomalia')}")
        return "\n".join(lines)

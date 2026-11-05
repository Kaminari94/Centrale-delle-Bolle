"""Review-only plan for PDF owners not present in the local database."""
import html
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bolle.models import Articolo, ArticoliConcessi, Categoria, Proprietario

INTERNAL_CODES = {"027110/S", "027110/R"}
DISCARDED_LEGACY_CODES = {"600102", "600109"}
VERIFIED_DESCRIPTIONS = {
    "603216": "YOG. COCCO INTERO 2x125g CLS",
    "603217": "YOG.MIRTILLO NERO LIG 2X125CLS",
}


def code_from_pdf(code):
    """Known Centrale convention; retain the source code separately."""
    if code.startswith("19"):
        return code[2:].lstrip("0") or "0"
    return code


def norm(value):
    return " ".join("".join(c if c.isalnum() else " " for c in value.upper()).split())


class Command(BaseCommand):
    help = "Dry-run separato: propone proprietari PDF mancanti, senza scrivere il database."

    def add_arguments(self, parser):
        parser.add_argument("--extraction")
        parser.add_argument("--valid-on")
        parser.add_argument("--existing-mapping", help="Mapping esplicito codice PDF -> proprietario esistente")
        parser.add_argument("--article-config")
        parser.add_argument("--output-dir", default="exports/proprietari_pdf_mancanti")
        parser.add_argument("--apply-plan", help="Applica un piano firmato, con backup obbligatorio")
        parser.add_argument("--render-plan", help="Rigenera solo il report HTML da un piano firmato")
        parser.add_argument("--backup-db")

    def handle(self, *args, **options):
        if options["apply_plan"]:
            if not options["backup_db"]: raise CommandError("--apply-plan richiede --backup-db")
            plan = self._read(options["apply_plan"]); self._validate_plan(plan)
            backup = self._backup(Path(options["backup_db"])); self._apply(plan)
            self.stdout.write(self.style.SUCCESS(f"Piano applicato. Backup verificato: {backup}")); return
        if options["render_plan"]:
            plan = self._read(options["render_plan"]); self._validate_plan(plan)
            out = Path(options["output_dir"]); out.mkdir(parents=True, exist_ok=True)
            report = out / "report_proprietari_mancanti.html"
            report.write_text(self._html(plan), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Fingerprint valido. Report rigenerato: {report}")); return
        if not all(options[x] for x in ("extraction", "valid_on", "existing_mapping", "article_config")):
            raise CommandError("Dry-run richiede --extraction, --valid-on, --existing-mapping e --article-config.")
        try: valid_on = date.fromisoformat(options["valid_on"])
        except ValueError as exc: raise CommandError("--valid-on deve essere YYYY-MM-DD") from exc
        extraction = self._read(options["extraction"]); mappings = self._read(options["existing_mapping"]); categories = self._read(options["article_config"])
        out = Path(options["output_dir"]); out.mkdir(parents=True, exist_ok=True)
        plan = self._plan(extraction, mappings, categories, valid_on)
        plan["database_fingerprint"] = self._fingerprint()
        plan["plan_sha256"] = self._hash({k: v for k, v in plan.items() if k != "plan_sha256"})
        raw = json.dumps(plan, ensure_ascii=False, indent=2)
        (out / "piano_proprietari_mancanti.json").write_text(raw, encoding="utf-8")
        (out / "report_proprietari_mancanti.html").write_text(self._html(plan), encoding="utf-8")
        self.stdout.write(self.style.WARNING("DRY-RUN: nessun proprietario, articolo o associazione e stato modificato."))
        self.stdout.write(f"Piano: {out / 'piano_proprietari_mancanti.json'}\nReport: {out / 'report_proprietari_mancanti.html'}")
        self.stdout.write(f"Esistenti invariati: {len(plan['existing_owners'])}; proposte proprietari: {len(plan['owners_to_create'])}; blocchi: {len(plan['anomalies'])}")

    @staticmethod
    def _hash(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _fingerprint(self):
        data = {"owners": list(Proprietario.objects.order_by("id").values("id", "codice", "nome")), "articles": list(Articolo.objects.order_by("id").values("id", "nome", "descrizione", "categoria_id")), "grants": list(ArticoliConcessi.objects.order_by("proprietario_id", "articolo_id").values("proprietario_id", "articolo_id"))}
        return self._hash(data)

    def _validate_plan(self, plan):
        payload = {k: v for k, v in plan.items() if k != "plan_sha256"}
        if plan.get("plan_sha256") != self._hash(payload): raise CommandError("Piano alterato: hash non valido.")
        if plan.get("database_fingerprint") != self._fingerprint(): raise CommandError("Database cambiato dopo l'anteprima: rigenerare il piano.")
        if any(x["status"] != "ready" for x in plan["owners_to_create"]) or any(x["status"] != "ready" for x in plan["articles_to_create_global"]): raise CommandError("Il piano contiene blocchi.")

    def _backup(self, destination):
        source = Path(settings.DATABASES["default"]["NAME"]); destination.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(source, destination)
        if destination.stat().st_size != source.stat().st_size: raise CommandError("Backup non verificato.")
        return f"{destination} sha256={hashlib.sha256(destination.read_bytes()).hexdigest()}"

    def _apply(self, plan):
        with transaction.atomic():
            diciture_id = Categoria.objects.get(nome="Diciture").id; owners = {}
            for item in plan["owners_to_create"]:
                same_code = list(Proprietario.objects.filter(codice=item["internal_code"]))
                if same_code:
                    if len(same_code) != 1 or same_code[0].nome != item["name"]: raise CommandError(f"Conflitto proprietario per codice {item['internal_code']}")
                    owner = same_code[0]
                else: owner = Proprietario.objects.create(codice=item["internal_code"], nome=item["name"])
                owners[item["internal_code"]] = owner
            articles = {}
            for item in plan["articles_to_create_global"]:
                found = list(Articolo.objects.filter(nome=item["code"]))
                if len(found) > 1: raise CommandError(f"Codice articolo non univoco: {item['code']}")
                articles[item["code"]] = found[0] if found else Articolo.objects.create(nome=item["code"], descrizione=item["description"], categoria_id=item["category_id"])
            for item in plan["owners_to_create"]:
                owner = owners[item["internal_code"]]; ids = [x["article_id"] for x in item["articles_existing_to_grant"]] + [articles[x].id for x in item["article_create_refs"]]
                ids = list(Articolo.objects.filter(id__in=ids).exclude(categoria_id=diciture_id).exclude(nome__in=INTERNAL_CODES).values_list("id", flat=True))
                ArticoliConcessi.objects.bulk_create([ArticoliConcessi(proprietario=owner, articolo_id=x) for x in ids], ignore_conflicts=True)

    @staticmethod
    def _read(filename):
        try: return json.loads(Path(filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise CommandError(f"JSON non leggibile: {exc}") from exc

    def _plan(self, extraction, mappings, config, valid_on):
        grouped, names = defaultdict(list), {}
        for record in extraction["records"]:
            if record["type"] == "client": names.setdefault(record["client_code"], record["name"])
            elif record["type"] == "article": grouped[record["client_code"]].append(record)
        articles = defaultdict(list)
        for article in Articolo.objects.all().only("id", "nome", "descrizione", "categoria_id"):
            articles[article.nome].append(article)
        diciture_id = Categoria.objects.get(nome="Diciture").id
        owners_by_id = {x.id: x for x in Proprietario.objects.all()}
        owner_by_code = defaultdict(list)
        for owner in owners_by_id.values(): owner_by_code[owner.codice].append(owner)
        existing, proposed, anomalies = [], [], []
        for source_code, rows in grouped.items():
            source_name, internal = names.get(source_code, ""), code_from_pdf(source_code)
            explicit = mappings.get(source_code)
            if explicit:
                try: owner = owners_by_id[int(explicit["proprietario_id"])]
                except (KeyError, TypeError, ValueError):
                    anomalies.append({"source_code": source_code, "reason": "mapping_esistente_non_valido"}); continue
                existing.append({"source_code": source_code, "pdf_name": source_name, "owner_id": owner.id, "owner_name": owner.nome, "owner_code": owner.codice, "reason": "mapping_esplicito__invariato"}); continue
            code_matches = owner_by_code.get(internal, [])
            if len(code_matches) == 1:
                owner = code_matches[0]
                if norm(source_name) == norm(owner.nome):
                    existing.append({"source_code": source_code, "pdf_name": source_name, "owner_id": owner.id, "owner_name": owner.nome, "owner_code": owner.codice, "reason": "codice_e_denominazione_coerenti__invariato"}); continue
                anomalies.append({"source_code": source_code, "pdf_name": source_name, "internal_code": internal, "reason": "conflitto_codice_con_proprietario_esistente", "existing_owner": {"id": owner.id, "code": owner.codice, "name": owner.nome}}); continue
            if len(code_matches) > 1:
                anomalies.append({"source_code": source_code, "pdf_name": source_name, "internal_code": internal, "reason": "codice_interno_non_univoco"}); continue
            proposed.append(self._new_owner(source_code, source_name, internal, rows, articles, config, diciture_id, valid_on))
        global_creates = self._dedupe_creates(proposed)
        blocked_codes = {x["code"] for x in global_creates if x["status"] == "blocked"}
        for owner in proposed:
            owner["article_create_refs"] = sorted(x["code"] for x in owner.pop("articles_to_create"))
            if blocked_codes.intersection(owner["article_create_refs"]): owner["status"] = "blocked"
        return {"valid_on": valid_on.isoformat(), "scope": "dry-run proprietari PDF mancanti; nessuna scrittura", "existing_owners": existing, "owners_to_create": proposed, "articles_to_create_global": global_creates, "anomalies": anomalies, "protections": {"valat": "escluso", "internal_articles": sorted(INTERNAL_CODES), "excluded_category": "Diciture", "discarded_legacy_codes": sorted(DISCARDED_LEGACY_CODES)}, "application_rules": "Un solo Articolo per codice; riusare l'eventuale articolo esistente senza modificarlo; bloccare codici non univoci."}

    def _new_owner(self, source_code, name, internal, rows, articles, config, diciture_id, valid_on):
        desired, missing, anomalies = [], defaultdict(list), []
        for row in rows:
            if row["anomalies"]:
                anomalies.append({"page": row["page"], "code": row["article_code"], "reason": row["anomalies"]}); continue
            start = date.fromisoformat(row["valid_from"]); end = date.fromisoformat(row["valid_to"]) if row["valid_to"] else None
            if not (start <= valid_on and (end is None or valid_on <= end)): continue
            if row["article_code"] in DISCARDED_LEGACY_CODES:
                continue
            found = articles[row["article_code"]]
            if len(found) == 1:
                if found[0].nome not in INTERNAL_CODES and found[0].categoria_id != diciture_id: desired.append({"code": found[0].nome, "description": found[0].descrizione or "", "article_id": found[0].id})
            elif not found:
                copy = dict(row); copy["description"] = VERIFIED_DESCRIPTIONS.get(row["article_code"], row["description"]); missing[row["article_code"]].append(copy)
            else:
                anomalies.append({"page": row["page"], "code": row["article_code"], "reason": "codice_articolo_non_univoco"})
        creates = []
        for code, items in missing.items():
            descriptions = {x["description"] for x in items}
            cfg = config.get(code, {}); category_id = cfg.get("categoria_id")
            category = Categoria.objects.filter(pk=category_id).first() if category_id else None
            suggestions = [] if category else self._suggest_categories(next(iter(descriptions)) if len(descriptions) == 1 else "")
            blockers = []
            if len(descriptions) != 1: blockers.append("descrizioni_OCR_discordanti")
            if not category: blockers.append("categoria_mancante_o_inesistente")
            if category and category.id == diciture_id: blockers.append("categoria_Diciture_esclusa")
            creates.append({"code": code, "description": next(iter(descriptions)) if len(descriptions) == 1 else None, "pages": sorted({x["page"] for x in items}), "category_id": category.id if category else None, "category_name": category.nome if category else None, "category_suggestions": suggestions, "status": "blocked" if blockers else "ready", "blocking_reasons": blockers})
        return {"source_pdf_code": source_code, "internal_code": internal, "name": name, "status": "blocked" if anomalies or any(x["status"] == "blocked" for x in creates) else "ready", "articles_existing_to_grant": sorted(desired, key=lambda x: x["code"]), "articles_to_create": creates, "anomalies": anomalies}

    @staticmethod
    def _dedupe_creates(owners):
        grouped = defaultdict(list)
        for owner in owners:
            for item in owner["articles_to_create"]:
                grouped[item["code"]].append((owner, item))
        result = []
        for code, occurrences in sorted(grouped.items()):
            descriptions = {item["description"] for _, item in occurrences}
            categories = {(item["category_id"], item["category_name"]) for _, item in occurrences}
            blockers = sorted({reason for _, item in occurrences for reason in item["blocking_reasons"]})
            if len(descriptions) != 1: blockers.append("descrizioni_discordanti_per_stesso_codice")
            if len(categories) != 1: blockers.append("categorie_discordanti_per_stesso_codice")
            result.append({"code": code, "description": next(iter(descriptions)) if len(descriptions) == 1 else None, "category_id": next(iter(categories))[0] if len(categories) == 1 else None, "category_name": next(iter(categories))[1] if len(categories) == 1 else None, "owners": [{"pdf_code": owner["source_pdf_code"], "internal_code": owner["internal_code"], "name": owner["name"]} for owner, _ in occurrences], "status": "blocked" if blockers else "ready", "blocking_reasons": sorted(set(blockers))})
        return result

    @staticmethod
    def _suggest_categories(description):
        """Conservative suggestions only; they never unlock a creation."""
        text = norm(description)
        rules = [
            (("LATTE", "ESL"), [16]), (("LATTE", "UHT"), [7]), (("LATTE", "BIO"), [15]),
            (("KEFIR",), [13]), (("YOG", "S LATT"), [12]), (("YOG", "MAGRO"), [11]),
            (("YOG", "LIGHT"), [11]), (("YOG",), [6, 11]), (("PASTA", "FRESCA"), [18]),
            (("PESTO",), [19]), (("MOZZ",), [20, 23]), (("BEVANDA",), [14]), (("LATTE",), [1, 7, 16]),
        ]
        for words, ids in rules:
            if all(word in text for word in words):
                return [{"id": c.id, "name": c.nome} for c in Categoria.objects.filter(id__in=ids).order_by("id")]
        return []

    def _html(self, plan):
        existing = "".join(f"<li><code>{html.escape(x['source_code'])}</code> {html.escape(x['pdf_name'])} → {html.escape(x['owner_name'])} ({html.escape(x['owner_code'])}), {html.escape(x['reason'])}</li>" for x in plan["existing_owners"]) or "<li>Nessuno</li>"
        blocks = "".join(f"<li>{html.escape(json.dumps(x, ensure_ascii=False))}</li>" for x in plan["anomalies"]) or "<li>Nessuno</li>"
        global_creates = "".join(f"<li><code>{html.escape(x['code'])}</code> — {html.escape(x.get('description') or 'DESCRIZIONE DISCORDANTE')} — categoria {x['category_id'] or '—'} {html.escape(x.get('category_name') or '')} — destinatari: {html.escape(', '.join(o['name'] for o in x['owners']))} — <b>{x['status']}</b> {html.escape('; '.join(x['blocking_reasons']))}</li>" for x in plan["articles_to_create_global"]) or "<li>Nessuno</li>"
        cards = []
        for o in plan["owners_to_create"]:
            grants = "".join(f"<li><code>{html.escape(x['code'])}</code> — {html.escape(x['description'])}</li>" for x in o["articles_existing_to_grant"]) or "<li>Nessuno</li>"
            refs = "".join(f"<li><code>{html.escape(code)}</code></li>" for code in o["article_create_refs"]) or "<li>Nessuno</li>"
            cards.append(f"<section><h2>{html.escape(o['name'])}</h2><p>Codice PDF: <code>{o['source_pdf_code']}</code>; codice interno proposto: <code>{o['internal_code']}</code>; stato: <b>{o['status']}</b></p><h3>Articoli esistenti da concedere ({len(o['articles_existing_to_grant'])})</h3><ul>{grants}</ul><h3>Riferimenti agli articoli da creare globali</h3><ul>{refs}</ul><pre>{html.escape(json.dumps(o['anomalies'], ensure_ascii=False, indent=2))}</pre></section>")
        return f"<!doctype html><meta charset='utf-8'><title>Proprietari PDF mancanti</title><style>body{{font:16px system-ui;margin:2rem;max-width:1100px}}section{{border:1px solid #ddd;padding:1rem;margin:1rem 0}}code{{font-weight:bold}}pre{{white-space:pre-wrap}}</style><h1>Dry-run proprietari PDF mancanti</h1><p>Nessuna modifica al database. VaLat, cestelli, Diciture e codici legacy 600102/600109 sono esclusi.</p><p>Hash piano: <code>{html.escape(plan.get('plan_sha256', 'non presente'))}</code></p><h2>Esistenti riconosciuti e invariati</h2><ul>{existing}</ul><h2>Anomalie/conflitti</h2><ul>{blocks}</ul><h2>Articoli unici da creare globalmente</h2><ul>{global_creates}</ul><h2>Proprietari da creare</h2>{''.join(cards)}"

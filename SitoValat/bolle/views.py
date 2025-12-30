import base64

from PyPDF2 import PdfMerger
from django.views.generic import TemplateView, ListView, DetailView
from django.views.generic import DeleteView, UpdateView, CreateView
from django.contrib.auth.views import LoginView
from django.contrib.auth import user_logged_in
from decimal import Decimal
from .models import *
import fitz
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.contrib import messages
from datetime import timedelta, datetime, date
from django.db.models import Prefetch
from django.forms import ModelForm
from django.forms.models import inlineformset_factory
from django.views.generic.edit import FormView
from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.core.files.storage import default_storage
from .utils.genera_pdf import genera_pdf_base64
from .utils.parser import parse_file
from django.utils.dateparse import parse_date
from pdfminer.high_level import extract_text
from django.http import FileResponse
import os
from django.utils.timezone import make_aware, now, is_aware
from django.db.models import Sum, Q, F
from django.utils import timezone
from django.conf import settings
from .utils import centrale_fattura
from .utils.gen_pdf_bolla import genera_pdf_bolla

def ensure_aware(dt, tz):
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt, tz)

class HomePageView(TemplateView):
    template_name = 'bolle/homepage.html'

    def get_context_data(self, *args, **kwargs):
        user = self.request.user
        context = super().get_context_data(**kwargs)
        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo i clienti della zona
            context["clienti"] = Cliente.objects.filter(zona=user.zona).exclude(
            tipo_documento_predefinito__nome="NTV").order_by("tipo_documento_predefinito__nome")
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra tutti i clienti del concessionario
            context["clienti"] = Cliente.objects.filter(concessionario=user.concessionario).exclude(
            tipo_documento_predefinito__nome="NTV").order_by("tipo_documento_predefinito__nome")
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            context["clienti"] = Cliente.objects.none()

        return context

    def post(self, request):
        cliente_id = request.POST.get("cliente")
        articoli = request.POST.get("articoli")
        try:
            with transaction.atomic():
                if cliente_id and articoli:
                    cliente = get_object_or_404(Cliente, pk=cliente_id)
                    articoli_list = articoli.strip().split("\n")
                    somma = 0
                    for articolo in articoli_list:
                        parts = articolo.split()
                        if len(parts) != 2:
                            context = self.get_context_data()
                            context['messages'] = [f"Ci sono valori non numerici. Modificare la lista."]
                            context['articoli_list'] = articoli
                            context['cliente_selezionato'] = str(cliente.pk)
                            return render(request, self.template_name, context)
                        codice, quantita = parts

                        quantita = int(quantita)
                        somma += quantita
                    if somma <= 0:
                        context = self.get_context_data()
                        context['messages'] = [f"Somma delle quantità minore o uguale a zero."]
                        context['articoli_list'] = articoli
                        context['cliente_selezionato'] = str(cliente.pk)
                        return render(request, self.template_name, context)
                    # Dopo i primi controlli si crea la bolla.
                    bolla = Bolla.objects.create(
                        cliente=cliente,
                        tipo_documento=cliente.tipo_documento_predefinito,
                    )
                    bolla.save()
                    arti = set(Articolo.objects.values_list('nome', flat=True))
                    articoli_concessi = ArticoliConcessi.objects.filter(
                        proprietario=cliente.proprietario
                    ).values_list('articolo', flat=True)

                    for articolo in articoli_list:
                        codice, quantita = articolo.split()
                        quantita = int(quantita)
                        #print(codice)
                        if quantita == 0:
                            continue

                        if codice == '31103':
                            codice = "031103"
                        elif codice == '31163':
                            codice = "031163"
                        elif len(codice) == 3:
                            codice = "600" + codice
                        elif len(codice) == 5:
                            codice = "6" + codice
                        if codice not in arti:
                            context = self.get_context_data()
                            context['messages'] = [f"Articolo errato: {codice}."]
                            context['articoli_list'] = articoli
                            context['cliente_selezionato'] = str(cliente.pk)
                            tipo_documento = bolla.tipo_documento
                            anno = (bolla.data or timezone.now()).year

                            # Lock & decrement the annual counter
                            counter = (TipoDocCounter.objects
                                       .select_for_update()
                                       .get(tipo=tipo_documento, anno=anno))

                            if counter.ultimo_numero > 0:
                                counter.ultimo_numero -= 1
                                counter.save(update_fields=["ultimo_numero"])

                                # Optional cache sync
                                tipo_documento.ultimo_numero = counter.ultimo_numero
                                tipo_documento.save(update_fields=["ultimo_numero"])

                            bolla.delete()
                            return render(request, self.template_name, context)
                        articolo = Articolo.objects.get(nome=codice)
                        if articolo.pk not in articoli_concessi: # Se l'articolo non è presente negli articoli concessi
                            context = self.get_context_data()
                            context['messages'] = [f"Articolo {codice} non concesso a {cliente.nome} {cliente.via}."]
                            context['articoli_list'] = articoli
                            context['cliente_selezionato'] = str(cliente.pk)
                            tipo_documento = bolla.tipo_documento
                            anno = (bolla.data or timezone.now()).year

                            # Lock & decrement the annual counter
                            counter = (TipoDocCounter.objects
                                       .select_for_update()
                                       .get(tipo=tipo_documento, anno=anno))

                            if counter.ultimo_numero > 0:
                                counter.ultimo_numero -= 1
                                counter.save(update_fields=["ultimo_numero"])

                                # Optional cache sync
                                tipo_documento.ultimo_numero = counter.ultimo_numero
                                tipo_documento.save(update_fields=["ultimo_numero"])

                            bolla.delete()
                            return render(request, self.template_name, context)

                        ultimo_carico =  RigaCarico.objects.filter(articolo=articolo).order_by('-carico__data').first()

                        riga = RigaBolla.objects.create(
                            bolla=bolla,
                            articolo=articolo,
                            quantita=quantita,
                            lotto= ultimo_carico.lotto if ultimo_carico else "---",
                        )
                        riga.save() #Salviamo la rigaaaaa e via con la prossima babydoll
                    return redirect('bolla-detail', pk=bolla.pk)  # Redirige alla lista delle bolle, dove c'è il bottone per stampare che pd non funziona su android
                else:
                    # Gestisci gli errori di input
                    context = self.get_context_data()
                    context['messages'] = ["Dati mancanti o non validi."]
                    return render(request, self.template_name, context)
        except Exception as e:
            print(f"Errore durante la creazione della bolla veloce: {e}")

class BollaListView(LoginRequiredMixin, ListView):
    model = Bolla
    template_name = 'bolle/bolle_list.html'
    context_object_name = 'bolle'
    ordering = ['-numero']

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        tipo_documento_id = self.request.GET.get('tipo_documento')

        # Gestione delle date
        oggi = make_aware(datetime.now())
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)

        # Filtro per tipo documento
        if not tipo_documento_id:
            queryset = queryset.filter(data__range=(data_inizio, data_fine))
            return queryset

        queryset = queryset.filter(tipo_documento_id=tipo_documento_id, data__range=(data_inizio, data_fine))
        # print(queryset.all())
        # print(tipo_documento_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        oggi = make_aware(datetime.now())

        # Gestione delle date
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)
        tipo_documento_id = self.request.GET.get('tipo_documento')

        context['data_inizio'] = data_inizio
        context['data_fine'] = data_fine
        # print(tipo_documento_id)
        if not tipo_documento_id:
            if hasattr(user, 'zona'):
                tipi = TipoDocumento.objects.filter(concessionario=user.zona.concessionario)
                context['tipi_documento'] = TipoDocumento.objects.filter(concessionario=user.zona.concessionario)
                bolle = Bolla.objects.filter(tipo_documento__in=tipi, data__range=(data_inizio, data_fine))
                context['tipo_documento_id'] = ""
            elif hasattr(user, 'concessionario'):
                tipi = TipoDocumento.objects.filter(concessionario=user.concessionario)
                context['tipi_documento'] = TipoDocumento.objects.filter(concessionario=user.concessionario)
                bolle = Bolla.objects.filter(tipo_documento__in=tipi, data__range=(data_inizio, data_fine))
                context['tipo_documento_id'] = ""
            else:
                tipi = TipoDocumento.objects.none()
                context['tipi_documento'] = TipoDocumento.objects.none()
                context['tipo_documento_id'] = ""
            bolle = Bolla.objects.filter(tipo_documento__in=tipi, data__range=(data_inizio, data_fine))
            context['bolle'] = bolle
            return context
        else:
            bolle = Bolla.objects.filter(tipo_documento_id = tipo_documento_id, data__range=(data_inizio, data_fine))
            context['bolle'] = bolle
            context['tipo_documento_id'] = tipo_documento_id
            if hasattr(user, 'zona'):
                tipi = TipoDocumento.objects.filter(concessionario=user.zona.concessionario)
                context['tipi_documento'] = TipoDocumento.objects.filter(concessionario=user.zona.concessionario)
            elif hasattr(user, 'concessionario'):
                tipi = TipoDocumento.objects.filter(concessionario=user.concessionario)
                context['tipi_documento'] = TipoDocumento.objects.filter(concessionario=user.concessionario)
            else:
                tipi = TipoDocumento.objects.none()
                context['tipi_documento'] = TipoDocumento.objects.none()
            return context

    def get_data_filtrata(self, data_str, oggi, inizio=True):
        """Funzione ausiliaria per calcolare data_inizio e data_fine"""
        if data_str:
            data = make_aware(datetime.strptime(data_str, '%Y-%m-%d'))
            if inizio:
                data = datetime.combine(data, datetime.min.time())
                data = data.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                data = datetime.combine(data, datetime.max.time())
                data = data.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Se non viene fornita una data, si usa quella corrente
            if inizio:
                data = oggi.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                data = oggi.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Verifica se la data è già aware, se sì non applicare make_aware
        if not is_aware(data):
            data = make_aware(data, timezone.get_current_timezone())

        return data

class BollaDetailView(LoginRequiredMixin, DetailView):
    model = Bolla
    template_name = 'bolle/bolla_detail.html'  # Template per il dettaglio
    context_object_name = 'bolla'  # Nome del contesto per il template

class BollaDeleteView(DeleteView):
    model = Bolla
    success_url = reverse_lazy('bolle-list')
    template_name = 'bolle/bolla_delete.html'

    def post(self, request, *args, **kwargs):
        bolla = self.get_object()
        tipo = bolla.tipo_documento
        anno = (bolla.data or timezone.now()).year

        with transaction.atomic():
            counter = (TipoDocCounter.objects
                       .select_for_update()
                       .get(tipo=tipo, anno=anno))

            ultima_bolla = (Bolla.objects
                            .filter(tipo_documento=tipo, data__year=anno)
                            .order_by('-numero')
                            .first())

            if bolla != ultima_bolla:
                messages.error(request, "Puoi eliminare solo l'ultima bolla di quell'anno per quel tipo documento.")
                return redirect('bolle-list')

            counter.ultimo_numero -= 1
            counter.save(update_fields=["ultimo_numero"])

            # cache (optional)
            tipo_doc = TipoDocumento.objects.select_for_update().get(pk=tipo.pk)
            tipo_doc.ultimo_numero = counter.ultimo_numero
            tipo_doc.save(update_fields=["ultimo_numero"])

            bolla.delete()

        return redirect(self.success_url)

class BollaUpdateView(LoginRequiredMixin, UpdateView):
    model = Bolla
    fields = ['cliente']  # Campi modificabili
    template_name = 'bolle/bolla_form.html'
    success_url = reverse_lazy('bolle-list')  # Dopo la modifica, torna alla lista

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.object.cliente
        categoria_selezionata = self.request.GET.get('categoria')
        if categoria_selezionata is None:
            categoria_selezionata = 0
        categoria_selezionata = int(categoria_selezionata)
        context["categoria_selezionata"] = str(categoria_selezionata)
        if categoria_selezionata == 0:
            # Se user non seleziona categoria, mostra articoli del proprietario (valat di solito)
            articoli_concessi = ArticoliConcessi.objects.filter(
                proprietario=cliente.proprietario
            ).values_list('articolo', flat=True)
        else:
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)
        # Ottieni gli articoli concessi al proprietario del cliente

        context['righe'] = self.object.righe.all()
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi).order_by('nome'))
        ).order_by('ordine')
        return context

    def post(self, request, *args, **kwargs):
        categoria_selezionata = request.POST.get('categoria')  # Nascosto nel form, serve per mantenere la cat. selezionata
        if 'add_riga' in request.POST:
            #aggiungi riga alla bolla
            articolo_id = request.POST.get('articolo')
            quantita = request.POST.get('quantita')
            lotto = request.POST.get('lotto')
            # Verifica se la quantità è valida
            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('bolla-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            if not articolo_id or articolo_id == "":
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('bolla-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            # Recupera l'ultimo lotto dal carico, se esiste
            articolo = Articolo.objects.get(pk=articolo_id)
            ultimo_carico = RigaCarico.objects.filter(articolo=articolo).order_by('-carico__data').first()
            if not lotto: # se il lotto non è fornito dall'utente, viene generato automaticamente
                if ultimo_carico:
                    lotto = ultimo_carico.lotto
                else:
                    # Genera un lotto predefinito se non trovato
                        oggi = now() + timedelta(days=5)
                        lotto = oggi.strftime('%d%m%y')
            if articolo.categoria.nome == "Imballaggio":
                lotto = "---"

            RigaBolla.objects.create(
                bolla = self.get_object(),
                articolo_id = articolo_id,
                quantita = quantita,
                lotto = lotto
            )
            return redirect(f"{reverse('bolla-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
        elif 'confirm' in request.POST:
            # Conferma la modifica e salva la bolla
            return redirect('bolle-list')
        return super().post(request, *args, **kwargs)

class BollaCreateView(LoginRequiredMixin, CreateView):
    model = Bolla
    template_name = "bolle/bolla_create.html"
    fields = ['cliente', 'note']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user
        if hasattr(user, 'zona'):
            form.fields['cliente'].queryset = Cliente.objects.filter(zona=user.zona).exclude(
            tipo_documento_predefinito__nome="NTV").order_by("tipo_documento_predefinito__nome")
        elif hasattr(user, 'concessionario'):
            form.fields['cliente'].queryset = Cliente.objects.filter(concessionario=user.concessionario).exclude(
            tipo_documento_predefinito__nome="NTV").order_by("tipo_documento_predefinito__nome")
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            messages.error(self.request, "Non hai i permessi per creare una bolla.")
            self.success_url = reverse_lazy('bolle-list')

        form.fields['cliente'].label = "Tipo Bolla e Cliente:"
        form.fields['note'].label = "Eventuali Note:"
        form.fields['note'].widget.attrs.update({
            'rows':1,
            'maxlength':'255',
            'placeholder': 'Inserisci eventuali note. Per far si che questa bolla venga contata nel conto del giorno dopo quello attuale inserire "conto domani".'
        })

        return form

    def form_valid(self, form):
        bolla = form.save(commit=False)
        cliente = form.cleaned_data['cliente']
        bolla.tipo_documento = cliente.tipo_documento_predefinito
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('bolla-update', kwargs={'pk':self.object.pk})

class UserLoginView(LoginView):
    template_name = 'bolle/login.html'

class RigaBollaDeleteView(DeleteView):
    model = RigaBolla
    def get_success_url(self):
        bolla_id = self.object.bolla.id
        return reverse('bolla-update', kwargs={'pk':bolla_id})

class BollaStampaView(DetailView):
    model = Bolla
    template_name = "bolle/bolla_stampa.html"

def BollaStampaViewPDF(request, pk):
    bolla = get_object_or_404(Bolla, pk=pk)

    pdf_buffer = genera_pdf_bolla(bolla)
    response = HttpResponse(pdf_buffer, content_type="application/pdf")
    nome_file = f'Bolla-N-{bolla.numero}-{bolla.cliente.nome.replace(" ", "_")}.pdf'
    response['Content-Disposition'] = f'attachment; filename={nome_file}'
    return response

class ArticoliConcessiForm(ModelForm):
    class Meta:
        model = ArticoliConcessi
        fields = ['articolo']

ArticoliConcessiFormSet = inlineformset_factory(
    Proprietario,
    ArticoliConcessi,
    form=ArticoliConcessiForm,
    extra=5,  # Numero di righe aggiuntive
)

class ArticoliConcessiUpdateView(LoginRequiredMixin, FormView):
    template_name = 'bolle/articoli_concessi_update.html'
    form_class = ArticoliConcessiFormSet
    success_url = reverse_lazy('proprietari-list')

    def get_form(self):
        proprietario = Proprietario.objects.get(pk=self.kwargs['pk'])
        return ArticoliConcessiFormSet(instance=proprietario)

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)

class ImportFileView(View):
    template_name = "bolle/import_file.html"

    def get(self, request):
         return render(request, self.template_name)

    def post(self, request):
        file_upload = request.FILES.get('file')

        if not file_upload:
            return render(request, self.template_name, {"error":"Nessun file caricato."})

        #salva il file temporaneamente

        relative_path = os.path.join('temp', file_upload.name)
        print(f"File salvato in: {relative_path}")
        temp_file_path = default_storage.save(relative_path, file_upload)
        print(f"File salvato in: {default_storage.path(temp_file_path)}")

        try:
            #Prova a fare il parsing
            parsed_data = parse_file(default_storage.path(temp_file_path))

            context = {
                "header": parsed_data["header"],
                "clienti": parsed_data["clienti"],
                "bolle": parsed_data["bolle"],
                "articoli": parsed_data["articoli"],
                "file_name": file_upload.name
            }

            request.session['parsed_data'] = context
            return render(request, self.template_name, context)
        finally:
            default_storage.delete(temp_file_path)

class ConfirmImportView(View):
    def post(self, request):
        parsed_data = request.session.get('parsed_data')

        if not parsed_data:
            return redirect("import-file")

        current_bolla_number = int(parsed_data["bolle"][0]["numero_bolla"])  # Partiamo dal primo numero di bolla nel file
        tipo_doc = ""
        for bolla in parsed_data["bolle"]:
            try:
                cliente = Cliente.objects.get(codice=bolla["codice_cliente"])
            except Cliente.DoesNotExist:
                print(f"Cliente con codice {bolla['codice_cliente']} non trovato. Bolla ignorata.")
                current_bolla_number +=1
                continue

            tipo_doc = cliente.tipo_documento_predefinito
            strdata = bolla["data"]
            data = datetime.strptime(strdata, "%d%m%Y").date()
            lotto = data + timedelta(days=5)
            data = datetime.strptime(strdata, "%d%m%Y").replace(hour=12, minute=0, second=0)
            data = timezone.make_aware(data)
            numero_bolla = int(bolla["numero_bolla"])
            if current_bolla_number != numero_bolla:
                print(f"Bolla n.{current_bolla_number} non valida. Modifico numero in {numero_bolla} .")
                current_bolla_number = numero_bolla

            try:
                with transaction.atomic():
                    bolla_obj = Bolla.objects.filter(
                        cliente=cliente,
                        tipo_documento=tipo_doc,
                        data=data,
                        numero=current_bolla_number,
                    ).first()

                    if not bolla_obj:
                        bolla_obj = Bolla(
                            cliente=cliente,
                            tipo_documento=tipo_doc,
                            data=data,
                            numero=current_bolla_number,
                            note = f"Importato il {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        )
                        bolla_obj.save(skip_auto_number=True)
                        # DA PROVARE nuovo import. Dovrebbe funzionare ora.

                    #print(f"Data parsata: {data}, Numero Bolla: {numero_bolla}")
                    for articolo in parsed_data["articoli"]:
                        # if int(articolo["numero_bolla"]) != bolla_obj.numero:
                        #     continue
                        try:
                            fix = articolo["codice_articolo"]
                            art = articolo["codice_articolo"]
                            if art == '31103':
                                fix = "031103"
                            elif art == '31163':
                                fix = "031163"
                            elif len(art) == 3:
                                fix = "600"+ art
                            elif len(art) == 5:
                                fix = "6" + art
                            #print(f"Articolo {art} cambiato in", fix)

                            articolo_obj = Articolo.objects.get(nome=fix)
                        except Articolo.DoesNotExist:
                            print(f"Articolo con codice {articolo['codice_articolo']} non trovato. Riga ignorata.")
                            continue
                        if int(articolo["numero_bolla"]) == bolla_obj.numero:
                            RigaBolla_obj, created = RigaBolla.objects.get_or_create(
                                bolla=bolla_obj,
                                articolo=articolo_obj,
                                quantita=articolo["quantita"],
                                defaults={"lotto": lotto}
                            )
                            #if created:
                            #    print(f"Riga della bolla {numero_bolla}, art: {fix}, quant:{articolo["quantita"]} creata con successo.")
                            #else:
                            #    print(f"Riga della bolla {numero_bolla} già esistente. Ignorata.")

                    if created:
                        print(f"Bolla {numero_bolla} creata con successo.")
                    else:
                        print(f"Bolla {numero_bolla} già esistente. Ignorata.")
            except Exception as e:
                print(f"Errore durante l'importazione della bolla {numero_bolla}: {e}")

            current_bolla_number += 1

        # Aggiorna il campo `ultimo_numero` del modello TipoDocumento
        # all'ultimo numero della bolla associata.

        # Recupera l'ultima bolla associata al tipo_documento
        ultima_bolla = Bolla.objects.filter(tipo_documento=tipo_doc).order_by('-numero').first()
        documento = TipoDocumento.objects.filter(nome=tipo_doc).first()
        if ultima_bolla:
            # Aggiorna il campo `ultimo_numero` del tipo_documento
            documento.ultimo_numero = ultima_bolla.numero
            documento.save()
            print(f"Aggiornato ultimo_numero di {documento} a {ultima_bolla.numero}.")
        else:
            print(f"Nessuna bolla trovata per il tipo_documento {documento}.")

        return HttpResponseRedirect(reverse("home"))

class ExportBolleView(View):
    template_name = "bolle/export_file.html"
    def get(self, request):
        # Recupera le date dai parametri GET
        user = self.request.user
        tipo_doc = TipoDocumento.objects.filter(nome="CLS", concessionario=user.concessionario).first()

        mesi_italiani = {
            "Gennaio": "01", "Febbraio": "02", "Marzo": "03",
            "Aprile": "04", "Maggio": "05", "Giugno": "06",
            "Luglio": "07", "Agosto": "08", "Settembre": "09",
            "Ottobre": "10", "Novembre": "11", "Dicembre": "12"
        }

        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')

        for mese, numero in mesi_italiani.items():
            if mese in data_inizio_str:
                data_inizio_str = data_inizio_str.replace(mese, numero)
                break
        for mese, numero in mesi_italiani.items():
            if mese in data_fine_str:
                data_fine_str = data_fine_str.replace(mese, numero)
                break

        # Togli il giorno della settimana
        data_inizio_str = " ".join(data_inizio_str.split(" ")[1:])

        data_fine_str = " ".join(data_fine_str.split(" ")[1:])
        tz = timezone.get_current_timezone()
        # Convertiamo a datetime
        if data_inizio_str:
            data_inizio = datetime.strptime(data_inizio_str, "%d %m %Y %H:%M")
            #print(data_inizio) debug
        else:
            data_inizio = now().replace(hour=0, minute=0, second=0, microsecond=0)

        if data_fine_str:
            data_fine = datetime.strptime(data_fine_str, "%d %m %Y %H:%M")
            #print(data_fine) debug
        else:
            data_fine = now().replace(hour=23, minute=59, second=59, microsecond=999999)

        data_inizio = ensure_aware(data_inizio, tz)
        data_fine = ensure_aware(data_fine, tz)
        # Verifica se le date sono valide
        if not data_inizio or not data_fine or data_inizio > data_fine:
            messages.error(request, "Errore: Seleziona un intervallo di date valido.")
            return render(request, "bolle/bolle_list.html", {"bolle": Bolla.objects.none()})

        # Filtra le bolle
        bolle = Bolla.objects.filter(data__range=[data_inizio, data_fine], tipo_documento=tipo_doc)

        if not bolle.exists():
            messages.error(request, "Nessuna bolla trovata per le date selezionate.")
            return redirect("bolle-list")

        # Inizializza linee file. Lista di stringhe
        linee = []
        data = datetime.now()

        # Header del file Centrale. AAA010014001 credo sia codice concessionario.
        # Quello del commento è il mio. (VaLat)

        linee.append(f"{user.concessionario.header}" + data.strftime(
            "%y%m%d") + "                                                                                                                             ")

        clienti = Cliente.objects.filter(tipo_documento_predefinito=tipo_doc).order_by('nome')
        for cliente in clienti:
            linea = (
                f"P{cliente.codice:0>10}"  # Codice cliente (10 caratteri)
                "0000"
                f"{cliente.nome:<35}"  # Nome cliente (35 caratteri)
                f"{cliente.via:<37}"  # Indirizzo (37 caratteri)
                f"{cliente.cap}{cliente.citta:<25}"  # CAP e città
                f"{cliente.provincia:<2}"  # Provincia (2 caratteri)
                f"{cliente.piva:<14}"  # Partita IVA (14 caratteri)
                f"{cliente.proprietario.codice:0>7}"  # Codice proprietario (7 caratteri)
                "   "
            )
            linee.append(linea)

            # Esporta le bolle
        for bolla in bolle:
            linea = (
                f"K00{bolla.numero:0>7}"  # Numero bolla (7 caratteri)
                f"{bolla.data.strftime('%d%m%Y'):<8}"  # Data (es 01011994)(8 caratteri)
                f"{bolla.cliente.codice:0>12}"  # Codice cliente (12 caratteri)
                "                                                                                                                 "
            )
            linee.append(linea)

            # Esporta gli articoli della bolla
            righe_bolla = RigaBolla.objects.filter(bolla=bolla)
            for riga in righe_bolla:
                if riga.articolo.categoria.nome == "Imballaggio":
                    continue
                prezzo = int(float(riga.articolo.prezzo) * 100)
                linea = (
                    f"K02{bolla.numero:0>7}"  # Numero bolla (7 caratteri)
                    f"{riga.articolo.nome:<20}"  # Codice articolo (20 caratteri)
                    f"{riga.quantita:0>7}"  # Quantità (8 caratteri, allineata a destra)
                    f"{prezzo:0>33}"  # Prezzo (33 caratteri)
                    "                                                                         "
                )
                linee.append(linea)

        linee.append("B99                                                                                                                                            ")
        file_name = f"014-CESSIONE-{data.strftime('%y%m%d')}"
        file_path = os.path.join(settings.BASE_DIR, 'temp', file_name)
        contenuto = "".join(f"{linea}\r\n" for linea in linee)
        response = HttpResponse(contenuto, content_type="text/plain; charset=utf-8")
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        return response

class CaricoListView(LoginRequiredMixin, ListView):
    model = Carico
    template_name = 'carichi/carico_list.html'
    context_object_name = 'carichi'
    ordering = ['-data']

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()
        data_inizio = self.request.GET.get('data_inizio')
        data_fine = self.request.GET.get('data_fine')
        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo quella zona
            zonaconc = Zona.objects.filter(pk=user.zona.pk)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra le zone del concessionario
            zonaconc = Zona.objects.filter(concessionario=user.concessionario)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            zonaconc = Zona.objects.none()
        queryset = queryset.filter(zona__in = zonaconc)
        if data_inizio and data_fine:
            queryset = queryset.filter(data__range=[data_inizio, data_fine], zona__in=zonaconc)
        return queryset[:20]

    def get_context_data(self, **kwargs):
        user = self.request.user
        data_inizio = self.request.GET.get('data_inizio', now().date())
        data_fine = self.request.GET.get('data_fine', now().date())
        context = super().get_context_data(**kwargs)
        context['data_inizio'] = self.request.GET.get('data_inizio', now().date())
        context['data_fine'] = self.request.GET.get('data_fine', now().date())
        # Filtra le zone in base ai permessi dell'utente
        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo quella zona
            zonaconc = Zona.objects.filter(pk=user.zona.pk)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra le zone del concessionario
            zonaconc = Zona.objects.filter(concessionario=user.concessionario)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            zonaconc = Zona.objects.none()
        if zonaconc:
            context['zone'] = zonaconc
        else:
            context['zone'] = Zona.objects.filter(concessionario=user.concessionario)  # DA MODIFICARE, LA ZONA DEVE ESSERE SOLO QUELLA SELEZ O DEI CONCESS
        return context

class CaricoDetailView(LoginRequiredMixin, DetailView):
    model = Carico
    template_name = 'carichi/carico_detail.html'
    context_object_name = 'carico'

class CaricoCreateView(LoginRequiredMixin, CreateView):
    model = Carico
    template_name = "carichi/carico_create.html"
    fields = ['data', 'zona', 'fornitore', 'numero', 'note']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user

        # Filtra le zone in base ai permessi dell'utente
        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo quella zona
            form.fields['zona'].queryset = Zona.objects.filter(pk=user.zona.pk)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra le zone del concessionario
            form.fields['zona'].queryset = Zona.objects.filter(concessionario=user.concessionario)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            form.fields['zona'].queryset = Zona.objects.none()
            messages.error(self.request, "Non hai i permessi per creare un carico.")
            self.success_url = reverse_lazy('home')  # Reindirizza alla home

        # Filtra i fornitori (opzionale, puoi decidere di mostrare tutti i fornitori)
        form.fields['fornitore'].queryset = Fornitore.objects.all()
        form.fields['fornitore'].initial = form.fields['fornitore'].queryset.first()
        form.fields['zona'].initial = form.fields['zona'].queryset.first()
        form.fields['note'].widget.attrs.update({"rows":1})
        return form

    def get_success_url(self):
        return reverse_lazy('carico-update', kwargs={'pk':self.object.pk})

class CaricoUpdateView(LoginRequiredMixin, UpdateView):
    model = Carico
    fields = ['data', 'zona', 'fornitore', 'numero', 'note']
    template_name = 'carichi/carico_form.html'
    success_url = reverse_lazy('carichi-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categoria_selezionata = self.request.GET.get('categoria')
        if categoria_selezionata is None:
            categoria_selezionata = 0
        categoria_selezionata = int(categoria_selezionata)
        context["categoria_selezionata"] = str(categoria_selezionata)
        if categoria_selezionata == 0:
            # Se user non seleziona categoria, mostra articoli del proprietario (valat di solito)
            prop = Proprietario.objects.filter(nome="VaLat").first()
            articoli_concessi = ArticoliConcessi.objects.filter(proprietario=prop
            ).values_list('articolo', flat=True)
        else:
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)

        # Righe associate al carico
        context['righe'] = self.object.righe.all()

        # Categorie e articoli disponibili
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi).order_by('nome'))
        ).order_by('ordine')
        oggi = now() + timedelta(days=6)
        data_lotto = oggi.strftime('%d%m%y')
        context["data_lotto"] = data_lotto
        return context

    def post(self, request, *args, **kwargs):
        categoria_selezionata = request.POST.get('categoria')  # Per mantenere cat. selezionata
        if 'add_riga' in request.POST:
            # Aggiungi una nuova riga al carico
            articolo_id = request.POST.get('articolo')
            quantita = request.POST.get('quantita')
            lotto = request.POST.get('lotto')

            # Verifica la quantità
            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('carico-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            # Verifica l'articolo
            if not articolo_id:
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('carico-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            # Recupera o genera il lotto
            articolo = Articolo.objects.get(pk=articolo_id)
            if not lotto:
                oggi = now() + timedelta(days=6)
                lotto = oggi.strftime('%d%m%y')

            # Crea la nuova riga di carico
            RigaCarico.objects.create(
                carico=self.get_object(),
                articolo=articolo,
                quantita=int(quantita),
                lotto=lotto
            )
            return redirect(f"{reverse('carico-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
        elif 'confirm' in request.POST:
            # Conferma la modifica e salva il carico
            return redirect('carichi-list')

        return super().post(request, *args, **kwargs)

class CaricoDeleteView(LoginRequiredMixin, DeleteView):
    model = Carico
    success_url = reverse_lazy('carichi-list')
    template_name = 'carichi/carico_confirm_delete.html'

class RigaCaricoDeleteView(LoginRequiredMixin, DeleteView):
    model = RigaCarico

    def get_success_url(self):
        return reverse_lazy('carico-update', kwargs={'pk': self.object.carico.pk})

class ResoListView(LoginRequiredMixin, ListView):
    model = Reso
    template_name = 'resi/reso_list.html'
    context_object_name = 'resi'
    ordering = ['-data']  # Ordina per data decrescente

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        data_inizio = self.request.GET.get('data_inizio')
        data_fine = self.request.GET.get('data_fine')
        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo quella zona
            zonaconc = Zona.objects.filter(pk=user.zona.pk)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra le zone del concessionario
            zonaconc = Zona.objects.filter(concessionario=user.concessionario)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            zonaconc = Zona.objects.none()
        queryset = queryset.filter(zona__in=zonaconc)
        if data_inizio and data_fine:
            queryset = queryset.filter(data__range=[data_inizio, data_fine], zona__in=zonaconc)
        return queryset[:20]

    def get_context_data(self, **kwargs):
        user = self.request.user
        data_inizio = self.request.GET.get('data_inizio', (now().date()-timedelta(days=3)))
        data_fine = self.request.GET.get('data_fine', now().date())
        context = super().get_context_data(**kwargs)
        context['data_inizio'] = self.request.GET.get('data_inizio', now().date())
        context['data_fine'] = self.request.GET.get('data_fine', now().date())
        zonaconc = Zona.objects.filter(user = user)
        if zonaconc:
            context['zone'] = zonaconc
        else:
            context['zone'] = Zona.objects.filter(concessionario=user.concessionario)  # DA MODIFICARE, LA ZONA DEVE ESSERE SOLO QUELLA SELEZ O DEI CONCESS
        return context

class ResoDetailView(LoginRequiredMixin, DetailView):
    model = Reso
    template_name = 'resi/reso_detail.html'
    context_object_name = 'reso'

class ResoCreateView(LoginRequiredMixin, CreateView):
    model = Reso
    template_name = 'resi/reso_create.html'
    fields = ['data', 'zona', 'note']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user

        if hasattr(user, 'zona'):
            # Utente con zona: mostra solo quella
            form.fields['zona'].queryset = Zona.objects.filter(pk=user.zona.pk)
        elif hasattr(user, 'concessionario'):
            # Utente con concessionario: mostra le zone del concessionario
            form.fields['zona'].queryset = Zona.objects.filter(concessionario=user.concessionario)
        else:
            # Utente senza permessi: blocca l'accesso
            form.fields['zona'].queryset = Zona.objects.none()
            messages.error(self.request, "Non hai i permessi per creare un reso.")
            self.success_url = reverse_lazy('home')

        form.fields['zona'].initial = form.fields['zona'].queryset.first()
        form.fields['note'].widget.attrs.update({"rows":1})
        return form

    def get_success_url(self):
        return reverse_lazy('reso-update', kwargs={'pk':self.object.pk})

class ResoUpdateView(LoginRequiredMixin, UpdateView):
    model = Reso
    fields = ['zona', 'note']  # Campi modificabili
    template_name = 'resi/reso_form.html'
    success_url = reverse_lazy('resi-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categoria_selezionata = self.request.GET.get('categoria')
        if categoria_selezionata is None:
            categoria_selezionata = 0
        categoria_selezionata = int(categoria_selezionata)
        context["categoria_selezionata"] = str(categoria_selezionata)
        if categoria_selezionata == 0:
            # Se user non seleziona categoria, mostra articoli del proprietario (valat di solito)
            prop = Proprietario.objects.filter(nome="VaLat").first()
            articoli_concessi = ArticoliConcessi.objects.filter(proprietario=prop
            ).values_list('articolo', flat=True)
        else:
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)

        # Righe associate al carico
        context['righe'] = self.object.righe.all()

        # Categorie e articoli disponibili
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi).order_by('nome'))
        ).order_by('ordine')

        return context

    def post(self, request, *args, **kwargs):
        categoria_selezionata = request.POST.get("categoria") # Mantenere categoria selezionata
        if 'add_riga' in request.POST:
            articolo_id = request.POST.get('articolo')
            quantita = request.POST.get('quantita')

            # Verifica se la quantità è valida
            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('reso-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

            # Verifica l'articolo
            if not articolo_id:
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('reso-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

            # Crea la nuova riga di reso
            RigaReso.objects.create(
                reso=self.get_object(),
                articolo_id=articolo_id,
                quantita=int(quantita),
            )
            return redirect(f"{reverse('reso-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

        elif 'confirm' in request.POST:
            return redirect('resi-list')

        return super().post(request, *args, **kwargs)

class ResoDeleteView(LoginRequiredMixin, DeleteView):
    model = Reso
    template_name = 'resi/reso_confirm_delete.html'
    success_url = reverse_lazy('resi-list')

class RigaResoDeleteView(LoginRequiredMixin, DeleteView):
    model = RigaReso

    def get_success_url(self):
        return reverse_lazy('reso-update', kwargs={'pk': self.object.reso.pk})

import calendar

def menu_riepiloghi(request):
    user = request.user
    context = {}
    oggi = datetime.now().date()
    data_inizio = date(oggi.year, oggi.month, 1)
    ultimo_giorno = calendar.monthrange(oggi.year, oggi.month)[1]
    data_fine = date(oggi.year, oggi.month, ultimo_giorno)

    # Determina le zone visibili all'utente
    if hasattr(user, 'zona'):
        # Se l'utente ha una zona, mostra solo quella
        context['zone'] = Zona.objects.filter(pk=user.zona.pk)
        concessionario = context['zone'].first().concessionario
        context['clienti'] = Cliente.objects.filter(zona__in = context['zone'])
    elif hasattr(user, 'concessionario'):
        # Se l'utente ha un concessionario, mostra tutte le zone del concessionario
        context['zone'] = Zona.objects.filter(concessionario=user.concessionario)
        concessionario = context['zone'].first().concessionario
        context['clienti'] = Cliente.objects.filter(zona__in = context['zone'])
    else:
        # Utente senza zona o concessionario: nessuna zona visibile
        context['zone'] = Zona.objects.none()
        context['clienti'] = Cliente.objects.none()
        messages.error(request, "Non hai zone assegnate. Vattinne")
        return render(request, 'riepiloghi/menu_riepiloghi.html')

    context['oggi'] = oggi
    context['casse_car'] = RigaCarico.objects.filter(carico__data__range=(data_inizio, data_fine), articolo__descrizione="Cestelli", carico__zona__concessionario = concessionario).aggregate(total=Sum('quantita'))['total'] or 0
    context['bancali_car'] = RigaCarico.objects.filter(carico__data__range=(data_inizio, data_fine), articolo__descrizione="Bancali EPAL", carico__zona__concessionario = concessionario).aggregate(total=Sum('quantita'))[
                         'total'] or 0

    context['casse_res'] = RigaReso.objects.filter(reso__data__range=(data_inizio, data_fine), articolo__descrizione="Cestelli", reso__zona__concessionario = concessionario).aggregate(total=Sum('quantita'))['total'] or 0
    context['bancali_res'] = RigaReso.objects.filter(reso__data__range=(data_inizio, data_fine), articolo__descrizione="Bancali EPAL", reso__zona__concessionario = concessionario).aggregate(total=Sum('quantita'))[
                          'total'] or 0
    context['diff_casse'] = context['casse_res'] - context['casse_car']
    context['diff_bancali'] = context['bancali_res'] - context['bancali_car']

    return render(request, 'riepiloghi/menu_riepiloghi.html', context)

from django.template.defaultfilters import date as _date

def riepilogo_giornaliero(request):
    data_giorno = request.GET.get('data_giorno')  # Ottieni la data dalla query string
    zona_id = request.GET.get('zona')  # Ottieni la zona dalla query string

    # Se i parametri non sono forniti, gestisci l'errore e manda indietro
    if not data_giorno or not zona_id:
        messages.error(request, "Inserire tutti i parametri.")
        return redirect('menu-riepiloghi')

    # Converti la data e verifica che la zona esista
    data = datetime.strptime(data_giorno, "%Y-%m-%d")
    data_inizio = datetime.combine(data, datetime.min.time())
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = datetime.combine(data, datetime.max.time())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    zona = get_object_or_404(Zona, pk=zona_id)
    giorno_precedente = data - timedelta(days=1)

    carico_giorno_precedente = Carico.objects.filter(data=giorno_precedente, zona=zona)
    reso_giorno_precedente = Reso.objects.filter(data=giorno_precedente, zona=zona)
    if not reso_giorno_precedente:
        reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=1)), zona=zona)
        if not reso_giorno_precedente:
            reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=2)), zona=zona)
    # Rozzo? Si. Ma efficace. Statt zitt.
    prec_inizio = datetime.combine(giorno_precedente, datetime.min.time()) #devo farlo sempre perchè è una datetime, per il range.
    prec_inizio = timezone.make_aware(prec_inizio, timezone.get_current_timezone())
    prec_fine = datetime.combine(giorno_precedente, datetime.max.time())
    prec_fine = timezone.make_aware(prec_fine, timezone.get_current_timezone())
    bolle_del_giorno = Bolla.objects.filter(
        Q(data__range=(data_inizio, data_fine)) |
        Q(data__range=(prec_inizio, prec_fine), note__icontains="conto domani"),
        tipo_documento__concessionario=zona.concessionario
    ).exclude(tipo_documento__nome="RF")

    reso_del_giorno = Reso.objects.filter(data=data, zona=zona)

    articoli = Articolo.objects.all()
    riepilogo = {}
    for articolo in articoli:
        carico_prec = 0
        reso_prec = 0
        nome_art = ""
        carico_totale = 0
        reso_att = 0
        bolla_totale = 0
        quantita_venduta = 0
        nome_art = articolo.nome
        carico_prec = RigaCarico.objects.filter(carico__in=carico_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        reso_prec = RigaReso.objects.filter(reso__in=reso_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        carico_totale = carico_prec + reso_prec
        reso_att = (RigaReso.objects.filter(reso__in=reso_del_giorno, articolo=articolo).aggregate(Sum("quantita")))['quantita__sum'] or 0
        bolla_totale = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0

        bolla_nt = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="NT").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0
        bolla_cls = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="CLS").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0
        quantita_venduta = carico_totale - bolla_totale - reso_att
        prezzo = Articolo.objects.filter(nome=nome_art).first().prezzo_ivato
        tot_euro = float(quantita_venduta * prezzo)
        if tot_euro == 0:
            continue

        riepilogo[nome_art] = {
            "nome" : nome_art,
            "carico_prec" : carico_prec,
            "reso_prec" : reso_prec,
            "carico_tot" : carico_totale,
            "reso_att" : reso_att,
            "bolla_nt" : bolla_nt,
            "bolla_cls" : bolla_cls,
            "bolla_totale" : bolla_totale,
            "quantita_venduta" : quantita_venduta,
            "tot_euro" : format(tot_euro, ".2f")
        }

    totale = 0
    for articolo in riepilogo:
        totale += float(riepilogo[articolo].get("tot_euro"))

    return render(request, 'riepiloghi/riepilogo_giornaliero.html', {'riepilogo': riepilogo, 'totale':format(totale, ".2f"), 'zona':zona, 'data_giorno': _date(data, "Y-m-d"), 'data':_date(data, "l, d E Y")})

def riepilogo_giornaliero_stampa(request):
    data_giorno = request.GET.get('data_giorno')  # Ottieni la data dalla query string
    zona_id = request.GET.get('zona')  # Ottieni la zona dalla query string

    # Se i parametri non sono forniti, gestisci l'errore e manda indietro
    if not data_giorno or not zona_id:
        messages.error(request, "Inserire tutti i parametri.")
        return redirect('menu-riepiloghi')

    # Converti la data e verifica che la zona esista
    data = datetime.strptime(data_giorno, "%Y-%m-%d")
    data_inizio = datetime.combine(data, datetime.min.time())
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = datetime.combine(data, datetime.max.time())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    zona = get_object_or_404(Zona, pk=zona_id)
    giorno_precedente = data - timedelta(days=1)

    carico_giorno_precedente = Carico.objects.filter(data=giorno_precedente, zona=zona)
    reso_giorno_precedente = Reso.objects.filter(data=giorno_precedente, zona=zona)
    if not reso_giorno_precedente:
        reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=1)), zona=zona)
        if not reso_giorno_precedente:
            reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=2)), zona=zona)
    # Rozzo? Si. Ma efficace. Statt zitt.

    bolle_del_giorno = Bolla.objects.filter(data__range=(data_inizio, data_fine),
                                            tipo_documento__concessionario=zona.concessionario).exclude(
        tipo_documento__nome="RF")
    reso_del_giorno = Reso.objects.filter(data=data, zona=zona)

    articoli = Articolo.objects.all()
    riepilogo = {}
    for articolo in articoli:
        carico_prec = 0
        reso_prec = 0
        nome_art = ""
        carico_totale = 0
        reso_att = 0
        bolla_totale = 0
        quantita_venduta = 0
        nome_art = articolo.nome
        carico_prec = RigaCarico.objects.filter(carico__in=carico_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        reso_prec = RigaReso.objects.filter(reso__in=reso_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        carico_totale = carico_prec + reso_prec
        reso_att = (RigaReso.objects.filter(reso__in=reso_del_giorno, articolo=articolo).aggregate(Sum("quantita")))['quantita__sum'] or 0
        bolla_totale = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        bolla_nt = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="NT").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0
        bolla_cls = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="CLS").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0
        quantita_venduta = carico_totale - bolla_totale - reso_att
        prezzo = Articolo.objects.filter(nome=nome_art).first().prezzo_ivato
        tot_euro = float(quantita_venduta * prezzo)
        if tot_euro == 0:
            continue

        riepilogo[nome_art] = {
            "nome" : nome_art,
            "carico_prec" : carico_prec,
            "reso_prec" : reso_prec,
            "carico_tot" : carico_totale,
            "reso_att" : reso_att,
            "bolla_nt" : bolla_nt,
            "bolla_cls" : bolla_cls,
            "bolla_totale" : bolla_totale,
            "quantita_venduta" : quantita_venduta,
            "tot_euro" : format(tot_euro, ".2f")
        }

    totale = 0
    for articolo in riepilogo:
        totale += float(riepilogo[articolo].get("tot_euro"))

    return render(request, 'riepiloghi/riepilogo_stampa.html', {'riepilogo': riepilogo, 'totale':format(totale, ".2f"), 'zona':zona, 'data':_date(data, "l, d E Y")})


from django import forms
from django.http import HttpResponse

class BulkUpdateForm(forms.Form):
    IVA_SCELTE = [
        (4, '4%'),
        (10, '10%'),
        (22, '22%'),
    ]
    categoria = forms.ModelChoiceField(queryset=Categoria.objects.all())
    costo = forms.DecimalField(max_digits=10, decimal_places=3)
    prezzo = forms.DecimalField(max_digits=10, decimal_places=3)
    iva = forms.ChoiceField(choices=IVA_SCELTE)

def bulk_update_view(request):
    if request.method == "POST":
        form = BulkUpdateForm(request.POST)
        if form.is_valid():
            categoria = form.cleaned_data['categoria']
            prezzo = form.cleaned_data['prezzo']
            costo = form.cleaned_data['costo']
            iva = form.cleaned_data['iva']
            Articolo.objects.filter(categoria=categoria).update(prezzo=prezzo)
            Articolo.objects.filter(categoria=categoria).update(costo=costo)
            Articolo.objects.filter(categoria=categoria).update(iva=iva)
            return HttpResponse("Prezzi aggiornati!")
    else:
        form = BulkUpdateForm()
    return render(request, "bolle/bulk_update.html", {"form": form})

from collections import defaultdict

def riepilogo_casse(request):
    data_inizio = request.GET.get("data_inizio")
    data_fine = request.GET.get("data_fine")
    zona = request.GET.get("zona")

    if not data_inizio or not data_fine or not zona:
        messages.error(request, "Inserire tutti i parametri.")
        return redirect('menu-riepiloghi')

    data_inizio = datetime.strptime(data_inizio, "%Y-%m-%d").date()
    data_fine = datetime.strptime(data_fine, "%Y-%m-%d").date()
    zona = get_object_or_404(Zona, pk=zona)
    # DEBUG messages.success(request, f"data_inizio: {data_inizio}, data_fine: {data_fine}, zona: {zona.nome}")
    cest_car = RigaCarico.objects.filter(carico__data__range=(data_inizio, data_fine), articolo__descrizione="Cestelli", carico__zona = zona)
    banc_car = RigaCarico.objects.filter(carico__data__range=(data_inizio, data_fine), articolo__descrizione="Bancali EPAL", carico__zona = zona)
    cest_res = RigaReso.objects.filter(reso__data__range=(data_inizio, data_fine), articolo__descrizione="Cestelli", reso__zona = zona)
    banc_res = RigaReso.objects.filter(reso__data__range=(data_inizio, data_fine), articolo__descrizione="Bancali EPAL", reso__zona = zona)
    somma_cest_res = cest_res.aggregate(total=Sum('quantita'))["total"] or 0
    somma_cest_car = cest_car.aggregate(total=Sum('quantita'))["total"] or 0
    somma_banc_res = banc_res.aggregate(total=Sum('quantita'))["total"] or 0
    somma_banc_car = banc_car.aggregate(total=Sum('quantita'))["total"] or 0
    diff_casse = somma_cest_res - somma_cest_car
    diff_bancali = somma_banc_res - somma_banc_car

    # Aggregazione per data
    riepilogo = defaultdict(lambda: {"casse_caricate": 0, "casse_rese": 0, "banc_caricati":0, "banc_resi":0})

    # Popola il dizionario con i dati di carico
    for carico in cest_car:
        data = carico.carico.data  # Supponendo che il campo della data si chiami 'data'
        riepilogo[data]["casse_caricate"] += carico.quantita

    # Popola il dizionario con i dati di reso
    for reso in cest_res:
        data = reso.reso.data  # Supponendo che il campo della data si chiami 'data'
        riepilogo[data]["casse_rese"] += reso.quantita

    for carico in banc_car:
        data = carico.carico.data
        riepilogo[data]["banc_caricati"] += carico.quantita

    for reso in banc_res:
        data = reso.reso.data
        riepilogo[data]["banc_resi"] += reso.quantita

    # Trasforma in una lista ordinata per data
    riepilogo_ordinato = sorted(
        [{"data": data, "casse_caricate": dati["casse_caricate"], "casse_rese": dati["casse_rese"], "banc_caricati": dati["banc_caricati"], "banc_resi": dati["banc_resi"]}
         for data, dati in riepilogo.items()],
        key=lambda x: x["data"]
    )

    return render(request, 'riepiloghi/riepilogo_casse.html',
                  {
                      'riepilogo': riepilogo_ordinato,
                      'somma_cest_car': somma_cest_car,
                      'somma_cest_res': somma_cest_res,
                      'somma_banc_car': somma_banc_car,
                      'somma_banc_res': somma_banc_res,
                      'diff_casse': diff_casse,
                      'diff_bancali':diff_bancali,
                      'zona': zona,
                      'data_inizio': _date(data_inizio, "d F Y"),
                      'data_fine': _date(data_fine, "d F Y")})

from collections import defaultdict
from .utils.genera_fattura import genera_fattura_xml

def riepilogo_cliente(request):
    data_inizio = request.GET.get("data_inizio")
    data_fine = request.GET.get("data_fine")
    cliente_id = request.GET.get("cliente_id")
    user = request.user

    if hasattr(user, 'zona'):
        # Se l'utente ha una zona, mostra solo quella
        zona = Zona.objects.filter(pk=user.zona.pk)
        concessionario = zona.first().concessionario
    elif hasattr(user, 'concessionario'):
        concessionario = user.concessionario
    else:
        concessionario = None

    if not data_inizio or not data_fine or not cliente_id:
        messages.error(request, "Inserire tutti i parametri.")
        return redirect('menu-riepiloghi')

    # Converti le date
    data_inizio = datetime.strptime(data_inizio, "%Y-%m-%d")
    data_fine = datetime.strptime(data_fine, "%Y-%m-%d")
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    data_fine = data_fine.replace(hour=23, minute=59, second=59, microsecond=999999)
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    tipo_ntv = TipoDocumento.objects.filter(nome="NTV", concessionario=concessionario).first()
    if cliente.tipo_documento_predefinito == tipo_ntv:
        bolle_cliente = SchedaTV.objects.filter(
            cliente=cliente,
            data__range=(data_inizio, data_fine)
        )
    else:
        bolle_cliente = Bolla.objects.filter(data__range=(data_inizio, data_fine), cliente=cliente)

    # Struttura: { articolo: { descrizione, righe, totale_quant, totale_euro } }
    riepilogo = defaultdict(lambda: {
        "descrizione": "",
        "righe": [],
        "totale_quant": 0,
        "totale_euro": 0.0,
    })

    for bolla in bolle_cliente:
        if bolla.cliente.tipo_documento_predefinito != tipo_ntv:
            data_bolla = bolla.data.date()
        for riga in bolla.righe.all():
            articolo = riga.articolo.nome
            prezzo_pers = PrezziPersonalizzati.objects.filter(articolo=riga.articolo, cliente=bolla.cliente).first()
            if prezzo_pers:
                prezzo_unitario = float(prezzo_pers.prezzo_ivato)
            else:
                prezzo_unitario = float(riga.articolo.prezzo_ivato)
            totale_riga = riga.quantita * prezzo_unitario
            if bolla.cliente.tipo_documento_predefinito == tipo_ntv:
                data_bolla = data_inizio.replace(day=riga.giorno)

            # Aggiorna il riepilogo per articolo
            riepilogo[articolo]["descrizione"] = riga.articolo.descrizione
            riepilogo[articolo]["righe"].append({
                "data": data_bolla,
                "quantita": riga.quantita,
                "euro": totale_riga,
            })
            riepilogo[articolo]["totale_quant"] += riga.quantita
            riepilogo[articolo]["totale_euro"] += totale_riga

    riepilogo = {key: dict(value) for key, value in riepilogo.items()}
    tot = 0
    for articolo in riepilogo.values():
        tot += articolo["totale_euro"]

    return render(request, 'riepiloghi/riepilogo_cliente.html', {
        'riepilogo': riepilogo,
        'totale_euro': format(tot, ".2f"),
        'cliente': cliente,
        'data_inizio': data_inizio,
        'data_fine': data_fine,
    })

def riepilogo_cls(request):
    data_inizio = request.GET.get("data_inizio")
    data_fine = request.GET.get("data_fine")
    user = request.user

    if not data_inizio or not data_fine:
        messages.error(request, "Inserire tutti i parametri.")
        return redirect('menu-riepiloghi')

    # Converti le date
    data_inizio = datetime.strptime(data_inizio, "%Y-%m-%d")
    data_fine = datetime.strptime(data_fine, "%Y-%m-%d")
    data_inizio = datetime.combine(data_inizio, datetime.min.time())
    data_fine = datetime.combine(data_fine, datetime.max.time())
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    if hasattr(user, 'zona'):
        # Se l'utente ha una zona, mostra solo quella
        zona = Zona.objects.filter(pk=user.zona.pk)
        concessionario = zona.first().concessionario
    elif hasattr(user, 'concessionario'):
        concessionario = user.concessionario
    else:
        concessionario = None

    if concessionario:
        cls_conc = TipoDocumento.objects.filter(concessionario=concessionario)
    else:
        messages.error(request, "Aò nun c'è sto concessionario, ma che caz???")
        return redirect("menu-riepiloghi")
    tipo_doc = TipoDocumento.objects.filter(concessionario = concessionario, nome="CLS")
    bolle_cliente = Bolla.objects.filter(data__range=(data_inizio, data_fine), tipo_documento__in=tipo_doc)

    # Struttura: { articolo: { descrizione, righe, totale_quant, totale_euro } }
    riepilogo = defaultdict(lambda: {
        "descrizione": "",
        "righe": [],
        "totale_quant": 0,
        "totale_euro": 0.0,
    })

    for bolla in bolle_cliente:
        data_bolla = bolla.data.date()
        for riga in bolla.righe.all():
            articolo = riga.articolo.nome
            prezzo_unitario = float(riga.articolo.costo_ivato)
            totale_riga = riga.quantita * prezzo_unitario

            # Aggiorna il riepilogo per articolo
            riepilogo[articolo]["descrizione"] = riga.articolo.descrizione
            riepilogo[articolo]["totale_quant"] += riga.quantita
            riepilogo[articolo]["totale_euro"] += totale_riga

    riepilogo = {key: dict(value) for key, value in riepilogo.items()}
    tot = 0
    for articolo in riepilogo.values():
        tot += articolo["totale_euro"]

    return render(request, 'riepiloghi/riepilogo_cls.html', {
        'riepilogo': riepilogo,
        'concession': concessionario.nome,
        'totale_euro': format(tot, ".2f"),
        'data_inizio': data_inizio,
        'data_fine': data_fine,
    })

def scarica_xml(request, pk):
    fattura = get_object_or_404(Fattura, pk=pk)
    xml_content = genera_fattura_xml(fattura)
    fattura.xml_file = xml_content
    fattura.save()
    # DEBUG print(xml_content)
    if not xml_content:
        return HttpResponse("Errore: Nessun file XML da scaricare", status=400)

    # Crea la risposta come file XML scaricabile
    response = HttpResponse(xml_content, content_type='application/xml')
    nome = fattura.cliente.nome.replace(" ", "")
    tipo = fattura.tipo_fattura.descrizione.replace(" ", "")
    numero = str(fattura.numero).zfill(4)
    response['Content-Disposition'] = f'attachment; filename="IT{fattura.concessionario.partita_iva}_{numero}.xml"'
    return response

class FatturaListView(LoginRequiredMixin, ListView):
    model = Fattura
    template_name = 'fatture/fatture_list.html'
    context_object_name = 'fatture'
    ordering = ['-numero']

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        tipo_fattura_id = self.request.GET.get('tipo_fattura')

        # Gestione delle date (campo `data` è una semplice data)
        oggi = datetime.now().date()
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)

        # Filtro per tipo fattura
        if not tipo_fattura_id:
            queryset = queryset.filter(data__range=(data_inizio, data_fine))
            return queryset

        queryset = queryset.filter(tipo_fattura_id=tipo_fattura_id, data__range=(data_inizio, data_fine))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        oggi = datetime.now().date()

        # Gestione delle date
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)
        tipo_fattura_id = self.request.GET.get('tipo_fattura')
        if data_inizio == oggi:
            data_inizio = data_inizio.replace(day=1)
            data_inizio = data_inizio - relativedelta(months=1)
            data_fine = data_inizio + relativedelta(months=1) - relativedelta(days=1)
        context['data_inizio'] = data_inizio
        context['data_fine'] = data_fine
        mesi_italiani = {
            "Gennaio": "01", "Febbraio": "02", "Marzo": "03",
            "Aprile": "04", "Maggio": "05", "Giugno": "06",
            "Luglio": "07", "Agosto": "08", "Settembre": "09",
            "Ottobre": "10", "Novembre": "11", "Dicembre": "12"
        }
        context['mesi'] = mesi_italiani
        anno = [(datetime.now().year)-1, datetime.now().year, (datetime.now().year)+1]
        context['anni'] = anno
        if not tipo_fattura_id:
            if hasattr(user, 'zona'):
                tipi = TipoFattura.objects.filter(concessionario=user.zona.concessionario)
                context['tipi_fattura'] = tipi
                fatture = Fattura.objects.filter(tipo_fattura__in=tipi, data__range=(data_inizio, data_fine))
                context['tipo_fattura_id'] = ""
            elif hasattr(user, 'concessionario'):
                tipi = TipoFattura.objects.filter(concessionario=user.concessionario)
                context['tipi_fattura'] = tipi
                fatture = Fattura.objects.filter(tipo_fattura__in=tipi, data__range=(data_inizio, data_fine))
                context['tipo_fattura_id'] = ""
            else:
                context['tipi_fattura'] = TipoFattura.objects.none()
                fatture = Fattura.objects.none()
                context['tipo_fattura_id'] = ""
            context['fatture'] = fatture
            return context
        else:
            fatture = Fattura.objects.filter(tipo_fattura_id=tipo_fattura_id, data__range=(data_inizio, data_fine))
            context['fatture'] = fatture
            context['tipo_fattura_id'] = tipo_fattura_id
            if hasattr(user, 'zona'):
                tipi = TipoFattura.objects.filter(concessionario=user.zona.concessionario)
                context['tipi_fattura'] = tipi
            elif hasattr(user, 'concessionario'):
                tipi = TipoFattura.objects.filter(concessionario=user.concessionario)
                context['tipi_fattura'] = tipi
            else:
                context['tipi_fattura'] = TipoFattura.objects.none()
            return context

    def get_data_filtrata(self, data_str, oggi, inizio=True):
        """Funzione ausiliaria per calcolare data_inizio e data_fine per le fatture (campo `data`)"""
        if data_str:
            data = datetime.strptime(data_str, '%Y-%m-%d').date()
            return data
        else:
            # Se non viene fornita una data, si usa quella corrente
            return oggi


import io
import zipfile

def scarica_tutte_xml(request):
    # Recupera le fatture filtrate secondo i parametri GET
    data_inizio_str = request.GET.get("data_inizio")
    data_fine_str = request.GET.get("data_fine")
    tipo_fattura_id = request.GET.get("tipo_fattura")

    mesi_italiani = {
        "Gennaio": "01", "Febbraio": "02", "Marzo": "03",
        "Aprile": "04", "Maggio": "05", "Giugno": "06",
        "Luglio": "07", "Agosto": "08", "Settembre": "09",
        "Ottobre": "10", "Novembre": "11", "Dicembre": "12"
    }

    for mese, numero in mesi_italiani.items():
        if mese in data_inizio_str:
            data_inizio_str = data_inizio_str.replace(mese, numero)
            break
    for mese, numero in mesi_italiani.items():
        if mese in data_fine_str:
            data_fine_str = data_fine_str.replace(mese, numero)
            break

    oggi = datetime.now().date()
    data_inizio = datetime.strptime(data_inizio_str, "%d %m %Y") if data_inizio_str else oggi
    data_fine = datetime.strptime(data_fine_str, "%d %m %Y").date() if data_fine_str else oggi
    queryset = Fattura.objects.filter(data__range=(data_inizio, data_fine))
    if tipo_fattura_id:
        queryset = queryset.filter(tipo_fattura__id= tipo_fattura_id)

    # Se non ci sono fatture, restituisce un errore
    if not queryset.exists():
        messages.error(request, "Errore: Nessuna fattura trovata per il periodo selezionato")
        return redirect('fatture-list')

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for fattura in queryset:
            xml_content = genera_fattura_xml(fattura)
            if xml_content:
                nome = fattura.cliente.nome.replace(" ", "")
                tipo = fattura.tipo_fattura.descrizione.replace(" ", "")
                numero = str(fattura.numero).zfill(4)
                file_name = f"IT{fattura.concessionario.partita_iva}_{numero}.xml"
                zip_file.writestr(file_name, xml_content)

    zip_buffer.seek(0)
    zip_filename = f"tutte_le_fatture_{oggi.strftime('%Y%m%d')}.zip"
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'

    return response

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO

def scarica_tutte_fatture_pdf(request):
    # Recupera le fatture filtrate secondo i parametri GET
    data_inizio_str = request.GET.get("data_inizio")
    data_fine_str = request.GET.get("data_fine")
    tipo_fattura_id = request.GET.get("tipo_fattura")

    mesi_italiani = {
        "Gennaio": "01", "Febbraio": "02", "Marzo": "03",
        "Aprile": "04", "Maggio": "05", "Giugno": "06",
        "Luglio": "07", "Agosto": "08", "Settembre": "09",
        "Ottobre": "10", "Novembre": "11", "Dicembre": "12"
    }

    for mese, numero in mesi_italiani.items():
        if mese in data_inizio_str:
            data_inizio_str = data_inizio_str.replace(mese, numero)
            break
    for mese, numero in mesi_italiani.items():
        if mese in data_fine_str:
            data_fine_str = data_fine_str.replace(mese, numero)
            break

    oggi = datetime.now().date()
    data_inizio = datetime.strptime(data_inizio_str, "%d %m %Y") if data_inizio_str else oggi
    data_fine = datetime.strptime(data_fine_str, "%d %m %Y").date() if data_fine_str else oggi

    # Filtra le fatture
    queryset = Fattura.objects.filter(data__range=(data_inizio, data_fine))
    if tipo_fattura_id:
        queryset = queryset.filter(tipo_fattura__id=tipo_fattura_id)

    # Se non ci sono fatture, restituisce un errore
    if not queryset.exists():
        messages.error(request, "Errore: Nessuna fattura trovata per il periodo selezionato")
        return redirect('fatture-list')

    # Crea un buffer per il PDF
    buffer = BytesIO()

    # Crea il PDF con ReportLab
    c = canvas.Canvas(buffer, pagesize=A4)

    for fattura in queryset:

        disegna_fattura(c, fattura)
        c.showPage() # Aggiungi una nuova pagina per ogni fattura

    # Salva il PDF
    c.setTitle(f"Insieme di fatture del {fattura.data.strftime('%d/%m/%y')}")
    c.save()

    buffer.seek(0) #risali all'inizio
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="tutte_le_fatture_del_{data_fine_str.replace(" ", "_")}.pdf"'

    return response

def disegna_fattura(c, fattura):

    c.setTitle("Fattura N. {}".format(fattura.numero) + " del {}".format(fattura.data.strftime("%d/%m/%y"))  + " " + "{}".format(fattura.cliente.nome))
    #Dimensioni pagina
    larghezza, altezza = A4
    # Fornitore (Concessionario quindi)
    logo_path = fattura.concessionario.logo.path
    max_righe_per_pagina = 38
    y_footer = 153.8897637795277

    def disegna_intestazione():
        c.drawImage(logo_path, 43, altezza - 130, width=200, height=100)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, altezza - 140, fattura.concessionario.nome)
        c.setFont("Helvetica", 12)
        c.drawString(50, altezza - 150, fattura.concessionario.via)
        c.drawString(50, altezza - 160,
                     fattura.concessionario.cap + " " + fattura.concessionario.citta + " " + fattura.concessionario.provincia)
        c.drawString(50, altezza - 170, str(fattura.concessionario.telefono))
        c.drawString(50, altezza - 180, "P.IVA: " + fattura.concessionario.partita_iva)

        # Dettagli del cliente
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(larghezza - 50, altezza - 97, "Cliente:")
        c.setFont("Helvetica", 12)
        c.drawRightString(larghezza - 50, altezza - 110, fattura.cliente.nome)
        c.drawRightString(larghezza - 50, altezza - 120, fattura.cliente.indirizzo)
        c.drawRightString(larghezza - 50, altezza - 130, fattura.cliente.via)
        c.drawRightString(larghezza - 50, altezza - 140,
                          fattura.cliente.cap + " " + fattura.cliente.citta + " " + fattura.cliente.provincia)
        c.drawRightString(larghezza - 50, altezza - 150, "P.IVA: " + fattura.cliente.piva)
        c.setLineWidth(1)  # Imposta spessore linea cliente
        c.line(390, altezza - 160, 550, altezza - 160)  # Linea per cliente
        c.setLineWidth(2)  # Imposta spessore linea
        c.line(50, altezza - 200, 550, altezza - 200)  # Linea per tutto il foglio
        # Dettagli documento tipo numero, data, codici vari boh
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, altezza - 210, "Tipo documento")
        c.drawString(180, altezza - 210, "Numero documento")
        c.drawString(320, altezza - 210, "Data documento")
        c.setFont("Helvetica", 10)
        c.drawString(50, altezza - 220, "{}".format(fattura.tipo_fattura))
        c.drawString(180, altezza - 220, "{}".format(fattura.numero))
        c.drawString(320, altezza - 220, "{}".format(fattura.data.strftime("%d/%m/%y")))
        if fattura.cliente.cod_dest != "0000000":
            c.setFont("Helvetica-Bold", 10)
            c.drawString(458, altezza - 210, "Codice destinatario")
            c.setFont("Helvetica", 10)
            c.drawString(458, altezza - 220, "{}".format(fattura.cliente.cod_dest))
        else:
            c.setFont("Helvetica-Bold", 10)
            c.drawRightString(larghezza - 50, altezza - 210, "Indirizzo email pec:")
            c.setFont("Helvetica", 8)
            c.drawRightString(larghezza - 50, altezza - 220, "{}".format(fattura.cliente.pec))
        c.setFont("Helvetica", 10)

    def disegna_footer(y):
        c.setLineWidth(1)  # Imposta spessore linea
        c.line(50, y - 20, 550, y - 20)  # Linea per tutto il foglio
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y - 40, "Imponibile 4%: ")
        c.drawString(230, y - 40, "Imponibile 10%: ")
        c.drawRightString(500, y - 40, "Imponibile 22%: ")
        c.drawString(83, y - 60, "IVA 4%: ")
        c.drawString(263, y - 60, "IVA 10%: ")
        c.drawRightString(500, y - 60, "IVA 22%: ")
        c.setFont("Helvetica", 10)
        c.drawString(135, y - 40, "€ {:.3f}".format(fattura.totali["4"]["imp"]))
        c.drawString(325, y - 40, "€ {:.3f}".format(fattura.totali["10"]["imp"]))
        c.drawRightString(550, y - 40, "€ {:.3f}".format(fattura.totali["22"]["imp"]))
        c.drawString(135, y - 60, "€ {:.3f}".format(fattura.totali["4"]["iva"]))
        c.drawString(325, y - 60, "€ {:.3f}".format(fattura.totali["10"]["iva"]))
        c.drawRightString(550, y - 60, "€ {:.3f}".format(fattura.totali["22"]["iva"]))

        # Totale finale
        c.setLineWidth(2)  # Imposta spessore linea
        c.line(50, y - 90, 550, y - 90)  # Linea per tutto il foglio
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y - 100, "Contributo ambientale CONAI assolto ove dovuto.")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(400, y - 110, "Totale:")
        c.drawRightString(550, y - 110, "€ {:.2f}".format(fattura.totali["tot"]))

    def disegna_tabella_articoli(y):
        # Intestaziona tabella con i prodotti
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, altezza - 250, "Codice")
        c.drawString(110, altezza - 250, "Descrizione")
        c.drawString(260, altezza - 250, "IVA")
        c.drawString(300, altezza - 250, "Quantità")
        c.drawString(400, altezza - 250, "Prezzo")
        c.drawRightString(550, altezza - 250, "Imponibile")

    disegna_intestazione()
    y = altezza-250
    disegna_tabella_articoli(y)
    y -= 20

    # Righe della fattura
    riga_counter = 0

    for riga in fattura.righe.all():
        if riga_counter >= max_righe_per_pagina:
            disegna_footer(y_footer)
            c.showPage()
            disegna_intestazione()
            y = altezza-250
            disegna_tabella_articoli(y)
            y -= 20
            riga_counter = 0 #reset contatore righe
        #Disegna dati della tabella, i vari prodotti
        c.setFont("Helvetica", 10)
        c.setLineWidth(1)  # Imposta spessore linea cliente
        c.drawString(50, y, riga.articolo.nome)
        c.drawString(110, y, riga.articolo.descrizione)
        if riga.articolo.categoria.nome == "Diciture":
            c.line(50, y - 2, 550, y - 2)  # Linea per cliente
            y -= 11
            riga_counter += 1
            continue
        c.drawString(260, y, str(riga.iva)+"%")
        c.drawRightString(350, y, str(riga.quantita))
        c.drawRightString(440, y, "€ {:.3f}".format(riga.prezzo))
        c.drawRightString(550, y, "€ {:.3f}".format(riga.imp))
        c.line(50, y-2, 550, y-2)  # Linea per cliente
        y -= 11
        riga_counter += 1

    disegna_footer(y_footer)



class FatturaDetailView(LoginRequiredMixin, DetailView):
    model = Fattura
    template_name = 'fatture/fattura_detail.html'  # Template per il dettaglio
    context_object_name = 'fattura'  # Nome del contesto per il template

class FatturaDeleteView(DeleteView):
    model = Fattura
    success_url = reverse_lazy('fatture-list')
    template_name = 'fatture/fattura_delete.html'

    def post(self, request, *args, **kwargs):
        fattura = self.get_object()
        user = self.request.user
        # Trova il concessionario (per aggiornare i tipi documento di quel concessionario)
        if hasattr(user, 'zona'):
            zona = user.zona
            concessionario = zona.concessionario
        else:
            concessionario = user.concessionario
        tipo_fattura = fattura.tipo_fattura
        ultimo_numero = tipo_fattura.ultimo_numero
        #perchè int() ? Perchè fattura.numero è una stringa invece ultimo_numero no
        if int(fattura.numero) != ultimo_numero:
            messages.error(request, f"Puoi eliminare solo l'ultima fattura del tipo fattura. Num da eliminare: {fattura.numero}. Ultimo numero: {ultimo_numero}")
            return redirect('fatture-list')

        anno_fatt = fattura.data.year
        tipi_fattura = TipoFattura.objects.filter(anno=anno_fatt, concessionario=concessionario)

        for tipo_fatt in tipi_fattura:
            tipo_fatt.ultimo_numero = tipo_fatt.ultimo_numero - 1
            tipo_fatt.save()

        # DEBUG print(f"Tipo documento aggiornato: {tipo_documento.nome}, ultimo numero: {tipo_documento.ultimo_numero}")
        fattura.delete()

        return redirect(self.success_url)

from dateutil.relativedelta import relativedelta

class FatturaUpdateView(LoginRequiredMixin, UpdateView):
    model = Fattura
    fields = ['cliente']  # Campi modificabili
    template_name = 'fatture/fattura_form.html'
    success_url = reverse_lazy('fatture-list')  # Dopo la modifica, torna alla lista

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.object.cliente
        categoria_selezionata = self.request.GET.get('categoria')
        if categoria_selezionata is None:
            categoria_selezionata = 0
        categoria_selezionata = int(categoria_selezionata)
        context["categoria_selezionata"] = str(categoria_selezionata)
        if categoria_selezionata == 0:
            # Se user non seleziona categoria, mostra articoli valat
            articoli_concessi = ArticoliConcessi.objects.filter(
                proprietario=cliente.proprietario
            ).values_list('articolo', flat=True)
            categoria_selezionata = 0
        else:
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)
            # Ottieni gli articoli concessi al proprietario del cliente

        context['righe'] = self.object.righe.all()
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi).order_by('nome'))
        ).order_by('ordine')

        context['mesi'] = [
            {'numero': i, 'nome': _date(datetime(1900, i, 1), "F")}
            for i in range(1, 13)
        ]
        # _date è il template per la formattazione date in django, così uso il locale di django direttamente.
        anno_corr = now().year
        context['anni'] = [i for i in range(anno_corr-1, anno_corr+2)]
        # aggiungi gli anni per menu a tendina
        return context

    def post(self, request, *args, **kwargs):
        categoria_selezionata = request.POST.get('categoria') # Per mantenere categoria sel.
        if 'add_riga' in request.POST:
            #aggiungi riga alla bolla
            articolo_id = request.POST.get('articolo')
            quantita = request.POST.get('quantita')
            prezzo = request.POST.get('prezzo')
            cliente = self.get_object().cliente
            user = self.request.user
            # Verifica se la quantità è valida
            if hasattr(user, 'zona'):
                zona = user.zona
                concessionario = zona.concessionario
            else:
                concessionario = user.concessionario
            #Check Quantità e id articolo
            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            if not articolo_id or articolo_id == "":
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            tipo_rf = TipoDocumento.objects.filter(nome="RF", concessionario=concessionario).first()
            articolo = get_object_or_404(Articolo, pk=articolo_id)
            prezzo_pers = PrezziPersonalizzati.objects.filter(articolo=articolo, cliente=cliente).first()
            if prezzo_pers:
                if not prezzo:
                    prezzo = prezzo_pers.prezzo
            if not prezzo:
                prezzo = articolo.prezzo
            iva = articolo.iva
            if cliente.tipo_documento_predefinito == tipo_rf:
                articolo = get_object_or_404(Articolo, pk=articolo_id)
                prezzo = articolo.prezzo_tr
                iva = 22
            #print(type(prezzo))
            #print(prezzo)
            RigaFattura.objects.create(
                fattura = self.get_object(),
                articolo_id = articolo_id,
                prezzo = Decimal(prezzo),
                quantita = quantita,
                iva = iva,
            )
            return redirect(f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
        elif 'recupera_totali' in request.POST:
            # Recupera i totali per il mese selezionato
            user = self.request.user
            mese = int(request.POST.get('mese'))
            anno = int(request.POST.get('anno'))
            if not anno:
                anno = datetime.now().year
            cliente = self.get_object().cliente

            # Calcola il range di date per il mese
            data_inizio = datetime(anno, mese, 1)
            data_inizio = datetime.combine(data_inizio, datetime.min.time())
            data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
            data_fine = (data_inizio + relativedelta(months=1)) - timedelta(days=1)
            data_fine = datetime.combine(data_fine, datetime.max.time())
            data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
            if hasattr(user, 'zona'):
                zona = user.zona
                concessionario = zona.concessionario
            else:
                concessionario = user.concessionario
            tipo_rf = TipoDocumento.objects.filter(nome="RF", concessionario = concessionario).first()
            tipo_cls = TipoDocumento.objects.filter(nome="CLS", concessionario = concessionario).first()
            tipo_ntv = TipoDocumento.objects.filter(nome="NTV", concessionario=concessionario).first()
            if cliente.tipo_documento_predefinito == tipo_rf:
                # DEBUG print("hello")
                # Recupera le bolle dei clienti CLS
                bolle_cliente = Bolla.objects.filter(
                    tipo_documento = tipo_cls,
                    data__range=(data_inizio, data_fine)
                )
            elif cliente.tipo_documento_predefinito == tipo_ntv:
                bolle_cliente = SchedaTV.objects.filter(
                    cliente = cliente,
                    data__range = (data_inizio, data_fine)
                )
            else:
                # Recupera le bolle del cliente per il mese selezionato
                bolle_cliente = Bolla.objects.filter(
                    cliente=cliente,
                    data__range=(data_inizio, data_fine)
                )
           # if cliente.tipo_documento_predefinito == tipo_ntv:
           # DEBUG     print(bolle_cliente.first().righe.all())
           #     return redirect('fattura-update', pk=self.get_object().pk)
            # Calcola i totali per ogni articolo
            riepilogo = defaultdict(lambda: {"quantita": 0, "articolo": None})
            for bolla in bolle_cliente:
                for riga in bolla.righe.all():
                    articolo = riga.articolo
                    riepilogo[articolo.pk]["articolo"] = articolo
                    riepilogo[articolo.pk]["quantita"] += riga.quantita

            # Aggiungi i totali come righe alla fattura
            articoli = 0

            for riga in riepilogo.values():
                articolo = riga["articolo"]
                iva = articolo.iva
                prezzo_pers = PrezziPersonalizzati.objects.filter(articolo=articolo, cliente=cliente).first()
                if prezzo_pers:
                    prezzo = float(prezzo_pers.prezzo)
                elif cliente.tipo_documento_predefinito == tipo_rf:
                    prezzo = articolo.prezzo_tr
                    iva = 22
                else:
                    prezzo = articolo.prezzo
                if type(prezzo) != Decimal:
                    prezzo = Decimal(prezzo)
                RigaFattura.objects.create(
                    fattura=self.get_object(),
                    articolo=riga["articolo"],
                    quantita=riga["quantita"],
                    prezzo=prezzo,
                    iva= iva,
                )
                articoli += 1
            mesetto = _date(data_inizio, "F")
            messages.success(request, f"Totali recuperati per il mese di {mesetto} {anno}. {articoli} articoli aggiunti.")
            return redirect('fattura-update', pk=self.get_object().pk)
        elif 'confirm' in request.POST:
            # Conferma la modifica e salva la bolla
            return redirect('fatture-list')
        return super().post(request, *args, **kwargs)

class FatturaCreateView(LoginRequiredMixin, CreateView):
    model = Fattura
    template_name = "fatture/fattura_create.html"
    fields = ['data', 'cliente', 'concessionario', 'tipo_fattura', 'condizioni_pagamento', 'scadenza_pagamento', 'modalita_pagamento']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user

        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo i clienti della zona
            zona = user.zona
            conc = zona.concessionario
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra tutti i clienti del concessionario
            conc = user.concessionario
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            form.fields['cliente'].queryset = Cliente.objects.none()
            form.fields['concessionario'].queryset = Concessionario.objects.none()
            messages.error(self.request, "Non hai i permessi per creare una bolla.")
            self.success_url = reverse_lazy('fatture-list')

        oggi = date.today().replace(day=1)
        fine_mese = oggi - relativedelta(days=1)

        form.fields['cliente'].queryset = Cliente.objects.filter(concessionario=conc).exclude(tipo_documento_predefinito__nome="CLS")
        form.fields['concessionario'].label = "Concessionario:"
        form.fields['concessionario'].queryset = Concessionario.objects.filter(pk = conc.pk)
        form.fields['concessionario'].initial = Concessionario.objects.filter(pk = conc.pk).first()
        form.fields['data'].label = "Data fattura:"
        form.fields['data'].initial = fine_mese
        form.fields['cliente'].label = "Cliente:"
        form.fields['tipo_fattura'].label = "Tipo Fattura:"
        form.fields['tipo_fattura'].initial = form.fields['tipo_fattura'].queryset.filter(descrizione__contains = "Fattura").first()
        form.fields['condizioni_pagamento'].label = "Condizioni di Pagamento:"
        form.fields['condizioni_pagamento'].initial = "TP02"
        form.fields['scadenza_pagamento'].label = "Data Scadenza Pagamento:"
        form.fields['scadenza_pagamento'].initial = fine_mese
        form.fields['modalita_pagamento'].label = "Modalità di Pagamento:"
        form.fields['modalita_pagamento'].initial = "MP01"
        # Rimosso perchè ... Boh non serve a un caz.
        #form.fields['note'].label = "Eventuali Note:"
        #form.fields['note'].widget.attrs.update({'placeholder': 'Inserisci eventuali note'})

        return form

    def form_valid(self, form):
        bolla = form.save(commit=False)
        cliente = form.cleaned_data['cliente']
        bolla.tipo_documento = cliente.tipo_documento_predefinito
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('fattura-update', kwargs={'pk':self.object.pk})

class RigaFatturaDeleteView(DeleteView):
    model = RigaFattura
    def get_success_url(self):
        fattura_id = self.object.fattura.id
        return reverse('fattura-update', kwargs={'pk':fattura_id})

def FatturaStampaView(request, pk):
    import base64
    fattura = get_object_or_404(Fattura, pk=pk)
    pdf_content = genera_pdf_base64(fattura)
    if not pdf_content:
        return HttpResponse("Errore: Nessun file PDF da scaricare", status=400)

    # Crea la risposta come file PDF scaricabile
    pdf_content = base64.b64decode(pdf_content)
    response = HttpResponse(pdf_content, content_type='application/pdf')
    nome = fattura.cliente.nome.replace(" ", "")
    response['Content-Disposition'] = f'attachment; filename="Fattura-{nome}-N-{fattura.numero}.pdf"'
    return response

class SchedaTVListView(LoginRequiredMixin, ListView):
    model = SchedaTV
    template_name = 'schede_tv/schedatv_list.html'
    context_object_name = 'schede_tv'
    ordering = ['-data']

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        zona = self.request.GET.get('zona')
        if zona:
            zona = int(zona)
            zona = Zona.objects.filter(pk = zona)
        if hasattr(user, 'zona'):
            if not zona:
                zona = Zona.objects.filter(pk = user.zona.pk)
            tipo_documento_id = TipoDocumento.objects.filter(concessionario=user.zona.concessionario, nome="NTV").first().pk
        elif hasattr(user, 'concessionario'):
            if not zona:
                zona = Zona.objects.filter(concessionario=user.concessionario).values_list("nome", flat=True)
            tipo_documento_id = TipoDocumento.objects.filter(concessionario=user.concessionario, nome="NTV").first().pk
        else:
            tipo_documento_id = TipoDocumento.objects.none()

        # tipo_documento_id = tipo_documento_id.pk()
        # Gestione delle date
        oggi = make_aware(datetime.now())
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)

        #DEBUG print(zona)
        # Filtro per tipo documento
        queryset = queryset.filter(tipo_documento_id=tipo_documento_id, cliente__zona__in=zona, data__range=(data_inizio, data_fine)).order_by('cliente__nome')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        oggi = make_aware(datetime.now())
        oggi = oggi.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Gestione delle date
        zona = self.request.GET.get('zona')
        data_inizio_str = self.request.GET.get('data_inizio')
        data_fine_str = self.request.GET.get('data_fine')
        data_inizio = self.get_data_filtrata(data_inizio_str, oggi, inizio=True)
        data_fine = self.get_data_filtrata(data_fine_str, oggi, inizio=False)
        context['data_inizio'] = data_inizio
        context['data_fine'] = data_fine
        # print(tipo_documento_id)
        if zona:
            zone = int(zona)
            zone = Zona.objects.filter(pk = zona)
        elif zona == "":
            zone = None
        else:
            zone = None
        if hasattr(user, 'zona'):
            if not zone:
                zone = Zona.objects.filter(pk= user.zona.pk)
            tipo = TipoDocumento.objects.filter(nome="NTV", concessionario=user.zona.concessionario).first().pk
        elif hasattr(user, 'concessionario'):
            if not zone:
                zone = Zona.objects.filter(concessionario = user.concessionario)
            tipo = TipoDocumento.objects.filter(nome="NTV", concessionario=user.concessionario).first().pk
        else:
            if not zone:
                zone = Zona.objects.none()
            tipo = TipoDocumento.objects.none()
        schede = SchedaTV.objects.filter(tipo_documento_id=tipo, cliente__zona__in=zone, data__range=(data_inizio, data_fine)).order_by("cliente__nome")
        context['schede_tv'] = schede
        mesi_italiani = {
            "Gennaio": "01", "Febbraio": "02", "Marzo": "03",
            "Aprile": "04", "Maggio": "05", "Giugno": "06",
            "Luglio": "07", "Agosto": "08", "Settembre": "09",
            "Ottobre": "10", "Novembre": "11", "Dicembre": "12"
        }
        context['mesi'] = mesi_italiani
        context['zone'] = zone
        return context


    def get_data_filtrata(self, data_str, oggi, inizio=True):
        """Funzione ausiliaria per calcolare data_inizio e data_fine"""
        if data_str:
            data = make_aware(datetime.strptime(data_str, '%Y-%m-%d'))
            if inizio:
                data = datetime.combine(data, datetime.min.time())
                data = data.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                data = datetime.combine(data, datetime.max.time())
                data = data.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # Se non viene fornita una data, si usa quella corrente
            if inizio:
                data = oggi.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                data = oggi.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Verifica se la data è già aware, se sì non applicare make_aware
        if not is_aware(data):
            data = make_aware(data, timezone.get_current_timezone())

        return data



class SchedaTVDetailView(LoginRequiredMixin, DetailView):
    model = SchedaTV
    template_name = 'schede_tv/schedatv_detail.html'
    context_object_name = 'scheda_tv'


class SchedaTVCreateView(LoginRequiredMixin, CreateView):
    model = SchedaTV
    template_name = "schede_tv/schedatv_create.html"
    fields = ['data', 'cliente', 'tipo_documento']  # Aggiungi tipo_documento

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user
        if hasattr(user, "zona"):
            conc = user.zona.concessionario
        elif hasattr(user, "concessionario"):
            conc = user.concessionario
        form.fields['tipo_documento'].queryset = TipoDocumento.objects.filter(concessionario= conc, nome="NTV")
        form.fields['cliente'].queryset = Cliente.objects.filter(concessionario = conc, tipo_documento_predefinito__nome = "NTV") or Cliente.objects.none()
        # Puoi fare qui logica per filtrare i tipi di documento, se necessario
        # E.g., form.fields['tipo_documento'].queryset = TipoDocumento.objects.filter(concessionario=user.concessionario)
        return form

    def get_success_url(self):
        return reverse_lazy('schedatv-update', kwargs={'pk': self.object.pk})


class SchedaTVUpdateView(LoginRequiredMixin, UpdateView):
    model = SchedaTV
    fields = ['data', 'cliente', 'numero']
    template_name = 'schede_tv/schedatv_form.html'
    success_url = reverse_lazy('schedetv-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = self.object.cliente
        categoria_selezionata = self.request.GET.get('categoria')
        if categoria_selezionata is None:
            categoria_selezionata = 0
        categoria_selezionata = int(categoria_selezionata)
        context["categoria_selezionata"] = str(categoria_selezionata)
        if categoria_selezionata == 0:
            # Se user non seleziona categoria, mostra articoli valat
            articoli_concessi = ArticoliConcessi.objects.filter(
                proprietario=cliente.proprietario
            ).values_list('articolo', flat=True)
            categoria_selezionata = 0
        else:
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)
            # Ottieni gli articoli concessi al proprietario del cliente

        context['righe'] = self.object.righe.all()
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi).order_by('nome'))
        ).order_by('ordine')
        return context

    def post(self, request, *args, **kwargs):
        categoria_selezionata = request.POST.get('categoria')
        if 'add_riga' in request.POST:
            articolo_id = request.POST.get('articolo')
            quantita = request.POST.get('quantita')
            giorno = int(request.POST.get('giorno'))

            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('schedatv-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

            if not articolo_id:
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('schedatv-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

            if not giorno:
                messages.error(request, "Inserire il giorno del mese.")
                return redirect(
                    f"{reverse('schedatv-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            elif giorno < 0 or giorno > 31:
                messages.error(request, "Giorno non valido, inserire numero del giorno valido.")
                return redirect(
                    f"{reverse('schedatv-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

            articolo = Articolo.objects.get(pk=articolo_id)

            RigaSchedaTV.objects.create(
                scheda=self.get_object(),
                giorno=giorno,
                articolo=articolo,
                quantita=int(quantita)
            )
            return redirect(
                f"{reverse('schedatv-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")

        elif 'confirm' in request.POST:
            return redirect('schedetv-list')

        return super().post(request, *args, **kwargs)


class SchedaTVDeleteView(LoginRequiredMixin, DeleteView):
    model = SchedaTV
    success_url = reverse_lazy('schedetv-list')
    template_name = 'schede_tv/schedatv_confirm_delete.html'

    def post(self, request, *args, **kwargs):
        scheda = self.get_object()
        tipo_documento = scheda.tipo_documento
        anno = (scheda.data or timezone.now().date()).year

        with transaction.atomic():
            # Lock the counter row for this (tipo, anno)
            try:
                counter = (TipoDocCounter.objects
                           .select_for_update()
                           .get(tipo=tipo_documento, anno=anno))
            except TipoDocCounter.DoesNotExist:
                messages.error(request, "Contatore annuale mancante: impossibile eliminare in modo sicuro.")
                return redirect('schedetv-list')

            # Check it's the last SchedaTV for that tipo AND year
            ultima_scheda = (SchedaTV.objects
                             .filter(tipo_documento=tipo_documento, data__year=anno)
                             .order_by('-numero')
                             .first())

            if scheda != ultima_scheda:
                messages.error(request, "Puoi eliminare solo l'ultima scheda TV di quell'anno per quel tipo documento.")
                return redirect('schedetv-list')

            # Decrement annual counter (guard against going negative)
            if counter.ultimo_numero == 0:
                messages.error(request, "Contatore annuale già a zero: stato incoerente.")
                return redirect('schedetv-list')

            counter.ultimo_numero -= 1
            counter.save(update_fields=["ultimo_numero"])

            # Optional: keep cached field on TipoDocumento aligned
            tipo_documento.ultimo_numero = counter.ultimo_numero
            tipo_documento.save(update_fields=["ultimo_numero"])

            scheda.delete()

        return redirect(self.success_url)

class RigaSchedaTVDeleteView(LoginRequiredMixin, DeleteView):
    model = RigaSchedaTV

    def get_success_url(self):
        return reverse_lazy('schedatv-update', kwargs={'pk': self.object.scheda.pk})

class CreaSchedeTV(View):
    template_name = "schede_tv/crea_schede.html"
    success_url = reverse_lazy('schedetv-list')
    def get(self, request):

        user = self.request.user
        mese = self.request.GET.get("mese")
        if not mese:
            messages.error(request, f"Selezionare il mese per cui creare le schede.")
            return redirect(self.success_url)
        else:
            mese = int(mese)

        if hasattr(user, "zona"):
            conc = user.zona.concessionario
        elif hasattr(user, "concessionario"):
            conc = user.concessionario
        else:
            conc = Concessionario.objects.none()

        tipo_doc = TipoDocumento.objects.filter(nome="NTV", concessionario=conc).first()
        #print(str(mese))
        # Recupera data di oggi e aggiungici un mese
        data_inizio = timezone.now().replace(day=1, month=mese)
        # data_inizio = data_inizio + relativedelta(months=1)
        #print("Oggi: ", timezone.now(), " Prossimo mese: ", data_inizio)

        clienti = Cliente.objects.filter(concessionario = conc, tipo_documento_predefinito = tipo_doc, ignorare = False).order_by("nome")
        numero = 0
        try:
            for cliente in clienti:

                SchedaTV.objects.create(
                    cliente = cliente,
                    tipo_documento = tipo_doc,
                    data = data_inizio,
                )
                numero += 1

            messages.success(request, f"Create {numero} schede per {_date(data_inizio, 'F Y')}")
            return redirect(self.success_url)

        except Exception as e:
            messages.error(request, f"Scheda per cliente non creata. Errore imprevisto.")
            return redirect(self.success_url)


class CreaFattureAuto(LoginRequiredMixin, View):
    template_name = "fatture/crea_fatture.html"
    success_url = reverse_lazy('fatture-list')
    login_url = "/login/"

    def get(self, request):

        user = self.request.user
        mese = self.request.GET.get("mese")
        anno = self.request.GET.get("anno")
        if not mese:
            messages.error(request, "Selezionare il mese per cui creare le fatture.")
            return redirect(self.success_url)
        else:
            mese = int(mese)
        if not anno:
            messages.error(request, "Selezionare l'anno per cui creare le fatture.")
            return redirect(self.success_url)
        else:
            anno = int(anno)

        if hasattr(user, "zona"):
            conc = user.zona.concessionario
        elif hasattr(user, "concessionario"):
            conc = user.concessionario
        else:
            conc = Concessionario.objects.none()

        tipo_doc = TipoDocumento.objects.filter(concessionario=conc)
        # Recupera data di oggi e aggiungici un mese
        data_inizio = timezone.now().replace(day=1, month=mese, year=anno, hour=0, minute=0)
        data_fine = data_inizio + relativedelta(months=1) - relativedelta(days=1)
        data_fine = datetime.combine(data_fine.date(), datetime.max.time())
        print("Oggi: ", data_inizio, " Ultimo giorno mese precedente: ", data_fine)
        clienti = Cliente.objects.filter(concessionario = conc, tipo_documento_predefinito__in=tipo_doc).order_by("nome")
        numero = 0
        riepilogo = []

        for cliente in clienti:
            fattura = {
                "cliente": cliente.nome,
                "id_cliente": cliente.id,
                "righe": defaultdict(lambda: {"prezzo": 0.0, "quantita": 0}),
                "errori": []
            }
            tipo_rf = TipoDocumento.objects.filter(nome="RF", concessionario=conc).first()
            tipo_cls = TipoDocumento.objects.filter(nome="CLS", concessionario=conc).first()
            tipo_ntv = TipoDocumento.objects.filter(nome="NTV", concessionario=conc).first()
            if cliente.tipo_documento_predefinito == tipo_rf:
                # DEBUG print("hello")
                # Recupera le bolle dei clienti CLS
                #bolle_cliente = Bolla.objects.filter(
                #    tipo_documento=tipo_cls,
                #    data__range=(data_inizio, data_fine)
                #)
                continue
            elif cliente.tipo_documento_predefinito == tipo_ntv:
                bolle_cliente = SchedaTV.objects.filter(
                    cliente=cliente,
                    data__range=(data_inizio, data_fine)
                )
            elif cliente.tipo_documento_predefinito == tipo_cls:
                continue
            else:
                # Recupera le bolle del cliente per il mese selezionato
                bolle_cliente = Bolla.objects.filter(
                    cliente=cliente,
                    data__range=(data_inizio, data_fine)
                )

            for bolla in bolle_cliente:
                for riga in bolla.righe.all():
                    if cliente.nome == "Ricco Group":
                        print(bolla)
                    articolo = riga.articolo
                    prezzo_pers = PrezziPersonalizzati.objects.filter(articolo=articolo, cliente=cliente).first()
                    if prezzo_pers:
                        prezzo = float(prezzo_pers.prezzo)
                    elif cliente.tipo_documento_predefinito == tipo_rf:
                        prezzo = articolo.prezzo_tr
                        iva = 22
                    else:
                        prezzo = articolo.prezzo

                    # Aggiornamento riga
                    if articolo.nome not in fattura["righe"]:
                        fattura["righe"][articolo.nome] = {
                            "prezzo":prezzo,
                            "quantita":0
                        }
                    fattura["righe"][articolo.nome]["prezzo"] = float(prezzo)
                    fattura["righe"][articolo.nome]["quantita"] += riga.quantita

            # Converti defaultdict a dict normale per il template
            if not fattura["righe"]:
                continue
            fattura["righe"] = dict(fattura["righe"])
            riepilogo.append(fattura)
            numero += 1

        context = {
            'riepilogo':riepilogo,
            'mese': _date(data_fine, 'F Y'),
            'total_clienti': len(riepilogo)
        }
        request.session['riepilogo'] = riepilogo
        request.session['data_fine'] = data_fine.strftime('%Y-%m-%d')
        request.session['numero'] = numero
        request.session['concessionario'] = conc.pk
        #print(riepilogo)
        return render(request, 'fatture/anteprima_fatture.html', context)


class ConfermaFattureView(View):
    def post(self, request, *args, **kwargs):

        with transaction.atomic():  # Garantisce che tutto vada a buon fine o si annulli
            riepilogo = request.session.get('riepilogo', [])  # Recupera il riepilogo dalla sessione

            data_fine_str = request.session.get('data_fine')
            data_fine = datetime.strptime(data_fine_str, '%Y-%m-%d')
            anno = data_fine.year
            num = request.session.get('numero')
            conc = get_object_or_404(Concessionario, pk=request.session.get('concessionario'))
            tipo_fattura = TipoFattura.objects.filter(anno=anno, concessionario=conc, tipo="TD01").first()
            tipo_NTV = TipoDocumento.objects.filter(concessionario = conc, nome="NTV").first()
            tipo_NT = TipoDocumento.objects.filter(concessionario = conc, nome="NT").first()
            numero = tipo_fattura.ultimo_numero
            if not riepilogo:
                messages.error(request, f"Non ci sono articoli ordinati da nessun cliente per il mese di {_date(data_fine, 'F Y')}")
                return redirect('fatture-list')
            for fattura_data in riepilogo:
                cliente = get_object_or_404(Cliente, pk=fattura_data['id_cliente'])
                # Crea la fattura
                if cliente.tipo_documento_predefinito == tipo_NTV:
                    numero += 1
                    fattura = Fattura.objects.create(
                        cliente=cliente,
                        data=data_fine,
                        numero = str(numero),
                        concessionario = conc,
                        tipo_fattura = tipo_fattura,
                        condizioni_pagamento = "TP02",
                        scadenza_pagamento = data_fine,
                        modalita_pagamento = "MP01",
                    )
                    fattura.numero = numero

                elif cliente.tipo_documento_predefinito == tipo_NT:
                    numero += 1
                    fattura = Fattura.objects.create(
                        cliente=cliente,
                        data=data_fine,
                        numero = str(numero),
                        concessionario=conc,
                        tipo_fattura=tipo_fattura,
                        condizioni_pagamento="TP02",
                        scadenza_pagamento=data_fine,
                        modalita_pagamento="MP05",
                    )
                    fattura.numero = numero

                else:
                    break
                # Crea le righe della fattura
                for codice_articolo, riga_data in fattura_data['righe'].items():
                    articolo = Articolo.objects.filter(nome=codice_articolo).first()
                    RigaFattura.objects.create(
                        fattura=fattura,
                        articolo_id=articolo.pk,
                        quantita=riga_data['quantita'],
                        prezzo=Decimal(riga_data['prezzo']),
                        iva = articolo.iva,
                    )

                # Aggiorna il totale della fattura
                fattura.aggiorna_totali()
            tipi_fattura = TipoFattura.objects.filter(concessionario=conc, anno=anno)
            for tipo_fatt in tipi_fattura:
                tipo_fatt.ultimo_numero = numero
                tipo_fatt.save()  # Salva il nuovo ultimo numero
            messages.success(request, f"Create {num} fatture per {_date(data_fine, 'F Y')}")
            return redirect('fatture-list')

import pandas as pd
import plotly.express as px
import django_pandas.io as dp

def report_avanzato(request):
    data_inizio = request.GET.get("data_inizio")
    data_fine = request.GET.get("data_fine")
    cliente_id = request.GET.get("cliente_id")
    user = request.user

    if hasattr(user, 'zona'):
        # Se l'utente ha una zona, mostra solo quella
        zona = Zona.objects.filter(pk=user.zona.pk)
        concessionario = zona.first().concessionario
    elif hasattr(user, 'concessionario'):
        concessionario = user.concessionario
    else:
        concessionario = None

    tipi_doc = TipoDocumento.objects.filter(concessionario=concessionario).exclude(nome__in=["RF"])
    clienti = Cliente.objects.filter(tipo_documento_predefinito__in=tipi_doc).order_by("tipo_documento_predefinito")

    if not data_inizio or not data_fine or not cliente_id:
        return render(request, 'riepiloghi/report.html', {'clienti': clienti})

    #if not data_inizio or not data_fine or not cliente_id:
    #    messages.error(request, "Inserire tutti i parametri.")
    #    return redirect('report-avanzato')

    # Converti le date
    data_inizio = datetime.strptime(data_inizio, "%Y-%m-%d")
    data_fine = datetime.strptime(data_fine, "%Y-%m-%d")
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    data_fine = data_fine.replace(hour=23, minute=59, second=59, microsecond=999999)
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    tipo_ntv = TipoDocumento.objects.filter(nome="NTV", concessionario=concessionario).first()
    if cliente.tipo_documento_predefinito == tipo_ntv:
        bolle_cliente = SchedaTV.objects.filter(
            cliente=cliente,
            data__range=(data_inizio, data_fine)
        )
        dati = RigaSchedaTV.objects.filter(scheda__in=bolle_cliente).select_related('scheda').values(
            'id',
            'quantita',
            'giorno',
            'scheda__data',
            'articolo',
        )
    else:
        bolle_cliente = Bolla.objects.filter(data__range=(data_inizio, data_fine), cliente=cliente)
        dati = RigaBolla.objects.filter(bolla__in=bolle_cliente).select_related('bolla').values(
        'id',  # Campo di RigaBolla
        'quantita',  # Campo di RigaBolla
        'lotto',  # Campo di RigaBolla
        'bolla__data',  # Campo di Bolla (data)
        'bolla__numero',  # Campo di Bolla (numero)
        'articolo'
        )

    dataframe = dp.read_frame(dati)
    if cliente.tipo_documento_predefinito == tipo_ntv:
        # Combina il giorno di RigaSchedaTV con la data di SchedaTV
        dataframe['bolla__data'] = dataframe.apply(
            lambda row: pd.to_datetime(f"{row['scheda__data'].year}-{row['scheda__data'].month}-{row['giorno']}"),
            axis=1
        )
    bar = px.bar(dataframe, x="articolo", y="quantita", labels={'articolo':"Articolo", 'quantita':"Quantità"}) #Quantità per articolo


    df_articoli = dataframe[dataframe['articolo'].str.contains('600125|600127|600171|600026|600111|600112', na=False)]
    df_heat = dataframe[~dataframe['articolo'].str.contains('600125|600127|600171|600026|600111|600112', na=False)]
    df_heat['giorno_settimana'] = df_heat['bolla__data'].dt.day_name()
    df_articoli['giorno_settimana'] = df_articoli['bolla__data'].dt.day_name()
    giorni_ita = {
        'Monday':'Lunedì',
        'Tuesday':'Martedì',
        'Wednesday':'Mercoledì',
        'Thursday':'Giovedì',
        'Friday':'Venerdì',
        'Saturday':'Sabato',
        'Sunday':'Domenica'
    }
    df_articoli['giorno_settimana'] = df_articoli['giorno_settimana'].map(giorni_ita)
    df_heat['giorno_settimana'] = df_heat['giorno_settimana'].map(giorni_ita)
    df_heatmap = df_heat.groupby(['articolo', 'giorno_settimana']).agg({"quantita":"sum"}).reset_index()

    df_raggrup = df_articoli.groupby(['articolo', 'giorno_settimana']).agg({'quantita':'sum'}).reset_index()
    ordine_giorni = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']
    df_raggrup['giorno_settimana'] = pd.Categorical(df_raggrup['giorno_settimana'], categories=ordine_giorni, ordered=True)
    df_raggrup = df_raggrup.sort_values('giorno_settimana')

    df_heatmap['giorno_settimana'] = pd.Categorical(df_heatmap['giorno_settimana'], categories=ordine_giorni,
                                                    ordered=True)
    df_heatmap = df_heatmap.sort_values('giorno_settimana')

    graf_giorni = px.bar(df_raggrup, x='giorno_settimana', y='quantita', color='articolo', barmode='group', labels={'articolo':"Articolo", 'giorno_settimana':"Giorno della Settimana", 'quantita':"Quantità"})
    pivot_table = df_heatmap.pivot_table(index='giorno_settimana', columns='articolo', values='quantita', aggfunc='sum')
    heat = px.imshow(pivot_table, labels=dict(x="Articolo", y="Giorno", color="Quantità"))
    bar_html = bar.to_html(full_html=False)
    heat_html = heat.to_html(full_html=False)
    giorni_html = graf_giorni.to_html(full_html=False)
    context ={
        'clienti': clienti,
        'cliente': cliente,
        'bar_html': bar_html,
        'heat_html': heat_html,
        'giorni_html': giorni_html,
        'data_inizio': data_inizio.date(),
        'data_fine': data_fine.date()
    }
    return render(request, 'riepiloghi/report.html', context)

import itertools

def previsione_carico(request):
    data_inizio = request.GET.get("data_inizio")
    data_fine = request.GET.get("data_fine")
    user = request.user

    if hasattr(user, 'zona'):
        # Se l'utente ha una zona, mostra solo quella
        zona = Zona.objects.filter(pk=user.zona.pk)
        concessionario = zona.first().concessionario
    elif hasattr(user, 'concessionario'):
        concessionario = user.concessionario
        zona = Zona.objects.filter(concessionario = concessionario).first()
    else:
        concessionario = None

    if not data_inizio or not data_fine:
        return render(request, 'riepiloghi/previsioni.html')

    giorni = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica']

    # Converti le date
    data_inizio = datetime.strptime(data_inizio, "%Y-%m-%d")
    data_fine = datetime.strptime(data_fine, "%Y-%m-%d")

    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())

    data_fine = data_fine.replace(hour=23, minute=59, second=59, microsecond=999999)

    carichi = Carico.objects.filter(data__range=(data_inizio, data_fine), zona = zona)
    resi = Reso.objects.filter(data__range=(data_inizio, data_fine), zona = zona)

    dati_carico = RigaCarico.objects.filter(carico__in=carichi).select_related('carico').values(
        'id',
        'carico__data',
        'articolo',
        'quantita',
    )
    dati_reso = RigaReso.objects.filter(reso__in=resi).select_related('reso').values(
        'id',
        'reso__data',
        'articolo',
        'quantita',
    )
    giorni_ita = {
        'Monday':'Lunedì',
        'Tuesday':'Martedì',
        'Wednesday':'Mercoledì',
        'Thursday':'Giovedì',
        'Friday':'Venerdì',
        'Saturday':'Sabato',
        'Sunday':'Domenica'
    }

    df_carico = dp.read_frame(dati_carico)
    df_reso = dp.read_frame(dati_reso)
    df_carico['carico__data'] = pd.to_datetime(df_carico['carico__data'], format='%Y-%m-%d')
    df_reso['reso__data'] = pd.to_datetime(df_reso['reso__data'], format='%Y-%m-%d')
    df_carico['giorno_settimana'] = df_carico['carico__data'].dt.day_name()

    df_reso['giorno_settimana'] = df_reso['reso__data'].dt.day_name()
    df_reso['giorno_settimana'] = df_reso['giorno_settimana'].map(giorni_ita)
    df_reso['giorno_successivo'] = df_reso['reso__data'] + pd.DateOffset(days=1)
    df_reso['giorno_successivo'] = df_reso['giorno_successivo'].dt.day_name().map(giorni_ita)
    df_carico['giorno_successivo'] = df_carico['carico__data'] + pd.DateOffset(days=1)
    df_carico['giorno_successivo'] = df_carico['giorno_successivo'].dt.day_name().map(giorni_ita)
    df_reso['giorno_settimana'] = df_reso['giorno_successivo']
    df_carico['giorno_settimana'] = df_carico['giorno_successivo']
    articoli = list(df_carico['articolo'].unique()) if not df_carico.empty else []
    combinazioni = list(itertools.product(articoli, giorni))
    df_completo = pd.DataFrame(combinazioni, columns=['articolo', 'giorno_settimana'])

    df_carico = df_carico.groupby(['articolo', 'giorno_settimana']).agg({"quantita":"mean"}).reset_index()
    df_reso = df_reso.groupby(['articolo', 'giorno_settimana']).agg({"quantita":"mean"}).reset_index()
    df_storico = pd.merge(
        df_completo,
        pd.merge(
            df_carico,
            df_reso,
            on=['articolo', 'giorno_settimana'],
            suffixes=('_carico', '_reso'),
            how='left'
        ),
        how='left'
    ).fillna(0)

    df_storico['vendite'] = df_storico['quantita_carico'] - df_storico['quantita_reso']
    df_storico = df_storico[df_storico['vendite'] > 0]
    df_media = df_storico.groupby(['articolo', 'giorno_settimana']).agg({"vendite":"median"}).reset_index()
    ordine_giorni = ['Lunedì', 'Martedì', 'Mercoledì', 'Venerdì', 'Sabato', 'Domenica']
    df_media['giorno_settimana'] = pd.Categorical(df_media['giorno_settimana'], categories=ordine_giorni, ordered=True)
    df_media = df_media.sort_values('giorno_settimana')
    df_media = df_media[~df_media["articolo"].str.contains("0234|6032|027110|600075/V|6035|020910", na=False)]

    #codice per i grafici

    bar_media = px.bar(
        df_media[df_media['vendite'] != 0],
        x='giorno_settimana',
        y='vendite',
        color='articolo',
        barmode='group',
        title='Vendite Medie per Articolo e Giorno',
        labels={'vendite': 'Vendite Medie', 'giorno_settimana': 'Giorno della Settimana'}
    ).update_xaxes(categoryorder='array', categoryarray=ordine_giorni)
    bar_media_html = bar_media.to_html()

    heatmap = px.density_heatmap(
        df_media[df_media['vendite'] != 0],
        x='giorno_settimana',
        y='articolo',
        z='vendite',
        title='Heatmap Vendite Medie per Giorno Settimana',
        labels = {'vendite': 'Vendite Medie', 'giorno_settimana': 'Giorno della Settimana', 'articolo':'Articolo'}
    ).update_xaxes(categoryorder='array', categoryarray=ordine_giorni).update_layout(coloraxis_colorbar_title='Somma delle Vendite')
    heatmap.update_traces(
        hovertemplate="Articolo: %{y}<br>Giorno: %{x}<br>Vendite: %{z}"
    )
    heatmap_html = heatmap.to_html()
    line = px.line(
        df_media,
        x='giorno_settimana',
        y='vendite',
        color='articolo',
        markers=True,
        title="Andamento Settimanale delle Vendite",
        labels={'vendite': 'Vendite Medie', 'giorno_settimana': 'Giorno'}
    ).update_xaxes(categoryorder='array', categoryarray=ordine_giorni)
    line_html = line.to_html()
    torta = px.pie(
        df_media.groupby('articolo')['vendite'].sum().reset_index(),
        names='articolo',
        values='vendite',
        title='Distribuzione Percentuale delle Vendite per Articolo',
        labels={'vendite':"Vendite"}
    ).update_traces(textinfo='none',
                    hovertemplate='<b>%{label}</b><br>Quantità: %{value}<extra></extra>'
                    )
    torta_html = torta.to_html()

    context = {
        'bar_media_html': bar_media_html,
        'heatmap_html': heatmap_html,
        'line_html': line_html,
        'torta_html': torta_html,
        'data_inizio': data_inizio.date(),
        'data_fine': data_fine.date()
    }
    return render(request, 'riepiloghi/previsioni.html', context)

class UploadFatturaView(View):
    template_name = 'riepiloghi/upload_fattura.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        # Gestione upload file
        file1_bytes = request.FILES['file1'].read()
        file2_bytes = request.FILES['file2'].read()

        if not (file1_bytes and file2_bytes):
            return render(request, self.template_name, {'error': 'Caricare entrambi i file delle note credito CLS!'})

        # Estrai PDF dai file .p7m o .xml
        try:
            pdf1_bytes = self._process_bytes(file1_bytes, request.FILES['file1'].name)
            pdf2_bytes = self._process_bytes(file2_bytes, request.FILES['file2'].name)
        except Exception as e:
            return render(request, self.template_name, {'error': f"Errore estrazione PDF: {str(e)}"})

        # Unisci i PDF e analizza le bolle
        merged_pdf_bytes = self._merge_in_memory([pdf1_bytes, pdf2_bytes])

        testo_pdf = self._extract_text_fast(merged_pdf_bytes)
        bolle_fattura = centrale_fattura.parse_fattura_pdf(testo_pdf)

        if not bolle_fattura:
            return render(request, self.template_name, {'error': 'Nessuna bolla trovata nella fattura!'})

        # Estrai la data dalla prima bolla (supponendo che tutte siano dello stesso mese)
        data_fattura_str = bolle_fattura[0]['data_bolla']  # Formato: 'DD/MM/YY'
        data_fattura = datetime.strptime(data_fattura_str, '%d/%m/%y').date()
        data_fattura = data_fattura.replace(day=1)

        # Filtra le bolle del database per il mese/anno della fattura
        bolle_db = self._get_bolle_by_month(data_fattura)

        # Prepara i DataFrame come richiesto dalla tua funzione
        df_bolle = pd.DataFrame([{
            'numero_bolla': str(bolla.numero),
            'data': bolla.data,
            'cliente': bolla.cliente.nome,
        } for bolla in bolle_db])

        df_articoli_bolle = pd.DataFrame([{
            'numero_bolla': str(riga.bolla.numero),
            'codice_articolo': riga.articolo.nome,
            'quantita': riga.quantita
        } for bolla in bolle_db for riga in bolla.righe.all() if riga.articolo.nome not in ["027110/R", "027110/S"]])

        # Esegui il confronto usando la tua funzione esistente
        report_data = centrale_fattura.confronta_fattura_bolle(bolle_fattura, df_bolle, df_articoli_bolle)
        # Converti il report in testo
        report_text = self._format_report(report_data)

        # Pulizia file temporanei
        #self._cleanup_temp_files([file1_path, file2_path, pdf1_path, pdf2_path, merged_pdf_path])

        return render(request, self.template_name, {
            'success': True,
            'report_text': report_text,
            'mese_anno': data_fattura.strftime('%B %Y').capitalize()
        })

    def _extract_text_fast(self, pdf_bytes):
        doc = fitz.open("pdf", pdf_bytes)
        return chr(12).join([page.get_text() for page in doc])

    def _process_bytes(self, file_bytes, filename):
        if filename.lower().endswith('.p7m'):
            return self._extract_p7m_bytes(file_bytes)
        elif filename.lower().endswith('.xml'):
            return self._extract_p7m_bytes(file_bytes)

    def _extract_p7m_bytes(self, data):
        pdf_start = data.find(b"<Attachment>")
        pdf_end = data.find(b"</Attachment>")
        base64_pdf = data[pdf_start + 12:pdf_end].strip()
        return base64.b64decode(base64_pdf)

    def _merge_in_memory(self, pdfs_list):
        merger = PdfMerger()
        for pdf_bytes in pdfs_list:
            merger.append(BytesIO(pdf_bytes))
        output = BytesIO()
        merger.write(output)
        return output.getvalue()

    def _format_report(self, report_data):
        """Genera un report dettagliato"""
        html_output = ""

        if report_data.get("errori"):
            html_output += "\nERRORI:"
            for errore in report_data["errori"]:
                html_output += f" - {errore}"

        # Processa ogni bolla
        for bolla in report_data.get("bolle", []):
            if bolla["articoli_mancanti_in_fattura"] or bolla["articoli_mancanti_in_bolle"] or bolla[
                "differenze_quantita"]:
                html_output += f"<h4><b>BOLLA:</b> {bolla['numero_bolla']} - <b>DATA:</b> {bolla['data_bolla']} - <b>CLIENTE:</b> {bolla['cliente']}</h4>"

                # Articoli mancanti
                if bolla["articoli_mancanti_in_fattura"]:
                    html_output += "<h5>ARTICOLI PRESENTI NELLE BOLLE MA NON IN FATTURA:</h5>"
                    html_output += "<ul>"
                    for art in bolla["articoli_mancanti_in_fattura"]:
                        html_output += f"<li>{art}</li>"
                    html_output += "</ul>"

                if bolla["articoli_mancanti_in_bolle"]:
                    html_output += "<h5>ARTICOLI PRESENTI IN FATTURA MA NON NELLE BOLLE:</h5>"
                    html_output += "<ul>"
                    for art in bolla["articoli_mancanti_in_bolle"]:
                        html_output += f"<li>{art}</li>"
                    html_output += "</ul>"

                # Differenze quantità
                if bolla["differenze_quantita"]:
                    html_output += "<h5>DIFFERENZE DI QUANTITÀ:</h5>"
                    html_output += "<table class='table table-hover table-sm'>"
                    html_output += "<thead><tr><th>Articolo</th><th>Q.tà Bolle</th><th>Q.tà Fattura</th><th>Differenza</th></tr></thead>"
                    html_output += "<tbody>"
                    for diff in bolla["differenze_quantita"]:
                        html_output += "<tr>"
                        html_output += f"<td>{diff['codice_articolo']}</td>"
                        html_output += f"<td>{diff['quantita_bolla']:.2f}</td>"
                        html_output += f"<td>{diff['quantita_fattura']:.2f}</td>"
                        html_output += f"<td>{diff['differenza']:.2f}</td>"
                        html_output += "</tr>"
                    html_output += "</tbody>"
                    html_output += "</table>"

        if not html_output:
            html_output = "<h5>Nessun articolo mancante, tutto ok!</h5>"

        return "".join(html_output)

    def _process_file(self, file_path):
        """Estrae PDF da .p7m o .xml"""
        if file_path.lower().endswith('.p7m'):
            output_path = file_path.replace('.p7m', '.pdf')
            centrale_fattura.clean_p7m(file_path, output_path)
        elif file_path.lower().endswith('.xml'):
            output_path = file_path.replace('.xml', '.pdf')
            centrale_fattura.extract_pdf_from_xml(file_path, output_path)
        else:
            raise ValueError("Formato file non supportato")
        return output_path

    def _merge_pdfs(self, pdf_paths, output_path):
        """Unisce i PDF in un unico file"""
        merger = PdfMerger()
        for pdf in pdf_paths:
            merger.append(pdf)
        merger.write(output_path)
        merger.close()

    def _get_bolle_by_month(self, data_fattura):
        """Filtra le bolle del database per mese/anno"""
        inizio_mese = data_fattura.replace(day=1)
        fine_mese = inizio_mese + relativedelta(months=1) - relativedelta(seconds=1)
        if hasattr(self.request.user, 'zona'):
            tipi = TipoDocumento.objects.filter(concessionario=self.request.user.zona.concessionario, nome="CLS")
        elif hasattr(self.request.user, 'concessionario'):
            tipi = TipoDocumento.objects.filter(concessionario=self.request.user.concessionario, nome="CLS")
        else:
            tipi = TipoDocumento.objects.none()

        return Bolla.objects.filter(
            tipo_documento__in=tipi,
            data__gte=inizio_mese,
            data__lte=fine_mese
        ).prefetch_related('righe')

    def _cleanup_temp_files(self, file_paths):
        """Elimina i file temporanei"""
        for path in file_paths:
            try:
                os.remove(path)
            except OSError:
                pass
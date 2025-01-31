from django.views.generic import TemplateView, ListView, DetailView
from django.views.generic import DeleteView, UpdateView, CreateView
from django.contrib.auth.views import LoginView
from .models import *
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
from django.http import FileResponse
import os
from django.utils.timezone import make_aware, now, is_aware
from django.db.models import Sum, Q, F
from django.utils import timezone


class HomePageView(TemplateView):
    template_name = 'bolle/homepage.html'
    model = Concessionario
    nome = Concessionario.nome

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
        tipo_documento = bolla.tipo_documento

        # Controlla se la bolla è l'ultima
        ultima_bolla = Bolla.objects.filter(tipo_documento=tipo_documento).order_by('-numero').first()
        if bolla != ultima_bolla:
            messages.error(request, "Puoi eliminare solo l'ultima bolla del tipo documento.")
            return redirect('bolle-list')

        # Decrementa l'ultimo numero del tipo documento
        tipo_documento.ultimo_numero -= 1
        tipo_documento.save()
        # DEBUG print(f"Tipo documento aggiornato: {tipo_documento.nome}, ultimo numero: {tipo_documento.ultimo_numero}")

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
        context["categoria_selezionata"] = categoria_selezionata
        if cliente.proprietario is None:
            # Se il cliente non ha un proprietario, mostra tutti gli articoli
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)

        else:
            # Ottieni gli articoli concessi al proprietario del cliente
            articoli_concessi = ArticoliConcessi.objects.filter(
                proprietario=cliente.proprietario
            ).values_list('articolo', flat=True)

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
            if ultimo_carico:
                lotto = ultimo_carico.lotto
            else:
                # Genera un lotto predefinito se non trovato
                oggi = now() + timedelta(days=5)
                lotto = oggi.strftime('%d%m%y')
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
        return super().post(request, *args, *kwargs)

class BollaCreateView(LoginRequiredMixin, CreateView):
    model = Bolla
    template_name = "bolle/bolla_create.html"
    fields = ['cliente', 'note']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user

        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo i clienti della zona
            form.fields['cliente'].queryset = Cliente.objects.filter(zona=user.zona)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra tutti i clienti del concessionario
            form.fields['cliente'].queryset = Cliente.objects.filter(concessionario=user.concessionario)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            form.fields['cliente'].queryset = Cliente.objects.none()
            messages.error(self.request, "Non hai i permessi per creare una bolla.")
            self.success_url = reverse_lazy('bolle-list')

        form.fields['cliente'].label = "Tipo Bolla e Cliente:"
        form.fields['note'].label = "Eventuali Note:"
        form.fields['note'].widget.attrs.update({'placeholder': 'Inserisci eventuali note'})

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
                    bolla_obj, created = Bolla.objects.get_or_create(
                        cliente=cliente,
                        tipo_documento=tipo_doc,
                        data=data,
                        numero=current_bolla_number,
                        defaults={"note": f"Importato il {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
                    )
                    if created:
                        bolla_obj.save(skip_auto_number=True)
                        # Da provare

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

        # Convertiamo a datetime
        if data_inizio_str:
            data_inizio = datetime.strptime(data_inizio_str, "%d %m %Y %H:%M")
            print(data_inizio)
        else:
            data_inizio = now().replace(hour=0, minute=0, second=0, microsecond=0)

        if data_fine_str:
            data_fine = datetime.strptime(data_fine_str, "%d %m %Y %H:%M")
            print(data_fine)
        else:
            data_fine = now().replace(hour=23, minute=59, second=59, microsecond=999999)

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
        file_path = os.path.join("/temp/", file_name)
        with open(file_path, "w", encoding="utf-8") as file:
            file.writelines(line + "\n" for line in linee)

        # Ritorna il file come risposta scaricabile
        response = FileResponse(open(file_path, "rb"), as_attachment=True, filename=file_name)

        # Elimina il file dopo averlo servito
        def elimina_file_dopo_risposta(response):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Errore durante l'eliminazione del file temporaneo: {e}")
            return response

        # Collega la funzione di pulizia alla risposta
        response.closed = elimina_file_dopo_risposta
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
        return queryset

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
        context["categoria_selezionata"] = categoria_selezionata

        # Righe associate al carico
        context['righe'] = self.object.righe.all()

        # Categorie e articoli disponibili
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(categoria_id = categoria_selezionata).order_by('nome'))
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
        return queryset

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
        context["categoria_selezionata"] = categoria_selezionata

        # Righe associate al carico
        context['righe'] = self.object.righe.all()

        # Categorie e articoli disponibili
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(categoria_id = categoria_selezionata).order_by('nome'))
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

    bolle_del_giorno = Bolla.objects.filter(data__range=(data_inizio, data_fine), tipo_documento__concessionario=zona.concessionario).exclude(tipo_documento__nome="RF")
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
    cliente = get_object_or_404(Cliente, pk=cliente_id)

    bolle_cliente = Bolla.objects.filter(data__range=(data_inizio, data_fine), cliente=cliente)

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
            prezzo_unitario = float(riga.articolo.prezzo_ivato)
            totale_riga = riga.quantita * prezzo_unitario

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
    response['Content-Disposition'] = f'attachment; filename="Fattura-{nome}-N-{fattura.numero}.xml'
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

        context['data_inizio'] = data_inizio
        context['data_fine'] = data_fine

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
        tipo_fattura = fattura.tipo_fattura

        # Controlla se la bolla è l'ultima
        ultima_fattura = Fattura.objects.filter(tipo_fattura=tipo_fattura).order_by('-numero').first()
        if fattura != ultima_fattura:
            messages.error(request, "Puoi eliminare solo l'ultima fattura del tipo fattura.")
            return redirect('fatture-list')

        # Decrementa l'ultimo numero del tipo documento
        tipo_fattura.ultimo_numero -= 1
        tipo_fattura.save()
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
        context["categoria_selezionata"] = categoria_selezionata
        if cliente.proprietario is None:
            # Se il cliente non ha un proprietario, mostra tutti gli articoli
            articoli_concessi = Articolo.objects.filter(categoria_id = categoria_selezionata)
        else:
            # Ottieni gli articoli concessi al proprietario del cliente
            articoli_concessi = ArticoliConcessi.objects.filter(
                proprietario=cliente.proprietario
            ).values_list('articolo', flat=True)

        context['righe'] = self.object.righe.all()
        context['categorie'] = Categoria.objects.prefetch_related(
            Prefetch('articoli', queryset=Articolo.objects.filter(pk__in=articoli_concessi, categoria_id = categoria_selezionata).order_by('nome'))
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
            # Verifica se la quantità è valida
            if not quantita or not quantita.isdigit() or int(quantita) <= 0:
                messages.error(request, "Inserisci una quantità valida maggiore di 0.")
                return redirect(
                    f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            if not articolo_id or articolo_id == "":
                messages.error(request, "Inserire un articolo.")
                return redirect(
                    f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
            RigaFattura.objects.create(
                fattura = self.get_object(),
                articolo_id = articolo_id,
                prezzo = prezzo,
                quantita = quantita,
            )
            return redirect(f"{reverse('fattura-update', kwargs={'pk': self.get_object().pk})}?categoria={categoria_selezionata}")
        elif 'recupera_totali' in request.POST:
            # Recupera i totali per il mese selezionato
            mese = int(request.POST.get('mese'))
            anno = int(request.POST.get('anno'))
            if not anno:
                anno = datetime.now().year  # Puoi anche aggiungere un filtro per selezionare l'anno
            cliente = self.get_object().cliente

            # Calcola il range di date per il mese
            data_inizio = datetime(anno, mese, 1)
            data_inizio = datetime.combine(data_inizio, datetime.min.time())
            data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
            data_fine = (data_inizio + relativedelta(months=1)) - timedelta(days=1)
            data_fine = datetime.combine(data_fine, datetime.max.time())
            data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())

            # Recupera le bolle del cliente per il mese selezionato
            bolle_cliente = Bolla.objects.filter(
                cliente=cliente,
                data__range=(data_inizio, data_fine)
            )

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
                RigaFattura.objects.create(
                    fattura=self.get_object(),
                    articolo=riga["articolo"],
                    quantita=riga["quantita"],
                )
                articoli += 1
            mesetto = _date(data_inizio, "F")
            messages.success(request, f"Totali recuperati per il mese di {mesetto} {anno}. {articoli} articoli aggiunti.")
            return redirect('fattura-update', pk=self.get_object().pk)
        elif 'confirm' in request.POST:
            # Conferma la modifica e salva la bolla
            return redirect('fatture-list')
        return super().post(request, *args, *kwargs)

class FatturaCreateView(LoginRequiredMixin, CreateView):
    model = Fattura
    template_name = "fatture/fattura_create.html"
    fields = ['data', 'cliente', 'concessionario', 'tipo_fattura', 'condizioni_pagamento', 'scadenza_pagamento', 'modalita_pagamento', 'note']

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        user = self.request.user

        if hasattr(user, 'zona'):
            # L'utente ha una zona: Mostra solo i clienti della zona
            zona = user.zona
            concessionario = zona.concessionario
            form.fields['cliente'].queryset = Cliente.objects.filter(zona=user.zona)
            form.fields['concessionario'].queryset = Concessionario.objects.filter(pk = concessionario.pk)
        elif hasattr(user, 'concessionario'):
            # L'utente ha un concessionario: Mostra tutti i clienti del concessionario
            form.fields['cliente'].queryset = Cliente.objects.filter(concessionario=user.concessionario)
            form.fields['concessionario'].queryset = Concessionario.objects.filter(pk = user.concessionario.pk)
        else:
            # L'utente non ha né zona né concessionario: Negare l'accesso
            form.fields['cliente'].queryset = Cliente.objects.none()
            form.fields['concessionario'].queryset = Concessionario.objects.none()
            messages.error(self.request, "Non hai i permessi per creare una bolla.")
            self.success_url = reverse_lazy('fatture-list')
        form.fields['data'].label = "Data fattura:"
        form.fields['cliente'].label = "Cliente:"
        form.fields['tipo_fattura'].label = "Tipo Fattura"
        form.fields['condizioni_pagamento'].label = "Condizioni di Pagamento"
        form.fields['scadenza_pagamento'].label = "Data Scadenza Pagamento"
        form.fields['modalita_pagamento'].label = "Modalità di Pagamento"
        form.fields['note'].label = "Eventuali Note:"
        form.fields['note'].widget.attrs.update({'placeholder': 'Inserisci eventuali note'})

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
    response['Content-Disposition'] = f'attachment; filename="Fattura-{nome}-N-{fattura.numero}.pdf'
    return response

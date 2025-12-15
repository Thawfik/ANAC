from datetime import date, timedelta

from django.contrib.messages.api import success
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, UpdateView, DeleteView
from django.db.models import Exists, OuterRef, F, Q
from django.views.generic.edit import CreateView
from django.urls import reverse_lazy
from django.shortcuts import redirect
from django.contrib import messages
from django.db import transaction

from . import serviceAllocation
from .models import Vol, Avion, Stand, Incident, Historique_allocations
from .forms import StandForm, IncidentForm, VolUpdateForm, AvionForm, DateFilterForm
from .serviceAllocation import reallouer_vol_unique, allouer_stands_optimise, liberer_stands_termines


class AllouerStandsView(View):
    """
    Vue pour déclencher manuellement l'allocation des stands aux vols en attente.
    Accessible via POST depuis le dashboard.
    """
    def post(self, request):
        allocated, unallocated = allouer_stands_optimise()
        
        if allocated > 0:
            messages.success(
                request, 
                f"✅ {allocated} vol(s) alloué(s) avec succès."
            )
        
        if unallocated > 0:
            messages.warning(
                request,
                f"⚠️ {unallocated} vol(s) n'ont pas pu être alloués (pas de stand compatible disponible)."
            )
        
        if allocated == 0 and unallocated == 0:
            messages.info(request, "ℹ️ Aucun vol en attente à allouer.")
        
        return redirect('dashboard')


# =========================================================
# VUES VOLS-AVION
# =========================================================
class VolCreateView(CreateView):
    """
    Permet de créer un nouveau vol, avec création/sélection optionnelle d'un avion.
    """
    model = Vol
    fields = [
        'num_vol_arrive', 'num_vol_depart', 'date_heure_debut_occupation',
        'date_heure_fin_occupation', 'provenance', 'destination'
    ]
    template_name = 'vols/vol_create.html'
    success_url = reverse_lazy('vol_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['avion_form'] = AvionForm(self.request.POST)
        else:
            context['avion_form'] = AvionForm()
        return context

    def form_valid(self, form):
        avion_form = AvionForm(self.request.POST)

        if avion_form.is_valid():
            immatriculation = avion_form.cleaned_data['immatriculation']

            if avion_form.cleaned_data.get('est_existant'):
                avion_instance = Avion.objects.get(immatriculation=immatriculation)
            else:
                avion_instance = avion_form.save()

            form.instance.avion = avion_instance
            form.instance.statut = 'ATTENTE'

            return super().form_valid(form)
        else:
            self.object = form.instance
            context = self.get_context_data()
            context['form'] = form
            context['avion_form'] = avion_form
            return self.render_to_response(context)


class BaseVolListView(ListView):
    """
    Classe de base pour toutes les vues de liste de vols actifs.
    Contient la logique commune pour les attributs et la vérification des incidents.
    """
    model = Vol
    context_object_name = 'vols'
    template_name = 'vols/vol_list.html'
    ordering = ['date_heure_debut_occupation']

    date_context_key = 'Aujourd\'hui'  # Valeur par défaut

    def get_queryset(self):
        raise NotImplementedError("La méthode get_queryset doit être implémentée par une sous-classe.")

    def get_context_data(self, **kwargs):
        """Ajoute l'information des incidents actifs sur les stands alloués."""
        context = super().get_context_data(**kwargs)
        # ✅ CORRECTION: Utiliser le bon nom de clé
        context['date_context_key'] = self.date_context_key  # Pas 'current_date_view'
        
        vols_avec_incidents = []
        for vol in context['vols']:
            vol.stand_a_incident = False
            if vol.stand_alloue:
                vol.stand_a_incident = vol.stand_alloue.incidents_rapportes.filter(
                    statut__in=['OUVERT', 'ENCOURS']
                ).exists()
            vols_avec_incidents.append(vol)

        context['vols'] = vols_avec_incidents
        return context


# --- Vue pour AUJOURD'HUI ---
class VolListView(BaseVolListView):
    """Affiche tous les vols actifs pour AUJOURD'HUI."""
    date_context_key = 'Aujourd\'hui'  # ✅ Correspond au template

    def get_queryset(self):
        date_aujourdhui = date.today()
        return Vol.objects.filter(
            statut__in=['ATTENTE', 'ALLOUE'],
            date_heure_debut_occupation__date=date_aujourdhui
        ).select_related('avion', 'stand_alloue').prefetch_related(
            'stand_alloue__incidents_rapportes'
        )


# --- Vue pour DEMAIN ---
class VolListTomorrowView(BaseVolListView):
    """Affiche tous les vols actifs pour DEMAIN."""
    date_context_key = 'Demain'  # ✅ CORRECTION: Majuscule pour correspondre au template

    def get_queryset(self):
        demain = date.today() + timedelta(days=1)
        return Vol.objects.filter(
            statut__in=['ATTENTE', 'ALLOUE'],
            date_heure_debut_occupation__date=demain
        ).select_related('avion', 'stand_alloue').prefetch_related(
            'stand_alloue__incidents_rapportes'
        )


# --- Vue pour DATE FUTURE ---
class VolListFutureView(BaseVolListView):
    """Affiche les vols pour une date spécifique choisie par l'utilisateur."""
    date_context_key = 'Future'  # ✅ Correspond au template

    def dispatch(self, request, *args, **kwargs):
        date_param = self.request.GET.get('date_choisie')

        if date_param:
            try:
                self.date_filtre = date.fromisoformat(date_param)
            except ValueError:
                return redirect('vol_list')
        else:
            self.date_filtre = None

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        if self.date_filtre:
            return Vol.objects.filter(
                statut__in=['ATTENTE', 'ALLOUE'],
                date_heure_debut_occupation__date=self.date_filtre
            ).select_related('avion', 'stand_alloue').prefetch_related(
                'stand_alloue__incidents_rapportes'
            )
        return Vol.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # ✅ Ajouter le formulaire et la date
        context['form'] = DateFilterForm(initial={
            'date_choisie': self.date_filtre if self.date_filtre else (date.today() + timedelta(days=2))
        })
        context['date_filtre'] = self.date_filtre
        
        return context
    
class VolUpdateView(UpdateView):
    """Permet de modifier les détails d'un vol existant."""
    model = Vol
    fields = [
        'num_vol_arrive', 'num_vol_depart', 'date_heure_debut_occupation',
        'date_heure_fin_occupation', 'provenance', 'destination',
    ]
    context_object_name = 'vol'
    template_name = 'vols/vol_create.html'

    def get_success_url(self):
        messages.success(self.request, f"Le vol {self.object.num_vol_arrive} a été mis à jour.")
        return reverse_lazy('vol_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        # Si les heures d'occupation sont modifiées, réinitialiser l'allocation
        if (form.cleaned_data['date_heure_debut_occupation'] != self.object.date_heure_debut_occupation or
                form.cleaned_data['date_heure_fin_occupation'] != self.object.date_heure_fin_occupation):
            
            # Pas besoin de modifier le statut du stand manuellement
            # Il sera recalculé automatiquement via la propriété @property
            form.instance.statut = 'ATTENTE'
            form.instance.stand_alloue = None
            messages.info(self.request,
                          "Les temps d'occupation ont été modifiés. Le vol est repassé en statut 'ATTENTE' pour réallocation.")

        return super().form_valid(form)


class VolDeleteView(DeleteView):
    """Permet de supprimer un vol."""
    model = Vol
    context_object_name = 'vol'
    template_name = 'vols/vol_confirm_delete.html'
    success_url = reverse_lazy('vol_list')

    def form_valid(self, form):
        # Pas besoin de libérer le stand manuellement
        # Le statut sera recalculé automatiquement
        messages.success(self.request, f"Le vol {self.object.num_vol_arrive} a été supprimé.")
        return super().form_valid(form)


class VolDetailView(DetailView):
    """Affiche les détails d'un vol spécifique et son statut d'allocation."""
    model = Vol
    context_object_name = 'vol'
    template_name = 'vols/vol_detail.html'

    def get_queryset(self):
        return Vol.objects.select_related('avion', 'stand_alloue')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        vol = self.object
        now = timezone.now()

        # Le vol est-il actuellement occupant du stand ?
        if vol.stand_alloue and vol.statut == 'ALLOUE':
            est_actuel = (
                vol.date_heure_debut_occupation <= now <= vol.date_heure_fin_occupation
            )
            context['est_occupant_actuel'] = est_actuel
        else:
            context['est_occupant_actuel'] = False

        return context


# =========================================================
# VUES STANDS
# =========================================================
class StandListView(ListView):
    """Liste tous les stands avec leurs informations de disponibilité."""
    model = Stand
    context_object_name = 'stands'
    template_name = 'stands/stand_list.html'

    def get_queryset(self):
        return Stand.objects.all().order_by('nom_operationnel')


class StandDetailView(DetailView):
    """
    Affiche les détails d'un stand spécifique, y compris les vols alloués
    et les incidents en cours.
    """
    model = Stand
    context_object_name = 'stand'
    template_name = 'stands/stand_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stand = self.object
        now = timezone.now()

        # ✅ CORRECTION: Utiliser le bon related_name 'vols_alloues'
        context['vols_futurs_alloues'] = stand.vols_alloues.filter(
            statut='ALLOUE',
            date_heure_fin_occupation__gt=now
        ).order_by('date_heure_debut_occupation')

        # Incidents en cours/ouverts
        context['incidents_actifs'] = Incident.objects.filter(
            stand=stand,
            statut__in=['OUVERT', 'ENCOURS']
        ).order_by('-date_heure_declaration')

        # Vol actuellement occupant (via la propriété du modèle)
        context['occupant_actuel'] = stand.vol_occupant_actuel

        return context


class StandCreateView(CreateView):
    """Permet de créer un nouveau stand."""
    model = Stand
    fields = ['nom_operationnel', 'longueur', 'largeur', 'distance_stand_aerogare']
    template_name = 'stands/stand_create.html'
    success_url = reverse_lazy('stand_list')

    def form_valid(self, form):
        # Assurer la disponibilité par défaut
        form.instance.disponibilite = True
        # ✅ CORRECTION: Ne pas essayer de modifier statut_operationnel
        # Il sera calculé automatiquement via @property
        messages.success(self.request, f"Le stand {form.instance.nom_operationnel} a été créé.")
        return super().form_valid(form)


class StandUpdateView(UpdateView):
    """Permet de modifier les dimensions ou le nom d'un stand."""
    model = Stand
    fields = ['nom_operationnel', 'longueur', 'largeur', 'disponibilite']
    context_object_name = 'stand'
    template_name = 'stands/stand_create.html'

    def get_success_url(self):
        messages.success(self.request, f"Le stand {self.object.nom_operationnel} a été mis à jour.")
        return reverse_lazy('stand_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        if (form.cleaned_data['longueur'] != self.object.longueur or
                form.cleaned_data['largeur'] != self.object.largeur):
            messages.warning(self.request,
                             "Les dimensions du stand ont changé. Veuillez vérifier les vols alloués.")

        return super().form_valid(form)


class StandDeleteView(DeleteView):
    """Permet de supprimer un stand."""
    model = Stand
    context_object_name = 'stand'
    template_name = 'stands/stand_confirm_delete.html'
    success_url = reverse_lazy('stand_list')

    def form_valid(self, form):
        now = timezone.now()
        # ✅ CORRECTION: Utiliser le bon related_name 'vols_alloues'
        vols_futurs = self.object.vols_alloues.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__gt=now
        )

        if vols_futurs.exists():
            messages.error(self.request,
                           f"Impossible de supprimer le stand {self.object.nom_operationnel}. {vols_futurs.count()} vol(s) futurs y sont encore alloués.")
            return redirect('stand_detail', pk=self.object.pk)

        messages.success(self.request, f"Le stand {self.object.nom_operationnel} a été supprimé.")
        return super().form_valid(form)
# =========================================================
# VUES INCIDENT
# =========================================================

def handle_incident_impact(stand_instance, request):
    """
    Récupère tous les vols alloués à ce stand qui n'ont pas encore commencé
    et les remet en statut 'ATTENTE'. Déclenche ensuite une réallocation.
    """
    now = timezone.now()

    # Récupérer les vols affectés : alloués à CE stand ET leur début d'occupation est DANS LE FUTUR
    affected_vols = Vol.objects.filter(
        stand_alloue=stand_instance,
        statut='ALLOUE',
        date_heure_debut_occupation__gt=now  # Le vol n'est pas encore arrivé
    )

    count = affected_vols.count()
    if count > 0:
        # Réinitialisation des statuts en masse pour une meilleure performance
        affected_vols.update(
            statut='ATTENTE',
            stand_alloue=None
        )

        messages.warning(request,
                         f"{count} vol(s) alloués au stand {stand_instance.nom_operationnel} ont été passés en 'ATTENTE' à cause de l'incident.")

        # 2. Déclenchement de la réallocation immédiate
        # On passe le QuerySet des vols affectés pour que le service ne traite qu'eux (optimisation)
        allocated, unallocated = allouer_stands_optimise(vols_a_traiter=affected_vols)

        if allocated > 0:
            messages.success(request, f"✅ {allocated} vol(s) ont été réalloués avec succès.")
        if unallocated > 0:
            messages.error(request,
                           f"❌ {unallocated} vol(s) n'ont pas pu être réalloués immédiatement après l'incident.")

    return count


class IncidentCreateView(CreateView):
    """
    Permet de déclarer un nouvel incident sur un Stand.
    Déclenche une réallocation si des vols futurs sont affectés.
    """
    model = Incident
    fields = ['stand', 'type_incident', 'description']
    template_name = 'incidents/incident_create.html'
    success_url = reverse_lazy('incident_list')

    def form_valid(self, form):
        # 1. Assurer que le statut est 'OUVERT' lors de la déclaration initiale
        form.instance.statut = 'OUVERT'

        # 2. Sauvegarde de l'incident
        response = super().form_valid(form)

        # 3. Vérification de l'impact et réallocation
        # Si un vol était alloué à ce stand, il est déclassé en 'ATTENTE'
        handle_incident_impact(form.instance.stand, self.request)

        messages.success(self.request, f"L'incident a été déclaré sur le stand {form.instance.stand.nom_operationnel}.")
        return response


class IncidentUpdateView(UpdateView):
    """
    Permet de modifier les détails d'un incident, y compris le changement de statut.
    Déclenche une réallocation si l'incident est réouvert.
    """
    model = Incident
    fields = ['stand', 'type_incident', 'description', 'statut']
    context_object_name = 'incident'
    template_name = 'incidents/incident_create.html'

    def get_success_url(self):
        messages.success(self.request, f"L'incident sur {self.object.stand.nom_operationnel} a été mis à jour.")
        return reverse_lazy('incident_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        original_statut = self.object.statut  # Statut avant la modification
        new_statut = form.cleaned_data['statut']

        trigger_reallocation = False

        # Logique métier: Gérer l'heure de résolution
        if new_statut == 'RESOLU' and not form.instance.date_heure_resolution:
            form.instance.date_heure_resolution = timezone.now()
        elif new_statut != 'RESOLU':
            form.instance.date_heure_resolution = None  # Effacer l'heure si l'incident est réouvert/modifié

        # Détection du besoin de réallocation : Si le statut passe de RESOLU (Stand OK)
        # à OUVERT ou ENCOURS (Stand Bloqué), on doit réallouer.
        if original_statut == 'RESOLU' and new_statut in ['OUVERT', 'ENCOURS']:
            trigger_reallocation = True

        response = super().form_valid(form)

        # Exécution de l'impact si le stand est rebloqué
        if trigger_reallocation:
            handle_incident_impact(form.instance.stand, self.request)

        return response



class IncidentResolutionView(UpdateView):
    """Vue pour modifier et potentiellement résoudre un incident."""
    model = Incident
    # On ajoute la date de résolution et le statut au formulaire de modification
    fields = ['type_incident', 'description', 'statut', 'date_heure_resolution']
    template_name = 'incidents/incident_resolution.html'

    @transaction.atomic
    def form_valid(self, form):
        incident = form.save(commit=False)

        # Si le statut passe à 'RESOLU'
        if incident.statut == 'RESOLU':
            # Si la date de résolution n'est pas encore définie, la définir maintenant
            if incident.date_heure_resolution is None:
                incident.date_heure_resolution = timezone.now()

            # Tenter de rendre le stand disponible (seulement si AUCUN autre incident n'est ouvert)
            stand = incident.stand
            incidents_actifs_restants = stand.incidents_rapportes.filter(
                statut__in=['OUVERT', 'ENCOURS']
            ).exclude(pk=incident.pk)  # Exclure l'incident que nous sommes en train de résoudre

            if not incidents_actifs_restants.exists():
                stand.disponibilite = True
                stand.save()
                messages.success(self.request,
                                 f"L'incident a été résolu. Le Stand {stand.nom_operationnel} est de nouveau disponible pour l'allocation.")
            else:
                messages.warning(self.request,
                                 f"L'incident a été résolu, mais le Stand {stand.nom_operationnel} reste indisponible car {incidents_actifs_restants.count()} autre(s) incident(s) actif(s) persiste(nt).")

        incident.save()
        messages.info(self.request, f"Incident {incident.pk} mis à jour (Statut: {incident.get_statut_display()}).")

        return redirect('stand_detail', pk=incident.stand.pk)


# Pour lister tous les incidents du système (pas seulement ceux d'un stand)
class IncidentListView(ListView):
    model = Incident
    context_object_name = 'incidents'
    template_name = 'incidents/incident_list.html'
    ordering = ['-date_heure_declaration']



from django.views.generic import TemplateView


class DashboardView(TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # --- 1. Statistiques des Stands ---
        total_stands = Stand.objects.count()
        
        # Stands bloqués par incidents
        stands_bloques = Stand.objects.filter(
            incidents_rapportes__statut__in=['OUVERT', 'ENCOURS']
        ).distinct().count()
        
        # CORRECTION ICI : Utiliser la même logique que statut_operationnel
        # Stands occupés = stands avec au moins un vol ALLOUE
        stands_occupes = Stand.objects.filter(
            vols_alloues__statut='ALLOUE'
        ).distinct().count()
        
        # Stands disponibles = total - (occupés + bloqués)
        stands_disponibles = total_stands - stands_bloques - stands_occupes

        context['stand_stats'] = {
            'total': total_stands,
            'occupes': stands_occupes,
            'bloques': stands_bloques,
            'disponibles': stands_disponibles,
        }

        # --- 2. Statistiques détaillées des vols par stand ---
        # Pour comprendre la différence entre les comptes
        context['vols_alloues_total'] = Vol.objects.filter(statut='ALLOUE').count()
        context['vols_en_cours_occupation'] = Vol.objects.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__lte=now,
            date_heure_fin_occupation__gt=now
        ).count()
        
        # --- Le reste de ton code reste identique ---
        # Vols en attente d'allocation
        vols_attente = Vol.objects.filter(statut='ATTENTE').count()

        # Vols alloués et futurs
        vols_alloues_futurs = Vol.objects.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__gt=now
        ).count()

        # Vols en cours d'occupation
        vols_en_cours = Vol.objects.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__lte=now,
            date_heure_fin_occupation__gt=now
        ).count()

        # Prochain vol à allouer
        prochain_vol = Vol.objects.filter(statut='ATTENTE').order_by('date_heure_debut_occupation').first()

        context['vol_stats'] = {
            'attente': vols_attente,
            'alloues_futurs': vols_alloues_futurs,
            'en_cours': vols_en_cours,
            'prochain_vol': prochain_vol,
        }

        # --- 3. Statistiques des Incidents ---
        context['incident_stats'] = Incident.objects.filter(
            statut__in=['OUVERT', 'ENCOURS']
        ).count()

        context['derniers_incidents'] = Incident.objects.filter(
            statut__in=['OUVERT', 'ENCOURS']
        ).select_related('stand').order_by('-date_heure_declaration')[:5]

        return context



class LancerAllocationView(View):
    """Déclenche le service d'allocation des stands et redirige vers la liste des vols."""
    def post(self, request, *args, **kwargs):
        # On appelle le service d'allocation
        allocated, unallocated = allouer_stands_optimise()

        if allocated > 0:
            messages.success(request, f"🚀 {allocated} vol(s) ont été alloués avec succès.")
        if unallocated > 0:
            messages.warning(request, f"⚠️ {unallocated} vol(s) n'ont pas pu être alloués (conflit, dimensions ou stand indisponible).")
        if allocated == 0 and unallocated == 0:
             messages.info(request, "Aucun vol en statut 'ATTENTE' à traiter.")

        # Rediriger vers la liste des vols pour voir le résultat
        return redirect('vol_list')


class ReallouerVolActionView(View):
    """
    Vue pour réallouer un vol dont le stand a un incident.
    Gère à la fois l'affichage de la confirmation (GET) et l'action (POST).
    """
    template_name = 'vols/vol_reallouer_confirm.html'
    
    def get(self, request, pk):
        """Affiche la page de confirmation."""
        try:
            vol = Vol.objects.select_related('stand_alloue', 'avion').get(pk=pk)
        except Vol.DoesNotExist:
            messages.error(request, "❌ Vol introuvable.")
            return redirect('vol_list')
        
        # Vérifier que le vol est alloué
        if vol.statut != 'ALLOUE' or not vol.stand_alloue:
            messages.warning(request, f"⚠️ Le vol {vol.num_vol_arrive} n'est pas alloué.")
            return redirect('vol_list')
        
        # Récupérer les incidents actifs du stand
        incidents_actifs = vol.stand_alloue.incidents_rapportes.filter(
            statut__in=['OUVERT', 'ENCOURS']
        )
        
        if not incidents_actifs.exists():
            messages.info(request, f"ℹ️ Le stand {vol.stand_alloue.nom_operationnel} n'a pas d'incident actif.")
            return redirect('vol_list')
        
        context = {
            'vol': vol,
            'incidents_actifs': incidents_actifs,
        }
        
        return render(request, self.template_name, context)
    
    def post(self, request, pk):
        """Traite l'action choisie par l'utilisateur."""
        action = request.POST.get('action')
        
        try:
            vol = Vol.objects.select_related('stand_alloue').get(pk=pk)
        except Vol.DoesNotExist:
            messages.error(request, "❌ Vol introuvable.")
            return redirect('vol_list')
        
        if action == 'reallouer':
            # ✅ Option 1: Réallouer à un autre stand
            succes, message = reallouer_vol_unique(pk)
            
            if succes:
                messages.success(request, f"✅ {message}")
            else:
                messages.warning(request, f"⚠️ {message}")
        
        elif action == 'garder':
            # ✅ Option 2: Garder le parking et résoudre l'incident
            stand = vol.stand_alloue
            
            # Résoudre tous les incidents actifs du stand
            incidents_resolus = stand.incidents_rapportes.filter(
                statut__in=['OUVERT', 'ENCOURS']
            ).update(
                statut='RESOLU',
                date_heure_resolution=timezone.now()
            )
            
            messages.success(
                request,
                f"✅ {incidents_resolus} incident(s) résolu(s). "
                f"Le vol {vol.num_vol_arrive} garde le parking {stand.nom_operationnel}."
            )
        
        else:
            messages.error(request, "❌ Action invalide.")
        
        return redirect('vol_list')

class libererStands(View):
    """
        Vue pour libérer les stands en fin d'occupation.
    """
    def post(self, request):
        succes, message = liberer_stands_termines()
        if succes:
            messages.success(request, f"✅ {message}")
        else:
            messages.warning(request, f" {message}")

        return redirect('historique_allocations')

class historique_allocations(ListView):
    model = Historique_allocations
    template_name = 'historique_allocations.html'
    context_object_name = 'historiques'
    paginate_by = 25

    def get_queryset(self):
        return Historique_allocations.objects.all().order_by('-date_heure_fin_occupation')


